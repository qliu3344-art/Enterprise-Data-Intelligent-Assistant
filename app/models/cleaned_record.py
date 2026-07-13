from datetime import datetime

from sqlalchemy import Column, Integer, String, Text, Float, Boolean, Date, DateTime, JSON, ForeignKey, Index
from app.database import Base


class CleanedRecord(Base):
    """清洗后统一记录表。"""

    __tablename__ = "cleaned_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    batch_id = Column(String(50), nullable=False)
    source_id = Column(
        Integer, ForeignKey("data_sources.id", ondelete="CASCADE"), nullable=False
    )

    # 统一 schema（4 个部门的通用维度）
    employee_name = Column(String(100), comment="员工姓名")
    employee_id = Column(String(50), comment="工号")
    department = Column(String(100), comment="部门")
    data_type = Column(
        String(50), comment="数据类型：attendance/sales/customer/operation"
    )
    record_date = Column(Date, comment="业务日期")

    # 以 JSON 存储具体业务字段（灵活扩展）
    business_data = Column(JSON, nullable=False, comment="标准化后的业务字段")

    # 质量标记
    quality_score = Column(Float, default=1.0, comment="数据质量分(0-1)")
    is_anomaly = Column(Boolean, default=False, comment="是否被标记为异常")
    anomaly_reason = Column(Text, comment="异常原因")

    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        Index("idx_cleaned_batch_id", "batch_id"),
        Index("idx_data_type", "data_type"),
        Index("idx_record_date", "record_date"),
    )

    def __repr__(self) -> str:
        return f"<CleanedRecord(id={self.id}, batch='{self.batch_id}', type='{self.data_type}')>"
