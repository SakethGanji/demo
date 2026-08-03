"""Identity & team-management API — /auth/*, /teams/*.

Mounted under the service-wide /api/v1 prefix. POC identity: callers pass
``X-User-Id``; there are no password/token endpoints. Each route declares its
own guard (they are not behind the blanket data-plane dependency).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException

from app.api.pagination import Page
from . import repo
from .deps import Principal, get_principal, pick_active_team
from .permissions import ROLE_RANK, Permission, Role
from .schemas import (
    AddMemberRequest,
    CreateTeamRequest,
    CreateUserRequest,
    MeResponse,
    MemberOut,
    MembershipOut,
    TeamOut,
    UpdateMemberRoleRequest,
    UserOut,
)

router = APIRouter(prefix="/auth", tags=["auth"])
teams_router = APIRouter(prefix="/teams", tags=["teams"])

_USER_OUT_KEYS = ("id", "email", "name", "is_superuser", "status", "created_at")


def _ensure_grantable(principal: Principal, team_id: str, role: Role) -> None:
    """Forbid granting a role above the caller's own rank (superusers exempt).

    Without this, a team admin could promote anyone — including themselves —
    to owner, exceeding their own authority.
    """
    if principal.is_superuser:
        return
    own = principal.role_in(team_id)
    if own is None or ROLE_RANK[role] > ROLE_RANK[own]:
        raise HTTPException(403, f"Cannot grant role '{role.value}' above your own")


# ---------------------------------------------------------------------------
# Current user
# ---------------------------------------------------------------------------

@router.get("/me", response_model=MeResponse)
async def me(principal: Principal = Depends(get_principal)) -> MeResponse:
    """Return the acting user and their team memberships."""
    user = await repo.get_user_by_id(principal.user_id)
    if not user:
        raise HTTPException(401, "Account no longer exists")
    memberships = await repo.list_memberships(principal.user_id)
    return MeResponse(
        user=UserOut(**{k: user[k] for k in _USER_OUT_KEYS}),
        memberships=[MembershipOut(**m) for m in memberships],
    )


# ---------------------------------------------------------------------------
# User provisioning
# ---------------------------------------------------------------------------

@router.post("/users", response_model=UserOut, status_code=201)
async def create_user(
    body: CreateUserRequest,
    principal: Principal = Depends(get_principal),
    x_team_id: str | None = Header(default=None, alias="X-Team-Id"),
) -> UserOut:
    """Create a user and add them to a team.

    Requires platform superuser, or TEAM_MANAGE in the target team. The new user
    is added to that team as a viewer (grant a higher role via the members API).
    """
    # An explicit body.team_id wins outright; only fall back to header/home-team
    # resolution when it's absent (so multi-team admins aren't forced through
    # the ambiguous-context 400).
    team_id = body.team_id or pick_active_team(principal, x_team_id)
    if not (principal.is_superuser or principal.can(team_id, Permission.TEAM_MANAGE)):
        raise HTTPException(403, "Requires team:manage in the target team")
    if await repo.get_team(team_id) is None:
        raise HTTPException(404, f"Team not found: {team_id}")
    if await repo.get_user_by_email(body.email):
        raise HTTPException(409, "A user with that email already exists")

    user = await repo.create_user(
        email=body.email,
        name=body.name,
        team_id=team_id,
        is_superuser=body.is_superuser and principal.is_superuser,  # only superusers mint superusers
    )
    await repo.upsert_member(team_id, user["id"], Role.VIEWER.value)
    return UserOut(**{k: user[k] for k in _USER_OUT_KEYS})


# ---------------------------------------------------------------------------
# Teams
# ---------------------------------------------------------------------------

@teams_router.post("", response_model=TeamOut, status_code=201)
async def create_team(
    body: CreateTeamRequest,
    principal: Principal = Depends(get_principal),
) -> TeamOut:
    """Create a team. The caller becomes its owner."""
    team = await repo.create_team(body.name)
    await repo.upsert_member(team["id"], principal.user_id, Role.OWNER.value)
    return TeamOut(**team)


@teams_router.get("", response_model=Page[MembershipOut])
async def list_my_teams(principal: Principal = Depends(get_principal)) -> Page[MembershipOut]:
    """List the teams the caller belongs to, with their role in each."""
    memberships = await repo.list_memberships(principal.user_id)
    items = [MembershipOut(**m) for m in memberships]
    return Page(items=items, total=len(items), limit=len(items), offset=0)


@teams_router.get("/{team_id}/members", response_model=Page[MemberOut])
async def list_members(
    team_id: str, principal: Principal = Depends(get_principal),
) -> Page[MemberOut]:
    """List members of a team (requires membership in the team)."""
    if not principal.can(team_id, Permission.TEAM_READ):
        raise HTTPException(404, f"Team not found: {team_id}")
    members = await repo.list_team_members(team_id)
    items = [MemberOut(**m) for m in members]
    return Page(items=items, total=len(items), limit=len(items), offset=0)


@teams_router.post("/{team_id}/members", response_model=MemberOut, status_code=201)
async def add_member(
    team_id: str, body: AddMemberRequest,
    principal: Principal = Depends(get_principal),
) -> MemberOut:
    """Add an existing user to a team with a role (requires team:manage)."""
    if not principal.can(team_id, Permission.TEAM_MANAGE):
        raise HTTPException(403, "Requires team:manage")
    _ensure_grantable(principal, team_id, body.role)
    target = await repo.get_user_by_id(body.user_id)
    if not target:
        raise HTTPException(404, f"User not found: {body.user_id}")
    await repo.upsert_member(team_id, body.user_id, body.role.value)
    return MemberOut(user_id=target["id"], email=target["email"], name=target["name"], role=body.role)


@teams_router.patch("/{team_id}/members/{user_id}", response_model=MemberOut)
async def update_member_role(
    team_id: str, user_id: str, body: UpdateMemberRoleRequest,
    principal: Principal = Depends(get_principal),
) -> MemberOut:
    """Change a member's role (requires team:manage). Cannot demote the last owner."""
    if not principal.can(team_id, Permission.TEAM_MANAGE):
        raise HTTPException(403, "Requires team:manage")
    _ensure_grantable(principal, team_id, body.role)
    current = await repo.get_membership(team_id, user_id)
    if not current:
        raise HTTPException(404, "User is not a member of this team")
    if current["role"] == Role.OWNER.value and body.role != Role.OWNER:
        await _guard_last_owner(team_id, user_id)
    await repo.upsert_member(team_id, user_id, body.role.value)
    target = await repo.get_user_by_id(user_id)
    return MemberOut(user_id=user_id, email=target["email"], name=target["name"], role=body.role)


@teams_router.delete("/{team_id}/members/{user_id}", status_code=204)
async def remove_member(
    team_id: str, user_id: str,
    principal: Principal = Depends(get_principal),
) -> None:
    """Remove a member from a team (requires team:manage). Cannot remove the last owner."""
    if not principal.can(team_id, Permission.TEAM_MANAGE):
        raise HTTPException(403, "Requires team:manage")
    current = await repo.get_membership(team_id, user_id)
    if not current:
        raise HTTPException(404, "User is not a member of this team")
    if current["role"] == Role.OWNER.value:
        await _guard_last_owner(team_id, user_id)
    await repo.remove_member(team_id, user_id)


async def _guard_last_owner(team_id: str, user_id: str) -> None:
    """Prevent orphaning a team by removing/demoting its only owner."""
    owners = [m for m in await repo.list_team_members(team_id) if m["role"] == Role.OWNER.value]
    if len(owners) <= 1 and any(o["user_id"] == user_id for o in owners):
        raise HTTPException(409, "Cannot remove or demote the last owner of a team")
