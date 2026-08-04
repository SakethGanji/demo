"""Files API — upload, download, and storage I/O.

All routes are mounted under the service-wide ``/api/v1`` prefix (see app.main),
so the paths below are relative to that. Dataset uploads/downloads live under the
``/datasets`` tree alongside the rest of the dataset API.

Upload routes:
  POST   /upload                          Simple multipart or inline JSON upload
  GET    /upload/status/{version_id}      Poll processing status

TUS resumable upload:
  OPTIONS /tus/                           Protocol discovery
  POST    /tus/                           Create upload
  HEAD    /tus/{upload_id}                Check offset
  PATCH   /tus/{upload_id}                Append data
  DELETE  /tus/{upload_id}                Cancel upload
  GET     /tus/{upload_id}/status         Poll status after completion

Downloads:
  GET    /datasets/{id}/download                     Download current version
  GET    /datasets/{id}/versions/{v}/download        Download specific version
  GET    /samples                                    List sample files
  GET    /samples/{filename}                         Download sample file
  GET    /samples/{filename}/data                    Paginated JSON data read

Storage:
  GET    /storage/usage                   Storage usage breakdown
"""

from __future__ import annotations

import base64
import fcntl
import hashlib
import json as _json
import os
import tempfile
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response

from app.api.pagination import Page
from app.features.auth.deps import (
    Principal,
    ensure_dataset_permission,
    get_principal,
    pick_active_team,
)
from app.features.auth.permissions import Permission
from app.infra.config import settings
from app.infra.db.storage import DatasetLayout, get_storage, uploads_dir
from . import repo
from app.shared.constants import (
    ALLOWED_EXTENSIONS,
    TUS_EXTENSIONS,
    TUS_MAX_SIZE,
)
from app.shared.data_io import (
    DEFAULT_SHEET_NAME,
    SCHEMA_EXTRACTOR_VERSION,
    build_sheet_schema,
    load_data,
    manifest_fingerprint,
    parsing_provenance,
    stream_to_disk,
)
from app.shared.schemas import ColumnInfo

from .schemas import (
    FileEntry,
    StorageUsageResponse,
    UploadResponse,
)
from .services.processing import build_complete_response, process_uploaded_file_async, processing_status
from .services.tus import (
    check_disk_space,
    cleanup_stale_uploads,
    delete_tus_upload,
    load_tus_meta,
    parse_tus_metadata,
    save_tus_meta,
    tus_data_path,
    tus_headers,
    tus_lock_path,
)
from .services.downloads import (
    download_dataset,
    download_dataset_version,
    download_sample_file,
    list_sample_files,
    read_sample_data,
)
from .services.management import get_storage_usage

router = APIRouter()


# ---------------------------------------------------------------------------
# Simple upload
# ---------------------------------------------------------------------------

