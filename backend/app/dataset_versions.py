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
        new_cats = sorted(set(new[target].dropna().astype(str)) - set(parent[target].dropna().astype(str)))
        if new_cats:
            w.append({"code": "new_target_category", "column": target, "values": new_cats[:20],
                      "message": f"The target '{target}' has {len(new_cats)} new value(s): {', '.join(new_cats[:5])}. Models trained on the "
                                 "current version have never predicted them."})
    if len(parent) and (len(parent) - len(new)) / len(parent) > row_drop:
        w.append({"code": "rows_dropped", "message": f"{len(parent) - len(new):,} fewer rows ({(len(parent) - len(new)) / len(parent):.0%}; "
                                                     f"warning above {row_drop:.0%}): {len(parent):,} → {len(new):,}."})
    for c in sd["columns_removed"]:
        w.append({"code": "column_removed", "column": c, "message": f"'{c}' is gone: runs that used it can't be re-fitted on this version as they were."})
    if target and target in parent.columns and target in new.columns:
        ma, mb = float(parent[target].isna().mean()), float(new[target].isna().mean())
        if mb - ma > 0.05:
            w.append({"code": "target_missing_values", "column": target,
                      "message": f"The target is missing on {mb:.1%} of rows (was {ma:.1%})."})
    return {"warnings": w, "columns_added": sd["columns_added"], "columns_removed": sd["columns_removed"],
            "type_changes": sd["type_changes"], "rows_before": int(len(parent)), "rows_after": int(len(new)),
            "row_drop_threshold": row_drop, "blocking": False}


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
