"""App-related Pydantic schemas."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


# ── Slug rules ───────────────────────────────────────────────────────────────
# Lowercase alphanumeric with internal single hyphens. 3–63 chars.
# Reserved labels are checked at the service layer (regex doesn't capture them).

SLUG_PATTERN = r"^[a-z0-9](?:-?[a-z0-9])*$"
SLUG_MIN_LEN = 3
SLUG_MAX_LEN = 63

AccessMode = Literal["private", "public", "password"]


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
    api_execution_ids: list[str] = Field(default_factory=list, description="Saved API tester executions this app may replay")


class AppUpdateRequest(BaseModel):
    """Request schema for updating an app."""

    name: str | None = Field(None, min_length=1, max_length=255, description="App name")
    definition: dict[str, Any] | None = Field(None, description="App definition")
    description: str | None = Field(None, max_length=1000, description="App description")
    workflow_ids: list[str] | None = Field(None, description="Linked workflow IDs for data binding")
    api_execution_ids: list[str] | None = Field(None, description="Saved API tester executions this app may replay")
    source_code: str | None = Field(None, description="TSX source code")
    create_version: bool = Field(False, description="Atomically create a version with this save")
    version_trigger: str = Field("manual", description="Version trigger type: ai, manual, publish")
    version_prompt: str | None = Field(None, description="User message that triggered AI generation")
    files: list[AppFilePayload] = Field(default_factory=list, description="Multi-file app contents")
    # Publishing settings — editable from the studio prior to publish.
    slug: str | None = Field(
        None,
        min_length=SLUG_MIN_LEN,
        max_length=SLUG_MAX_LEN,
        pattern=SLUG_PATTERN,
        description="URL slug for the public app",
    )
    access: AccessMode | None = Field(None, description="Access mode: private, public, password")
    access_password: str | None = Field(
        None,
        min_length=4,
        max_length=128,
        description="Plaintext password (write-only). Hashed at rest. Pass empty string to clear.",
    )
    embed_enabled: bool | None = Field(None, description="Allow embedding in iframes")


class AppPublishRequest(BaseModel):
    """Request schema for publishing. All fields optional — present fields override
    the app's current settings before publishing."""

    slug: str | None = Field(
        None, min_length=SLUG_MIN_LEN, max_length=SLUG_MAX_LEN, pattern=SLUG_PATTERN
    )
    access: AccessMode | None = None
    access_password: str | None = Field(None, min_length=4, max_length=128)


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
    bundle_hash: str | None = None
    bundled_at: str | None = None


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
    api_execution_ids: list[str] = []
    source_code: str | None = None
    files: list[AppFilePayload] = Field(default_factory=list)
    current_version: AppVersionResponse | None = None
    created_at: str
    updated_at: str
    # Publishing fields. `access_password_set` is a bool because the actual hash
    # is never returned to clients.
    slug: str | None = None
    access: AccessMode = "private"
    access_password_set: bool = False
    embed_enabled: bool = False
    published_at: str | None = None
    published_version: AppVersionResponse | None = None


class AppPublishResponse(BaseModel):
    """Response for publish."""

    id: str
    active: bool
    version_id: int | None = None
    slug: str | None = None
    bundle_hash: str | None = None
    public_url: str | None = None


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
