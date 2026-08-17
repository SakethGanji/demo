"""Profiling service — column profiling, correlation, type detection."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import duckdb
from fastapi import HTTPException

from app.shared.utils.sql import quote_ident, safe_value
from app.shared.constants import is_boolean_duckdb_type, is_datetime_duckdb_type, is_numeric_duckdb_type
from app.shared.data_io import load_data
from app.shared.datasets import resolve_dataset_path

from ..schemas import ColumnProfile, HistogramBin, ProfileRequest, ProfileResponse, TopValue


class _Unrepresentable:
    """Marker for an aggregate whose true value has no finite double."""

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "<unrepresentable>"


UNREPRESENTABLE = _Unrepresentable()


def _is_representable(val: Any) -> bool:
    """False for inf/NaN — neither is JSON, and neither is a real answer."""
    try:
        return math.isfinite(val)
    except TypeError:  # not a number at all (str, date, None) — nothing to check
        return True


def eval_aggregates(
    conn: duckdb.DuckDBPyConnection, exprs: list[str], source: str = "df",
) -> list[Any]:
    """Evaluate scalar aggregate *exprs* over *source*, one query if possible.

    Returns one value per expression, positionally, with ``UNREPRESENTABLE`` in
    place of any whose value cannot be a finite double.

    Why this is not a plain SELECT: DuckDB *raises* ``OutOfRangeException`` when
    an aggregate's accumulator leaves the double range, and it kills the whole
    statement. The variance family squares its input (STDDEV/VAR, and CORR via
    STDDEV_POP), so one legitimate finite value near 1e308 overflows the sum of
    squares — which is how a single extreme cell used to take out every
    statistic for every column on the sheet. Bisecting on failure isolates the
    offending expressions and leaves their neighbours computed; the common path
    still costs exactly one query.
    """
    if not exprs:
        return []
    try:
        row = conn.execute(f"SELECT {', '.join(exprs)} FROM {source}").fetchone()
    except duckdb.OutOfRangeException:
        if len(exprs) == 1:
            return [UNREPRESENTABLE]
        mid = len(exprs) // 2
        return (eval_aggregates(conn, exprs[:mid], source)
                + eval_aggregates(conn, exprs[mid:], source))
    # An aggregate can also *return* inf/NaN instead of raising (a partial
    # overflow, 0/0). That is unrepresentable for the same reason.
    return [v if _is_representable(v) else UNREPRESENTABLE for v in row]


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
) -> dict[str, dict[str, float | None]] | None:
    """Compute pairwise correlations via a single DuckDB query using CORR()."""
    if len(numeric_cols) < 2:
        return None

    pairs: list[tuple[str, str]] = []
    for i, a in enumerate(numeric_cols):
        for j, b in enumerate(numeric_cols):
            if j > i:
                pairs.append((a, b))

    corr_exprs = [f"CORR({quote_ident(a)}, {quote_ident(b)})" for a, b in pairs]
    result = eval_aggregates(conn, corr_exprs)

    correlations: dict[str, dict[str, float]] = {c: {} for c in numeric_cols}
    for c in numeric_cols:
        correlations[c][c] = 1.0
    for idx, (a, b) in enumerate(pairs):
        # An unrepresentable coefficient (CORR overflows STDDEV_POP on a column
        # holding a value near 1e308) reads as null, the same "no coefficient"
        # the matrix already carries for a zero-variance pair — and only for
        # the pairs affected, not for the whole matrix.
        raw = result[idx]
        val = None if raw is UNREPRESENTABLE else safe_value(raw)
        correlations[a][b] = val
        correlations[b][a] = val

    return correlations


def _bin_edge(lo: float, hi: float, bucket: int, num_bins: int) -> float:
    """Edge *bucket* of *num_bins* equal bins spanning [lo, hi].

    Written as a weighted blend of the two ends rather than
    ``lo + bucket * (hi - lo) / num_bins``: SQL evaluates that left to right,
    so ``bucket * (hi - lo)`` overflowed to inf on a column reaching 1e308 and
    the bin edges came back as Infinity — not JSON, and a 500 on the way out.
    Every term here stays within [lo, hi], so the result is always finite.
    """
    t = bucket / num_bins
    return lo * (1.0 - t) + hi * t


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
        count=row_count,          # total rows — explorer derives non-null from it
        non_null_count=non_null_count,
        null_count=null_count,
        null_percent=null_percent,
        unique_count=unique_count,
        top_values=top_values,
    )

    # ----- dtype-specific stats -----
    if dtype == "numeric":
        stat_exprs = [
            ("mean", f"AVG({qcol})"),
            ("median", f"MEDIAN({qcol})"),
            ("std", f"STDDEV_SAMP({qcol})"),
            ("min", f"MIN({qcol})"),
            ("max", f"MAX({qcol})"),
            ("q25", f"QUANTILE_CONT({qcol}, 0.25)"),
            ("q75", f"QUANTILE_CONT({qcol}, 0.75)"),
        ]
        stats = eval_aggregates(conn, [expr for _, expr in stat_exprs])
        for (field, _), raw in zip(stat_exprs, stats):
            if raw is UNREPRESENTABLE:
                # Null AND named. Nulling it silently would be indistinguishable
                # from "no data", and any substitute would be a confident wrong
                # number; the other statistics on this column still stand.
                profile.unavailable_stats.append(field)
                continue
            setattr(profile, field, safe_value(raw))

        # float(): a DECIMAL column's MIN/MAX come back as Decimal, which does
        # not mix with the float arithmetic below.
        lo = None if profile.min is None else float(profile.min)
        hi = None if profile.max is None else float(profile.max)
        if include_histogram and non_null_count > 1 and unique_count > 1 \
                and lo is not None and hi is not None and lo < hi:
            num_bins = min(20, unique_count)
            # Bucketing runs in half-scale coordinates. The direct form
            # (v - lo) / ((hi - lo) / n) overflows to inf as soon as the span
            # exceeds DBL_MAX (it does for -1e308..1e308), and inf/inf is NaN,
            # which FLOOR(...)::INT then refuses to cast — a 500. Halving is
            # exact in binary floating point, so v/2 - lo/2 over half the bin
            # width is bit-identical to the direct form for every input that
            # does not overflow, and finite for the ones that do.
            half_width = (hi / 2 - lo / 2) / num_bins
            if half_width > 0:
                # Every bin 1..num_bins is emitted (LEFT JOIN a generated series,
                # COALESCE count to 0) so an empty middle bin isn't silently dropped
                # — a chart consuming the array positionally would otherwise draw
                # non-adjacent bins as adjacent and misrepresent the distribution.
                hist_rows = conn.execute(f"""
                    WITH gen AS (SELECT generate_series AS bucket FROM generate_series(1, {num_bins})),
                    counts AS (
                        SELECT
                            LEAST(GREATEST(
                                FLOOR(({qcol}::DOUBLE / 2 - ?) / ?)::INT + 1,
                            1), {num_bins}) AS bucket,
                            COUNT(*) AS cnt
                        FROM df
                        WHERE {qcol} IS NOT NULL
                        GROUP BY bucket
                    )
                    SELECT gen.bucket, COALESCE(counts.cnt, 0) AS cnt
                    FROM gen LEFT JOIN counts ON counts.bucket = gen.bucket
                    ORDER BY gen.bucket
                """, [lo / 2, half_width]).fetchall()
                profile.histogram = [
                    HistogramBin(
                        bin_start=round(_bin_edge(lo, hi, int(r[0]) - 1, num_bins), 6),
                        bin_end=round(_bin_edge(lo, hi, int(r[0]), num_bins), 6),
                        count=int(r[1]),
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
