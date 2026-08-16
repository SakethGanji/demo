"""Team-scoped RBAC: roles, permissions, and the role→permission matrix.

A user's authority is evaluated *within a team*. Roles are ordered:

    viewer < editor < admin < owner

Platform superusers (``users.is_superuser``) bypass team checks entirely.
"""

from __future__ import annotations

from enum import Enum


class Role(str, Enum):
    """A member's role within a team (stored in team_members.role)."""

    OWNER = "owner"
    ADMIN = "admin"
    EDITOR = "editor"
    VIEWER = "viewer"


# Higher rank = more authority. Used for "at least this role" checks.
ROLE_RANK: dict[Role, int] = {
    Role.VIEWER: 10,
    Role.EDITOR: 20,
    Role.ADMIN: 30,
    Role.OWNER: 40,
}


class Permission(str, Enum):
    """Discrete capabilities checked by the API layer."""

    DATASET_READ = "dataset:read"       # list/get/versions/tags/sheets/download/analytics
    DATASET_WRITE = "dataset:write"     # upload, create version, patch, set/delete tags
    DATASET_DELETE = "dataset:delete"   # delete a dataset
    # Read columns the data dictionary marks sensitive without masking, and
    # download the raw file of a dataset that declares any. Deliberately NOT
    # granted to editors: the point is that people who work with a dataset
    # every day don't routinely see its PII.
    DATASET_READ_SENSITIVE = "dataset:read_sensitive"
    TEAM_READ = "team:read"             # view team + members
    TEAM_MANAGE = "team:manage"         # add/remove members, change roles
    TEAM_DELETE = "team:delete"         # delete the team / transfer ownership


_VIEWER: set[Permission] = {Permission.DATASET_READ, Permission.TEAM_READ}
_EDITOR: set[Permission] = _VIEWER | {Permission.DATASET_WRITE}
_ADMIN: set[Permission] = _EDITOR | {
    Permission.DATASET_DELETE,
    Permission.DATASET_READ_SENSITIVE,
    Permission.TEAM_MANAGE,
}
_OWNER: set[Permission] = _ADMIN | {Permission.TEAM_DELETE}

ROLE_PERMISSIONS: dict[Role, set[Permission]] = {
    Role.VIEWER: _VIEWER,
    Role.EDITOR: _EDITOR,
    Role.ADMIN: _ADMIN,
    Role.OWNER: _OWNER,
}


def role_has(role: Role | str, permission: Permission) -> bool:
    """True if *role* grants *permission*."""
    try:
        role = Role(role)
    except ValueError:
        return False
    return permission in ROLE_PERMISSIONS[role]


def role_at_least(role: Role | str, minimum: Role) -> bool:
    """True if *role* ranks at or above *minimum*."""
    try:
        role = Role(role)
    except ValueError:
        return False
    return ROLE_RANK[role] >= ROLE_RANK[minimum]
