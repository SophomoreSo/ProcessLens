from __future__ import annotations

from typing import Any, Generic, TypeVar
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict

T = TypeVar("T")


class ApiModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ErrorBody(BaseModel):
    code: str
    message: str
    details: Any | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody


class Page(ApiModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int


class DurationStats(BaseModel):
    """Duration statistics in seconds. ``p90`` uses linear interpolation (numpy default)."""

    n: int
    mean: float | None = None
    median: float | None = None
    p90: float | None = None
    min: float | None = None
    max: float | None = None


def validate_timezone(name: str) -> str:
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"Unknown IANA timezone: {name!r}") from exc
    return name
