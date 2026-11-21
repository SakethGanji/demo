"""DL3: edit a dataset version as a list of typed operations. Nothing here writes: `apply_ops` returns the new frame, each
row's position in the version it came from (or -1 for a new row) and a change log. `DatasetStore.edit_version` turns
that into a NEW version through `derive()` (lineage, root rows, holdout carried over, audit); the version edited is
never modified in place.

Operations (each a dict with "op"):
    set_cells       {cells: [{row, column, value}]}               row = the row's position (`_row`) in the version edited
    delete_rows     {rows: [row, …]} or {where: <filter>}
    filter_rows     {where: <filter>}                              keep only the matching rows
    add_rows        {rows: [{column: value, …}]}
    rename_column   {column, to}
    drop_column     {column}
    add_column      {name, value} or {name, formula}               formula: a safe arithmetic expression, see FORMULA
    cast_column     {column, to: number | integer | text | bool}
    replace_values  {column, find, replace, match: exact | contains}
    fill_missing    {column, strategy: constant | mean | median | mode, value?}

A filter is {column, cmp, value} (cmp: eq ne lt le gt ge contains in is_missing not_missing), or a list of them (all
must hold). Every mistake raises EditError, shown to the user as a 400."""

from __future__ import annotations

import ast
import math
import re
from typing import Any

import numpy as np
import pandas as pd

MAX_OPS = 200
MAX_CELLS = 5000
MAX_ADD_ROWS = 1000
EXAMPLES_PER_OP = 20
FORMULA_MAX_LEN = 300
FORMULA_MAX_NODES = 60
FUNCS = {"abs": np.abs, "sqrt": np.sqrt, "log": np.log, "log1p": np.log1p, "exp": np.exp, "round": np.round,
         "min": np.fmin, "max": np.fmax}
FUNC_ARITY = {"abs": 1, "sqrt": 1, "log": 1, "log1p": 1, "exp": 1, "round": (1, 2), "min": 2, "max": 2}
FN_SHORTCUTS = {"ratio": "/", "difference": "-", "sum": "+", "product": "*"}
CMPS = ("eq", "ne", "lt", "le", "gt", "ge", "contains", "in", "is_missing", "not_missing")
OPS = ("set_cells", "delete_rows", "filter_rows", "add_rows", "rename_column", "drop_column", "add_column", "cast_column",
       "replace_values", "fill_missing")
FORMULA_HELP = ("a formula uses column names (in `backticks` when they have spaces), numbers, + - * / and ( ), and "
                "abs, sqrt, log, log1p, exp, round, min, max: e.g. loan_amount / income or log1p(income)")


class EditError(ValueError):
    """A bad operation; the message says which one and why."""


def _is_num(s: pd.Series) -> bool:
    return s.dtype.kind in "iuf"


def _cell(v: Any) -> Any:
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(v, float) and not math.isfinite(v):
        return None
    return v.item() if hasattr(v, "item") else v


def _coerce(value: Any, s: pd.Series, where: str) -> Any:
    """A value for column `s`: '' / None → missing; numbers for a number column (a text value there is refused: cast
    the column to text first); bools for a bool column; else text."""
    if value is None or (isinstance(value, str) and value.strip() == ""):
        return np.nan
    if _is_num(s):
        if isinstance(value, bool):
            raise EditError(f"{where}: {value!r} is not a number, and '{s.name}' is a number column.")
        try:
            f = float(str(value).replace(",", "")) if isinstance(value, str) else float(value)
        except (TypeError, ValueError):
            raise EditError(f"{where}: {value!r} is not a number, and '{s.name}' is a number column (cast it to text first "
                            "if it should hold text).") from None
        if not math.isfinite(f):
            raise EditError(f"{where}: {value!r} is not a finite number.")
        return f
    if s.dtype.kind == "b":
        t = str(value).strip().lower()
        if t in ("true", "1", "yes"):
            return True
        if t in ("false", "0", "no"):
            return False
        raise EditError(f"{where}: {value!r} is not true or false, and '{s.name}' is a true/false column.")
    if isinstance(value, (dict, list)):
        raise EditError(f"{where}: a cell holds one value, not {type(value).__name__}.")
    return str(value)


