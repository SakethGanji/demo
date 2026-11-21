"""P07 (plan §5.5, §6.5 R-D): the `derive()` contract's pandas adapter.

Data Doctor, Feature Lab, Autopilot, relabel, active learning, anomaly review, shift weights, annotations and the
sample generator hand `DatasetStore.derive()` a pandas frame plus, per row, its position in the parent (or -1 for a new
row). This module turns that into a Parquet table for the VersionWriter:

* **Row ids.** A kept row keeps the parent's `__row_id` (the parent's ids in read order, `__row_id` order for a Parquet
  parent; `lineage.json` root rows or `0..n-1` for a CSV-era one, the same rule P06 and P10 use). A new row gets
  `next_row_id + k` in frame order.
* **Kinds are preserved** (P04's handoff: active learning wrote an integer label back as text). A column the parent has
  is written with the parent's type when the frame's values fit it exactly: engine_v1 hands pandas an integer column
  with missing cells as float64, a DATE as ISO text, and a label column set from strings as object; all of those go back
  to BIGINT / DATE / … A column whose values no longer fit (a real change: Doctor parsing text as numbers) keeps the
  frame's type, and the change is reported.
* Only the edited table is written; the parent's other tables are carried (zero bytes).
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

from .writer import LakeError, is_reserved_name

log = logging.getLogger("ensemble.derive")

KIND_TYPES = {"integer": "BIGINT", "number": "DOUBLE", "text": "VARCHAR", "bool": "BOOLEAN"}


def no_floats(v: Any) -> Any:
    """A caller's change record for the manifest (§3.2: no floats in hashed JSON): floats → decimal strings, NaN /
    ±inf → None, numpy scalars → Python, anything else unknown → str."""
    import math

    if isinstance(v, dict):
        return {str(k): no_floats(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [no_floats(x) for x in v]
    if hasattr(v, "item") and not isinstance(v, (str, bytes)):
        try:
            v = v.item()
        except (ValueError, TypeError):
            return str(v)
    if isinstance(v, bool) or v is None or isinstance(v, (int, str)):
        return v
    if isinstance(v, float):
        return None if not math.isfinite(v) else repr(v)
    return str(v)


def _q(s: str) -> str:
    return '"' + str(s).replace('"', '""') + '"'


class ParentRows:
    """The parent table a derived version continues: its row ids by read position, its table names, its column types."""

    def __init__(self, db, store, dstore, vid: str) -> None:
        from .upload import _Parent

        try:
            p = _Parent(db, store, dstore, vid, None)
            self.fresh = False
        except LakeError as e:
            if e.code != "parent_unmapped":
                raise
            # a CSV-era version with no row mapping (made before DL1): its rows cannot claim a root row's identity, so
            # they all get fresh ids (recorded in the manifest; the edge is unpaired)
            p = _FreshParent(db, store, dstore, vid)
            self.fresh = True
        self.p = p
        self.vid, self.root, self.table, self.tables = p.vid, p.root, p.table, list(p.tables)
        self.ids = np.asarray(p.ids, dtype="int64")
        self.split = getattr(p, "split", None)
        self.storage = p.storage
        self.types: dict[str, str] = {}
        if self.storage == "parquet":
            with db.connect() as con:
                import json

                r = con.execute("SELECT schema_json FROM version_tables WHERE version_id = ? AND table_name = ?",
                                (vid, self.table)).fetchone()
            self.types = {c["name"]: c["type"] for c in json.loads(r[0])} if r else {}
        else:
            from .. import frame_io as FIO

            kinds = FIO.columns(dstore.schema(vid)) if dstore is not None else {}
            self.types = {c: KIND_TYPES.get(k, "VARCHAR") for c, k in kinds.items()}
        self.next_row_id = p.next_row_id(db)

    def reserve(self, db, n: int) -> int:
        """F-X1 M1: take `n` new row ids for this root table atomically (writer.reserve_row_ids); they start at the
        returned value, which also becomes `next_row_id` (n = 0: nothing is taken)."""
        from .writer import reserve_row_ids

        if int(n) > 0:
            self.next_row_id = reserve_row_ids(db, self.root, self.table, self.next_row_id, int(n))
        return self.next_row_id

    def keys(self, db) -> list[dict[str, Any]]:
        from .upload import _parent_keys

        return _parent_keys(db, self.p) if not self.fresh else []


class _FreshParent:
    def __init__(self, db, store, dstore, vid: str) -> None:
        from .. import frames

        with db.connect() as con:
            r = dict(con.execute("SELECT id, root_id, storage, target FROM datasets WHERE id = ?", (vid,)).fetchone())
        self.vid, self.root, self.storage = vid, r["root_id"] or vid, r["storage"] or "csv"
        self.table, self.tables = "data", ["data"]
        self.n = frames.n_rows(frames.table(vid))
        self.ids = np.full(self.n, -1, dtype="int64")
        self.split = None

    def next_row_id(self, db) -> int:
        with db.connect() as con:
            r = con.execute("SELECT next_row_id FROM dataset_root_tables WHERE root_id = ? AND table_name = ?",
                            (self.root, self.table)).fetchone()
        return int(r[0]) if r else 0


def _arrow_column(s: pd.Series) -> Any:
    import pyarrow as pa

    if isinstance(s.dtype, pd.CategoricalDtype):
        s = s.astype(object)
    try:
        return pa.array(s, from_pandas=True)
    except (pa.ArrowInvalid, pa.ArrowTypeError, TypeError, ValueError, OverflowError):
        # mixed values (ints and text in one object column): text, missing stays missing
        return pa.array([None if v is None or (isinstance(v, float) and np.isnan(v)) or v is pd.NA else str(v) for v in s],
                        type=pa.string())


def frame_arrow(df: pd.DataFrame, ids: np.ndarray) -> Any:
    import pyarrow as pa

    names = [str(c) for c in df.columns]
    bad = [c for c in names if is_reserved_name(c)]
    if bad:
        raise LakeError(400, "reserved_column", f"Column {bad[0]!r} is a reserved name ('__…', '_row_id', '_row').")
    arrays = [pa.array(np.asarray(ids, dtype="int64"))] + [_arrow_column(df.iloc[:, i]) for i in range(df.shape[1])]
    return pa.Table.from_arrays(arrays, names=["__row_id", *names])


def _vt(t: str) -> str:
    from ..dataset_edit import vtype

    return vtype(t)


def typed_select(con, view: str, parent_types: dict[str, str], hints: dict[str, str] | None = None
                 ) -> tuple[str, list[dict[str, Any]]]:
    """`SELECT __row_id, …` over the registered frame with each column in the parent's type when its values fit it
    exactly (see the module doc). Returns (sql, [{column, from, to, kept}]) for columns whose frame type differed."""
    hints = hints or {}
    cols = [(r[0], r[1]) for r in con.execute(f"DESCRIBE SELECT * FROM {view}").fetchall() if r[0] != "__row_id"]
    parts, notes = ["__row_id"], []
    for c, dt in cols:
        x = _q(c)
        want = parent_types.get(c) or KIND_TYPES.get(hints.get(c, ""))
        if not want:
            parts.append(x)
            continue
        pv, dv = _vt(want), _vt(dt)
        if hints.get(c) == "text" and dv != "VARCHAR":
            parts.append(f"CAST({x} AS VARCHAR) AS {x}")
            notes.append({"column": c, "from": dt, "to": "VARCHAR", "kept": True, "why": "declared text"})
            continue
        if pv == dv or (pv in ("BIGINT", "INTEGER") and dv in ("BIGINT", "INTEGER")):
            parts.append(x if dt == want or pv == dv else f"{x}::{want} AS {x}")
            continue
        expr = None
        if pv in ("BIGINT", "INTEGER") and dv in ("DOUBLE", "VARCHAR"):
            d = f"{x}::DOUBLE" if dv == "DOUBLE" else f"TRY_CAST(trim({x}) AS DOUBLE)"
            lost = con.execute(f"SELECT count(*) FILTER (WHERE {x} IS NOT NULL AND ({d} IS NULL OR NOT isfinite({d}) OR {d} <> round({d})"
                               f" OR abs({d}) >= 9.2e18)) FROM {view}").fetchone()[0]
            expr = f"CAST(round({d}) AS {want})" if not lost else None
        elif pv == "DOUBLE" and dv in ("BIGINT", "INTEGER"):
            expr = f"{x}::DOUBLE"
        elif pv in ("DOUBLE", "BOOLEAN", "DATE", "TIMESTAMP") and dv == "VARCHAR":
            tc = f"TRY_CAST(trim({x}) AS {want})"
            lost = con.execute(f"SELECT count(*) FILTER (WHERE {x} IS NOT NULL AND trim({x}) <> '' AND {tc} IS NULL) FROM {view}").fetchone()[0]
            expr = tc if not lost else None
        if expr is not None:
            parts.append(f"{expr} AS {x}")
            notes.append({"column": c, "from": dt, "to": want, "kept": True})
        else:
            parts.append(x)
            notes.append({"column": c, "from": want, "to": dt, "kept": False})
    return ", ".join(parts), notes


def write_frame(w, table: str, df: pd.DataFrame, ids: np.ndarray, parent_types: dict[str, str],
                hints: dict[str, str] | None = None, **kw: Any) -> list[dict[str, Any]]:
    """The frame as table `table` of the writer's version (ids = __row_id per row). Returns the type notes."""
    import secrets

    if len(ids) != len(df):
        raise LakeError(400, "derive_rows", f"{len(ids)} row ids for {len(df)} rows.")
    at = frame_arrow(df, ids)
    view = "_frame_" + secrets.token_hex(4)
    w.con.register(view, at)
    try:
        sql, notes = typed_select(w.con, view, parent_types, hints)
        w.write(table, f"SELECT {sql} FROM {view}", **kw)
    finally:
        try:
            w.con.execute("SELECT 1").fetchall()  # DuckDB 2.0: a fetchone() result stays open and blocks unregister
            w.con.unregister(view)
        except Exception:  # noqa: BLE001 - never hide the write's own error behind the cleanup's
            log.debug("unregister %s failed", view)
    return notes


