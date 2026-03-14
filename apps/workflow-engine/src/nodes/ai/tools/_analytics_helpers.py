"""Shared helpers for analytics tools."""

from __future__ import annotations

from typing import Any

DATASETS_DIR = "/tmp/accelerator/datasets"


def resolve_dataset_ref(input_data: dict[str, Any]) -> dict[str, Any]:
    """If dataset_id is present, swap it for file_path.

    Backward-compatible: if only ``data`` is provided (no dataset_id),
    the payload is returned unchanged.
    """
    payload = dict(input_data)
    dataset_id = payload.pop("dataset_id", None)
    if dataset_id:
        payload.pop("data", None)
        payload["file_path"] = f"{DATASETS_DIR}/{dataset_id}.parquet"
    return payload
