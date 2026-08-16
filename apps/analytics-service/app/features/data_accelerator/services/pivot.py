"""Pivot service (§11) — compiled onto the aggregation engine's pure helpers.

Execution shape:
1. Long-format aggregation: GROUP BY rows + column via the extracted
   aggregation helpers (`_compile_where` / `_compile_group_entries` /
   `_compile_select_aggs` / `_assemble_sql`).
2. Percentage displays: window functions over the materialized long result.
3. Widening: one output column per distinct pivot value (capped), built with
   bound CASE expressions — never string-interpolated values.
4. Totals are RE-AGGREGATED from the source at the coarser grain (rows only /
   pivot dim only / none), which stays correct for non-additive functions
   (mean, nunique) where summing cells would lie.
"""

from __future__ import annotations

import tempfile
import uuid
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd
from fastapi import HTTPException

from app.api.errors import ProblemException
from app.infra.db.storage import ArtifactLayout, get_storage
from app.shared.constants import ALLOWED_AGG_FUNCTIONS, MAX_AGGREGATION_ROWS
from app.shared.data_io import load_data
from app.shared.datasets import resolve_dataset_path
from app.shared.utils.sql import quote_ident, safe_value

from ..schemas import GroupByBucket, PivotRequest, PivotResponse, PivotValue
from .aggregation import (
    _assemble_sql,
    _compile_group_entries,
    _compile_select_aggs,
    _compile_where,
)

MAX_PIVOT_COLUMNS = 200

_PCT_PARTITION = {
    "pct_of_row": "row dims",        # partition by row dims
    "pct_of_column": "pivot dim",    # partition by the pivot dim
    "pct_of_grand_total": "",        # no partition
}


def _value_alias(spec: PivotValue) -> str:
    return spec.alias or f"{spec.column}_{spec.function}"


def _cell_name(pivot_value: Any, alias: str, multi_values: bool) -> str:
    """Output column name for one (pivot value, value spec) cell."""
    label = "null" if pivot_value is None else str(pivot_value)
    return f"{label}_{alias}" if multi_values else label


def _aggregate_into(
    conn: duckdb.DuckDBPyConnection,
    table: str,
    request: PivotRequest,
    group_by: list[str | GroupByBucket],
    available: set[str],
    source: str,
) -> list[str]:
    """Run one grouped aggregation of the request's values into *table*.

    Reused at every grain: the long table (rows + column), row totals (rows
    only), column totals (pivot dim only), grand totals (no grouping).
    Returns the value aliases.
    """
    where_sql, where_binds, needs_src = _compile_where(
        request.filters, None, available)
    from_target = "_agg_src" if where_sql else source
    group_entries = _compile_group_entries(group_by, from_target)
    agg_parts, aliases, select_binds, _exprs, aggs_need_src = _compile_select_aggs(
        list(request.values), available)
    sql = _assemble_sql(
        source, from_target, where_sql, group_entries, agg_parts, aliases,
        "", None, "asc", MAX_AGGREGATION_ROWS)
    if needs_src or aggs_need_src:
        conn.execute(f"CREATE OR REPLACE VIEW _filter_src AS SELECT * FROM {source}")
    try:
        conn.execute(f"CREATE TABLE {table} AS {sql}", where_binds + select_binds)
    except duckdb.Error as e:
        raise HTTPException(400, f"Pivot query error: {e}")
    return aliases


def _apply_pct_displays(
    conn: duckdb.DuckDBPyConnection,
    request: PivotRequest,
    row_names: list[str],
    col_name: str | None,
    aliases: list[str],
) -> None:
    """Rewrite _pivot_long value columns per their display mode (windowed)."""
    if all(v.display == "value" for v in request.values):
        return
    selects = [quote_ident(n) for n in row_names]
    if col_name:
        selects.append(quote_ident(col_name))
    for spec, alias in zip(request.values, aliases):
        q = quote_ident(alias)
        if spec.display == "value":
            selects.append(q)
            continue
        if spec.display == "pct_of_row":
            partition = ", ".join(quote_ident(n) for n in row_names)
        elif spec.display == "pct_of_column":
            partition = quote_ident(col_name) if col_name else ""
        else:  # pct_of_grand_total
            partition = ""
        over = f"PARTITION BY {partition}" if partition else ""
        selects.append(
            f"CASE WHEN SUM({q}) OVER ({over}) = 0 THEN NULL "
            f"ELSE ROUND({q} * 100.0 / SUM({q}) OVER ({over}), 6) END AS {q}")
    conn.execute(
        f"CREATE OR REPLACE TABLE _pivot_long AS SELECT {', '.join(selects)} FROM _pivot_long")


