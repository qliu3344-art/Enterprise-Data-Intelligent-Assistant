from datetime import datetime

from sqlalchemy import Column, Integer, String, Text, DateTime, Enum as SQLEnum
from app.database import Base


class DataSource(Base):
    """数据源配置表。"""

    __tablename__ = "data_sources"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False, comment="数据源名称")

    source_type = Column(
        SQLEnum("excel", "csv", "mysql", "pdf", name="source_type_enum"),
        nullable=False,
    )

    # 文件路径（excel/csv/pdf 使用）
    file_path = Column(String(500), comment="文件路径")

    # MySQL 连接信息（mysql 类型使用）
    db_host = Column(String(200))
    db_port = Column(Integer, default=3306)
    db_name = Column(String(100))
    db_user = Column(String(100))
    db_password = Column(String(200))
    db_query = Column(String(1000), comment="SQL 查询语句或表名")

    # 配置
    sheet_name = Column(String(100), comment="Excel sheet 名")
    delimiter = Column('delimiter', String(10), default=",", comment="CSV 分隔符", quote=True)
    encoding = Column(String(20), default="utf-8", comment="文件编码")
    skip_rows = Column(Integer, default=0, comment="跳过表头行数")

    # 状态
    status = Column(
        SQLEnum("active", "inactive", "error", name="datasource_status_enum"),
        default="active",
    )
    last_collect_at = Column(DateTime, comment="上次采集时间")
    last_collect_count = Column(Integer, default=0, comment="上次采集条数")
    error_message = Column(Text, comment="最近错误信息")

    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    def __repr__(self) -> str:
        return f"<DataSource(id={self.id}, name='{self.name}', type='{self.source_type}')>"
