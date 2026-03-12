"""Aggregation service."""

from __future__ import annotations

import uuid
from typing import Any

import duckdb
import pandas as pd
from fastapi import HTTPException

from app.shared.utils.sql import quote_ident, safe_value, sanitize_filter_expr
from app.shared.constants import AGG_SQL_MAP, ALLOWED_AGG_FUNCTIONS
from app.shared.data_io import load_data
from app.shared.datasets import resolve_dataset_path
from app.infra.db.storage import get_storage, sample_key

from ..schemas import AggregateRequest, AggregateResponse


async def run_aggregation(request: AggregateRequest) -> AggregateResponse:
    """Aggregate data with group-by, sort, and optional filtering."""
    file_path = request.file_path
    if not file_path and request.dataset_id:
        file_path = await resolve_dataset_path(
            request.dataset_id, sheet=request.sheet,
            version_id=request.version_id, version_number=request.version_number, tag=request.tag,
        )
    conn = load_data(file_path=file_path, data=request.data)
    try:
        original_count = conn.execute("SELECT COUNT(*) FROM df").fetchone()[0]

        # Validate columns exist
        available = {r[0] for r in conn.execute("DESCRIBE df").fetchall()}

        missing = [c for c in request.group_by if c not in available]
        if missing:
            raise HTTPException(status_code=400, detail=f"Group-by columns not found: {missing}")

        for spec in request.aggregations:
            if spec.column not in available:
                raise HTTPException(status_code=400, detail=f"Aggregation column not found: {spec.column}")
            if spec.function not in ALLOWED_AGG_FUNCTIONS:
                raise HTTPException(
                    status_code=400,
                    detail=f"Unknown aggregation function: {spec.function}. Allowed: {sorted(ALLOWED_AGG_FUNCTIONS)}",
                )

        # ---- Build SQL ----
        group_cols_sql = ", ".join(quote_ident(c) for c in request.group_by)

        agg_parts: list[str] = []
        agg_aliases: list[str] = []
        for spec in request.aggregations:
            alias = spec.alias or f"{spec.column}_{spec.function}"
            agg_aliases.append(alias)
            qcol = quote_ident(spec.column)
            qalias = quote_ident(alias)

            if spec.function == "nunique":
                agg_parts.append(f"COUNT(DISTINCT {qcol}) AS {qalias}")
            else:
                agg_parts.append(f"{AGG_SQL_MAP[spec.function]}({qcol}) AS {qalias}")

        sql = f"SELECT {group_cols_sql}, {', '.join(agg_parts)} FROM df"

        if request.filter_expr:
            safe_expr = sanitize_filter_expr(request.filter_expr)
            sql += f" WHERE {safe_expr}"

        sql += f" GROUP BY {group_cols_sql}"

        if request.sort_by:
            valid_sort = set(request.group_by) | set(agg_aliases)
            if request.sort_by in valid_sort:
                order = "ASC" if request.sort_order == "asc" else "DESC"
                sql += f" ORDER BY {quote_ident(request.sort_by)} {order}"

        if request.limit is not None:
            sql += f" LIMIT {int(request.limit)}"

        try:
            conn.execute(f"CREATE TABLE agg_result AS {sql}")
        except duckdb.Error as e:
            raise HTTPException(status_code=400, detail=f"Query error: {e}")

        group_count: int = conn.execute("SELECT COUNT(*) FROM agg_result").fetchone()[0]
        result_cols = [r[0] for r in conn.execute("DESCRIBE agg_result").fetchall()]

        # Grand totals — computed in DuckDB
        totals: dict[str, Any] = {}
        for alias in agg_aliases:
            if alias in result_cols:
                try:
                    total = conn.execute(
                        f"SELECT SUM({quote_ident(alias)}) FROM agg_result"
                    ).fetchone()[0]
                    totals[alias] = safe_value(total)
                except duckdb.Error:
                    pass

        # Persist result as parquet
        storage = get_storage()
        result_filename = f"agg_{uuid.uuid4().hex}.parquet"
        key = sample_key(result_filename)
        storage.ensure_dir("samples")
        result_path = storage.resolve(key)
        conn.execute(f"COPY agg_result TO '{result_path}' (FORMAT PARQUET)")

        # Materialise rows only when caller wants them
        result_data = None
        if request.return_data:
            result_df = conn.execute("SELECT * FROM agg_result").fetchdf()
            result_data = result_df.where(pd.notnull(result_df), None).to_dict(orient="records")

        return AggregateResponse(
            success=True,
            original_count=original_count,
            group_count=group_count,
            columns=result_cols,
            data=result_data,
            totals=totals if totals else None,
            result_file=result_filename,
        )
    finally:
        conn.close()
