"""用户反馈表 — 记录对查询结果的显式/隐式反馈（数据飞轮的地基）。"""

from datetime import datetime

from sqlalchemy import Column, Integer, String, Text, DateTime, Index

from app.database import Base


class Feedback(Base):
    __tablename__ = "feedback"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(64), default="", comment="会话标识")
    question = Column(Text, nullable=False, comment="用户问题（冗余，方便离线分析）")
    trace_id = Column(Integer, nullable=True, comment="关联 query_trace.id，可空")
    rating = Column(Integer, nullable=False, comment="1 有用 / -1 没用（二值）")
    comment = Column(Text, nullable=True, comment="点踩时的纠错文本，可空")
    source = Column(String(16), default="explicit", comment="explicit 显式 / implicit 隐式")
    created_at = Column(DateTime, default=datetime.now, comment="创建时间")

    __table_args__ = (Index("idx_feedback_trace", "trace_id"),)

    def __repr__(self) -> str:
        return f"<Feedback(id={self.id}, rating={self.rating}, trace_id={self.trace_id})>"
