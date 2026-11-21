"""DL3 / P07: edit a dataset version as a list of typed operations, **compiled into one DuckDB query** over the parent
table (plan §5.5). Nothing here writes: `compile_ops` builds a chain of temp views `_e0 … _eN` on a DuckDB connection
(`_e0` = the parent table with `__row_id`; `_eN` = the edited table) and returns a change log, the renames, the target
after the ops and the type changes. `DatasetStore.edit_version` hands `_eN` to the VersionWriter, which writes it as the
edited table of a NEW version (the other tables are carried); the version edited is never modified in place.

Operations (each a dict with "op"):
    set_cells       {cells: [{row_id | row, column, value}]}     row_id = __row_id; row = position (`_row`, one release)
    delete_rows     {rows: [row, …]} | {row_ids: [...]} | {where: <filter>}
    filter_rows     {where: <filter>}                              keep only the matching rows
    add_rows        {rows: [{column: value, …}]}                   new rows get next_row_id + k
    rename_column   {column, to}
    drop_column     {column}
    add_column      {name, value} or {name, formula}               formula: a safe arithmetic expression → SQL
    cast_column     {column, to: number | integer | text | bool}   TRY_CAST, with the count of values that became missing
    replace_values  {column, find, replace, match: exact | contains}
    fill_missing    {column, strategy: constant | mean | median | mode, value?}

How each compiles (§5.5): set_cells → a VALUES table joined on `__row_id`; delete_rows / filter_rows → a WHERE from the
whitelisted filter AST; add_rows → `UNION ALL BY NAME` with new ids; the column ops → an explicit select list.

A filter is {column, cmp, value} (cmp: eq ne lt le gt ge contains in is_missing not_missing), or a list of them (all
must hold). Every mistake raises EditError, shown to the user as a 400.

P03 (plan §4.4, DLA4 #1): every value is checked against its column's type by `lake.values` before any SQL is compiled
(BIGINT: whole numbers in int64; DOUBLE: finite; …), a number column must keep finite values and a finite sum and mean, a
formula's non-finite result is refused (÷0 is missing, as in SQL), and an engine error inside an op is a typed 400
(`edit_invalid`) whose engine text goes to the log only. Those refusals are `values.ValueRefused` (a 400 with a code).
`_coerce` (pandas) stays for Data Doctor's value checks."""

from __future__ import annotations

import ast
import logging
import math
import re
from typing import Any

import numpy as np
import pandas as pd

from .lake import values as V
from .lake.values import ValueRefused
from .lake.writer import is_reserved_name

log_ = logging.getLogger("ensemble.edit")

MAX_OPS = 200
MAX_CELLS = 5000
MAX_ADD_ROWS = 1000  # DL4 (m-f): per version (all add_rows ops together), not per op
ROUND_DIGITS = (-15, 15)  # DL4 (M-a): round(x, digits) with digits in this range
EXAMPLES_PER_OP = 20
FORMULA_MAX_LEN = 300
FORMULA_MAX_NODES = 60
FUNCS = ("abs", "sqrt", "log", "log1p", "exp", "round", "min", "max")
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


def _type_of(s: pd.Series, kind: str | None = None) -> str:
    """The column's type for value checks: an integer column (int dtype, or schema kind 'integer' while it has missing
    cells) is BIGINT, other numbers DOUBLE, bools BOOLEAN, the rest VARCHAR."""
    if s.dtype.kind == "b":
        return "BOOLEAN"
    if s.dtype.kind in "iu" or (kind == "integer" and s.dtype.kind == "f"):
        return "BIGINT"
    if s.dtype.kind == "f":
        return "DOUBLE"
    return "VARCHAR"


#: an integer column with missing cells is held as float64 on the CSV store (data.csv reads it back that way), so only
#: whole numbers that float64 holds exactly survive the round trip
FLOAT_INT_MAX = 2 ** 53


def _coerce(value: Any, s: pd.Series, where: str, *, kind: str | None = None, op_index: int | None = None) -> Any:
    """A value for column `s`: '' / None → missing; checked against the column's type (lake.values: a whole number in
    int64 for an integer column, a finite number for a number column; a text value there is refused: cast the column
    to text first); bools for a bool column; else text."""
    if value is None or (isinstance(value, str) and value.strip() == ""):
        return np.nan
    t = _type_of(s, kind)
    col = str(s.name)
    if t in ("BIGINT", "DOUBLE"):
        if isinstance(value, (bool, np.bool_)):
            raise ValueRefused("value_invalid", f"{where}: {value!r} is not a number, and '{col}' is a number column.",
                               op_index=op_index, column=col, type=t)
        try:
            v = V.check(value, t, op_index=op_index, column=col)
        except ValueRefused as e:
            if e.code == "value_invalid" and isinstance(value, str):
                e.payload["message"] = (f"{where}: {value[:40]!r} is not a {'whole ' if t == 'BIGINT' else ''}number, and '{col}' is a "
                                        f"{'whole-number' if t == 'BIGINT' else 'number'} column (cast it to text first if it should hold text).")
            else:
                e.payload["message"] = f"{where}: {e.payload['message'].split(': ', 1)[-1]}"
            raise
        if t == "BIGINT" and s.dtype.kind == "f" and abs(v) > FLOAT_INT_MAX:
            raise ValueRefused("value_out_of_range", f"{where}: {value!r} is out of range; '{col}' has missing cells, so it holds "
                                                     f"whole numbers from -2^53 to 2^53.", op_index=op_index, column=col, type=t,
                               min=str(-FLOAT_INT_MAX), max=str(FLOAT_INT_MAX))
        return v
    if t == "BOOLEAN":
        try:
            return V.check(value, "BOOLEAN", op_index=op_index, column=col)
        except ValueRefused as e:
            e.payload["message"] = f"{where}: {value!r} is not true or false, and '{col}' is a true/false column."
            raise
    if isinstance(value, (dict, list)):
        raise EditError(f"{where}: a cell holds one value, not {type(value).__name__}.")
    return V.check(value, "VARCHAR", op_index=op_index, column=col)


