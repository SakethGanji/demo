"""TUS resumable upload protocol — state management, helpers."""

from __future__ import annotations

import base64
import binascii
import json as _json
import logging
import os
import time
from pathlib import Path
from typing import Any

from app.api.errors import ProblemException
from app.infra.db.storage import uploads_dir
from app.shared.constants import TUS_UPLOAD_EXPIRY_SECONDS, TUS_VERSION
from .. import repo

logger = logging.getLogger(__name__)


def tus_headers(**extra: str) -> dict[str, str]:
    """Standard TUS response headers included on every response."""
    return {
        "Tus-Resumable": TUS_VERSION,
        "Tus-Version": TUS_VERSION,
        "Tus-Checksum-Algorithm": "sha256",
        **extra,
    }


def parse_tus_metadata(header: str) -> dict[str, str]:
    """Parse the Upload-Metadata header: 'key base64val, key2 base64val2' -> dict.

    A malformed value is the client's error, so it is a 400 naming the key —
    the same rule ``decode_cursor`` applies to opaque page cursors. Letting
    ``binascii.Error``/``UnicodeDecodeError`` escape turned a mistyped header
    into an opaque 500 that blamed the server.
    """
    result: dict[str, str] = {}
    if not header:
        return result
    for pair in header.split(","):
        parts = pair.strip().split(" ", 1)
        key = parts[0]
        try:
            val = base64.b64decode(parts[1], validate=True).decode() if len(parts) > 1 else ""
        except (binascii.Error, ValueError, UnicodeDecodeError) as exc:
            raise ProblemException(
                400,
                f"Malformed Upload-Metadata: value for {key!r} is not base64-encoded UTF-8.",
                code="invalid-upload-metadata", key=key,
            ) from exc
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
        # An explicit code, because 507 is outside the service's default title
        # map: without it the envelope said title "Error"/code "error" and a
        # client could not tell "out of disk" from any other failure.
        raise ProblemException(
            507,
            f"Insufficient disk space. Free: {free_gb:.1f}GB, need: {needed_gb:.1f}GB",
            code="insufficient-storage",
        )


async def cleanup_stale_uploads() -> int:
    """Remove uploads older than TUS_UPLOAD_EXPIRY_SECONDS. Returns count removed.

    Also closes the ``dataset_versions`` row the abandoned upload created. The
    sweep used to delete only the staging files, so the version row sat at
    status ``uploading`` forever: it kept showing up in the version list as
    permanently "processing", it had consumed a version number, and once the
    meta file was gone GET /tus/{id}/status 404'd, leaving the UI no way to
    explain it. Stranded lifecycle rows are defects here (see b69bfc0), so the
    sweep now reports them failed rather than abandoning them.
    """
    if not uploads_dir().exists():
        return 0
    now = time.time()
    removed = 0
    for meta_file in uploads_dir().glob("*.meta.json"):
        try:
            meta = _json.loads(meta_file.read_text())
            updated_at = meta.get("updated_at", meta.get("created_at", 0))
            if now - updated_at <= TUS_UPLOAD_EXPIRY_SECONDS:
                continue
            upload_id = meta_file.stem.replace(".meta", "")
            version_id = meta.get("version_id")
            if version_id:
                # Guarded by fail_version: an upload that completed and went
                # ready before its meta file expired is left alone.
                await repo.fail_version(version_id, "Upload expired without completing")
            delete_tus_upload(upload_id, meta)
            removed += 1
        except Exception:
            # One unreadable meta file must not abort the whole sweep.
            logger.warning("Stale-upload sweep skipped %s", meta_file, exc_info=True)
            continue
    return removed
