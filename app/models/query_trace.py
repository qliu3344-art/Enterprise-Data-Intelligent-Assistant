"""查询 Trace 表 — 记录每次自然语言查询的执行信息（可观测性基础）。"""

from datetime import datetime

from sqlalchemy import Column, Integer, String, Text, Float, DateTime, JSON, Index

from app.database import Base


class QueryTrace(Base):
    __tablename__ = "query_trace"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(64), nullable=False, comment="会话标识")
    question = Column(Text, nullable=False, comment="用户问题")
    answer = Column(Text, nullable=False, comment="最终答案")
    intent = Column(String(32), default="", comment="意图分类 data_query/doc_query/hybrid")
    mode = Column(String(32), default="", comment="执行模式 agent/rag/hybrid")
    iterations = Column(Integer, default=0, comment="工具调用次数（Agent 迭代轮数）")
    tools_used = Column(JSON, default=list, comment="实际调用的工具列表")
    latency_ms = Column(Float, default=0.0, comment="端到端耗时（毫秒）")
    created_at = Column(DateTime, default=datetime.now, comment="创建时间")

    __table_args__ = (Index("idx_trace_session", "session_id", "created_at"),)
