"""store: rows into a project data table (``append``, or ``upsert`` by ``key``); ``new_table`` creates the table with
column types inferred from the rows. Data tables reserve ``id``, ``created_at`` and ``updated_at`` (any row carrying
them used to fail the cell), so those fields are stored as ``src_id``, ``src_created_at`` and ``src_updated_at``."""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from . import CellOutcome
from ._common import ms_since


def _type(vals: list[Any]) -> str:
    vals = [v for v in vals if v is not None]
    if not vals:
        return "string"
    if all(isinstance(v, bool) for v in vals):
        return "boolean"
    if all(isinstance(v, int) and not isinstance(v, bool) for v in vals):
        return "integer"
    if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in vals):
        return "number"
    if all(isinstance(v, (dict, list)) for v in vals):
        return "json"
    return "string"


def _rows(v: Any) -> list[dict[str, Any]]:
    if isinstance(v, dict) and isinstance(v.get("rows"), list):
        v = v["rows"]
    if not isinstance(v, list):
        raise ValueError("rows must be a list of rows (e.g. {{cells.c2.rows}})")
    return [_rename(r) if isinstance(r, dict) else {"value": r} for r in v]


RESERVED = ("id", "created_at", "updated_at")


def stored_name(k: str) -> str:
    return f"src_{k}" if k in RESERVED else k


def _rename(r: dict[str, Any]) -> dict[str, Any]:
    if not any(k in r for k in RESERVED):
        return r
    return {(stored_name(k) if stored_name(k) not in r else k): v for k, v in r.items()}


async def run(ctx: Any, cell: Any, inputs: dict[str, Any]) -> CellOutcome:
    from ...data_table_service import DataTableService
    from ...project_service import resolve_project
    from ..storage import columns_of

    t0 = time.perf_counter()
    try:
        rows = _rows(inputs.get("rows"))
    except ValueError as e:
        return CellOutcome.fail("store_failed", str(e))
    keys = inputs.get("key")
    keys = [stored_name(k) for k in ([keys] if isinstance(keys, str) else list(keys or []))]
    try:
        async with ctx.session() as s:
            project = await resolve_project(s, ctx.project_id)
            svc = DataTableService(s)
            created = False
            if inputs.get("table_id"):
                t = await svc.get_table(project, inputs["table_id"])
            else:
                name = inputs.get("new_table")
                t = await svc.table_by_name(project.id, name)
                if t is None:
                    cols = columns_of(rows)
                    await svc.create_table(project, {"name": name, "columns": [
                        {"name": c, "type": _type([r.get(c) for r in rows])} for c in cols]})
                    t = await svc.table_by_name(project.id, name)
                    created = True
            inserted = updated = 0
            if inputs.get("mode") == "upsert" and keys:
                existing = await _existing(s, t, keys)
                new = []
                for r in rows:
                    k = tuple(str(r.get(x)) for x in keys)
                    if k in existing:
                        await svc.update_row(t, existing[k], r, True)
                        updated += 1
                    else:
                        new.append(r)
                if new:
                    await svc.insert_rows(t, new)
                inserted = len(new)
            elif rows:
                for i in range(0, len(rows), 1000):
                    await svc.insert_rows(t, rows[i:i + 1000])
                inserted = len(rows)
            tid, tname = t.id, t.name
    except Exception as e:  # noqa: BLE001 - DataTableError and friends become a cell error
        return CellOutcome(status="error", error={"code": "store_failed", "message": getattr(e, "message", str(e))[:500]},
                           ms=ms_since(t0))
    return CellOutcome(output={"table_id": tid, "table": tname, "created": created, "inserted": inserted,
                               "updated": updated, "row_count": len(rows), "mode": inputs.get("mode") or "append",
                               "at": datetime.now().isoformat()}, ms=ms_since(t0))


async def _existing(s: Any, t: Any, keys: list[str]) -> dict[tuple, Any]:
    from sqlalchemy import select

    from ....db.models import DataTableRowModel

    rows = (await s.execute(select(DataTableRowModel.id, DataTableRowModel.data)
                            .where(DataTableRowModel.table_id == t.id))).all()
    out: dict[tuple, Any] = {}
    for rid, data in rows:
        out.setdefault(tuple(str((data or {}).get(x)) for x in keys), rid)
    return out