def carry_rest(w, parent: ParentRows, db, table: str, columns: list[str]) -> None:
    """The parent's other tables by reference, and the parent's keys that still hold on the written table."""
    for other in parent.tables:
        if other != table and parent.storage == "parquet":
            w.carry(other)
    for k in parent.keys(db):
        if all(c in columns for c in k["columns"]):
            w.key(table, k["columns"], kind=k["kind"], declared_by=k["declared_by"])


# --------------------------------------------------------------------------- the bridge until P09 (splits as Parquet)


def order_rows(ids: np.ndarray) -> np.ndarray:
    """The written table is sorted by __row_id, and every reader sees it in that order: the permutation from frame
    order to read order (stable)."""
    return np.argsort(np.asarray(ids, dtype="int64"), kind="stable")


def reorder_split(sp: dict[str, Any], order: np.ndarray) -> dict[str, Any]:
    """A DL4 split given by frame position, re-indexed to read order."""
    inv = np.empty(len(order), dtype="int64")
    inv[order] = np.arange(len(order))
    out = dict(sp)
    fold = np.asarray(sp.get("fold") or [], dtype="int64")
    if len(fold) == len(order):
        out["fold"] = [int(x) for x in fold[order]]
    for k in ("holdout", "tuning", "excluded"):
        if isinstance(sp.get(k), list):
            out[k] = sorted(int(inv[int(i)]) for i in sp[k] if 0 <= int(i) < len(order))
    return out


