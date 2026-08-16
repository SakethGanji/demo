"""Filter compilation and application — used by sampling, profiling, aggregation."""

from __future__ import annotations

import re
from typing import Any

import duckdb
from fastapi import HTTPException

from app.api.errors import ProblemException
from app.shared.utils.sql import quote_ident, sanitize_filter_expr

# DuckDB's binder error for a name that is not a column of the FROM clause.
# Matching it is what turns "the caller named a column that does not exist"
# into the 400 every other column-taking endpoint publishes, instead of an
# uncaught exception the API renders as a 500.
_UNKNOWN_COLUMN_RE = re.compile(r'Referenced column "?([^"\n]+?)"? not found')


def _unknown_column(columns: list[str], available: list[str]) -> ProblemException:
    """The shared ``unknown-column`` 400 contract (same shape as /aggregate)."""
    return ProblemException(
        400,
        f"Column not found: {', '.join(repr(c) for c in columns)}. "
        f"Valid options: {available}",
        code="unknown-column", columns=columns, available=available,
    )


def _num(val: Any, op: str, cast) -> Any:
    """Convert a filter value to int/float, or raise the ``invalid-filter-value``
    400 rather than letting an unguarded ``int()``/``float()`` on caller input
    (a filter builder emits strings) surface as a 500. The length/top-N/percentile/
    last-N-days operators all take a numeric value that only DuckDB would
    otherwise validate — at execution, uncaught."""
    try:
        return cast(val)
    except (TypeError, ValueError):
        raise ProblemException(
            400,
            f"Filter operator '{op}' needs a numeric value, got {val!r}.",
            code="invalid-filter-value", op=op, value=val,
        ) from None