def edit_invalid(op_index: int | None, op: str | None, column: str | None) -> ValueRefused:
    where = f"Operation {op_index} ({op})" if op_index else "These operations"
    return ValueRefused("edit_invalid", f"{where}{f' on {column!r}' if column else ''} can't be applied to this data: a value does "
                                        "not fit its column's type or range. Check the values and types in the operation.",
                        op_index=op_index, column=column)



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


# =====================================================================================================================
# P07 (plan §5.5): the SQL compiler
# =====================================================================================================================

INT_TYPES = ("BIGINT", "INTEGER", "SMALLINT", "TINYINT", "HUGEINT", "UBIGINT", "UINTEGER", "USMALLINT", "UTINYINT")
NUM_TYPES = INT_TYPES + ("DOUBLE", "FLOAT", "REAL")
#: the largest |x| exp() takes before it overflows a DOUBLE (numpy gives inf there; refused as non_finite_result)
EXP_MAX = 709.782712893384
CAST_KIND = {"number": "DOUBLE", "integer": "BIGINT", "text": "VARCHAR", "bool": "BOOLEAN"}
KIND_OF = {"DOUBLE": "number", "BIGINT": "integer", "INTEGER": "integer", "VARCHAR": "text", "BOOLEAN": "bool",
           "DATE": "date", "TIMESTAMP": "timestamp"}


