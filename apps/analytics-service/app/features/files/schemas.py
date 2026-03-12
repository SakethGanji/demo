"""Files feature schemas — upload responses, download requests, file listings."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.shared.schemas import ColumnInfo


class UploadResponse(BaseModel):
    """Unified response for all upload methods."""

    dataset_id: str
    version_id: str | None = None
    status: str  # "complete" | "uploaded" | "processing" | "error"
    file_path: str | None = None
    file_size_bytes: int | None = None
    row_count: int | None = None
    column_count: int | None = None
    columns: list[ColumnInfo] | None = None
    preview: list[dict[str, Any]] | None = None
    error: str | None = None
    message: str | None = None


class FileEntry(BaseModel):
    """A single file/artifact in a storage listing."""

    key: str
    filename: str
    size_bytes: int
    file_type: str = Field(description="dataset, sample, export")
    dataset_id: str | None = None
    created_at: str | None = None


class FileListResponse(BaseModel):
    """File listing response."""

    files: list[FileEntry]
    total_count: int


class StorageUsageResponse(BaseModel):
    """Storage usage breakdown."""

    total_bytes: int
    datasets_bytes: int
    samples_bytes: int
    exports_bytes: int
    uploads_bytes: int