def compile_filter(f: dict[str, Any], params: list[Any],
                   available: set[str] | None = None) -> str:
    """Compile a Filter or FilterGroup dict into a SQL WHERE clause fragment.

    Uses parameterized queries (? placeholders) for all user values to prevent injection.
    Appends values to the *params* list in order.

    When *available* is supplied it is the set of column names the clause will
    be bound against, and a condition naming anything else raises the
    ``unknown-column`` 400 rather than compiling SQL DuckDB will reject at bind
    time (a 500). The check lives here, on the recursive walk, so a condition
    nested three FilterGroups deep is validated exactly like a top-level one —
    callers cannot get that right by inspecting a flat list.
    """
    # FilterGroup (has 'logic' + 'conditions')
    if "logic" in f and "conditions" in f:
        logic = f["logic"].upper()
        if logic not in ("AND", "OR"):
            raise HTTPException(400, f"Invalid filter logic: {f['logic']}. Use 'and' or 'or'.")
        parts = [compile_filter(cond, params, available) for cond in f["conditions"]]
        parts = [p for p in parts if p]
        if not parts:
            return ""
        return f"({f' {logic} '.join(parts)})"

    # Single Filter
    col = f.get("column")
    op = f.get("op", "").lower()
    val = f.get("value")
    case_sensitive = f.get("case_sensitive", True)

    if not col or not op:
        raise HTTPException(400, "Filter requires 'column' and 'op'")
    if available is not None and col not in available:
        raise _unknown_column([col], sorted(available))
    qcol = quote_ident(col)

    # Null/empty checks (no value needed)
    if op == "is_null":
        return f"{qcol} IS NULL"
    if op == "is_not_null":
        return f"{qcol} IS NOT NULL"
    if op == "is_empty":
        return f"({qcol} IS NULL OR CAST({qcol} AS VARCHAR) = '')"
    if op == "is_not_empty":
        return f"({qcol} IS NOT NULL AND CAST({qcol} AS VARCHAR) != '')"

    # Comparison operators
    op_map = {"eq": "=", "neq": "!=", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}
    if op in op_map:
        params.append(val)
        return f"{qcol} {op_map[op]} ?"

    # Set operators
    if op == "in":
        if not isinstance(val, list) or len(val) == 0:
            raise HTTPException(400, f"Filter 'in' requires a non-empty list, got: {val}")
        placeholders = ", ".join(["?"] * len(val))
        params.extend(val)
        return f"{qcol} IN ({placeholders})"
    if op == "not_in":
        if not isinstance(val, list) or len(val) == 0:
            raise HTTPException(400, f"Filter 'not_in' requires a non-empty list, got: {val}")
        placeholders = ", ".join(["?"] * len(val))
        params.extend(val)
        return f"{qcol} NOT IN ({placeholders})"

    # Range operators
    if op in ("between", "not_between"):
        if not isinstance(val, list) or len(val) != 2:
            raise HTTPException(400, f"Filter '{op}' requires [low, high], got: {val}")
        params.extend(val)
        expr = f"{qcol} BETWEEN ? AND ?"
        return expr if op == "between" else f"NOT ({expr})"

    # String operators
    col_expr = qcol if case_sensitive else f"LOWER({qcol})"

    if op == "contains":
        v = val if case_sensitive else str(val).lower()
        params.append(f"%{v}%")
        return f"{col_expr} LIKE ?"
    if op == "icontains":
        params.append(f"%{str(val).lower()}%")
        return f"LOWER({qcol}) LIKE ?"
    if op == "not_contains":
        v = val if case_sensitive else str(val).lower()
        params.append(f"%{v}%")
        return f"{col_expr} NOT LIKE ?"
    if op == "starts_with":
        v = val if case_sensitive else str(val).lower()
        params.append(f"{v}%")
        return f"{col_expr} LIKE ?"
    if op == "ends_with":
        v = val if case_sensitive else str(val).lower()
        params.append(f"%{v}")
        return f"{col_expr} LIKE ?"
    if op == "regex":
        params.append(val)
        return f"regexp_matches({qcol}, ?)"

    # Length operators (string length)
    len_op_map = {"len_eq": "=", "len_gt": ">", "len_gte": ">=", "len_lt": "<", "len_lte": "<="}
    if op in len_op_map:
        params.append(_num(val, op, int))
        return f"LENGTH(CAST({qcol} AS VARCHAR)) {len_op_map[op]} ?"
    if op == "len_between":
        if not isinstance(val, list) or len(val) != 2:
            raise HTTPException(400, f"Filter 'len_between' requires [min_len, max_len], got: {val}")
        params.extend([_num(val[0], op, int), _num(val[1], op, int)])
        return f"LENGTH(CAST({qcol} AS VARCHAR)) BETWEEN ? AND ?"

    # Top/bottom N by column value
    if op == "top_n":
        n = _num(val, op, int)
        return f"{qcol} >= (SELECT {qcol} FROM (SELECT DISTINCT {qcol} FROM _filter_src ORDER BY {qcol} DESC LIMIT {n}) sub ORDER BY {qcol} ASC LIMIT 1)"
    if op == "bottom_n":
        n = _num(val, op, int)
        return f"{qcol} <= (SELECT {qcol} FROM (SELECT DISTINCT {qcol} FROM _filter_src ORDER BY {qcol} ASC LIMIT {n}) sub ORDER BY {qcol} DESC LIMIT 1)"

    # Percentile operators
    if op == "top_pct":
        pct = _num(val, op, float)
        return f"{qcol} >= (SELECT PERCENTILE_CONT({1.0 - pct}) WITHIN GROUP (ORDER BY {qcol}) FROM _filter_src)"
    if op == "bottom_pct":
        pct = _num(val, op, float)
        return f"{qcol} <= (SELECT PERCENTILE_CONT({pct}) WITHIN GROUP (ORDER BY {qcol}) FROM _filter_src)"

    # Date operators
    if op == "date_before":
        params.append(val)
        return f"CAST({qcol} AS DATE) < CAST(? AS DATE)"
    if op == "date_after":
        params.append(val)
        return f"CAST({qcol} AS DATE) > CAST(? AS DATE)"
    if op == "date_between":
        if not isinstance(val, list) or len(val) != 2:
            raise HTTPException(400, f"Filter 'date_between' requires [start, end], got: {val}")
        params.extend(val)
        return f"CAST({qcol} AS DATE) BETWEEN CAST(? AS DATE) AND CAST(? AS DATE)"
    if op == "last_n_days":
        n = _num(val, op, int)
        return f"CAST({qcol} AS DATE) >= CURRENT_DATE - INTERVAL '{n}' DAY"

    # Duplicate/unique operators
    if op == "is_duplicate":
        return f"{qcol} IN (SELECT {qcol} FROM _filter_src GROUP BY {qcol} HAVING COUNT(*) > 1)"
    if op == "is_unique":
        return f"{qcol} IN (SELECT {qcol} FROM _filter_src GROUP BY {qcol} HAVING COUNT(*) = 1)"

    raise HTTPException(400, f"Unknown filter operator: '{op}'")


