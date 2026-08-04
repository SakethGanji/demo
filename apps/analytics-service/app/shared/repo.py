"""Shared dataset & version read operations.

These are cross-feature DB queries — any feature that needs to look up
a dataset or version should import from here, not duplicate the SQL.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import text

from app.infra.db.postgres import async_session_factory

DEFAULT_TEAM_ID = "00000000-0000-0000-0000-000000000001"
DEFAULT_USER_ID = "00000000-0000-0000-0000-000000000001"


def is_uuid(value: str | None) -> bool:
    """True if *value* is a well-formed UUID.

    Guards ID lookups so a malformed path parameter resolves to "not found"
    (404) instead of blowing up on Postgres's uuid cast (500).
    """
    if not value:
        return False
    try:
        UUID(str(value))
        return True
    except (ValueError, AttributeError, TypeError):
        return False


async def get_dataset(dataset_id: str) -> dict | None:
    """Fetch a dataset by ID."""
    if not is_uuid(dataset_id):
        return None
    async with async_session_factory() as s:
        row = (await s.execute(
            text("SELECT * FROM datasets WHERE id = :id"),
            {"id": dataset_id},
        )).mappings().first()
        return dict(row) if row else None


async def get_version(version_id: str) -> dict | None:
    """Fetch a dataset version by ID."""
    if not is_uuid(version_id):
        return None
    async with async_session_factory() as s:
        row = (await s.execute(
            text("SELECT * FROM dataset_versions WHERE id = :id"),
            {"id": version_id},
        )).mappings().first()
        return dict(row) if row else None


async def get_version_by_number(dataset_id: str, version_number: int) -> dict | None:
    """Fetch a dataset version by dataset ID and version number."""
    if not is_uuid(dataset_id):
        return None
    async with async_session_factory() as s:
        row = (await s.execute(
            text("SELECT * FROM dataset_versions WHERE dataset_id = :did AND version_number = :vn"),
            {"did": dataset_id, "vn": version_number},
        )).mappings().first()
        return dict(row) if row else None


async def get_current_version(dataset_id: str) -> dict | None:
    """Fetch the current (latest ready) version for a dataset."""
    if not is_uuid(dataset_id):
        return None
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
# Sheets (dataset_version_sheets)
# ---------------------------------------------------------------------------

async def list_version_sheets(version_id: str) -> list[dict]:
    """All sheet rows for a version, in workbook order."""
    if not is_uuid(version_id):
        return []
    async with async_session_factory() as s:
        rows = (await s.execute(
            text("""
                SELECT id::text, dataset_version_id::text, sheet_key, sheet_name,
                       sheet_index, visibility, status, is_default, storage_key,
                       row_count, column_count, size_bytes, checksum,
                       schema_json, schema_fingerprint, created_at::text AS created_at
                FROM dataset_version_sheets
                WHERE dataset_version_id = :vid
                ORDER BY sheet_index
            """),
            {"vid": version_id},
        )).mappings().all()
        return [dict(r) for r in rows]


async def get_version_sheet(version_id: str, sheet: str) -> dict | None:
    """One sheet row by exact name (or normalized sheet_key as fallback)."""
    if not is_uuid(version_id):
        return None
    async with async_session_factory() as s:
        row = (await s.execute(
            text("""
                SELECT id::text, dataset_version_id::text, sheet_key, sheet_name,
                       sheet_index, visibility, status, is_default, storage_key,
                       row_count, column_count, size_bytes, checksum,
                       schema_json, schema_fingerprint, created_at::text AS created_at
                FROM dataset_version_sheets
                WHERE dataset_version_id = :vid
                  AND (sheet_name = :sheet OR sheet_key = :sheet)
                ORDER BY (sheet_name = :sheet) DESC
                LIMIT 1
            """),
            {"vid": version_id, "sheet": sheet},
        )).mappings().first()
        return dict(row) if row else None


async def update_sheet_schema(
    sheet_id: str,
    *,
    schema_json: list | dict,
    schema_fingerprint: str,
    row_count: int | None = None,
    column_count: int | None = None,
) -> None:
    """Fill in lazily-computed schema metadata on a backfilled sheet row.

    Sheet *data* is immutable; this only completes derived metadata that the
    SQL backfill could not compute (parquet inspection needs DuckDB).
    """
    import json as _json

    async with async_session_factory() as s:
        await s.execute(
            text("""
                UPDATE dataset_version_sheets
                SET schema_json = CAST(:schema AS jsonb),
                    schema_fingerprint = :fp,
                    row_count = COALESCE(:rc, row_count),
                    column_count = COALESCE(:cc, column_count)
                WHERE id = :id
            """),
            {"id": sheet_id, "schema": _json.dumps(schema_json),
             "fp": schema_fingerprint, "rc": row_count, "cc": column_count},
        )
        await s.commit()


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
    if not is_uuid(dataset_id):
        return None
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