@router.post("/upload", response_model=UploadResponse, tags=["uploads"])
async def upload_dataset(
    background_tasks: BackgroundTasks,
    file: UploadFile | None = None,
    data: str | None = Form(default=None),
    dataset_id: str | None = Form(default=None),
    include_sheets: str | None = Form(
        default=None,
        description="Comma-separated sheet names to ingest (partial-workbook opt-in)"),
    sync: bool = Query(default=True),
    principal: Principal = Depends(get_principal),
    x_team_id: str | None = Header(default=None, alias="X-Team-Id"),
) -> UploadResponse:
    """Unified upload endpoint — accepts either a multipart file or inline JSON.

    Modes:
      1. **File upload** (multipart form):  `curl -F file=@data.csv /files/upload`
         Streams to disk in 8MB chunks — constant memory regardless of file size.
      2. **Inline JSON** (form field):      `curl -F 'data=[{"a":1},{"a":2}]' /files/upload`
         For small programmatic uploads; data is sent as a JSON string form field.

    Query params:
      - sync=true  (default) — block until processing finishes, return full metadata
      - sync=false — return immediately, process in background, poll /files/upload/status/{id}

    Form fields:
      - dataset_id (optional) — pass an existing dataset ID to create a new version
    """
    storage = get_storage()

    if file is not None and file.filename:
        # --- File upload path (streaming) ---
        suffix = Path(file.filename).suffix.lower()
        if suffix not in ALLOWED_EXTENSIONS:
            raise HTTPException(
                400,
                f"Unsupported file type: {suffix}. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}",
            )

        # Create or reuse dataset in DB (with authorization)
        if dataset_id:
            ds = await ensure_dataset_permission(principal, dataset_id, Permission.DATASET_WRITE)
        else:
            team_id = pick_active_team(principal, x_team_id)
            if not principal.can(team_id, Permission.DATASET_WRITE):
                raise HTTPException(403, "Insufficient permissions: requires dataset:write")
            ds = await repo.create_dataset(name=file.filename, team_id=team_id, owner_id=principal.user_id)
            dataset_id = str(ds["id"])

        # Create a version row (status=uploading)
        version = await repo.create_version(
            dataset_id,
            storage_type="temp",
            status="uploading",
            source={"type": "upload", "filename": file.filename},
        )
        version_id = str(version["id"])
        version_number = version["version_number"]
        team_id = str(ds.get("team_id", "default"))

        # Stream raw upload to the local staging area (keyed by version_id for
        # concurrency safety). Staging is always local, even with S3 storage.
        staging = uploads_dir() / "_staging"
        staging.mkdir(parents=True, exist_ok=True)
        raw_path = staging / f"{version_id}_raw{suffix}"
        file_size = await stream_to_disk(file, raw_path)

        # Enforce the simple-upload size cap (use TUS for very large files).
        if file_size > settings.max_upload_bytes:
            raw_path.unlink(missing_ok=True)
            await repo.fail_version(version_id, "File exceeds max upload size")
            raise HTTPException(
                413,
                f"File too large ({file_size} bytes). Max {settings.max_upload_bytes} bytes "
                f"for this endpoint — use resumable TUS upload for larger files.",
            )

        processing_status[version_id] = {"status": "uploaded", "dataset_id": dataset_id, "file_size_bytes": file_size}
        sheet_filter = ({s.strip() for s in include_sheets.split(",") if s.strip()}
                        if include_sheets else None)

        if sync:
            await process_uploaded_file_async(
                dataset_id, raw_path, version_id=version_id,
                team_id=team_id, version_number=version_number,
                source_filename=file.filename, include_sheets=sheet_filter,
            )
            info = processing_status[version_id]
            if info["status"] == "error":
                raise HTTPException(500, f"Processing failed: {info['error']}")
            return UploadResponse(
                dataset_id=dataset_id,
                version_id=version_id,
                status="complete",
                file_path=info.get("file_path"),
                file_size_bytes=file_size,
                row_count=info.get("row_count"),
                column_count=info.get("column_count"),
                columns=[ColumnInfo(**c) for c in info.get("columns", [])],
                preview=info.get("preview"),
                message="Upload and processing complete",
            )

        background_tasks.add_task(
            process_uploaded_file_async, dataset_id, raw_path, version_id,
            team_id=team_id, version_number=version_number,
            source_filename=file.filename, include_sheets=sheet_filter,
        )
        return UploadResponse(
            dataset_id=dataset_id,
            version_id=version_id,
            status="uploaded",
            file_size_bytes=file_size,
            message=f"File uploaded. Processing in background — poll {settings.api_prefix}/upload/status/{version_id}",
        )

    elif data is not None:
        # --- Inline JSON path ---
        try:
            rows = _json.loads(data)
        except _json.JSONDecodeError as e:
            raise HTTPException(400, f"Invalid JSON in data field: {e}")
        if not isinstance(rows, list) or not rows:
            raise HTTPException(400, "data must be a non-empty JSON array of objects")

        # Create dataset + version in DB (with authorization)
        if dataset_id:
            ds = await ensure_dataset_permission(principal, dataset_id, Permission.DATASET_WRITE)
        else:
            team_id = pick_active_team(principal, x_team_id)
            if not principal.can(team_id, Permission.DATASET_WRITE):
                raise HTTPException(403, "Insufficient permissions: requires dataset:write")
            ds = await repo.create_dataset(name=f"inline_{len(rows)}", team_id=team_id, owner_id=principal.user_id)
            dataset_id = str(ds["id"])

        version = await repo.create_version(
            dataset_id,
            storage_type="temp",
            status="uploading",
            source={"type": "inline_json", "row_count": len(rows)},
        )
        version_number = version["version_number"]
        team_id = str(ds.get("team_id", "default"))

        layout = DatasetLayout(team_id, dataset_id, version_number)
        layout.ensure_dirs()

        # Write parquet locally, then publish to the storage backend.
        conn = load_data(data=rows)
        with tempfile.TemporaryDirectory(prefix="accel_inline_") as td:
            local_parquet = Path(td) / "dataset.parquet"
            conn.execute(f"COPY df TO '{local_parquet}' (FORMAT PARQUET)")
            size_bytes = local_parquet.stat().st_size
            artifact_checksum = hashlib.sha256(local_parquet.read_bytes()).hexdigest()
            storage.put_file(layout.canonical_parquet, local_parquet)
        parquet_path = storage.resolve(layout.canonical_parquet)
        row_count = conn.execute("SELECT COUNT(*) FROM df").fetchone()[0]
        col_count = len(conn.execute("DESCRIBE df").fetchall())
        schema_cols, fingerprint = build_sheet_schema(conn.execute("DESCRIBE df").fetchall())
        source_checksum = hashlib.sha256(data.encode()).hexdigest()
        sheet_row = {
            "sheet_key": DEFAULT_SHEET_NAME,
            "sheet_name": DEFAULT_SHEET_NAME,
            "sheet_index": 0,
            "is_default": True,
            "storage_key": layout.canonical_parquet,
            "row_count": row_count,
            "column_count": col_count,
            "size_bytes": size_bytes,
            "checksum": artifact_checksum,
            "schema_json": schema_cols,
            "schema_fingerprint": fingerprint,
            "schema_extractor_version": SCHEMA_EXTRACTOR_VERSION,
        }
        await repo.complete_version(
            str(version["id"]),
            path=parquet_path,
            size_bytes=size_bytes,
            row_count=row_count,
            checksum=artifact_checksum,
            source_checksum=source_checksum,
            manifest_checksum=manifest_fingerprint([sheet_row]),
            sheet_count=1,
            source={"source_format": "inline_json", "ingest": parsing_provenance("inline_json")},
        )
        await repo.insert_version_sheets(str(version["id"]), [sheet_row])
        layout.write_manifest(
            row_count=row_count,
            column_count=col_count,
            size_bytes=size_bytes,
        )

        return build_complete_response(
            dataset_id, conn, parquet_path, version_id=str(version["id"]),
        )

    else:
        raise HTTPException(400, "Provide either a 'file' (multipart) or 'data' (JSON string) field")