def q(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def vtype(t: str) -> str:
    """The lake.values type a DuckDB column type is checked as."""
    t = (t or "").upper()
    if t in ("BIGINT", "HUGEINT", "UBIGINT", "UINTEGER"):
        return "BIGINT"
    if t in ("INTEGER", "SMALLINT", "TINYINT", "USMALLINT", "UTINYINT"):
        return "INTEGER"
    if t in ("DOUBLE", "FLOAT", "REAL") or t.startswith("DECIMAL") or t.startswith("NUMERIC"):
        return "DOUBLE"
    if t == "BOOLEAN":
        return "BOOLEAN"
    if t == "DATE":
        return "DATE"
    if t.startswith("TIMESTAMP"):
        return "TIMESTAMP"
    return "VARCHAR"


def is_num(t: str) -> bool:
    return vtype(t) in ("BIGINT", "INTEGER", "DOUBLE")


def lit(v: Any, t: str) -> str:
    """A checked value (lake.values.check's output) as a typed SQL literal. Only values that passed `check` get here."""
    vt = vtype(t)
    if v is None:
        return f"NULL::{t}"
    if vt in ("BIGINT", "INTEGER"):
        return f"{int(v)}::{t}"
    if vt == "DOUBLE":
        f = float(v)
        if not math.isfinite(f):
            raise ValueRefused("value_out_of_range", "A value is not a finite number.")
        return f"{f!r}::DOUBLE"
    if vt == "BOOLEAN":
        return "TRUE" if v else "FALSE"
    if vt in ("DATE", "TIMESTAMP"):
        from datetime import datetime, timezone

        s = str(v)
        if vt == "TIMESTAMP":
            d = datetime.fromisoformat(s)
            if d.tzinfo is not None:
                d = d.astimezone(timezone.utc).replace(tzinfo=None)
            s = d.isoformat(sep=" ")
        return f"{vt} '" + s.replace("'", "''") + "'"
    return "'" + str(v).replace("'", "''") + "'"


def _jsonable(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, float) and not math.isfinite(v):
        return None
    if hasattr(v, "isoformat"):
        return v.isoformat()
    if isinstance(v, (bytes, bytearray)):
        return v.hex()
    if hasattr(v, "item"):
        return v.item()
    if not isinstance(v, (str, int, float, bool)):
        return str(v)
    return v


class Compiled:
    """The result of `compile_ops`: the final view and what happened on the way."""

    def __init__(self) -> None:
        self.view = "_e0"
        self.cols: list[list[str]] = []           # [[name, duckdb type]] in order (no __row_id)
        self.log: list[dict[str, Any]] = []
        self.renamed: dict[str, str] = {}
        self.target: str | None = None
        self.target_touched: dict[str, Any] | None = None
        self.casts: dict[str, str] = {}           # column → the kind a cast_column gave it
        self.widened: dict[str, str] = {}         # column → type, when a fill widened an integer column
        self.n_added = 0
        self.next_row_id = 0


class _C:
    """Compiler state over one DuckDB connection."""

    def __init__(self, con: Any, base: str, target: str | None, next_row_id: int) -> None:
        self.con = con
        self.k = 0
        self.out = Compiled()
        self.out.view = base
        self.out.target = target
        self.out.next_row_id = int(next_row_id)
        self.out.cols = [[r[0], r[1]] for r in con.execute(f"DESCRIBE SELECT * FROM {base}").fetchall() if r[0] != "__row_id"]
        self.base = base
        self._pos_ready = False
        self.n_cells = 0

    # -- plumbing -------------------------------------------------------------------------------------------------

    @property
    def cur(self) -> str:
        return self.out.view

    def names(self) -> list[str]:
        return [c[0] for c in self.out.cols]

    def type_of(self, c: str) -> str:
        return dict((n, t) for n, t in self.out.cols)[c]

    def view(self, sql: str) -> str:
        self.k += 1
        name = f"_e{self.k}"
        self.con.execute(f"CREATE OR REPLACE TEMP VIEW {name} AS {sql}")
        self.out.view = name
        return name

    def one(self, sql: str, params: list[Any] | None = None) -> Any:
        return self.con.execute(sql, params or []).fetchone()

    def col(self, c: Any, ctx: str) -> str:
        names = self.names()
        if not isinstance(c, str) or c not in names:
            raise EditError(f"{ctx}: no column {c!r} (columns: {', '.join(names[:12])}{'…' if len(names) > 12 else ''}).")
        return c

    def select(self, repl: dict[str, str] | None = None, drop: set[str] | None = None, rename: dict[str, str] | None = None,
               add: list[tuple[str, str]] | None = None, src: str = "s") -> str:
        """`__row_id, <columns>` with replacements / drops / renames / additions, in order."""
        repl, drop, rename = repl or {}, drop or set(), rename or {}
        parts = [f"{src}.__row_id"]
        for n, _ in self.out.cols:
            if n in drop:
                continue
            expr = repl.get(n, f"{src}.{q(n)}")
            parts.append(f"{expr} AS {q(rename.get(n, n))}")
        for n, expr in add or []:
            parts.append(f"{expr} AS {q(n)}")
        return ", ".join(parts)

    def value(self, value: Any, col: str, t: str, where: str, op_index: int) -> Any:
        """A value for column `col` of type `t`: '' / None → missing; checked by lake.values (DL3's messages)."""
        if value is None or (isinstance(value, str) and value.strip() == ""):
            return None
        vt = vtype(t)
        if vt in ("BIGINT", "INTEGER", "DOUBLE"):
            if isinstance(value, (bool, np.bool_)):
                raise ValueRefused("value_invalid", f"{where}: {value!r} is not a number, and '{col}' is a number column.",
                                   op_index=op_index, column=col, type=vt)
            try:
                return V.check(value, vt, op_index=op_index, column=col)
            except ValueRefused as e:
                if e.code == "value_invalid" and isinstance(value, str):
                    whole = vt != "DOUBLE"
                    e.payload["message"] = (f"{where}: {value[:40]!r} is not a {'whole ' if whole else ''}number, and '{col}' is a "
                                            f"{'whole-number' if whole else 'number'} column (cast it to text first if it should hold text).")
                else:
                    e.payload["message"] = f"{where}: {e.payload['message'].split(': ', 1)[-1]}"
                raise
        if vt == "BOOLEAN":
            try:
                return V.check(value, "BOOLEAN", op_index=op_index, column=col)
            except ValueRefused as e:
                e.payload["message"] = f"{where}: {value!r} is not true or false, and '{col}' is a true/false column."
                raise
        if isinstance(value, (dict, list)):
            raise EditError(f"{where}: a cell holds one value, not {type(value).__name__}.")
        return V.check(value, vt, op_index=op_index, column=col)

    def check_columns(self, cols: set[str], op_index: int) -> None:
        """P03 (§4.4): a number column keeps finite values and a finite sum and mean (1.797e308 on every row is a valid
        DOUBLE, but the column's mean overflows: refused here, not as a 500 when it is profiled)."""
        nums = [c for c in cols if c in self.names() and is_num(self.type_of(c))]
        if not nums:
            return
        sel = ", ".join(f"count(*) FILTER (WHERE NOT isfinite({q(c)}::DOUBLE)), sum({q(c)}::DOUBLE), avg({q(c)}::DOUBLE)"
                        for c in nums)
        try:
            row = self.one(f"SELECT {sel} FROM {self.cur}")
        except Exception as e:  # noqa: BLE001 - an overflow inside the engine is the values' fault
            log_.info("column check failed: %s", str(e)[:300])
            raise ValueRefused("value_out_of_range", f"Operation {op_index}: a number column's values add up to more than a "
                                                     "number can hold, so its sum and mean overflow; use smaller values.",
                               op_index=op_index, type="DOUBLE", max=repr(V.DOUBLE_MAX)) from None
        for i, c in enumerate(nums):
            bad, s, m = row[3 * i], row[3 * i + 1], row[3 * i + 2]
            if bad:
                ex = self.con.execute(f"SELECT __row_id, {q(c)}::DOUBLE FROM {self.cur} WHERE NOT isfinite({q(c)}::DOUBLE)"
                                      " ORDER BY __row_id LIMIT 5").fetchall()
                raise ValueRefused("non_finite_result", f"Operation {op_index}, '{c}': the column is not finite in {int(bad)} row(s) "
                                                        "(a number overflowed): use smaller values or another formula.",
                                   op_index=op_index, column=c, count=int(bad),
                                   examples=[{"row_id": int(r), "value": "inf" if v > 0 else "-inf" if v < 0 else "nan"} for r, v in ex])
            if (s is not None and not math.isfinite(s)) or (m is not None and not math.isfinite(m)):
                ex = self.con.execute(f"SELECT {q(c)}::DOUBLE FROM {self.cur} WHERE {q(c)} IS NOT NULL ORDER BY abs({q(c)}::DOUBLE) DESC"
                                      " LIMIT 5").fetchall()
                raise ValueRefused("value_out_of_range", f"Operation {op_index}, '{c}': the column's values add up to more than a "
                                                         f"number can hold (±{V.DOUBLE_MAX:.4g}), so its sum and mean overflow; use "
                                                         "smaller values.", op_index=op_index, column=c, type="DOUBLE",
                                   max=repr(V.DOUBLE_MAX), examples=[{"value": repr(float(r[0]))} for r in ex])

    # -- rows -----------------------------------------------------------------------------------------------------

    def _positions(self) -> None:
        if not self._pos_ready:
            self.con.execute(f"CREATE OR REPLACE TEMP TABLE _epos AS SELECT (row_number() OVER (ORDER BY __row_id) - 1)::BIGINT"
                             f" AS pos, __row_id FROM {self.base}")
            self._pos_ready = True

    def row_ids(self, refs: list[tuple[str, Any]], ctx: str) -> list[int]:
        """Each (kind, value) with kind 'row' (the grid's `_row`: a position in the version edited, translated through
        the row order, one release) or 'row_id' (`__row_id`) → a __row_id that is still in the data."""
        out: list[int] = []
        pos = []
        for kind, r in refs:
            if isinstance(r, bool) or not isinstance(r, int):
                raise EditError(f"{ctx}: row {r!r} is not a whole number (the grid's {'_row' if kind == 'row' else '__row_id'}).")
            if kind == "row":
                pos.append(r)
        pmap: dict[int, int] = {}
        if pos:
            self._positions()
            for p, rid in self.con.execute("SELECT pos, __row_id FROM _epos WHERE pos IN (SELECT unnest(?::BIGINT[]))",
                                           [sorted(set(pos))]).fetchall():
                pmap[int(p)] = int(rid)
        for kind, r in refs:
            if kind == "row":
                if r not in pmap:
                    raise EditError(f"{ctx}: row {r} is not in the data (past the last row).")
                out.append(pmap[r])
            else:
                out.append(int(r))
        have = {int(x[0]) for x in self.con.execute(f"SELECT __row_id FROM {self.cur} WHERE __row_id IN (SELECT unnest(?::BIGINT[]))",
                                                     [sorted(set(out))]).fetchall()}
        for (kind, r), rid in zip(refs, out):
            if rid not in have:
                raise EditError(f"{ctx}: row {r} is not in the data (deleted by an earlier operation"
                                f"{', or past the last row' if kind == 'row' else ', or no such __row_id'}).")
        return out

    def where(self, where: Any, ctx: str, op_index: int) -> str:
        conds = where if isinstance(where, list) else [where]
        if not conds or not all(isinstance(c, dict) for c in conds):
            raise EditError(f"{ctx}: 'where' is {{column, cmp, value}} or a list of them.")
        parts = []
        for c in conds:
            col = self.col(c.get("column"), ctx)
            t = self.type_of(col)
            cmp = c.get("cmp", "eq")
            if cmp not in CMPS:
                raise EditError(f"{ctx}: cmp is one of {', '.join(CMPS)}, not {cmp!r}.")
            x = q(col)
            v = c.get("value")
            if cmp == "is_missing":
                parts.append(f"{x} IS NULL")
                continue
            if cmp == "not_missing":
                parts.append(f"{x} IS NOT NULL")
                continue
            if cmp == "contains":
                parts.append(f"coalesce(contains(lower(CAST({x} AS VARCHAR)), lower({lit(str(v), 'VARCHAR')})), false)")
                continue
            num = is_num(t)
            if cmp == "in":
                if not isinstance(v, list) or not v:
                    raise EditError(f"{ctx}: 'in' takes a non-empty list of values.")
                vals = [self.value(z, col, t, ctx, op_index) for z in v]
                vals = [z for z in vals if z is not None]
                if not vals:
                    parts.append("false")
                elif num or vtype(t) == "BOOLEAN":
                    parts.append(f"coalesce({x} IN ({', '.join(lit(z, t) for z in vals)}), false)")
                else:
                    parts.append(f"coalesce(CAST({x} AS VARCHAR) IN ({', '.join(lit(str(z), 'VARCHAR') for z in vals)}), false)")
                continue
            z = self.value(v, col, t, ctx, op_index)
            if z is None:
                raise EditError(f"{ctx}: compare with a value, or use is_missing / not_missing.")
            if cmp in ("lt", "le", "gt", "ge") and not (num or vtype(t) in ("DATE", "TIMESTAMP")):
                raise EditError(f"{ctx}: '{cmp}' compares numbers, and '{col}' is not a number column.")
            op = {"eq": "=", "ne": "<>", "lt": "<", "le": "<=", "gt": ">", "ge": ">="}[cmp]
            lhs, rhs = (x, lit(z, t)) if num or vtype(t) in ("BOOLEAN", "DATE", "TIMESTAMP") else \
                (f"CAST({x} AS VARCHAR)", lit(str(z), "VARCHAR"))
            if cmp == "ne":
                parts.append(f"({lhs} IS DISTINCT FROM {rhs})")  # pandas: a missing cell is ≠ any value
            else:
                parts.append(f"coalesce({lhs} {op} {rhs}, false)")
        return " AND ".join(f"({p})" for p in parts)

    # -- formula --------------------------------------------------------------------------------------------------

    def formula(self, expr: Any, ctx: str) -> tuple[str, list[str]]:
        """The formula as SQL over DOUBLE (never eval): ÷0 and log/sqrt of a negative are missing; an overflow is ±inf
        and refused afterwards (non_finite_result); NaN is missing."""
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
        except (SyntaxError, ValueError, RecursionError, MemoryError):
            raise EditError(f"{ctx}: can't read the formula {expr!r}; {FORMULA_HELP}.") from None
        nodes = list(ast.walk(tree))
        if len(nodes) > FORMULA_MAX_NODES:
            raise EditError(f"{ctx}: the formula is too long ({len(nodes)} parts; at most {FORMULA_MAX_NODES}).")
        used: list[str] = []
        cols = dict((n, t) for n, t in self.out.cols)

        def num_const(n: ast.AST) -> float | None:
            if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)) and not isinstance(n.value, bool):
                try:
                    return float(n.value)
                except OverflowError:
                    raise EditError(f"{ctx}: the number {str(n.value)[:20]}… is too large.") from None
            if isinstance(n, ast.UnaryOp) and isinstance(n.op, (ast.USub, ast.UAdd)):
                v = num_const(n.operand)
                return None if v is None else (-v if isinstance(n.op, ast.USub) else v)
            return None

        def ev(n: ast.AST) -> str:
            if isinstance(n, ast.Expression):
                return ev(n.body)
            if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)) and not isinstance(n.value, bool):
                f = num_const(n)
                if not math.isfinite(f):
                    raise EditError(f"{ctx}: the number {str(n.value)[:20]}… is too large.")
                return f"({f!r}::DOUBLE)"
            if isinstance(n, ast.Name):
                c = names.get(n.id, n.id)
                if c not in cols:
                    raise EditError(f"{ctx}: no column {c!r} in the formula (put names with spaces in `backticks`).")
                if not is_num(cols[c]):
                    raise EditError(f"{ctx}: '{c}' is not a number column; a formula uses number columns only (cast it first).")
                used.append(c)
                return f"({q(c)}::DOUBLE)"
            if isinstance(n, ast.UnaryOp) and isinstance(n.op, (ast.USub, ast.UAdd)):
                v = ev(n.operand)
                return f"(-{v})" if isinstance(n.op, ast.USub) else v
            if isinstance(n, ast.BinOp) and isinstance(n.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)):
                a, b = ev(n.left), ev(n.right)
                if isinstance(n.op, ast.Div):  # P03: ÷0 is missing, as in SQL
                    return f"({a} / NULLIF({b}, 0))"
                return f"({a} {'+' if isinstance(n.op, ast.Add) else '-' if isinstance(n.op, ast.Sub) else '*'} {b})"
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in FUNCS and not n.keywords:
                ar = FUNC_ARITY[n.func.id]
                if len(n.args) not in (ar if isinstance(ar, tuple) else (ar,)):
                    raise EditError(f"{ctx}: {n.func.id}() takes {ar if not isinstance(ar, tuple) else ' or '.join(map(str, ar))} argument(s).")
                fn = n.func.id
                if fn == "round" and len(n.args) == 2:
                    d = num_const(n.args[1])
                    lo, hi = ROUND_DIGITS
                    if d is None or not math.isfinite(d) or d != int(d) or not lo <= d <= hi:
                        raise EditError(f"{ctx}: round(x, digits) takes a whole number of digits from {lo} to {hi}.")
                    return f"round_even({ev(n.args[0])}, {int(d)})"  # numpy rounds half to even
                args = [ev(x) for x in n.args]
                x = args[0]
                if fn == "abs":
                    return f"abs({x})"
                if fn == "round":
                    return f"round_even({x}, 0)"
                if fn == "sqrt":
                    return f"(CASE WHEN {x} < 0 THEN NULL ELSE sqrt({x}) END)"
                if fn == "log":
                    return f"(CASE WHEN {x} < 0 THEN NULL WHEN {x} = 0 THEN '-inf'::DOUBLE ELSE ln({x}) END)"
                if fn == "log1p":
                    return f"(CASE WHEN {x} < -1 THEN NULL WHEN {x} = -1 THEN '-inf'::DOUBLE ELSE ln(1 + {x}) END)"
                if fn == "exp":
                    return f"(CASE WHEN {x} > {EXP_MAX!r} THEN 'inf'::DOUBLE ELSE exp({x}) END)"
                # min / max: like numpy fmin / fmax, a missing side is ignored
                return f"{'least' if fn == 'min' else 'greatest'}({args[0]}, {args[1]})"
            what = type(n).__name__
            if isinstance(n, ast.Call):
                what = f"the function {getattr(n.func, 'id', '?')}()"
            elif isinstance(n, ast.BinOp):
                what = f"the operator {type(n.op).__name__}"
            raise EditError(f"{ctx}: {what} is not allowed in a formula; {FORMULA_HELP}.")

        try:
            sql = ev(tree)
        except RecursionError:
            raise EditError(f"{ctx}: can't compute {expr!r}; {FORMULA_HELP}.") from None
        return f"(CASE WHEN isnan({sql}) THEN NULL ELSE {sql} END)", used


