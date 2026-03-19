"""Download service — format conversion, streaming, file management."""

from __future__ import annotations

import logging
import shutil
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from app.infra.db.storage import DatasetLayout, get_storage, sample_key
from app.shared.data_io import export_dataframe, load_data
from app.shared.datasets import resolve_dataset_path
from app.shared.repo import get_current_version
from app.shared.utils.sql import quote_ident, safe_value, sanitize_filter_expr

logger = logging.getLogger(__name__)

DOWNLOAD_CHUNK_SIZE = 8 * 1024 * 1024  # 8MB

MEDIA_TYPES = {
    "csv": "text/csv",
    "parquet": "application/octet-stream",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "excel": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


async def _file_chunks(path: str) -> AsyncIterator[bytes]:
    """Yield file in chunks for streaming response."""
    with open(path, "rb") as f:
        while chunk := f.read(DOWNLOAD_CHUNK_SIZE):
            yield chunk


def streaming_file_response(
    file_path: str,
    filename: str,
    media_type: str = "application/octet-stream",
) -> StreamingResponse:
    """Create a streaming response for large file downloads."""
    return StreamingResponse(
        _file_chunks(file_path),
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(Path(file_path).stat().st_size),
        },
    )


async def download_dataset(
    dataset_id: str,
    format: str = "csv",
    sheet: str | None = None,
    columns: list[str] | None = None,
    limit: int | None = None,
    filter_expr: str | None = None,
) -> StreamingResponse:
    """Prepare and stream a dataset download in the requested format.

    Loads the dataset's parquet via DuckDB, applies optional column selection,
    row limit, and filter, then exports to a temp file and streams it back.
    """
    fmt = format.lower()
    if fmt in ("excel",):
        fmt = "xlsx"
    if fmt not in ("csv", "parquet", "xlsx"):
        raise HTTPException(400, f"Unsupported format: {format}. Use csv, parquet, or xlsx.")

    file_path = await resolve_dataset_path(dataset_id, sheet=sheet)
    conn = load_data(file_path=file_path)

    try:
        # Build SELECT with optional column subsetting
        if columns:
            desc_rows = conn.execute("DESCRIBE df").fetchall()
            available = {r[0] for r in desc_rows}
            missing = [c for c in columns if c not in available]
            if missing:
                raise HTTPException(400, f"Columns not found: {missing}")
            select_cols = ", ".join(quote_ident(c) for c in columns)
        else:
            select_cols = "*"

        # Build WHERE
        where = ""
        if filter_expr:
            sanitize_filter_expr(filter_expr)
            where = f" WHERE {filter_expr}"

        # Build LIMIT
        limit_clause = f" LIMIT {int(limit)}" if limit else ""

        # Create filtered view
        conn.execute(
            f"CREATE TABLE _download AS SELECT {select_cols} FROM df{where}{limit_clause}"
        )

        # Export to temp file
        suffix = f".{fmt}"
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        tmp.close()
        export_dataframe(conn, tmp.name, fmt, table_name="_download")
    finally:
        conn.close()

    # Determine filename
    ver = await get_current_version(dataset_id)
    source = (ver or {}).get("source") or {}
    base_name = source.get("filename", f"dataset_{dataset_id[:8]}")
    base_stem = Path(base_name).stem
    dl_filename = f"{base_stem}.{fmt}"

    media_type = MEDIA_TYPES.get(fmt, "application/octet-stream")

    async def _stream_and_cleanup() -> AsyncIterator[bytes]:
        try:
            async for chunk in _file_chunks(tmp.name):
                yield chunk
        finally:
            Path(tmp.name).unlink(missing_ok=True)

    return StreamingResponse(
        _stream_and_cleanup(),
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{dl_filename}"',
            "Content-Length": str(Path(tmp.name).stat().st_size),
        },
    )


