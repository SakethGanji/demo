"""Filter compilation and application — used by sampling, profiling, aggregation."""

from __future__ import annotations

from typing import Any

import duckdb
from fastapi import HTTPException

from app.shared.utils.sql import quote_ident, sanitize_filter_expr


def compile_filter(f: dict[str, Any], params: list[Any]) -> str:
    """Compile a Filter or FilterGroup dict into a SQL WHERE clause fragment.

    Uses parameterized queries (? placeholders) for all user values to prevent injection.
    Appends values to the *params* list in order.
    """
    # FilterGroup (has 'logic' + 'conditions')
    if "logic" in f and "conditions" in f:
        logic = f["logic"].upper()
        if logic not in ("AND", "OR"):
            raise HTTPException(400, f"Invalid filter logic: {f['logic']}. Use 'and' or 'or'.")
        parts = [compile_filter(cond, params) for cond in f["conditions"]]
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
        params.append(int(val))
        return f"LENGTH(CAST({qcol} AS VARCHAR)) {len_op_map[op]} ?"
    if op == "len_between":
        if not isinstance(val, list) or len(val) != 2:
            raise HTTPException(400, f"Filter 'len_between' requires [min_len, max_len], got: {val}")
        params.extend([int(val[0]), int(val[1])])
        return f"LENGTH(CAST({qcol} AS VARCHAR)) BETWEEN ? AND ?"

    # Top/bottom N by column value
    if op == "top_n":
        n = int(val)
        return f"{qcol} >= (SELECT {qcol} FROM (SELECT DISTINCT {qcol} FROM _filter_src ORDER BY {qcol} DESC LIMIT {n}) sub ORDER BY {qcol} ASC LIMIT 1)"
    if op == "bottom_n":
        n = int(val)
        return f"{qcol} <= (SELECT {qcol} FROM (SELECT DISTINCT {qcol} FROM _filter_src ORDER BY {qcol} ASC LIMIT {n}) sub ORDER BY {qcol} DESC LIMIT 1)"

    # Percentile operators
    if op == "top_pct":
        pct = float(val)
        return f"{qcol} >= (SELECT PERCENTILE_CONT({1.0 - pct}) WITHIN GROUP (ORDER BY {qcol}) FROM _filter_src)"
    if op == "bottom_pct":
        pct = float(val)
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
        n = int(val)
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
) -> tuple[str, int | None, str | None]:
    """Apply structured filters and/or raw filter_expr to a source table.

    Returns (table_or_view_name, matched_count, human_readable_description).
    Creates _filtered_view if filters are applied.
    """
    if not filters and not filter_expr:
        return source_table, None, None

    where_parts: list[str] = []
    params: list[Any] = []
    descriptions: list[str] = []

    # For top_n/bottom_n subqueries, create alias
    conn.execute(f"CREATE OR REPLACE VIEW _filter_src AS SELECT * FROM {source_table}")

    if filters:
        # Wrap in implicit AND
        group = {"logic": "and", "conditions": filters}
        clause = compile_filter(group, params)
        if clause:
            where_parts.append(clause)
        descriptions.append(f"{len(filters)} structured filter(s)")

    if filter_expr:
        safe_expr = sanitize_filter_expr(filter_expr)
        where_parts.append(f"({safe_expr})")
        descriptions.append(f"expr: {filter_expr}")

    if not where_parts:
        conn.execute("DROP VIEW IF EXISTS _filter_src")
        return source_table, None, None

    where_sql = " AND ".join(where_parts)
    # DuckDB doesn't support parameterized CREATE VIEW, so use a table
    conn.execute(
        f"CREATE OR REPLACE TABLE _filtered_view AS SELECT * FROM {source_table} WHERE {where_sql}",
        params,
    )
    matched: int = conn.execute("SELECT COUNT(*) FROM _filtered_view").fetchone()[0]
    conn.execute("DROP VIEW IF EXISTS _filter_src")

    return "_filtered_view", matched, "; ".join(descriptions)
