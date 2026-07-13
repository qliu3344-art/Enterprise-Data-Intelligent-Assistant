from typing import Any, Generic, List, TypeVar
from pydantic import BaseModel

T = TypeVar("T")


class APIResponse(BaseModel, Generic[T]):
    """统一 API 响应模型。"""

    code: int = 200
    message: str = "ok"
    data: T | None = None


class PaginatedResponse(BaseModel, Generic[T]):
    """分页响应模型。"""

    items: List[T]
    total: int
    page: int
    page_size: int
