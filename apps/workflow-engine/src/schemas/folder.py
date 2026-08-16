"""Folder-related Pydantic schemas."""

from pydantic import BaseModel, Field


class FolderCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    parent_folder_id: str | None = Field(default=None, description="Parent folder ID for nesting")
    team_id: str = Field(default="default")


class FolderUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    parent_folder_id: str | None = Field(default=..., description="New parent folder ID, or null for root")


class FolderResponse(BaseModel):
    id: str
    name: str
    parent_folder_id: str | None
    team_id: str
    created_at: str


class FolderListItem(BaseModel):
    id: str
    name: str
    parent_folder_id: str | None
    created_at: str
