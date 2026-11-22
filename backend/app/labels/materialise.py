"""Materialise a snapshot into a new version (labeling plan §5.6). Filled in below the contract commit."""

from __future__ import annotations

from typing import Any

from .values import LabelError


def materialise(app: Any, snapshot_id: str, version_id: str | None, by: str, *, columns: list[str] | None = None,
                reason: str = "") -> dict[str, Any]:
    raise LabelError(501, "not_built", "Materialise lands in the next LB-A commit.")
