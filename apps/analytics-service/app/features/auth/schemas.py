"""Identity & team request/response schemas."""

from __future__ import annotations

from pydantic import BaseModel, EmailStr, Field

from .permissions import Role


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

class UserOut(BaseModel):
    id: str
    email: str
    name: str
    is_superuser: bool = False
    status: str = "active"
    created_at: str


class MembershipOut(BaseModel):
    team_id: str
    team_name: str
    role: Role


class MeResponse(BaseModel):
    user: UserOut
    memberships: list[MembershipOut]


class CreateUserRequest(BaseModel):
    email: EmailStr
    name: str = Field(..., min_length=1, max_length=255)
    team_id: str | None = Field(default=None, description="Home team; defaults to caller's active team")
    is_superuser: bool = False


# ---------------------------------------------------------------------------
# Teams & membership management
# ---------------------------------------------------------------------------

class CreateTeamRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)


class TeamOut(BaseModel):
    id: str
    name: str
    created_at: str


class AddMemberRequest(BaseModel):
    user_id: str = Field(..., description="User to add to the team")
    role: Role = Role.VIEWER


class UpdateMemberRoleRequest(BaseModel):
    role: Role


class MemberOut(BaseModel):
    user_id: str
    email: str
    name: str
    role: Role
