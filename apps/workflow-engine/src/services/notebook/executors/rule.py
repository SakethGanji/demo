"""rule: compare / filter / count / assert over row sets (pure Python, ``services/notebook/rule.py``)."""

from __future__ import annotations

from typing import Any

from . import CellOutcome


async def run(ctx: Any, cell: Any, inputs: dict[str, Any]) -> CellOutcome:
    from .. import rule as R
    from ..storage import preview

    try:
        res = R.evaluate(inputs)
    except R.RuleError as e:
        return CellOutcome.fail("rule_failed", e.message, **e.extra)
    rows = res.pop("rows")
    cols = res.pop("columns", None)
    out = preview(rows, cols, **res)
    if not res["passed"]:
        return CellOutcome(status="error", output=out, full_rows=rows,
                           error={"code": "rule_failed", "message": res["summary"]})
    return CellOutcome(output=out, full_rows=rows)
