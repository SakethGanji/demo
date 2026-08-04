"""Shared dataset path & sheet resolution.

Any feature that needs to resolve a dataset_id to a file path (for DuckDB,
pandas, etc.) should use these functions rather than querying the DB directly.

Sheet metadata is first-class (``dataset_version_sheets``); versions ingested
before that table existed fall back to the legacy ``source`` JSONB. Multi-sheet
versions never auto-resolve to a sheet — callers must name one, or get a
problem+json ``sheet-selection-required`` error listing the choices.
"""

from __future__ import annotations

import duckdb
from fastapi import HTTPException

from app.api.errors import ProblemException
from app.infra.db.storage import get_storage
from app.shared.data_io import (
    SCHEMA_EXTRACTOR_VERSION,
    build_sheet_schema,
    describe_parquet,
    normalize_sheet_key,
)
from app.shared.repo import (
    get_current_version,
    get_version,
    get_version_by_number,
    get_version_by_tag,
    list_version_sheets,
    update_sheet_schema,
)


async def resolve_dataset_path(
    dataset_id: str,
    sheet: str | None = None,
    *,
    version_id: str | None = None,
    version_number: int | None = None,
    tag: str | None = None,
) -> str:
    """Resolve a dataset_id (and optional version target) to a file path.

    Version resolution priority: version_id > version_number > tag > current.
    With *sheet*, returns that sheet's parquet path. Without it, single-sheet
    versions auto-resolve; multi-sheet versions raise ``sheet-selection-required``.
    """
    ver = await resolve_version(dataset_id, version_id=version_id,
                                version_number=version_number, tag=tag)
    if not ver.get("path"):
        raise HTTPException(404, f"Version has no data (status: {ver.get('status', 'unknown')})")
    return await resolve_version_sheet_path(ver, sheet)


async def resolve_version(
    dataset_id: str,
    *,
    version_id: str | None = None,
    version_number: int | None = None,
    tag: str | None = None,
) -> dict:
    """Resolve a version target to its row, 404ing when it doesn't belong here."""
    if version_id:
        ver = await get_version(version_id)
        if not ver or str(ver["dataset_id"]) != dataset_id:
            raise HTTPException(404, f"Version {version_id} not found for dataset {dataset_id}")
    elif version_number is not None:
        ver = await get_version_by_number(dataset_id, version_number)
        if not ver:
            raise HTTPException(404, f"Version {version_number} not found for dataset {dataset_id}")
    elif tag:
        ver = await get_version_by_tag(dataset_id, tag)
        if not ver:
            raise HTTPException(404, f"Tag '{tag}' not found on dataset {dataset_id}")
    else:
        ver = await _get_current_version_or_404(dataset_id)
    return ver


async def get_version_sheet_rows(ver: dict) -> list[dict]:
    """Sheet rows for a version — the DB table first, legacy JSONB fallback."""
    rows = await list_version_sheets(str(ver["id"]))
    if rows:
        return rows
    source = ver.get("source") or {}
    return [
        {
            "id": None,
            "sheet_key": normalize_sheet_key(s["name"]),
            "sheet_name": s["name"],
            "sheet_index": i,
            "visibility": s.get("visibility", "visible"),
            "status": "ready",
            "is_default": bool(s.get("is_default", i == 0)),
            "storage_key": s.get("storage_key"),
            "row_count": s.get("row_count"),
            "column_count": s.get("column_count"),
            "size_bytes": None,
            "checksum": None,
            "schema_json": None,
            "schema_fingerprint": None,
        }
        for i, s in enumerate(source.get("sheets") or [])
    ]


def sheet_data_path(ver: dict, sheet_row: dict) -> str:
    """Full path/URI of a sheet's parquet (canonical when storage_key is NULL)."""
    if sheet_row.get("storage_key"):
        return get_storage().resolve(sheet_row["storage_key"])
    return str(ver["path"])


async def resolve_version_sheet_path(ver: dict, sheet: str | None) -> str:
    """Resolve a (version, sheet?) pair to a parquet path.

    Never silently picks a sheet: multi-sheet versions require an explicit
    name and answer with the machine-readable ``sheet-selection-required``
    problem listing the available sheets.
    """
    sheets = await get_version_sheet_rows(ver)
    if sheet:
        row = _find_sheet(sheets, sheet)
        if not row:
            raise HTTPException(404, f"Sheet not found: {sheet}")
        return sheet_data_path(ver, row)

    ready = [r for r in sheets if r.get("status", "ready") == "ready"]
    if len(ready) > 1:
        raise ProblemException(
            400,
            f"This dataset version has {len(ready)} sheets — name one via the 'sheet' parameter",
            code="sheet-selection-required",
            sheets=[r["sheet_name"] for r in ready],
        )
    if len(ready) == 1:
        return sheet_data_path(ver, ready[0])
    return str(ver["path"])


def _find_sheet(sheets: list[dict], sheet: str) -> dict | None:
    """Match by exact sheet name first, then by normalized sheet_key."""
    for r in sheets:
        if r["sheet_name"] == sheet:
            return r
    for r in sheets:
        if r["sheet_key"] == sheet:
            return r
    return None


async def ensure_sheet_schema(ver: dict, sheet_row: dict) -> dict:
    """Return *sheet_row* with schema populated, computing it if missing.

    Versions ingested before schemas were captured get their schema filled
    lazily here (parquet DESCRIBE) and persisted back — sheet data itself is
    immutable, this only completes derived metadata.
    """
    if sheet_row.get("schema_json"):
        return sheet_row
    path = sheet_data_path(ver, sheet_row)
    try:
        described = describe_parquet(path)
    except (duckdb.Error, OSError) as e:
        raise HTTPException(
            404, f"Sheet data unreadable for '{sheet_row['sheet_name']}': {type(e).__name__}",
        )
    columns, fingerprint = build_sheet_schema(described)
    updated = {
        **sheet_row,
        "schema_json": columns,
        "schema_fingerprint": fingerprint,
        "column_count": sheet_row.get("column_count") or len(columns),
    }
    if sheet_row.get("id"):
        await update_sheet_schema(
            sheet_row["id"],
            schema_json=columns,
            schema_fingerprint=fingerprint,
            extractor_version=SCHEMA_EXTRACTOR_VERSION,
            column_count=updated["column_count"],
        )
    return updated


def get_default_sheet_name(sheets: list[dict]) -> str | None:
    """The default sheet's name, if any."""
    for r in sheets:
        if r.get("is_default"):
            return r["sheet_name"]
    return sheets[0]["sheet_name"] if sheets else None


async def _get_current_version_or_404(dataset_id: str) -> dict:
    """Fetch the current version or raise 404."""
    ver = await get_current_version(dataset_id)
    if not ver or not ver.get("path"):
        raise HTTPException(404, f"Dataset not found: {dataset_id}")
    return ver
