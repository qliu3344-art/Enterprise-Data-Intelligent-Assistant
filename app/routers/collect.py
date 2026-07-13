"""数据采集 API。"""

from fastapi import APIRouter, Depends, BackgroundTasks
from sqlalchemy.orm import Session

from app.database import get_db
from app.exceptions import NotFoundException
from app.models.datasource import DataSource
from app.models.raw_record import RawRecord
from app.schemas.collect import BatchCollectRequest
from app.services.collector import collect_from_source, collect_batch

router = APIRouter(prefix="/collect", tags=["数据采集"])


# ⚠️ /batch 必须在 /{source_id} 之前注册，否则 FastAPI 会把 "batch" 当作 source_id 解析
@router.post("/batch", response_model=dict)
def trigger_batch_collect(
    body: BatchCollectRequest,
    db: Session = Depends(get_db),
):
    """批量采集多个数据源。"""
    sources = (
        db.query(DataSource)
        .filter(DataSource.id.in_(body.source_ids))
        .all()
    )
    if not sources:
        raise NotFoundException("未找到指定的数据源")

    result = collect_batch(sources, db)
    return {"code": 200, "message": "批量采集完成", "data": result}


@router.post("/{source_id}", response_model=dict)
def trigger_single_collect(
    source_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """触发单源采集。"""
    source = db.query(DataSource).filter(DataSource.id == source_id).first()
    if not source:
        raise NotFoundException(f"数据源不存在: id={source_id}")

    result = collect_from_source(source, db)
    return {"code": 200, "message": "采集完成", "data": result}


@router.get("/history", response_model=dict)
def list_collect_history(
    page: int = 1,
    page_size: int = 20,
    source_id: int = 0,
    db: Session = Depends(get_db),
):
    """采集历史列表（按批次聚合）。"""
    from sqlalchemy import func

    query = db.query(RawRecord)
    if source_id > 0:
        query = query.filter(RawRecord.source_id == source_id)

    agg_query = (
        db.query(
            RawRecord.batch_id,
            RawRecord.source_id,
            func.min(RawRecord.created_at).label("created_at"),
            func.count(RawRecord.id).label("record_count"),
            func.min(RawRecord.status).label("status"),
        )
        .group_by(RawRecord.batch_id, RawRecord.source_id)
        .order_by(func.min(RawRecord.created_at).desc())
    )

    total = agg_query.count()
    items = agg_query.offset((page - 1) * page_size).limit(page_size).all()

    result = []
    for item in items:
        source = db.query(DataSource).filter(DataSource.id == item.source_id).first()
        result.append(
            {
                "batch_id": item.batch_id,
                "source_id": item.source_id,
                "source_name": source.name if source else "未知",
                "source_type": source.source_type if source else "",
                "record_count": item.record_count,
                "status": item.status,
                "created_at": item.created_at.isoformat() if item.created_at else None,
            }
        )

    return {
        "code": 200,
        "message": "ok",
        "data": {"items": result, "total": total},
    }


@router.get("/{batch_id}", response_model=dict)
def get_batch_detail(batch_id: str, db: Session = Depends(get_db)):
    """某批次详情（含原始数据预览，最多 100 条）。"""
    records = (
        db.query(RawRecord)
        .filter(RawRecord.batch_id == batch_id)
        .order_by(RawRecord.source_row_index)
        .limit(100)
        .all()
    )

    if not records:
        raise NotFoundException(f"批次不存在: {batch_id}")

    total = (
        db.query(RawRecord).filter(RawRecord.batch_id == batch_id).count()
    )

    first = records[0]
    source = db.query(DataSource).filter(DataSource.id == first.source_id).first()

    return {
        "code": 200,
        "message": "ok",
        "data": {
            "batch_id": batch_id,
            "source_id": first.source_id,
            "source_name": source.name if source else "未知",
            "source_type": source.source_type if source else "",
            "total_records": total,
            "status": first.status,
            "raw_records": [
                {
                    "id": r.id,
                    "raw_data": r.raw_data,
                    "source_row_index": r.source_row_index,
                }
                for r in records
            ],
            "created_at": first.created_at.isoformat() if first.created_at else None,
        },
    }
