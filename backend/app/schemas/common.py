"""Common/shared Pydantic schemas for API responses."""

from typing import Generic, List, Optional, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class BaseResponse(BaseModel):
    code: int = 0
    message: str = "success"
    data: Optional[dict] = None


class PageResponse(BaseModel, Generic[T]):
    total: int
    page: int
    page_size: int
    items: List[T]


class ErrorResponse(BaseModel):
    code: int
    message: str
    detail: Optional[str] = None