def split_into(w, parent: ParentRows, table: str, ids_read_order: np.ndarray, given: dict[str, Any] | None,
               new_holdout_share: float, *, target_in_child: bool = True) -> dict[str, Any] | None:
    """P09 (§5.7): the new version's split, committed with it by the writer (replaces the P06/P07 DL4 JSON bridge).
    A caller's split (by read position) is written as given; otherwise the parent's split is carried by a join on
    `__row_id` (kept rows keep their fold; new rows get splitmix64(__row_id ⊕ seed)). A structured split is drawn fresh
    on first use."""
    from . import splitstore as SS

    if given is not None:
        return SS.given_into(w, table, ids_read_order, given, parent.ids, parent.split)
    return SS.carry_into(w, table, parent.ids, parent.split, new_holdout_share, target_in_child=target_in_child,
                         parent_vid=parent.vid)


def lineage_summary(dstore, vid: str, lineage: dict[str, Any]) -> None:
    """lineage.json of a derived Parquet version: what happened, for the CSV-era readers that look there (kind, parent,
    version). P09: it no longer carries row maps; a row's identity is its `__row_id`."""
    import json

    d = dstore.dir(vid)
    d.mkdir(parents=True, exist_ok=True)
    lin = {k: v for k, v in lineage.items() if k != "parent_rows"}
    lin.update(storage="parquet", rows="__row_id (P09: no row map; the manifest's provenance is the record)")
    (d / "lineage.json").write_text(json.dumps(lin, default=str))
