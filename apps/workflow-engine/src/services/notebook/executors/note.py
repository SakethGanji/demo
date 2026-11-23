"""note: markdown only; the run loop skips it."""

from __future__ import annotations

from typing import Any

from . import CellOutcome


async def run(ctx: Any, cell: Any, inputs: dict[str, Any]) -> CellOutcome:
    return CellOutcome(output={"markdown": (inputs or {}).get("markdown", "")})
