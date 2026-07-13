"""业务分析服务 — 多维度统计、趋势分析、数据质量报告、Excel 导出。"""

import os
import threading
import time
from datetime import datetime, date

import pandas as pd
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.config import settings
from app.models.cleaned_record import CleanedRecord
from app.logger import logger

# 互斥锁：防止并发请求同时执行全表扫描导致连接池耗尽
_analytics_lock = threading.Lock()


# 从 business_data JSON 中提取日期的候选键（按优先级）
_DATE_KEYS = [
    "考勤日期", "销售日期", "交易日期", "记录日期",
    "日期", "date", "Date", "DATE", "record_date", "业务日期",
]

_DEPT_KEYS = ["所属部门", "部门", "department", "Department", "dept", "Dept"]


def _extract_date(record: CleanedRecord):
    """优先用 record_date 列，为空则从 business_data JSON 中按候选键提取。"""
    if record.record_date is not None:
        return record.record_date
    bd = record.business_data
    if not bd:
        return None
    for key in _DATE_KEYS:
        val = bd.get(key)
        if val is None:
            continue
        try:
            if isinstance(val, str):
                # 支持 "2025/11/01" 和 "2025-11-01"
                val = val.replace("/", "-").strip()
                return datetime.strptime(val[:10], "%Y-%m-%d").date()
            return pd.Timestamp(val).date()
        except (ValueError, TypeError):
            continue
    return None


def _extract_department(record: CleanedRecord) -> str:
    """优先用 department 列，为空则从 business_data JSON 中按候选键提取。"""
    if record.department:
        return record.department
    bd = record.business_data
    if not bd:
        return "未知部门"
    for key in _DEPT_KEYS:
        val = bd.get(key)
        if val and str(val).strip():
            return str(val).strip()
    return "未知部门"


def get_dashboard(db: Session) -> dict:
    """仪表盘聚合：一次查询返回全部数据，彻底消除并发问题。"""
    records = db.query(CleanedRecord).all()

    if not records:
        return {
            "summary": {"total_records": 0, "total_batches": 0, "by_type": []},
            "trend": {"metric": "record_count", "data_points": []},
            "quality": {"total_records": 0, "missing_rate": 0, "anomaly_rate": 0, "avg_quality_score": 0, "by_department": []},
        }

    # ——— summary ———
    batch_ids = set(r.batch_id for r in records)
    df = pd.DataFrame([
        {"data_type": r.data_type or "unknown", "record_date": _extract_date(r),
         "is_anomaly": r.is_anomaly, "quality_score": r.quality_score or 1.0}
        for r in records
    ])
    by_type = []
    for dtype, group in df.groupby("data_type"):
        dates = group["record_date"].dropna()
        by_type.append({
            "data_type": dtype, "record_count": len(group),
            "date_range_start": dates.min().isoformat() if not dates.empty else None,
            "date_range_end": dates.max().isoformat() if not dates.empty else None,
            "anomaly_count": int(group["is_anomaly"].sum()),
            "anomaly_rate": round(float(group["is_anomaly"].mean()) * 100, 2),
            "avg_quality_score": round(float(group["quality_score"].mean()), 4),
        })
    summary = {"total_records": len(records), "total_batches": len(batch_ids), "by_type": by_type}

    # ——— trend ———
    df2 = df[["record_date", "data_type", "is_anomaly"]].copy()
    df2["record_date"] = pd.to_datetime(df2["record_date"], errors="coerce")
    df2 = df2.dropna(subset=["record_date"])
    df2["period"] = df2["record_date"].dt.strftime("%Y-%m")
    data_points = []
    for (period, dtype), group in df2.groupby(["period", "data_type"]):
        data_points.append({"period": period, "value": len(group), "data_type": dtype})
    data_points.sort(key=lambda x: x["period"])
    trend = {"metric": "record_count", "data_points": data_points}

    # ——— quality ———
    total = len(records)
    anomaly_count = sum(1 for r in records if r.is_anomaly)
    avg_score = sum(r.quality_score or 1.0 for r in records) / total
    dept_data: dict[str, dict] = {}
    for r in records:
        dept = _extract_department(r)
        if dept not in dept_data:
            dept_data[dept] = {"total": 0, "anomalies": 0, "score_sum": 0.0}
        dept_data[dept]["total"] += 1
        if r.is_anomaly:
            dept_data[dept]["anomalies"] += 1
        dept_data[dept]["score_sum"] += r.quality_score or 1.0
    by_dept = []
    for dept, stats in dept_data.items():
        by_dept.append({
            "department": dept, "total_records": stats["total"],
            "anomaly_rate": round(stats["anomalies"] / stats["total"] * 100, 2),
            "avg_quality_score": round(stats["score_sum"] / stats["total"], 4),
        })
    quality = {
        "total_records": total, "missing_rate": 0.0,
        "anomaly_rate": round(anomaly_count / total * 100, 2),
        "avg_quality_score": round(avg_score, 4), "by_department": by_dept,
    }

    return {"summary": summary, "trend": trend, "quality": quality}


