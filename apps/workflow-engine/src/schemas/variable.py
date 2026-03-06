"""Variable-related Pydantic schemas."""

from pydantic import BaseModel, Field


class VariableCreateRequest(BaseModel):
    key: str = Field(..., min_length=1, max_length=255)
    value: str = Field(...)
    type: str = Field(default="string", description="string, secret, or number")
    description: str | None = Field(default=None, max_length=500)
    team_id: str = Field(default="default")


class VariableUpdateRequest(BaseModel):
    value: str | None = Field(default=None)
    description: str | None = Field(default=..., max_length=500)


class VariableResponse(BaseModel):
    id: int
    key: str
    value: str
    type: str
    description: str | None
    created_at: str
    updated_at: str


class VariableListItem(BaseModel):
    id: int
    key: str
    value: str
    type: str
    description: str | None
