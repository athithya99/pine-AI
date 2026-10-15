from typing import Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    size: int


class ImportRowError(BaseModel):
    row: int
    errors: list[str]


class ImportResult(BaseModel):
    total_rows: int
    created: int
    updated: int
    failed: int
    errors: list[ImportRowError]
