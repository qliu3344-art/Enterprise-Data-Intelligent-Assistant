"""用户反馈 API — 采集对查询结果的点赞/点踩反馈（数据飞轮的地基）。"""

from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.logger import logger

router = APIRouter(prefix="/feedback", tags=["用户反馈"])


class FeedbackRequest(BaseModel):
    session_id: str = Field(default="", max_length=64, description="会话标识，与查询时一致")
    question: str = Field(..., min_length=1, max_length=500, description="被反馈的问题原文")
    rating: Literal[1, -1] = Field(..., description="1 有用 / -1 没用")
    comment: str = Field(default="", max_length=1000, description="点踩时的纠错文本（可选）")
    source: Literal["explicit", "implicit"] = Field(
        default="explicit", description="explicit 显式 / implicit 隐式"
    )


@router.post("")
def submit_feedback(body: FeedbackRequest, db: Session = Depends(get_db)):
    """提交一条查询反馈。

    前端只需传 question + rating（+ 可选 comment），trace_id 由后端 best-effort
    关联到最近一次同问题的 query_trace——关联失败不影响反馈落库。
    """
    from app.models.feedback import Feedback
    from app.models.query_trace import QueryTrace

    # best-effort 关联最近一次同问题的 trace（用于归因回溯）
    trace_id = None
    try:
        q = db.query(QueryTrace).filter(QueryTrace.question == body.question)
        if body.session_id:
            q = q.filter(QueryTrace.session_id == body.session_id)
        trace = q.order_by(QueryTrace.id.desc()).first()
        trace_id = trace.id if trace else None
    except Exception as e:
        logger.warning(f"反馈关联 trace 失败（不影响落库）: {e}")

    try:
        fb = Feedback(
            session_id=body.session_id,
            question=body.question,
            trace_id=trace_id,
            rating=body.rating,
            comment=body.comment or None,
            source=body.source,
        )
        db.add(fb)
        db.commit()
        return {"code": 200, "message": "ok", "data": {"id": fb.id, "trace_id": trace_id}}
    except Exception as e:
        db.rollback()
        logger.warning(f"反馈写入失败: {e}")
        return {"code": 500, "message": "反馈写入失败", "data": None}


@router.get("")
def list_feedback(rating: int | None = None, limit: int = 50, db: Session = Depends(get_db)):
    """列出反馈（默认最近 50 条），可按 rating 过滤——用于人工 review badcase 候选池。"""
    from app.models.feedback import Feedback

    q = db.query(Feedback)
    if rating is not None:
        q = q.filter(Feedback.rating == rating)
    items = q.order_by(Feedback.id.desc()).limit(limit).all()

    return {
        "code": 200,
        "message": "ok",
        "data": {
            "items": [
                {
                    "id": f.id,
                    "session_id": f.session_id,
                    "question": f.question,
                    "trace_id": f.trace_id,
                    "rating": f.rating,
                    "comment": f.comment,
                    "source": f.source,
                    "created_at": f.created_at.isoformat() if f.created_at else None,
                }
                for f in items
            ]
        },
    }
