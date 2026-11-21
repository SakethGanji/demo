"""P08 (plan §5.4, R6): the two-pass diff of two version tables, in DuckDB, cached in `diff_cache`.

1. Tables by name: equal `table_sha256` → unchanged (no scan); a removed and an added table with the same sha → a rename.
2. The primary tables are compared:
   - **same root** → the key is `__row_id`;
   - **a confirmed business key** (`key=col[,col]`, cross-root or CSV vs Parquet) → the key columns, which must be
     unique and non-null on both sides (else 400 `key_invalid` with the first 10 offending values);
   - otherwise a **multiset row-hash diff**: added / removed only, labelled "no key: changes appear as remove + add".
3. **Pass 1** joins one hash per row (DuckDB `hash()` over the shared columns; ephemeral, never stored) and keeps the
   added / removed / changed keys in a temp table. **Pass 2** counts, per column, the cells that differ, only over the
   changed rows. Columns whose type changed are compared too (same family as that family, else as text) and are
   listed in `schema.dtype_changes` / `type_changes` (P07's note: the pandas diff skipped them).
4. Samples: up to 50 added, removed and changed rows by keyset, plus 5 changed rows cell by cell.
5. The result is cached in `diff_cache(a_sha, b_sha, key)`. Column statistics (missing, mean, median, top values,
   PSI) come from `table_stats` at read time, so a cached diff fills them in once the profile job has run.
"""

from __future__ import annotations

import bisect
import json
import math
import time
from datetime import datetime, timezone
from typing import Any

import numpy as np

from .. import frames
from . import duck, stats
from .pages import ROW_ID, kind, q

SAMPLE = 50
EXAMPLES = 5
DIFF_VERSION = 2


