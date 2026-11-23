"""change: a proposed UPDATE on a writable connection. In a run it only reads the before-image and returns
``needs_approval`` with the diff; the write happens on ``POST /cells/{id}/apply`` (``apply_change``), once per
idempotency key, in one transaction.

``key`` is ``{column: value}`` (AND-ed equality; a list value means "any of these", which is how a change is scoped to
exactly the rows a user selected, or to ``{{cells.cN.rows.col}}``), ``set`` is ``{column: new value}``. Identifiers must be plain
(``schema.table`` / ``column``) and are quoted; values are always bound parameters. At most 500 rows per change.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from typing import Any

from . import CellOutcome
from ._common import Fail, connection_url, ms_since

ROW_CAP = 500
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _qi(name: str) -> str:
    parts = str(name).split(".")
    if not parts or len(parts) > 2 or not all(_IDENT.match(p) for p in parts):
        raise ValueError(f"'{name}' is not a plain table/column name")
    return ".".join(f'"{p}"' for p in parts)


def _where(key: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    clauses, params = [], {}
    for i, (col, val) in enumerate(key.items()):
        if val is None:
            clauses.append(f"{_qi(col)} IS NULL")
        elif isinstance(val, list):
            # text comparison so a list of ids matches text, integer and uuid keys alike (a selection's keys are
            # strings once they have been through the browser); an empty list matches nothing
            clauses.append(f"{_qi(col)}::text = ANY(%(k{i})s::text[])")
            params[f"k{i}"] = [None if v is None else str(v) for v in val]
        else:
            clauses.append(f"{_qi(col)} = %(k{i})s")
            params[f"k{i}"] = val
    return " AND ".join(clauses) or "FALSE", params


def default_key(cell_id: str, key: dict[str, Any], set_: dict[str, Any]) -> str:
    raw = json.dumps({"cell": cell_id, "key": key, "set": set_}, sort_keys=True, default=str)
    return "chg_" + hashlib.sha256(raw.encode()).hexdigest()[:40]


async def run(ctx: Any, cell: Any, inputs: dict[str, Any]) -> CellOutcome:
    from ... import external_db as xdb

    t0 = time.perf_counter()
    try:
        conn, url = await connection_url(ctx, inputs.get("connection"))
    except Fail as f:
        return f.outcome
    if conn.readonly:
        return CellOutcome.fail("change_failed", f"connection '{conn.key}' is read-only", detail_code="connection_readonly")
    key, set_ = inputs.get("key") or {}, inputs.get("set") or {}
    try:
        table = _qi(inputs.get("table") or "")
        for c in set_:
            _qi(c)
        where, params = _where(key)
    except ValueError as e:
        return CellOutcome.fail("change_failed", str(e))
    try:
        res = await xdb.run_readonly(url, f"SELECT * FROM {table} WHERE {where}", params, ROW_CAP + 1,
                                     max_limit=ROW_CAP + 1)
    except xdb.ExternalDbError as e:
        return CellOutcome(status="error", error={"code": "change_failed", "message": e.message}, ms=ms_since(t0))
    before = res["rows"]
    if len(before) > ROW_CAP:
        return CellOutcome.fail("row_cap_exceeded", f"this change matches more than {ROW_CAP} rows; narrow the key",
                                cap=ROW_CAP)
    diff = [{"key": {k: r.get(k) for k in key}, "changes": [
        {"column": c, "before": r.get(c), "after": v} for c, v in set_.items() if str(r.get(c)) != str(v)]}
        for r in before]
    idem = inputs.get("idempotency_key") or default_key(cell.id, key, set_)
    out = {"connection": conn.key, "connection_id": conn.id, "table": inputs.get("table"), "key": key, "set": set_,
           "reason": inputs.get("reason") or "", "columns": res["columns"], "rows": before[:50], "before": before[:50],
           "diff": diff, "row_count": len(before), "idempotency_key": idem}
    if not before:
        out["summary"] = "nothing to change (no rows match the key)"
        return CellOutcome(output=out, ms=ms_since(t0))
    out["summary"] = f"{len(before)} row{'s' if len(before) != 1 else ''} will change"
    return CellOutcome(status="needs_approval", output=out, ms=ms_since(t0))


async def apply_change(url: str, table: str, key: dict[str, Any], set_: dict[str, Any]) -> list[dict[str, Any]]:
    """Run the UPDATE in one transaction; returns the after-image. Raises ``ExternalDbError``/``ValueError``."""
    import psycopg

    from ... import external_db as xdb

    where, params = _where(key)
    sets = []
    for i, (c, v) in enumerate(set_.items()):
        sets.append(f"{_qi(c)} = %(s{i})s")
        params[f"s{i}"] = v
    stmt = f"UPDATE {_qi(table)} SET {', '.join(sets)} WHERE {where} RETURNING *"
    conn = await xdb._connect(url)
    try:
        try:
            async with conn.transaction():
                async with conn.cursor() as cur:
                    await cur.execute(stmt, params)
                    rows = await cur.fetchall()
                    cols = [d.name for d in cur.description] if cur.description else []
                    if len(rows) > ROW_CAP:
                        raise xdb.ExternalDbError("row_cap_exceeded", f"more than {ROW_CAP} rows; rolled back", 422)
        except psycopg.Error as e:
            raise xdb.ExternalDbError("change_failed", xdb._scrub(str(e), url), 400) from None
    finally:
        await conn.close()
    return [{c: xdb.serialize_value(r[i]) for i, c in enumerate(cols)} for r in rows]