async def download_dataset_version(
    dataset_id: str,
    version_number: int,
    format: str = "csv",
) -> StreamingResponse:
    """Download a specific version of a dataset."""
    from app.shared.repo import get_version_by_number

    fmt = format.lower()
    if fmt in ("excel",):
        fmt = "xlsx"
    if fmt not in ("csv", "parquet", "xlsx"):
        raise HTTPException(400, f"Unsupported format: {format}")

    row = await get_version_by_number(dataset_id, version_number)

    if not row or row.get("status") != "ready" or not row.get("path"):
        raise HTTPException(404, f"Version {version_number} not found for dataset {dataset_id}")

    file_path = row["path"]

    if fmt == "parquet":
        # Stream parquet directly, no conversion needed
        source = row.get("source") or {}
        base_name = source.get("filename", f"dataset_{dataset_id[:8]}")
        dl_filename = f"{Path(base_name).stem}_v{version_number}.parquet"
        return streaming_file_response(file_path, dl_filename)

    # Convert via DuckDB
    conn = load_data(file_path=file_path)
    try:
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=f".{fmt}")
        tmp.close()
        export_dataframe(conn, tmp.name, fmt)
    finally:
        conn.close()

    source = row.get("source") or {}
    base_name = source.get("filename", f"dataset_{dataset_id[:8]}")
    dl_filename = f"{Path(base_name).stem}_v{version_number}.{fmt}"
    media_type = MEDIA_TYPES.get(fmt, "application/octet-stream")

    async def _stream_and_cleanup() -> AsyncIterator[bytes]:
        try:
            async for chunk in _file_chunks(tmp.name):
                yield chunk
        finally:
            Path(tmp.name).unlink(missing_ok=True)

    return StreamingResponse(
        _stream_and_cleanup(),
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{dl_filename}"',
            "Content-Length": str(Path(tmp.name).stat().st_size),
        },
    )


def read_sample_data(
    filename: str,
    offset: int = 0,
    limit: int = 100,
    columns: list[str] | None = None,
    filter_expr: str | None = None,
    sort_by: str | None = None,
    sort_order: str = "asc",
) -> dict:
    """Read paginated data from a sample/result parquet file via DuckDB."""
    storage = get_storage()
    key = sample_key(filename)
    if not storage.exists(key):
        raise HTTPException(404, f"File not found: {filename}")
    path = storage.resolve(key)

    conn = load_data(file_path=path)
    try:
        # Total row count (unfiltered)
        total_count = conn.execute("SELECT COUNT(*) FROM df").fetchone()[0]

        # Column metadata
        desc = conn.execute("DESCRIBE df").fetchall()
        all_columns = [{"name": r[0], "dtype": r[1]} for r in desc]

        # Build SELECT
        if columns:
            available = {r[0] for r in desc}
            missing = [c for c in columns if c not in available]
            if missing:
                raise HTTPException(400, f"Columns not found: {missing}")
            select_cols = ", ".join(quote_ident(c) for c in columns)
        else:
            select_cols = "*"

        # Build WHERE
        where = ""
        if filter_expr:
            sanitize_filter_expr(filter_expr)
            where = f" WHERE {filter_expr}"

        # Count after filter
        filtered_count = conn.execute(f"SELECT COUNT(*) FROM df{where}").fetchone()[0]

        # Build ORDER BY
        order = ""
        if sort_by:
            available = {r[0] for r in desc}
            if sort_by not in available:
                raise HTTPException(400, f"Sort column not found: {sort_by}")
            direction = "DESC" if sort_order.lower() == "desc" else "ASC"
            order = f" ORDER BY {quote_ident(sort_by)} {direction}"

        # Query with pagination
        query = f"SELECT {select_cols} FROM df{where}{order} LIMIT {limit} OFFSET {offset}"
        result = conn.execute(query)
        col_names = [d[0] for d in result.description]
        rows = [
            {col_names[i]: safe_value(v) for i, v in enumerate(row)}
            for row in result.fetchall()
        ]
    finally:
        conn.close()

    return {
        "filename": filename,
        "total_count": total_count,
        "filtered_count": filtered_count,
        "offset": offset,
        "limit": limit,
        "columns": all_columns,
        "data": rows,
    }


def download_sample_file(filename: str) -> StreamingResponse:
    """Resolve and stream a sample file for download."""
    storage = get_storage()
    key = sample_key(filename)
    if not storage.exists(key):
        raise HTTPException(404, f"File not found: {filename}")
    path = storage.resolve(key)
    return streaming_file_response(path, filename)


async def list_sample_files() -> list[dict]:
    """List all sample files in storage."""
    storage = get_storage()
    keys = storage.list_keys("samples")
    files = []
    for key in keys:
        path = Path(storage.resolve(key))
        filename = path.name
        try:
            size = path.stat().st_size
        except OSError:
            continue
        files.append({
            "key": key,
            "filename": filename,
            "size_bytes": size,
        })
    return files
