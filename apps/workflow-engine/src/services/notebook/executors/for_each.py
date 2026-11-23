"""for_each: run the body cells once per row of ``over`` (at most ``max_rows``). Each body cell's result is stored
with ``row_index``. A failing row is counted and the loop goes on, unless ``stop_on_fail`` (then no further row starts
and the cell fails).

- ``mode: sequential`` runs rows in order; ``parallel`` runs up to ``concurrency`` rows at once (``child_task`` is
  refused at create: coming).
- A retry (``ctx.only_rows``) runs just those rows and merges them into the retried output (``ctx.previous``).
- Each result row also carries the input ``row`` (when it is an object), so later cells can act on the failed or
  passed inputs: a rule filter on ``status`` and then ``{{cells.cN.rows.row.id}}``.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from . import CellOutcome
from ._common import ms_since


async def run(ctx: Any, cell: Any, inputs: dict[str, Any]) -> CellOutcome:
    t0 = time.perf_counter()
    over = inputs.get("over")
    if isinstance(over, dict) and isinstance(over.get("rows"), list):
        over = over["rows"]
    if over is None:
        over = []
    if not isinstance(over, list):
        return CellOutcome.fail("bad_ref", "over must be a list of rows (e.g. {{cells.c2.rows}})")
    cap = int(inputs.get("max_rows") or 500)
    if len(over) > cap:
        return CellOutcome.fail("row_cap_exceeded", f"{len(over)} rows is more than this loop's limit of {cap}",
                                cap=cap, row_count=len(over))
    parallel = inputs.get("mode") == "parallel"
    stop_on_fail = bool(inputs.get("stop_on_fail"))
    todo = list(range(len(over)))
    per_row: dict[int, dict[str, Any]] = {}
    only = getattr(ctx, "only_rows", None)
    if only is not None:
        for r in ((getattr(ctx, "previous", None) or {}).get("rows") or []):
            if isinstance(r, dict) and isinstance(r.get("i"), int):
                per_row[r["i"]] = r
        todo = [i for i in only if 0 <= i < len(over)]
    stopped: list[int] = []

    async def one(i: int) -> None:
        if stopped:
            return
        res = await ctx.run_body(over[i], i)
        if res.ok:
            per_row[i] = {"i": i, "status": "success"}
        else:
            per_row[i] = {"i": i, "status": "error", "error": res.error}
        if isinstance(over[i], dict):
            per_row[i]["row"] = over[i]
            if stop_on_fail:
                stopped.append(i)

    if parallel:
        sem = asyncio.Semaphore(int(inputs.get("concurrency") or 4))

        async def guarded(i: int) -> None:
            async with sem:
                await one(i)

        await asyncio.gather(*(guarded(i) for i in todo))
    else:
        for i in todo:
            await one(i)
            if stopped:
                break
    rows = [per_row[i] for i in sorted(per_row)]
    ok = sum(1 for r in rows if r["status"] == "success")
    failed = sum(1 for r in rows if r["status"] == "error")
    stopped_at = min(stopped) if stopped else None
    out = {"ok": ok, "failed": failed, "row_count": len(over), "rows": rows[:500], "columns": ["i", "status", "error", "row"],
           "stopped_at": stopped_at, "mode": "parallel" if parallel else "sequential",
           "retried": todo if only is not None else None,
           "summary": f"{ok} ok, {failed} failed of {len(over)}"}
    if stopped_at is not None:
        err = per_row[stopped_at].get("error") or {}
        return CellOutcome(status="error", output=out, ms=ms_since(t0),
                           error={"code": err.get("code") or "internal", "row_index": stopped_at, "row_error": err,
                                  "message": f"row {stopped_at} failed and the loop stops on failure"})
    return CellOutcome(output=out, ms=ms_since(t0))
