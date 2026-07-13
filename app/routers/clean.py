"""数据清洗 API — 触发清洗、查看日志、审核异常。"""

import time
from datetime import datetime
import json

import pandas as pd
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.exceptions import NotFoundException
from app.logger import logger
from app.models.raw_record import RawRecord
from app.models.cleaned_record import CleanedRecord
from app.models.pipeline_log import PipelineLog
from app.models.schema_mapping import SchemaMapping
from app.models.datasource import DataSource
from app.schemas.clean import AnomalyUpdateRequest
from app.services.cleaner import CleaningPipeline

router = APIRouter(prefix="/clean", tags=["数据清洗"])


@router.post("/{batch_id}", response_model=dict)
def trigger_clean(batch_id: str, db: Session = Depends(get_db)):
    """对某批次原始数据执行完整清洗流程。"""
    # 1. 查询该批次的原始记录
    raw_records = (
        db.query(RawRecord)
        .filter(RawRecord.batch_id == batch_id)
        .order_by(RawRecord.source_row_index)
        .all()
    )

    if not raw_records:
        raise NotFoundException(f"批次不存在或无数据: {batch_id}")

    # 2. 转为 DataFrame
    df = pd.DataFrame([r.raw_data for r in raw_records])
    source = db.query(DataSource).filter(DataSource.id == raw_records[0].source_id).first()

    # 3. 获取表头映射（如果已有 LLM 映射结果）
    data_type_hint = _infer_data_type(source.name if source else "")
    mapping_record = (
        db.query(SchemaMapping)
        .filter(SchemaMapping.source_id == raw_records[0].source_id)
        .order_by(SchemaMapping.created_at.desc())
        .first()
    )

    # 4. 执行清洗
    context = {
        "data_type": data_type_hint,
        "date_range": _compute_date_range(df),
    }

    pipeline = CleaningPipeline(batch_id)
    df_cleaned = pipeline.run(df, context)

    # 5. 写入 cleaned_records 表
    cleaned_ids = []
    for idx, row in df_cleaned.iterrows():
        business_cols = _extract_business_columns(row, mapping_record)
        record = CleanedRecord(
            batch_id=batch_id,
            source_id=raw_records[0].source_id,
            employee_name=row.get("employee_name") if "employee_name" in df_cleaned.columns else None,
            employee_id=str(row.get("employee_id")) if "employee_id" in df_cleaned.columns else None,
            department=row.get("department") if "department" in df_cleaned.columns else None,
            data_type=data_type_hint,
            record_date=_safe_date(row.get("record_date")) if "record_date" in df_cleaned.columns else None,
            business_data=business_cols,
            quality_score=1.0,
            is_anomaly=bool(row.get("is_anomaly", False)),
            anomaly_reason=str(row.get("anomaly_reason", "")) if row.get("anomaly_reason") else None,
        )
        db.add(record)
        db.flush()
        cleaned_ids.append(record.id)

    # 6. 写入清洗日志
    for log_entry in pipeline.logs:
        plog = PipelineLog(
            batch_id=batch_id,
            step_name=log_entry["step"],
            input_count=log_entry["input_count"],
            output_count=log_entry["output_count"],
            affected_count=abs(log_entry["input_count"] - log_entry["output_count"]),
            details=log_entry["details"],
            duration_seconds=None,
        )
        db.add(plog)

    # 7. 更新 raw_records 状态
    for r in raw_records:
        r.status = "cleaned"

    db.commit()

    anomaly_count = int(df_cleaned["is_anomaly"].sum()) if "is_anomaly" in df_cleaned.columns else 0

    return {
        "code": 200,
        "message": "清洗完成",
        "data": {
            "batch_id": batch_id,
            "status": "completed",
            "total_input": len(raw_records),
            "total_output": len(cleaned_ids),
            "anomaly_count": anomaly_count,
            "steps": pipeline.logs,
            "duration_seconds": None,
        },
    }


