from datetime import datetime

from sqlalchemy import Column, Integer, String, Text, DateTime, JSON, ForeignKey, Index, Enum as SQLEnum
from app.database import Base


class RawRecord(Base):
    """原始采集记录表。"""

    __tablename__ = "raw_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source_id = Column(
        Integer, ForeignKey("data_sources.id", ondelete="CASCADE"), nullable=False
    )
    batch_id = Column(String(50), nullable=False, comment="采集批次号")

    # 原始数据以 JSON 存储（保留所有原始字段，不做清洗）
    raw_data = Column(JSON, nullable=False)

    # 元信息
    source_row_index = Column(Integer, comment="在原文件中的行号")

    # 状态
    status = Column(
        SQLEnum("raw", "aligned", "cleaned", "error", name="raw_record_status_enum"),
        default="raw",
    )
    error_info = Column(Text, comment="清洗失败原因")

    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        Index("idx_source_id", "source_id"),
        Index("idx_batch_id", "batch_id"),
        Index("idx_status", "status"),
    )

    def __repr__(self) -> str:
        return f"<RawRecord(id={self.id}, batch='{self.batch_id}', status='{self.status}')>"
