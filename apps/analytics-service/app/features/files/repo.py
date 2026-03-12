"""Files feature — dataset & version DB operations (reads + writes)."""

from __future__ import annotations

import json

from sqlalchemy import text

from app.infra.db.postgres import async_session_factory
from app.shared.repo import (
    DEFAULT_TEAM_ID,
    DEFAULT_USER_ID,
    get_dataset,
    get_version,
)

# Re-export shared reads
__all__ = [
    "get_dataset",
    "get_version",
    "create_dataset",
    "create_version",
    "complete_version",
    "fail_version",
]


async def create_dataset(
    name: str,
    *,
    description: str | None = None,
    team_id: str = DEFAULT_TEAM_ID,
    owner_id: str = DEFAULT_USER_ID,
) -> dict:
    """Create a new dataset row."""
    async with async_session_factory() as s:
        row = (await s.execute(
            text("""
                INSERT INTO datasets (name, description, team_id, owner_id)
                VALUES (:name, :description, :team_id, :owner_id)
                RETURNING id, name, description, team_id, owner_id,
                          current_version_id, created_at, updated_at
            """),
            {"name": name, "description": description,
             "team_id": team_id, "owner_id": owner_id},
        )).mappings().one()
        await s.commit()
        return dict(row)


async def create_version(
    dataset_id: str,
    *,
    path: str | None = None,
    storage_type: str = "temp",
    status: str = "uploading",
    size_bytes: int | None = None,
    source: dict | None = None,
) -> dict:
    """Create a new dataset version, auto-incrementing version_number."""
    async with async_session_factory() as s:
        prev = (await s.execute(
            text("""
                SELECT COALESCE(MAX(version_number), 0)
                FROM dataset_versions WHERE dataset_id = :did
            """),
            {"did": dataset_id},
        )).scalar()
        ver_num = prev + 1

        row = (await s.execute(
            text("""
                INSERT INTO dataset_versions
                    (dataset_id, version_number, path, storage_type, status, size_bytes, source)
                VALUES (:did, :vn, :path, :st, :status, :sb, CAST(:source AS jsonb))
                RETURNING *
            """),
            {"did": dataset_id, "vn": ver_num, "path": path,
             "st": storage_type, "status": status, "sb": size_bytes,
             "source": json.dumps(source) if source else None},
        )).mappings().one()
        await s.commit()
        return dict(row)


async def complete_version(
    version_id: str,
    *,
    path: str,
    size_bytes: int | None = None,
    row_count: int | None = None,
    checksum: str | None = None,
    source: dict | None = None,
) -> dict:
    """Mark a version as ready. Updates current_version_id on the dataset."""
    async with async_session_factory() as s:
        row = (await s.execute(
            text("""
                UPDATE dataset_versions
                SET status = 'ready', storage_type = 'local',
                    path = :path, size_bytes = :sb,
                    row_count = :rc, checksum = :cs, processed_at = now(),
                    source = CASE
                        WHEN CAST(:source AS text) IS NOT NULL
                        THEN COALESCE(source, '{}'::jsonb) || CAST(:source AS jsonb)
                        ELSE source
                    END
                WHERE id = :id
                RETURNING *
            """),
            {"id": version_id, "path": path, "sb": size_bytes,
             "rc": row_count, "cs": checksum,
             "source": json.dumps(source) if source else None},
        )).mappings().one()

        await s.execute(
            text("UPDATE datasets SET current_version_id = :vid, updated_at = now() WHERE id = :did"),
            {"vid": version_id, "did": row["dataset_id"]},
        )
        await s.commit()
        return dict(row)


async def fail_version(version_id: str, error: str | None = None) -> None:
    """Mark a version as failed."""
    async with async_session_factory() as s:
        await s.execute(
            text("UPDATE dataset_versions SET status = 'failed', error = :error, processed_at = now() WHERE id = :id"),
            {"id": version_id, "error": error},
        )
        await s.commit()