def get_summary(db: Session) -> dict:
    """汇总统计：按 data_type 分组，总计数量、异常率、质量分。"""
    with _analytics_lock:
        records = db.query(CleanedRecord).all()

        if not records:
            return {"total_records": 0, "total_batches": 0, "by_type": []}

        batch_ids = set(r.batch_id for r in records)
        df = pd.DataFrame(
            [
                {
                    "data_type": r.data_type or "unknown",
                    "record_date": _extract_date(r),
                    "is_anomaly": r.is_anomaly,
                    "quality_score": r.quality_score or 1.0,
                }
                for r in records
            ]
        )

        by_type = []
        for dtype, group in df.groupby("data_type"):
            dates = group["record_date"].dropna()
            by_type.append(
                {
                    "data_type": dtype,
                    "record_count": len(group),
                    "date_range_start": dates.min().isoformat() if not dates.empty else None,
                    "date_range_end": dates.max().isoformat() if not dates.empty else None,
                    "anomaly_count": int(group["is_anomaly"].sum()),
                    "anomaly_rate": round(float(group["is_anomaly"].mean()) * 100, 2),
                    "avg_quality_score": round(float(group["quality_score"].mean()), 4),
                }
            )

        return {
            "total_records": len(records),
            "total_batches": len(batch_ids),
            "by_type": by_type,
        }


def get_trend(
    db: Session,
    metric: str = "record_count",
    data_type: str = "",
    granularity: str = "month",
) -> dict:
    """趋势分析：按时间粒度聚合指标。"""
    with _analytics_lock:
        query = db.query(CleanedRecord)
        if data_type:
            query = query.filter(CleanedRecord.data_type == data_type)
        records = query.all()

        if not records:
            return {"metric": metric, "data_points": []}

        df = pd.DataFrame(
            [
                {
                    "record_date": _extract_date(r),
                    "data_type": r.data_type or "unknown",
                    "is_anomaly": r.is_anomaly,
                }
                for r in records
            ]
        )
        df["record_date"] = pd.to_datetime(df["record_date"], errors="coerce")
        df = df.dropna(subset=["record_date"])

        if granularity == "month":
            df["period"] = df["record_date"].dt.strftime("%Y-%m")
        else:
            df["period"] = df["record_date"].dt.strftime("%Y-%m-%d")

        grouped = df.groupby(["period", "data_type"])
        data_points = []

        for (period, dtype), group in grouped:
            if metric == "anomaly_rate":
                value = round(float(group["is_anomaly"].mean()) * 100, 2)
            else:
                value = len(group)
            data_points.append({"period": period, "value": value, "data_type": dtype})

        data_points.sort(key=lambda x: x["period"])
        return {"metric": metric, "data_points": data_points}


def get_quality_report(db: Session) -> dict:
    """数据质量报告：全局缺失率、异常率、按部门质量分。"""
    with _analytics_lock:
        records = db.query(CleanedRecord).all()
        if not records:
            return {
                "total_records": 0,
                "missing_rate": 0,
                "anomaly_rate": 0,
                "avg_quality_score": 0,
                "by_department": [],
            }

        total = len(records)
        anomaly_count = sum(1 for r in records if r.is_anomaly)
        avg_score = sum(r.quality_score or 1.0 for r in records) / total

        dept_data: dict[str, dict] = {}
        for r in records:
            dept = _extract_department(r)
            if dept not in dept_data:
                dept_data[dept] = {"total": 0, "anomalies": 0, "score_sum": 0.0}
            dept_data[dept]["total"] += 1
            if r.is_anomaly:
                dept_data[dept]["anomalies"] += 1
            dept_data[dept]["score_sum"] += r.quality_score or 1.0

        by_dept = []
        for dept, stats in dept_data.items():
            by_dept.append(
                {
                    "department": dept,
                    "total_records": stats["total"],
                    "anomaly_rate": round(stats["anomalies"] / stats["total"] * 100, 2),
                    "avg_quality_score": round(stats["score_sum"] / stats["total"], 4),
                }
            )

        return {
            "total_records": total,
            "missing_rate": 0.0,
            "anomaly_rate": round(anomaly_count / total * 100, 2),
            "avg_quality_score": round(avg_score, 4),
            "by_department": by_dept,
        }


def export_to_excel(
    db: Session,
    data_types: list[str] | None = None,
    date_start: str | None = None,
    date_end: str | None = None,
    include_anomalies: bool = True,
) -> str:
    """导出清洗后数据为 Excel 文件，返回文件路径。"""
    query = db.query(CleanedRecord)

    if data_types:
        query = query.filter(CleanedRecord.data_type.in_(data_types))
    if date_start:
        query = query.filter(CleanedRecord.record_date >= date_start)
    if date_end:
        query = query.filter(CleanedRecord.record_date <= date_end)
    if not include_anomalies:
        query = query.filter(CleanedRecord.is_anomaly == False)

    records = query.order_by(CleanedRecord.record_date).all()

    if not records:
        raise ValueError("没有符合条件的数据可导出")

    # 构建导出 DataFrame
    rows = []
    for r in records:
        row = {
            "ID": r.id,
            "批次": r.batch_id,
            "员工姓名": r.employee_name,
            "工号": r.employee_id,
            "部门": _extract_department(r),
            "数据类型": r.data_type,
            "业务日期": _extract_date(r),
            "质量分": r.quality_score,
            "是否异常": "是" if r.is_anomaly else "否",
            "异常原因": r.anomaly_reason or "",
        }
        # 展开 business_data JSON 字段
        if r.business_data:
            for k, v in r.business_data.items():
                row[f"业务_{k}"] = v
        rows.append(row)

    df = pd.DataFrame(rows)

    # 生成文件
    os.makedirs(settings.EXPORT_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"data_export_{timestamp}.xlsx"
    filepath = os.path.join(settings.EXPORT_DIR, filename)

    df.to_excel(filepath, index=False, engine="openpyxl")
    logger.info(f"数据导出成功: {filepath}, rows={len(df)}")

    return filepath
