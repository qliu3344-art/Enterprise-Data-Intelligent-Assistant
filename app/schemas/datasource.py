from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


class DataSourceCreate(BaseModel):
    """创建/注册数据源。"""

    name: str = Field(..., min_length=1, max_length=100, description="数据源名称")
    source_type: str = Field(..., pattern="^(excel|csv|mysql|pdf)$")

    # 文件路径（excel/csv/pdf 用）
    file_path: Optional[str] = Field(None, max_length=500)

    # MySQL 连接信息
    db_host: Optional[str] = Field(None, max_length=200)
    db_port: int = 3306
    db_name: Optional[str] = Field(None, max_length=100)
    db_user: Optional[str] = Field(None, max_length=100)
    db_password: Optional[str] = Field(None, max_length=200)
    db_query: Optional[str] = Field(None, max_length=1000)

    # 解析配置
    sheet_name: Optional[str] = None
    delimiter: str = ","
    encoding: str = "utf-8"
    skip_rows: int = 0


class DataSourceUpdate(BaseModel):
    """更新数据源配置。"""

    name: Optional[str] = Field(None, min_length=1, max_length=100)
    file_path: Optional[str] = None
    db_host: Optional[str] = None
    db_port: Optional[int] = None
    db_name: Optional[str] = None
    db_user: Optional[str] = None
    db_password: Optional[str] = None
    db_query: Optional[str] = None
    sheet_name: Optional[str] = None
    delimiter: Optional[str] = None
    encoding: Optional[str] = None
    skip_rows: Optional[int] = None
    status: Optional[str] = Field(None, pattern="^(active|inactive|error)$")


class DataSourceResponse(BaseModel):
    """数据源响应。"""

    id: int
    name: str
    source_type: str
    file_path: Optional[str] = None
    db_host: Optional[str] = None
    db_port: Optional[int] = None
    db_name: Optional[str] = None
    db_user: Optional[str] = None
    db_query: Optional[str] = None
    sheet_name: Optional[str] = None
    delimiter: str
    encoding: str
    skip_rows: int
    status: str
    last_collect_at: Optional[datetime] = None
    last_collect_count: int
    error_message: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class DataSourceListResponse(BaseModel):
    """数据源列表响应。"""

    items: list[DataSourceResponse]
    total: int


class DataSourceTestResponse(BaseModel):
    """连接测试响应。"""

    success: bool
    message: str
    row_count: Optional[int] = None
    columns: Optional[list[str]] = None
