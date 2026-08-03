"""FastAPI dependencies: authentication (Principal) and RBAC enforcement.

POC identity model: the caller states who they are via the ``X-User-Id`` header
and we trust it (no passwords/tokens). The header must name an existing, active
user; everything downstream — team membership, roles, permissions — is still
enforced server-side. Swapping this for real authentication (SSO/JWT) later
only means replacing :func:`get_principal`; the RBAC layer is unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from fastapi import Depends, Header, HTTPException, Request

from app.infra.config import settings
from app.shared.repo import DEFAULT_TEAM_ID, DEFAULT_USER_ID, get_dataset
from . import repo
from .permissions import Permission, Role, role_has


@dataclass(slots=True)
class Principal:
    """The authenticated caller and their team memberships."""

    user_id: str
    email: str
    name: str
    is_superuser: bool
    home_team_id: str | None
    memberships: dict[str, Role] = field(default_factory=dict)  # team_id -> role

    @property
    def team_ids(self) -> list[str]:
        return list(self.memberships.keys())

    def role_in(self, team_id: str) -> Role | None:
        return self.memberships.get(team_id)

    def can(self, team_id: str, permission: Permission) -> bool:
        if self.is_superuser:
            return True
        role = self.memberships.get(team_id)
        return bool(role and role_has(role, permission))


async def get_principal(
    request: Request,
    x_user_id: str | None = Header(
        default=None, alias="X-User-Id",
        description="Acting user's id (POC header identity — no password auth)",
    ),
) -> Principal:
    """Resolve the acting user from the ``X-User-Id`` header.

    Also stashes the resolved principal on ``request.state`` so cross-cutting
    middleware (e.g. the audit trail) can attribute the action.
    """
    if not settings.auth_enabled:
        # Local-debug escape hatch: run as the seeded system superuser.
        principal = Principal(
            user_id=DEFAULT_USER_ID, email="system@localhost", name="System",
            is_superuser=True, home_team_id=DEFAULT_TEAM_ID,
            memberships={DEFAULT_TEAM_ID: Role.OWNER},
        )
        request.state.principal = principal
        return principal
    if not x_user_id:
        raise HTTPException(401, "Missing X-User-Id header")

    user = await repo.get_user_by_id(x_user_id)  # None for malformed/unknown ids
    if not user or user.get("status") != "active":
        raise HTTPException(401, "Unknown or inactive user")

    memberships = {m["team_id"]: Role(m["role"]) for m in await repo.list_memberships(user["id"])}
    principal = Principal(
        user_id=user["id"],
        email=user["email"],
        name=user["name"],
        is_superuser=bool(user.get("is_superuser")),
        home_team_id=user.get("team_id"),
        memberships=memberships,
    )
    request.state.principal = principal
    return principal


# ---------------------------------------------------------------------------
# Team-context resolution (for collection endpoints: list / create)
# ---------------------------------------------------------------------------

def pick_active_team(principal: Principal, x_team_id: str | None) -> str:
    """Resolve the team a collection-scoped request operates in.

    Uses ``X-Team-Id`` when provided (must be a team you belong to), otherwise
    your home team, otherwise your only membership. Raises on ambiguity/denial.
    """
    if x_team_id:
        if principal.is_superuser or x_team_id in principal.memberships:
            return x_team_id
        raise HTTPException(403, "You are not a member of the requested team")

    if principal.home_team_id and (
        principal.is_superuser or principal.home_team_id in principal.memberships
    ):
        return principal.home_team_id
    if len(principal.memberships) == 1:
        return next(iter(principal.memberships))
    raise HTTPException(400, "Ambiguous team context — specify the X-Team-Id header")


async def get_active_team(
    principal: Principal = Depends(get_principal),
    x_team_id: str | None = Header(default=None, alias="X-Team-Id",
                                   description="Team to operate in; defaults to your home team"),
) -> str:
    """Dependency form of :func:`pick_active_team`."""
    return pick_active_team(principal, x_team_id)


# ---------------------------------------------------------------------------
# Authorization helpers
# ---------------------------------------------------------------------------

async def ensure_dataset_permission(
    principal: Principal, dataset_id: str, permission: Permission,
) -> dict:
    """Load a dataset and confirm the principal has *permission* in its team.

    Returns the dataset row so callers can reuse it. Raises 404 if the dataset
    does not exist (or the id is malformed), 403 if the principal lacks access.
    Note: 404 is returned for datasets in teams the caller can't see, so we
    never leak the existence of other teams' datasets.
    """
    ds = await get_dataset(dataset_id)
    if not ds:
        raise HTTPException(404, f"Dataset not found: {dataset_id}")
    team_id = str(ds["team_id"])
    if not principal.can(team_id, permission):
        # Readers of a team they don't belong to get 404 (existence hidden);
        # members lacking write/delete get a truthful 403.
        if principal.is_superuser or team_id in principal.memberships:
            raise HTTPException(403, f"Insufficient permissions: requires {permission.value}")
        raise HTTPException(404, f"Dataset not found: {dataset_id}")
    return ds


def require_team_permission(permission: Permission):
    """Dependency factory: require *permission* in the active team (collection ops)."""

    async def _dep(
        principal: Principal = Depends(get_principal),
        team_id: str = Depends(get_active_team),
    ) -> tuple[Principal, str]:
        if not principal.can(team_id, permission):
            raise HTTPException(403, f"Insufficient permissions: requires {permission.value}")
        return principal, team_id

    return _dep