@router.get("/upload/status/{version_id}", response_model=UploadResponse, tags=["uploads"])
async def upload_status(version_id: str, principal: Principal = Depends(get_principal)) -> UploadResponse:
    """Poll processing status for an async file upload (keyed by version_id)."""
    ver = await repo.get_version(version_id)
    if ver:
        await ensure_dataset_permission(principal, str(ver["dataset_id"]), Permission.DATASET_READ)
    if version_id not in processing_status:
        raise HTTPException(404, f"Unknown version: {version_id}")
    info = processing_status[version_id]
    columns = [ColumnInfo(**c) for c in info["columns"]] if info.get("columns") else None
    return UploadResponse(
        dataset_id=info.get("dataset_id", ""),
        version_id=version_id,
        status=info["status"],
        file_path=info.get("file_path"),
        file_size_bytes=info.get("file_size_bytes"),
        row_count=info.get("row_count"),
        column_count=info.get("column_count"),
        columns=columns,
        preview=info.get("preview"),
        error=info.get("error"),
    )


# ---------------------------------------------------------------------------
# TUS resumable upload protocol
# ---------------------------------------------------------------------------

@router.options("/tus/", tags=["uploads"])
async def tus_options() -> Response:
    """TUS discovery — tells the client what we support."""
    return Response(
        status_code=204,
        headers=tus_headers(
            **{
                "Tus-Extension": TUS_EXTENSIONS,
                "Tus-Max-Size": str(TUS_MAX_SIZE),
            }
        ),
    )


