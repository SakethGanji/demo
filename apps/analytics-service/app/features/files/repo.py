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
    "insert_version_sheets",
]


async def create_dataset(
    name: str,
    *,
    description: str | None = None,
    team_id: str = DEFAULT_TEAM_ID,
    owner_id: str = DEFAULT_USER_ID,
    classification: str = "internal",
) -> dict:
    """Create a new dataset row."""
    async with async_session_factory() as s:
        row = (await s.execute(
            text("""
                INSERT INTO datasets (name, description, team_id, owner_id, classification)
                VALUES (:name, :description, :team_id, :owner_id, :classification)
                RETURNING id, name, description, team_id, owner_id,
                          classification, current_version_id, created_at, updated_at
            """),
            {"name": name, "description": description,
             "team_id": team_id, "owner_id": owner_id,
             "classification": classification},
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
    source_checksum: str | None = None,
    manifest_checksum: str | None = None,
    sheet_count: int | None = None,
    source: dict | None = None,
) -> dict:
    """Mark a version as ready.

    ``row_count`` is the TOTAL across all sheets. ``current_version_id``
    advances only if this version outranks the dataset's current one —
    concurrent uploads finishing out of order can't move "current" backwards.
    """
    async with async_session_factory() as s:
        row = (await s.execute(
            text("""
                UPDATE dataset_versions
                SET status = 'ready', storage_type = 'local',
                    path = :path, size_bytes = :sb,
                    row_count = :rc, checksum = :cs,
                    source_checksum = :scs, manifest_checksum = :mcs,
                    sheet_count = :shc, processed_at = now(),
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
             "scs": source_checksum, "mcs": manifest_checksum, "shc": sheet_count,
             "source": json.dumps(source) if source else None},
        )).mappings().one()

        await s.execute(
            text("""
                UPDATE datasets d
                SET current_version_id = :vid, updated_at = now()
                WHERE d.id = :did
                  AND NOT EXISTS (
                      SELECT 1 FROM dataset_versions cv
                      WHERE cv.id = d.current_version_id
                        AND cv.status = 'ready'
                        AND cv.version_number > :vn
                  )
            """),
            {"vid": version_id, "did": row["dataset_id"], "vn": row["version_number"]},
        )
        await s.commit()
        return dict(row)


async def insert_version_sheets(version_id: str, sheets: list[dict]) -> None:
    """Persist per-sheet metadata rows for a freshly processed version.

    Each dict carries: sheet_key, sheet_name, sheet_index, visibility, status,
    is_default, storage_key, row_count, column_count, size_bytes, checksum,
    schema_json, schema_fingerprint.
    """
    if not sheets:
        return
    async with async_session_factory() as s:
        for sh in sheets:
            await s.execute(
                text("""
                    INSERT INTO dataset_version_sheets
                        (dataset_version_id, sheet_key, sheet_name, sheet_index,
                         visibility, status, is_default, storage_key,
                         row_count, column_count, size_bytes, checksum,
                         schema_json, schema_fingerprint,
                         schema_extractor_version, processed_at)
                    VALUES (:vid, :key, :name, :idx, :vis, :status, :dflt, :sk,
                            :rc, :cc, :sb, :cs, CAST(:schema AS jsonb), :fp,
                            :ev, now())
                    ON CONFLICT (dataset_version_id, sheet_key) DO NOTHING
                """),
                {
                    "vid": version_id,
                    "key": sh["sheet_key"],
                    "name": sh["sheet_name"],
                    "idx": sh.get("sheet_index", 0),
                    "vis": sh.get("visibility", "visible"),
                    "status": sh.get("status", "ready"),
                    "dflt": sh.get("is_default", False),
                    "sk": sh.get("storage_key"),
                    "rc": sh.get("row_count"),
                    "cc": sh.get("column_count"),
                    "sb": sh.get("size_bytes"),
                    "cs": sh.get("checksum"),
                    "schema": json.dumps(sh["schema_json"]) if sh.get("schema_json") is not None else None,
                    "fp": sh.get("schema_fingerprint"),
                    "ev": sh.get("schema_extractor_version"),
                },
            )
        await s.commit()


async def fail_version(version_id: str, error: str | None = None) -> None:
    """Mark a version as failed."""
    async with async_session_factory() as s:
        await s.execute(
            text("UPDATE dataset_versions SET status = 'failed', error = :error, processed_at = now() WHERE id = :id"),
            {"id": version_id, "error": error},
        )
        await s.commit()


