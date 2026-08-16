"""Identity & team request/response schemas."""

from __future__ import annotations

from pydantic import BaseModel, EmailStr, Field, model_validator

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
    """Identify the user to add by internal id *or* by exact email address.

    There is no user-directory endpoint, and ``GET /teams/{id}/members`` only
    reveals people who already share a team with the caller — so an id-only
    contract makes the ordinary "add a colleague from another team" case
    unbuildable: an admin console knows the colleague's email and has no way
    to turn it into an id (``POST /auth/users`` dead-ends on a 409 that
    carries no id). Accepting an exact email closes that gap without opening
    an enumeration surface: the caller must already know the address, must
    hold team:manage in the target team, and a miss returns the same 404 as
    an unknown id.
    """

    user_id: str | None = Field(default=None, description="User to add to the team")
    email: EmailStr | None = Field(
        default=None,
        description="Exact email of the user to add; alternative to user_id",
    )
    role: Role = Role.VIEWER

    @model_validator(mode="after")
    def _exactly_one_identifier(self) -> "AddMemberRequest":
        if (self.user_id is None) == (self.email is None):
            raise ValueError("Provide exactly one of 'user_id' or 'email'")
        return self


class UpdateMemberRoleRequest(BaseModel):
    role: Role


class MemberOut(BaseModel):
    user_id: str
    email: str
    name: str
    role: Role
