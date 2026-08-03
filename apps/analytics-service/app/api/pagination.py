"""Pagination primitives shared by every list endpoint.

A single ``Page[T]`` envelope keeps list responses uniform:

    {"items": [...], "total": 123, "limit": 50, "offset": 0}
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, Sequence, TypeVar

from fastapi import Query
from pydantic import BaseModel

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    """Uniform pagination envelope for list responses."""

    items: list[T]
    total: int
    limit: int
    offset: int

    @classmethod
    def of(cls, items: Sequence[T], total: int, params: "PageParams") -> "Page[T]":
        return cls(items=list(items), total=total, limit=params.limit, offset=params.offset)


@dataclass(slots=True)
class PageParams:
    """Parsed ``limit``/``offset`` query parameters."""

    limit: int
    offset: int


def pagination(
    limit: int = Query(50, ge=1, le=200, description="Max items to return (1-200)"),
    offset: int = Query(0, ge=0, description="Number of items to skip"),
) -> PageParams:
    """FastAPI dependency yielding validated pagination parameters."""
    return PageParams(limit=limit, offset=offset)
