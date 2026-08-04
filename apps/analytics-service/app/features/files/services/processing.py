"""Upload processing — background processing, status tracking, response builder."""

from __future__ import annotations

import hashlib
import logging
import tempfile
from pathlib import Path
from typing import Any

import duckdb

from app.infra.db.storage import DatasetLayout, get_storage
from app.shared import jobs
from .. import repo
from app.shared.data_io import (
    DEFAULT_SHEET_NAME,
    SCHEMA_EXTRACTOR_VERSION,
    ConversionResult,
    build_sheet_schema,
    convert_to_parquet,
    describe_parquet,
    extract_metadata,
    manifest_fingerprint,
    normalize_sheet_keys,
    parsing_provenance,
)
from app.shared.scanning import scan_upload
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


def _build_sheet_rows(
    result: ConversionResult,
    layout: DatasetLayout,
    local_parquet: Path,
    meta: dict[str, Any],
    canonical_size: int,
    canonical_checksum: str,
    sheet_keys: list[str],
) -> list[dict[str, Any]]:
    """Per-sheet metadata rows (schema, fingerprint, checksum) for the DB.

    Must run while the locally-built parquet artifacts still exist — schema
    extraction and checksumming read the local files, not storage. Every ready
    sheet gets an explicit storage_key (the NULL fallback exists only for
    rows backfilled from pre-Phase-1 versions).
    """
    rows: list[dict[str, Any]] = []
    if result.sheets:
        multi = result.is_multi_sheet
        for idx, s in enumerate(result.sheets):
            local = Path(s.parquet_path)
            columns, fingerprint = build_sheet_schema(
                describe_parquet(str(local)), s.original_columns,
            )
            rows.append({
                "sheet_key": sheet_keys[idx],
                "sheet_name": s.name,
                "sheet_index": idx,
                "visibility": s.visibility,
                "status": "ready",
                "is_default": s.is_default,
                # Sheet parquets are stored under the deduplicated sheet_key
                # (sanitized names can collide); single-sheet workbooks write
                # straight to the canonical parquet.
                "storage_key": layout.sheet_parquet(sheet_keys[idx]) if multi
                               else layout.canonical_parquet,
                "row_count": s.row_count,
                "column_count": s.column_count,
                "size_bytes": local.stat().st_size,
                "checksum": _file_checksum(str(local)),
                "schema_json": columns,
                "schema_fingerprint": fingerprint,
                "schema_extractor_version": SCHEMA_EXTRACTOR_VERSION,
            })
    else:
        # CSV/parquet source — one synthetic sheet over the canonical parquet.
        columns, fingerprint = build_sheet_schema(describe_parquet(str(local_parquet)))
        rows.append({
            "sheet_key": DEFAULT_SHEET_NAME,
            "sheet_name": DEFAULT_SHEET_NAME,
            "sheet_index": 0,
            "visibility": "visible",
            "status": "ready",
            "is_default": True,
            "storage_key": layout.canonical_parquet,
            "row_count": meta.get("row_count"),
            "column_count": meta.get("column_count"),
            "size_bytes": canonical_size,
            "checksum": canonical_checksum,
            "schema_json": columns,
            "schema_fingerprint": fingerprint,
            "schema_extractor_version": SCHEMA_EXTRACTOR_VERSION,
        })
    return rows


