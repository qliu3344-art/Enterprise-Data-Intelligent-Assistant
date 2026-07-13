from datetime import datetime

from sqlalchemy import Column, Integer, String, Float, DateTime, JSON, Index
from app.database import Base


class PipelineLog(Base):
    """清洗流水日志表。"""

    __tablename__ = "pipeline_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    batch_id = Column(String(50), nullable=False)

    # 每一步的执行统计
    step_name = Column(
        String(50), nullable=False, comment="dedup / fill_missing / anomaly_detect"
    )
    input_count = Column(Integer, default=0)
    output_count = Column(Integer, default=0)
    affected_count = Column(Integer, default=0, comment="被修改/标记的记录数")

    # 执行详情
    details = Column(JSON, comment="清洗规则及 LLM 判断详情")
    duration_seconds = Column(Float, comment="本步骤耗时（秒）")

    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (Index("idx_pipeline_batch_id", "batch_id"),)

    def __repr__(self) -> str:
        return f"<PipelineLog(id={self.id}, batch='{self.batch_id}', step='{self.step_name}')>"
