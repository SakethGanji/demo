"""F-X1 C2: Data Doctor fixes as SQL, for a version above `config.doctor_rows()` rows (no full pandas read).

The fixes (data_doctor.TRANSFORMS) compile into a chain of DuckDB views over the parent table, `_d0` (the parent, with
`__row_id`) … `_dN`, the same way the P07 edit compiler does, and `_dN` is written as the new version through the
VersionWriter: kept rows keep their `__row_id` (and so their holdout and fold), the parent's split is carried by join,
the other tables are carried, and an identical result returns the existing version (dedupe). Row numbers in
drop_rows / relabel are positions in the version (in `__row_id` order), as in the pandas path. Each fix's effect
(cells / rows / columns) is counted in DuckDB. drop_near_duplicates is pandas-only and is refused here (400).

Literals are written by `_lit` from values already checked (data_doctor.check_args, lake.values); column names are
quoted. Nothing from the request reaches the SQL text unquoted.
"""

from __future__ import annotations

import math
import re
from typing import Any

import numpy as np

from . import config
from . import data_doctor as dd
from .lake.values import ValueRefused

NUM_TYPES = ("BIGINT", "INTEGER", "SMALLINT", "TINYINT", "HUGEINT", "DOUBLE", "FLOAT", "REAL", "UBIGINT", "UINTEGER",
             "USMALLINT", "UTINYINT")
FLOAT_TYPES = ("DOUBLE", "FLOAT", "REAL")


def q(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def _lit(v: Any) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, (int, np.integer)):
        return str(int(v))
    if isinstance(v, (float, np.floating)):
        f = float(v)
        if not math.isfinite(f):
            raise ValueRefused("value_invalid", f"{v!r} is not a finite number.")
        return f"CAST({f!r} AS DOUBLE)"
    return "'" + str(v).replace("'", "''") + "'"


def _is_num(t: str) -> bool:
    t = (t or "").upper()
    return t in NUM_TYPES or t.startswith("DECIMAL")


class _Chain:
    """The view chain: `cur` is the latest view; `cols` its columns (no __row_id) with DuckDB types, in order."""

    def __init__(self, con: Any, base_sql: str) -> None:
        self.con, self.i = con, 0
        con.execute(f"CREATE OR REPLACE TEMP VIEW _d0 AS SELECT * FROM ({base_sql})")
        self.cur = "_d0"
        self._types()

    def _types(self) -> None:
        self.cols = [(r[0], r[1]) for r in self.con.execute(f"DESCRIBE {self.cur}").fetchall() if r[0] != "__row_id"]

    @property
    def names(self) -> list[str]:
        return [n for n, _ in self.cols]

    def type(self, c: str) -> str:
        return dict(self.cols)[c]

    def one(self, sql: str) -> Any:
        return self.con.execute(sql).fetchall()[0][0]

    def count(self, where: str = "TRUE") -> int:
        """Rows of the current view where `where` holds (columns as s."name" or bare)."""
        return int(self.one(f"SELECT count(*) FROM {self.cur} s WHERE {where}"))

    def select(self, replace: dict[str, str] | None = None, add: dict[str, str] | None = None, drop: set[str] | None = None,
               tail: str = "", src: str | None = None) -> None:
        """A new view: every column kept in order (`replace` swaps its expression), `add` appended (or swapped when the
        name exists, as a pandas assignment would), `drop` left out."""
        replace, add, drop = dict(replace or {}), dict(add or {}), set(drop or ())
        for k in list(add):
            if k in self.names:
                replace[k] = add.pop(k)
        sel = ["s.__row_id"] + [f"{replace[n]} AS {q(n)}" if n in replace else f"s.{q(n)}" for n in self.names if n not in drop]
        sel += [f"{e} AS {q(n)}" for n, e in add.items()]
        self.i += 1
        v = f"_d{self.i}"
        self.con.execute(f"CREATE OR REPLACE TEMP VIEW {v} AS SELECT {', '.join(sel)} FROM {src or self.cur + ' s'} {tail}")
        self.cur = v
        self._types()

    def filter(self, sql_from: str) -> None:
        self.i += 1
        v = f"_d{self.i}"
        self.con.execute(f"CREATE OR REPLACE TEMP VIEW {v} AS {sql_from}")
        self.cur = v
        self._types()