def _col(df: pd.DataFrame, c: Any, where: str) -> str:
    if not isinstance(c, str) or c not in df.columns:
        raise EditError(f"{where}: no column {c!r} (columns: {', '.join(map(str, list(df.columns)[:12]))}{'…' if df.shape[1] > 12 else ''}).")
    return c


def _mask(df: pd.DataFrame, where: Any, ctx: str) -> pd.Series:
    conds = where if isinstance(where, list) else [where]
    if not conds or not all(isinstance(c, dict) for c in conds):
        raise EditError(f"{ctx}: 'where' is {{column, cmp, value}} or a list of them.")
    m = pd.Series(True, index=df.index)
    for c in conds:
        col = _col(df, c.get("column"), ctx)
        cmp = c.get("cmp", "eq")
        if cmp not in CMPS:
            raise EditError(f"{ctx}: cmp is one of {', '.join(CMPS)}, not {cmp!r}.")
        s = df[col]
        v = c.get("value")
        if cmp == "is_missing":
            m &= s.isna()
            continue
        if cmp == "not_missing":
            m &= s.notna()
            continue
        if cmp == "contains":
            m &= s.astype("string").str.contains(str(v), case=False, regex=False).fillna(False).astype(bool)
            continue
        if cmp == "in":
            if not isinstance(v, list) or not v:
                raise EditError(f"{ctx}: 'in' takes a non-empty list of values.")
            vals = [_coerce(x, s, ctx) for x in v]
            m &= s.isin(vals) if _is_num(s) else s.astype("string").isin([str(x) for x in vals])
            continue
        x = _coerce(v, s, ctx)
        if isinstance(x, float) and math.isnan(x):
            raise EditError(f"{ctx}: compare with a value, or use is_missing / not_missing.")
        if cmp in ("lt", "le", "gt", "ge") and not _is_num(s):
            raise EditError(f"{ctx}: '{cmp}' compares numbers, and '{col}' is not a number column.")
        lhs = s if _is_num(s) or s.dtype.kind == "b" else s.astype("string")
        r = {"eq": lhs == x, "ne": lhs != x, "lt": lhs < x, "le": lhs <= x, "gt": lhs > x, "ge": lhs >= x}[cmp]
        m &= pd.Series(r, index=df.index).fillna(cmp == "ne").astype(bool)
    return m


# -- formula: parsed with ast, evaluated by walking the tree (never eval) -------------------------------------------------

