"""App-related Pydantic schemas."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class AppCreateRequest(BaseModel):
    """Request schema for creating an app."""

    name: str = Field(..., min_length=1, max_length=255, description="App name")
    definition: dict[str, Any] = Field(..., description="App definition (nodes, rootNodeId, etc.)")
    description: str | None = Field(None, max_length=1000, description="App description")
    folder_id: str | None = Field(None, description="Folder to organize this app in")


class AppUpdateRequest(BaseModel):
    """Request schema for updating an app."""

    name: str | None = Field(None, min_length=1, max_length=255, description="App name")
    definition: dict[str, Any] | None = Field(None, description="App definition")
    description: str | None = Field(None, max_length=1000, description="App description")


class AppListItem(BaseModel):
    """Schema for app in list response."""

    id: str
    name: str
    created_at: str
    updated_at: str


class AppDetailResponse(BaseModel):
    """Detailed app response."""

    id: str
    name: str
    definition: dict[str, Any]
    active: bool
    created_at: str
    updated_at: str


class AppPublishResponse(BaseModel):
    """Response for publish."""

    id: str
    active: bool
    version_id: int | None = None