def _match(ch: _Chain, c: str, values: list[Any]) -> str:
    """data_doctor._match in SQL: a number column matches numbers (and numeric strings); else text equality."""
    x = f"s.{q(c)}"
    if _is_num(ch.type(c)):
        nums = [float(v) for v in values if not isinstance(v, bool) and (not isinstance(v, str) or re.fullmatch(r"-?\d+(\.\d+)?", v))]
        nums = [v for v in nums if math.isfinite(v)]
        return f"(CAST({x} AS DOUBLE) IN ({', '.join(_lit(v) for v in nums)}))" if nums else "FALSE"
    return f"({x} IS NOT NULL AND CAST({x} AS VARCHAR) IN ({', '.join(_lit(str(v)) for v in values)}))"


def _validate(ch: _Chain, transforms: list[dict[str, Any]], target: str | None) -> None:
    """data_doctor.validate without a frame: the same refusals, the presence checks counted in DuckDB."""
    for t in transforms:
        op = t.get("op")
        if op not in dd.TRANSFORMS:
            raise ValueError(f"Unknown fix {op!r}; use one of {', '.join(dd.TRANSFORMS)}.")
        for c in ([t["column"]] if "column" in t else []) + list(t.get("columns") or []):
            if c not in ch.names:
                raise ValueError(f"{op}: no column named {c!r}.")
        if op == "rescale":
            try:
                float(t.get("factor"))
            except (TypeError, ValueError):
                raise ValueError("rescale needs a numeric factor.") from None
            if t.get("date_column") not in ch.names or not t.get("from") or "column" not in t:
                raise ValueError("rescale needs column, date_column (a column of the table) and from (a date).")
        if op in ("to_missing", "split_codes", "add_missing_flag", "merge_classes") and "column" not in t:
            raise ValueError(f"{op} needs a column.")
        if op == "relabel" and (not target or len(t.get("rows") or []) != len(t.get("labels") or []) or not t.get("rows")):
            raise ValueError("relabel needs the target and as many labels as rows.")
        if op in ("to_missing", "split_codes", "drop_columns") and target and \
                (t.get("column") == target or target in (t.get("columns") or [])):
            raise ValueError(f"{op} can't change the target column '{target}'.")
        if op in ("to_missing", "merge_classes"):
            key = "values" if op == "to_missing" else "classes"
            vals = t.get(key)
            if not isinstance(vals, list) or not vals:
                raise ValueError(f"{op} needs {key}: a non-empty list.")
            c = t["column"]
            if op == "to_missing" and _is_num(ch.type(c)):
                bad = [v for v in vals if isinstance(v, bool) or not isinstance(v, (int, float, str))
                       or (isinstance(v, str) and not re.fullmatch(r"-?\d+(\.\d+)?", v))]
                if bad:
                    raise ValueError(f"to_missing: {c!r} is numeric; {', '.join(map(repr, bad))} can't be one of its values.")
            if op == "to_missing":
                absent = [] if ch.count(_match(ch, c, vals)) else vals
            else:
                have = {r[0] for r in ch.con.execute(
                    f"SELECT DISTINCT CAST({q(c)} AS VARCHAR) FROM {ch.cur} WHERE CAST({q(c)} AS VARCHAR) IN "
                    f"({', '.join(_lit(str(v)) for v in vals)})").fetchall()}
                absent = [v for v in vals if str(v) not in have]
            if absent:
                raise ValueError(f"{op}: {c!r} has no cell equal to {', '.join(map(repr, absent))}.")


