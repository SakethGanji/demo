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
    "get_version_sheets",
]


def _sheet_summary(row: dict) -> SheetSummary:
    return SheetSummary(
        name=row["sheet_name"],
        sheet_key=row["sheet_key"],
        logical_sheet_id=row.get("logical_sheet_id"),
        storage_key=row.get("storage_key"),
        row_count=row.get("row_count") or 0,
        column_count=row.get("column_count") or 0,
        is_default=bool(row.get("is_default")),
        visibility=row.get("visibility", "visible"),
        status=row.get("status", "ready"),
    )


async def _preview_masking(ver: dict, sheet_row: dict | None,
                           principal) -> dict[str, str | None]:
    """Sensitive columns to mask in a preview drawn from *sheet_row*.

    Previews are literal dataset cells, so the data dictionary's `sensitivity`
    declaration governs them exactly as it governs the explorer's preview and
    the row-diff samples. ``principal`` is optional only so the internal
    callers that never render a preview keep working; a None principal is
    treated as un-elevated and masks.
    """
    from app.shared.masking import resolve_masking

    if not sheet_row or not sheet_row.get("logical_sheet_id"):
        return {}
    sheet_row = await ensure_sheet_schema(ver, sheet_row)
    return await resolve_masking(str(ver["dataset_id"]), sheet_row, principal)


def _sheet_response(row: dict, preview: list[dict] | None = None,
                    masked: dict[str, str | None] | None = None) -> SheetMetadataResponse:
    from app.shared.masking import mask_rows

    schema = row.get("schema_json") or []
    if preview is not None and masked:
        preview = mask_rows(preview, masked)
    return SheetMetadataResponse(
        name=row["sheet_name"],
        sheet_key=row["sheet_key"],
        logical_sheet_id=row.get("logical_sheet_id"),
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
        masked_columns=sorted(masked or {}),
    )


async def get_dataset_metadata(dataset_id: str, principal=None) -> DatasetMetadataResponse:
    ver = await _get_current_version_or_404(dataset_id)
    file_path = str(ver["path"])
    conn = load_data(file_path=file_path)
    try:
        meta = extract_metadata(conn)
    finally:
        conn.close()

    sheets = await get_version_sheet_rows(ver)
    default_sheet = get_default_sheet_name(sheets)
    # The preview is read from the version's default sheet, so that is the
    # sheet whose dictionary decides what has to be masked here.
    masked = await _preview_masking(
        ver, _find_sheet(sheets, default_sheet) if default_sheet else None, principal)
    from app.shared.masking import mask_rows

    return DatasetMetadataResponse(
        dataset_id=dataset_id,
        file_path=file_path,
        row_count=meta["row_count"],
        column_count=meta["column_count"],
        columns=[ColumnInfo(**c) for c in meta["columns"]],
        preview=mask_rows(meta["preview"], masked) if masked else meta["preview"],
        sheets=[_sheet_summary(r) for r in sheets] or None,
        default_sheet=default_sheet,
        masked_columns=sorted(masked),
    )


async def get_dataset_sheets(dataset_id: str) -> list[SheetMetadataResponse]:
    """Full metadata for every sheet — served from Postgres, no file I/O.

    Legacy versions ingested before schemas were captured get their schema
    computed and persisted on first read.
    """
    ver = await _get_current_version_or_404(dataset_id)
    return await get_version_sheets(ver)


async def get_version_sheets(ver: dict) -> list[SheetMetadataResponse]:
    """Full metadata for every sheet of *ver*, whichever version that is.

    ``get_dataset_sheets`` only ever answers for the CURRENT version, which is
    not the version a caller is querying whenever one is pinned or a tag
    rollback has moved the current pointer backwards. Consumers that offered
    those names as "the tables in this version" were describing a schema the
    query would not see.
    """
    rows = await get_version_sheet_rows(ver)
    if not rows:
        raise HTTPException(404, "No sheets recorded for this dataset version")
    return [_sheet_response(await ensure_sheet_schema(ver, r)) for r in rows]


async def get_sheet_metadata(dataset_id: str, sheet_name: str,
                             principal=None) -> SheetMetadataResponse:
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
    masked = await _preview_masking(ver, row, principal)
    return _sheet_response(row, preview=meta["preview"], masked=masked)
