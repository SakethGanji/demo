"""wait: hold the run (status ``waiting``) for a duration, or until a read-only query's rows are non-empty / empty.

v1 polls inside the run loop (the heartbeat keeps going); a wait does not survive an engine restart (the run is
reaped as ``interrupted``). Event waits are coming.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from . import CellOutcome
from ._common import Fail, connection_url, ms_since


async def run(ctx: Any, cell: Any, inputs: dict[str, Any]) -> CellOutcome:
    from ... import external_db as xdb
    from ..rule import RuleError, duration_seconds

    t0 = time.perf_counter()
    try:
        timeout = duration_seconds(inputs.get("timeout") or "1h")
        poll = max(0.5, duration_seconds(inputs.get("poll_every") or "30s"))
        dur = duration_seconds(inputs["duration"]) if inputs.get("mode", "duration") == "duration" else None
    except (RuleError, KeyError, TypeError) as e:
        return CellOutcome.fail("invalid_spec", getattr(e, "message", "a duration wait needs duration"))
    await ctx.set_run_status("waiting", {"reason": "waiting", "cell_id": cell.id})
    try:
        if dur is not None:
            await asyncio.sleep(dur)
            return CellOutcome(output={"mode": "duration", "waited_s": round(time.perf_counter() - t0, 1)},
                               ms=ms_since(t0))
        cond = inputs.get("condition") or {}
        try:
            _conn, url = await connection_url(ctx, cond.get("connection"))
        except Fail as f:
            return f.outcome
        polls = 0
        while True:
            polls += 1
            try:
                res = await xdb.run_readonly(url, cond.get("sql") or "", cond.get("params") or None, 50, max_limit=50)
            except xdb.ExternalDbError as e:
                return CellOutcome(status="error", error={"code": "query_failed", "message": e.message},
                                   ms=ms_since(t0))
            holds = bool(res["rows"]) if cond.get("expect", "nonempty") == "nonempty" else not res["rows"]
            if holds:
                return CellOutcome(output={"mode": "condition", "polls": polls, "rows": res["rows"],
                                           "columns": res["columns"], "row_count": len(res["rows"]),
                                           "waited_s": round(time.perf_counter() - t0, 1)}, ms=ms_since(t0))
            if time.perf_counter() - t0 + poll > timeout:
                return CellOutcome(status="error", ms=ms_since(t0), output={"polls": polls},
                                   error={"code": "timeout", "message": f"the condition did not hold within "
                                                                        f"{inputs.get('timeout') or '1h'}"})
            await asyncio.sleep(poll)
    finally:
        await ctx.set_run_status("running", None)