@router.post("/tus/", tags=["uploads"])
async def tus_create(
    request: Request,
    principal: Principal = Depends(get_principal),
    x_team_id: str | None = Header(default=None, alias="X-Team-Id"),
) -> Response:
    """TUS creation — client announces a new upload, we return a Location URL."""
    upload_length = request.headers.get("Upload-Length")
    if upload_length is None:
        raise HTTPException(400, "Upload-Length header is required")
    total_size = int(upload_length)

    if total_size > TUS_MAX_SIZE:
        raise HTTPException(413, f"File too large. Max: {TUS_MAX_SIZE} bytes")

    metadata = parse_tus_metadata(request.headers.get("Upload-Metadata", ""))
    filename = metadata.get("filename", "upload.bin")
    incoming_dataset_id = metadata.get("dataset_id")

    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            400,
            f"Unsupported file type: {suffix}. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}",
        )

    # Disk space check before accepting
    check_disk_space(total_size)

    # Clean up stale uploads opportunistically
    cleanup_stale_uploads()

    # Create or reuse dataset in DB (with authorization)
    if incoming_dataset_id:
        await ensure_dataset_permission(principal, incoming_dataset_id, Permission.DATASET_WRITE)
        ds_id = incoming_dataset_id
    else:
        team_id = pick_active_team(principal, x_team_id)
        if not principal.can(team_id, Permission.DATASET_WRITE):
            raise HTTPException(403, "Insufficient permissions: requires dataset:write")
        ds = await repo.create_dataset(name=filename, team_id=team_id, owner_id=principal.user_id)
        ds_id = str(ds["id"])

    # Create version row
    version = await repo.create_version(
        ds_id,
        storage_type="temp",
        status="uploading",
        size_bytes=total_size,
        source={"type": "tus_upload", "filename": filename},
    )

    upload_id = uuid.uuid4().hex
    uploads_dir().mkdir(parents=True, exist_ok=True)
    file_path = tus_data_path(upload_id, suffix)
    file_path.touch()

    meta = {
        "filename": filename,
        "suffix": suffix,
        "total_size": total_size,
        "offset": 0,
        "file_path": str(file_path),
        "created_at": time.time(),
        "dataset_id": ds_id,
        "version_id": str(version["id"]),
    }
    save_tus_meta(upload_id, meta)

    location = f"{settings.api_prefix}/tus/{upload_id}"
    return Response(
        status_code=201,
        headers=tus_headers(Location=location),
    )


@router.head("/tus/{upload_id}", tags=["uploads"])
async def tus_head(upload_id: str) -> Response:
    """TUS offset check — client asks 'how much have you received?' to resume."""
    meta = load_tus_meta(upload_id)
    if meta is None:
        raise HTTPException(404, "Upload not found")

    file_path = Path(meta["file_path"])
    actual_offset = file_path.stat().st_size if file_path.exists() else 0

    if actual_offset != meta["offset"]:
        meta["offset"] = actual_offset
        save_tus_meta(upload_id, meta)

    return Response(
        status_code=200,
        headers=tus_headers(
            **{
                "Upload-Offset": str(actual_offset),
                "Upload-Length": str(meta["total_size"]),
                "Cache-Control": "no-store",
            }
        ),
    )


