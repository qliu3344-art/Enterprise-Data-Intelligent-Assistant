from datetime import date, datetime
from typing import Any, Optional
from pydantic import BaseModel


class SummaryItem(BaseModel):
    """按 data_type 分组的汇总统计项。"""

    data_type: str
    record_count: int
    date_range_start: Optional[str] = None
    date_range_end: Optional[str] = None
    anomaly_count: int
    anomaly_rate: float
    avg_quality_score: float


class SummaryResponse(BaseModel):
    """汇总统计响应。"""

    total_records: int
    total_batches: int
    by_type: list[SummaryItem]


class TrendPoint(BaseModel):
    """趋势数据点。"""

    period: str  # 如 "2025-11"
    value: float
    data_type: str


class TrendResponse(BaseModel):
    """趋势分析响应。"""

    metric: str  # 如 "record_count" / "anomaly_rate"
    data_points: list[TrendPoint]


class QualityReportResponse(BaseModel):
    """数据质量报告。"""

    total_records: int
    missing_rate: float  # 缺失率
    anomaly_rate: float  # 异常率
    avg_quality_score: float
    by_department: list[dict[str, Any]]


class ExportRequest(BaseModel):
    """导出请求。"""

    data_types: Optional[list[str]] = None  # 如 ["attendance", "sales"]
    date_start: Optional[str] = None
    date_end: Optional[str] = None
    include_anomalies: bool = True