class DiffError(ValueError):
    def __init__(self, status: int, code: str, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.status, self.code, self.message, self.extra = status, code, message, extra

    def payload(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, **self.extra}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _lit(s: str) -> str:
    return "'" + str(s).replace("'", "''") + "'"


# -- the two sides ---------------------------------------------------------------------------------------------------

class Side:
    """One version's primary table as a DuckDB view `_a` / `_b`, with `__row_id` (CSV-era: lineage root rows, else the
    row position for a root, else None = no ids)."""

    def __init__(self, db, dstore, vid: str, table: str | None = None) -> None:
        with db.connect() as con:
            r = con.execute("SELECT id, root_id, storage, name, target, sha256, primary_table, rows, state FROM datasets WHERE id = ?",
                            (vid,)).fetchone()
            if not r:
                raise DiffError(404, "not_found", f"No dataset with id '{vid}'.")
            self.rec = dict(r)
            if self.rec.get("state") == "deleted":
                raise DiffError(410, "deleted", f"{vid} was deleted: its rows are gone (the tombstone and lineage are kept).")
            self.storage = self.rec["storage"] or "csv"
            if self.storage == "parquet":
                self.tables = [dict(t) for t in con.execute(
                    "SELECT table_name AS name, table_sha256 AS sha, n_rows, n_cols FROM version_tables WHERE version_id = ?"
                    " ORDER BY ordinal", (vid,))]
        self.vid = vid
        self.root = self.rec["root_id"] or vid
        self.arrow = None
        self.has_ids = True
        if self.storage == "parquet":
            lt = frames.lake_table(frames.Table(vid, table) if table else frames.table(vid))
            self.table, self.identity, self.n_rows = lt.name, lt.table_sha256, lt.n_rows
            self.columns = [(c["name"], c["type"]) for c in lt.schema]
            self.rel = frames.lake_sql(lt)
        else:
            import pyarrow as pa

            from .pages import _arrow_type

            self.identity = frames.data_identity(vid)
            df = frames.read_version(vid).reset_index(drop=True)
            df.columns = [str(c) for c in df.columns]
            lin = (dstore.lineage(vid) if dstore is not None else None) or {}
            rr = lin.get("root_rows")
            if rr and len(rr) == len(df):
                ids = np.asarray(rr, dtype="int64")
            elif self.root == vid:
                ids = np.arange(len(df), dtype="int64")
            else:
                ids, self.has_ids = np.arange(len(df), dtype="int64"), False
            df.insert(0, ROW_ID, ids)
            self.arrow = pa.Table.from_pandas(df, preserve_index=False)
            self.table, self.n_rows = frames.PRIMARY_TABLE, len(df)
            self.columns = [(f.name, _arrow_type(f.type)) for f in self.arrow.schema if f.name != ROW_ID]
            self.rel = None
            self.tables = [{"name": self.table, "sha": self.identity, "n_rows": self.n_rows, "n_cols": len(self.columns)}]

    def bind(self, con, name: str, rename: dict[str, str] | None = None) -> None:
        """The view `name` over this side; `rename` {name here: name to show} (an edit's renamed columns)."""
        src = self.rel
        if self.arrow is not None:
            con.register(f"{name}_arrow", self.arrow)
            src = f"{name}_arrow"
        cols = ", ".join(f"{q(c)} AS {q((rename or {}).get(c, c))}" for c, _ in self.columns)
        con.execute(f"CREATE OR REPLACE TEMP VIEW {name} AS SELECT {q(ROW_ID)}, {cols} FROM {src}")

    def type_map(self, rename: dict[str, str] | None = None) -> dict[str, str]:
        return {(rename or {}).get(c, c): t for c, t in self.columns}


def _family(t: str) -> str:
    from ..dataset_versions import type_family

    return type_family(t)


def _pair(col: str, ta: str, tb: str) -> tuple[str, str, str]:
    """(a expr, b expr, how) comparing one shared column; a type change across families compares as text."""
    from ..dataset_versions import _pair as P

    p = P(col, ta, tb)
    x = q(col)
    if p:
        ea, eb = p
        how = "same type" if ta == tb else f"as {_family(ta)}"
        return ea.replace(x, f"a.{x}"), eb.replace(x, f"b.{x}"), how
    fa, fb = _family(ta), _family(tb)
    a_, b_ = f"a.{x}", f"b.{x}"

    def as_bool(e: str, f: str) -> str:
        if f == "bool":
            return e
        if f == "number":
            return f"(CAST({e} AS DOUBLE) <> 0)"
        return (f"(CASE WHEN lower(trim(CAST({e} AS VARCHAR))) IN ('true', 'yes', 'y', 't', '1') THEN true"
                f" WHEN lower(trim(CAST({e} AS VARCHAR))) IN ('false', 'no', 'n', 'f', '0') THEN false END)")

    if "bool" in (fa, fb) and {fa, fb} <= {"bool", "text", "number"}:
        return as_bool(a_, fa), as_bool(b_, fb), "as yes / no (the type family changed)"
    if {fa, fb} == {"number", "text"}:
        ea = f"CAST({a_} AS DOUBLE)" if fa == "number" else f"TRY_CAST({a_} AS DOUBLE)"
        eb = f"CAST({b_} AS DOUBLE)" if fb == "number" else f"TRY_CAST({b_} AS DOUBLE)"
        return ea, eb, "as numbers (text read as numbers; the type family changed)"
    return f"CAST({a_} AS VARCHAR)", f"CAST({b_} AS VARCHAR)", "as text (the type family changed)"


def _schema(ta: dict[str, str], tb: dict[str, str], renamed: dict[str, str]) -> dict[str, Any]:
    added = [c for c in tb if c not in ta]
    removed = [c for c in ta if c not in tb]
    type_changes, dtype_changes = [], []
    for c, t in tb.items():
        if c in ta and ta[c] != t:
            dtype_changes.append({"column": c, "from": ta[c], "to": t})
            if _family(ta[c]) != _family(t):
                type_changes.append({"column": c, "from": ta[c], "to": t, "kind_from": _family(ta[c]), "kind_to": _family(t)})
    out = {"columns_added": added, "columns_removed": removed, "type_changes": type_changes, "dtype_changes": dtype_changes,
           "n_columns": [len(ta), len(tb)]}
    if renamed:
        out["renamed"] = [{"from": k, "to": v} for k, v in renamed.items()]
    return out


def _tables(a: Side, b: Side) -> list[dict[str, Any]]:
    ta, tb = {t["name"]: t for t in a.tables}, {t["name"]: t for t in b.tables}
    out = []
    for n, t in ta.items():
        if n in tb:
            out.append({"table": n, "status": "unchanged" if t["sha"] == tb[n]["sha"] else "changed", "a_sha256": t["sha"],
                        "b_sha256": tb[n]["sha"], "rows": [t["n_rows"], tb[n]["n_rows"]]})
    gone = [n for n in ta if n not in tb]
    new = [n for n in tb if n not in ta]
    for n in gone:
        same = next((m for m in new if tb[m]["sha"] == ta[n]["sha"]), None)
        if same:
            new.remove(same)
            out.append({"table": n, "status": "renamed", "to": same, "a_sha256": ta[n]["sha"], "b_sha256": tb[same]["sha"],
                        "rows": [ta[n]["n_rows"], tb[same]["n_rows"]]})
        else:
            out.append({"table": n, "status": "removed", "a_sha256": ta[n]["sha"], "rows": [ta[n]["n_rows"], None]})
    for n in new:
        out.append({"table": n, "status": "added", "b_sha256": tb[n]["sha"], "rows": [None, tb[n]["n_rows"]]})
    return out


def _cell(v: Any) -> Any:
    from ..dataset_edit import _jsonable

    return _jsonable(v)


# -- the diff --------------------------------------------------------------------------------------------------------

def compute(db, dstore, a_id: str, b_id: str, *, key: list[str] | None = None, renamed: dict[str, str] | None = None,
            target: str | None = None, use_cache: bool = True) -> dict[str, Any]:
    t0 = time.perf_counter()
    A, B = Side(db, dstore, a_id), Side(db, dstore, b_id)
    renamed = dict(renamed or {})
    back = {v: k for k, v in renamed.items()}  # b's new name → a's old name
    ta, tb = A.type_map(), B.type_map(back)
    if key:
        miss = [c for c in key if c not in ta or c not in tb]
        if miss:
            raise DiffError(400, "key_invalid", f"The key column {miss[0]!r} is not in both versions.", columns=key)
        method = "key"
    elif A.root == B.root and A.has_ids and B.has_ids:
        method = "row_id"
    else:
        method = "row_hash"
    ckey = {"row_id": ROW_ID, "key": "key:" + json.dumps(key), "row_hash": "row_hash"}[method]
    if renamed:
        ckey += "|renamed:" + json.dumps(sorted(renamed.items()))
    sd = _schema(ta, tb, renamed)
    shared = [c for c in ta if c in tb]
    base = {"a_table": A.table, "b_table": B.table, "tables": _tables(A, B), "schema": sd, "method": method}
    if A.identity == B.identity and not renamed:
        rows = {"a": A.n_rows, "b": B.n_rows, "matched": B.n_rows, "unchanged": B.n_rows, "changed": 0, "added": 0, "removed": 0,
                "matched_by": "identical tables (the same table sha256): no rows were read", "changed_examples": [],
                "changed_by_column": {}, "compared_columns": len(shared), "note": "the two versions hold the same table"}
        return {**base, "rows": rows, "samples": {"added": [], "removed": [], "changed": []}, "cached": False, "identical": True,
                "seconds": round(time.perf_counter() - t0, 3), "_A": A, "_B": B}
    if use_cache:
        with db.connect() as con:
            r = con.execute("SELECT result, computed_at FROM diff_cache WHERE a_sha256 = ? AND b_sha256 = ? AND key = ?",
                            (A.identity, B.identity, ckey)).fetchone()
        if r:
            out = json.loads(r[0])
            if out.get("diff_version") == DIFF_VERSION:
                out.update(cached=True, cached_at=r[1], seconds=round(time.perf_counter() - t0, 3), _A=A, _B=B)
                return out
    with duck.job(threads=max(2, (__import__("os").cpu_count() or 2))) as con:
        A.bind(con, "_a")
        B.bind(con, "_b", back)
        pairs = {c: _pair(c, ta[c], tb[c]) for c in shared}
        cmp_cols = [c for c in shared if c not in (key or [])]
        ha = f"hash({', '.join(pairs[c][0] for c in cmp_cols)})" if cmp_cols else "0"
        hb = f"hash({', '.join(pairs[c][1] for c in cmp_cols)})" if cmp_cols else "0"
        if method == "row_hash":
            rows, samples = _multiset(con, A, B, ha, hb, len(shared))
            out = {**base, "rows": rows, "samples": samples}
        else:
            if method == "key":
                _check_key(con, key, pairs)
                ka = [pairs[c][0] for c in key]
                kb = [pairs[c][1] for c in key]
                on = " AND ".join(f"x.k{i} = y.k{i}" for i in range(len(key)))
                sel_a = ", ".join(f"{e} AS k{i}" for i, e in enumerate(ka))
                sel_b = ", ".join(f"{e} AS k{i}" for i, e in enumerate(kb))
                how = f"the confirmed key {', '.join(key)}"
            else:
                on, sel_a, sel_b = "x.k0 = y.k0", f"a.{q(ROW_ID)} AS k0", f"b.{q(ROW_ID)} AS k0"
                how = "__row_id (the same rows of one dataset)"
            # pass 1: one hash per row, joined on the key; keep only the rows that differ
            con.execute(f"""CREATE TEMP TABLE _k AS
                WITH x AS (SELECT {sel_a}, a.{q(ROW_ID)} AS ra, {ha} AS h FROM _a a),
                     y AS (SELECT {sel_b}, b.{q(ROW_ID)} AS rb, {hb} AS h FROM _b b)
                SELECT x.ra, y.rb, x.ra IS NULL AS added, y.rb IS NULL AS removed FROM x FULL JOIN y ON {on}
                WHERE x.ra IS NULL OR y.rb IS NULL OR x.h <> y.h""")
            n_add, n_rem, n_chg = con.execute("SELECT count(*) FILTER (WHERE added), count(*) FILTER (WHERE removed),"
                                              " count(*) FILTER (WHERE NOT added AND NOT removed) FROM _k").fetchone()
            # pass 2: per column, the cells that differ, over the changed rows only
            by_col: dict[str, int] = {}
            if n_chg and cmp_cols:
                exprs = [f"count(*) FILTER (WHERE {pairs[c][0]} IS DISTINCT FROM {pairs[c][1]})" for c in cmp_cols]
                r = con.execute(f"SELECT {', '.join(exprs)} FROM (SELECT ra, rb FROM _k WHERE NOT added AND NOT removed) k"
                                f" JOIN _a a ON a.{q(ROW_ID)} = k.ra JOIN _b b ON b.{q(ROW_ID)} = k.rb").fetchone()
                by_col = {c: int(v) for c, v in zip(cmp_cols, r) if v}
            examples = _examples(con, cmp_cols, pairs, key) if n_chg else []
            samples = {
                "added": _sample(con, "_b", "added", "rb"), "removed": _sample(con, "_a", "removed", "ra"),
                "changed": [int(x[0]) for x in con.execute(
                    f"SELECT rb FROM _k WHERE NOT added AND NOT removed ORDER BY rb LIMIT {SAMPLE}").fetchall()]}
            matched = B.n_rows - int(n_add)
            note = "every shared column is compared; a column whose type changed is compared as its type family (else as text)"
            rows = {"a": A.n_rows, "b": B.n_rows, "matched": matched, "unchanged": matched - int(n_chg), "changed": int(n_chg),
                    "added": int(n_add), "removed": int(n_rem), "matched_by": how, "changed_examples": examples,
                    "changed_by_column": by_col, "compared_columns": len(cmp_cols), "note": note}
            out = {**base, "rows": rows, "samples": samples}
        out["compare"] = {c: pairs[c][2] for c in shared if pairs[c][2] != "same type"}
        # the DL1 contract checks, in SQL (P06's Matcher): target gone, type changed, new target category, rows
        # dropped, column gone, "renamed?" hints, target missing more often
        from ..dataset_versions import Matcher

        m = Matcher(con, "SELECT * FROM _a", f"SELECT * EXCLUDE ({q(ROW_ID)}), {q(ROW_ID)} AS __fr FROM _b")
        out["checks"] = m.contract(target)["warnings"]
    out.update(diff_version=DIFF_VERSION, key=ckey, a_sha256=A.identity, b_sha256=B.identity, identical=False,
               computed_seconds=round(time.perf_counter() - t0, 3))
    with db.connect() as con:
        con.execute("INSERT OR REPLACE INTO diff_cache (a_sha256, b_sha256, key, computed_at, result) VALUES (?, ?, ?, ?, ?)",
                    (A.identity, B.identity, ckey, _now(), json.dumps(out, default=str)))
    out.update(cached=False, seconds=round(time.perf_counter() - t0, 3), _A=A, _B=B)
    return out


def _check_key(con, key: list[str], pairs: dict[str, tuple[str, str, str]]) -> None:
    for side, alias, i in (("_a", "a", 0), ("_b", "b", 1)):
        ks = [pairs[c][i] for c in key]
        nn = " OR ".join(f"{e} IS NULL" for e in ks)
        n_null = con.execute(f"SELECT count(*) FROM {side} {alias} WHERE {nn}").fetchone()[0]
        dups = con.execute(f"SELECT {', '.join(ks)}, count(*) FROM {side} {alias} GROUP BY ALL HAVING count(*) > 1 ORDER BY 1 LIMIT 10").fetchall()
        if n_null or dups:
            raise DiffError(400, "key_invalid", f"The key {', '.join(key)} is not unique and non-null in version "
                                                f"{'a' if i == 0 else 'b'}: {n_null:,} missing, {len(dups)}{'+' if len(dups) == 10 else ''} repeated.",
                            side="a" if i == 0 else "b", n_missing=int(n_null),
                            repeated=[{"key": [_cell(v) for v in d[:-1]], "n": int(d[-1])} for d in dups])


def _examples(con, cols: list[str], pairs: dict[str, tuple[str, str, str]], key: list[str] | None) -> list[dict[str, Any]]:
    if not cols:
        return []
    sel = ", ".join(f"{pairs[c][0]} IS DISTINCT FROM {pairs[c][1]} AS d{i}, a.{q(c)} AS a{i}, b.{q(c)} AS b{i}" for i, c in enumerate(cols))
    ksel = ", " + ", ".join(f"a.{q(c)} AS key{i}" for i, c in enumerate(key)) if key else ""
    at = con.execute(f"SELECT k.ra, k.rb{ksel}, {sel} FROM (SELECT ra, rb FROM _k WHERE NOT added AND NOT removed ORDER BY rb LIMIT {EXAMPLES}) k"
                     f" JOIN _a a ON a.{q(ROW_ID)} = k.ra JOIN _b b ON b.{q(ROW_ID)} = k.rb ORDER BY k.rb").to_arrow_table().to_pylist()
    out = []
    for r in at:
        cells = [{"column": c, "a": _cell(r[f"a{i}"]), "b": _cell(r[f"b{i}"])} for i, c in enumerate(cols) if r[f"d{i}"]]
        e = {"row_a": int(r["ra"]), "row_b": int(r["rb"]), "cells": cells[:8]}
        if key:
            e["key"] = _cell(r["key0"]) if len(key) == 1 else [_cell(r[f"key{i}"]) for i in range(len(key))]
        out.append(e)
    return out


def _sample(con, view: str, flag: str, col: str) -> list[dict[str, Any]]:
    at = con.execute(f"SELECT v.* FROM (SELECT {col} FROM _k WHERE {flag} ORDER BY {col} LIMIT {SAMPLE}) k"
                     f" JOIN {view} v ON v.{q(ROW_ID)} = k.{col} ORDER BY v.{q(ROW_ID)}").to_arrow_table().to_pylist()
    return [{("_row_id" if k == ROW_ID else k): _cell(v) for k, v in r.items()} for r in at]


def _multiset(con, A: Side, B: Side, ha: str, hb: str, n_shared: int) -> tuple[dict[str, Any], dict[str, Any]]:
    if not n_shared:
        rows = {"a": A.n_rows, "b": B.n_rows, "matched": 0, "unchanged": 0, "changed": 0, "added": B.n_rows, "removed": A.n_rows,
                "matched_by": "no rows matched: the two versions share no column", "changed_examples": [], "changed_by_column": {},
                "compared_columns": 0, "note": "no key: changes appear as remove + add"}
        return rows, {"added": [], "removed": [], "changed": []}
    con.execute(f"CREATE TEMP TABLE _ha AS SELECT {ha} AS h, count(*) AS n FROM _a a GROUP BY 1")
    con.execute(f"CREATE TEMP TABLE _hb AS SELECT {hb} AS h, count(*) AS n FROM _b b GROUP BY 1")
    same, rem, add = con.execute("SELECT coalesce(sum(least(coalesce(x.n, 0), coalesce(y.n, 0))), 0),"
                                 " coalesce(sum(greatest(coalesce(x.n, 0) - coalesce(y.n, 0), 0)), 0),"
                                 " coalesce(sum(greatest(coalesce(y.n, 0) - coalesce(x.n, 0), 0)), 0)"
                                 " FROM _ha x FULL JOIN _hb y ON x.h = y.h").fetchone()

    def side_sample(view: str, alias: str, h: str, other: str) -> list[dict[str, Any]]:
        at = con.execute(f"SELECT {alias}.* FROM {view} {alias} WHERE {h} NOT IN (SELECT h FROM {other}) ORDER BY {alias}.{q(ROW_ID)}"
                         f" LIMIT {SAMPLE}").to_arrow_table().to_pylist()
        return [{("_row_id" if k == ROW_ID else k): _cell(v) for k, v in r.items()} for r in at]

    rows = {"a": A.n_rows, "b": B.n_rows, "matched": int(same), "unchanged": int(same), "changed": 0, "added": int(add),
            "removed": int(rem), "matched_by": f"exact row content over {n_shared} shared columns (no key)",
            "changed_examples": [], "changed_by_column": {}, "compared_columns": n_shared,
            "note": "no key: changes appear as remove + add"}
    return rows, {"added": side_sample("_b", "b", hb, "_ha"), "removed": side_sample("_a", "a", ha, "_hb"), "changed": []}


# -- column statistics from table_stats -------------------------------------------------------------------------------

def _cdf(qs: list[float], x: float) -> float:
    """The share of values ≤ x, from 21 evenly spaced quantiles (linear between them)."""
    ps = [i / 20 for i in range(21)]
    if x < qs[0]:
        return 0.0
    if x >= qs[-1]:
        return 1.0
    i = bisect.bisect_right(qs, x) - 1
    lo, hi = qs[i], qs[i + 1]
    return ps[i] + (ps[i + 1] - ps[i]) * ((x - lo) / (hi - lo) if hi > lo else 1.0)


def _psi(e: list[float], a: list[float]) -> float:
    eps = 1e-4
    return float(sum((max(y, eps) - max(x, eps)) * math.log(max(y, eps) / max(x, eps)) for x, y in zip(e, a)))


def _band(p: float) -> str:
    from ..registry.drift import band

    return band(p)


def column_stats(sa: dict[str, Any] | None, sb: dict[str, Any] | None, ta: dict[str, str], tb: dict[str, str],
                 renamed: dict[str, str]) -> list[dict[str, Any]]:
    """The diff's per-column table (the DL1 shape) from the two tables' stats; PSI from their quantiles (numbers) or
    top-20 values (categories)."""
    back = {v: k for k, v in renamed.items()}
    ca = {c["name"]: c for c in (sa or {}).get("columns") or []}
    cb = {back.get(c["name"], c["name"]): c for c in (sb or {}).get("columns") or []}
    out = []
    for c in [*ta, *[c for c in tb if c not in ta]]:
        x, y = ca.get(c), cb.get(c)
        ina, inb = c in ta, c in tb
        ka = kind(ta[c]) if ina else None
        kb = kind(tb[c]) if inb else None
        kk = lambda k: "number" if k in ("integer", "number") else ("category" if k in ("text", "bool", "date") else k)  # noqa: E731
        row: dict[str, Any] = {"column": c, "kind": kk(ka or kb), "in": "both" if ina and inb else ("a" if ina else "b"),
                               "missing_a": x.get("missing") if x else None, "missing_b": y.get("missing") if y else None}
        if ina and inb and kk(ka) != kk(kb):
            row["kind"] = f"{kk(ka)} → {kk(kb)}"
        for side, s, k in (("a", x, ka), ("b", y, kb)):
            if not s:
                continue
            if k in ("integer", "number"):
                row.update({f"mean_{side}": s.get("mean"), f"median_{side}": s.get("median")})
            else:
                nn = max(1, s["n"] - s["nulls"])
                row.update({f"top_{side}": [{"value": t["value"], "share": round(t["n"] / nn, 4)} for t in (s.get("top") or [])[:3]],
                            f"distinct_{side}": s.get("distinct")})
        if ina and inb and kk(ka) != kk(kb):
            row.update(psi=None, band=None, note="the type changed: the two sides can't share bins")
        elif x and y:
            try:
                if kk(ka) == "number" and len(x.get("quantiles") or []) == 21 and len(y.get("quantiles") or []) == 21:
                    qa, qb = [float(v) for v in x["quantiles"]], [float(v) for v in y["quantiles"]]
                    edges = sorted(set(qa[2:19:2]))
                    fa = [0.0, *[_cdf(qa, e) for e in edges], 1.0]
                    fb = [0.0, *[_cdf(qb, e) for e in edges], 1.0]
                    p = _psi([fa[i + 1] - fa[i] for i in range(len(fa) - 1)], [fb[i + 1] - fb[i] for i in range(len(fb) - 1)])
                    row.update(psi=round(p, 6), band=_band(p), psi_basis="deciles of a, from both tables' quantiles")
                elif x.get("top") is not None and y.get("top") is not None:
                    na, nb = max(1, x["n"]), max(1, y["n"])
                    labels = [t["value"] for t in x["top"]]
                    ma, mb = {t["value"]: t["n"] for t in x["top"]}, {t["value"]: t["n"] for t in y["top"]}
                    ea = [ma[v] / na for v in labels]
                    eb = [mb.get(v, 0) / nb if (y.get("top_complete") or v in mb) else 0.0 for v in labels]
                    ea += [x["nulls"] / na, max(0.0, 1 - sum(ea) - x["nulls"] / na)]
                    eb += [y["nulls"] / nb, max(0.0, 1 - sum(eb) - y["nulls"] / nb)]
                    p = _psi(ea, eb)
                    row.update(psi=round(p, 6), band=_band(p), psi_basis="top 20 values of a, missing and other")
                    if (x.get("distinct") or 0) > 0.95 * max(1, x["n"] - x["nulls"]):
                        row["note"] = "nearly every value is distinct (an id?): PSI means little here"
                else:
                    row.update(psi=None, band=None)
            except (TypeError, ValueError, ZeroDivisionError):
                row.update(psi=None, band=None)
        else:
            row.update(psi=None, band=None)
        out.append(row)
    return sorted(out, key=lambda r: -(r.get("psi") or 0))


def with_columns(out: dict[str, Any], *, wait_s: float = 0.0) -> dict[str, Any]:
    """Add `columns` (from table_stats; scheduled when missing) to a diff result."""
    A, B = out.pop("_A"), out.pop("_B")
    sts = []
    for s in (A, B):
        if s.storage == "parquet":
            sts.append(stats.ensure(s.vid, s.table, wait_s=wait_s, n_rows=s.n_rows))
        else:
            sts.append(stats.ensure(s.vid, None, wait_s=wait_s, n_rows=s.n_rows))
    renamed = {r["from"]: r["to"] for r in out["schema"].get("renamed") or []}
    back = {v: k for k, v in renamed.items()}
    out["columns"] = column_stats(sts[0], sts[1], A.type_map(), B.type_map(back), renamed)
    pending = [x for x, s in (("a", sts[0]), ("b", sts[1])) if not s]
    if pending:
        out["columns_pending"] = pending
        out["columns_note"] = "column statistics are being computed in the background (profile job); ask again shortly"
    return out


# -- P20: masking a diff for its reader -------------------------------------------------------------------------------

def _resample(rows: list[dict[str, Any]], vid: str, table: str, m: Any, to_side: dict[str, str]) -> list[dict[str, Any]]:
    """The sample rows with their masked columns re-read through `frames.scan(mask_for=m)` by `__row_id` (a Parquet
    side: its `_row_id` is the table's `__row_id`). `to_side` maps a shown column name to the side's own name."""
    ids = sorted({int(r["_row_id"]) for r in rows if r.get("_row_id") is not None})
    if not ids:
        return rows
    src = frames.scan(vid, table, mask_for=m)
    cols = sorted({to_side.get(k, k) for r in rows for k in r} & set(m.cols))
    if not cols:
        return rows
    at = duck.interactive().query(f"SELECT {q(ROW_ID)}, {', '.join(q(c) for c in cols)} FROM {src.rel}"
                                  f" WHERE {q(ROW_ID)} IN ({', '.join(map(str, ids))})", fetch="arrow", bind=src.bind)
    by = {r[ROW_ID]: r for r in at.to_pylist()}
    out = []
    for r in rows:
        s = by.get(r.get("_row_id"))
        d = {}
        for k, v in r.items():
            c = to_side.get(k, k)
            d[k] = v if c not in m.cols else s[c] if s is not None else m.value(c, v)
        out.append(d)
    return out


def mask_result(out: dict[str, Any], ma: Any, mb: Any) -> dict[str, Any]:
    """A diff result (cached: computed on the stored values, so equal-but-masked cells still count as changes) as shown
    to a reader with masks `ma` (version a) and `mb` (version b): samples of added rows (b) and removed rows (a) are
    re-read through the masked scan; changed cells, keys, and the top values / mean / median of the column table go
    through the same masks in Python."""
    if not (ma and ma.cols) and not (mb and mb.cols):
        return out
    ren = {r["from"]: r["to"] for r in (out.get("schema") or {}).get("renamed") or []}  # a's name → b's name
    base_b = {k: v.split(" → ")[-1] for k, v in ren.items()}

    def side_col(m: Any, c: str, side: str) -> str:
        c = c.split(" → ")[0]
        return base_b.get(c, c) if side == "b" else c

    smp = dict(out.get("samples") or {})
    for key, m, side, vid, table in (("added", mb, "b", out["b"]["id"], out.get("b_table")),
                                     ("removed", ma, "a", out["a"]["id"], out.get("a_table"))):
        rows = smp.get(key) or []
        if not rows or not (m and m.cols):
            continue
        to_side = base_b if side == "b" else {}
        if frames.is_parquet(vid):
            smp[key] = _resample(rows, vid, table, m, to_side)
        else:  # a CSV-era side: its diff _row_id may be a lineage root row, not the position the scan pages by
            smp[key] = [{k: m.value(to_side.get(k, k), v) for k, v in r.items()} for r in rows]
    out["samples"] = smp
    rows = out.get("rows") or {}
    for e in rows.get("changed_examples") or []:
        for c in e.get("cells") or []:
            c["a"] = ma.value(side_col(ma, c["column"], "a"), c["a"]) if ma else c["a"]
            c["b"] = mb.value(side_col(mb, c["column"], "b"), c["b"]) if mb else c["b"]
        if "key" in e and ma and ma.cols:
            ks = [k.strip() for k in str(out.get("key") or "").removeprefix("key:").strip("[]").replace('"', "").split(",") if k.strip()]
            if len(ks) == 1:
                e["key"] = ma.value(ks[0], e["key"])
            elif isinstance(e["key"], list):
                e["key"] = [ma.value(k, v) for k, v in zip(ks, e["key"])]
    for c in out.get("columns") or []:
        name = str(c.get("column") or "").split(" → ")[0]
        for side, m in (("a", ma), ("b", mb)):
            col = side_col(m, name, side) if m else name
            if not (m and col in m.cols):
                continue
            c["masked"] = True
            if c.get(f"top_{side}"):
                c[f"top_{side}"] = [{**t, "value": m.value(col, _num_text(t["value"], c.get("kind")))} for t in c[f"top_{side}"]]
            for f in ("mean", "median"):
                if f"{f}_{side}" in c:
                    c[f"{f}_{side}"] = None
    out["masked_columns"] = {"a": ma.masked if ma else [], "b": mb.masked if mb else []}
    return out


def _num_text(v: Any, k: Any) -> Any:
    if k == "number":
        try:
            f = float(v)
            return int(f) if f.is_integer() else f
        except (TypeError, ValueError):
            return v
    return v


def mask_key_error(payload: Any, m: Any, key: list[str] | None) -> Any:
    """key_invalid's `repeated` keys (values of the key columns) masked."""
    if not (m and m.cols and isinstance(payload, dict) and key):
        return payload
    rep = payload.get("repeated")
    if isinstance(rep, list):
        payload = {**payload, "repeated": [{**r, "key": [m.value(c, v) for c, v in zip(key, r.get("key") or [])]} for r in rep]}
    return payload
