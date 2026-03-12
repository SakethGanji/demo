"""File management — listing, deletion, storage usage."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from fastapi import HTTPException

from app.infra.db.storage import get_storage, uploads_dir
from app.features.data_accelerator import repo as da_repo
from .. import repo
from ..schemas import StorageUsageResponse
from app.features.data_accelerator.schemas import DeleteResponse

logger = logging.getLogger(__name__)


async def delete_dataset_with_files(dataset_id: str) -> DeleteResponse:
    """Delete a dataset from DB and remove all associated storage files."""
    ds = await repo.get_dataset(dataset_id)
    if not ds:
        raise HTTPException(404, f"Dataset not found: {dataset_id}")

    storage = get_storage()
    paths = await da_repo.delete_dataset(dataset_id)

    # Clean up storage directories for all versions
    deleted_keys: list[str] = []
    for path in paths:
        p = Path(path)
        # Walk up to the version directory (e.g. .../v000001/)
        version_dir = p.parent.parent  # parquet/dataset.parquet -> parquet -> v000001
        if version_dir.exists() and version_dir.is_dir():
            try:
                shutil.rmtree(version_dir)
                deleted_keys.append(str(version_dir))
            except OSError as e:
                logger.warning("Failed to remove %s: %s", version_dir, e)

    return DeleteResponse(
        success=True,
        message=f"Deleted dataset {dataset_id} ({len(paths)} version(s))",
        deleted_keys=deleted_keys,
    )


async def get_storage_usage() -> StorageUsageResponse:
    """Compute storage usage by category."""
    storage = get_storage()

    def _dir_size(prefix: str) -> int:
        keys = storage.list_keys(prefix)
        total = 0
        for key in keys:
            try:
                total += storage.size(key)
            except OSError:
                pass
        return total

    datasets_bytes = _dir_size("datasets")
    samples_bytes = _dir_size("samples")
    exports_bytes = _dir_size("exports")

    uploads_bytes = 0
    ud = uploads_dir()
    if ud.exists():
        for f in ud.iterdir():
            if f.is_file():
                uploads_bytes += f.stat().st_size

    return StorageUsageResponse(
        total_bytes=datasets_bytes + samples_bytes + exports_bytes + uploads_bytes,
        datasets_bytes=datasets_bytes,
        samples_bytes=samples_bytes,
        exports_bytes=exports_bytes,
        uploads_bytes=uploads_bytes,
    )
