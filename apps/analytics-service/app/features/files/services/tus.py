"""TUS resumable upload protocol — state management, helpers."""

from __future__ import annotations

import base64
import json as _json
import os
import time
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from app.infra.db.storage import uploads_dir
from app.shared.constants import TUS_UPLOAD_EXPIRY_SECONDS, TUS_VERSION


def tus_headers(**extra: str) -> dict[str, str]:
    """Standard TUS response headers included on every response."""
    return {
        "Tus-Resumable": TUS_VERSION,
        "Tus-Version": TUS_VERSION,
        "Tus-Checksum-Algorithm": "sha256",
        **extra,
    }


def parse_tus_metadata(header: str) -> dict[str, str]:
    """Parse the Upload-Metadata header: 'key base64val, key2 base64val2' -> dict."""
    result: dict[str, str] = {}
    if not header:
        return result
    for pair in header.split(","):
        parts = pair.strip().split(" ", 1)
        key = parts[0]
        val = base64.b64decode(parts[1]).decode() if len(parts) > 1 else ""
        result[key] = val
    return result


def tus_meta_path(upload_id: str) -> Path:
    """Path to the JSON metadata file for a TUS upload."""
    return uploads_dir() / f"{upload_id}.meta.json"


def tus_data_path(upload_id: str, suffix: str) -> Path:
    """Path to the actual data file for a TUS upload."""
    return uploads_dir() / f"{upload_id}{suffix}"


def tus_lock_path(upload_id: str) -> Path:
    """Path to the lock file preventing concurrent PATCH on same upload."""
    return uploads_dir() / f"{upload_id}.lock"


def save_tus_meta(upload_id: str, meta: dict[str, Any]) -> None:
    """Persist upload metadata to disk as JSON."""
    meta["updated_at"] = time.time()
    tus_meta_path(upload_id).write_text(_json.dumps(meta))


def load_tus_meta(upload_id: str) -> dict[str, Any] | None:
    """Load upload metadata from disk. Returns None if not found."""
    path = tus_meta_path(upload_id)
    if not path.exists():
        return None
    return _json.loads(path.read_text())


def delete_tus_upload(upload_id: str, meta: dict[str, Any] | None = None) -> None:
    """Remove all files associated with a TUS upload."""
    if meta is None:
        meta = load_tus_meta(upload_id)
    if meta:
        data_path = Path(meta.get("file_path", ""))
        if data_path.exists():
            data_path.unlink()
    tus_meta_path(upload_id).unlink(missing_ok=True)
    tus_lock_path(upload_id).unlink(missing_ok=True)


def check_disk_space(required_bytes: int) -> None:
    """Raise if the upload directory doesn't have enough free space."""
    uploads_dir().mkdir(parents=True, exist_ok=True)
    stat = os.statvfs(uploads_dir())
    free_bytes = stat.f_bavail * stat.f_frsize
    # Require at least 2x the upload size (raw file + parquet conversion headroom)
    if free_bytes < required_bytes * 2:
        free_gb = free_bytes / (1024 ** 3)
        needed_gb = (required_bytes * 2) / (1024 ** 3)
        raise HTTPException(
            507,
            f"Insufficient disk space. Free: {free_gb:.1f}GB, need: {needed_gb:.1f}GB",
        )


def cleanup_stale_uploads() -> int:
    """Remove uploads older than TUS_UPLOAD_EXPIRY_SECONDS. Returns count removed."""
    if not uploads_dir().exists():
        return 0
    now = time.time()
    removed = 0
    for meta_file in uploads_dir().glob("*.meta.json"):
        try:
            meta = _json.loads(meta_file.read_text())
            updated_at = meta.get("updated_at", meta.get("created_at", 0))
            if now - updated_at > TUS_UPLOAD_EXPIRY_SECONDS:
                upload_id = meta_file.stem.replace(".meta", "")
                delete_tus_upload(upload_id, meta)
                removed += 1
        except Exception:
            continue
    return removed
