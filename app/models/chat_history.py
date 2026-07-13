"""多轮对话历史表 — 按 session_id 存储用户与 Agent 的问答记录。"""

from datetime import datetime

from sqlalchemy import Column, Integer, String, Text, DateTime, Index
from app.database import Base


class ChatHistory(Base):
    __tablename__ = "chat_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(64), nullable=False, comment="会话标识")
    role = Column(String(16), nullable=False, comment="user / assistant")
    content = Column(Text, nullable=False, comment="消息内容")
    created_at = Column(DateTime, default=datetime.now, comment="创建时间")

    __table_args__ = (Index("idx_chat_session", "session_id", "created_at"),)

    def __repr__(self) -> str:
        return (
            f"<ChatHistory(id={self.id}, session='{self.session_id}', "
            f"role='{self.role}')>"
        )
