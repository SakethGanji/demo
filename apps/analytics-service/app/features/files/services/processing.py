"""Upload processing — background processing, status tracking, response builder."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any

import duckdb

from app.infra.db.storage import DatasetLayout, get_storage
from app.shared import jobs
from .. import repo
from app.shared.data_io import ConversionResult, convert_to_parquet, extract_metadata
from app.shared.schemas import ColumnInfo

from ..schemas import UploadResponse

logger = logging.getLogger(__name__)

# In-memory status for fast polling (supplements the jobs table).
# Kept for sync uploads where we need the result immediately in the same request.
processing_status: dict[str, dict[str, Any]] = {}


def _file_checksum(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


async def process_uploaded_file_async(
    dataset_id: str,
    raw_path: Path,
    version_id: str | None = None,
    *,
    team_id: str = "default",
    version_number: int = 1,
    source_filename: str | None = None,
) -> None:
    """Convert raw upload to Parquet, update status + DB + jobs."""
    storage = get_storage()
    layout = DatasetLayout(team_id, dataset_id, version_number, source_filename)
    layout.ensure_dirs()

    # Create a job record
    job = await jobs.create_job(
        "import",
        dataset_id=dataset_id,
        dataset_version_id=version_id,
        parameters={"source_file": str(raw_path)},
    )
    job_id = str(job["id"])
    await jobs.start_job(job_id)

    status_key = version_id or dataset_id
    try:
        processing_status.setdefault(status_key, {})["status"] = "processing"
        await jobs.update_job_progress(job_id, 10)

        parquet_path = storage.resolve(layout.canonical_parquet)

        # Build per-sheet path resolver for Excel files
        def sheet_path_fn(sheet_name: str) -> str:
            return storage.resolve(layout.sheet_parquet(sheet_name))

        result: ConversionResult = convert_to_parquet(
            raw_path, Path(parquet_path), sheet_path_fn=sheet_path_fn,
        )
        await jobs.update_job_progress(job_id, 60)

        meta = extract_metadata(result.conn)
        result.conn.close()
        await jobs.update_job_progress(job_id, 80)

        size_bytes = storage.size(layout.canonical_parquet)
        checksum = _file_checksum(parquet_path)

        # Build sheet info for manifest and DB source
        sheets_meta = None
        default_sheet = result.default_sheet
        if result.sheets and result.is_multi_sheet:
            sheets_meta = [
                {
                    "name": s.name,
                    "storage_key": layout.sheet_parquet(s.name),
                    "row_count": s.row_count,
                    "column_count": s.column_count,
                    "is_default": s.is_default,
                }
                for s in result.sheets
            ]

        processing_status[status_key].update(
            status="complete",
            file_path=parquet_path,
            sheets=sheets_meta,
            **meta,
        )

        if version_id:
            source_update = {}
            if sheets_meta:
                source_update["sheets"] = sheets_meta
            await repo.complete_version(
                version_id,
                path=parquet_path,
                size_bytes=size_bytes,
                row_count=meta.get("row_count"),
                checksum=checksum,
                source=source_update if source_update else None,
            )

        layout.write_manifest(
            row_count=meta.get("row_count"),
            column_count=meta.get("column_count"),
            size_bytes=size_bytes,
            checksum=checksum,
            sheets=sheets_meta,
            default_sheet=default_sheet,
        )

        await jobs.complete_job(job_id, result={
            "file_path": parquet_path,
            "storage_key": layout.canonical_parquet,
            "size_bytes": size_bytes,
            "row_count": meta.get("row_count"),
            "column_count": meta.get("column_count"),
            "checksum": checksum,
            "sheets": [s.name for s in result.sheets] if result.sheets else None,
        })

    except Exception as e:
        logger.exception("Processing failed for %s", dataset_id)
        processing_status[status_key].update(status="error", error=str(e))
        if version_id:
            await repo.fail_version(version_id, str(e))
        await jobs.fail_job(job_id, str(e))
    finally:
        if raw_path.exists():
            raw_path.unlink()



def build_complete_response(
    dataset_id: str, conn: duckdb.DuckDBPyConnection, file_path: str, **extra: Any,
) -> UploadResponse:
    """Build a full UploadResponse from an open DuckDB connection."""
    meta = extract_metadata(conn)
    conn.close()
    return UploadResponse(
        dataset_id=dataset_id,
        status="complete",
        file_path=file_path,
        row_count=meta["row_count"],
        column_count=meta["column_count"],
        columns=[ColumnInfo(**c) for c in meta["columns"]],
        preview=meta["preview"],
        **extra,
    )
