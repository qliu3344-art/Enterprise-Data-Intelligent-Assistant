from datetime import datetime
from typing import Any, Optional
from pydantic import BaseModel, Field


class CollectResponse(BaseModel):
    """单源采集响应。"""

    batch_id: str
    source_id: int
    source_name: str
    record_count: int
    status: str
    message: str = ""


class BatchCollectRequest(BaseModel):
    """批量采集请求。"""

    source_ids: list[int] = Field(..., min_length=1, max_length=50)


class BatchCollectResponse(BaseModel):
    """批量采集响应。"""

    total_sources: int
    success_count: int
    failed_count: int
    results: list[CollectResponse]


class CollectHistoryItem(BaseModel):
    """采集历史项。"""

    batch_id: str
    source_id: int
    source_name: str
    source_type: str
    record_count: int
    status: str
    created_at: datetime


class BatchDetailResponse(BaseModel):
    """批次详情（含原始数据预览）。"""

    batch_id: str
    source_id: int
    source_name: str
    source_type: str
    total_records: int
    status: str
    raw_records: list[dict[str, Any]]  # 前 100 条预览
    created_at: datetime
