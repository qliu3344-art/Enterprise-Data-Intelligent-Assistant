from datetime import datetime

from sqlalchemy import Column, Integer, String, Float, Text, DateTime, JSON, ForeignKey, Index
from app.database import Base


class SchemaMapping(Base):
    """表头映射配置表。"""

    __tablename__ = "schema_mappings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source_id = Column(
        Integer, ForeignKey("data_sources.id", ondelete="CASCADE"), nullable=False
    )

    # 映射关系：{"原始表头": "标准字段名", ...}
    mapping_data = Column(JSON, nullable=False, comment="原始表头→标准字段的映射")

    # 未映射的列
    unmapped_columns = Column(JSON, comment="LLM 无法映射的原始列名列表")

    # 元信息
    is_llm_generated = Column(Integer, default=1, comment="是否为 LLM 自动生成")
    confidence = Column(Float, comment="LLM 映射置信度")
    manual_reviewed = Column(Integer, default=0, comment="是否已人工复核")

    created_at = Column(DateTime, default=datetime.now)

    def __repr__(self) -> str:
        return f"<SchemaMapping(id={self.id}, source_id={self.source_id}, reviewed={self.manual_reviewed})>"
