"""Files feature — dataset & version DB operations (reads + writes)."""

from __future__ import annotations

import asyncio
import json

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

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
    """Create a new dataset version, auto-incrementing version_number.

    The number is allocated INSIDE the INSERT, not by a separate SELECT MAX:
    two uploads racing on the same dataset both read the same maximum and the
    loser hit the ``(dataset_id, version_number)`` unique index, surfacing as a
    500. Measured at 50% failure with two concurrent uploads. A serializable
    read of the max inside the statement, plus a bounded retry on the unique
    violation, makes concurrent version creation safe.
    """
    for attempt in range(5):
        try:
            async with async_session_factory() as s:
                row = (await s.execute(
                    text("""
                        INSERT INTO dataset_versions
                            (dataset_id, version_number, path, storage_type, status, size_bytes, source)
                        SELECT :did,
                               COALESCE((SELECT MAX(version_number)
                                           FROM dataset_versions
                                          WHERE dataset_id = :did), 0) + 1,
                               :path, :st, :status, :sb, CAST(:source AS jsonb)
                        RETURNING *
                    """),
                    {"did": dataset_id, "path": path,
                     "st": storage_type, "status": status, "sb": size_bytes,
                     "source": json.dumps(source) if source else None},
                )).mappings().one()
                await s.commit()
                return dict(row)
        except IntegrityError:
            # Someone else took this number between our read and our write.
            # Re-read and try again; the loser simply gets the next number.
            if attempt == 4:
                raise
            await asyncio.sleep(0.02 * (attempt + 1))
    raise RuntimeError("unreachable")  # pragma: no cover


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


async def get_previous_version_sheets(dataset_id: str, before_version_number: int) -> list[dict]:
    """Sheet rows of the latest READY version below *before_version_number*.

    Used for checksum-based artifact reuse: an unchanged sheet in a new
    version points at the previous version's parquet instead of re-uploading.
    """
    async with async_session_factory() as s:
        rows = (await s.execute(
            text("""
                SELECT s.sheet_key, s.checksum, s.storage_key, s.status
                FROM dataset_version_sheets s
                JOIN dataset_versions dv ON dv.id = s.dataset_version_id
                WHERE dv.dataset_id = :did AND dv.status = 'ready'
                  AND dv.version_number = (
                      SELECT MAX(version_number) FROM dataset_versions
                      WHERE dataset_id = :did AND status = 'ready'
                        AND version_number < :vn
                  )
            """),
            {"did": dataset_id, "vn": before_version_number},
        )).mappings().all()
        return [dict(r) for r in rows]


async def insert_version_sheets(version_id: str, sheets: list[dict]) -> None:
    """Persist per-sheet metadata rows for a freshly processed version.

    Each dict carries: sheet_key, sheet_name, sheet_index, visibility, status,
    is_default, storage_key, row_count, column_count, size_bytes, checksum,
    schema_json, schema_fingerprint.

    Every row is linked to a logical sheet (``dataset_sheets``): an existing
    live logical sheet with the same key, or a freshly created one. Same key =
    same identity is the default; confirmed renames re-point it afterwards.
    """
    if not sheets:
        return
    async with async_session_factory() as s:
        dataset_id = (await s.execute(
            text("SELECT dataset_id FROM dataset_versions WHERE id = :vid"),
            {"vid": version_id},
        )).scalar_one()
        for sh in sheets:
            logical_id = (await s.execute(
                text("""
                    INSERT INTO dataset_sheets
                        (dataset_id, current_sheet_key, display_name, first_seen_version_id)
                    VALUES (:did, :key, :name, :vid)
                    ON CONFLICT (dataset_id, current_sheet_key) WHERE retired_at IS NULL
                    DO UPDATE SET display_name = EXCLUDED.display_name
                    RETURNING id
                """),
                {"did": dataset_id, "key": sh["sheet_key"],
                 "name": sh["sheet_name"], "vid": version_id},
            )).scalar_one()
            await s.execute(
                text("""
                    INSERT INTO dataset_version_sheets
                        (dataset_version_id, logical_sheet_id, sheet_key, sheet_name,
                         sheet_index, visibility, status, is_default, storage_key,
                         row_count, column_count, size_bytes, checksum,
                         schema_json, schema_fingerprint,
                         schema_extractor_version, processed_at)
                    VALUES (:vid, :lsid, :key, :name, :idx, :vis, :status, :dflt, :sk,
                            :rc, :cc, :sb, :cs, CAST(:schema AS jsonb), :fp,
                            :ev, now())
                    ON CONFLICT (dataset_version_id, sheet_key) DO NOTHING
                """),
                {
                    "vid": version_id,
                    "lsid": logical_id,
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


async def fail_version(version_id: str, error: str | None = None) -> bool:
    """Mark a version as failed *if it has not already finished*.

    The ``status <> 'ready'`` guard is the same one commit b69bfc0 gave the run
    tables: "fail it if it has not already succeeded". Without it a late
    cancel — DELETE /tus/{upload_id} arriving after the bytes landed and the
    version went ready — demoted a good version to ``failed``, which hides its
    data from every ready-filtered read with no way back.

    Returns True when a row was actually demoted, so callers can tell a real
    cancel from a no-op.
    """
    async with async_session_factory() as s:
        result = await s.execute(
            text("""
                UPDATE dataset_versions
                SET status = 'failed', error = :error, processed_at = now()
                WHERE id = :id AND status <> 'ready'
            """),
            {"id": version_id, "error": error},
        )
        await s.commit()
        return result.rowcount > 0


