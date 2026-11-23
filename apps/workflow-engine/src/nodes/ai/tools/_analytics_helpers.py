"""Shared helpers for analytics tools."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ....engine.types import ExecutionContext


def resolve_dataset_ref(input_data: dict[str, Any]) -> dict[str, Any]:
    """Pass ``dataset_id`` through to the analytics service, which resolves it to the dataset's current version itself.

    When a dataset_id is given, inline ``data`` is dropped (the dataset wins). Backward-compatible: if only ``data``
    is provided, the payload is returned unchanged.
    """
    payload = dict(input_data)
    if payload.get("dataset_id"):
        payload.pop("data", None)
    else:
        payload.pop("dataset_id", None)
    return payload


async def resolve_dataset_payload(input_data: dict[str, Any], context: ExecutionContext) -> dict[str, Any]:
    """``resolve_dataset_ref`` plus project names: a ``dataset_id`` that is not a UUID is a dataset name (or link id)
    in the run's project and becomes the analytics UUID. Raises ``DatasetNotFound`` when the project has no such dataset."""
    from ....services.project_dataset_service import resolve_dataset_for_run

    payload = resolve_dataset_ref(input_data)
    if payload.get("dataset_id"):
        payload["dataset_id"] = await resolve_dataset_for_run(getattr(context, "project_id", None), payload["dataset_id"])
    return payload
