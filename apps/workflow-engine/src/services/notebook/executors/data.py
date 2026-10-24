"""data: rows typed or pasted into the notebook (CSV is parsed at create time)."""

from __future__ import annotations

from typing import Any

from . import CellOutcome


async def run(ctx: Any, cell: Any, inputs: dict[str, Any]) -> CellOutcome:
    from ..service import normalize_data_spec
    from ..storage import preview

    spec = normalize_data_spec(inputs or {})
    rows = list(spec.get("rows") or [])
    return CellOutcome(output=preview(rows, spec.get("columns") or None), full_rows=rows)
