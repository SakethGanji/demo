"""DL1: what two versions of a table have in common, frame by frame (no storage here; catalog/dsets.py reads the files).

match_rows       a new upload against its parent: by a row key, else by an exact hash of the shared columns
contract_checks  what a bank would want flagged on a new version (warn, never block)
diff             schema, rows (added / removed / changed) and per-column shifts (missing %, mean / median, top
                 categories, PSI) between any two versions
"""

from __future__ import annotations

import re
from collections import defaultdict, deque
from typing import Any

import numpy as np
import pandas as pd

from .registry.drift import feature_psi

#: a contract warning when a version has this share fewer rows than its parent
ROW_DROP_WARN = 0.20
#: a target with at most this many values is a set of categories (new ones are flagged)
TARGET_MAX_CATS = 50
KEY_NAME = re.compile(r"(^|[_\s-])(id|key|uuid|guid|no|number|ref)$|^id[_\s-]|_id$", re.I)


def kind(s: pd.Series) -> str:
    """number, bool, date or text: what a change of type means to an analyst (int ↔ float is still a number)."""
    k = s.dtype.kind
    if k in "iuf":
        return "number"
    if k == "b":
        return "bool"
    if k == "M":
        return "date"
    return "text"


def _same_type(a: pd.DataFrame, b: pd.DataFrame, skip: tuple[str, ...] = ()) -> list[str]:
    return [c for c in b.columns if c in a.columns and c not in skip and a[c].dtype == b[c].dtype]


def row_hash(df: pd.DataFrame, cols: list[str]) -> np.ndarray:
    if not cols:
        return np.zeros(len(df), dtype=np.uint64)
    return pd.util.hash_pandas_object(df[cols], index=False).to_numpy()


def guess_key(a: pd.DataFrame, b: pd.DataFrame) -> str | None:
    """A column both files share whose name says it is an identifier and whose values are unique and present in both."""
    for c in b.columns:
        if c in a.columns and KEY_NAME.search(str(c)) and a[c].notna().all() and b[c].notna().all() \
                and a[c].astype(str).is_unique and b[c].astype(str).is_unique:
            return c
    return None