def _formula(df: pd.DataFrame, expr: str, ctx: str) -> tuple[pd.Series, list[str]]:
    if not isinstance(expr, str) or not expr.strip():
        raise EditError(f"{ctx}: the formula is empty; {FORMULA_HELP}.")
    if len(expr) > FORMULA_MAX_LEN:
        raise EditError(f"{ctx}: the formula is longer than {FORMULA_MAX_LEN} characters.")
    names: dict[str, str] = {}

    def sub(m: re.Match) -> str:
        k = f"__c{len(names)}"
        names[k] = m.group(1)
        return k

    src = re.sub(r"`([^`]+)`", sub, expr)
    try:
        tree = ast.parse(src, mode="eval")
    except SyntaxError:
        raise EditError(f"{ctx}: can't read the formula {expr!r}; {FORMULA_HELP}.") from None
    nodes = list(ast.walk(tree))
    if len(nodes) > FORMULA_MAX_NODES:
        raise EditError(f"{ctx}: the formula is too long ({len(nodes)} parts; at most {FORMULA_MAX_NODES}).")
    used: list[str] = []

    def ev(n: ast.AST) -> Any:
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)) and not isinstance(n.value, bool):
            return float(n.value)
        if isinstance(n, ast.Name):
            c = names.get(n.id, n.id)
            if c not in df.columns:
                raise EditError(f"{ctx}: no column {c!r} in the formula (put names with spaces in `backticks`).")
            if not _is_num(df[c]):
                raise EditError(f"{ctx}: '{c}' is not a number column; a formula uses number columns only (cast it first).")
            used.append(c)
            return df[c].astype(float)
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, (ast.USub, ast.UAdd)):
            v = ev(n.operand)
            return -v if isinstance(n.op, ast.USub) else v
        if isinstance(n, ast.BinOp) and isinstance(n.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)):
            a, b = ev(n.left), ev(n.right)
            with np.errstate(divide="ignore", invalid="ignore"):
                if isinstance(n.op, ast.Add):
                    return a + b
                if isinstance(n.op, ast.Sub):
                    return a - b
                if isinstance(n.op, ast.Mult):
                    return a * b
                return a / b
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in FUNCS and not n.keywords:
            ar = FUNC_ARITY[n.func.id]
            ok = len(n.args) in (ar if isinstance(ar, tuple) else (ar,))
            if not ok:
                raise EditError(f"{ctx}: {n.func.id}() takes {ar if not isinstance(ar, tuple) else ' or '.join(map(str, ar))} argument(s).")
            args = [ev(x) for x in n.args]
            if n.func.id == "round" and len(args) == 2:
                if not isinstance(args[1], float):
                    raise EditError(f"{ctx}: round(x, digits) takes a number of digits.")
                return np.round(args[0], int(args[1]))
            with np.errstate(divide="ignore", invalid="ignore"):
                return FUNCS[n.func.id](*args)
        what = type(n).__name__
        if isinstance(n, ast.Call):
            what = f"the function {getattr(n.func, 'id', '?')}()"
        elif isinstance(n, ast.BinOp):
            what = f"the operator {type(n.op).__name__}"
        raise EditError(f"{ctx}: {what} is not allowed in a formula; {FORMULA_HELP}.")

    out = ev(tree)
    if not isinstance(out, pd.Series):
        out = pd.Series(float(out), index=df.index)
    out = out.replace([np.inf, -np.inf], np.nan)
    return out, used


# -- apply ------------------------------------------------------------------------------------------------------------

def _rid(i: Any) -> Any:
    i = int(i)
    return i if i >= 0 else f"new-{-i}"


def _rows_param(df: pd.DataFrame, rows: Any, ctx: str) -> list[int]:
    if not isinstance(rows, list) or not rows:
        raise EditError(f"{ctx}: 'rows' is a non-empty list of row ids (_row).")
    out = []
    for r in rows:
        if isinstance(r, bool) or not isinstance(r, int):
            raise EditError(f"{ctx}: row id {r!r} is not a whole number (the grid's _row).")
        if r not in df.index:
            raise EditError(f"{ctx}: row {r} is not in the data (deleted by an earlier operation, or past the last row).")
        out.append(r)
    return out


