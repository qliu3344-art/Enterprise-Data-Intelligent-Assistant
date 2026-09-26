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
from app.retry import retry_call

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
2. reason 只写一句业务结论，面向业务人员，严禁出现任何数字、公式、统计术语（Q3/IQR/均值/标准差/σ等）
3. 输出 JSON：{{"anomalies": [{{"record_index": 0, "field": "order_amount", "value": 99999, "is_anomaly": true, "reason": "超出正常范围，疑似录入错误"}}]}}
4. 只输出 JSON

## reason 示例（必须照这个风格写，不要出现任何数字）
✅ 好的 reason："加班时长偏高，建议核实是否为项目冲刺期"
✅ 好的 reason："请假天数超出正常范围，疑似数据录入错误"
✅ 好的 reason："与历史同期相比明显偏高，需人工复核"
❌ 坏的 reason："超过Q3+1.5×IQR且均值+2σ..."（写了公式 → 直接判错）
❌ 坏的 reason："虽未超历史最大值但IQR法判定..."（写了IQR → 直接判错）
❌ 坏的 reason："加班42.5小时超过均值11.12"（写了数字 → 直接判错）
"""

# 单批容量：一条 prompt 里模型能稳定逐条判断、且不丢注意力，大概就是几十条。
# 这是「约束」，推出的是「要分批」；不是「总量上限」，那才是取舍、才要丢东西。
# 两者必须分清——约束是真的，取舍是错的。
#
# 成本也不是这里的理由：一批五千行 8 个数值列的数据，偏态业务数据下 IQR 候选在千条
# 量级，按几十条一批就是几十次调用，全批几万 token，几毛钱的量级——而一次清洗省掉的
# 是人工几个小时的核对。注意力容量才是理由，成本不是；而注意力容量靠分批解决，
# 不靠截断解决。
CANDIDATE_BATCH_SIZE = 30


def _batched(items: list, size: int):
    """按 size 切分列表，最后一批允许不足。"""
    for i in range(0, len(items), size):
        yield items[i:i + size]


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

        # LLM 终判：候选分批送，覆盖百分之百，一条不丢
        historical_stats = self._compute_basic_stats(df)
        data_type = context.get("data_type", "未知") if context else "未知"
        date_range = context.get("date_range", "未知") if context else "未知"

        batches = list(_batched(statistical_candidates, CANDIDATE_BATCH_SIZE))
        anomalies: list[dict] = []
        degraded_batches = 0
        degradation_reason = ""

        for bi, batch in enumerate(batches, 1):
            prompt = ANOMALY_PROMPT.format(
                data_type=data_type,
                date_range=date_range,
                historical_stats=historical_stats,
                data_summary=self._summarize_candidates(batch),
            )
            try:
                resp = retry_call(
                    Generation.call,
                    model=settings.LLM_MODEL,
                    messages=[{"role": "user", "content": prompt}],
                    result_format="message",
                    temperature=0.1,
                )

                content = resp.output.choices[0].message.content.strip()
                if content.startswith("```"):
                    content = content.split("\n", 1)[1].rsplit("```", 1)[0].strip()

                result = json.loads(content)
            except Exception as e:
                # 模型挂掉只意味着「暂时没人下结论」，不意味着「结论是正常」——
                # 这是两件事，不能混。所以这一批保留候选、标记为待人工审核，
                # 而不是当成正常放过去。默认值是拒绝，不是放行。
                # 分批之后单批失败不影响其余批次，不会因为一批挂掉丢掉整轮候选。
                degraded_batches += 1
                degradation_reason = str(e)
                logger.error(
                    f"LLM 异常判定失败（第 {bi}/{len(batches)} 批），该批降级为待审核: {e}"
                )
                anomalies.extend(self._pending_batch(batch))
                continue

            # 分批只影响「怎么送」，不影响「送多少」——结果按批合并，总数守恒
            anomalies.extend(result.get("anomalies", []))

        confirmed = [a for a in anomalies if a.get("is_anomaly")]
        self._log_step(
            "anomaly_detect",
            len(statistical_candidates),
            len(confirmed),
            {
                "llm_called": degraded_batches < len(batches),
                "degraded": degraded_batches > 0,
                "batches": len(batches),
                "degraded_batches": degraded_batches,
                "candidates": len(statistical_candidates),
                "confirmed_anomalies": len(confirmed),
                "error": degradation_reason,
            },
        )
        return anomalies

    @staticmethod
    def _pending_batch(batch: list[dict]) -> list[dict]:
        """把一批候选标成待人工审核。

        待审核不等于积压：只有统计层筛出来的候选才需要人工，占总数据量的千分之一
        量级；而且记录带字段和值，人工看到的是「第 137 行、加班时长、超出正常范围」，
        不用从头翻表。
        """
        return [
            {
                "record_index": c["record_index"],
                "field": c["field"],
                "value": c["value"],
                "is_anomaly": False,
                "reason": f"LLM异常降级，待审核: {c['field']} 当前值 {c['value']} 超出 IQR 统计范围",
                "pending_review": True,
            }
            for c in batch
        ]

    def _iqr_detect(self, df: pd.DataFrame) -> list[dict]:
        """IQR 方法初筛异常值候选，按偏离程度排序后取 top-N。"""
        candidates = []
        numeric_cols = df.select_dtypes(include=["float64", "int64"]).columns
        for col in numeric_cols:
            Q1 = df[col].quantile(0.25)
            Q3 = df[col].quantile(0.75)
            IQR = Q3 - Q1
            if IQR == 0:
                continue
            median = df[col].median()
            lower = Q1 - 1.5 * IQR
            upper = Q3 + 1.5 * IQR
            outliers = df[(df[col] < lower) | (df[col] > upper)]
            for idx, val in outliers[col].items():
                if pd.isna(val):
                    continue
                deviation = abs(float(val) - float(median)) / float(IQR)
                candidates.append(
                    {
                        "record_index": int(idx),
                        "field": col,
                        "value": float(val),
                        "q1": float(Q1),
                        "q3": float(Q3),
                        "iqr": float(IQR),
                        "deviation": round(deviation, 2),
                    }
                )
        # 按偏离程度降序排列，全部送给 LLM（IQR 已做初筛，不再二次截断）
        candidates.sort(key=lambda x: x["deviation"], reverse=True)
        return candidates

    def _compute_basic_stats(self, df: pd.DataFrame) -> str:
        """计算数值列的基本统计量，转为自然语言供 LLM 参考。

        刻意不用 JSON 数值格式——避免 LLM 看到数字后忍不住做数学计算
        并写到 reason 里。用纯中文描述，"大约"模糊化，不暴露精确值。
        """
        lines = []
        for col in df.select_dtypes(include=["float64", "int64"]).columns:
            mean_val = round(float(df[col].mean()), 1)
            median_val = round(float(df[col].median()), 1)
            min_val = round(float(df[col].min()), 1)
            max_val = round(float(df[col].max()), 1)
            lines.append(
                f"字段「{col}」：大部分值在 {min_val} 到 {max_val} 之间，"
                f"中位数约 {median_val}，平均值约 {mean_val}"
            )
        return "\n".join(lines)

    def _summarize_candidates(self, candidates: list[dict]) -> str:
        """将一批候选异常转为 LLM 可读的文本。

        传进来的已经是切好的一批，这里不做任何截断——候选按偏离度排完序之后，
        不能按数量截断：排序应该决定「先看谁」，不该决定「看谁」。

        刻意用模糊描述替代精确数值（如"偏高"、"极低"），避免 LLM 在 reason
        中忍不住引用数字，与 ANOMALY_PROMPT 的"严禁数字"要求保持一致性。
        """
        lines = []
        for c in candidates:
            deviation = c.get("deviation", 0)
            if deviation >= 3.0:
                level = "严重偏离正常范围"
            elif deviation >= 2.0:
                level = "明显偏高/偏低"
            elif deviation >= 1.5:
                level = "略超出正常范围"
            else:
                level = "处于正常范围边缘"

            field = c["field"]
            record = c["record_index"]
            lines.append(f"第{record}行, 字段「{field}」: {level}（偏离度 {deviation} 倍 IQR）")
        return "\n".join(lines)

    def _mark_anomalies(
        self, df: pd.DataFrame, anomalies: list[dict]
    ) -> pd.DataFrame:
        """在 DataFrame 上标注异常标记和原因。

        处理三种状态：
          - is_anomaly=True → confirmed（LLM 确认的异常）
          - pending_review=True → pending_review（LLM 降级，待后续复核）
          - 其他 → normal（正常数据）
        """
        if "is_anomaly" not in df.columns:
            df["is_anomaly"] = False
        if "anomaly_reason" not in df.columns:
            df["anomaly_reason"] = ""
        if "anomaly_status" not in df.columns:
            df["anomaly_status"] = "normal"
        if "pending_check_fields" not in df.columns:
            df["pending_check_fields"] = None

        for a in anomalies:
            idx = a["record_index"]
            if not (0 <= idx < len(df)):
                continue

            if a.get("is_anomaly"):
                df.at[idx, "is_anomaly"] = True
                df.at[idx, "anomaly_reason"] = a.get("reason", "")
                df.at[idx, "anomaly_status"] = "confirmed"
            elif a.get("pending_review"):
                df.at[idx, "is_anomaly"] = False
                df.at[idx, "anomaly_reason"] = a.get("reason", "")
                df.at[idx, "anomaly_status"] = "pending_review"
                df.at[idx, "pending_check_fields"] = {
                    "field": a.get("field"),
                    "value": a.get("value"),
                }
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