def _hash_pairs(ha: np.ndarray, hb: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Positions (ia, ib) of equal hashes, matched in file order (duplicates pair first with first)."""
    q: dict[int, deque] = defaultdict(deque)
    for i, h in enumerate(ha):
        q[int(h)].append(i)
    ia, ib = [], []
    for j, h in enumerate(hb):
        d = q.get(int(h))
        if d:
            ia.append(d.popleft())
            ib.append(j)
    return np.asarray(ia, dtype=int), np.asarray(ib, dtype=int)


def _key_pairs(a: pd.DataFrame, b: pd.DataFrame, key: str) -> tuple[np.ndarray, np.ndarray]:
    pos = {v: i for i, v in enumerate(a[key].astype(str))}
    ia, ib = [], []
    for j, v in enumerate(b[key].astype(str)):
        i = pos.get(v)
        if i is not None:
            ia.append(i)
            ib.append(j)
    return np.asarray(ia, dtype=int), np.asarray(ib, dtype=int)


def _changed(a: pd.DataFrame, b: pd.DataFrame, ia: np.ndarray, ib: np.ndarray, skip: tuple[str, ...] = ()) -> np.ndarray:
    """Mask over the pairs: True where a shared column of unchanged type differs."""
    cols = _same_type(a, b, skip)
    if not len(ia) or not cols:
        return np.zeros(len(ia), dtype=bool)
    return row_hash(a.iloc[ia], cols) != row_hash(b.iloc[ib], cols)


def match_rows(parent: pd.DataFrame, new: pd.DataFrame, row_key: str | None) -> dict[str, Any]:
    """Each row of `new` → its row in `parent` or -1. `row_key`: a column name; None = guess one from the names, then
    fall back to the exact hash; "" or "hash" = the hash. Raises ValueError (shown to the user) on a bad key."""
    from .datasets import DatasetError

    key = None
    if row_key not in ("", "hash"):
        key = row_key or guess_key(parent, new)
        if row_key:
            for side, f in (("the current version", parent), ("the new file", new)):
                if key not in f.columns:
                    raise DatasetError(f"Row key '{key}' is not a column of {side}.")
                dup = int(f[key].astype(str).duplicated().sum())
                if dup or f[key].isna().any():
                    raise DatasetError(f"Row key '{key}' is not unique in {side} ({dup:,} repeated values"
                                       f"{', some missing' if f[key].isna().any() else ''}): pick another, or match rows by hash.")
    if key:
        ia, ib = _key_pairs(parent, new, key)
        changed = _changed(parent, new, ia, ib, skip=(key,))
        how = f"row key '{key}'" + ("" if row_key else " (guessed from its name)")
    else:
        cols = _same_type(parent, new)
        if not cols:
            raise DatasetError("The new file shares no column of the same type with the current version: nothing to match rows on.")
        ia, ib = _hash_pairs(row_hash(parent, cols), row_hash(new, cols))
        changed = np.zeros(len(ia), dtype=bool)
        how = f"exact row hash over {len(cols)} shared columns"
    rmap = np.full(len(new), -1, dtype=int)
    rmap[ib] = ia
    return {"row_map": rmap.tolist(), "matched_by": how, "row_key": key, "matched": int(len(ia)),
            "changed": int(changed.sum()), "added": int(len(new) - len(ib)), "removed": int(len(parent) - len(ia)),
            **({} if key else {"note": "an exact-hash match counts a changed row as one removed and one new row: give a row "
                                       "key to see changes"})}


def schema_diff(a: pd.DataFrame, b: pd.DataFrame) -> dict[str, Any]:
    common = [c for c in b.columns if c in a.columns]
    return {"columns_added": [c for c in b.columns if c not in a.columns],
            "columns_removed": [c for c in a.columns if c not in b.columns],
            "type_changes": [{"column": c, "from": str(a[c].dtype), "to": str(b[c].dtype), "kind_from": kind(a[c]), "kind_to": kind(b[c])}
                             for c in common if kind(a[c]) != kind(b[c])],
            "dtype_changes": [{"column": c, "from": str(a[c].dtype), "to": str(b[c].dtype)}
                              for c in common if a[c].dtype != b[c].dtype and kind(a[c]) == kind(b[c])],
            "n_columns": [int(a.shape[1]), int(b.shape[1])]}


def _labels(s: pd.Series) -> set[str]:
    """DL4 (M-b): class labels as text, with 1.0 read as 1 (a float copy of an int target is not a new class)."""
    def one(v: Any) -> str:
        if isinstance(v, (float, np.floating)) and float(v).is_integer():
            return str(int(v))
        return str(v)
    return {one(v) for v in s.dropna().tolist()}


def contract_checks(parent: pd.DataFrame, new: pd.DataFrame, target: str | None, row_drop: float = ROW_DROP_WARN) -> dict[str, Any]:
    """Warnings, never a block: the target gone, a type changed, a new category in the target, too many rows gone, a
    column gone."""
    sd = schema_diff(parent, new)
    w: list[dict[str, Any]] = []
    if target and target in parent.columns and target not in new.columns:
        w.append({"code": "target_missing", "message": f"The target '{target}' is not in the new file: runs on this version need another target."})
    for t in sd["type_changes"]:
        w.append({"code": "type_changed", "column": t["column"],
                  "message": f"'{t['column']}' changed type: {t['kind_from']} ({t['from']}) → {t['kind_to']} ({t['to']})."})
    if target and target in parent.columns and target in new.columns and parent[target].nunique() <= TARGET_MAX_CATS:
        new_cats = sorted(_labels(new[target]) - _labels(parent[target]))
        if new_cats:
            w.append({"code": "new_target_category", "column": target, "values": new_cats[:20],
                      "message": f"The target '{target}' has {len(new_cats)} new value(s): {', '.join(new_cats[:5])}. Models trained on the "
                                 "current version have never predicted them."})
    if len(parent) and (len(parent) - len(new)) / len(parent) > row_drop:
        w.append({"code": "rows_dropped", "message": f"{len(parent) - len(new):,} fewer rows ({(len(parent) - len(new)) / len(parent):.0%}; "
                                                     f"warning above {row_drop:.0%}): {len(parent):,} → {len(new):,}."})
    for c in sd["columns_removed"]:
        w.append({"code": "column_removed", "column": c, "message": f"'{c}' is gone: runs that used it can't be re-fitted on this version as they were."})
    renamed = rename_hints(parent, new, sd["columns_removed"], sd["columns_added"])
    for h in renamed:
        w.append({"code": "column_renamed_maybe", "column": h["from"], "to": h["to"], "overlap": h["overlap"],
                  "message": f"Was '{h['from']}' renamed to '{h['to']}'? Same type, and {h['overlap']:.0%} of the new column's "
                             "values are the old column's. Nothing is renamed automatically: edit the file or the version if so."})
    if target and target in parent.columns and target in new.columns:
        ma, mb = float(parent[target].isna().mean()), float(new[target].isna().mean())
        if mb - ma > 0.05:
            w.append({"code": "target_missing_values", "column": target,
                      "message": f"The target is missing on {mb:.1%} of rows (was {ma:.1%})."})
    return {"warnings": w, "columns_added": sd["columns_added"], "columns_removed": sd["columns_removed"],
            "type_changes": sd["type_changes"], "rows_before": int(len(parent)), "rows_after": int(len(new)),
            "row_drop_threshold": row_drop, "blocking": False, "renamed_maybe": renamed}


def rename_hints(parent: pd.DataFrame, new: pd.DataFrame, removed: list[str], added: list[str],
                 min_overlap: float = 0.95) -> list[dict[str, Any]]:
    """§4.4 (DLA4): "columns renamed?" pairs for the upload check: a removed and an added column of the same schema
    kind whose new values are ≥ 95 % (distinct values) the old column's. A hint, never an automatic rename."""
    from .frame_io import kind_of

    out = []
    for b in added:
        nb = new[b].dropna().astype(str).unique()
        if not len(nb):
            continue
        best = None
        for a in removed:
            if kind_of(parent[a]) != kind_of(new[b]):
                continue
            ov = float(pd.Index(nb).isin(parent[a].dropna().astype(str).unique()).mean())
            if ov >= min_overlap and (best is None or ov > best["overlap"]):
                best = {"from": str(a), "to": str(b), "overlap": round(ov, 4)}
        if best:
            out.append(best)
    return out


def _f(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return round(f, 6) if np.isfinite(f) else None


def _top(s: pd.Series, n: int = 3) -> list[dict[str, Any]]:
    vc = s.dropna().astype(str).value_counts(normalize=True).head(n)
    return [{"value": k, "share": round(float(v), 4)} for k, v in vc.items()]


def column_stats(a: pd.DataFrame, b: pd.DataFrame, max_rows: int = 200_000) -> list[dict[str, Any]]:
    """Per column: missing %, mean / median (numbers), top categories (text), PSI on the columns both have."""
    a = a.sample(max_rows, random_state=0) if len(a) > max_rows else a
    b = b.sample(max_rows, random_state=0) if len(b) > max_rows else b
    out = []
    for c in [*a.columns, *[c for c in b.columns if c not in a.columns]]:
        sa, sb = (a[c] if c in a.columns else None), (b[c] if c in b.columns else None)
        k = kind(sa if sa is not None else sb)
        row: dict[str, Any] = {"column": c, "kind": k, "in": "both" if sa is not None and sb is not None else ("a" if sb is None else "b"),
                               "missing_a": _f(sa.isna().mean()) if sa is not None else None,
                               "missing_b": _f(sb.isna().mean()) if sb is not None else None}
        if sa is not None and sb is not None and kind(sa) != kind(sb):
            row["kind"] = f"{kind(sa)} → {kind(sb)}"
        for side, x in (("a", sa), ("b", sb)):  # each side by its own type (a column can change type between versions)
            if x is None:
                continue
            if kind(x) == "number":
                row.update({f"mean_{side}": _f(x.mean()), f"median_{side}": _f(x.median())})
            else:
                row.update({f"top_{side}": _top(x), f"distinct_{side}": int(x.nunique())})
        if sa is not None and sb is not None and kind(sa) != kind(sb):
            row.update(psi=None, band=None, note="the type changed: the two sides can't share bins")
        elif sa is not None and sb is not None:
            pk = "number" if kind(sa) == "number" and kind(sb) == "number" else "category"
            try:
                p = feature_psi(c, pk, sa, sb)
                row.update(psi=_f(p["psi"]), band=p["band"])
            except Exception:  # noqa: BLE001 - a column PSI can't be drawn for is reported without one
                row.update(psi=None, band=None)
            if k != "number" and sa.nunique() > 0.95 * max(len(sa), 1):
                row["note"] = "nearly every value is distinct (an id?): PSI means little here"
        out.append(row)
    return sorted(out, key=lambda r: -(r.get("psi") or 0))


def diff(a: pd.DataFrame, b: pd.DataFrame, pairs: tuple[np.ndarray, np.ndarray] | None, how: str,
         key: str | None = None, sample: int = 5) -> dict[str, Any]:
    """`pairs` (positions in a, positions in b) of the same original rows, or None to match by exact row hash."""
    sd = schema_diff(a, b)
    hcols = _same_type(a, b)
    if pairs is None:
        if not hcols:  # DL2 (B4): nothing to compare rows on (a zero hash would "match" every row)
            ia = ib = np.zeros(0, dtype=int)
            how = "no rows matched: the two versions share no column of the same type"
        else:
            ia, ib = _hash_pairs(row_hash(a, hcols), row_hash(b, hcols))
            how = f"exact row hash over {len(hcols)} shared columns (a changed row counts as removed + added)"
        changed = np.zeros(len(ia), dtype=bool)
    else:
        ia, ib = (np.asarray(x, dtype=int) for x in pairs)
        changed = _changed(a, b, ia, ib, skip=(key,) if key else ())
        # DL2 (B5): rows the map leaves unpaired on both sides (e.g. the new rows of two sibling uploads, which got
        # different root ids) pair when they are identical over the shared columns
        ra, rb = np.setdiff1d(np.arange(len(a)), ia), np.setdiff1d(np.arange(len(b)), ib)
        if hcols and len(ra) and len(rb):
            xa, xb = _hash_pairs(row_hash(a.iloc[ra], hcols), row_hash(b.iloc[rb], hcols))
            if len(xa):
                ia, ib = np.concatenate([ia, ra[xa]]), np.concatenate([ib, rb[xb]])
                changed = np.concatenate([changed, np.zeros(len(xa), dtype=bool)])
                how += f"; {len(xa):,} more rows identical by exact hash"
    cols = _same_type(a, b, (key,) if key else ())
    examples = []
    for i, j in list(zip(ia[changed], ib[changed]))[:sample]:
        cells = [{"column": c, "a": _cell(a.iat[i, a.columns.get_loc(c)]), "b": _cell(b.iat[j, b.columns.get_loc(c)])}
                 for c in cols if not _eq(a.iat[i, a.columns.get_loc(c)], b.iat[j, b.columns.get_loc(c)])]
        examples.append({"row_a": int(i), "row_b": int(j), **({"key": _cell(a.iat[i, a.columns.get_loc(key)])} if key else {}),
                         "cells": cells[:8]})
    rows = {"a": int(len(a)), "b": int(len(b)), "matched": int(len(set(ib.tolist()))), "unchanged": int((~changed).sum()),
            "changed": int(changed.sum()), "added": int(len(b) - len(set(ib.tolist()))), "removed": int(len(a) - len(set(ia.tolist()))),
            "matched_by": how, "changed_examples": examples,
            "compared_columns": len(cols), "note": "changes are read on the shared columns whose type did not change"}
    return {"schema": sd, "rows": rows, "columns": column_stats(a, b)}


def _eq(x: Any, y: Any) -> bool:
    if pd.isna(x) and pd.isna(y):
        return True
    return x == y


def _cell(v: Any) -> Any:
    if v is None or (isinstance(v, float) and not np.isfinite(v)) or pd.isna(v):
        return None
    return v.item() if hasattr(v, "item") else v


# =====================================================================================================================
# P06 (plan §4.2, §5.3; DLA4 #2): an upload as the next version, matched in DuckDB. Nothing here decides: candidates
# are proposed with their numbers, every method is simulated with matched / changed / added / removed and
# holdout_identity_lost, and the person's choice (`match`) is what the commit uses. The profile role 'ignored' plays no
# part (DLA4 #2: `acct` "A00000…" was hidden by it).
# =====================================================================================================================

#: a share of parent holdout rows losing their identity at or above which the commit needs an acknowledgement (§11: 1 %)
IDENTITY_LOSS_ACK = 0.01
#: a candidate key matching at least this share (of the new file's rows) more than the chosen method needs one too
BETTER_KEY_ACK = 0.01
#: below this share of the new file's rows matched: "is this the same dataset?" (a warning, no acknowledgement)
LOW_OVERLAP = 0.50
METHODS = ("key", "declared", "row_hash", "none")
ACK_TOKEN = "holdout_identity_loss"
_INTS = {"TINYINT", "SMALLINT", "INTEGER", "BIGINT", "HUGEINT", "UTINYINT", "USMALLINT", "UINTEGER", "UBIGINT"}


def type_family(t: str) -> str:
    """number | text | date | bool | other: what may be compared across two versions (int ↔ float is still a number)."""
    t = (t or "").upper()
    if t in _INTS or t in ("FLOAT", "DOUBLE", "REAL") or t.startswith(("DECIMAL", "NUMERIC")):
        return "number"
    if t == "VARCHAR" or t.startswith("VARCHAR"):
        return "text"
    if t == "DATE" or t.startswith("TIMESTAMP"):
        return "date"
    if t == "BOOLEAN":
        return "bool"
    return "other"


def _qi(s: str) -> str:
    return '"' + s.replace('"', '""') + '"'


def _pair(col: str, tp: str, tn: str) -> tuple[str, str] | None:
    """(parent expr, new expr) comparing a shared column in one type, or None when the families do not compare."""
    fp, fn, c = type_family(tp), type_family(tn), _qi(col)
    if fp == fn == "number":
        if tp.upper() in _INTS and tn.upper() in _INTS:
            return f"{c}::BIGINT", f"{c}::BIGINT"
        return f"{c}::DOUBLE", f"{c}::DOUBLE"
    if fp == fn == "date":
        return f"{c}::TIMESTAMP", f"{c}::TIMESTAMP"
    if fp == fn and fp in ("text", "bool"):
        return c, c
    if {fp, fn} == {"text", "date"}:  # a CSV-era parent reads dates as text: compare as ISO text
        return (f"strftime({c}::TIMESTAMP, '%Y-%m-%d')" if fp == "date" and tp.upper() == "DATE" else f"{c}::VARCHAR",
                f"strftime({c}::TIMESTAMP, '%Y-%m-%d')" if fn == "date" and tn.upper() == "DATE" else f"{c}::VARCHAR")
    return None


def _dec(x: float | None, places: int = 4) -> str | None:
    """A share as decimal text (manifest JSON has no floats)."""
    return None if x is None else f"{x:.{places}f}"


def method_label(m: dict[str, Any] | None) -> str:
    if not m:
        return "none chosen"
    k = m.get("method")
    if k in ("key", "declared"):
        cols = m.get("columns") or []
        return f"{'declared ' if k == 'declared' else ''}key {', '.join(cols)}" if cols else "declared key"
    return {"row_hash": "row content (exact row hash)", "none": "no match (all rows new)"}.get(k, str(k))


def parse_match(m: Any, table: str) -> dict[str, Any] | None:
    """The commit's `match` for `table`: {"method": …} or {table: {"method": …}}. Raises ValueError on a bad shape."""
    if m is None or m == {}:
        return None
    if not isinstance(m, dict):
        raise ValueError("match is {\"method\": \"key\"|\"declared\"|\"row_hash\"|\"none\", \"columns\": [...]}.")
    if "method" not in m:
        if table in m:
            m = m[table]
        elif len(m) == 1 and isinstance(next(iter(m.values())), dict):
            raise ValueError(f"match names table {next(iter(m))!r}; this upload's table is {table!r}.")
        if not isinstance(m, dict) or "method" not in m:
            raise ValueError("match is {\"method\": \"key\"|\"declared\"|\"row_hash\"|\"none\", \"columns\": [...]}.")
    k = m.get("method")
    if k not in METHODS:
        raise ValueError(f"match.method is one of {', '.join(METHODS)}, not {k!r}.")
    out: dict[str, Any] = {"method": k}
    if k == "key":
        cols = m.get("columns")
        if isinstance(cols, str):
            cols = [cols]
        if not isinstance(cols, list) or not cols or not all(isinstance(c, str) and c for c in cols):
            raise ValueError("match.columns names the key column(s), e.g. {\"method\": \"key\", \"columns\": [\"acct\"]}.")
        out["columns"] = list(dict.fromkeys(cols))
    return out


class Matcher:
    """Parent table vs the new file, in one DuckDB connection. Views: _p (`__row_id`, columns), _n (`__fr` = the new
    file's row number, columns), table _h (the parent's holdout `__row_id`s). `parent_sql` / `new_sql` are relations
    (SELECT … or read_parquet(…)); `new_sql` must carry `__fr`."""

    def __init__(self, con: Any, parent_sql: str, new_sql: str, holdout_ids: Any = None,
                 declared_keys: list[list[str]] | None = None) -> None:
        self.con = con
        con.execute(f"CREATE OR REPLACE TEMP VIEW _p AS SELECT * FROM ({parent_sql})")
        con.execute(f"CREATE OR REPLACE TEMP VIEW _n AS SELECT * FROM ({new_sql})")
        h = np.asarray(holdout_ids if holdout_ids is not None else [], dtype=np.int64)
        con.execute("CREATE OR REPLACE TEMP TABLE _h (__row_id BIGINT)")
        if len(h):
            import pyarrow as pa

            con.register("_h_in", pa.table({"__row_id": h}))
            con.execute("INSERT INTO _h SELECT DISTINCT __row_id FROM _h_in")
            con.unregister("_h_in")
        self.ptypes = {r[0]: r[1] for r in con.execute("DESCRIBE SELECT * FROM _p").fetchall() if r[0] != "__row_id"}
        self.ntypes = {r[0]: r[1] for r in con.execute("DESCRIBE SELECT * FROM _n").fetchall() if r[0] != "__fr"}
        self.shared: dict[str, tuple[str, str]] = {}
        for c, tn in self.ntypes.items():
            if c in self.ptypes:
                pr = _pair(c, self.ptypes[c], tn)
                if pr:
                    self.shared[c] = pr
        self.n_parent = int(con.execute("SELECT count(*) FROM _p").fetchone()[0])
        self.n_new = int(con.execute("SELECT count(*) FROM _n").fetchone()[0])
        self.n_holdout = int(con.execute("SELECT count(*) FROM _h SEMI JOIN _p USING (__row_id)").fetchone()[0])
        self.declared = [list(k) for k in (declared_keys or []) if k and all(c in self.shared for c in k)]
        self._cands: list[dict[str, Any]] | None = None

    # -- candidates ---------------------------------------------------------------------------------------------------

    def _side_stats(self, side: str, cols: list[str]) -> dict[str, tuple[int, int]]:
        """{column: (non-null, distinct)} for one side, one scan."""
        if not cols:
            return {}
        idx = 0 if side == "_p" else 1
        aggs = []
        for c in cols:
            e = self.shared[c][idx]
            aggs += [f"count({e})", f"count(DISTINCT {e})"]
        row = self.con.execute(f"SELECT {', '.join(aggs)} FROM {side}").fetchone()
        return {c: (int(row[2 * i]), int(row[2 * i + 1])) for i, c in enumerate(cols)}

    def _key_stats(self, cols: list[str]) -> dict[str, Any]:
        """Uniqueness, nulls and overlap of a (composite) key on both sides."""
        pe = ", ".join(f"{self.shared[c][0]} AS k{i}" for i, c in enumerate(cols))
        ne = ", ".join(f"{self.shared[c][1]} AS k{i}" for i, c in enumerate(cols))
        ks = ", ".join(f"k{i}" for i in range(len(cols)))
        nn = " AND ".join(f"k{i} IS NOT NULL" for i in range(len(cols)))
        out: dict[str, Any] = {}
        for side, e, n in (("parent", pe, self.n_parent), ("new", ne, self.n_new)):
            rel = "_p" if side == "parent" else "_n"
            full, dist = self.con.execute(f"SELECT count(*) FILTER (WHERE {nn}), count(DISTINCT ({ks})) FILTER (WHERE {nn})"
                                          f" FROM (SELECT {e} FROM {rel})").fetchone()
            out[side] = {"rows": n, "non_null": int(full), "distinct": int(dist), "nulls": int(n - full),
                         "duplicates": int(full - dist)}
        ov = self.con.execute(f"SELECT count(*) FROM (SELECT DISTINCT {ks} FROM (SELECT {ne} FROM _n) WHERE {nn}) n"
                              f" SEMI JOIN (SELECT DISTINCT {ks} FROM (SELECT {pe} FROM _p) WHERE {nn}) p USING ({ks})").fetchone()[0]
        out["overlap"] = (int(ov) / out["new"]["distinct"]) if out["new"]["distinct"] else 0.0
        return out

    def _id_like(self, cols: list[str], st: dict[str, Any]) -> bool:
        if len(cols) != 1:
            return False
        c = cols[0]
        if KEY_NAME.search(c):
            return True
        if type_family(self.ntypes.get(c, "")) != "text" or not st["new"]["non_null"]:
            return False
        e = self.shared[c][1]
        mn, mx = self.con.execute(f"SELECT min(length({e})), max(length({e})) FROM _n").fetchone()
        return mn == mx and st["new"]["distinct"] >= 0.99 * st["new"]["non_null"]

    def candidates(self, top: int = 3) -> list[dict[str, Any]]:
        """key_candidates() (§5.3 step 1): every shared column of a compatible type (whatever its profile role) plus the
        parent's declared keys, scored uniqueness_both × non_null_both × overlap, tie-break id-like. Proposes; never
        decides."""
        if self._cands is not None:
            return self._cands[:top]
        cols = list(self.shared)
        ps, ns = self._side_stats("_p", cols), self._side_stats("_n", cols)
        pre = []
        for c in cols:
            (pn, pd_), (nn, nd) = ps[c], ns[c]
            u = (pd_ / pn if pn else 0) * (nd / nn if nn else 0) * (pn / max(self.n_parent, 1)) * (nn / max(self.n_new, 1))
            if u >= 0.9:  # a key is unique and present on both sides; others cannot score
                pre.append([c])
        keys = pre + [k for k in self.declared if k not in pre]
        out = []
        for k in keys:
            st = self._key_stats(k)
            p, n = st["parent"], st["new"]
            uniq = (p["distinct"] / p["non_null"] if p["non_null"] else 0) * (n["distinct"] / n["non_null"] if n["non_null"] else 0)
            nonnull = (p["non_null"] / max(p["rows"], 1)) * (n["non_null"] / max(n["rows"], 1))
            score = uniq * nonnull * st["overlap"]
            out.append({"columns": k, "score": round(score, 6), "uniqueness": round(uniq, 6), "non_null": round(nonnull, 6),
                        "overlap": round(st["overlap"], 6), "id_like": self._id_like(k, st), "declared": k in self.declared,
                        "valid": p["duplicates"] == 0 and n["duplicates"] == 0 and p["nulls"] == 0 and n["nulls"] == 0,
                        "parent": p, "new": n,
                        "role_ignored_does_not_hide": True})
        out.sort(key=lambda r: (-round(r["score"], 3), not r["declared"], not r["id_like"], -r["score"], r["columns"]))
        self._cands = out
        return out[:top]

    # -- simulate -------------------------------------------------------------------------------------------------------

    def key_problems(self, cols: list[str], limit: int = 10) -> dict[str, Any] | None:
        """None when `cols` is a valid key on both sides; else counts and the first offending values per side."""
        miss = [c for c in cols if c not in self.shared]
        if miss:
            why = []
            for c in miss:
                if c not in self.ntypes:
                    why.append(f"'{c}' is not in the new file")
                elif c not in self.ptypes:
                    why.append(f"'{c}' is not in the parent")
                else:
                    why.append(f"'{c}' is {self.ptypes[c]} in the parent and {self.ntypes[c]} in the new file")
            return {"code": "key_column_missing", "columns": miss, "message": "Key: " + "; ".join(why) + "."}
        st = self._key_stats(cols)
        bad = {s: st[s] for s in ("parent", "new") if st[s]["duplicates"] or st[s]["nulls"]}
        if not bad:
            return None
        ks = ", ".join(f"k{i}" for i in range(len(cols)))
        out: dict[str, Any] = {"code": "key_invalid", "columns": cols, "sides": {}}
        for s, v in bad.items():
            idx = 0 if s == "parent" else 1
            e = ", ".join(f"{self.shared[c][idx]} AS k{i}" for i, c in enumerate(cols))
            rel = "_p" if s == "parent" else "_n"
            dups = self.con.execute(f"SELECT {ks}, count(*) FROM (SELECT {e} FROM {rel}) GROUP BY ALL HAVING count(*) > 1"
                                    f" ORDER BY count(*) DESC, {ks} LIMIT {limit}").fetchall()
            out["sides"][s] = {"duplicates": v["duplicates"], "nulls": v["nulls"],
                               "examples": [{"value": [None if x is None else str(x) for x in r[:-1]], "count": int(r[-1])}
                                            for r in dups]}
        out["message"] = ("Key " + ", ".join(cols) + " is not unique and present on both sides: " +
                          "; ".join(f"{s}: {v['duplicates']:,} repeated, {v['nulls']:,} missing" for s, v in out["sides"].items()) +
                          ". Pick another key or match by row content.")
        return out

    def _cmp(self, skip: tuple[str, ...] = ()) -> str:
        cs = [c for c in self.shared if c not in skip]
        if not cs:
            return "false"
        return " OR ".join(f"(p.k_{i} IS DISTINCT FROM n.k_{i})" for i, c in enumerate(cs) if c not in skip)

    def _cols_sel(self, idx: int, prefix: str, skip: tuple[str, ...] = ()) -> str:
        cs = [c for c in self.shared if c not in skip]
        return ", ".join(f"{self.shared[c][idx]} AS k_{i}" for i, c in enumerate(cs))

    def materialise(self, m: dict[str, Any], name: str = "_m") -> None:
        """Temp table `name` (__fr, pid, changed): each matched new row and the parent row id it keeps."""
        k = m["method"]
        if k == "none":
            self.con.execute(f"CREATE OR REPLACE TEMP TABLE {name} (__fr BIGINT, pid BIGINT, changed BOOLEAN)")
            return
        if k in ("key", "declared"):
            cols = m["columns"]
            skip = tuple(cols)
            on = " AND ".join(f"p.j{i} = n.j{i}" for i in range(len(cols)))
            pj = ", ".join(f"{self.shared[c][0]} AS j{i}" for i, c in enumerate(cols))
            nj = ", ".join(f"{self.shared[c][1]} AS j{i}" for i, c in enumerate(cols))
            pc, nc = self._cols_sel(0, "p", skip), self._cols_sel(1, "n", skip)
            self.con.execute(
                f"CREATE OR REPLACE TEMP TABLE {name} AS SELECT n.__fr, p.__row_id AS pid, ({self._cmp(skip)}) AS changed"
                f" FROM (SELECT __fr, {nj}{', ' + nc if nc else ''} FROM _n) n"
                f" JOIN (SELECT __row_id, {pj}{', ' + pc if pc else ''} FROM _p) p ON {on}")
            return
        if not self.shared:
            raise ValueError("The new file shares no column of a comparable type with the parent: nothing to match rows on.")
        pe = ", ".join(v[0] for v in self.shared.values())
        ne = ", ".join(v[1] for v in self.shared.values())
        self.con.execute(
            f"CREATE OR REPLACE TEMP TABLE {name} AS SELECT n.__fr, p.__row_id AS pid, false AS changed FROM"
            f" (SELECT __fr, h, row_number() OVER (PARTITION BY h ORDER BY __fr) AS o FROM (SELECT __fr, hash({ne}) AS h FROM _n)) n"
            f" JOIN (SELECT __row_id, h, row_number() OVER (PARTITION BY h ORDER BY __row_id) AS o FROM"
            f" (SELECT __row_id, hash({pe}) AS h FROM _p)) p USING (h, o)")

    def simulate(self, m: dict[str, Any]) -> dict[str, Any]:
        """matched / changed / added / removed / holdout_identity_lost for one method (§5.3 step 2)."""
        out: dict[str, Any] = {"method": m["method"], **({"columns": m["columns"]} if m.get("columns") else {}),
                               "label": method_label(m)}
        if m["method"] in ("key", "declared"):
            prob = self.key_problems(m["columns"])
            if prob:
                return {**out, "valid": False, "problem": prob}
        self.materialise(m, "_sim")
        matched, changed = self.con.execute("SELECT count(*), count(*) FILTER (WHERE changed) FROM _sim").fetchone()
        lost = self.con.execute("SELECT count(*) FROM (_h SEMI JOIN _p USING (__row_id)) h"
                                " ANTI JOIN _sim s ON s.pid = h.__row_id").fetchone()[0]
        self.con.execute("DROP TABLE IF EXISTS _sim")
        matched, changed, lost = int(matched), int(changed), int(lost)
        return {**out, "valid": True, "matched": matched, "changed": changed, "unchanged": matched - changed,
                "added": self.n_new - matched, "removed": self.n_parent - matched, "holdout_identity_lost": lost,
                "parent_holdout": self.n_holdout,
                "holdout_identity_lost_share": round(lost / self.n_holdout, 6) if self.n_holdout else 0.0,
                "matched_share": round(matched / self.n_new, 6) if self.n_new else 0.0}

    def warnings_for(self, chosen: dict[str, Any], best: dict[str, Any] | None) -> list[dict[str, Any]]:
        """identity_loss and better_key_available need `acknowledge: ["holdout_identity_loss"]`; low_overlap informs."""
        w: list[dict[str, Any]] = []
        if not chosen.get("valid"):
            return w
        lost, nh = chosen["holdout_identity_lost"], chosen["parent_holdout"]
        if lost and nh and lost / nh >= IDENTITY_LOSS_ACK:
            w.append({"code": "identity_loss", "needs_ack": True, "n": lost, "of": nh, "share": _dec(lost / nh),
                      "message": f"Matching by {chosen['label']} gives {lost:,} of the parent's {nh:,} holdout rows new ids "
                                 f"({lost / nh:.1%}): the holdout is redrawn for them, so runs on the two versions no longer pair "
                                 "on those rows."})
        if best and best.get("valid") and best is not chosen and self.n_new and \
                (best["matched"] - chosen["matched"]) / self.n_new >= BETTER_KEY_ACK and \
                not (chosen["method"] in ("key", "declared") and chosen.get("columns") == best.get("columns")):
            d = best["matched"] - chosen["matched"]
            w.append({"code": "better_key_available", "needs_ack": True, "columns": best.get("columns"), "n": d,
                      "share": _dec(d / self.n_new),
                      "message": f"Key {', '.join(best.get('columns') or [])} would match {d:,} more rows "
                                 f"({d / self.n_new:.1%} of the file) than {chosen['label']}."})
        if chosen["method"] != "none" and self.n_new and chosen["matched"] / self.n_new < LOW_OVERLAP:
            w.append({"code": "low_overlap", "needs_ack": False, "share": _dec(chosen["matched"] / self.n_new),
                      "message": f"Only {chosen['matched']:,} of {self.n_new:,} rows ({chosen['matched'] / self.n_new:.0%}) match "
                                 "the parent: is this the same dataset?"})
        return w

    # -- contract checks (schema level, DuckDB; warn, never block) ------------------------------------------------------

    def contract(self, target: str | None, row_drop: float = ROW_DROP_WARN) -> dict[str, Any]:
        """contract_checks() for the DuckDB path: target gone, type changed, new target category, rows dropped,
        column gone, "renamed?" hints (rename_hints' rule: same family, ≥ 95 % of the new distinct values are the
        old column's), target missing more often. Warnings only."""
        w: list[dict[str, Any]] = []
        added = [c for c in self.ntypes if c not in self.ptypes]
        removed = [c for c in self.ptypes if c not in self.ntypes]
        type_changes = [{"column": c, "from": self.ptypes[c], "to": t, "kind_from": type_family(self.ptypes[c]),
                         "kind_to": type_family(t)} for c, t in self.ntypes.items()
                        if c in self.ptypes and type_family(self.ptypes[c]) != type_family(t)]
        if target and target in self.ptypes and target not in self.ntypes:
            w.append({"code": "target_missing", "message": f"The target '{target}' is not in the new file: runs on this version need another target."})
        for t in type_changes:
            w.append({"code": "type_changed", "column": t["column"],
                      "message": f"'{t['column']}' changed type: {t['kind_from']} ({t['from']}) → {t['kind_to']} ({t['to']})."})
        if target and target in self.shared:
            pe, ne = self.shared[target]
            nd = self.con.execute(f"SELECT count(DISTINCT {pe}) FROM _p").fetchone()[0]
            if nd <= TARGET_MAX_CATS:
                new_cats = [str(r[0]) for r in self.con.execute(
                    f"SELECT DISTINCT {ne} AS v FROM _n WHERE v IS NOT NULL EXCEPT SELECT DISTINCT {pe} FROM _p ORDER BY 1 LIMIT 20").fetchall()]
                if new_cats:
                    w.append({"code": "new_target_category", "column": target, "values": new_cats,
                              "message": f"The target '{target}' has {len(new_cats)} new value(s): {', '.join(new_cats[:5])}. Models "
                                         "trained on the current version have never predicted them."})
            ma = self.con.execute(f"SELECT count(*) FILTER (WHERE {pe} IS NULL) FROM _p").fetchone()[0] / max(self.n_parent, 1)
            mb = self.con.execute(f"SELECT count(*) FILTER (WHERE {ne} IS NULL) FROM _n").fetchone()[0] / max(self.n_new, 1)
            if mb - ma > 0.05:
                w.append({"code": "target_missing_values", "column": target,
                          "message": f"The target is missing on {mb:.1%} of rows (was {ma:.1%})."})
        if self.n_parent and (self.n_parent - self.n_new) / self.n_parent > row_drop:
            w.append({"code": "rows_dropped", "message": f"{self.n_parent - self.n_new:,} fewer rows "
                                                         f"({(self.n_parent - self.n_new) / self.n_parent:.0%}; warning above "
                                                         f"{row_drop:.0%}): {self.n_parent:,} → {self.n_new:,}."})
        for c in removed:
            w.append({"code": "column_removed", "column": c, "message": f"'{c}' is gone: runs that used it can't be re-fitted on this version as they were."})
        renamed = []
        for b in added:
            best = None
            for a in removed:
                pr = _pair(b, self.ptypes[a], self.ntypes[b])
                if not pr or type_family(self.ptypes[a]) != type_family(self.ntypes[b]):
                    continue
                pa_ = pr[0].replace(_qi(b), _qi(a))
                nd, hit = self.con.execute(
                    f"SELECT count(*), count(*) FILTER (WHERE v IN (SELECT DISTINCT {pa_}::VARCHAR FROM _p)) FROM"
                    f" (SELECT DISTINCT {pr[1]}::VARCHAR AS v FROM _n WHERE {pr[1]} IS NOT NULL)").fetchone()
                ov = hit / nd if nd else 0.0
                if nd and ov >= 0.95 and (best is None or ov > best["_ov"]):
                    best = {"from": a, "to": b, "overlap": _dec(ov), "_ov": ov}
            if best:
                best.pop("_ov")
                renamed.append(best)
        for h in renamed:
            w.append({"code": "column_renamed_maybe", "column": h["from"], "to": h["to"], "overlap": h["overlap"],
                      "message": f"Was '{h['from']}' renamed to '{h['to']}'? Same type, and {float(h['overlap']):.0%} of the new "
                                 "column's values are the old column's. Nothing is renamed automatically: edit the file or the "
                                 "version if so."})
        return {"warnings": w, "columns_added": added, "columns_removed": removed, "type_changes": type_changes,
                "rows_before": self.n_parent, "rows_after": self.n_new, "row_drop_threshold": _dec(row_drop, 2),
                "blocking": False, "renamed_maybe": renamed}