def compile_ops(con: Any, base: str, ops: Any, *, target: str | None, next_row_id: int) -> Compiled:
    """Compile `ops` over the relation `base` (`__row_id`, columns) on `con`. Returns a Compiled whose `.view` is the
    edited table. DL4 (M-a) / P03 (§4.4): an engine error is a 400 `edit_invalid` naming the operation and column; the
    engine's text is logged, never returned."""
    cur: dict[str, Any] = {}
    try:
        return _compile(con, base, ops, target, next_row_id, cur)
    except (EditError, ValueRefused):
        raise
    except Exception as e:  # noqa: BLE001 - duckdb.Error and friends: the request's fault, never a 500
        log_.warning("edit op %s on %r failed in the engine: %s: %s", cur.get("i"), cur.get("column"), type(e).__name__, str(e)[:500])
        raise edit_invalid(cur.get("i"), cur.get("op"), cur.get("column")) from None


def _ex_rows(c: _C, sql: str, cols: list[str]) -> list[dict[str, Any]]:
    out = []
    for r in c.con.execute(sql).fetchall():
        out.append({"row_id": int(r[0]), **{k: _jsonable(v) for k, v in zip(cols, r[1:])}})
    return out


def _compile(con: Any, base: str, ops: Any, target: str | None, next_row_id: int, cur: dict[str, Any]) -> Compiled:
    if not isinstance(ops, list) or not ops:
        raise EditError("Send a non-empty list of operations: [{\"op\": \"set_cells\", …}, …].")
    if len(ops) > MAX_OPS:
        raise EditError(f"At most {MAX_OPS} operations per version ({len(ops)} sent).")
    c = _C(con, base, target, next_row_id)
    out = c.out
    for i, op in enumerate(ops):
        if not isinstance(op, dict) or op.get("op") not in OPS:
            raise EditError(f"Operation {i + 1}: 'op' is one of {', '.join(OPS)}.")
        kind = op["op"]
        oi = i + 1
        cur.update(i=oi, op=kind, column=op.get("column") if isinstance(op.get("column"), str) else
                   op.get("name") if isinstance(op.get("name"), str) else None)
        ctx = f"Operation {oi} ({kind})"
        entry: dict[str, Any] = {"i": oi, "op": kind}
        ex: list[dict[str, Any]] = []
        touched: set[str] = set()
        prev = c.cur

        if kind == "set_cells":
            cells = op.get("cells")
            if not isinstance(cells, list) or not cells:
                raise EditError(f"{ctx}: 'cells' is a non-empty list of {{row_id or row, column, value}}.")
            c.n_cells += len(cells)
            if c.n_cells > MAX_CELLS:
                raise EditError(f"At most {MAX_CELLS:,} cell edits per version; for more, use replace_values, fill_missing or "
                                "upload the file as a new version.")
            refs, cols_ = [], []
            for cell in cells:
                if not isinstance(cell, dict):
                    raise EditError(f"{ctx}: each cell is {{row_id or row, column, value}}.")
                col = c.col(cell.get("column"), ctx)
                cols_.append(col)
                refs.append(("row_id", cell["row_id"]) if "row_id" in cell else ("row", cell.get("row")))
            rids = c.row_ids(refs, ctx)
            per: dict[str, dict[int, Any]] = {}
            for cell, col, rid, ref in zip(cells, cols_, rids, refs):
                cur["column"] = col
                t = c.type_of(col)
                v = c.value(cell.get("value"), col, t, f"{ctx}, row {ref[1]}, '{col}'", oi)
                per.setdefault(col, {})[rid] = v  # the last edit of a cell wins
            repl, joins = {}, []
            for j, (col, vals) in enumerate(per.items()):
                t = c.type_of(col)
                tv = f"_v{oi}_{j}"
                con.execute(f"CREATE OR REPLACE TEMP TABLE {tv} (rid BIGINT, val {t})")
                con.executemany(f"INSERT INTO {tv} VALUES (?, ?)", [[rid, v] for rid, v in vals.items()])
                joins.append(f" LEFT JOIN {tv} v{j} ON v{j}.rid = s.__row_id")
                repl[col] = f"CASE WHEN v{j}.rid IS NOT NULL THEN v{j}.val ELSE s.{q(col)} END"
            n_changed, n_rows = 0, set()
            for j, (col, vals) in enumerate(per.items()):
                tv = f"_v{oi}_{j}"
                rows = con.execute(f"SELECT s.__row_id, s.{q(col)}, v.val FROM {prev} s JOIN {tv} v ON v.rid = s.__row_id"
                                   f" WHERE s.{q(col)} IS DISTINCT FROM v.val ORDER BY s.__row_id").fetchall()
                n_changed += len(rows)
                for rid, old, new in rows:
                    n_rows.add(int(rid))
                    if len(ex) < EXAMPLES_PER_OP:
                        ex.append({"row_id": int(rid), "column": col, "old": _jsonable(old), "new": _jsonable(new)})
            c.view(f"SELECT {c.select(repl)} FROM {prev} s{''.join(joins)}")
            touched.update(per)
            entry.update(cells=n_changed, rows=len(n_rows), summary=f"set {n_changed} cell(s) in {len(n_rows)} row(s)")

        elif kind in ("delete_rows", "filter_rows"):
            if kind == "delete_rows" and ("rows" in op or "row_ids" in op):
                rr = op.get("row_ids") if "row_ids" in op else op.get("rows")
                if not isinstance(rr, list) or not rr:
                    raise EditError(f"{ctx}: 'rows' is a non-empty list of row ids (_row), or 'row_ids' a list of __row_id.")
                ids = sorted(set(c.row_ids([("row_id" if "row_ids" in op else "row", r) for r in rr], ctx)))
                tv = f"_d{oi}"
                con.execute(f"CREATE OR REPLACE TEMP TABLE {tv} (rid BIGINT)")
                con.executemany(f"INSERT INTO {tv} VALUES (?)", [[r] for r in ids])
                cond = f"s.__row_id IN (SELECT rid FROM {tv})"
                how = f"{len(ids)} row(s) by id"
            else:
                if "where" not in op:
                    raise EditError(f"{ctx}: send 'where' (a filter){' or rows' if kind == 'delete_rows' else ''}.")
                w = c.where(op["where"], ctx, oi)
                cond = f"({w})" if kind == "delete_rows" else f"NOT ({w})"
                how = None
            n_drop = int(c.one(f"SELECT count(*) FROM {prev} s WHERE {cond}")[0])
            names = c.names()[:6]
            ex = [{"row_id": r["row_id"], "deleted": {k: v for k, v in r.items() if k != "row_id"}}
                  for r in _ex_rows(c, f"SELECT s.__row_id, {', '.join('s.' + q(n) for n in names)} FROM {prev} s WHERE {cond}"
                                       f" ORDER BY s.__row_id LIMIT {EXAMPLES_PER_OP}", names)]
            c.view(f"SELECT s.* FROM {prev} s WHERE NOT ({cond})")
            if not int(c.one(f"SELECT count(*) FROM {c.cur}")[0]):
                raise EditError(f"{ctx}: this removes every row.")
            if how is None:
                how = f"{n_drop} row(s) where {_describe(op['where'])}" if kind == "delete_rows" else \
                    f"kept rows where {_describe(op['where'])}: {n_drop} removed"
            entry.update(rows=n_drop, summary=how)

        elif kind == "add_rows":
            rows = op.get("rows")
            if not isinstance(rows, list) or not rows or not all(isinstance(r, dict) for r in rows):
                raise EditError(f"{ctx}: 'rows' is a non-empty list of {{column: value}}.")
            out.n_added += len(rows)
            if out.n_added > MAX_ADD_ROWS:
                raise EditError(f"At most {MAX_ADD_ROWS:,} added rows per version ({out.n_added:,} with this operation); upload a file "
                                "as a new version for more.")
            data = []
            for j, r in enumerate(rows):
                for k in r:
                    c.col(k, f"{ctx}, new row {j + 1}")
                rid = out.next_row_id
                out.next_row_id += 1
                vals = [c.value(r.get(n), n, t, f"{ctx}, new row {j + 1}, '{n}'", oi) for n, t in out.cols]
                data.append([rid, *vals])
                if len(ex) < EXAMPLES_PER_OP:
                    ex.append({"row_id": rid, "added": {n: _jsonable(v) for (n, _), v in list(zip(out.cols, vals))[:6]}})
            tv = f"_a{oi}"
            con.execute(f"CREATE OR REPLACE TEMP TABLE {tv} (__row_id BIGINT, {', '.join(f'{q(n)} {t}' for n, t in out.cols)})")
            con.executemany(f"INSERT INTO {tv} VALUES ({', '.join('?' * (len(out.cols) + 1))})", data)
            c.view(f"SELECT * FROM {prev} UNION ALL BY NAME SELECT * FROM {tv}")
            touched.update(c.names())
            entry.update(rows=len(rows), summary=f"added {len(rows)} row(s)", row_ids=[d[0] for d in data][:EXAMPLES_PER_OP])

        elif kind == "rename_column":
            col = c.col(op.get("column"), ctx)
            to = op.get("to")
            if not isinstance(to, str) or not to.strip() or len(to.strip()) > 120:
                raise EditError(f"{ctx}: 'to' is the new name (1–120 characters).")
            to = to.strip()
            if to in c.names() or to.lower() in {n.lower() for n in c.names() if n != col}:
                raise EditError(f"{ctx}: there is already a column named {to!r}.")
            if is_reserved_name(to):
                raise EditError(f"{ctx}: {to!r} is reserved (names starting with '__', and '_row_id' / '_row').")
            c.view(f"SELECT {c.select(rename={col: to})} FROM {prev} s")
            out.cols = [[to if n == col else n, t] for n, t in out.cols]
            orig = next((k for k, v in out.renamed.items() if v == col), col)  # renamed = {name in the parent: name now}
            out.renamed[orig] = to
            for d in (out.casts, out.widened):
                if col in d:
                    d[to] = d.pop(col)
            if out.target == col:
                out.target_touched = out.target_touched or {"i": oi, "op": kind, "what": f"renames (to '{to}')", "column": col}
                out.target = to
            entry.update(column=col, to=to, summary=f"renamed '{col}' → '{to}'")

        elif kind == "drop_column":
            col = c.col(op.get("column"), ctx)
            if len(out.cols) <= 2:
                raise EditError(f"{ctx}: a dataset keeps at least two columns.")
            c.view(f"SELECT {c.select(drop={col})} FROM {prev} s")
            out.cols = [[n, t] for n, t in out.cols if n != col]
            out.casts.pop(col, None)
            out.widened.pop(col, None)
            if out.target == col:
                out.target_touched = out.target_touched or {"i": oi, "op": kind, "what": "drops", "column": col}
                out.target = None
            entry.update(column=col, summary=f"dropped '{col}'")

        elif kind == "add_column":
            name = op.get("name")
            if not isinstance(name, str) or not name.strip() or len(name.strip()) > 120:
                raise EditError(f"{ctx}: 'name' is the new column's name (1–120 characters).")
            name = name.strip()
            if name in c.names() or name.lower() in {n.lower() for n in c.names()}:
                raise EditError(f"{ctx}: there is already a column named {name!r}.")
            if is_reserved_name(name):
                raise EditError(f"{ctx}: {name!r} is reserved (names starting with '__', and '_row_id' / '_row').")
            formula, fn = op.get("formula"), op.get("fn")
            if fn is not None:
                if fn not in FN_SHORTCUTS:
                    raise EditError(f"{ctx}: fn is one of {', '.join(FN_SHORTCUTS)}.")
                a, b = op.get("a"), op.get("b")
                if not isinstance(a, str) or not isinstance(b, str):
                    raise EditError(f"{ctx}: {fn} takes two columns, a and b.")
                formula = f"`{a}` {FN_SHORTCUTS[fn]} `{b}`"
            if formula is not None:
                sql, used = c.formula(formula, ctx)
                c.view(f"SELECT {c.select(add=[(name, sql)])} FROM {prev} s")
                out.cols.append([name, "DOUBLE"])
                bad, miss = c.one(f"SELECT count(*) FILTER (WHERE NOT isfinite({q(name)})), count(*) FILTER (WHERE {q(name)} IS NULL)"
                                  f" FROM {c.cur}")
                if bad:  # P03 (§4.4): an overflow (x * 1e308, exp(1000), log(0)) is refused, never stored as missing
                    exr = con.execute(f"SELECT __row_id, {q(name)} FROM {c.cur} WHERE NOT isfinite({q(name)}) ORDER BY __row_id"
                                      " LIMIT 5").fetchall()
                    raise ValueRefused("non_finite_result", f"{ctx}: {formula!r} is not a finite number in {int(bad)} row(s) (it "
                                                            "overflows); use smaller values or another formula.",
                                       count=int(bad), examples=[{"row_id": int(r), "value": "inf" if v > 0 else "-inf"} for r, v in exr])
                entry.update(column=name, formula=formula, uses=sorted(set(used)), missing=int(miss),
                             summary=f"added '{name}' = {formula}" + (f" ({int(miss)} missing: missing inputs or ÷0)" if miss else ""))
            elif "value" in op:
                v = op["value"]
                if isinstance(v, (dict, list)):
                    raise EditError(f"{ctx}: 'value' is one value.")
                if isinstance(v, str):
                    try:
                        v = float(v.replace(",", "")) if v.strip() else None
                    except ValueError:
                        pass
                if isinstance(v, bool):
                    t = "BOOLEAN"
                elif isinstance(v, int):
                    t = "BIGINT"
                elif isinstance(v, float) or v is None:
                    t = "DOUBLE"
                else:
                    t = "VARCHAR"
                v = V.check(v, vtype(t), column=name, op_index=oi) if v is not None else None
                c.view(f"SELECT {c.select(add=[(name, lit(v, t))])} FROM {prev} s")
                out.cols.append([name, t])
                entry.update(column=name, value=_jsonable(v), summary=f"added '{name}' = {v!r} on every row")
            else:
                raise EditError(f"{ctx}: send 'value' (a constant), 'formula', or fn with a and b.")
            touched.add(name)
            entry["rows"] = int(c.one(f"SELECT count(*) FROM {c.cur}")[0])

        elif kind == "cast_column":
            col = c.col(op.get("column"), ctx)
            to = op.get("to")
            if to not in CAST_KIND:
                raise EditError(f"{ctx}: 'to' is number, integer, text or bool.")
            t = c.type_of(col)
            if out.target == col:
                out.target_touched = out.target_touched or {"i": oi, "op": kind, "what": f"casts (to {to})", "column": col}
            x = f"s.{q(col)}"
            txt = f"replace(trim(CAST({x} AS VARCHAR)), ',', '')"
            if to == "number":
                expr = f"{x}::DOUBLE" if is_num(t) else f"TRY_CAST({txt} AS DOUBLE)"
            elif to == "integer":
                d = f"{x}::DOUBLE" if is_num(t) else f"TRY_CAST({txt} AS DOUBLE)"
                expr = f"TRY_CAST(round({d}) AS BIGINT)"
            elif to == "text":
                if vtype(t) == "DOUBLE":  # 3.0 → '3' (as DL3 did), else the number's shortest text
                    expr = (f"CASE WHEN {x} IS NULL THEN NULL WHEN isfinite({x}) AND {x} = round({x}) AND abs({x}) < 9.007199254740992e15"
                            f" THEN CAST(CAST({x} AS BIGINT) AS VARCHAR) ELSE CAST({x} AS VARCHAR) END")
                else:
                    expr = f"CAST({x} AS VARCHAR)"
            else:
                low = f"lower(trim(CAST({x} AS VARCHAR)))"
                expr = (f"CASE WHEN {low} IN ('true', '1', '1.0', 'yes') THEN TRUE WHEN {low} IN ('false', '0', '0.0', 'no')"
                        " THEN FALSE ELSE NULL END")
            nt = CAST_KIND[to]
            lost_rows = con.execute(f"SELECT s.__row_id, s.{q(col)} FROM {prev} s WHERE {x} IS NOT NULL AND ({expr}) IS NULL"
                                    f" ORDER BY s.__row_id LIMIT {EXAMPLES_PER_OP}").fetchall()
            n_lost = int(c.one(f"SELECT count(*) FROM {prev} s WHERE {x} IS NOT NULL AND ({expr}) IS NULL")[0])
            ex = [{"row_id": int(r), "column": col, "old": _jsonable(v), "new": None} for r, v in lost_rows]
            c.view(f"SELECT {c.select({col: f'({expr})::{nt}'})} FROM {prev} s")
            out.cols = [[n, nt if n == col else tt] for n, tt in out.cols]
            out.casts[col] = to
            out.widened.pop(col, None)
            touched.add(col)
            entry.update(column=col, to=to, became_missing=n_lost,
                         summary=f"cast '{col}' to {to}" + (f": {n_lost} value(s) could not be read and are now missing" if n_lost else ""))

        elif kind == "replace_values":
            col = c.col(op.get("column"), ctx)
            match = op.get("match", "exact")
            if match not in ("exact", "contains"):
                raise EditError(f"{ctx}: match is exact or contains.")
            if "find" not in op or op.get("find") in (None, ""):
                raise EditError(f"{ctx}: say what to find ('find').")
            t = c.type_of(col)
            x = f"s.{q(col)}"
            if match == "contains":
                if is_num(t):
                    raise EditError(f"{ctx}: 'contains' replaces text; '{col}' is a number column (use exact).")
                f_, r_ = lit(str(op["find"]), "VARCHAR"), lit(str(op.get("replace") or ""), "VARCHAR")
                if vtype(t) != "VARCHAR":
                    raise EditError(f"{ctx}: 'contains' replaces text; cast '{col}' to text first.")
                hit = f"coalesce(contains({x}, {f_}), false)"
                newx = f"replace({x}, {f_}, {r_})"
            else:
                f_ = c.value(op["find"], col, t, ctx, oi)
                rep = c.value(op.get("replace"), col, t, ctx, oi)
                if f_ is None:
                    raise EditError(f"{ctx}: say what to find ('find'); use fill_missing for missing cells.")
                hit = f"coalesce({x} = {lit(f_, t)}, false)" if is_num(t) or vtype(t) != "VARCHAR" else \
                    f"coalesce({x} = {lit(str(f_), 'VARCHAR')}, false)"
                newx = lit(rep, t)
            exr = con.execute(f"SELECT s.__row_id, {x}, {newx} FROM {prev} s WHERE {hit} ORDER BY s.__row_id LIMIT {EXAMPLES_PER_OP}").fetchall()
            n_hit = int(c.one(f"SELECT count(*) FROM {prev} s WHERE {hit}")[0])
            ex = [{"row_id": int(r), "column": col, "old": _jsonable(o), "new": _jsonable(n)} for r, o, n in exr]
            c.view(f"SELECT {c.select({col: f'CASE WHEN {hit} THEN {newx} ELSE {x} END'})} FROM {prev} s")
            touched.add(col)
            entry.update(column=col, cells=n_hit, rows=n_hit,
                         summary=f"replaced {op['find']!r} → {op.get('replace')!r} in '{col}' ({match}): {n_hit} cell(s)")

        elif kind == "fill_missing":
            col = c.col(op.get("column"), ctx)
            strat = op.get("strategy", "constant")
            t = c.type_of(col)
            x = q(col)
            nt = t
            if strat == "constant":
                if "value" not in op:
                    raise EditError(f"{ctx}: send 'value' for a constant fill.")
                v = c.value(op["value"], col, t, ctx, oi)
            elif strat in ("mean", "median"):
                if not is_num(t):
                    raise EditError(f"{ctx}: {strat} needs a number column; '{col}' is not one.")
                v = c.one(f"SELECT {'avg' if strat == 'mean' else 'median'}({x}::DOUBLE) FROM {prev}")[0]
                v = None if v is None else float(v)
                if v is not None and vtype(t) in ("BIGINT", "INTEGER") and not float(v).is_integer():
                    nt = "DOUBLE"  # a whole-number column filled with a fractional mean becomes a number column (reported)
                elif v is not None and vtype(t) in ("BIGINT", "INTEGER"):
                    v = int(v)
            elif strat == "mode":
                r = c.one(f"SELECT {x} FROM {prev} WHERE {x} IS NOT NULL GROUP BY 1 ORDER BY count(*) DESC, 1 LIMIT 1")
                if r is None:
                    raise EditError(f"{ctx}: '{col}' has no values to take the mode of.")
                v = r[0]
            else:
                raise EditError(f"{ctx}: strategy is constant, mean, median or mode.")
            if v is None or (isinstance(v, float) and math.isnan(v)):
                raise EditError(f"{ctx}: the fill value is missing itself.")
            n_miss = int(c.one(f"SELECT count(*) FROM {prev} WHERE {x} IS NULL")[0])
            ex = [{"row_id": int(r[0]), "column": col, "old": None, "new": _jsonable(v)}
                  for r in con.execute(f"SELECT __row_id FROM {prev} WHERE {x} IS NULL ORDER BY __row_id LIMIT {EXAMPLES_PER_OP}").fetchall()]
            fill = lit(v.isoformat() if hasattr(v, "isoformat") and vtype(nt) in ("DATE", "TIMESTAMP") else v, nt)
            c.view(f"SELECT {c.select({col: f'coalesce(s.{x}::{nt}, {fill})'})} FROM {prev} s")
            if nt != t:
                out.cols = [[n, nt if n == col else tt] for n, tt in out.cols]
                out.widened[col] = nt
                entry["type_changed"] = {"from": t, "to": nt}
            touched.add(col)
            entry.update(column=col, strategy=strat, value=_jsonable(v), cells=n_miss, rows=n_miss,
                         summary=f"filled {n_miss} missing value(s) in '{col}' with {strat}" + ("" if strat == "constant" else f" ({_jsonable(v)!r})")
                                 + (f"; '{col}' is now a number column (the {strat} is not a whole number)" if nt != t else ""))

        c.check_columns(touched, oi)
        entry["examples"] = ex
        entry["examples_truncated"] = bool((entry.get("cells") or entry.get("rows") or 0) > len(ex)) if ex else False
        out.log.append(entry)
    return out