def _check_values(ch: _Chain, transforms: list[dict[str, Any]], target: str | None) -> None:
    """data_doctor.check_values on the column types (lake.values): fill, labels, into, factor."""
    from .lake import values as V

    def vt(c: str) -> str:
        t = ch.type(c).upper()
        return "BIGINT" if t in ("BIGINT", "INTEGER", "SMALLINT", "TINYINT") else "DOUBLE" if _is_num(t) else "VARCHAR"

    for i, t in enumerate(transforms, 1):
        op = t.get("op")
        if op == "split_codes":
            t["fill"] = V.check(t.get("fill", 0), vt(t["column"]), op_index=i, column=t["column"])
        elif op == "relabel" and target in ch.names:
            t["labels"] = [V.check(v, vt(target), op_index=i, column=target) for v in t.get("labels") or []]
        elif op == "merge_classes":
            V.check(t.get("into") or "other", "VARCHAR", op_index=i, column=t.get("column"))
        elif op == "rescale":
            V.check(t.get("factor"), "DOUBLE", op_index=i, column=t.get("column"))


def compile_fixes(con: Any, base_sql: str, ids: np.ndarray, transforms: list[dict[str, Any]],
                  target: str | None) -> tuple[_Chain, list[dict[str, Any]]]:
    """The fixes as views over `base_sql`; returns the chain and each fix's effect (as data_doctor.apply records it)."""
    import pyarrow as pa

    dd.check_args(transforms, len(ids))
    ch = _Chain(con, base_sql)
    _validate(ch, transforms, target)
    _check_values(ch, transforms, target)
    effects: list[dict[str, Any]] = []
    for k, t in enumerate(transforms, 1):
        op = t["op"]
        if op == "to_missing":
            c = t["column"]
            m = _match(ch, c, t.get("values") or [])
            cells = ch.count(m)
            add = {f"{c}_was_missing": f"CAST((s.{q(c)} IS NULL OR {m}) AS BIGINT)"} if t.get("flag", True) else {}
            ch.select(replace={c: f"CASE WHEN {m} THEN NULL ELSE s.{q(c)} END"}, add=add)
            effects.append({**t, "cells": cells})
        elif op == "split_codes":
            c = t["column"]
            m = _match(ch, c, t.get("codes") or [])
            cells = ch.count(m)
            ch.select(replace={c: f"CASE WHEN {m} THEN CAST({_lit(t.get('fill', 0))} AS {ch.type(c)}) ELSE s.{q(c)} END"},
                      add={f"{c}_code": f"CASE WHEN {m} THEN regexp_replace(CAST(s.{q(c)} AS VARCHAR), '\\.0$', '') ELSE 'value' END"})
            effects.append({**t, "cells": cells, "added": f"{c}_code"})
        elif op == "add_missing_flag":
            c = t["column"]
            cells = ch.count(f"{q(c)} IS NULL")
            ch.select(add={f"{c}_was_missing": f"CAST(s.{q(c)} IS NULL AS BIGINT)"})
            effects.append({**t, "cells": cells, "added": f"{c}_was_missing"})
        elif op == "drop_duplicates":
            n0 = ch.count()
            keys = []
            for c, ty in ch.cols:  # data_doctor._row_keys: unique per row, never missing, not a float
                if ty.upper() in FLOAT_TYPES:
                    continue
                nn, nd = ch.con.execute(f"SELECT count({q(c)}), count(DISTINCT {q(c)}) FROM {ch.cur}").fetchall()[0]
                if n0 > 1 and nn == n0 and nd == n0:
                    keys.append(c)
            content = [c for c in ch.names if c not in keys] or ch.names
            ch.filter(f"SELECT * FROM {ch.cur} QUALIFY row_number() OVER (PARTITION BY {', '.join(q(c) for c in content)}"
                      " ORDER BY __row_id) = 1")
            effects.append({**t, "rows": n0 - ch.count()})
        elif op == "drop_near_duplicates":
            raise ValueRefused("too_many_rows", f"drop_near_duplicates checks up to {dd.NEAR_MAX_ROWS:,} rows; this version is "
                                                "larger. Drop exact duplicates, or fix a sample.", op_index=k)
        elif op == "drop_rows":
            n0 = ch.count()
            con.register(f"_drop{k}", pa.table({"rid": pa.array(ids[np.asarray(t["rows"], dtype="int64")], pa.int64())}))
            ch.filter(f"SELECT * FROM {ch.cur} s ANTI JOIN _drop{k} d ON s.__row_id = d.rid")
            effects.append({"op": op, "reason": t.get("reason"), "rows": n0 - ch.count()})
        elif op == "drop_columns":
            cols = [c for c in t.get("columns") or [] if c != target]
            ch.select(drop=set(cols))
            effects.append({**t, "columns": cols})
        elif op == "relabel":
            rid = ids[np.asarray(t["rows"], dtype="int64")]
            tt = ch.type(target)
            labs = [None if v is None else str(v) for v in t["labels"]]
            con.register(f"_rel{k}", pa.table({"rid": pa.array(rid, pa.int64()), "lab": pa.array(labs, pa.string())}))
            new = f"CAST(r.lab AS {tt})" if _is_num(tt) else "r.lab"
            changed = int(ch.one(f"SELECT count(*) FROM {ch.cur} s JOIN _rel{k} r ON s.__row_id = r.rid"
                                 f" WHERE CAST(s.{q(target)} AS VARCHAR) IS DISTINCT FROM CAST({new} AS VARCHAR)"))
            ch.select(replace={target: f"CASE WHEN r.rid IS NOT NULL THEN {new} ELSE s.{q(target)} END"},
                      src=f"{ch.cur} s LEFT JOIN _rel{k} r ON s.__row_id = r.rid")
            effects.append({"op": op, "reason": t.get("reason"), "rows": len(rid), "changed": changed})
        elif op == "rescale":
            c, dc = t["column"], t["date_column"]
            dty = ch.type(dc).upper()
            if _is_num(dty):
                try:
                    cond = f"CAST(s.{q(dc)} AS DOUBLE) >= {_lit(float(t['from']))}"
                except (TypeError, ValueError):
                    raise ValueRefused("value_invalid", f"Fix {k} (rescale): from {t['from']!r} is not a number, and "
                                                        f"'{dc}' holds numbers.", op_index=k) from None
            else:
                import pandas as pd

                try:
                    ts = pd.Timestamp(str(t["from"]))
                except (TypeError, ValueError):
                    raise ValueRefused("value_invalid", f"Fix {k} (rescale): from {t['from']!r} is not a date.", op_index=k) from None
                x = f"s.{q(dc)}" if dty.startswith(("DATE", "TIMESTAMP")) else f"TRY_CAST(s.{q(dc)} AS TIMESTAMP)"
                cond = f"CAST({x} AS TIMESTAMP) >= CAST({_lit(ts.isoformat())} AS TIMESTAMP)"
            m = f"COALESCE({cond}, FALSE)"
            sc = t.get("scale") or {}
            if sc.get("before") and sc.get("after"):  # A2 C2: only rows on the new scale
                xa = f"abs(CAST(s.{q(c)} AS DOUBLE))"
                la, lb = math.log(float(sc["after"])), math.log(float(sc["before"]))
                m = (f"({m} AND isfinite({xa}) AND {xa} > 0 AND abs(ln({xa}) - {_lit(la)}) < abs(ln({xa}) - {_lit(lb)}))")
            m = f"COALESCE({m}, FALSE)"
            cells = ch.count(m)
            ch.select(replace={c: f"CASE WHEN {m} THEN CAST(s.{q(c)} AS DOUBLE) * {_lit(float(t['factor']))} "
                                  f"ELSE CAST(s.{q(c)} AS DOUBLE) END"},
                      add={f"{c}_rescaled": f"CAST({m} AS BIGINT)"})
            if ch.count(f"{q(c)} IS NOT NULL AND NOT isfinite({q(c)})"):  # §4.4: a rescaled column must stay finite
                raise ValueRefused("value_out_of_range", f"Fix {k} (rescale): '{c}' × {t['factor']} is not finite on some rows.",
                                   op_index=k, column=c)
            effects.append({**t, "cells": cells})
        elif op == "merge_classes":
            c, into = t["column"], str(t.get("into") or "other")
            m = f"(CAST(s.{q(c)} AS VARCHAR) IN ({', '.join(_lit(str(v)) for v in t.get('classes') or [])}))"
            cells = ch.count(m)
            ch.select(replace={c: f"CASE WHEN {m} THEN {_lit(into)} ELSE CAST(s.{q(c)} AS VARCHAR) END"})
            effects.append({**t, "cells": cells})
    return ch, effects


