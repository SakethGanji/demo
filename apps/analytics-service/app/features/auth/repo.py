"""Auth persistence — users and team memberships (raw SQL)."""

from __future__ import annotations

from sqlalchemy import text

from app.infra.db.postgres import async_session_factory
from app.shared.repo import is_uuid

_USER_COLS = (
    "id::text, team_id::text, name, email, role, "
    "COALESCE(is_superuser, false) AS is_superuser, "
    "COALESCE(status, 'active') AS status, "
    "created_at::text AS created_at"
)


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

async def get_user_by_email(email: str) -> dict | None:
    async with async_session_factory() as s:
        row = (await s.execute(
            text(f"SELECT {_USER_COLS} FROM users WHERE lower(email) = lower(:e)"),
            {"e": email},
        )).mappings().first()
        return dict(row) if row else None


async def get_user_by_id(user_id: str) -> dict | None:
    if not is_uuid(user_id):
        return None
    async with async_session_factory() as s:
        row = (await s.execute(
            text(f"SELECT {_USER_COLS} FROM users WHERE id = :id"),
            {"id": user_id},
        )).mappings().first()
        return dict(row) if row else None


async def create_user(
    *,
    email: str,
    name: str,
    team_id: str,
    is_superuser: bool = False,
    role: str = "member",
) -> dict:
    async with async_session_factory() as s:
        row = (await s.execute(
            text(f"""
                INSERT INTO users (email, name, team_id, is_superuser, role)
                VALUES (:email, :name, :team_id, :su, :role)
                RETURNING {_USER_COLS}
            """),
            {"email": email, "name": name,
             "team_id": team_id, "su": is_superuser, "role": role},
        )).mappings().one()
        await s.commit()
        return dict(row)


# ---------------------------------------------------------------------------
# Teams & memberships
# ---------------------------------------------------------------------------

async def create_team(name: str) -> dict:
    async with async_session_factory() as s:
        row = (await s.execute(
            text("INSERT INTO teams (name) VALUES (:n) RETURNING id::text, name, created_at::text AS created_at"),
            {"n": name},
        )).mappings().one()
        await s.commit()
        return dict(row)


async def get_team(team_id: str) -> dict | None:
    if not is_uuid(team_id):
        return None
    async with async_session_factory() as s:
        row = (await s.execute(
            text("SELECT id::text, name, created_at::text AS created_at FROM teams WHERE id = :id"),
            {"id": team_id},
        )).mappings().first()
        return dict(row) if row else None


async def list_memberships(user_id: str) -> list[dict]:
    """All (team_id, team_name, role) rows for a user."""
    async with async_session_factory() as s:
        rows = (await s.execute(
            text("""
                SELECT tm.team_id::text AS team_id, t.name AS team_name, tm.role
                FROM team_members tm
                JOIN teams t ON t.id = tm.team_id
                WHERE tm.user_id = :uid
                ORDER BY t.name
            """),
            {"uid": user_id},
        )).mappings().all()
        return [dict(r) for r in rows]


async def get_membership(team_id: str, user_id: str) -> dict | None:
    if not is_uuid(team_id) or not is_uuid(user_id):
        return None
    async with async_session_factory() as s:
        row = (await s.execute(
            text("SELECT role FROM team_members WHERE team_id = :tid AND user_id = :uid"),
            {"tid": team_id, "uid": user_id},
        )).mappings().first()
        return dict(row) if row else None


async def upsert_member(team_id: str, user_id: str, role: str) -> dict:
    async with async_session_factory() as s:
        row = (await s.execute(
            text("""
                INSERT INTO team_members (team_id, user_id, role)
                VALUES (:tid, :uid, :role)
                ON CONFLICT (team_id, user_id) DO UPDATE
                    SET role = EXCLUDED.role, updated_at = now()
                RETURNING team_id::text AS team_id, user_id::text AS user_id, role
            """),
            {"tid": team_id, "uid": user_id, "role": role},
        )).mappings().one()
        await s.commit()
        return dict(row)


async def remove_member(team_id: str, user_id: str) -> bool:
    async with async_session_factory() as s:
        res = await s.execute(
            text("DELETE FROM team_members WHERE team_id = :tid AND user_id = :uid"),
            {"tid": team_id, "uid": user_id},
        )
        await s.commit()
        return res.rowcount > 0


async def list_team_members(team_id: str) -> list[dict]:
    async with async_session_factory() as s:
        rows = (await s.execute(
            text("""
                SELECT u.id::text AS user_id, u.email, u.name, tm.role
                FROM team_members tm
                JOIN users u ON u.id = tm.user_id
                WHERE tm.team_id = :tid
                ORDER BY u.email
            """),
            {"tid": team_id},
        )).mappings().all()
        return [dict(r) for r in rows]
