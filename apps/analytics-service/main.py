"""Analytics Service - FastAPI app for data sampling, profiling, and aggregation.

Uses DuckDB for fast analytical queries on CSV, Parquet, and Excel files.
"""

from __future__ import annotations

import base64
import hashlib
import math
import re
import time
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

DATASETS_DIR = Path("/tmp/analytics/datasets")
SAMPLES_DIR = Path("/tmp/analytics/samples")

app = FastAPI(
    title="Analytics Service",
    description="Data sampling, profiling, and aggregation operations",
    version="1.0.0",
)


# =============================================================================
# Shared Helpers
# =============================================================================


def _quote_ident(name: str) -> str:
    """Quote a SQL identifier to prevent injection."""
    return '"' + name.replace('"', '""') + '"'


def _export_dataframe(
    conn_or_df, output_path: str, output_format: str, table_name: str = "df",
) -> str:
    """Write data to file. Uses DuckDB COPY for CSV/Parquet (fast), pandas for Excel."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fmt = output_format.lower()
    qtable = _quote_ident(table_name)
    if isinstance(conn_or_df, pd.DataFrame):
        df = conn_or_df
        if fmt == "csv":
            df.to_csv(path, index=False)
        elif fmt in ("xlsx", "excel"):
            df.to_excel(path, index=False, engine="openpyxl")
        elif fmt == "parquet":
            df.to_parquet(path, index=False)
        else:
            raise HTTPException(400, f"Unsupported format: {fmt}")
    else:
        conn = conn_or_df
        if fmt == "csv":
            conn.execute(f"COPY {qtable} TO '{path}' (FORMAT CSV, HEADER)")
        elif fmt == "parquet":
            conn.execute(f"COPY {qtable} TO '{path}' (FORMAT PARQUET)")
        elif fmt in ("xlsx", "excel"):
            df = conn.execute(f"SELECT * FROM {qtable}").fetchdf()
            df.to_excel(path, index=False, engine="openpyxl")
        else:
            raise HTTPException(400, f"Unsupported format: {fmt}")
    return str(path)


def _safe_value(val: Any) -> Any:
    """Convert database/numpy types to JSON-safe Python types."""
    if val is None:
        return None
    if isinstance(val, float) and (math.isnan(val) or math.isinf(val)):
        return None
    if isinstance(val, (np.integer,)):
        return int(val)
    if isinstance(val, (np.floating,)):
        v = float(val)
        return None if math.isnan(v) or math.isinf(v) else v
    if isinstance(val, np.bool_):
        return bool(val)
    if hasattr(val, "isoformat"):
        return str(val)
    return val


_DANGEROUS_SQL_RE = re.compile(
    r"\b(DROP|DELETE|INSERT|UPDATE|ALTER|CREATE|TRUNCATE|EXEC|EXECUTE|GRANT|REVOKE|UNION)\b",
    re.IGNORECASE,
)


def _sanitize_filter_expr(expr: str) -> str:
    """Validate a SQL filter expression to prevent injection."""
    if ";" in expr:
        raise HTTPException(400, "Filter expression must not contain semicolons")
    if _DANGEROUS_SQL_RE.search(expr):
        raise HTTPException(400, "Filter expression contains disallowed SQL keywords")
    return expr


def load_data(
    file_path: str | None = None,
    data: list[dict[str, Any]] | None = None,
) -> duckdb.DuckDBPyConnection:
    """Load data into a DuckDB in-memory connection as table ``df``.

    Uses DuckDB's native readers for CSV/Parquet (much faster than pandas).
    Falls back to pandas for Excel files and inline JSON data.
    """
    conn = duckdb.connect()

    if file_path:
        path = Path(file_path)
        if not path.exists():
            raise HTTPException(status_code=404, detail=f"File not found: {file_path}")

        suffix = path.suffix.lower()
        if suffix == ".csv":
            conn.execute(
                "CREATE TABLE df AS SELECT * FROM read_csv_auto(?)", [str(path)]
            )
        elif suffix == ".parquet":
            conn.execute(
                "CREATE TABLE df AS SELECT * FROM read_parquet(?)", [str(path)]
            )
        elif suffix in (".xlsx", ".xls"):
            pdf = pd.read_excel(path, engine="openpyxl")
            conn.execute("CREATE TABLE df AS SELECT * FROM pdf")
        else:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported file format: {suffix}. Supported: .csv, .parquet, .xlsx, .xls",
            )
    elif data:
        pdf = pd.DataFrame(data)
        conn.execute("CREATE TABLE df AS SELECT * FROM pdf")
    else:
        raise HTTPException(
            status_code=400, detail="Either file_path or data must be provided"
        )

    return conn


# =============================================================================
# Sampling API
# =============================================================================


class SampleRequest(BaseModel):
    """Request model for sampling endpoint."""

    file_path: str | None = Field(default=None, description="Path to CSV or Parquet file")
    data: list[dict[str, Any]] | None = Field(default=None, description="Inline JSON array of data")
    method: str = Field(
        default="random",
        description="Sampling method: random, stratified, systematic, cluster, first_n, last_n",
    )
    sample_size: int | None = Field(default=None, description="Number of rows to sample")
    sample_fraction: float | None = Field(default=None, description="Fraction of rows to sample (0-1)")
    replace: bool = Field(default=False, description="Sample with replacement (random, stratified)")
    stratify_column: str | None = Field(default=None, description="Column for stratified sampling")
    cluster_column: str | None = Field(default=None, description="Column for cluster sampling")
    num_clusters: int | None = Field(default=None, description="Number of clusters to select")
    seed: int | None = Field(default=None, description="Random seed for reproducibility")
    rounds: int = Field(default=1, description="Number of sampling rounds")
    round_sample_size: int | None = Field(default=None, description="Per-round sample size (overrides sample_size when rounds > 1)")
    round_sample_fraction: float | None = Field(default=None, description="Per-round sample fraction")
    output_path: str | None = Field(default=None, description="Save sampled data to this file path")
    output_format: str = Field(default="csv", description="Output file format: csv, xlsx, parquet")
    return_data: bool = Field(default=True, description="Include sampled rows in response (set False for large datasets when using output_path)")


class ColumnSummary(BaseModel):
    """Quick stats for one column of the sampled data."""

    name: str
    dtype: str
    nulls: int
    unique: int
    top_values: list[Any] | None = None
    min: Any | None = None
    max: Any | None = None
    mean: float | None = None


class SampleResponse(BaseModel):
    """Response model for sampling endpoint."""

    success: bool
    original_count: int
    sampled_count: int
    method: str
    columns: list[ColumnSummary] = []
    preview: list[dict[str, Any]] = []
    download_url: str | None = None
    data: list[dict[str, Any]] | None = None
    rounds_completed: int = 1
    round_counts: list[int] | None = None
    clusters_selected: list[str] | None = None
    output_path: str | None = None


def _duckdb_set_seed(conn: duckdb.DuckDBPyConnection, seed: int | None) -> None:
    """Set DuckDB random seed (normalised to 0-1 range)."""
    if seed is not None:
        conn.execute(f"SELECT setseed({(abs(seed) % 2147483647) / 2147483647.0})")


def _duckdb_sample(
    conn: duckdb.DuckDBPyConnection,
    method: str,
    n: int | None,
    frac: float | None,
    seed: int | None,
    replace: bool = False,
    stratify_column: str | None = None,
    cluster_column: str | None = None,
    num_clusters: int | None = None,
) -> tuple[duckdb.DuckDBPyConnection, list[str] | None]:
    """Sample data using DuckDB SQL. Creates a ``sampled`` table in *conn*.

    All work stays inside DuckDB — the full dataset is never loaded into pandas.
    Returns ``(conn, clusters_selected)``.
    """
    row_count: int = conn.execute("SELECT COUNT(*) FROM df").fetchone()[0]
    clusters_selected: list[str] | None = None

    if n is None and frac is None and method not in ("cluster",):
        raise HTTPException(400, "Either sample_size or sample_fraction required")

    # --- first_n / last_n -------------------------------------------------
    if method == "first_n":
        target = n if n is not None else int(row_count * frac)
        conn.execute(f"CREATE TABLE sampled AS SELECT * FROM df LIMIT {int(target)}")

    elif method == "last_n":
        target = min(n if n is not None else int(row_count * frac), row_count)
        conn.execute(f"""
            CREATE TABLE sampled AS
            WITH numbered AS (SELECT *, ROW_NUMBER() OVER () AS _rn FROM df)
            SELECT * EXCLUDE (_rn) FROM numbered WHERE _rn > {row_count - target}
        """)

    # --- random ------------------------------------------------------------
    elif method == "random":
        if n is not None:
            target = min(n, row_count) if not replace else n
        else:
            target = int(row_count * (min(frac, 1.0) if not replace else frac))

        if not replace:
            seed_clause = f", {seed}" if seed is not None else ""
            target = min(target, row_count)
            conn.execute(
                f"CREATE TABLE sampled AS SELECT * FROM df USING SAMPLE {target} ROWS (reservoir{seed_clause})"
            )
        else:
            _duckdb_set_seed(conn, seed)
            conn.execute(f"""
                CREATE TABLE sampled AS
                WITH numbered AS (
                    SELECT *, ROW_NUMBER() OVER () AS _rn FROM df
                ),
                picks AS (
                    SELECT (floor(random() * {row_count})::INT + 1) AS _rid
                    FROM generate_series(1, {target})
                )
                SELECT numbered.* EXCLUDE (_rn)
                FROM picks JOIN numbered ON numbered._rn = picks._rid
            """)

    # --- systematic --------------------------------------------------------
    elif method == "systematic":
        target = n if n is not None else int(row_count * frac)
        target = min(target, row_count)
        step = max(1, row_count // target) if target > 0 else row_count
        conn.execute(f"""
            CREATE TABLE sampled AS
            WITH numbered AS (SELECT *, ROW_NUMBER() OVER () AS _rn FROM df)
            SELECT * EXCLUDE (_rn) FROM numbered
            WHERE (_rn - 1) % {step} = 0
            LIMIT {int(target)}
        """)

    # --- stratified --------------------------------------------------------
    elif method == "stratified":
        if not stratify_column:
            raise HTTPException(400, "stratify_column required for stratified sampling")
        qcol = _quote_ident(stratify_column)
        _duckdb_set_seed(conn, seed)

        if frac is not None:
            effective_frac = min(frac, 1.0) if not replace else frac
            conn.execute(f"""
                CREATE TABLE sampled AS
                WITH ranked AS (
                    SELECT *,
                        ROW_NUMBER() OVER (PARTITION BY {qcol} ORDER BY random()) AS _rn,
                        COUNT(*) OVER (PARTITION BY {qcol}) AS _gsize
                    FROM df
                )
                SELECT * EXCLUDE (_rn, _gsize) FROM ranked
                WHERE _rn <= GREATEST(1, CEIL(_gsize * {effective_frac}))
            """)
        else:
            target = n
            conn.execute(f"""
                CREATE TABLE sampled AS
                WITH group_stats AS (
                    SELECT {qcol} AS _gkey, COUNT(*) AS _gc,
                           COUNT(*) * 1.0 / (SELECT COUNT(*) FROM df) AS _gfrac
                    FROM df GROUP BY {qcol}
                ),
                ranked AS (
                    SELECT df.*,
                        ROW_NUMBER() OVER (PARTITION BY df.{qcol} ORDER BY random()) AS _rn,
                        GREATEST(1, CEIL({target} * gs._gfrac))::INT AS _glimit
                    FROM df JOIN group_stats gs ON df.{qcol} = gs._gkey
                )
                SELECT * EXCLUDE (_rn, _glimit) FROM ranked
                WHERE _rn <= _glimit
            """)

    # --- cluster -----------------------------------------------------------
    elif method == "cluster":
        if not cluster_column:
            raise HTTPException(400, "cluster_column required for cluster sampling")
        qcol = _quote_ident(cluster_column)

        all_clusters = [r[0] for r in conn.execute(f"SELECT DISTINCT {qcol} FROM df").fetchall()]
        if num_clusters is None or num_clusters >= len(all_clusters):
            selected = all_clusters
        else:
            import random as rng
            if seed is not None:
                rng.seed(seed)
            selected = rng.sample(all_clusters, num_clusters)

        clusters_selected = [str(c) for c in selected]
        placeholders = ", ".join(["?"] * len(selected))
        conn.execute(
            f"CREATE TABLE sampled AS SELECT * FROM df WHERE {qcol} IN ({placeholders})",
            selected,
        )

    else:
        raise HTTPException(
            400,
            f"Unknown sampling method: {method}. "
            "Supported: random, stratified, systematic, cluster, first_n, last_n",
        )

    return conn, clusters_selected


def _duckdb_multi_round_sample(
    conn: duckdb.DuckDBPyConnection,
    method: str,
    rounds: int,
    n: int | None,
    frac: float | None,
    seed: int | None,
    replace: bool = False,
    stratify_column: str | None = None,
) -> tuple[duckdb.DuckDBPyConnection, list[int]]:
    """Multi-round sampling using DuckDB SQL.

    Each round samples from the remaining pool (without replacement) or the full
    dataset (with replacement).  Only the sampled rows are ever materialised.
    Creates a ``sampled`` table with a ``_sample_round`` column in *conn*.
    """
    conn.execute("CREATE TABLE _pool AS SELECT *, ROW_NUMBER() OVER () AS _rid FROM df")
    conn.execute("CREATE TABLE _selected (_rid INTEGER, _round INTEGER)")

    round_counts: list[int] = []

    for round_num in range(1, rounds + 1):
        if replace:
            source_sql = "SELECT * FROM _pool"
        else:
            source_sql = (
                "SELECT * FROM _pool p "
                "WHERE NOT EXISTS (SELECT 1 FROM _selected s WHERE s._rid = p._rid)"
            )

        pool_count: int = conn.execute(f"SELECT COUNT(*) FROM ({source_sql})").fetchone()[0]
        if pool_count == 0:
            break

        # Determine target count for this round
        if n is not None:
            target = n if replace else min(n, pool_count)
        elif frac is not None:
            target = max(1, int(pool_count * frac))
        else:
            break
        if target <= 0:
            break

        round_seed = (seed + round_num - 1) if seed is not None else None
        _duckdb_set_seed(conn, round_seed)

        if method == "systematic":
            step = max(1, pool_count // target)
            conn.execute(f"""
                INSERT INTO _selected
                SELECT _rid, {round_num} FROM (
                    SELECT _rid, ROW_NUMBER() OVER (ORDER BY _rid) AS _pos
                    FROM ({source_sql})
                ) sub
                WHERE (_pos - 1) % {step} = 0
                LIMIT {target}
            """)
        elif method == "stratified" and stratify_column:
            qcol = _quote_ident(stratify_column)
            if frac is not None:
                conn.execute(f"""
                    INSERT INTO _selected
                    SELECT _rid, {round_num} FROM (
                        SELECT _rid,
                            ROW_NUMBER() OVER (PARTITION BY {qcol} ORDER BY random()) AS _rn,
                            COUNT(*) OVER (PARTITION BY {qcol}) AS _gs
                        FROM ({source_sql})
                    ) sub
                    WHERE _rn <= GREATEST(1, CEIL(_gs * {frac}))
                """)
            else:
                conn.execute(f"""
                    INSERT INTO _selected
                    SELECT _rid, {round_num} FROM (
                        SELECT src._rid,
                            ROW_NUMBER() OVER (PARTITION BY src.{qcol} ORDER BY random()) AS _rn,
                            GREATEST(1, CEIL({target} * gs._gfrac))::INT AS _gl
                        FROM ({source_sql}) src
                        JOIN (
                            SELECT {qcol} AS _gk,
                                   COUNT(*) * 1.0 / SUM(COUNT(*)) OVER () AS _gfrac
                            FROM ({source_sql}) GROUP BY {qcol}
                        ) gs ON src.{qcol} = gs._gk
                    ) sub
                    WHERE _rn <= _gl
                """)
        else:
            # random (default for multi-round)
            conn.execute(f"""
                INSERT INTO _selected
                SELECT _rid, {round_num}
                FROM ({source_sql})
                ORDER BY random()
                LIMIT {target}
            """)

        rc: int = conn.execute(
            f"SELECT COUNT(*) FROM _selected WHERE _round = {round_num}"
        ).fetchone()[0]
        round_counts.append(rc)

    # Build final result with _sample_round column
    conn.execute("""
        CREATE TABLE sampled AS
        SELECT p.* EXCLUDE (_rid), s._round AS _sample_round
        FROM _pool p
        JOIN _selected s ON p._rid = s._rid
        ORDER BY s._round, p._rid
    """)
    conn.execute("DROP TABLE IF EXISTS _pool")
    conn.execute("DROP TABLE IF EXISTS _selected")

    return conn, round_counts


@app.post("/sample", response_model=SampleResponse)
async def sample_data(request: SampleRequest) -> SampleResponse:
    """Sample data from a file or inline data.

    All sampling is done inside DuckDB — the full dataset is never loaded into
    Python memory.  Supports: random, stratified, systematic, cluster, first_n,
    last_n.  Multi-round sampling supported for random, stratified, systematic.
    """
    conn = load_data(file_path=request.file_path, data=request.data)
    original_count: int = conn.execute("SELECT COUNT(*) FROM df").fetchone()[0]

    method = request.method.lower()
    n = request.round_sample_size or request.sample_size
    frac = request.round_sample_fraction or request.sample_fraction
    seed = request.seed

    clusters_selected: list[str] | None = None
    round_counts: list[int] | None = None

    if request.rounds > 1 and method in ("random", "stratified", "systematic"):
        conn, round_counts = _duckdb_multi_round_sample(
            conn,
            method=method,
            rounds=request.rounds,
            n=n,
            frac=frac,
            seed=seed,
            replace=request.replace,
            stratify_column=request.stratify_column,
        )
    else:
        conn, clusters_selected = _duckdb_sample(
            conn,
            method=method,
            n=request.sample_size,
            frac=request.sample_fraction,
            seed=seed,
            replace=request.replace,
            stratify_column=request.stratify_column,
            cluster_column=request.cluster_column,
            num_clusters=request.num_clusters,
        )

    sampled_count: int = conn.execute("SELECT COUNT(*) FROM sampled").fetchone()[0]

    # Export to caller-specified path if requested
    out_path = None
    if request.output_path:
        out_path = _export_dataframe(conn, request.output_path, request.output_format, table_name="sampled")

    # Always persist sampled data as parquet and provide a download URL
    SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
    ts = int(time.time() * 1000)
    sample_hash = hashlib.sha256(f"{ts}_{sampled_count}_{method}".encode()).hexdigest()[:8]
    sample_filename = f"sample_{ts}_{sample_hash}.parquet"
    sample_path = SAMPLES_DIR / sample_filename
    conn.execute(f"COPY sampled TO '{sample_path}' (FORMAT PARQUET)")
    download_url = f"/downloads/{sample_filename}"

    # Build column summaries from the sampled table
    col_info = conn.execute("PRAGMA table_info('sampled')").fetchall()
    col_summaries: list[ColumnSummary] = []
    for c in col_info:
        col_name, col_dtype = c[1], c[2]
        qcol = _quote_ident(col_name)
        stats = conn.execute(
            f"SELECT COUNT(*) - COUNT({qcol}), COUNT(DISTINCT {qcol}), "
            f"MIN({qcol}), MAX({qcol}) FROM sampled"
        ).fetchone()
        nulls, unique, cmin, cmax = stats

        mean_val = None
        if col_dtype in ("BIGINT", "INTEGER", "SMALLINT", "TINYINT", "FLOAT", "DOUBLE", "DECIMAL", "HUGEINT"):
            mean_row = conn.execute(f"SELECT AVG({qcol}) FROM sampled").fetchone()
            if mean_row and mean_row[0] is not None:
                mean_val = float(mean_row[0])

        top_vals = None
        if col_dtype == "VARCHAR" or unique <= 20:
            top_rows = conn.execute(
                f"SELECT {qcol}, COUNT(*) AS cnt FROM sampled "
                f"WHERE {qcol} IS NOT NULL GROUP BY {qcol} ORDER BY cnt DESC LIMIT 5"
            ).fetchall()
            top_vals = [_safe_value(r[0]) for r in top_rows]

        col_summaries.append(ColumnSummary(
            name=col_name,
            dtype=col_dtype,
            nulls=nulls,
            unique=unique,
            top_values=top_vals,
            min=_safe_value(cmin),
            max=_safe_value(cmax),
            mean=mean_val,
        ))

    # Preview: first 5 rows
    preview_df = conn.execute("SELECT * FROM sampled LIMIT 5").fetchdf()
    preview = [
        {k: _safe_value(v) for k, v in row.items()}
        for row in preview_df.to_dict(orient="records")
    ]

    # Materialise full rows only when explicitly requested
    sampled_data = None
    if request.return_data:
        sampled_df = conn.execute("SELECT * FROM sampled").fetchdf()
        sampled_data = sampled_df.where(pd.notnull(sampled_df), None).to_dict(orient="records")

    return SampleResponse(
        success=True,
        original_count=original_count,
        sampled_count=sampled_count,
        method=method,
        columns=col_summaries,
        preview=preview,
        download_url=download_url,
        data=sampled_data,
        rounds_completed=len(round_counts) if round_counts else 1,
        round_counts=round_counts,
        clusters_selected=clusters_selected,
        output_path=out_path,
    )


# =============================================================================
# Dataset Upload API
# =============================================================================


class UploadRequest(BaseModel):
    """Request model for dataset upload."""

    data: list[dict[str, Any]] = Field(..., description="Array of row objects")


class ColumnInfo(BaseModel):
    """Column metadata."""

    name: str
    dtype: str


class UploadResponse(BaseModel):
    """Response model for dataset upload."""

    dataset_id: str
    file_path: str
    row_count: int
    column_count: int
    columns: list[ColumnInfo]
    preview: list[dict[str, Any]]


@app.post("/upload", response_model=UploadResponse)
async def upload_dataset(request: UploadRequest) -> UploadResponse:
    """Upload inline data, persist as Parquet, return dataset_id + metadata."""
    if not request.data:
        raise HTTPException(400, "data must be a non-empty array")

    conn = load_data(data=request.data)

    ts = int(time.time() * 1000)
    hash_suffix = hashlib.sha256(f"{ts}_{len(request.data)}".encode()).hexdigest()[:8]
    dataset_id = f"ds_{ts}_{hash_suffix}"

    DATASETS_DIR.mkdir(parents=True, exist_ok=True)
    file_path = DATASETS_DIR / f"{dataset_id}.parquet"
    conn.execute(f"COPY df TO '{file_path}' (FORMAT PARQUET)")

    row_count: int = conn.execute("SELECT COUNT(*) FROM df").fetchone()[0]
    col_info = conn.execute("PRAGMA table_info('df')").fetchall()
    columns = [ColumnInfo(name=c[1], dtype=c[2]) for c in col_info]

    preview_rows = conn.execute("SELECT * FROM df LIMIT 5").fetchdf()
    preview = [
        {k: _safe_value(v) for k, v in row.items()}
        for row in preview_rows.to_dict(orient="records")
    ]

    conn.close()

    return UploadResponse(
        dataset_id=dataset_id,
        file_path=str(file_path),
        row_count=row_count,
        column_count=len(columns),
        columns=columns,
        preview=preview,
    )


class DatasetMetadataResponse(BaseModel):
    """Response model for dataset metadata lookup."""

    dataset_id: str
    file_path: str
    row_count: int
    column_count: int
    columns: list[ColumnInfo]
    preview: list[dict[str, Any]]


@app.get("/datasets/{dataset_id}", response_model=DatasetMetadataResponse)
async def get_dataset(dataset_id: str) -> DatasetMetadataResponse:
    """Return metadata for a previously uploaded dataset."""
    file_path = DATASETS_DIR / f"{dataset_id}.parquet"
    if not file_path.exists():
        raise HTTPException(404, f"Dataset not found: {dataset_id}")

    conn = load_data(file_path=str(file_path))

    row_count: int = conn.execute("SELECT COUNT(*) FROM df").fetchone()[0]
    col_info = conn.execute("PRAGMA table_info('df')").fetchall()
    columns = [ColumnInfo(name=c[1], dtype=c[2]) for c in col_info]

    preview_rows = conn.execute("SELECT * FROM df LIMIT 5").fetchdf()
    preview = [
        {k: _safe_value(v) for k, v in row.items()}
        for row in preview_rows.to_dict(orient="records")
    ]

    conn.close()

    return DatasetMetadataResponse(
        dataset_id=dataset_id,
        file_path=str(file_path),
        row_count=row_count,
        column_count=len(columns),
        columns=columns,
        preview=preview,
    )


# =============================================================================
# File Downloads
# =============================================================================


@app.get("/downloads/{filename}")
async def download_file(filename: str) -> FileResponse:
    """Serve a saved sample or export file for download."""
    # Only allow files from the samples directory
    path = SAMPLES_DIR / filename
    if not path.exists():
        raise HTTPException(404, f"File not found: {filename}")
    return FileResponse(path, filename=filename, media_type="application/octet-stream")


@app.get("/health")
async def health_check() -> dict[str, str]:
    """Health check endpoint."""
    return {"status": "healthy"}


# =============================================================================
# Data Profiling API  (DuckDB-accelerated)
# =============================================================================


class TopValue(BaseModel):
    """A frequent value in a column."""

    value: Any
    count: int
    percent: float


class HistogramBin(BaseModel):
    """A histogram bin."""

    bin_start: float
    bin_end: float
    count: int


class ColumnProfile(BaseModel):
    """Profile statistics for a single column."""

    name: str
    dtype: str  # numeric, categorical, datetime, boolean, text
    count: int
    null_count: int
    null_percent: float
    unique_count: int
    top_values: list[TopValue]
    # Numeric-only
    mean: float | None = None
    median: float | None = None
    std: float | None = None
    min: float | None = None
    max: float | None = None
    q25: float | None = None
    q75: float | None = None
    # Datetime-only
    min_date: str | None = None
    max_date: str | None = None
    # Text-only
    avg_length: float | None = None
    min_length: int | None = None
    max_length: int | None = None
    # Histogram
    histogram: list[HistogramBin] | None = None


class ProfileRequest(BaseModel):
    """Request model for profiling endpoint."""

    file_path: str | None = Field(default=None, description="Path to data file")
    data: list[dict[str, Any]] | None = Field(default=None, description="Inline JSON array of data")
    columns: list[str] | None = Field(default=None, description="Columns to profile (None = all)")
    include_histograms: bool = Field(default=True, description="Include histograms for numeric columns")
    include_correlations: bool = Field(default=False, description="Include correlation matrix")
    include_duplicates: bool = Field(default=True, description="Count duplicate rows (expensive on large datasets)")
    top_n: int = Field(default=10, description="Number of top values to return per column")


class ProfileResponse(BaseModel):
    """Response model for profiling endpoint."""

    success: bool
    row_count: int
    column_count: int
    columns: list[ColumnProfile]
    correlations: dict[str, dict[str, float]] | None = None
    memory_usage_bytes: int
    duplicate_row_count: int


_NUMERIC_DUCKDB_TYPES = frozenset({
    "TINYINT", "SMALLINT", "INTEGER", "BIGINT", "HUGEINT",
    "UTINYINT", "USMALLINT", "UINTEGER", "UBIGINT",
    "FLOAT", "DOUBLE", "REAL",
})

_DATETIME_DUCKDB_TYPES = frozenset({
    "DATE", "TIMESTAMP", "TIMESTAMPTZ", "TIMESTAMP_S",
    "TIMESTAMP_MS", "TIMESTAMP_NS", "TIME", "TIMETZ",
    "TIMESTAMP WITH TIME ZONE",
})


def _is_numeric_duckdb_type(type_str: str) -> bool:
    base = type_str.upper().split("(")[0].strip()
    return base in _NUMERIC_DUCKDB_TYPES or base.startswith("DECIMAL") or base.startswith("NUMERIC")


def _is_datetime_duckdb_type(type_str: str) -> bool:
    base = type_str.upper().split("(")[0].strip()
    return base in _DATETIME_DUCKDB_TYPES


def _is_boolean_duckdb_type(type_str: str) -> bool:
    return type_str.upper().strip() in {"BOOLEAN", "BOOL"}


def _map_duckdb_type(type_str: str, unique_count: int, row_count: int) -> str:
    """Map DuckDB column type to profile dtype category."""
    if _is_boolean_duckdb_type(type_str):
        return "boolean"
    if _is_numeric_duckdb_type(type_str):
        return "numeric"
    if _is_datetime_duckdb_type(type_str):
        return "datetime"
    # String/varchar — use cardinality to decide categorical vs text
    if row_count > 0:
        ratio = unique_count / row_count
        if ratio < 0.5 or unique_count <= 20:
            return "categorical"
    return "text"


def _correlations_duckdb(
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

    corr_exprs = [f"CORR({_quote_ident(a)}, {_quote_ident(b)})" for a, b in pairs]
    result = conn.execute(f"SELECT {', '.join(corr_exprs)} FROM df").fetchone()

    correlations: dict[str, dict[str, float]] = {c: {} for c in numeric_cols}
    for c in numeric_cols:
        correlations[c][c] = 1.0
    for idx, (a, b) in enumerate(pairs):
        val = _safe_value(result[idx])
        correlations[a][b] = val
        correlations[b][a] = val

    return correlations


def _profile_column_duckdb(
    conn: duckdb.DuckDBPyConnection,
    col_name: str,
    col_type_str: str,
    row_count: int,
    top_n: int,
    include_histogram: bool,
) -> ColumnProfile:
    """Profile a single column using DuckDB SQL queries."""
    qcol = _quote_ident(col_name)

    # Basic counts — single query
    basic = conn.execute(f"""
        SELECT COUNT({qcol}), COUNT(DISTINCT {qcol}) FROM df
    """).fetchone()
    non_null_count, unique_count = int(basic[0]), int(basic[1])
    null_count = row_count - non_null_count
    null_percent = round(null_count / row_count * 100, 2) if row_count > 0 else 0.0

    dtype = _map_duckdb_type(col_type_str, unique_count, row_count)

    # Top values
    top_rows = conn.execute(f"""
        SELECT {qcol}, COUNT(*) AS cnt
        FROM df WHERE {qcol} IS NOT NULL
        GROUP BY {qcol} ORDER BY cnt DESC LIMIT ?
    """, [top_n]).fetchall()

    top_values = [
        TopValue(
            value=_safe_value(row[0]),
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
        profile.mean = _safe_value(stats[0])
        profile.median = _safe_value(stats[1])
        profile.std = _safe_value(stats[2])
        profile.min = _safe_value(stats[3])
        profile.max = _safe_value(stats[4])
        profile.q25 = _safe_value(stats[5])
        profile.q75 = _safe_value(stats[6])

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
            profile.avg_length = _safe_value(text_stats[0])
            profile.min_length = int(text_stats[1]) if text_stats[1] is not None else None
            profile.max_length = int(text_stats[2]) if text_stats[2] is not None else None

    return profile


@app.post("/profile", response_model=ProfileResponse)
async def profile_data(request: ProfileRequest) -> ProfileResponse:
    """Profile data columns — statistics, distributions, data quality.

    All stats are computed via DuckDB columnar queries for speed.
    """
    conn = load_data(file_path=request.file_path, data=request.data)
    row_count = conn.execute("SELECT COUNT(*) FROM df").fetchone()[0]

    # Column metadata from DESCRIBE
    desc_rows = conn.execute("DESCRIBE df").fetchall()
    col_info: list[tuple[str, str]] = [(r[0], r[1]) for r in desc_rows]

    if request.columns:
        available = {name for name, _ in col_info}
        missing = [c for c in request.columns if c not in available]
        if missing:
            raise HTTPException(status_code=400, detail=f"Columns not found: {missing}")
        col_info = [(n, t) for n, t in col_info if n in set(request.columns)]

    column_profiles = [
        _profile_column_duckdb(
            conn, name, type_str, row_count, request.top_n, request.include_histograms
        )
        for name, type_str in col_info
    ]

    # Correlations — computed via DuckDB CORR() in a single pass
    correlations = None
    if request.include_correlations:
        numeric_cols = [n for n, t in col_info if _is_numeric_duckdb_type(t)]
        correlations = _correlations_duckdb(conn, numeric_cols)

    # Memory usage estimate from DuckDB catalog
    try:
        mem_row = conn.execute(
            "SELECT estimated_size FROM duckdb_tables() WHERE table_name = 'df'"
        ).fetchone()
        memory_usage_bytes = int(mem_row[0]) if mem_row and mem_row[0] else 0
    except Exception:
        memory_usage_bytes = 0

    # Duplicate rows — optional, expensive on large datasets
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


# =============================================================================
# Aggregation / Pivot API  (DuckDB SQL)
# =============================================================================


class AggregationSpec(BaseModel):
    """Specification for a single aggregation."""

    column: str
    function: str = Field(
        description="Aggregation function: sum, mean, median, count, min, max, std, nunique, first, last"
    )
    alias: str | None = Field(default=None, description="Output column name (defaults to column_function)")


class AggregateRequest(BaseModel):
    """Request model for aggregation endpoint."""

    file_path: str | None = Field(default=None, description="Path to data file")
    data: list[dict[str, Any]] | None = Field(default=None, description="Inline JSON array of data")
    group_by: list[str] = Field(description="Columns to group by")
    aggregations: list[AggregationSpec] = Field(description="Aggregation specifications")
    sort_by: str | None = Field(default=None, description="Column to sort results by")
    sort_order: str = Field(default="desc", description="Sort order: asc or desc")
    limit: int | None = Field(default=None, description="Maximum number of groups to return")
    filter_expr: str | None = Field(
        default=None,
        description="SQL WHERE clause expression to filter data before aggregation (e.g. 'sales >= 100')",
    )
    output_path: str | None = Field(default=None, description="Save aggregated data to this file path")
    output_format: str = Field(default="csv", description="Output file format: csv, xlsx, parquet")
    return_data: bool = Field(default=True, description="Include aggregated rows in response (set False for large results when using output_path)")


class AggregateResponse(BaseModel):
    """Response model for aggregation endpoint."""

    success: bool
    original_count: int
    group_count: int
    columns: list[str]
    data: list[dict[str, Any]] | None = None
    totals: dict[str, Any] | None = None
    output_path: str | None = None


ALLOWED_AGG_FUNCTIONS = {
    "sum", "mean", "median", "count", "min", "max", "std", "nunique", "first", "last",
}

_AGG_SQL_MAP = {
    "sum": "SUM",
    "mean": "AVG",
    "median": "MEDIAN",
    "count": "COUNT",
    "min": "MIN",
    "max": "MAX",
    "std": "STDDEV_SAMP",
    "first": "FIRST",
    "last": "LAST",
}


@app.post("/aggregate", response_model=AggregateResponse)
async def aggregate_data(request: AggregateRequest) -> AggregateResponse:
    """Aggregate data with group-by, sort, and optional filtering.

    Runs as a single DuckDB SQL query — fast even on large datasets.
    """
    conn = load_data(file_path=request.file_path, data=request.data)
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
    group_cols_sql = ", ".join(_quote_ident(c) for c in request.group_by)

    agg_parts: list[str] = []
    agg_aliases: list[str] = []
    for spec in request.aggregations:
        alias = spec.alias or f"{spec.column}_{spec.function}"
        agg_aliases.append(alias)
        qcol = _quote_ident(spec.column)
        qalias = _quote_ident(alias)

        if spec.function == "nunique":
            agg_parts.append(f"COUNT(DISTINCT {qcol}) AS {qalias}")
        else:
            agg_parts.append(f"{_AGG_SQL_MAP[spec.function]}({qcol}) AS {qalias}")

    sql = f"SELECT {group_cols_sql}, {', '.join(agg_parts)} FROM df"

    if request.filter_expr:
        safe_expr = _sanitize_filter_expr(request.filter_expr)
        sql += f" WHERE {safe_expr}"

    sql += f" GROUP BY {group_cols_sql}"

    if request.sort_by:
        valid_sort = set(request.group_by) | set(agg_aliases)
        if request.sort_by in valid_sort:
            order = "ASC" if request.sort_order == "asc" else "DESC"
            sql += f" ORDER BY {_quote_ident(request.sort_by)} {order}"

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
                    f"SELECT SUM({_quote_ident(alias)}) FROM agg_result"
                ).fetchone()[0]
                totals[alias] = _safe_value(total)
            except duckdb.Error:
                pass

    # Export to file if requested
    out_path = None
    if request.output_path:
        out_path = _export_dataframe(conn, request.output_path, request.output_format, table_name="agg_result")

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
        output_path=out_path,
    )


# =============================================================================
# Report Generation API
# =============================================================================


class ReportSections(BaseModel):
    """Which sections to include in the report."""

    overview: bool = True
    column_stats: bool = True
    distributions: bool = True
    top_values: bool = True
    correlations: bool = False
    data_preview: bool = True
    aggregation: bool = True


class ReportRequest(BaseModel):
    """Request model for report generation endpoint."""

    file_path: str | None = Field(default=None, description="Path to data file")
    data: list[dict[str, Any]] | None = Field(default=None, description="Inline JSON array of data")
    title: str = Field(default="Data Report", description="Report title")
    sections: ReportSections = Field(default_factory=ReportSections)
    top_n: int = Field(default=10, description="Number of top values per column")
    preview_rows: int = Field(default=10, description="Number of rows for data preview")
    group_by: list[str] | None = Field(default=None, description="Group-by columns for aggregation section")
    aggregations: list[AggregationSpec] | None = Field(default=None, description="Aggregation specs")
    output_path: str | None = Field(default=None, description="Save report to this file path")
    output_format: str = Field(default="html", description="Output format: html or markdown")


class ReportResponse(BaseModel):
    """Response model for report generation endpoint."""

    success: bool
    html: str | None = None
    markdown: str | None = None
    pdf_base64: str | None = None
    row_count: int
    column_count: int
    output_path: str | None = None


REPORT_CSS = """
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; margin: 0; padding: 20px; background: #f5f7fa; color: #2c3e50; }
.report-container { max-width: 1100px; margin: 0 auto; }
h1 { color: #2c3e50; border-bottom: 3px solid #3498db; padding-bottom: 10px; }
h2 { color: #34495e; border-bottom: 1px solid #bdc3c7; padding-bottom: 6px; margin-top: 30px; }
.overview-cards { width: 100%; }
.overview-card { display: table-cell; width: 25%; padding: 8px; }
.overview-card-inner { background: #fff; border-radius: 8px; padding: 16px; text-align: center; border: 1px solid #e1e8ed; }
.overview-card-inner .value { font-size: 28px; font-weight: 700; color: #2c3e50; }
.overview-card-inner .label { font-size: 13px; color: #7f8c8d; margin-top: 4px; }
table { border-collapse: collapse; width: 100%; margin: 12px 0; background: #fff; border-radius: 6px; overflow: hidden; }
th { background: #34495e; color: #fff; padding: 10px 12px; text-align: left; font-size: 13px; }
td { padding: 8px 12px; border-bottom: 1px solid #ecf0f1; font-size: 13px; }
tr:nth-child(even) { background: #f8f9fa; }
.bar-bg { width: 100%; background: #ecf0f1; height: 18px; border-radius: 3px; }
.bar-fill { height: 18px; background: #3498db; border-radius: 3px 0 0 3px; }
.section { background: #fff; border-radius: 8px; padding: 20px; margin-bottom: 20px; border: 1px solid #e1e8ed; }
.badge { display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 11px; font-weight: 600; }
.badge-numeric { background: #d4efdf; color: #27ae60; }
.badge-categorical { background: #d6eaf8; color: #2980b9; }
.badge-datetime { background: #fdebd0; color: #e67e22; }
.badge-text { background: #fadbd8; color: #e74c3c; }
.badge-boolean { background: #e8daef; color: #8e44ad; }
.corr-table td { text-align: center; font-size: 12px; padding: 6px; }
.meta { color: #95a5a6; font-size: 12px; margin-top: 20px; text-align: center; }
"""


def _format_bytes(n: int) -> str:
    """Format bytes to human readable string."""
    for unit in ("B", "KB", "MB", "GB"):
        if abs(n) < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024  # type: ignore[assignment]
    return f"{n:.1f} TB"


def _corr_cell_style(val: float | None) -> str:
    """Return inline background style for a correlation value."""
    if val is None:
        return "background:#f5f5f5"
    v = max(-1.0, min(1.0, val))
    if v >= 0:
        g = int(180 + 75 * (1 - v))
        return f"background:rgb({g},255,{g})"
    else:
        r = int(180 + 75 * (1 + v))
        return f"background:rgb(255,{r},{r})"


def _render_overview(row_count: int, col_count: int, dup_count: int, mem_bytes: int) -> str:
    """Render overview cards section."""
    cards = [
        ("Rows", f"{row_count:,}"),
        ("Columns", str(col_count)),
        ("Duplicates", f"{dup_count:,}"),
        ("Memory", _format_bytes(mem_bytes)),
    ]
    html = '<div class="overview-cards" style="display:table;width:100%">'
    for label, value in cards:
        html += f'''<div class="overview-card">
<div class="overview-card-inner"><div class="value">{value}</div><div class="label">{label}</div></div>
</div>'''
    html += "</div>"
    return html


def _render_column_stats(columns: list[ColumnProfile]) -> str:
    """Render column statistics table."""
    html = "<table><tr><th>Column</th><th>Type</th><th>Non-Null</th><th>Null %</th><th>Unique</th><th>Mean</th><th>Std</th><th>Min</th><th>Max</th></tr>"
    for c in columns:
        dtype_class = f"badge-{c.dtype}"
        mean_val = f"{c.mean:.2f}" if c.mean is not None else "-"
        std_val = f"{c.std:.2f}" if c.std is not None else "-"
        min_val = str(c.min if c.min is not None else (c.min_date or "-"))
        max_val = str(c.max if c.max is not None else (c.max_date or "-"))
        null_pct = f"{c.null_percent:.1f}%"
        html += f"<tr><td><strong>{c.name}</strong></td><td><span class='badge {dtype_class}'>{c.dtype}</span></td>"
        html += f"<td>{c.count - c.null_count:,}</td><td>{null_pct}</td><td>{c.unique_count:,}</td>"
        html += f"<td>{mean_val}</td><td>{std_val}</td><td>{min_val}</td><td>{max_val}</td></tr>"
    html += "</table>"
    return html


def _render_distributions(columns: list[ColumnProfile]) -> str:
    """Render distribution bar charts for numeric columns."""
    numeric = [c for c in columns if c.histogram]
    if not numeric:
        return "<p>No numeric columns with histograms.</p>"
    html = ""
    for c in numeric:
        html += f"<h3>{c.name}</h3><table><tr><th>Range</th><th>Count</th><th>Distribution</th></tr>"
        max_count = max((b.count for b in c.histogram), default=1)
        for b in c.histogram:
            pct = (b.count / max_count * 100) if max_count > 0 else 0
            html += f"<tr><td>{b.bin_start:.2f} – {b.bin_end:.2f}</td><td>{b.count:,}</td>"
            html += f'<td><div class="bar-bg"><div class="bar-fill" style="width:{pct:.0f}%"></div></div></td></tr>'
        html += "</table>"
    return html


def _render_top_values(columns: list[ColumnProfile], top_n: int) -> str:
    """Render top values for each column."""
    html = ""
    for c in columns:
        if not c.top_values:
            continue
        html += f"<h3>{c.name}</h3><table><tr><th>Value</th><th>Count</th><th>%</th><th></th></tr>"
        for tv in c.top_values[:top_n]:
            html += f"<tr><td>{tv.value}</td><td>{tv.count:,}</td><td>{tv.percent:.1f}%</td>"
            html += f'<td><div class="bar-bg"><div class="bar-fill" style="width:{tv.percent}%"></div></div></td></tr>'
        html += "</table>"
    return html


def _render_correlations(correlations: dict[str, dict[str, float]]) -> str:
    """Render correlation matrix."""
    if not correlations:
        return "<p>No numeric columns for correlation.</p>"
    cols = list(correlations.keys())
    html = '<table class="corr-table"><tr><th></th>'
    for c in cols:
        html += f"<th>{c}</th>"
    html += "</tr>"
    for r in cols:
        html += f"<tr><td><strong>{r}</strong></td>"
        for c in cols:
            val = correlations.get(c, {}).get(r)
            style = _corr_cell_style(val)
            display = f"{val:.2f}" if val is not None else "-"
            html += f'<td style="{style}">{display}</td>'
        html += "</tr>"
    html += "</table>"
    return html


def _render_data_preview(conn: duckdb.DuckDBPyConnection, preview_rows: int) -> str:
    """Render first N rows as a preview table."""
    preview_df = conn.execute(f"SELECT * FROM df LIMIT {int(preview_rows)}").fetchdf()
    html = "<table><tr>"
    for col in preview_df.columns:
        html += f"<th>{col}</th>"
    html += "</tr>"
    for _, row in preview_df.iterrows():
        html += "<tr>"
        for val in row:
            html += f"<td>{_safe_value(val)}</td>"
        html += "</tr>"
    html += "</table>"
    return html


def _render_aggregation(
    conn: duckdb.DuckDBPyConnection,
    group_by: list[str],
    aggregations: list[AggregationSpec],
) -> str:
    """Render aggregation results table."""
    group_cols_sql = ", ".join(_quote_ident(c) for c in group_by)
    agg_parts: list[str] = []
    for spec in aggregations:
        alias = spec.alias or f"{spec.column}_{spec.function}"
        qcol = _quote_ident(spec.column)
        qalias = _quote_ident(alias)
        if spec.function == "nunique":
            agg_parts.append(f"COUNT(DISTINCT {qcol}) AS {qalias}")
        else:
            agg_parts.append(f"{_AGG_SQL_MAP[spec.function]}({qcol}) AS {qalias}")

    sql = f"SELECT {group_cols_sql}, {', '.join(agg_parts)} FROM df GROUP BY {group_cols_sql}"
    try:
        agg_df = conn.execute(sql).fetchdf()
    except duckdb.Error as e:
        return f"<p>Aggregation error: {e}</p>"

    html = "<table><tr>"
    for col in agg_df.columns:
        html += f"<th>{col}</th>"
    html += "</tr>"
    for _, row in agg_df.iterrows():
        html += "<tr>"
        for val in row:
            sv = _safe_value(val)
            if isinstance(sv, float):
                html += f"<td>{sv:,.2f}</td>"
            else:
                html += f"<td>{sv}</td>"
        html += "</tr>"
    html += "</table>"
    return html


def _render_report(
    title: str,
    sections: ReportSections,
    conn: duckdb.DuckDBPyConnection,
    row_count: int,
    col_count: int,
    columns: list[ColumnProfile],
    correlations: dict[str, dict[str, float]] | None,
    dup_count: int,
    mem_bytes: int,
    top_n: int,
    preview_rows: int,
    group_by: list[str] | None,
    aggregations: list[AggregationSpec] | None,
) -> str:
    """Orchestrate all sections into a full HTML document."""
    body = ""

    if sections.overview:
        body += '<div class="section"><h2>Overview</h2>'
        body += _render_overview(row_count, col_count, dup_count, mem_bytes)
        body += "</div>"

    if sections.column_stats:
        body += '<div class="section"><h2>Column Statistics</h2>'
        body += _render_column_stats(columns)
        body += "</div>"

    if sections.distributions:
        body += '<div class="section"><h2>Distributions</h2>'
        body += _render_distributions(columns)
        body += "</div>"

    if sections.top_values:
        body += '<div class="section"><h2>Top Values</h2>'
        body += _render_top_values(columns, top_n)
        body += "</div>"

    if sections.correlations and correlations:
        body += '<div class="section"><h2>Correlations</h2>'
        body += _render_correlations(correlations)
        body += "</div>"

    if sections.data_preview:
        body += '<div class="section"><h2>Data Preview</h2>'
        body += _render_data_preview(conn, preview_rows)
        body += "</div>"

    if sections.aggregation and group_by and aggregations:
        body += '<div class="section"><h2>Aggregation</h2>'
        body += _render_aggregation(conn, group_by, aggregations)
        body += "</div>"

    from datetime import datetime

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>{title}</title>
<style>{REPORT_CSS}</style></head>
<body><div class="report-container">
<h1>{title}</h1>
{body}
<div class="meta">Generated on {timestamp} &middot; {row_count:,} rows &times; {col_count} columns</div>
</div></body></html>"""


def _render_markdown_report(
    title: str,
    sections: ReportSections,
    conn: duckdb.DuckDBPyConnection,
    row_count: int,
    col_count: int,
    columns: list[ColumnProfile],
    correlations: dict[str, dict[str, float]] | None,
    dup_count: int,
    mem_bytes: int,
    top_n: int,
    preview_rows: int,
    group_by: list[str] | None,
    aggregations: list[AggregationSpec] | None,
) -> str:
    """Generate a Markdown data report."""
    lines: list[str] = [f"# {title}", ""]

    if sections.overview:
        lines.append("## Overview")
        lines.append("")
        lines.append(f"| Metric | Value |")
        lines.append(f"|--------|-------|")
        lines.append(f"| Rows | {row_count:,} |")
        lines.append(f"| Columns | {col_count} |")
        lines.append(f"| Duplicates | {dup_count:,} |")
        lines.append(f"| Memory | {_format_bytes(mem_bytes)} |")
        lines.append("")

    if sections.column_stats:
        lines.append("## Column Statistics")
        lines.append("")
        lines.append("| Column | Type | Non-Null | Null % | Unique | Mean | Std | Min | Max |")
        lines.append("|--------|------|----------|--------|--------|------|-----|-----|-----|")
        for c in columns:
            mean_v = f"{c.mean:.2f}" if c.mean is not None else "-"
            std_v = f"{c.std:.2f}" if c.std is not None else "-"
            min_v = str(c.min if c.min is not None else (c.min_date or "-"))
            max_v = str(c.max if c.max is not None else (c.max_date or "-"))
            lines.append(
                f"| {c.name} | {c.dtype} | {c.count - c.null_count:,} | {c.null_percent:.1f}% "
                f"| {c.unique_count:,} | {mean_v} | {std_v} | {min_v} | {max_v} |"
            )
        lines.append("")

    if sections.top_values:
        lines.append("## Top Values")
        lines.append("")
        for c in columns:
            if not c.top_values:
                continue
            lines.append(f"### {c.name}")
            lines.append("")
            lines.append("| Value | Count | % |")
            lines.append("|-------|-------|---|")
            for tv in c.top_values[:top_n]:
                lines.append(f"| {tv.value} | {tv.count:,} | {tv.percent:.1f}% |")
            lines.append("")

    if sections.correlations and correlations:
        lines.append("## Correlations")
        lines.append("")
        cols = list(correlations.keys())
        lines.append("| | " + " | ".join(cols) + " |")
        lines.append("|" + "|".join(["---"] * (len(cols) + 1)) + "|")
        for r in cols:
            vals = []
            for c in cols:
                v = correlations.get(c, {}).get(r)
                vals.append(f"{v:.2f}" if v is not None else "-")
            lines.append(f"| **{r}** | " + " | ".join(vals) + " |")
        lines.append("")

    if sections.data_preview:
        preview_df = conn.execute(f"SELECT * FROM df LIMIT {int(preview_rows)}").fetchdf()
        lines.append("## Data Preview")
        lines.append("")
        lines.append("| " + " | ".join(str(c) for c in preview_df.columns) + " |")
        lines.append("|" + "|".join(["---"] * len(preview_df.columns)) + "|")
        for _, row in preview_df.iterrows():
            lines.append("| " + " | ".join(str(_safe_value(v)) for v in row) + " |")
        lines.append("")

    if sections.aggregation and group_by and aggregations:
        lines.append("## Aggregation")
        lines.append("")
        group_cols_sql = ", ".join(_quote_ident(c) for c in group_by)
        agg_parts: list[str] = []
        for spec in aggregations:
            alias = spec.alias or f"{spec.column}_{spec.function}"
            qcol = _quote_ident(spec.column)
            qalias = _quote_ident(alias)
            if spec.function == "nunique":
                agg_parts.append(f"COUNT(DISTINCT {qcol}) AS {qalias}")
            else:
                agg_parts.append(f"{_AGG_SQL_MAP[spec.function]}({qcol}) AS {qalias}")
        sql = f"SELECT {group_cols_sql}, {', '.join(agg_parts)} FROM df GROUP BY {group_cols_sql}"
        try:
            agg_df = conn.execute(sql).fetchdf()
            lines.append("| " + " | ".join(str(c) for c in agg_df.columns) + " |")
            lines.append("|" + "|".join(["---"] * len(agg_df.columns)) + "|")
            for _, row in agg_df.iterrows():
                vals = []
                for v in row:
                    sv = _safe_value(v)
                    vals.append(f"{sv:,.2f}" if isinstance(sv, float) else str(sv))
                lines.append("| " + " | ".join(vals) + " |")
        except duckdb.Error as e:
            lines.append(f"Aggregation error: {e}")
        lines.append("")

    from datetime import datetime
    lines.append(f"---\n_Generated on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} "
                 f"— {row_count:,} rows x {col_count} columns_")

    return "\n".join(lines)


@app.post("/report", response_model=ReportResponse)
async def generate_report(request: ReportRequest) -> ReportResponse:
    """Generate an HTML data report with profiling, distributions, and aggregation.

    Email-safe HTML output (inline CSS, no JS).
    """
    conn = load_data(file_path=request.file_path, data=request.data)
    row_count = conn.execute("SELECT COUNT(*) FROM df").fetchone()[0]

    # Column metadata
    desc_rows = conn.execute("DESCRIBE df").fetchall()
    col_info: list[tuple[str, str]] = [(r[0], r[1]) for r in desc_rows]

    # Profile all columns
    column_profiles = [
        _profile_column_duckdb(conn, name, type_str, row_count, request.top_n, True)
        for name, type_str in col_info
    ]

    # Correlations — single-pass DuckDB CORR()
    correlations = None
    if request.sections.correlations:
        numeric_cols = [n for n, t in col_info if _is_numeric_duckdb_type(t)]
        correlations = _correlations_duckdb(conn, numeric_cols)

    # Memory estimate
    try:
        mem_row = conn.execute(
            "SELECT estimated_size FROM duckdb_tables() WHERE table_name = 'df'"
        ).fetchone()
        mem_bytes = int(mem_row[0]) if mem_row and mem_row[0] else 0
    except Exception:
        mem_bytes = 0

    # Duplicate count — skip for large datasets (> 500K rows) unless overview is on
    dup_count = 0
    if request.sections.overview and row_count <= 500_000:
        dup_count = conn.execute(
            "SELECT (SELECT COUNT(*) FROM df) - (SELECT COUNT(*) FROM (SELECT DISTINCT * FROM df))"
        ).fetchone()[0]

    # Validate aggregation specs if provided
    if request.aggregations:
        available = {name for name, _ in col_info}
        for spec in request.aggregations:
            if spec.function not in ALLOWED_AGG_FUNCTIONS:
                raise HTTPException(400, f"Unknown aggregation function: {spec.function}")
            if spec.column not in available:
                raise HTTPException(400, f"Aggregation column not found: {spec.column}")

    html = _render_report(
        title=request.title,
        sections=request.sections,
        conn=conn,
        row_count=row_count,
        col_count=len(col_info),
        columns=column_profiles,
        correlations=correlations,
        dup_count=int(dup_count),
        mem_bytes=mem_bytes,
        top_n=request.top_n,
        preview_rows=request.preview_rows,
        group_by=request.group_by,
        aggregations=request.aggregations,
    )

    out_path = None
    result_html: str | None = html
    result_md: str | None = None
    result_pdf_b64: str | None = None

    fmt = request.output_format.lower()
    if fmt == "markdown":
        result_md = _render_markdown_report(
            title=request.title,
            sections=request.sections,
            conn=conn,
            row_count=row_count,
            col_count=len(col_info),
            columns=column_profiles,
            correlations=correlations,
            dup_count=int(dup_count),
            mem_bytes=mem_bytes,
            top_n=request.top_n,
            preview_rows=request.preview_rows,
            group_by=request.group_by,
            aggregations=request.aggregations,
        )
        result_html = None
    elif fmt == "pdf":
        from weasyprint import HTML as WeasyHTML

        pdf_bytes = WeasyHTML(string=html).write_pdf()
        result_pdf_b64 = base64.b64encode(pdf_bytes).decode("ascii")
        result_html = None

    if request.output_path:
        path = Path(request.output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        if fmt == "markdown":
            path.write_text(result_md, encoding="utf-8")
        elif fmt == "pdf" and result_pdf_b64:
            path.write_bytes(base64.b64decode(result_pdf_b64))
        else:
            path.write_text(html, encoding="utf-8")
        out_path = str(path)

    return ReportResponse(
        success=True,
        html=result_html,
        markdown=result_md,
        pdf_base64=result_pdf_b64,
        row_count=row_count,
        column_count=len(col_info),
        output_path=out_path,
    )


# =============================================================================
# Bank Statement API (Mock for Demo)
# =============================================================================

class BankStatementRequest(BaseModel):
    """Request model for bank statement endpoint."""

    account_number: str = Field(..., description="Customer account number")
    account_holder: str = Field(default="Account Holder", description="Account holder name")
    from_date: str = Field(..., description="Start date (YYYY-MM-DD)")
    to_date: str = Field(..., description="End date (YYYY-MM-DD)")
    statement_type: str = Field(
        default="full",
        description="Statement type: full, summary, credits, debits",
    )
    include_running_balance: bool = Field(
        default=True, description="Include running balance per transaction"
    )


class BankStatementResponse(BaseModel):
    """Response model for bank statement endpoint."""

    account_number: str
    account_holder: str
    statement_period: dict[str, str]
    opening_balance: float
    closing_balance: float
    total_credits: float
    total_debits: float
    transaction_count: int
    transactions: list[dict[str, Any]]


@app.post("/bank/statement", response_model=BankStatementResponse)
async def get_bank_statement(request: BankStatementRequest) -> BankStatementResponse:
    """
    Get bank statement for a customer account.

    This is a MOCK endpoint for demo purposes.
    Returns realistic-looking sample transaction data.
    """
    import random
    from datetime import datetime, timedelta

    # Parse dates
    try:
        from_date = datetime.strptime(request.from_date, "%Y-%m-%d")
        to_date = datetime.strptime(request.to_date, "%Y-%m-%d")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid date format: {e}")

    # Use account number as seed for consistent demo data per account
    seed = sum(ord(c) for c in request.account_number)
    random.seed(seed)

    # Sample transaction descriptions
    credit_descriptions = [
        ("Salary Credit - ACME Corp", "SAL"),
        ("NEFT-HDFC-John Smith", "NEFT"),
        ("IMPS-987654-Refund", "IMPS"),
        ("Interest Credit", "INT"),
        ("Cash Deposit - Branch", "DEP"),
        ("UPI-Google Pay-Cashback", "UPI"),
        ("RTGS-Business Payment", "RTGS"),
        ("Dividend Credit - MF", "DIV"),
    ]

    debit_descriptions = [
        ("ATM-WDL-SBI ATM MG Road", "ATM"),
        ("POS-Amazon India", "POS"),
        ("BILLPAY-Electricity BESCOM", "BILL"),
        ("UPI-Swiggy", "UPI"),
        ("UPI-Zomato", "UPI"),
        ("NEFT-Rent Payment", "NEFT"),
        ("EMI-HDFC Home Loan", "EMI"),
        ("POS-BigBasket", "POS"),
        ("UPI-PhonePe-Insurance", "UPI"),
        ("ATM-WDL-ICICI ATM Kormangala", "ATM"),
        ("POS-Reliance Fresh", "POS"),
        ("AUTOPAY-Netflix", "AUTO"),
        ("AUTOPAY-Spotify", "AUTO"),
        ("POS-Shell Petrol", "POS"),
    ]

    # Generate transactions
    transactions: list[dict[str, Any]] = []
    num_days = (to_date - from_date).days
    num_transactions = min(max(num_days, 5), 50)  # At least 5, max 50

    opening_balance = round(random.uniform(25000, 150000), 2)
    running_balance = opening_balance
    total_credits = 0.0
    total_debits = 0.0

    for _ in range(num_transactions):
        # Random date within range
        days_offset = random.randint(0, max(num_days, 1))
        txn_date = from_date + timedelta(days=days_offset)
        txn_time = f"{random.randint(8, 20):02d}:{random.randint(0, 59):02d}:{random.randint(0, 59):02d}"

        # 35% credits, 65% debits (realistic spending pattern)
        is_credit = random.random() < 0.35

        if is_credit:
            desc, txn_code = random.choice(credit_descriptions)
            # Credits tend to be larger (salary, transfers)
            amount = round(random.choice([
                random.uniform(500, 2000),      # Small credits
                random.uniform(5000, 15000),    # Medium credits
                random.uniform(30000, 80000),   # Salary range
            ]), 2)
            txn_type = "CR"
            running_balance += amount
            total_credits += amount
        else:
            desc, txn_code = random.choice(debit_descriptions)
            # Debits vary more
            amount = round(random.choice([
                random.uniform(50, 500),        # Small purchases
                random.uniform(500, 2000),      # Medium purchases
                random.uniform(2000, 10000),    # Large purchases/bills
                random.uniform(10000, 25000),   # EMIs/rent
            ]), 2)
            txn_type = "DR"
            running_balance -= amount
            total_debits += amount

        txn: dict[str, Any] = {
            "date": txn_date.strftime("%Y-%m-%d"),
            "time": txn_time,
            "description": desc,
            "type": txn_type,
            "amount": amount,
            "reference": f"{txn_code}{random.randint(10000000, 99999999)}",
        }

        if request.include_running_balance:
            txn["balance"] = round(running_balance, 2)

        transactions.append(txn)

    # Sort by date and time
    transactions.sort(key=lambda x: (x["date"], x["time"]))

    # Recalculate running balance after sorting
    if request.include_running_balance:
        running = opening_balance
        for txn in transactions:
            if txn["type"] == "CR":
                running += txn["amount"]
            else:
                running -= txn["amount"]
            txn["balance"] = round(running, 2)

    # Filter based on statement type
    if request.statement_type == "credits":
        transactions = [t for t in transactions if t["type"] == "CR"]
    elif request.statement_type == "debits":
        transactions = [t for t in transactions if t["type"] == "DR"]
    elif request.statement_type == "summary":
        transactions = []

    closing_balance = round(opening_balance + total_credits - total_debits, 2)

    return BankStatementResponse(
        account_number=request.account_number,
        account_holder=request.account_holder,
        statement_period={
            "from": request.from_date,
            "to": request.to_date,
        },
        opening_balance=opening_balance,
        closing_balance=closing_balance,
        total_credits=round(total_credits, 2),
        total_debits=round(total_debits, 2),
        transaction_count=len(transactions),
        transactions=transactions,
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8001)