def apply_ops(src: pd.DataFrame, ops: Any, target: str | None = None) -> dict[str, Any]:
    """The ops applied in order to a copy of `src` (rows indexed by their position in `src`). Returns {frame (index reset),
    row_map (position in src or -1 per row), log (one entry per op), renamed, target (after renames / drops)}."""
    if not isinstance(ops, list) or not ops:
        raise EditError("Send a non-empty list of operations: [{\"op\": \"set_cells\", …}, …].")
    if len(ops) > MAX_OPS:
        raise EditError(f"At most {MAX_OPS} operations per version ({len(ops)} sent).")
    df = src.copy()
    df.index = pd.RangeIndex(len(df))
    next_new = -1
    log: list[dict[str, Any]] = []
    renamed: dict[str, str] = {}
    tgt = target
    n_cells = 0
    for i, op in enumerate(ops):
        if not isinstance(op, dict) or op.get("op") not in OPS:
            raise EditError(f"Operation {i + 1}: 'op' is one of {', '.join(OPS)}.")
        kind = op["op"]
        ctx = f"Operation {i + 1} ({kind})"
        ex: list[dict[str, Any]] = []
        entry: dict[str, Any] = {"i": i + 1, "op": kind}

        def note(row: Any, column: str, old: Any, new: Any) -> None:
            if len(ex) < EXAMPLES_PER_OP:
                ex.append({"row": _rid(row), "column": column, "old": _cell(old), "new": _cell(new)})

        if kind == "set_cells":
            cells = op.get("cells")
            if not isinstance(cells, list) or not cells:
                raise EditError(f"{ctx}: 'cells' is a non-empty list of {{row, column, value}}.")
            n_cells += len(cells)
            if n_cells > MAX_CELLS:
                raise EditError(f"At most {MAX_CELLS:,} cell edits per version; for more, use replace_values, fill_missing or "
                                "upload the file as a new version.")
            changed, rows = 0, set()
            for c in cells:
                if not isinstance(c, dict):
                    raise EditError(f"{ctx}: each cell is {{row, column, value}}.")
                col = _col(df, c.get("column"), ctx)
                (r,) = _rows_param(df, [c.get("row")], ctx)
                new = _coerce(c.get("value"), df[col], f"{ctx}, row {r}, '{col}'")
                old = df.at[r, col]
                if _cell(old) == _cell(new) or (_cell(old) is None and _cell(new) is None):
                    continue
                if isinstance(new, str) and df[col].dtype.kind != "O":
                    df[col] = df[col].astype(object)
                df.at[r, col] = new
                changed += 1
                rows.add(r)
                note(r, col, old, new)
            entry.update(cells=changed, rows=len(rows), summary=f"set {changed} cell(s) in {len(rows)} row(s)")
        elif kind in ("delete_rows", "filter_rows"):
            if kind == "delete_rows" and "rows" in op:
                drop = _rows_param(df, op["rows"], ctx)
                how = f"{len(set(drop))} row(s) by id"
            else:
                if "where" not in op:
                    raise EditError(f"{ctx}: send 'where' (a filter){' or rows' if kind == 'delete_rows' else ''}.")
                m = _mask(df, op["where"], ctx)
                drop = list(df.index[~m] if kind == "filter_rows" else df.index[m])
                how = f"{len(drop)} row(s) where {_describe(op['where'])}" if kind == "delete_rows" else \
                    f"kept rows where {_describe(op['where'])}: {len(drop)} removed"
            drop = sorted(set(drop))
            for r in drop[:EXAMPLES_PER_OP]:
                ex.append({"row": _rid(r), "deleted": {k: _cell(v) for k, v in list(df.loc[r].items())[:6]}})
            df = df.drop(index=drop)
            if df.empty:
                raise EditError(f"{ctx}: this removes every row.")
            entry.update(rows=len(drop), summary=how)
        elif kind == "add_rows":
            rows = op.get("rows")
            if not isinstance(rows, list) or not rows or not all(isinstance(r, dict) for r in rows):
                raise EditError(f"{ctx}: 'rows' is a non-empty list of {{column: value}}.")
            if len(rows) > MAX_ADD_ROWS:
                raise EditError(f"{ctx}: at most {MAX_ADD_ROWS} rows; upload a file as a new version for more.")
            new_rows = []
            for j, r in enumerate(rows):
                for k in r:
                    _col(df, k, f"{ctx}, new row {j + 1}")
                new_rows.append({c: _coerce(r.get(c), df[c], f"{ctx}, new row {j + 1}, '{c}'") for c in df.columns})
            idx = list(range(next_new, next_new - len(rows), -1))
            next_new -= len(rows)
            add = pd.DataFrame(new_rows, index=idx, columns=df.columns)
            for c in df.columns:
                if df[c].dtype.kind in "iuf":
                    add[c] = pd.to_numeric(add[c], errors="coerce")
            df = pd.concat([df, add])
            for k, r in zip(idx, new_rows):
                if len(ex) < EXAMPLES_PER_OP:
                    ex.append({"row": _rid(k), "added": {c: _cell(v) for c, v in list(r.items())[:6]}})
            entry.update(rows=len(rows), summary=f"added {len(rows)} row(s)")
        elif kind == "rename_column":
            col = _col(df, op.get("column"), ctx)
            to = op.get("to")
            if not isinstance(to, str) or not to.strip() or len(to.strip()) > 120:
                raise EditError(f"{ctx}: 'to' is the new name (1–120 characters).")
            to = to.strip()
            if to in df.columns:
                raise EditError(f"{ctx}: there is already a column named {to!r}.")
            df = df.rename(columns={col: to})
            renamed[col] = to
            if tgt == col:
                tgt = to
            entry.update(column=col, to=to, summary=f"renamed '{col}' → '{to}'")
        elif kind == "drop_column":
            col = _col(df, op.get("column"), ctx)
            if df.shape[1] <= 2:
                raise EditError(f"{ctx}: a dataset keeps at least two columns.")
            df = df.drop(columns=[col])
            if tgt == col:
                tgt = None
            entry.update(column=col, summary=f"dropped '{col}'")
        elif kind == "add_column":
            name = op.get("name")
            if not isinstance(name, str) or not name.strip() or len(name.strip()) > 120:
                raise EditError(f"{ctx}: 'name' is the new column's name (1–120 characters).")
            name = name.strip()
            if name in df.columns:
                raise EditError(f"{ctx}: there is already a column named {name!r}.")
            formula = op.get("formula")
            fn = op.get("fn")
            if fn is not None:
                if fn not in FN_SHORTCUTS:
                    raise EditError(f"{ctx}: fn is one of {', '.join(FN_SHORTCUTS)}.")
                a, b = op.get("a"), op.get("b")
                if not isinstance(a, str) or not isinstance(b, str):
                    raise EditError(f"{ctx}: {fn} takes two columns, a and b.")
                formula = f"`{a}` {FN_SHORTCUTS[fn]} `{b}`"
            if formula is not None:
                vals, used = _formula(df, formula, ctx)
                df[name] = vals
                entry.update(column=name, formula=formula, uses=sorted(set(used)), missing=int(vals.isna().sum()),
                             summary=f"added '{name}' = {formula}" + (f" ({int(vals.isna().sum())} missing: missing inputs or ÷0)" if vals.isna().any() else ""))
            elif "value" in op:
                v = op["value"]
                if isinstance(v, (dict, list)):
                    raise EditError(f"{ctx}: 'value' is one value.")
                if isinstance(v, str):
                    try:
                        v = float(v.replace(",", "")) if v.strip() else np.nan
                    except ValueError:
                        pass
                df[name] = v
                entry.update(column=name, value=_cell(v), summary=f"added '{name}' = {v!r} on every row")
            else:
                raise EditError(f"{ctx}: send 'value' (a constant), 'formula', or fn with a and b.")
            entry["rows"] = int(len(df))
        elif kind == "cast_column":
            col = _col(df, op.get("column"), ctx)
            to = op.get("to")
            s = df[col]
            if to == "number" or to == "integer":
                t = s.astype("string").str.replace(",", "", regex=False).str.strip() if not _is_num(s) else s
                new = pd.to_numeric(t, errors="coerce")
                if to == "integer":
                    new = new.round()
            elif to == "text":
                new = s.map(lambda v: None if _cell(v) is None else (str(int(v)) if isinstance(v, float) and v.is_integer() else str(v))).astype(object)
            elif to == "bool":
                t = s.astype("string").str.strip().str.lower()
                new = t.map({"true": True, "1": True, "1.0": True, "yes": True, "false": False, "0": False, "0.0": False, "no": False})
            else:
                raise EditError(f"{ctx}: 'to' is number, integer, text or bool.")
            lost = s.notna() & pd.Series(new, index=s.index).isna()
            for r in list(df.index[lost])[:EXAMPLES_PER_OP]:
                note(r, col, s.at[r], None)
            df[col] = new
            entry.update(column=col, to=to, became_missing=int(lost.sum()),
                         summary=f"cast '{col}' to {to}" + (f": {int(lost.sum())} value(s) could not be read and are now missing" if lost.any() else ""))
        elif kind == "replace_values":
            col = _col(df, op.get("column"), ctx)
            match = op.get("match", "exact")
            if match not in ("exact", "contains"):
                raise EditError(f"{ctx}: match is exact or contains.")
            if "find" not in op or op.get("find") in (None, ""):
                raise EditError(f"{ctx}: say what to find ('find').")
            s = df[col]
            if match == "contains":
                if _is_num(s):
                    raise EditError(f"{ctx}: 'contains' replaces text; '{col}' is a number column (use exact).")
                t = s.astype("string")
                new = t.str.replace(str(op["find"]), str(op.get("replace") or ""), regex=False)
                hit = (t != new).fillna(False).astype(bool)
                new = new.astype(object).where(s.notna(), None)
            else:
                f = _coerce(op["find"], s, ctx)
                rep = _coerce(op.get("replace"), s, ctx)
                hit = (s == f) if _is_num(s) else (s.astype("string") == str(f)).fillna(False).astype(bool)
                new = s.copy()
                if isinstance(rep, str) and new.dtype.kind != "O":
                    new = new.astype(object)
                new[hit] = rep
            for r in list(df.index[hit])[:EXAMPLES_PER_OP]:
                note(r, col, s.at[r], new.at[r])
            df[col] = new
            entry.update(column=col, cells=int(hit.sum()), rows=int(hit.sum()),
                         summary=f"replaced {op['find']!r} → {op.get('replace')!r} in '{col}' ({match}): {int(hit.sum())} cell(s)")
        elif kind == "fill_missing":
            col = _col(df, op.get("column"), ctx)
            strat = op.get("strategy", "constant")
            s = df[col]
            miss = s.isna()
            if strat == "constant":
                if "value" not in op:
                    raise EditError(f"{ctx}: send 'value' for a constant fill.")
                v = _coerce(op["value"], s, ctx)
            elif strat in ("mean", "median"):
                if not _is_num(s):
                    raise EditError(f"{ctx}: {strat} needs a number column; '{col}' is not one.")
                v = float(getattr(s, strat)())
            elif strat == "mode":
                m = s.mode(dropna=True)
                if m.empty:
                    raise EditError(f"{ctx}: '{col}' has no values to take the mode of.")
                v = m.iloc[0]
            else:
                raise EditError(f"{ctx}: strategy is constant, mean, median or mode.")
            if v is None or (isinstance(v, float) and math.isnan(v)):
                raise EditError(f"{ctx}: the fill value is missing itself.")
            for r in list(df.index[miss])[:EXAMPLES_PER_OP]:
                note(r, col, None, v)
            if isinstance(v, str) and s.dtype.kind != "O":
                df[col] = s.astype(object)
            df.loc[miss, col] = v
            entry.update(column=col, strategy=strat, value=_cell(v), cells=int(miss.sum()), rows=int(miss.sum()),
                         summary=f"filled {int(miss.sum())} missing value(s) in '{col}' with {strat}" + ("" if strat == "constant" else f" ({_cell(v)!r})"))
        entry["examples"] = ex
        entry["examples_truncated"] = bool((entry.get("cells") or entry.get("rows") or 0) > len(ex)) if ex else False
        log.append(entry)
    rmap = [int(i) if int(i) >= 0 else -1 for i in df.index]
    return {"frame": df.reset_index(drop=True), "row_map": rmap, "log": log, "renamed": renamed, "target": tgt}


def _describe(where: Any) -> str:
    conds = where if isinstance(where, list) else [where]
    sym = {"eq": "=", "ne": "≠", "lt": "<", "le": "≤", "gt": ">", "ge": "≥"}
    parts = []
    for c in conds:
        cmp = c.get("cmp", "eq")
        if cmp in ("is_missing", "not_missing"):
            parts.append(f"{c.get('column')} {cmp.replace('_', ' ')}")
        else:
            parts.append(f"{c.get('column')} {sym.get(cmp, cmp)} {c.get('value')!r}")
    return " and ".join(parts)
