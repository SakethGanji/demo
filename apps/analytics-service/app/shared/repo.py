"""Shared dataset & version read operations.

These are cross-feature DB queries — any feature that needs to look up
a dataset or version should import from here, not duplicate the SQL.
"""

from __future__ import annotations

from sqlalchemy import text

from app.infra.db.postgres import async_session_factory

DEFAULT_TEAM_ID = "00000000-0000-0000-0000-000000000001"
DEFAULT_USER_ID = "00000000-0000-0000-0000-000000000001"


async def get_dataset(dataset_id: str) -> dict | None:
    """Fetch a dataset by ID."""
    async with async_session_factory() as s:
        row = (await s.execute(
            text("SELECT * FROM datasets WHERE id = :id"),
            {"id": dataset_id},
        )).mappings().first()
        return dict(row) if row else None


async def get_version(version_id: str) -> dict | None:
    """Fetch a dataset version by ID."""
    async with async_session_factory() as s:
        row = (await s.execute(
            text("SELECT * FROM dataset_versions WHERE id = :id"),
            {"id": version_id},
        )).mappings().first()
        return dict(row) if row else None


async def get_version_by_number(dataset_id: str, version_number: int) -> dict | None:
    """Fetch a dataset version by dataset ID and version number."""
    async with async_session_factory() as s:
        row = (await s.execute(
            text("SELECT * FROM dataset_versions WHERE dataset_id = :did AND version_number = :vn"),
            {"did": dataset_id, "vn": version_number},
        )).mappings().first()
        return dict(row) if row else None


async def get_current_version(dataset_id: str) -> dict | None:
    """Fetch the current (latest ready) version for a dataset."""
    async with async_session_factory() as s:
        row = (await s.execute(
            text("""
                SELECT dv.* FROM dataset_versions dv
                JOIN datasets d ON d.current_version_id = dv.id
                WHERE d.id = :did
            """),
            {"did": dataset_id},
        )).mappings().first()
        return dict(row) if row else None


# ---------------------------------------------------------------------------
# Tags (read operations)
# ---------------------------------------------------------------------------

async def list_tags_for_dataset(dataset_id: str) -> list[dict]:
    """All tags for a dataset, joined with version_number."""
    async with async_session_factory() as s:
        rows = (await s.execute(
            text("""
                SELECT t.id::text, t.tag_name, t.version_id::text,
                       dv.version_number,
                       t.created_at::text AS created_at,
                       t.updated_at::text AS updated_at
                FROM dataset_version_tags t
                JOIN dataset_versions dv ON dv.id = t.version_id
                WHERE t.dataset_id = :did
                ORDER BY t.tag_name
            """),
            {"did": dataset_id},
        )).mappings().all()
        return [dict(r) for r in rows]


async def list_tags_for_version(version_id: str) -> list[str]:
    """Tag names for a single version."""
    async with async_session_factory() as s:
        rows = (await s.execute(
            text("SELECT tag_name FROM dataset_version_tags WHERE version_id = :vid ORDER BY tag_name"),
            {"vid": version_id},
        )).all()
        return [r[0] for r in rows]


async def get_version_by_tag(dataset_id: str, tag_name: str) -> dict | None:
    """Resolve a tag to the full version row."""
    async with async_session_factory() as s:
        row = (await s.execute(
            text("""
                SELECT dv.*, t.tag_name
                FROM dataset_version_tags t
                JOIN dataset_versions dv ON dv.id = t.version_id
                WHERE t.dataset_id = :did AND t.tag_name = :tag
            """),
            {"did": dataset_id, "tag": tag_name},
        )).mappings().first()
        return dict(row) if row else None
