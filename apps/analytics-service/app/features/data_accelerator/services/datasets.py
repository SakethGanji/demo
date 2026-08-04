"""Dataset service — feature-specific metadata responses.

Path resolution and sheet lookups live in ``app.shared.datasets``.
This module builds the data_accelerator-specific response schemas.
"""

from __future__ import annotations

from fastapi import HTTPException

from app.shared.datasets import (
    _find_sheet,
    _get_current_version_or_404,
    ensure_sheet_schema,
    get_default_sheet_name,
    get_version_sheet_rows,
    resolve_dataset_path,
    sheet_data_path,
)
from app.shared.data_io import extract_metadata, load_data
from app.shared.schemas import ColumnInfo

from ..schemas import (
    DatasetMetadataResponse,
    SheetColumn,
    SheetMetadataResponse,
    SheetSummary,
)

# Re-export for consumers that import from here
__all__ = [
    "resolve_dataset_path",
    "get_dataset_metadata",
    "get_dataset_sheets",
    "get_sheet_metadata",
]


def _sheet_summary(row: dict) -> SheetSummary:
    return SheetSummary(
        name=row["sheet_name"],
        sheet_key=row["sheet_key"],
        storage_key=row.get("storage_key"),
        row_count=row.get("row_count") or 0,
        column_count=row.get("column_count") or 0,
        is_default=bool(row.get("is_default")),
        visibility=row.get("visibility", "visible"),
        status=row.get("status", "ready"),
    )


def _sheet_response(row: dict, preview: list[dict] | None = None) -> SheetMetadataResponse:
    schema = row.get("schema_json") or []
    return SheetMetadataResponse(
        name=row["sheet_name"],
        sheet_key=row["sheet_key"],
        visibility=row.get("visibility", "visible"),
        status=row.get("status", "ready"),
        is_default=bool(row.get("is_default")),
        row_count=row.get("row_count") or 0,
        column_count=row.get("column_count") or len(schema),
        size_bytes=row.get("size_bytes"),
        checksum=row.get("checksum"),
        schema_fingerprint=row.get("schema_fingerprint"),
        columns=[SheetColumn(**c) for c in schema],
        preview=preview,
    )


async def get_dataset_metadata(dataset_id: str) -> DatasetMetadataResponse:
    ver = await _get_current_version_or_404(dataset_id)
    file_path = str(ver["path"])
    conn = load_data(file_path=file_path)
    try:
        meta = extract_metadata(conn)
    finally:
        conn.close()

    sheets = await get_version_sheet_rows(ver)
    return DatasetMetadataResponse(
        dataset_id=dataset_id,
        file_path=file_path,
        row_count=meta["row_count"],
        column_count=meta["column_count"],
        columns=[ColumnInfo(**c) for c in meta["columns"]],
        preview=meta["preview"],
        sheets=[_sheet_summary(r) for r in sheets] or None,
        default_sheet=get_default_sheet_name(sheets),
    )


async def get_dataset_sheets(dataset_id: str) -> list[SheetMetadataResponse]:
    """Full metadata for every sheet — served from Postgres, no file I/O.

    Legacy versions ingested before schemas were captured get their schema
    computed and persisted on first read.
    """
    ver = await _get_current_version_or_404(dataset_id)
    rows = await get_version_sheet_rows(ver)
    if not rows:
        raise HTTPException(404, "No sheets recorded for this dataset version")
    return [_sheet_response(await ensure_sheet_schema(ver, r)) for r in rows]


async def get_sheet_metadata(dataset_id: str, sheet_name: str) -> SheetMetadataResponse:
    """Full metadata for a single sheet, including a data preview."""
    ver = await _get_current_version_or_404(dataset_id)
    rows = await get_version_sheet_rows(ver)
    row = _find_sheet(rows, sheet_name)
    if not row:
        raise HTTPException(404, f"Sheet not found: {sheet_name}")
    row = await ensure_sheet_schema(ver, row)

    conn = load_data(file_path=sheet_data_path(ver, row))
    try:
        meta = extract_metadata(conn)
    finally:
        conn.close()
    if not row.get("row_count"):
        row = {**row, "row_count": meta["row_count"]}
    return _sheet_response(row, preview=meta["preview"])
