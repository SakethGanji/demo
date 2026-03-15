"""App-related Pydantic schemas."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class AppFilePayload(BaseModel):
    """A single file in a multi-file app."""

    path: str
    content: str
    file_type: str = "tsx"
    parsed_index: dict[str, Any] | None = None


class AppCreateRequest(BaseModel):
    """Request schema for creating an app."""

    name: str = Field(..., min_length=1, max_length=255, description="App name")
    definition: dict[str, Any] = Field(..., description="App definition (nodes, rootNodeId, etc.)")
    description: str | None = Field(None, max_length=1000, description="App description")
    folder_id: str | None = Field(None, description="Folder to organize this app in")
    workflow_ids: list[str] = Field(default_factory=list, description="Linked workflow IDs for data binding")


class AppUpdateRequest(BaseModel):
    """Request schema for updating an app."""

    name: str | None = Field(None, min_length=1, max_length=255, description="App name")
    definition: dict[str, Any] | None = Field(None, description="App definition")
    description: str | None = Field(None, max_length=1000, description="App description")
    workflow_ids: list[str] | None = Field(None, description="Linked workflow IDs for data binding")
    source_code: str | None = Field(None, description="TSX source code")
    create_version: bool = Field(False, description="Atomically create a version with this save")
    version_trigger: str = Field("manual", description="Version trigger type: ai, manual, publish")
    version_prompt: str | None = Field(None, description="User message that triggered AI generation")
    files: list[AppFilePayload] = Field(default_factory=list, description="Multi-file app contents")


class AppListItem(BaseModel):
    """Schema for app in list response."""

    id: str
    name: str
    created_at: str
    updated_at: str


# ── Version schemas ──────────────────────────────────────────────────────────


class AppVersionResponse(BaseModel):
    """Version info returned inline with app detail."""

    id: int
    version_number: int
    parent_version_id: int | None = None
    trigger: str
    label: str | None = None
    prompt: str | None = None
    message: str | None = None
    created_at: str


class AppVersionDetail(AppVersionResponse):
    """Single version with source_code (for fetching a specific version)."""

    source_code: str
    files: list[AppFilePayload] = Field(default_factory=list)


class AppVersionListItem(BaseModel):
    """Lightweight version for history list (no source_code)."""

    id: int
    version_number: int
    parent_version_id: int | None = None
    trigger: str
    label: str | None = None
    prompt: str | None = None
    message: str | None = None
    created_at: str


class AppDetailResponse(BaseModel):
    """Detailed app response."""

    id: str
    name: str
    definition: dict[str, Any]
    active: bool
    workflow_ids: list[str] = []
    source_code: str | None = None
    files: list[AppFilePayload] = Field(default_factory=list)
    current_version: AppVersionResponse | None = None
    created_at: str
    updated_at: str


class AppPublishResponse(BaseModel):
    """Response for publish."""

    id: str
    active: bool
    version_id: int | None = None


class CreateVersionRequest(BaseModel):
    """Request for manually creating a version."""

    source_code: str
    trigger: str = "manual"
    label: str | None = None
    prompt: str | None = None
    message: str | None = None


class UpdateVersionLabelRequest(BaseModel):
    """Request for renaming a version."""

    label: str | None = None