@router.patch("/tus/{upload_id}", tags=["uploads"])
async def tus_patch(
    upload_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
) -> Response:
    """TUS upload — client sends bytes starting at Upload-Offset."""
    meta = load_tus_meta(upload_id)
    if meta is None:
        raise HTTPException(404, "Upload not found")

    content_type = request.headers.get("Content-Type", "")
    if content_type != "application/offset+octet-stream":
        raise HTTPException(415, "Content-Type must be application/offset+octet-stream")

    client_offset = int(request.headers.get("Upload-Offset", "-1"))
    if client_offset != meta["offset"]:
        raise HTTPException(
            409, f"Offset mismatch: server at {meta['offset']}, client sent {client_offset}"
        )

    file_path = Path(meta["file_path"])
    lock_path = tus_lock_path(upload_id)

    lock_file = open(lock_path, "w")
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        lock_file.close()
        raise HTTPException(423, "Upload is locked — another PATCH is in progress")

    checksum_header = request.headers.get("Upload-Checksum")
    expected_digest: bytes | None = None
    if checksum_header:
        parts = checksum_header.split(" ", 1)
        if len(parts) == 2 and parts[0] == "sha256":
            expected_digest = base64.b64decode(parts[1])

    try:
        bytes_received = 0
        hasher = hashlib.sha256() if expected_digest else None

        with open(file_path, "ab") as f:
            async for chunk in request.stream():
                f.write(chunk)
                bytes_received += len(chunk)
                if hasher:
                    hasher.update(chunk)
                if bytes_received % (64 * 1024 * 1024) < len(chunk):
                    f.flush()
                    os.fsync(f.fileno())

        if expected_digest and hasher and hasher.digest() != expected_digest:
            with open(file_path, "ab") as f:
                f.truncate(client_offset)
            raise HTTPException(
                460,
                "Checksum mismatch — corrupted data, PATCH rejected. Retry from same offset.",
            )

        meta["offset"] += bytes_received
        save_tus_meta(upload_id, meta)

    finally:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        lock_file.close()
        lock_path.unlink(missing_ok=True)

    # Check if upload is complete
    if meta["offset"] >= meta["total_size"]:
        dataset_id = meta["dataset_id"]
        version_id = meta["version_id"]
        processing_status[version_id] = {
            "status": "uploaded",
            "dataset_id": dataset_id,
            "file_size_bytes": meta["total_size"],
        }
        save_tus_meta(upload_id, meta)

        ds = await repo.get_dataset(dataset_id)
        team_id = str(ds["team_id"]) if ds else "default"
        ver = await repo.get_version(version_id)
        version_number = ver["version_number"] if ver else 1

        background_tasks.add_task(
            process_uploaded_file_async, dataset_id, file_path, version_id,
            team_id=team_id, version_number=version_number,
            source_filename=meta.get("filename"),
        )

    return Response(
        status_code=204,
        headers=tus_headers(**{"Upload-Offset": str(meta["offset"])}),
    )


@router.delete("/tus/{upload_id}", tags=["uploads"])
async def tus_terminate(upload_id: str) -> Response:
    """TUS termination — client cancels an in-progress upload."""
    meta = load_tus_meta(upload_id)
    if meta is None:
        raise HTTPException(404, "Upload not found")

    # Mark version as failed in DB if it exists
    version_id = meta.get("version_id")
    if version_id:
        await repo.fail_version(version_id, "Upload cancelled by client")

    delete_tus_upload(upload_id, meta)
    return Response(status_code=204, headers=tus_headers())


@router.get("/tus/{upload_id}/status", tags=["uploads"])
async def tus_upload_status(upload_id: str) -> UploadResponse:
    """Check processing status after a TUS upload completes."""
    meta = load_tus_meta(upload_id)
    if meta is None:
        raise HTTPException(404, "Upload not found")
    dataset_id = meta.get("dataset_id")
    version_id = meta.get("version_id")

    if not version_id or version_id not in processing_status:
        return UploadResponse(
            dataset_id=dataset_id or "",
            version_id=version_id,
            status="uploading",
            file_size_bytes=meta.get("total_size"),
            message=f"Upload in progress: {meta['offset']}/{meta['total_size']} bytes",
        )

    ps = processing_status[version_id]
    columns = [ColumnInfo(**c) for c in ps["columns"]] if ps.get("columns") else None
    return UploadResponse(
        dataset_id=dataset_id or "",
        version_id=version_id,
        status=ps["status"],
        file_path=ps.get("file_path"),
        file_size_bytes=ps.get("file_size_bytes"),
        row_count=ps.get("row_count"),
        column_count=ps.get("column_count"),
        columns=columns,
        preview=ps.get("preview"),
        error=ps.get("error"),
    )


# ---------------------------------------------------------------------------
# Copy-on-write sheet replacement
# ---------------------------------------------------------------------------