def apply(store: Any, dataset_id: str, transforms: Any, *, by: str | None, acknowledge: Any, name: str,
          note: str) -> dict[str, Any]:
    """The fixes compiled and written as a new version (or the existing one, when the data is identical). Returns
    {rec, effects, rows_before, rows_after, columns_after} (or {noop: True, …})."""
    op = "clean_labels" if any(isinstance(t, dict) and t.get("op") == "relabel" for t in transforms or []) else "data_doctor"
    return write_chain(store, dataset_id, lambda con, sql, ids, target: compile_fixes(con, sql, ids, transforms, target),
                       op=op, kind="data_doctor", by=by, acknowledge=acknowledge, name=name, note=note)


def write_chain(store: Any, dataset_id: str, build: Any, *, op: str, kind: str, by: str | None, acknowledge: Any,
                name: str, note: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """`build(con, parent_sql, ids, target) -> (chain, effects)` over the parent table, written as a new version through
    the VersionWriter (dedupe on; a result identical to the parent writes nothing and returns {noop: True})."""
    from .catalog import governance as G
    from .catalog.schema import add_edge, audit
    from .datasets import DatasetError, guard_one_class_counts, now
    from .jobs import progress as JP
    from .lake import derive as LD
    from .lake.store import open_store
    from .lake.writer import LakeError, VersionWriter

    parent = store.db.get_dataset(dataset_id)
    if not parent:
        raise DatasetError(f"No dataset with id '{dataset_id}'.")
    by = by or G.current_actor()
    if not by:
        raise DatasetError(f"Say who is making this {kind} version: send by (or the X-Ensemble-Actor header).")
    reason = (G.current_reason() or "").strip()[:500]
    JP.mark(0.01, "verifying")
    store.verify_for_edit(dataset_id, parent)
    lake = open_store(store.settings)
    pr = LD.ParentRows(store.db, lake, store, dataset_id)
    if pr.fresh or pr.storage != "parquet":
        raise DatasetError("This version has no row mapping (CSV era): its fixes run on the whole frame.")
    target = parent["target"]
    ids = np.asarray(pr.ids, dtype="int64")
    with VersionWriter(store.db, lake, op=op, by=by, parent_id=dataset_id, reason=reason, name=name,
                       primary_table=pr.table, dedupe=True, duck_kw={"mem_mb": config.doctor_write_mb()},
                       job_id=JP.job_id()) as w:  # F-JOBS: a queued apply commits its version once
        JP.mark(0.05, "compiling")
        try:
            ch, effects = build(w.con, pr.p.sql, ids, target)
        except ValueRefused:
            raise
        except ValueError as e:
            raise DatasetError(str(e)) from e
        n0, n1 = len(ids), ch.count()
        if n1 < 2:
            raise DatasetError("These fixes leave fewer than two rows.")
        one_class = None
        if target and target in ch.names:
            nb = int(ch.one(f"SELECT count(DISTINCT {q(target)}) FROM _d0"))
            na, left = ch.con.execute(f"SELECT count(DISTINCT {q(target)}), min({q(target)})::VARCHAR FROM {ch.cur}").fetchall()[0]
            one_class = guard_one_class_counts(nb, int(na), [left] if left is not None else [], target, acknowledge,
                                               what="These fixes leave")
        names = ch.names
        cols_0 = [(r[0], r[1]) for r in ch.con.execute("DESCRIBE _d0").fetchall() if r[0] != "__row_id"]
        cols0 = len(cols_0)
        if n1 == n0 and cols_0 == ch.cols and not int(ch.one(  # F-X1 M4: a no-op apply writes nothing
                f"SELECT count(*) FROM (SELECT * FROM {ch.cur} EXCEPT ALL SELECT * FROM _d0)")):
            return {"noop": True, "effects": effects, "rows_before": int(n0), "rows_after": int(n1), "columns_after": names}
        with JP.track(w.con, 0.3, 0.75, "writing"):
            w.write(pr.table, f"SELECT * FROM {ch.cur} ORDER BY __row_id")
        JP.mark(0.76, "carrying")
        LD.carry_rest(w, pr, store.db, pr.table, names)
        rows = np.sort(w.con.execute(f"SELECT __row_id FROM {ch.cur}").fetchnumpy()["__row_id"].astype("int64"))
        extra = {"kind": kind, "fixes": effects, "applied_at": now(), "by": by, "columns_before": cols0,
                 "columns_after": len(names), "how": "sql (F-X1: above config.doctor_rows() rows)", **(extra or {}),
                 **({"warnings": [one_class]} if one_class else {})}
        w.extra_ops.append(LD.no_floats({"op": op, "table": pr.table, "rows": {"kept": int(n1), "new": 0, "removed": int(n0 - n1)},
                                         "ids": "kept rows keep the parent's __row_id", "n_changes": len(effects),
                                         "changes": [e for e in effects[:50]]}))
        split_rec = LD.split_into(w, pr, pr.table, rows, None, config.HOLDOUT_SHARE, target_in_child=target in names)
        if split_rec:
            w.extra_ops.append(LD.no_floats({"op": "split_carried", "table": pr.table, **split_rec}))
        w.warnings = LD.no_floats([one_class] if one_class else [])
        JP.mark(0.85, "committing")
        try:
            out = w.commit(make_default=False, target=target if target in names else None, note=note)
        except LakeError as e:
            raise DatasetError(e.message) from e
    vid = out["version_id"]
    base = {"effects": effects, "rows_before": int(n0), "rows_after": int(n1), "columns_after": names}
    if out.get("deduped") and out["deduped"] != "job":  # F-JOBS: "job" = this job's own commit (a re-run): finish it
        with store.db.connect() as con:
            audit(con, by, "dataset_version_deduplicated", comment=reason,
                  details={"dataset_id": vid, "root_id": pr.root, "parent": dataset_id, "op": op, "data_sha256": out["data_sha256"],
                           "note": "the same data as an existing version: no new version was made, the existing one is returned"})
        rec = dict(store.db.get_dataset(vid) or {})
        rec["deduplicated"] = {"existing": vid, "data_sha256": out["data_sha256"], "sha256": out["data_sha256"]}
        return {**base, "rec": rec}
    lineage = {"matched_by": f"{op} (rows keep their __row_id)", **extra, "parent": dataset_id, "root": pr.root, "op": op,
               "rows_before": int(n0), "rows_after": int(n1), "changes": effects, "by": by, **({"reason": reason} if reason else {})}
    JP.mark(0.9, "finishing")
    LD.lineage_summary(store, vid, lineage)
    rec = store.finish_lake_version(vid, target=target if target in names else None, by=by)
    profile = rec["profile"]
    profile["target_source"] = parent["profile"].get("target_source", "given")
    profile["lineage"] = {k: v for k, v in lineage.items() if k != "changes"}
    rec = {**rec, "name": name, "note": note, "profile": profile}
    store.db.insert_dataset({k: rec[k] for k in ("id", "name", "filename", "source", "note", "created_at", "size_bytes",
                                                  "rows", "target", "kind", "profile")})
    with store.db.connect() as con:
        add_edge(con, ("dataset", dataset_id), ("dataset", vid), "derived_from", actor=by, update_meta=True,
                 meta={"op": op, "storage": "parquet", "rows_before": int(n0), "rows_after": int(n1),
                       "n_changes": len(effects)})
    return {**base, "rec": store.db.get_dataset(vid) or rec}