def apply_filters(
    conn: duckdb.DuckDBPyConnection,
    source_table: str,
    filters: list[dict[str, Any]] | None,
    filter_expr: str | None = None,
    available: set[str] | None = None,
) -> tuple[str, int | None, str | None]:
    """Apply structured filters and/or raw filter_expr to a source table.

    Returns (table_or_view_name, matched_count, human_readable_description).
    Creates _filtered_view if filters are applied.

    Column names are validated against *available* — the caller's set, or, by
    default, the source table's own columns. A filter on a column that does not
    exist is a request error and answers 400 ``unknown-column``; before this it
    reached DuckDB's binder and came back as an uncaught ``BinderException``,
    i.e. a 500 whose body (in debug) echoed the generated SQL. ``filter_expr``
    is raw SQL and cannot be checked up front, so the binder error it raises is
    translated at execution time instead.
    """
    if not filters and not filter_expr:
        return source_table, None, None

    where_parts: list[str] = []
    params: list[Any] = []
    descriptions: list[str] = []

    if available is None:
        available = {r[0] for r in conn.execute(f"DESCRIBE {source_table}").fetchall()}

    # For top_n/bottom_n subqueries, create alias
    conn.execute(f"CREATE OR REPLACE VIEW _filter_src AS SELECT * FROM {source_table}")
    try:
        if filters:
            # Wrap in implicit AND
            group = {"logic": "and", "conditions": filters}
            clause = compile_filter(group, params, available)
            if clause:
                where_parts.append(clause)
            descriptions.append(f"{len(filters)} structured filter(s)")

        if filter_expr:
            safe_expr = sanitize_filter_expr(filter_expr)
            where_parts.append(f"({safe_expr})")
            descriptions.append(f"expr: {filter_expr}")

        if not where_parts:
            return source_table, None, None

        where_sql = " AND ".join(where_parts)
        # DuckDB doesn't support parameterized CREATE VIEW, so use a table
        try:
            conn.execute(
                f"CREATE OR REPLACE TABLE _filtered_view AS "
                f"SELECT * FROM {source_table} WHERE {where_sql}",
                params,
            )
        except duckdb.BinderException as exc:
            match = _UNKNOWN_COLUMN_RE.search(str(exc))
            if match:
                raise _unknown_column([match.group(1)], sorted(available)) from exc
            # Still the caller's expression, never the server's fault: the whole
            # WHERE clause comes from the request body. Report only DuckDB's
            # first line so the generated SQL is not echoed back.
            raise ProblemException(
                400, f"Filter could not be applied: {str(exc).splitlines()[0]}",
                code="invalid-filter", available=sorted(available),
            ) from exc
        matched: int = conn.execute("SELECT COUNT(*) FROM _filtered_view").fetchone()[0]
    finally:
        conn.execute("DROP VIEW IF EXISTS _filter_src")

    return "_filtered_view", matched, "; ".join(descriptions)
