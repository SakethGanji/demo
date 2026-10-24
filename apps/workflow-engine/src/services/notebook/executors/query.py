"""query: one SELECT on a project connection (read-only transaction, at most ``limit`` <= 500 rows)."""

from __future__ import annotations

import time
from typing import Any

from . import CellOutcome
from ._common import Fail, connection_url, ms_since


async def run(ctx: Any, cell: Any, inputs: dict[str, Any]) -> CellOutcome:
    from ... import external_db as xdb
    from ..storage import preview

    t0 = time.perf_counter()
    try:
        _conn, url = await connection_url(ctx, inputs.get("connection"))
    except Fail as f:
        return f.outcome
    limit = int(inputs.get("limit") or 500)
    params = inputs.get("params") or None
    try:
        res = await xdb.run_readonly(url, inputs.get("sql") or "", params, limit, max_limit=500)
    except xdb.ExternalDbError as e:
        code = {"query_not_allowed": "query_failed", "timeout": "timeout"}.get(e.code, "query_failed")
        return CellOutcome(status="error", error={"code": code, "message": e.message, "detail_code": e.code},
                           ms=ms_since(t0))
    rows = res["rows"]
    return CellOutcome(output=preview(rows, res["columns"], truncated=bool(res["truncated"]), cap=limit),
                       full_rows=rows, ms=ms_since(t0))
