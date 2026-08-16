"""File management — listing, deletion, storage usage."""

from __future__ import annotations

import logging
import posixpath

from fastapi import HTTPException

from app.infra.db.storage import (
    ARTIFACT_ROOT,
    dataset_artifact_prefix,
    get_storage,
    uploads_dir,
)
from app.features.data_accelerator import repo as da_repo
from app.features.library import repo as library_repo
from .. import repo
from .processing import processing_status
from ..schemas import StorageUsageResponse
from app.features.data_accelerator.schemas import DeleteResponse

logger = logging.getLogger(__name__)


async def delete_dataset_with_files(dataset_id: str) -> DeleteResponse:
    """Delete a dataset from DB and remove all associated storage files."""
    ds = await repo.get_dataset(dataset_id)
    if not ds:
        raise HTTPException(404, f"Dataset not found: {dataset_id}")

    storage = get_storage()
    # Snapshot owned-artifact keys BEFORE the DB delete — the artifact rows go
    # with the dataset via FK cascade, but their blobs don't.
    artifact_keys = await library_repo.list_dataset_artifact_keys(dataset_id)
    paths = await da_repo.delete_dataset(dataset_id)

    # Clean up stored artifacts for all versions (works for local FS and S3).
    deleted_keys: list[str] = []
    for path in paths:
        key = storage.key_of(str(path))
        if not key:
            logger.warning("Stored path %s does not belong to the active backend; skipping", path)
            continue
        # Walk up to the version root (…/vNNNNNN/parquet/dataset.parquet -> …/vNNNNNN)
        version_root = posixpath.dirname(posixpath.dirname(key))
        try:
            if storage.delete_prefix(version_root):
                deleted_keys.append(version_root)
        except Exception as e:
            logger.warning("Failed to remove %s: %s", version_root, e)

    # Analytics-run outputs. The dataset prefix is the primary sweep — it is
    # one call and it also reaches blobs whose artifact row never landed, which
    # the key-by-key loop could not. Keys outside it (a dataset that changed
    # teams) are then cleaned individually.
    prefix = dataset_artifact_prefix(str(ds["team_id"]), dataset_id)
    try:
        if storage.delete_prefix(prefix):
            deleted_keys.append(prefix)
    except Exception as e:
        logger.warning("Failed to remove artifact prefix %s: %s", prefix, e)

    for key in artifact_keys:
        if key.startswith(prefix + "/"):
            continue
        try:
            storage.delete(key)
            deleted_keys.append(key)
        except Exception as e:
            logger.warning("Failed to remove artifact %s: %s", key, e)

    # Drop the deleted dataset's upload-status cache entries. Nothing ever
    # evicted them, so the dict grew for the life of the process and each entry
    # kept holding the version's preview ROWS and column list long after the
    # dataset itself was gone.
    for key in [k for k, v in processing_status.items()
                if v.get("dataset_id") == dataset_id]:
        processing_status.pop(key, None)

    return DeleteResponse(
        success=True,
        message=f"Deleted dataset {dataset_id} ({len(paths)} version(s), "
                f"{len(artifact_keys)} artifact(s))",
        deleted_keys=deleted_keys,
    )


async def get_storage_usage() -> StorageUsageResponse:
    """Compute storage usage by category."""
    storage = get_storage()

    # list_sizes, not list_keys + size(): the latter is a HEAD request per
    # object on S3, so a bucket with 24k objects made this endpoint take half a
    # minute. The listing already carries every size.
    datasets_bytes = sum(n for _, n in storage.list_sizes("datasets"))

    # Derived outputs all live under one root now; the kind is the 4th segment
    # (artifacts/{team}/{dataset}/{kind}/...), so one scan splits exports from
    # everything else. `exports/` as a top-level prefix is gone — an export is
    # just another artifact kind, owned like the file it was converted from.
    samples_bytes = exports_bytes = 0
    for key, size in storage.list_sizes(ARTIFACT_ROOT):
        parts = key.split("/")
        if len(parts) > 3 and parts[3] == "export":
            exports_bytes += size
        else:
            samples_bytes += size

    # Everything under the uploads dir counts: flat TUS data/meta/lock files
    # *and* the `_staging/` subdirectory the simple-upload path streams into.
    # A non-recursive scan reported 0 bytes for every in-flight simple upload,
    # so an admin watching this screen could not see a stuck upload's bytes.
    uploads_bytes = 0
    ud = uploads_dir()
    if ud.exists():
        for f in ud.rglob("*"):
            if f.is_file():
                uploads_bytes += f.stat().st_size

    return StorageUsageResponse(
        total_bytes=datasets_bytes + samples_bytes + exports_bytes + uploads_bytes,
        datasets_bytes=datasets_bytes,
        samples_bytes=samples_bytes,
        exports_bytes=exports_bytes,
        uploads_bytes=uploads_bytes,
    )
