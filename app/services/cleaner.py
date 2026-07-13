"""数据清洗 Pipeline — 去重 → 填充 → 异常判定。

三步顺序执行，每一步记录日志到 pipeline_logs 表。
异常判定采用 IQR 初筛 + LLM 终判的两阶段策略。
"""

import json
import time

import pandas as pd
from dashscope import Generation

from app.config import settings
from app.logger import logger

ANOMALY_PROMPT = """你是一个业务数据分析师。请判断以下数据中是否存在业务异常值。

## 业务上下文
- 数据来源：{data_type} 部门
- 时间范围：{date_range}

## 历史统计参考
{historical_stats}

## 候选异常数据
{data_summary}

## 要求
1. 逐条判断每个候选值是否为业务异常
2. reason 必须是一句简短的人话，直接说结论，禁止写计算过程、数学公式、统计术语
3. 输出 JSON：{{"anomalies": [{{"record_index": 0, "field": "order_amount", "value": 99999, "is_anomaly": true, "reason": "超出正常范围，疑似录入错误"}}]}}
4. 只输出 JSON

## reason 示例（必须照这个风格写）
✅ 好的 reason："加班时长偏高，建议核实是否为项目冲刺期"
✅ 好的 reason："请假天数超出正常范围，疑似数据录入错误"
✅ 好的 reason："与历史同期相比明显偏高，需人工复核"
❌ 坏的 reason："超过Q3+1.5×IQR且均值+2σ..."（禁止写公式）
❌ 坏的 reason："虽未超历史最大值但IQR法判定..."（禁止纠结过程）
"""


