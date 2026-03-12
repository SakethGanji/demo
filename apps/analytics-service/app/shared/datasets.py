"""Shared dataset path & sheet resolution.

Any feature that needs to resolve a dataset_id to a file path (for DuckDB,
pandas, etc.) should use these functions rather than querying the DB directly.
"""

from __future__ import annotations

from fastapi import HTTPException

from app.infra.db.storage import get_storage
from app.shared.repo import get_current_version, get_version, get_version_by_number, get_version_by_tag


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
    Without *sheet*, returns the canonical parquet path.
    With *sheet*, returns the specific sheet's parquet path.
    """
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

    if not ver.get("path"):
        raise HTTPException(404, f"Version has no data (status: {ver.get('status', 'unknown')})")
    if sheet:
        return _resolve_sheet_path(ver, sheet)
    return str(ver["path"])


def get_sheets(ver: dict) -> list[dict]:
    """Extract sheet list from version source JSONB."""
    source = ver.get("source") or {}
    return source.get("sheets", [])


def get_default_sheet(ver: dict) -> str | None:
    """Return the default sheet name (if any) from version metadata."""
    for s in get_sheets(ver):
        if s.get("is_default"):
            return s["name"]
    sheets = get_sheets(ver)
    return sheets[0]["name"] if sheets else None


async def _get_current_version_or_404(dataset_id: str) -> dict:
    """Fetch the current version or raise 404."""
    ver = await get_current_version(dataset_id)
    if not ver or not ver.get("path"):
        raise HTTPException(404, f"Dataset not found: {dataset_id}")
    return ver


def _resolve_sheet_path(ver: dict, sheet_name: str) -> str:
    """Resolve a sheet name to its parquet path from version metadata."""
    storage = get_storage()
    for s in get_sheets(ver):
        if s["name"] == sheet_name:
            return storage.resolve(s["storage_key"])
    raise HTTPException(404, f"Sheet not found: {sheet_name}")
