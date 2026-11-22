"""The training overlay (labeling plan §4.4, §5.5). Filled in below the contract commit."""

from __future__ import annotations

from typing import Any

from .values import LabelError


def verify_run(db: Any, store: Any, run_id: str) -> dict[str, Any]:
    raise LabelError(501, "not_built", "Run overlay verify lands in the next LB-A commit.")
