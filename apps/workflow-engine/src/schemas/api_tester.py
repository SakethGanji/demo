"""Schemas for the API Tester (Postman-lite)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


HTTP_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}


class ApiTestExecuteRequest(BaseModel):
    """User-supplied request spec to execute and persist."""

    name: str | None = None
    method: str
    url: str
    headers: dict[str, str] = Field(default_factory=dict)
    body: str | None = None  # Raw body text. JSON should be pre-stringified.


class ApiTestExecutionResponse(BaseModel):
    """Full captured execution returned to the client."""

    id: str
    name: str | None
    method: str
    url: str
    request_headers: dict[str, Any]
    request_body_text: str | None
    response_status: int | None
    response_headers: dict[str, Any]
    response_content_type: str | None
    response_size: int
    response_body_b64: str | None
    response_truncated: bool
    latency_ms: float | None
    error: str | None
    created_at: datetime


class ApiTestExecutionListItem(BaseModel):
    """Compact list-view row."""

    id: str
    name: str | None
    method: str
    url: str
    response_status: int | None
    response_content_type: str | None
    latency_ms: float | None
    error: str | None
    created_at: datetime


class ApiTestExecutionRenameRequest(BaseModel):
    name: str | None = None
