"""Carry-over and staleness (labeling plan §5.1, §5.2): the layer's resolved labels judged against any version.

    status(db, layer_id, version_id=None, policy=None, rows=False) -> {version_id, columns: {col: counts}, totals, rows?}
    judge(salt, resolved_rows, version_rows, spec_cols, stale_on) -> per-row status

Per resolved (row, column): ok · stale (context changed) · base_changed (the labelled cell's base value changed) ·
conflict (a correction whose base changed) · orphaned (row absent) · absorbed (the version already holds the label).
Plus `unlabelled`: rows of the version with no resolved label."""

from __future__ import annotations

import json
from typing import Any

from . import rows as ROWS
from . import rowhash as RH
from .resolve import resolve
from .store import _layer_full, current_schema, default_version, schema_columns
from .values import LabelError, canon_cell, typed_equal

STATUSES = ("ok", "absorbed", "stale", "base_changed", "conflict", "orphaned")


def judge(salt: str, r: dict[str, Any], row: dict[str, Any] | None, spec_col: dict[str, Any] | None, stale_on: str = "ctx") -> tuple[str, str | None]:
    """(status, reason) of one resolved row against the version's row (None: absent)."""
    if row is None:
        return "orphaned", None
    col = r["column"]
    stale = _stale(salt, r, row, col, stale_on)
    # Typed comparison (LB-FIX M1): "no" on a BOOLEAN column is absorbed by false.
    absorbed = r["status"] == "applied" and (
        (not r["is_null"] and typed_equal(r["value"], row.get(col))) or (bool(r["is_null"]) and row.get(col) is None))
    if absorbed:
        # Stale takes precedence (LB-FIX m3): a label whose context changed is stale even when the version holds it.
        return ("stale", stale) if stale else ("absorbed", None)
    if r.get("base_value_hash") and col in row and RH.cell_hash(salt, row.get(col)) != r["base_value_hash"]:
        return ("conflict" if (spec_col or {}).get("role") == "feature" else "base_changed"), "base_value_changed"
    if stale:
        return "stale", stale
    return "ok", None


def _stale(salt: str, r: dict[str, Any], row: dict[str, Any], col: str, stale_on: str) -> str | None:
    if stale_on == "all":
        others = [c for c in row if c not in (col, "__row_id")]
        if r.get("row_hash_all") and RH.row_hash(salt, others, row) != r["row_hash_all"]:
            return "row_changed"
        return None
    ctx = json.loads(r["ctx_cols"]) if r.get("ctx_cols") else None
    if ctx is None:
        ctx = [c for c in row if c not in (col, "__row_id")]
    if any(c not in row for c in ctx):
        return "context_changed"
    if r.get("row_hash_ctx") and RH.row_hash(salt, ctx, row) != r["row_hash_ctx"]:
        return "context_changed"
    return None


def judged(db: Any, layer_id: str, version_id: str, res: dict[str, Any], stale_on: str = "ctx") -> list[dict[str, Any]]:
    """The resolved rows with `carry` (status vs the version) and `carry_reason`."""
    with db.connect() as con:
        lay = _layer_full(con, layer_id)
        vi = ROWS.version_info(con, version_id)
        sch = current_schema(con, layer_id)
    if vi["root"] != lay["root_id"]:
        raise LabelError(400, "version_other_root", f"{version_id} is not a version of this layer's dataset.")
    if vi["storage"] != "parquet":
        raise LabelError(409, "labels_need_lake_version", f"{version_id} is a CSV-era version; labels need a lake version.")
    spec = schema_columns(sch)
    vrows = ROWS.read(version_id, lay["table_name"], {r["row_id"] for r in res["rows"]})
    out = []
    for r in res["rows"]:
        st, why = judge(lay["salt"], r, vrows.get(r["row_id"]), spec.get(r["column"]), stale_on)
        out.append({**r, "carry": st, "carry_reason": why})
    return out


def status(db: Any, layer_id: str, version_id: str | None = None, policy: dict[str, Any] | None = None,
           with_rows: bool = False) -> dict[str, Any]:
    with db.connect() as con:
        lay = _layer_full(con, layer_id)
        vid = version_id or default_version(con, lay["root_id"])
    res = resolve(db, layer_id, policy)
    rows = judged(db, layer_id, vid, res, res["policy"]["stale_on"])
    cols: dict[str, dict[str, int]] = {}
    labelled: set[int] = set()
    for r in rows:
        c = cols.setdefault(r["column"], {**{s: 0 for s in STATUSES}, "disputed": 0, "prelabel_only": 0, "schema_dropped": 0})
        if r["status"] != "applied":
            c[r["status"]] += 1
        else:
            c[r["carry"]] += 1
        if r["carry"] != "orphaned":
            labelled.add(r["row_id"])
    n_rows = len(ROWS.all_ids(vid, lay["table_name"]))
    totals = {s: sum(c[s] for c in cols.values()) for s in (*STATUSES, "disputed", "prelabel_only", "schema_dropped")}
    totals["live"] = totals["ok"] + totals["absorbed"]
    totals["unlabelled"] = n_rows - len(labelled)
    out = {"layer_id": layer_id, "version_id": vid, "through_seq": res["through_seq"], "rows_in_version": n_rows,
           "columns": cols, "totals": totals}
    if with_rows:
        out["rows"] = [{k: r[k] for k in ("row_id", "column", "value", "status", "method", "carry", "carry_reason", "last_seq")}
                       for r in rows]
    return out
