"""Credential-related Pydantic schemas."""

from typing import Any

from pydantic import BaseModel, Field


class CredentialCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    type: str = Field(..., min_length=1, description="e.g. openai, postgres, smtp")
    data: dict[str, Any] = Field(..., description="Credential data (will be encrypted)")
    team_id: str = Field(default="default")


class CredentialUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    type: str | None = Field(default=None, min_length=1)
    data: dict[str, Any] | None = Field(default=None, description="New credential data (will be encrypted)")


class CredentialResponse(BaseModel):
    id: str
    name: str
    type: str
    created_at: str


class CredentialListItem(BaseModel):
    id: str
    name: str
    type: str
    created_at: str
