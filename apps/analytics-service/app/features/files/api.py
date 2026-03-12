"""Files API — upload, download, and storage I/O.

Upload routes:
  POST   /files/upload                    Simple multipart or inline JSON upload
  GET    /files/upload/status/{version_id} Poll processing status

TUS resumable upload:
  OPTIONS /files/tus/                     Protocol discovery
  POST    /files/tus/                     Create upload
  HEAD    /files/tus/{upload_id}          Check offset
  PATCH   /files/tus/{upload_id}          Append data
  DELETE  /files/tus/{upload_id}          Cancel upload
  GET     /files/tus/{upload_id}/status   Poll status after completion

Downloads:
  GET    /files/datasets/{id}/download              Download current version
  GET    /files/datasets/{id}/versions/{v}/download  Download specific version
  GET    /files/samples                              List sample files
  GET    /files/samples/{filename}                   Download sample file

Storage:
  GET    /files/storage/usage             Storage usage breakdown
"""

from __future__ import annotations

import base64
import fcntl
import hashlib
import json as _json
import os
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response

from app.infra.db.storage import DatasetLayout, get_storage, uploads_dir
from . import repo
from app.shared.constants import (
    ALLOWED_EXTENSIONS,
    TUS_EXTENSIONS,
    TUS_MAX_SIZE,
)
from app.shared.data_io import load_data, stream_to_disk
from app.shared.schemas import ColumnInfo

from .schemas import (
    FileEntry,
    FileListResponse,
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
)
from .services.management import get_storage_usage

router = APIRouter(prefix="/files", tags=["files"])


# ---------------------------------------------------------------------------
# Simple upload
# ---------------------------------------------------------------------------

@router.post("/upload", response_model=UploadResponse)
async def upload_dataset(
    background_tasks: BackgroundTasks,
    file: UploadFile | None = None,
    data: str | None = Form(default=None),
    dataset_id: str | None = Form(default=None),
    sync: bool = Query(default=True),
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

        # Create or reuse dataset in DB
        if dataset_id:
            ds = await repo.get_dataset(dataset_id)
            if not ds:
                raise HTTPException(404, f"Dataset not found: {dataset_id}")
        else:
            ds = await repo.create_dataset(name=file.filename)
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

        # Stream raw upload to temp staging area (keyed by version_id for concurrency safety)
        raw_key = f"datasets/_staging/{version_id}_raw{suffix}"
        raw_path = Path(storage.resolve(raw_key))
        storage.ensure_dir("datasets/_staging")
        file_size = await stream_to_disk(file, raw_path)

        processing_status[version_id] = {"status": "uploaded", "dataset_id": dataset_id, "file_size_bytes": file_size}

        if sync:
            await process_uploaded_file_async(
                dataset_id, raw_path, version_id=version_id,
                team_id=team_id, version_number=version_number,
                source_filename=file.filename,
            )
            info = processing_status[version_id]
            if info["status"] == "error":
                raise HTTPException(500, f"Processing failed: {info['error']}")
            return UploadResponse(
                dataset_id=dataset_id,
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
            source_filename=file.filename,
        )
        return UploadResponse(
            dataset_id=dataset_id,
            version_id=version_id,
            status="uploaded",
            file_size_bytes=file_size,
            message="File uploaded. Processing in background — poll /files/upload/status/{version_id}",
        )

    elif data is not None:
        # --- Inline JSON path ---
        try:
            rows = _json.loads(data)
        except _json.JSONDecodeError as e:
            raise HTTPException(400, f"Invalid JSON in data field: {e}")
        if not isinstance(rows, list) or not rows:
            raise HTTPException(400, "data must be a non-empty JSON array of objects")

        # Create dataset + version in DB
        if dataset_id:
            ds = await repo.get_dataset(dataset_id)
            if not ds:
                raise HTTPException(404, f"Dataset not found: {dataset_id}")
        else:
            ds = await repo.create_dataset(name=f"inline_{len(rows)}")
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

        conn = load_data(data=rows)
        parquet_path = storage.resolve(layout.canonical_parquet)
        conn.execute(f"COPY df TO '{parquet_path}' (FORMAT PARQUET)")

        size_bytes = storage.size(layout.canonical_parquet)
        row_count = conn.execute("SELECT COUNT(*) FROM df").fetchone()[0]
        col_count = len(conn.execute("DESCRIBE df").fetchall())
        await repo.complete_version(
            str(version["id"]),
            path=parquet_path,
            size_bytes=size_bytes,
            row_count=row_count,
        )
        layout.write_manifest(
            row_count=row_count,
            column_count=col_count,
            size_bytes=size_bytes,
        )

        return build_complete_response(dataset_id, conn, parquet_path)

    else:
        raise HTTPException(400, "Provide either a 'file' (multipart) or 'data' (JSON string) field")


@router.get("/upload/status/{version_id}", response_model=UploadResponse)
async def upload_status(version_id: str) -> UploadResponse:
    """Poll processing status for an async file upload (keyed by version_id)."""
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

@router.options("/tus/")
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


@router.post("/tus/")
async def tus_create(request: Request) -> Response:
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

    # Create or reuse dataset in DB
    if incoming_dataset_id:
        ds = await repo.get_dataset(incoming_dataset_id)
        if not ds:
            raise HTTPException(404, f"Dataset not found: {incoming_dataset_id}")
        ds_id = incoming_dataset_id
    else:
        ds = await repo.create_dataset(name=filename)
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

    location = f"/files/tus/{upload_id}"
    return Response(
        status_code=201,
        headers=tus_headers(Location=location),
    )


@router.head("/tus/{upload_id}")
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


@router.patch("/tus/{upload_id}")
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


@router.delete("/tus/{upload_id}")
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


@router.get("/tus/{upload_id}/status")
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
# Downloads
# ---------------------------------------------------------------------------

@router.get("/datasets/{dataset_id}/download")
async def download_dataset_endpoint(
    dataset_id: str,
    format: str = Query("csv", description="Download format: csv, parquet, xlsx"),
    sheet: str | None = Query(None, description="Sheet name for multi-sheet datasets"),
    columns: str | None = Query(None, description="Comma-separated column names to include"),
    limit: int | None = Query(None, ge=1, description="Max rows to include"),
    filter_expr: str | None = Query(None, description="SQL WHERE filter expression"),
):
    """Download the current version of a dataset.

    Supports format conversion (csv, parquet, xlsx), column subsetting,
    row limiting, and SQL filtering. Streams the response for large files.
    """
    col_list = [c.strip() for c in columns.split(",") if c.strip()] if columns else None
    return await download_dataset(
        dataset_id, format=format, sheet=sheet,
        columns=col_list, limit=limit, filter_expr=filter_expr,
    )


@router.get("/datasets/{dataset_id}/versions/{version_number}/download")
async def download_version_endpoint(
    dataset_id: str,
    version_number: int,
    format: str = Query("csv", description="Download format: csv, parquet, xlsx"),
):
    """Download a specific version of a dataset."""
    return await download_dataset_version(dataset_id, version_number, format=format)


@router.get("/samples", response_model=FileListResponse)
async def list_samples():
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
    return FileListResponse(files=entries, total_count=len(entries))


@router.get("/samples/{filename}")
async def download_sample(filename: str):
    """Download a sample or export file."""
    return download_sample_file(filename)


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

@router.get("/storage/usage", response_model=StorageUsageResponse)
async def storage_usage() -> StorageUsageResponse:
    """Get storage usage breakdown by category."""
    return await get_storage_usage()