async def process_uploaded_file_async(
    dataset_id: str,
    raw_path: Path,
    version_id: str | None = None,
    *,
    team_id: str = "default",
    version_number: int = 1,
    source_filename: str | None = None,
    include_sheets: set[str] | None = None,
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

        # Malware/content scan before we touch the file (no-op unless configured).
        scan = await scan_upload(raw_path, filename=source_filename)
        if not scan.ok:
            raise ValueError(f"Upload rejected by scanner: {scan.reason or 'infected'}")

        # Identity of the exact uploaded bytes, before any conversion.
        source_checksum = _file_checksum(str(raw_path))
        source_format = raw_path.suffix.lower().lstrip(".")

        # Build artifacts in a local temp dir, then publish to the storage
        # backend (local FS or S3) via put_file — conversion, checksumming, and
        # metadata extraction always run against real local files.
        with tempfile.TemporaryDirectory(prefix="accel_build_") as build_dir:
            local_parquet = Path(build_dir) / "dataset.parquet"
            sheet_locals: dict[str, Path] = {}

            def sheet_path_fn(sheet_name: str) -> str:
                p = Path(build_dir) / "sheets" / f"sheet_{len(sheet_locals):04d}.parquet"
                sheet_locals[sheet_name] = p
                return str(p)

            result: ConversionResult = convert_to_parquet(
                raw_path, local_parquet, sheet_path_fn=sheet_path_fn,
                include_sheets=include_sheets,
            )
            await jobs.update_job_progress(job_id, 50)

            meta = extract_metadata(result.conn)
            result.conn.close()

            size_bytes = local_parquet.stat().st_size
            checksum = _file_checksum(str(local_parquet))

            storage.put_file(layout.canonical_parquet, local_parquet)
            # Sheet parquets are keyed by deduplicated sheet_key — sanitized
            # display names can collide ("Q 1" and "Q-1"), keys cannot.
            sheet_keys = normalize_sheet_keys([s.name for s in result.sheets])

            # Schema + checksum per sheet, while local artifacts still exist.
            sheet_rows = _build_sheet_rows(
                result, layout, local_parquet, meta, size_bytes, checksum, sheet_keys,
            )

            # Artifact reuse: a sheet whose checksum matches the previous ready
            # version points at that version's parquet instead of re-uploading.
            reused_sheets: list[str] = []
            if version_id:
                prev = {p["sheet_key"]: p for p in await repo.get_previous_version_sheets(
                    dataset_id, version_number)}
                for row in sheet_rows:
                    p = prev.get(row["sheet_key"])
                    if (p and p.get("storage_key") and p.get("checksum")
                            and p["checksum"] == row["checksum"]
                            and p.get("status", "ready") == "ready"):
                        row["storage_key"] = p["storage_key"]
                        reused_sheets.append(row["sheet_name"])

            for key, s in zip(sheet_keys, result.sheets):
                local_sheet = sheet_locals.get(s.name)
                if local_sheet is not None and local_sheet.exists() and s.name not in reused_sheets:
                    storage.put_file(layout.sheet_parquet(key), local_sheet)

        parquet_path = storage.resolve(layout.canonical_parquet)
        await jobs.update_job_progress(job_id, 80)

        # Precise version summaries: row_count is the TOTAL across sheets;
        # the manifest checksum is the version's canonical content identity.
        total_rows = sum(r["row_count"] or 0 for r in sheet_rows)
        manifest_checksum = manifest_fingerprint(sheet_rows)

        # Build sheet info for manifest and DB source
        sheets_meta = None
        default_sheet = result.default_sheet
        if result.sheets and result.is_multi_sheet:
            sheets_meta = [
                {
                    "name": r["sheet_name"],
                    "sheet_key": r["sheet_key"],
                    "storage_key": r["storage_key"],
                    "row_count": r["row_count"],
                    "column_count": r["column_count"],
                    "is_default": r["is_default"],
                    "visibility": r["visibility"],
                    "checksum": r["checksum"],
                }
                for r in sheet_rows
            ]

        processing_status[status_key].update(
            status="complete",
            file_path=parquet_path,
            sheets=sheets_meta,
            **meta,
        )
        processing_status[status_key]["row_count"] = total_rows

        if version_id:
            provenance = parsing_provenance(source_format)
            if include_sheets is not None:
                provenance["options"]["include_sheets"] = sorted(include_sheets)
                provenance["options"]["excluded_sheets"] = result.excluded_sheets
            if reused_sheets:
                provenance["reused_sheets"] = reused_sheets
            source_update: dict[str, Any] = {
                "source_format": source_format,
                "ingest": provenance,
            }
            if sheets_meta:
                source_update["sheets"] = sheets_meta
            await repo.complete_version(
                version_id,
                path=parquet_path,
                size_bytes=size_bytes,
                row_count=total_rows,
                checksum=checksum,
                source_checksum=source_checksum,
                manifest_checksum=manifest_checksum,
                sheet_count=len(sheet_rows),
                source=source_update,
            )
            await repo.insert_version_sheets(version_id, sheet_rows)

        layout.write_manifest(
            row_count=total_rows,
            column_count=meta.get("column_count"),
            size_bytes=size_bytes,
            checksum=checksum,
            source_checksum=source_checksum,
            manifest_checksum=manifest_checksum,
            sheets=sheets_meta,
            default_sheet=default_sheet,
        )

        await jobs.complete_job(job_id, result={
            "file_path": parquet_path,
            "storage_key": layout.canonical_parquet,
            "size_bytes": size_bytes,
            "row_count": total_rows,
            "column_count": meta.get("column_count"),
            "checksum": checksum,
            "source_checksum": source_checksum,
            "manifest_checksum": manifest_checksum,
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
