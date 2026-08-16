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
    error_kind: str | None = None   # machine-readable: invalid-file | upload-rejected |
                                    # sheet-not-found | processing-error. The sync path
                                    # returns this as a problem+json `code`; async
                                    # pollers need it here or they can only read prose.
    message: str | None = None


class FileEntry(BaseModel):
    """A single file/artifact in a storage listing."""

    key: str
    filename: str
    size_bytes: int
    file_type: str = Field(
        description="Artifact kind: sample_output, query_output, export, …")
    dataset_id: str | None = None
    created_at: str | None = None


class SheetReplaceResponse(BaseModel):
    """The new version produced by replacing ONE sheet's data.

    Typed because the route is the only JSON-returning one in this module that
    used a bare ``dict``: its six fields were an untyped object in the OpenAPI
    document, so a generated client got no ``reused_sheets`` and no
    ``version_number`` to link the user to what was just created.
    """

    dataset_id: str
    version_id: str
    version_number: int
    replaced_sheet: str = Field(description="The sheet whose data was replaced")
    reused_sheets: list[str] = Field(
        description="Sheets carried over copy-on-write from the base version")
    row_count: int = Field(
        description="Total rows across every sheet of the new version")


class ExportResponse(BaseModel):
    """A stored result file converted to another format (§12)."""

    export_file: str = Field(description="New filename — download via GET /samples/{export_file}")
    format: str
    size_bytes: int
    media_type: str
    source_file: str


class StorageUsageResponse(BaseModel):
    """Storage usage breakdown."""

    total_bytes: int
    datasets_bytes: int
    samples_bytes: int
    exports_bytes: int
    uploads_bytes: int




class RetentionRule(BaseModel):
    """How long one artifact kind is kept."""

    artifact_type: str
    retention_days: int | None = Field(
        description="Days kept after creation; null means kept indefinitely")


class RetentionPolicyResponse(BaseModel):
    """The retention policy, plus what is currently due for collection."""

    rules: list[RetentionRule]
    orphan_grace_hours: int
    expired_pending: int = Field(
        description="Artifacts already past their deadline, awaiting the sweep")


class GcResponse(BaseModel):
    """Result of one garbage-collection sweep."""

    expired_deleted: int
    orphans_deleted: int
    bytes_freed: int
    by_type: dict[str, int] = Field(default_factory=dict)
    more_remaining: bool = Field(
        default=False,
        description=(
            "The sweep stopped at its per-call limit with collectable items "
            "left over; call again. False means the pass reached the end of "
            "the backlog, so zero deletions really does mean nothing to do."))