@router.get("/{batch_id}/logs", response_model=dict)
def get_clean_logs(batch_id: str, db: Session = Depends(get_db)):
    """查看某批次的清洗日志。"""
    logs = (
        db.query(PipelineLog)
        .filter(PipelineLog.batch_id == batch_id)
        .order_by(PipelineLog.created_at)
        .all()
    )
    return {
        "code": 200,
        "message": "ok",
        "data": {
            "items": [
                {
                    "id": log.id,
                    "batch_id": log.batch_id,
                    "step_name": log.step_name,
                    "input_count": log.input_count,
                    "output_count": log.output_count,
                    "affected_count": log.affected_count,
                    "details": log.details,
                    "duration_seconds": log.duration_seconds,
                    "created_at": log.created_at.isoformat() if log.created_at else None,
                }
                for log in logs
            ]
        },
    }


@router.get("/anomalies", response_model=dict)
def list_anomalies(
    page: int = 1,
    page_size: int = 20,
    data_type: str = "",
    db: Session = Depends(get_db),
):
    """查询已标记为异常的记录。"""
    query = db.query(CleanedRecord).filter(CleanedRecord.is_anomaly == True)
    if data_type:
        query = query.filter(CleanedRecord.data_type == data_type)

    total = query.count()
    items = (
        query.order_by(CleanedRecord.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    return {
        "code": 200,
        "message": "ok",
        "data": {
            "items": [
                {
                    "id": r.id,
                    "batch_id": r.batch_id,
                    "source_id": r.source_id,
                    "employee_name": r.employee_name,
                    "employee_id": r.employee_id,
                    "department": r.department,
                    "data_type": r.data_type,
                    "record_date": r.record_date.isoformat() if r.record_date else None,
                    "business_data": r.business_data,
                    "quality_score": r.quality_score,
                    "is_anomaly": r.is_anomaly,
                    "anomaly_reason": r.anomaly_reason,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                }
                for r in items
            ],
            "total": total,
        },
    }


@router.put("/anomalies/{record_id}", response_model=dict)
def update_anomaly(
    record_id: int,
    body: AnomalyUpdateRequest,
    db: Session = Depends(get_db),
):
    """人工修正异常标记（覆盖 LLM 判定）。"""
    record = db.query(CleanedRecord).filter(CleanedRecord.id == record_id).first()
    if not record:
        raise NotFoundException(f"记录不存在: id={record_id}")

    record.is_anomaly = body.is_anomaly
    if body.anomaly_reason is not None:
        record.anomaly_reason = body.anomaly_reason

    db.commit()
    return {"code": 200, "message": "异常标记已更新", "data": None}


# —— 辅助函数 ——
def _infer_data_type(source_name: str) -> str:
    """从数据源名称推断数据类型。"""
    name_lower = source_name.lower()
    if "考勤" in name_lower or "attendance" in name_lower:
        return "attendance"
    elif "销售" in name_lower or "sales" in name_lower:
        return "sales"
    elif "客户" in name_lower or "customer" in name_lower:
        return "customer"
    elif "运营" in name_lower or "operation" in name_lower:
        return "operation"
    return ""


def _compute_date_range(df: pd.DataFrame) -> str:
    """从 DataFrame 推断日期范围。"""
    for col in df.columns:
        if any(kw in col.lower() for kw in ["date", "日期", "时间"]):
            try:
                dates = pd.to_datetime(df[col], errors="coerce").dropna()
                if not dates.empty:
                    return f"{dates.min().date()} ~ {dates.max().date()}"
            except Exception:
                pass
    return "未知"


def _extract_business_columns(row: pd.Series, mapping_record) -> dict:
    """从原始行提取业务字段。如果已有映射，按映射提取；否则全部保留。"""
    result = {}
    if mapping_record and mapping_record.mapping_data:
        # 按映射关系重命名字段
        reverse_map = {v: k for k, v in mapping_record.mapping_data.items()}
        for std_key, orig_key in reverse_map.items():
            if orig_key in row.index:
                val = row[orig_key]
                result[std_key] = val if not pd.isna(val) else None
    else:
        # 无映射时保留所有非空字段
        for k, v in row.items():
            if k not in ("is_anomaly", "anomaly_reason") and not pd.isna(v):
                result[k] = v
    return result


def _safe_date(val) -> datetime | None:
    """安全转换日期。"""
    if val is None or pd.isna(val):
        return None
    try:
        return pd.to_datetime(val).date()
    except Exception:
        return None
