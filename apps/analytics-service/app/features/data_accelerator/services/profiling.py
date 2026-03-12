"""Profiling service — column profiling, correlation, type detection."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb
from fastapi import HTTPException

from app.shared.utils.sql import quote_ident, safe_value
from app.shared.constants import is_boolean_duckdb_type, is_datetime_duckdb_type, is_numeric_duckdb_type
from app.shared.data_io import load_data
from app.shared.datasets import resolve_dataset_path

from ..schemas import ColumnProfile, HistogramBin, ProfileRequest, ProfileResponse, TopValue


def map_duckdb_type(type_str: str, unique_count: int, row_count: int) -> str:
    """Map DuckDB column type to profile dtype category."""
    if is_boolean_duckdb_type(type_str):
        return "boolean"
    if is_numeric_duckdb_type(type_str):
        return "numeric"
    if is_datetime_duckdb_type(type_str):
        return "datetime"
    # String/varchar — use cardinality to decide categorical vs text
    if row_count > 0:
        ratio = unique_count / row_count
        if ratio < 0.5 or unique_count <= 20:
            return "categorical"
    return "text"


def correlations_duckdb(
    conn: duckdb.DuckDBPyConnection, numeric_cols: list[str],
) -> dict[str, dict[str, float]] | None:
    """Compute pairwise correlations via a single DuckDB query using CORR()."""
    if len(numeric_cols) < 2:
        return None

    pairs: list[tuple[str, str]] = []
    for i, a in enumerate(numeric_cols):
        for j, b in enumerate(numeric_cols):
            if j > i:
                pairs.append((a, b))

    corr_exprs = [f"CORR({quote_ident(a)}, {quote_ident(b)})" for a, b in pairs]
    result = conn.execute(f"SELECT {', '.join(corr_exprs)} FROM df").fetchone()

    correlations: dict[str, dict[str, float]] = {c: {} for c in numeric_cols}
    for c in numeric_cols:
        correlations[c][c] = 1.0
    for idx, (a, b) in enumerate(pairs):
        val = safe_value(result[idx])
        correlations[a][b] = val
        correlations[b][a] = val

    return correlations


def profile_column_duckdb(
    conn: duckdb.DuckDBPyConnection,
    col_name: str,
    col_type_str: str,
    row_count: int,
    top_n: int,
    include_histogram: bool,
) -> ColumnProfile:
    """Profile a single column using DuckDB SQL queries."""
    qcol = quote_ident(col_name)

    # Basic counts — single query
    basic = conn.execute(f"""
        SELECT COUNT({qcol}), COUNT(DISTINCT {qcol}) FROM df
    """).fetchone()
    non_null_count, unique_count = int(basic[0]), int(basic[1])
    null_count = row_count - non_null_count
    null_percent = round(null_count / row_count * 100, 2) if row_count > 0 else 0.0

    dtype = map_duckdb_type(col_type_str, unique_count, row_count)

    # Top values
    top_rows = conn.execute(f"""
        SELECT {qcol}, COUNT(*) AS cnt
        FROM df WHERE {qcol} IS NOT NULL
        GROUP BY {qcol} ORDER BY cnt DESC LIMIT ?
    """, [top_n]).fetchall()

    top_values = [
        TopValue(
            value=safe_value(row[0]),
            count=int(row[1]),
            percent=round(int(row[1]) / row_count * 100, 2) if row_count > 0 else 0.0,
        )
        for row in top_rows
    ]

    profile = ColumnProfile(
        name=col_name,
        dtype=dtype,
        count=row_count,
        null_count=null_count,
        null_percent=null_percent,
        unique_count=unique_count,
        top_values=top_values,
    )

    # ----- dtype-specific stats -----
    if dtype == "numeric":
        stats = conn.execute(f"""
            SELECT
                AVG({qcol}),
                MEDIAN({qcol}),
                STDDEV_SAMP({qcol}),
                MIN({qcol}),
                MAX({qcol}),
                QUANTILE_CONT({qcol}, 0.25),
                QUANTILE_CONT({qcol}, 0.75)
            FROM df
        """).fetchone()
        profile.mean = safe_value(stats[0])
        profile.median = safe_value(stats[1])
        profile.std = safe_value(stats[2])
        profile.min = safe_value(stats[3])
        profile.max = safe_value(stats[4])
        profile.q25 = safe_value(stats[5])
        profile.q75 = safe_value(stats[6])

        if include_histogram and non_null_count > 1 and unique_count > 1:
            num_bins = min(20, unique_count)
            hist_rows = conn.execute(f"""
                WITH bounds AS (
                    SELECT MIN({qcol})::DOUBLE AS lo, MAX({qcol})::DOUBLE AS hi
                    FROM df WHERE {qcol} IS NOT NULL
                )
                SELECT
                    lo + (bucket - 1) * (hi - lo) / {num_bins} AS bin_start,
                    lo + bucket * (hi - lo) / {num_bins} AS bin_end,
                    cnt
                FROM (
                    SELECT
                        LEAST(GREATEST(
                            FLOOR(({qcol}::DOUBLE - lo) / ((hi - lo) / {num_bins}))::INT + 1,
                        1), {num_bins}) AS bucket,
                        COUNT(*) AS cnt,
                        ANY_VALUE(lo) AS lo,
                        ANY_VALUE(hi) AS hi
                    FROM df, bounds
                    WHERE {qcol} IS NOT NULL AND lo < hi
                    GROUP BY bucket
                ) sub
                ORDER BY bucket
            """).fetchall()
            profile.histogram = [
                HistogramBin(
                    bin_start=round(float(r[0]), 6),
                    bin_end=round(float(r[1]), 6),
                    count=int(r[2]),
                )
                for r in hist_rows
            ]

    elif dtype == "datetime":
        try:
            dt_stats = conn.execute(f"""
                SELECT MIN({qcol})::VARCHAR, MAX({qcol})::VARCHAR
                FROM df WHERE {qcol} IS NOT NULL
            """).fetchone()
            if dt_stats[0] is not None:
                profile.min_date = str(dt_stats[0])
                profile.max_date = str(dt_stats[1])
        except Exception:
            pass

    elif dtype == "text":
        if non_null_count > 0:
            text_stats = conn.execute(f"""
                SELECT
                    AVG(LENGTH(CAST({qcol} AS VARCHAR))),
                    MIN(LENGTH(CAST({qcol} AS VARCHAR))),
                    MAX(LENGTH(CAST({qcol} AS VARCHAR)))
                FROM df WHERE {qcol} IS NOT NULL
            """).fetchone()
            profile.avg_length = safe_value(text_stats[0])
            profile.min_length = int(text_stats[1]) if text_stats[1] is not None else None
            profile.max_length = int(text_stats[2]) if text_stats[2] is not None else None

    return profile


async def run_profiling(request: ProfileRequest) -> ProfileResponse:
    """Profile data columns — statistics, distributions, data quality."""
    file_path = request.file_path
    if not file_path and request.dataset_id:
        file_path = await resolve_dataset_path(
            request.dataset_id, sheet=request.sheet,
            version_id=request.version_id, version_number=request.version_number, tag=request.tag,
        )
    conn = load_data(file_path=file_path, data=request.data)
    try:
        row_count = conn.execute("SELECT COUNT(*) FROM df").fetchone()[0]

        desc_rows = conn.execute("DESCRIBE df").fetchall()
        col_info: list[tuple[str, str]] = [(r[0], r[1]) for r in desc_rows]

        if request.columns:
            available = {name for name, _ in col_info}
            missing = [c for c in request.columns if c not in available]
            if missing:
                raise HTTPException(status_code=400, detail=f"Columns not found: {missing}")
            col_info = [(n, t) for n, t in col_info if n in set(request.columns)]

        column_profiles = [
            profile_column_duckdb(
                conn, name, type_str, row_count, request.top_n, request.include_histograms
            )
            for name, type_str in col_info
        ]

        correlations = None
        if request.include_correlations:
            numeric_cols = [n for n, t in col_info if is_numeric_duckdb_type(t)]
            correlations = correlations_duckdb(conn, numeric_cols)

        memory_usage_bytes = 0
        try:
            mem_row = conn.execute(
                "SELECT estimated_size FROM duckdb_tables() WHERE table_name = 'df'"
            ).fetchone()
            if mem_row and mem_row[0]:
                memory_usage_bytes = int(mem_row[0])
            elif request.file_path:
                memory_usage_bytes = Path(request.file_path).stat().st_size
        except Exception:
            pass

        dup_count = 0
        if request.include_duplicates:
            dup_count = conn.execute(
                "SELECT (SELECT COUNT(*) FROM df) - (SELECT COUNT(*) FROM (SELECT DISTINCT * FROM df))"
            ).fetchone()[0]

        return ProfileResponse(
            success=True,
            row_count=row_count,
            column_count=len(col_info),
            columns=column_profiles,
            correlations=correlations,
            memory_usage_bytes=memory_usage_bytes,
            duplicate_row_count=int(dup_count),
        )
    finally:
        conn.close()
