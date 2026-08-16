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
    """All sheet rows for a version, in workbook order.

    ``sheet_key``/``sheet_name`` are the version's own, frozen at ingest and
    deliberately never rewritten by a rename (parquet layout keys off them, and
    versions are immutable). ``logical_sheet_key``/``logical_sheet_name`` carry
    the *current* name of the same logical sheet, so name-addressed resolution
    can take ROADMAP §1's second hop (name → version sheet row → logical id)
    instead of dead-ending on a name the UI stopped showing after a rename.
    """
    if not is_uuid(version_id):
        return []
    async with async_session_factory() as s:
        rows = (await s.execute(
            text("""
                SELECT vs.id::text, vs.dataset_version_id::text,
                       vs.logical_sheet_id::text,
                       vs.sheet_key, vs.sheet_name,
                       ls.current_sheet_key AS logical_sheet_key,
                       ls.display_name AS logical_sheet_name,
                       vs.sheet_index, vs.visibility, vs.status, vs.is_default,
                       vs.storage_key,
                       vs.row_count, vs.column_count, vs.size_bytes, vs.checksum,
                       vs.schema_json, vs.schema_fingerprint,
                       vs.created_at::text AS created_at
                FROM dataset_version_sheets vs
                LEFT JOIN dataset_sheets ls ON ls.id = vs.logical_sheet_id
                WHERE vs.dataset_version_id = :vid
                ORDER BY vs.sheet_index
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
                SELECT id::text, dataset_version_id::text, logical_sheet_id::text,
                       sheet_key, sheet_name,
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
    extractor_version: str | None = None,
    row_count: int | None = None,
    column_count: int | None = None,
) -> None:
    """Fill in lazily-computed schema metadata on a backfilled sheet row.

    Sheet *data* is immutable; this only completes derived metadata that the
    SQL backfill could not compute (parquet inspection needs DuckDB). The
    backfill is explicitly tracked (extractor version + timestamp) so later
    readers can tell original ingest metadata from enrichment.
    """
    import json as _json

    async with async_session_factory() as s:
        await s.execute(
            text("""
                UPDATE dataset_version_sheets
                SET schema_json = CAST(:schema AS jsonb),
                    schema_fingerprint = :fp,
                    schema_extractor_version = COALESCE(:ev, schema_extractor_version),
                    schema_backfilled_at = now(),
                    row_count = COALESCE(:rc, row_count),
                    column_count = COALESCE(:cc, column_count)
                WHERE id = :id
            """),
            {"id": sheet_id, "schema": _json.dumps(schema_json),
             "fp": schema_fingerprint, "ev": extractor_version,
             "rc": row_count, "cc": column_count},
        )
        await s.commit()


# ---------------------------------------------------------------------------
# Logical sheets (dataset_sheets)
# ---------------------------------------------------------------------------

_LOGICAL_SHEET_COLS = """id::text, dataset_id::text, current_sheet_key, display_name,
                         first_seen_version_id::text, retired_at::text AS retired_at,
                         created_at::text AS created_at"""


async def list_dataset_sheets(dataset_id: str, *, include_retired: bool = False) -> list[dict]:
    """Logical sheets of a dataset (live only unless *include_retired*)."""
    if not is_uuid(dataset_id):
        return []
    clause = "" if include_retired else " AND retired_at IS NULL"
    async with async_session_factory() as s:
        rows = (await s.execute(
            text(f"SELECT {_LOGICAL_SHEET_COLS} FROM dataset_sheets "
                 f"WHERE dataset_id = :did{clause} ORDER BY current_sheet_key"),
            {"did": dataset_id},
        )).mappings().all()
        return [dict(r) for r in rows]


async def get_live_dataset_sheet(dataset_id: str, selector: str) -> dict | None:
    """Live logical sheet matched by current key or display name."""
    if not is_uuid(dataset_id):
        return None
    async with async_session_factory() as s:
        row = (await s.execute(
            text(f"""
                SELECT {_LOGICAL_SHEET_COLS} FROM dataset_sheets
                WHERE dataset_id = :did AND retired_at IS NULL
                  AND (current_sheet_key = :sel OR display_name = :sel)
                ORDER BY (current_sheet_key = :sel) DESC
                LIMIT 1
            """),
            {"did": dataset_id, "sel": selector},
        )).mappings().first()
        return dict(row) if row else None


async def get_latest_sheet_row_for_logical(
    dataset_id: str, logical_sheet_id: str, *, before_version_number: int,
) -> dict | None:
    """The logical sheet's row in the latest ready version below *before_version_number*.

    Used by confirm-rename to compare the renamed-from sheet's last known
    schema fingerprint against the renamed-to sheet.
    """
    async with async_session_factory() as s:
        row = (await s.execute(
            text("""
                SELECT s.id::text, s.sheet_key, s.sheet_name, s.schema_fingerprint,
                       s.row_count, dv.version_number
                FROM dataset_version_sheets s
                JOIN dataset_versions dv ON dv.id = s.dataset_version_id
                WHERE dv.dataset_id = :did AND dv.status = 'ready'
                  AND dv.version_number < :vn AND s.logical_sheet_id = :lsid
                ORDER BY dv.version_number DESC
                LIMIT 1
            """),
            {"did": dataset_id, "vn": before_version_number, "lsid": logical_sheet_id},
        )).mappings().first()
        return dict(row) if row else None


async def count_logical_sheet_state(logical_sheet_id: str) -> dict[str, int]:
    """How much keyed state hangs off a logical sheet (rename-conflict guard).

    Covers only the tables whose rows would be ORPHANED by deleting the sheet
    row (``ON DELETE SET NULL``, plus column metadata). The tables that
    ``ON DELETE CASCADE`` — transformations, saved views, relationships —
    would be destroyed outright instead, and are counted by
    ``app.features.data_accelerator.repo.count_cascading_logical_sheet_state``.
    Confirm-rename merges both dicts; adding a cascade table here as well
    would just double-count it.
    """
    async with async_session_factory() as s:
        metadata = (await s.execute(
            text("SELECT COUNT(*) FROM dataset_sheet_metadata WHERE logical_sheet_id = :id"),
            {"id": logical_sheet_id},
        )).scalar()
        rules = (await s.execute(
            text("SELECT COUNT(*) FROM quality_rules WHERE logical_sheet_id = :id"),
            {"id": logical_sheet_id},
        )).scalar()
        columns = (await s.execute(
            text("SELECT COUNT(*) FROM dataset_column_metadata "
                 "WHERE logical_sheet_id = :id"),
            {"id": logical_sheet_id},
        )).scalar()
        return {"sheet_metadata": metadata, "quality_rules": rules,
                "column_metadata": columns}


async def reassign_logical_sheet(
    *,
    dataset_id: str,
    old_logical_id: str,
    new_logical_id: str,
    new_sheet_key: str,
    new_display_name: str,
) -> int:
    """Confirm a rename: fold the auto-created identity into the original one.

    One transaction: version sheet rows move from the new (spurious) logical id
    to the surviving one, the surviving row takes over the new key/name, keyed
    state (sheet metadata, quality rules incl. cross-sheet ref_sheet params) is
    re-keyed to the new sheet_key so name-based lookups keep working, and the
    spurious row is deleted. Returns the number of version sheet rows moved.
    The caller must have verified the spurious id carries no keyed state.
    """
    async with async_session_factory() as s:
        old_key = (await s.execute(
            text("SELECT current_sheet_key FROM dataset_sheets WHERE id = :id"),
            {"id": old_logical_id},
        )).scalar_one()
        moved = (await s.execute(
            text("UPDATE dataset_version_sheets SET logical_sheet_id = :old "
                 "WHERE logical_sheet_id = :new"),
            {"old": old_logical_id, "new": new_logical_id},
        )).rowcount
        # Delete the spurious identity BEFORE the survivor takes its key —
        # the partial unique index on live (dataset_id, current_sheet_key)
        # would otherwise reject the update.
        await s.execute(
            text("DELETE FROM dataset_sheets WHERE id = :id"),
            {"id": new_logical_id},
        )
        await s.execute(
            text("UPDATE dataset_sheets SET current_sheet_key = :key, display_name = :name "
                 "WHERE id = :id"),
            {"id": old_logical_id, "key": new_sheet_key, "name": new_display_name},
        )
        await s.execute(
            text("UPDATE dataset_sheet_metadata SET sheet_key = :key "
                 "WHERE dataset_id = :did AND logical_sheet_id = :old"),
            {"key": new_sheet_key, "did": dataset_id, "old": old_logical_id},
        )
        await s.execute(
            text("UPDATE quality_rules SET sheet_selector = :key, updated_at = now() "
                 "WHERE dataset_id = :did AND logical_sheet_id = :old"),
            {"key": new_sheet_key, "did": dataset_id, "old": old_logical_id},
        )
        await s.execute(
            text("""
                UPDATE quality_rules
                SET parameters = jsonb_set(parameters, '{ref_sheet}', to_jsonb(CAST(:key AS text))),
                    updated_at = now()
                WHERE dataset_id = :did AND parameters->>'ref_sheet' = :old_key
            """),
            {"key": new_sheet_key, "did": dataset_id, "old_key": old_key},
        )
        await s.commit()
        return moved


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
    """Resolve a tag to the full version row (tags are case-insensitive slugs)."""
    if not is_uuid(dataset_id):
        return None
    tag_name = tag_name.strip().lower()
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
