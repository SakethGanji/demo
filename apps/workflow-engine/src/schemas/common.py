"""Common schemas used across the API."""

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class SuccessResponse(BaseModel):
    """Generic success response."""

    success: bool = True
    message: str | None = None


class ErrorResponse(BaseModel):
    """Generic error response."""

    success: bool = False
    error: str
    details: dict[str, Any] | None = None
    code: str | None = None


class PaginatedResponse(BaseModel, Generic[T]):
    """Generic paginated response."""

    items: list[T]
    total: int
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
    has_next: bool
    has_prev: bool


class HealthResponse(BaseModel):
    """Health check response."""

    status: str = "healthy"
    version: str
    uptime_seconds: float | None = None


class RootResponse(BaseModel):
    """Root endpoint response."""

    name: str
    version: str
    status: str