@router.post("/datasets/{dataset_id}/sheets/{sheet_name}/replace", tags=["sheets"])
async def replace_sheet_endpoint(
    dataset_id: str,
    sheet_name: str,
    file: UploadFile,
    principal: Principal = Depends(get_principal),
) -> dict:
    """Replace ONE sheet's data in a new immutable version.

    The uploaded file must be single-table (CSV, parquet, or one-sheet Excel).
    Every other sheet of the current version is reused copy-on-write — no data
    is duplicated — and lineage records the base version.
    """
    from .services.replace import replace_sheet

    ds = await ensure_dataset_permission(principal, dataset_id, Permission.DATASET_WRITE)
    return await replace_sheet(ds, sheet_name, file, principal)


# ---------------------------------------------------------------------------
# Downloads
# ---------------------------------------------------------------------------

@router.get("/datasets/{dataset_id}/download", tags=["downloads"])
async def download_dataset_endpoint(
    dataset_id: str,
    format: str = Query("csv", description="Download format: csv, parquet, xlsx"),
    sheet: str | None = Query(None, description="Sheet name for multi-sheet datasets"),
    columns: str | None = Query(None, description="Comma-separated column names to include"),
    limit: int | None = Query(None, ge=1, description="Max rows to include"),
    filter_expr: str | None = Query(None, description="SQL WHERE filter expression"),
    principal: Principal = Depends(get_principal),
):
    """Download the current version of a dataset.

    Supports format conversion (csv, parquet, xlsx), column subsetting,
    row limiting, and SQL filtering. Streams the response for large files.
    """
    await ensure_dataset_permission(principal, dataset_id, Permission.DATASET_READ)
    col_list = [c.strip() for c in columns.split(",") if c.strip()] if columns else None
    return await download_dataset(
        dataset_id, format=format, sheet=sheet,
        columns=col_list, limit=limit, filter_expr=filter_expr,
    )


@router.get("/datasets/{dataset_id}/versions/{version_number}/download", tags=["downloads"])
async def download_version_endpoint(
    dataset_id: str,
    version_number: int,
    format: str = Query("csv", description="Download format: csv, parquet, xlsx"),
    sheet: str | None = Query(None, description="Sheet name for multi-sheet datasets"),
    principal: Principal = Depends(get_principal),
):
    """Download a specific version of a dataset."""
    await ensure_dataset_permission(principal, dataset_id, Permission.DATASET_READ)
    return await download_dataset_version(dataset_id, version_number, format=format, sheet=sheet)


@router.get("/samples", response_model=Page[FileEntry], tags=["downloads"])
async def list_samples() -> Page[FileEntry]:
    """List all sample/export files."""
    files = await list_sample_files()
    entries = [
        FileEntry(
            key=f["key"],
            filename=f["filename"],
            size_bytes=f["size_bytes"],
            file_type="sample",
        )
        for f in files
    ]
    return Page(items=entries, total=len(entries), limit=len(entries), offset=0)


@router.get("/samples/{filename}", tags=["downloads"])
async def download_sample(filename: str):
    """Download a sample or export file."""
    return download_sample_file(filename)


@router.get("/samples/{filename}/data", tags=["downloads"])
async def read_sample(
    filename: str,
    offset: int = Query(0, ge=0, description="Row offset for pagination"),
    limit: int = Query(100, ge=1, le=10000, description="Max rows to return"),
    columns: str | None = Query(None, description="Comma-separated column names to include"),
    filter_expr: str | None = Query(None, description="SQL WHERE filter expression"),
    sort_by: str | None = Query(None, description="Column to sort by"),
    sort_order: str = Query("asc", description="Sort order: asc or desc"),
):
    """Read paginated data from a sample or result file.

    Returns JSON rows with pagination metadata. Use this to display
    results in a table UI or to pull slices without downloading the
    whole file.
    """
    col_list = [c.strip() for c in columns.split(",") if c.strip()] if columns else None
    return read_sample_data(
        filename,
        offset=offset,
        limit=limit,
        columns=col_list,
        filter_expr=filter_expr,
        sort_by=sort_by,
        sort_order=sort_order,
    )


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

@router.get("/storage/usage", response_model=StorageUsageResponse, tags=["storage"])
async def storage_usage() -> StorageUsageResponse:
    """Get storage usage breakdown by category."""
    return await get_storage_usage()