class CleaningPipeline:
    """三步清洗流程：去重 → 填充 → 异常判异"""

    def __init__(self, batch_id: str):
        self.batch_id = batch_id
        self.logs: list[dict] = []

    def run(self, df: pd.DataFrame, context: dict | None = None) -> pd.DataFrame:
        """执行完整清洗流程，返回清洗后的 DataFrame。"""
        if context is None:
            context = {}

        t0 = time.time()

        # 步骤1：去重
        df = self._deduplicate(df)

        # 步骤2：填充缺失值
        df = self._fill_missing(df, context)

        # 步骤3：异常判定
        anomalies = self._detect_anomalies(df, context)
        df = self._mark_anomalies(df, anomalies)

        elapsed = round(time.time() - t0, 2)
        logger.info(f"清洗完成: batch={self.batch_id}, rows={len(df)}, time={elapsed}s")

        return df

    # —— 步骤1：去重 ——
    def _deduplicate(self, df: pd.DataFrame) -> pd.DataFrame:
        before = len(df)
        key_cols = self._find_key_columns(df)

        if key_cols:
            df = df.drop_duplicates(subset=key_cols, keep="first")
        else:
            df = df.drop_duplicates(keep="first")

        # 重置索引，避免后续 .at[] 访问越界
        df = df.reset_index(drop=True)

        after = len(df)
        self._log_step("dedup", before, after, {"key_columns": key_cols})
        return df

    def _find_key_columns(self, df: pd.DataFrame) -> list[str]:
        """自动识别可能的唯一标识列组合。"""
        candidates = []
        for col in df.columns:
            col_lower = col.lower()
            if any(
                kw in col_lower
                for kw in ["id", "编号", "日期", "date", "name", "姓名"]
            ):
                candidates.append(col)
        return candidates if len(candidates) <= 3 else candidates[:3]

    # —— 步骤2：缺失值填充 ——
    def _fill_missing(
        self, df: pd.DataFrame, context: dict | None = None
    ) -> pd.DataFrame:
        before_missing = int(df.isnull().sum().sum())
        fill_rules: dict[str, str] = {}

        for col in df.columns:
            if df[col].dtype in ("float64", "int64"):
                fill_val = df[col].median()
                if not pd.isna(fill_val):
                    fill_rules[col] = f"中位数({fill_val})"
                    df[col] = df[col].fillna(fill_val)
            elif df[col].dtype == "object":
                mode_vals = df[col].mode()
                if not mode_vals.empty:
                    fill_val = mode_vals[0]
                    fill_rules[col] = f"众数({fill_val})"
                    df[col] = df[col].fillna(fill_val)

        after_missing = int(df.isnull().sum().sum())
        total_cells = df.size
        missing_rate = before_missing / total_cells if total_cells > 0 else 0.0
        fill_rules["_missing_rate"] = f"{missing_rate:.2%} ({before_missing}/{total_cells})"
        self._log_step("fill_missing", before_missing, after_missing, fill_rules)
        return df

    # —— 步骤3：异常判定 ——
    def _detect_anomalies(
        self, df: pd.DataFrame, context: dict | None = None
    ) -> list[dict]:
        """IQR 初筛 + LLM 终判。"""
        # 统计初筛
        statistical_candidates = self._iqr_detect(df)

        if not statistical_candidates:
            self._log_step(
                "anomaly_detect", 0, 0, {"llm_called": False, "message": "无统计异常候选"}
            )
            return []

        # LLM 终判
        historical_stats = self._compute_basic_stats(df)
        data_summary = self._summarize_candidates(statistical_candidates)

        prompt = ANOMALY_PROMPT.format(
            data_type=context.get("data_type", "未知") if context else "未知",
            date_range=context.get("date_range", "未知") if context else "未知",
            historical_stats=json.dumps(historical_stats, ensure_ascii=False),
            data_summary=data_summary,
        )

        try:
            resp = Generation.call(
                model=settings.LLM_MODEL,
                messages=[{"role": "user", "content": prompt}],
                result_format="message",
                temperature=0.1,
            )

            content = resp.output.choices[0].message.content.strip()
            if content.startswith("```"):
                content = content.split("\n", 1)[1].rsplit("```", 1)[0].strip()

            result = json.loads(content)
        except (json.JSONDecodeError, Exception) as e:
            logger.error(f"LLM 异常判定失败: {e}")
            self._log_step("anomaly_detect", len(statistical_candidates), 0, {"error": str(e)})
            return []

        anomalies = result.get("anomalies", [])
        confirmed = [a for a in anomalies if a.get("is_anomaly")]
        self._log_step(
            "anomaly_detect",
            len(statistical_candidates),
            len(confirmed),
            {
                "llm_called": True,
                "candidates": len(statistical_candidates),
                "confirmed_anomalies": len(confirmed),
            },
        )
        return anomalies

    def _iqr_detect(self, df: pd.DataFrame) -> list[dict]:
        """IQR 方法初筛异常值候选。"""
        candidates = []
        numeric_cols = df.select_dtypes(include=["float64", "int64"]).columns
        for col in numeric_cols:
            Q1 = df[col].quantile(0.25)
            Q3 = df[col].quantile(0.75)
            IQR = Q3 - Q1
            if IQR == 0:
                continue
            lower = Q1 - 1.5 * IQR
            upper = Q3 + 1.5 * IQR
            outliers = df[(df[col] < lower) | (df[col] > upper)]
            for idx, val in outliers[col].items():
                candidates.append(
                    {
                        "record_index": int(idx),
                        "field": col,
                        "value": float(val) if not pd.isna(val) else None,
                        "q1": float(Q1),
                        "q3": float(Q3),
                        "iqr": float(IQR),
                    }
                )
        # 最多送 LLM_MAX_ANOMALY_CANDIDATES 条
        return candidates[: settings.LLM_MAX_ANOMALY_CANDIDATES]

    def _compute_basic_stats(self, df: pd.DataFrame) -> dict:
        """计算数值列的基本统计量，供 LLM 参考。"""
        stats = {}
        for col in df.select_dtypes(include=["float64", "int64"]).columns:
            stats[col] = {
                "mean": round(float(df[col].mean()), 2),
                "median": round(float(df[col].median()), 2),
                "std": round(float(df[col].std()), 2),
                "min": round(float(df[col].min()), 2),
                "max": round(float(df[col].max()), 2),
            }
        return stats

    def _summarize_candidates(self, candidates: list[dict]) -> str:
        """将候选异常转为 LLM 可读的文本（只传值和字段，不传统计细节）。"""
        lines = []
        for c in candidates[:30]:
            lines.append(
                f"第{c['record_index']}行, 字段={c['field']}, 当前值={c['value']}"
            )
        return "\n".join(lines)

    def _mark_anomalies(
        self, df: pd.DataFrame, anomalies: list[dict]
    ) -> pd.DataFrame:
        """在 DataFrame 上标注异常标记和原因。"""
        if "is_anomaly" not in df.columns:
            df["is_anomaly"] = False
        if "anomaly_reason" not in df.columns:
            df["anomaly_reason"] = ""

        for a in anomalies:
            if a.get("is_anomaly"):
                idx = a["record_index"]
                if 0 <= idx < len(df):
                    df.at[idx, "is_anomaly"] = True
                    df.at[idx, "anomaly_reason"] = a.get("reason", "")
        return df

    # —— 日志 ——
    def _log_step(
        self,
        step_name: str,
        input_count: int,
        output_count: int,
        details: dict | None = None,
    ):
        self.logs.append(
            {
                "step": step_name,
                "input_count": input_count,
                "output_count": output_count,
                "details": details or {},
            }
        )
