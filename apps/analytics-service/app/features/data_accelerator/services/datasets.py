"""Dataset service — feature-specific metadata responses.

Path resolution and sheet lookups live in ``app.shared.datasets``.
This module builds the data_accelerator-specific response schemas.
"""

from __future__ import annotations

from fastapi import HTTPException

from app.infra.db.storage import get_storage
from app.shared.datasets import (
    get_default_sheet,
    get_sheets,
    resolve_dataset_path,
    _get_current_version_or_404,
    _resolve_sheet_path,
)
from app.shared.data_io import extract_metadata, load_data
from app.shared.schemas import ColumnInfo

from ..schemas import DatasetMetadataResponse, SheetMetadataResponse, SheetSummary

# Re-export for consumers that import from here
__all__ = [
    "resolve_dataset_path",
    "get_dataset_metadata",
    "get_dataset_sheets",
    "get_sheet_metadata",
]


async def get_dataset_metadata(dataset_id: str) -> DatasetMetadataResponse:
    ver = await _get_current_version_or_404(dataset_id)
    file_path = str(ver["path"])
    conn = load_data(file_path=file_path)
    try:
        meta = extract_metadata(conn)
    finally:
        conn.close()

    sheets = get_sheets(ver)
    sheet_summaries = [SheetSummary(**s) for s in sheets] if sheets else None
    default_sheet = get_default_sheet(ver)

    return DatasetMetadataResponse(
        dataset_id=dataset_id,
        file_path=file_path,
        row_count=meta["row_count"],
        column_count=meta["column_count"],
        columns=[ColumnInfo(**c) for c in meta["columns"]],
        preview=meta["preview"],
        sheets=sheet_summaries,
        default_sheet=default_sheet,
    )


async def get_dataset_sheets(dataset_id: str) -> list[SheetMetadataResponse]:
    """Return full metadata for every sheet in a dataset."""
    ver = await _get_current_version_or_404(dataset_id)
    sheets = get_sheets(ver)
    if not sheets:
        raise HTTPException(404, "Dataset has no sheets (not an Excel upload)")

    storage = get_storage()
    results: list[SheetMetadataResponse] = []
    for s in sheets:
        path = storage.resolve(s["storage_key"])
        conn = load_data(file_path=path)
        try:
            meta = extract_metadata(conn)
        finally:
            conn.close()
        results.append(SheetMetadataResponse(
            name=s["name"],
            row_count=meta["row_count"],
            column_count=meta["column_count"],
            columns=[ColumnInfo(**c) for c in meta["columns"]],
            preview=meta["preview"],
        ))
    return results


async def get_sheet_metadata(dataset_id: str, sheet_name: str) -> SheetMetadataResponse:
    """Return full metadata for a single sheet."""
    ver = await _get_current_version_or_404(dataset_id)
    path = _resolve_sheet_path(ver, sheet_name)
    conn = load_data(file_path=path)
    try:
        meta = extract_metadata(conn)
    finally:
        conn.close()
    return SheetMetadataResponse(
        name=sheet_name,
        row_count=meta["row_count"],
        column_count=meta["column_count"],
        columns=[ColumnInfo(**c) for c in meta["columns"]],
        preview=meta["preview"],
    )
