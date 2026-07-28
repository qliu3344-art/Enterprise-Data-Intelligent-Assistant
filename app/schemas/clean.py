from datetime import datetime
from typing import Any, Optional
from pydantic import BaseModel, Field


class CleanResponse(BaseModel):
    """清洗触发响应。"""

    batch_id: str
    status: str
    total_input: int
    total_output: int
    anomaly_count: int
    steps: list[dict[str, Any]]
    duration_seconds: float


class CleanLogItem(BaseModel):
    """清洗日志项。"""

    id: int
    batch_id: str
    step_name: str
    input_count: int
    output_count: int
    affected_count: int
    details: Optional[dict[str, Any]] = None
    duration_seconds: Optional[float] = None
    created_at: datetime

    class Config:
        from_attributes = True


class AnomalyRecordResponse(BaseModel):
    """异常记录响应。"""

    id: int
    batch_id: str
    source_id: int
    employee_name: Optional[str] = None
    employee_id: Optional[str] = None
    department: Optional[str] = None
    data_type: Optional[str] = None
    record_date: Optional[str] = None
    business_data: dict[str, Any]
    quality_score: float
    is_anomaly: bool
    anomaly_reason: Optional[str] = None
    anomaly_status: str = "normal"
    pending_check_fields: Optional[dict[str, Any]] = None
    created_at: datetime

    class Config:
        from_attributes = True


class AnomalyUpdateRequest(BaseModel):
    """人工修正异常标记。"""

    is_anomaly: bool
    anomaly_reason: Optional[str] = None