async def run_pivot(request: PivotRequest, layout: ArtifactLayout,
                    *, persist: bool = True) -> PivotResponse:
    """Execute a pivot request; persists the widened result as parquet."""
    for spec in request.values:
        if spec.function not in ALLOWED_AGG_FUNCTIONS:
            raise HTTPException(
                400, f"Unknown aggregation function: {spec.function}. "
                     f"Allowed: {sorted(ALLOWED_AGG_FUNCTIONS)}")
        if request.columns is None and spec.display in ("pct_of_row", "pct_of_column"):
            raise HTTPException(
                400, f"display '{spec.display}' requires a pivot 'columns' dimension")

    file_path = request.file_path
    if not file_path and request.dataset_id:
        file_path = await resolve_dataset_path(
            request.dataset_id, sheet=request.sheet,
            version_id=request.version_id,
            version_number=request.version_number, tag=request.tag)
    conn = load_data(file_path=file_path, data=request.data)
    try:
        return _run_pivot_on_conn(conn, request, layout, persist=persist)
    finally:
        conn.close()


def _run_pivot_on_conn(conn: duckdb.DuckDBPyConnection,
                       request: PivotRequest, layout: ArtifactLayout,
                       *, persist: bool = True) -> PivotResponse:
    source = "df"
    original_count = conn.execute(f"SELECT COUNT(*) FROM {source}").fetchone()[0]
    available = {r[0] for r in conn.execute(f"DESCRIBE {source}").fetchall()}

    dim_entries: list[str | GroupByBucket] = list(request.rows)
    if request.columns is not None:
        dim_entries.append(request.columns)
    missing = [c for c in
               (e.column if isinstance(e, GroupByBucket) else e for e in dim_entries)
               if c not in available]
    if missing:
        raise HTTPException(400, f"Pivot columns not found: {missing}")
    for spec in request.values:
        if spec.column not in available:
            raise HTTPException(400, f"Value column not found: {spec.column}")

    def _dim_name(entry: str | GroupByBucket) -> str:
        if isinstance(entry, GroupByBucket):
            return entry.alias or f"{entry.column}_bucket"
        return entry

    row_names = [_dim_name(e) for e in request.rows]
    col_name = _dim_name(request.columns) if request.columns is not None else None
    if col_name in row_names:
        raise HTTPException(400, f"Pivot dimension '{col_name}' is also a row dimension")

    # 1. Long table + percentage displays.
    aliases = _aggregate_into(conn, "_pivot_long", request, dim_entries,
                              available, source)
    _apply_pct_displays(conn, request, row_names, col_name, aliases)

    # 2. Widen (or pass the long table through when there's no pivot dim).
    multi_values = len(request.values) > 1
    pivot_values: list[Any] = []
    if col_name is None:
        conn.execute("CREATE TABLE _pivot_wide AS SELECT * FROM _pivot_long")
        cell_names: list[str] = list(aliases)
    else:
        qcol = quote_ident(col_name)
        pivot_values = [r[0] for r in conn.execute(
            f"SELECT DISTINCT {qcol} FROM _pivot_long ORDER BY 1 NULLS LAST "
            f"LIMIT {MAX_PIVOT_COLUMNS + 1}").fetchall()]
        if len(pivot_values) > MAX_PIVOT_COLUMNS:
            raise ProblemException(
                400,
                f"Pivot dimension '{col_name}' has more than {MAX_PIVOT_COLUMNS} "
                f"distinct values — bucket it or filter first",
                code="too-many-pivot-columns", limit=MAX_PIVOT_COLUMNS)
        selects = [quote_ident(n) for n in row_names]
        binds: list[Any] = []
        cell_names = []
        for value in pivot_values:
            for alias in aliases:
                cell = _cell_name(value, alias, multi_values)
                cell_names.append(cell)
                selects.append(
                    f"MAX(CASE WHEN {qcol} IS NOT DISTINCT FROM ? "
                    f"THEN {quote_ident(alias)} END) AS {quote_ident(cell)}")
                binds.append(value)
        group_sql = ", ".join(quote_ident(n) for n in row_names)
        conn.execute(
            f"CREATE TABLE _pivot_wide AS SELECT {', '.join(selects)} "
            f"FROM _pivot_long GROUP BY {group_sql}", binds)

    # 3. Row totals: re-aggregate at the rows-only grain and join on. Only for
    #    value-display measures — a row total for a pct_of_row/column value is a
    #    raw sum in a different unit sitting beside percentage cells, the same
    #    reason grand totals and column totals (step 5) are value-display only.
    row_total_aliases = [a for a, s in zip(aliases, request.values) if s.display == "value"]
    if request.include_row_totals and col_name is not None and row_total_aliases:
        _aggregate_into(conn, "_pivot_row_totals", request, list(request.rows),
                        available, source)
        total_cols = ", ".join(
            f"t.{quote_ident(a)} AS {quote_ident('total_' + a)}" for a in row_total_aliases)
        join_on = " AND ".join(
            f"w.{quote_ident(n)} IS NOT DISTINCT FROM t.{quote_ident(n)}"
            for n in row_names)
        conn.execute(
            f"CREATE OR REPLACE TABLE _pivot_wide AS "
            f"SELECT w.*, {total_cols} FROM _pivot_wide w "
            f"LEFT JOIN _pivot_row_totals t ON {join_on}")
        cell_names += [f"total_{a}" for a in row_total_aliases]

    # 4. Sort + row cap.
    sort_name = request.sort_by or None
    if sort_name is not None and sort_name not in row_names:
        raise HTTPException(
            400, f"sort_by must be a row dimension, got: {sort_name}. "
                 f"Row dimensions: {row_names}")
    order_names = [sort_name] if sort_name else row_names
    order_sql = ", ".join(
        f"{quote_ident(n)} {'DESC' if request.sort_order == 'desc' else 'ASC'} NULLS LAST"
        for n in order_names)
    effective_limit = min(request.limit or MAX_AGGREGATION_ROWS, MAX_AGGREGATION_ROWS)
    conn.execute(
        f"CREATE TABLE _pivot_out AS SELECT * FROM _pivot_wide "
        f"ORDER BY {order_sql} LIMIT {effective_limit + 1}")
    fetched = conn.execute("SELECT COUNT(*) FROM _pivot_out").fetchone()[0]
    truncated = fetched > effective_limit
    if truncated:
        conn.execute(
            f"CREATE OR REPLACE TABLE _pivot_out AS SELECT * FROM _pivot_out "
            f"LIMIT {effective_limit}")

    # 5. Totals (grand + per-pivot-column), re-aggregated — value displays only.
    value_aliases = [a for a, s in zip(aliases, request.values) if s.display == "value"]
    totals: dict[str, Any] | None = None
    if value_aliases:
        _aggregate_into(conn, "_pivot_grand", request, [], available, source)
        row = conn.execute(
            f"SELECT {', '.join(quote_ident(a) for a in value_aliases)} "
            f"FROM _pivot_grand").fetchone()
        totals = {a: safe_value(v) for a, v in zip(value_aliases, row)}

    column_totals: dict[str, Any] | None = None
    if request.include_column_totals and col_name is not None and value_aliases:
        _aggregate_into(conn, "_pivot_col_totals", request, [request.columns],
                        available, source)
        column_totals = {}
        for r in conn.execute(
                f"SELECT {quote_ident(col_name)}, "
                f"{', '.join(quote_ident(a) for a in value_aliases)} "
                f"FROM _pivot_col_totals").fetchall():
            for alias, v in zip(value_aliases, r[1:]):
                cell = _cell_name(r[0], alias, multi_values)
                if cell in cell_names:
                    column_totals[cell] = safe_value(v)

    # 6. Persist + respond.
    result_cols = [r[0] for r in conn.execute("DESCRIBE _pivot_out").fetchall()]
    result_filename: str | None = None
    if persist:
        storage = get_storage()
        result_filename = f"pivot_{uuid.uuid4().hex}.parquet"
        with tempfile.TemporaryDirectory(prefix="accel_pivot_") as td:
            local = Path(td) / result_filename
            conn.execute(f"COPY _pivot_out TO '{local}' (FORMAT PARQUET)")
            storage.put_file(layout.key(result_filename), local)

    data = None
    if request.return_data:
        df = conn.execute("SELECT * FROM _pivot_out").fetchdf()
        data = df.where(pd.notnull(df), None).to_dict(orient="records")
        data = [{k: safe_value(v) for k, v in row.items()} for row in data]

    return PivotResponse(
        success=True,
        original_count=original_count,
        row_count=conn.execute("SELECT COUNT(*) FROM _pivot_out").fetchone()[0],
        columns=result_cols,
        pivot_columns=["null" if v is None else str(v) for v in pivot_values],
        data=data,
        totals=totals,
        column_totals=column_totals,
        truncated=truncated,
        result_file=result_filename,
    )
