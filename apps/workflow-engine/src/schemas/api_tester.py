"""Schemas for the API Tester (Postman-lite)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


HTTP_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}


class ApiTestFilePart(BaseModel):
    """One file in a multipart/form-data upload, base64-encoded over the wire."""

    field: str
    filename: str
    content_type: str | None = None
    content_b64: str  # Raw bytes b64-encoded by the client.


class ApiTestFileMeta(BaseModel):
    """Metadata-only persisted record of a multipart file (no bytes)."""

    field: str
    filename: str
    content_type: str | None = None
    size: int


class ApiTestExecuteRequest(BaseModel):
    """User-supplied request spec to execute and persist."""

    name: str | None = None
    method: str
    url: str
    headers: dict[str, str] = Field(default_factory=dict)
    # Raw body text. Used when files is empty. JSON pre-stringified.
    body: str | None = None
    # When non-empty the request is sent as multipart/form-data; `body` is
    # ignored and `headers["Content-Type"]` is overwritten by httpx so the
    # boundary is correct. Text fields go alongside in `form_fields`.
    files: list[ApiTestFilePart] = Field(default_factory=list)
    # Plain text fields included in a multipart upload (paired with files).
    form_fields: dict[str, str] = Field(default_factory=dict)


class ApiTestExecutionResponse(BaseModel):
    """Full captured execution returned to the client."""

    id: str
    name: str | None
    method: str
    url: str
    request_headers: dict[str, Any]
    request_body_text: str | None
    request_files: list[ApiTestFileMeta] | None = None
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
