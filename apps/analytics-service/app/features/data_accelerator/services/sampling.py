"""Sampling service — pipeline orchestration, goal validation, column summaries."""

from __future__ import annotations

import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, NamedTuple

import duckdb
import pandas as pd
from fastapi import HTTPException

from app.infra.db.storage import ArtifactLayout, get_storage
from app.api.errors import ProblemException
from app.shared.utils.sql import quote_ident, safe_value
from app.shared.data_io import load_data
from app.shared.datasets import (
    _find_sheet,
    get_version_sheet_rows,
    resolve_dataset_path,
    resolve_version,
    resolve_version_sheet_path,
)
from app.features.quality import repo as quality_repo
from app.shared.filters import apply_filters
from app.shared.schemas import ColumnSummary

from .methods import (
    duckdb_set_seed,
    sample_cluster,
    sample_llm_semantic,
    sample_random,
    sample_stratified,
    sample_systematic,
    sample_time_stratified,
    sample_weighted,
)
from ..schemas import (
    CoordinatedSampleRequest,
    CoordinatedSampleResponse,
    DistributionGoals,
    GoalValidationResult,
    RelatedSheetSample,
    ReproducibilityInfo,
    SampleRequest,
    SampleResponse,
    SamplingStep,
    StepResult,
)


def build_column_summaries(conn: duckdb.DuckDBPyConnection, table: str) -> list[ColumnSummary]:
    """Build column summaries from a DuckDB table."""
    col_info = conn.execute(f"PRAGMA table_info('{table}')").fetchall()
    col_summaries: list[ColumnSummary] = []
    for c in col_info:
        col_name, col_dtype = c[1], c[2]
        qcol = quote_ident(col_name)
        stats = conn.execute(
            f"SELECT COUNT(*) - COUNT({qcol}), COUNT(DISTINCT {qcol}), "
            f"MIN({qcol}), MAX({qcol}) FROM {table}"
        ).fetchone()
        nulls, unique, cmin, cmax = stats

        mean_val = None
        if col_dtype in ("BIGINT", "INTEGER", "SMALLINT", "TINYINT", "FLOAT", "DOUBLE", "DECIMAL", "HUGEINT"):
            mean_row = conn.execute(f"SELECT AVG({qcol}) FROM {table}").fetchone()
            if mean_row and mean_row[0] is not None:
                mean_val = float(mean_row[0])

        top_vals = None
        if col_dtype == "VARCHAR" or unique <= 20:
            top_rows = conn.execute(
                f"SELECT {qcol}, COUNT(*) AS cnt FROM {table} "
                f"WHERE {qcol} IS NOT NULL GROUP BY {qcol} ORDER BY cnt DESC LIMIT 5"
            ).fetchall()
            top_vals = [safe_value(r[0]) for r in top_rows]

        col_summaries.append(ColumnSummary(
            name=col_name,
            dtype=col_dtype,
            nulls=nulls,
            unique=unique,
            top_values=top_vals,
            min=safe_value(cmin),
            max=safe_value(cmax),
            mean=mean_val,
        ))
    return col_summaries


def validate_goals(
    conn: duckdb.DuckDBPyConnection,
    sampled_table: str,
    target_total_volume: int,
    distribution_goals: DistributionGoals | None,
) -> GoalValidationResult:
    """Validate the final sample against the specified distribution goals."""
    actual_total: int = conn.execute(f"SELECT COUNT(*) FROM {sampled_table}").fetchone()[0]
    met = True
    warnings: list[str] = []
    class_min_results: dict[str, dict[str, Any]] | None = None
    dist_results: dict[str, dict[str, Any]] | None = None

    if actual_total != target_total_volume:
        if actual_total < target_total_volume:
            warnings.append(
                f"Target volume {target_total_volume} not reached: got {actual_total} rows"
            )
            met = False

    if distribution_goals:
        qcol = quote_ident(distribution_goals.column)

        if distribution_goals.class_minimums:
            class_min_results = {}
            for class_val, min_count in distribution_goals.class_minimums.items():
                actual = conn.execute(
                    f"SELECT COUNT(*) FROM {sampled_table} WHERE CAST({qcol} AS VARCHAR) = ?",
                    [class_val],
                ).fetchone()[0]
                passed = actual >= min_count
                if not passed:
                    met = False
                    warnings.append(f"Class '{class_val}': needed {min_count}, got {actual}")
                class_min_results[class_val] = {
                    "required": min_count, "actual": actual, "met": passed,
                }

        if distribution_goals.target_distribution:
            dist_results = {}
            for class_val, target_pct in distribution_goals.target_distribution.items():
                actual = conn.execute(
                    f"SELECT COUNT(*) FROM {sampled_table} WHERE CAST({qcol} AS VARCHAR) = ?",
                    [class_val],
                ).fetchone()[0]
                actual_pct = actual / actual_total if actual_total > 0 else 0.0
                tolerance = 0.05
                passed = abs(actual_pct - target_pct) <= tolerance
                if not passed:
                    met = False
                    warnings.append(
                        f"Distribution '{class_val}': target {target_pct:.1%}, got {actual_pct:.1%}"
                    )
                dist_results[class_val] = {
                    "target_pct": target_pct,
                    "actual_pct": round(actual_pct, 4),
                    "actual_count": actual,
                    "met": passed,
                }

    return GoalValidationResult(
        met=met,
        target_total_volume=target_total_volume,
        actual_total=actual_total,
        class_minimum_results=class_min_results,
        distribution_results=dist_results,
        warnings=warnings,
    )


def _referenced_columns(request: SampleRequest) -> list[tuple[str, str]]:
    """Every column name the caller supplied, paired with the field it came from.

    The label is what makes the 400 actionable: "sort_by" and
    "sampling_steps[2].time_column" fail for the same reason but are fixed in
    different places in the request body.
    """
    refs: list[tuple[str, str]] = []
    if request.sort_by:
        refs.append(("sort_by", request.sort_by))
    for col in request.deduplicate_columns or ():
        refs.append(("deduplicate_columns", col))
    if request.distribution_goals:
        refs.append(("distribution_goals.column", request.distribution_goals.column))
    for idx, step in enumerate(request.sampling_steps):
        where = f"sampling_steps[{idx}]"
        for attr in ("stratify_column", "cluster_column", "weight_column",
                     "time_column", "text_column"):
            value = getattr(step, attr, None)
            if value:
                refs.append((f"{where}.{attr}", value))
        for col in step.deduplicate_columns or ():
            refs.append((f"{where}.deduplicate_columns", col))
    return refs


def validate_request_columns(
    conn: duckdb.DuckDBPyConnection, source: str, request: SampleRequest,
) -> None:
    """Reject unknown column references before any sampling work happens.

    Every one of these names is interpolated into SQL (or handed to pandas)
    further down, where an unknown name surfaces as a DuckDB BinderException or
    a pandas KeyError — i.e. an opaque 500 with no field-level information.
    ``sort_by`` was worse than that: it was silently skipped, so the response
    came back ``success: true`` with the requested sort echoed into
    ``reproducibility.post_processing`` and the rows in pipeline order.

    Validating up front gives sampling the same ``unknown-column`` 400 contract
    that ``/aggregate`` (aggregation.py) and ``/pivot`` (pivot.py) already
    publish, so a UI can highlight the offending field instead of guessing.
    """
    available = [r[0] for r in conn.execute(f"DESCRIBE {source}").fetchall()]
    known = set(available)
    missing = [(field, col) for field, col in _referenced_columns(request)
               if col not in known]
    if not missing:
        return
    described = ", ".join(f"{field}={col!r}" for field, col in missing)
    raise ProblemException(
        400,
        f"Column not found: {described}. Valid options: {sorted(known)}",
        code="unknown-column",
        columns=sorted({col for _, col in missing}),
        fields=sorted({field for field, _ in missing}),
        available=sorted(known),
    )


async def execute_step(
    conn: duckdb.DuckDBPyConnection,
    step: SamplingStep,
    pool_table: str,
    seed: int | None,
    step_index: int,
) -> tuple[pd.DataFrame, StepResult]:
    """Execute a single sampling step (possibly multi-round) against the current pool.

    Returns (selected_df, step_result).
    """
    method = step.method.lower()
    pool_count: int = conn.execute(f"SELECT COUNT(*) FROM {pool_table}").fetchone()[0]
    step_warnings: list[str] = []
    class_counts: dict[str, int] | None = None
    filter_matched: int | None = None

    # Apply filters (structured and/or raw expression)
    source_table = pool_table
    filter_desc: str | None = None
    if step.filters or step.filter_expr:
        source_table, filter_matched, filter_desc = apply_filters(
            conn, pool_table, step.filters, step.filter_expr,
        )
        if filter_matched == 0:
            step_warnings.append(f"Filters matched 0 rows")
            return pd.DataFrame(), StepResult(
                step_index=step_index, method=method, rows_selected=0,
                pool_before=pool_count, pool_after=pool_count,
                filter_applied=filter_desc, filter_matched=0,
                warnings=step_warnings,
            )

    all_round_results: list[pd.DataFrame] = []
    per_round_counts: list[int] = []
    rounds = max(1, step.rounds)

    for round_num in range(rounds):
        round_seed = (seed + step_index * 100 + round_num) if seed is not None else None

        # For multi-round without replacement, remove previous rounds' picks from source
        if round_num > 0 and not step.replace and all_round_results:
            prev = pd.concat(all_round_results, ignore_index=True)
            conn.execute("CREATE OR REPLACE TABLE _prev_round AS SELECT * FROM prev")
            src_cols = conn.execute(f"PRAGMA table_info('{source_table}')").fetchall()
            col_names = [c[1] for c in src_cols]
            # Compare as VARCHAR: rows round-trip through pandas, so DuckDB may
            # re-infer a column's type from the subset (e.g. an all-numeric-looking
            # slice of a text column becomes INT), which would make a typed
            # IS NOT DISTINCT FROM raise a ConversionException against the pool.
            join_conds = " AND ".join(
                f"CAST(s.{quote_ident(c)} AS VARCHAR) IS NOT DISTINCT FROM CAST(p.{quote_ident(c)} AS VARCHAR)"
                for c in col_names
            )
            # Materialize as table (not view) so we can drop _prev_round
            conn.execute(f"""
                CREATE OR REPLACE TABLE _round_pool AS
                SELECT s.* FROM {source_table} s
                WHERE NOT EXISTS (SELECT 1 FROM _prev_round p WHERE {join_conds})
            """)
            round_source = "_round_pool"
            conn.execute("DROP TABLE IF EXISTS _prev_round")
        else:
            round_source = source_table

        round_count: int = conn.execute(f"SELECT COUNT(*) FROM {round_source}").fetchone()[0]
        if round_count == 0:
            step_warnings.append(f"Round {round_num + 1}: pool exhausted")
            break

        if method == "random":
            result = sample_random(conn, round_source, step.sample_size, step.sample_fraction, round_seed, step.replace)

        elif method == "stratified":
            if not step.stratify_column:
                raise HTTPException(400, f"Step {step_index}: stratify_column required")
            result, round_class_counts = sample_stratified(
                conn, round_source, step.stratify_column,
                step.sample_size, step.sample_fraction, step.class_targets,
                round_seed, step.replace,
            )
            # Report shortfalls
            if step.class_targets:
                for cv, requested in step.class_targets.items():
                    got = round_class_counts.get(cv, 0)
                    if got < requested:
                        step_warnings.append(f"Round {round_num + 1}: class '{cv}' requested {requested}, got {got}")
            # Merge class counts across rounds
            if class_counts is None:
                class_counts = round_class_counts
            else:
                for k, v in round_class_counts.items():
                    class_counts[k] = class_counts.get(k, 0) + v

        elif method == "systematic":
            result = sample_systematic(conn, round_source, step.sample_size, step.sample_fraction)

        elif method == "cluster":
            if not step.cluster_column:
                raise HTTPException(400, f"Step {step_index}: cluster_column required")
            result = sample_cluster(conn, round_source, step.cluster_column, step.num_clusters, round_seed)

        elif method == "weighted":
            if not step.weight_column:
                raise HTTPException(400, f"Step {step_index}: weight_column required")
            result = sample_weighted(
                conn, round_source, step.weight_column,
                step.sample_size, step.sample_fraction, round_seed, step.replace,
            )

        elif method == "time_stratified":
            if not step.time_column:
                raise HTTPException(400, f"Step {step_index}: time_column required")
            result = sample_time_stratified(
                conn, round_source, step.time_column,
                step.sample_size, step.sample_fraction, step.time_bins, round_seed,
            )

        elif method == "llm_semantic":
            if not step.text_column:
                raise HTTPException(400, f"Step {step_index}: text_column required for llm_semantic")
            result = await sample_llm_semantic(
                conn, round_source, step.text_column, step.sample_size or 50,
                step.strategy, provider=step.llm_provider,
                api_url=step.llm_api_url, api_key=step.llm_api_key,
                model=step.llm_model, seed=round_seed, query=step.llm_query,
            )

        else:
            raise HTTPException(400, f"Step {step_index}: unknown method '{method}'")

        all_round_results.append(result)
        per_round_counts.append(len(result))

    # Cleanup temp tables
    conn.execute("DROP TABLE IF EXISTS _filtered_view")
    conn.execute("DROP TABLE IF EXISTS _round_pool")

    combined = pd.concat(all_round_results, ignore_index=True) if all_round_results else pd.DataFrame()
    total_selected = len(combined)

    step_result = StepResult(
        step_index=step_index,
        method=method,
        rows_selected=total_selected,
        pool_before=pool_count,
        pool_after=pool_count,  # updated by caller after pool removal
        rounds_completed=len(per_round_counts),
        per_round_counts=per_round_counts if rounds > 1 else None,
        class_counts=class_counts,
        filter_applied=filter_desc or step.filter_expr,
        filter_matched=filter_matched,
        warnings=step_warnings,
    )

    return combined, step_result


async def run_sampling_pipeline(request: SampleRequest, layout: ArtifactLayout,
                                *, persist: bool = True) -> SampleResponse:
    """Execute the full sampling pipeline.

    *layout* is built by the caller because only it knows the owning team;
    the same object is used to register the output, so the key the writer
    produces and the key authorization resolves are the same string.
    """
    file_path = request.file_path
    if not file_path and request.dataset_id:
        file_path = await resolve_dataset_path(
            request.dataset_id, sheet=request.sheet,
            version_id=request.version_id, version_number=request.version_number, tag=request.tag,
        )
    conn = load_data(file_path=file_path, data=request.data)
    try:
        return await _run_sampling_pipeline_inner(
            conn, request, layout=layout, persist=persist)
    finally:
        conn.close()


async def _run_sampling_pipeline_inner(
    conn: duckdb.DuckDBPyConnection, request: SampleRequest,
    *, layout: ArtifactLayout | None = None, source: str = "df",
    output: str = "sampled", persist: bool = True,
) -> SampleResponse:
    """Inner pipeline logic — assumes caller manages connection lifecycle.

    *source*/*output* name the input relation and the table the result lands in.
    They default to the single-sample contract (``df`` → ``sampled``);
    coordinated sampling overrides them so a related sheet can be sub-sampled
    (§24) on the same connection without colliding with the driver's tables.
    """
    original_count: int = conn.execute(f"SELECT COUNT(*) FROM {source}").fetchone()[0]

    if original_count == 0:
        raise HTTPException(400, "Dataset is empty (0 rows)")

    # Checked against the SOURCE, not the sampled frame: a typo'd sort_by on a
    # run that happens to select zero rows is still a typo.
    validate_request_columns(conn, source, request)

    seed = request.seed

    # Create pool with row IDs for tracking
    conn.execute(f"CREATE TABLE _go_pool AS SELECT *, ROW_NUMBER() OVER () AS _go_rid FROM {source}")
    all_selected: list[pd.DataFrame] = []
    steps_summary: list[StepResult] = []

    for idx, step in enumerate(request.sampling_steps):
        # Handle deduplicate step (operates on already-selected rows, not the pool)
        if step.method.lower() == "deduplicate":
            before_count = sum(len(df) for df in all_selected)
            if all_selected:
                combined_so_far = pd.concat(all_selected, ignore_index=True)
                dedup_cols = step.deduplicate_columns
                combined_so_far = combined_so_far.drop_duplicates(subset=dedup_cols)
                all_selected = [combined_so_far]
            after_count = sum(len(df) for df in all_selected)
            removed = before_count - after_count
            steps_summary.append(StepResult(
                step_index=idx, method="deduplicate",
                rows_selected=after_count, pool_before=before_count,
                pool_after=after_count,
                warnings=[f"Removed {removed} duplicate rows"] if removed > 0 else [],
            ))
            continue

        pool_count: int = conn.execute("SELECT COUNT(*) FROM _go_pool").fetchone()[0]

        if pool_count == 0:
            steps_summary.append(StepResult(
                step_index=idx, method=step.method, rows_selected=0,
                pool_before=0, pool_after=0,
            ))
            continue

        # Create a view without the internal _go_rid column
        conn.execute(
            "CREATE OR REPLACE VIEW _pool_view AS SELECT * EXCLUDE (_go_rid) FROM _go_pool"
        )
        selected_df, step_result = await execute_step(conn, step, "_pool_view", seed, idx)
        rows_selected = len(selected_df)

        if rows_selected > 0:
            all_selected.append(selected_df)

            if not step.replace:
                # Remove selected rows from pool via anti-join
                conn.execute("CREATE OR REPLACE TABLE _step_selected AS SELECT * FROM selected_df")
                pool_cols = conn.execute("PRAGMA table_info('_pool_view')").fetchall()
                col_names = [c[1] for c in pool_cols]
                # VARCHAR-cast both sides: see note in execute_step — the sampled
                # rows come back via pandas and can carry re-inferred column types.
                join_conds = " AND ".join(
                    f"CAST(p.{quote_ident(c)} AS VARCHAR) IS NOT DISTINCT FROM CAST(s.{quote_ident(c)} AS VARCHAR)"
                    for c in col_names
                )
                conn.execute(f"""
                    CREATE OR REPLACE TABLE _go_pool_new AS
                    SELECT p.* FROM _go_pool p
                    WHERE NOT EXISTS (
                        SELECT 1 FROM _step_selected s WHERE {join_conds}
                    )
                """)
                conn.execute("DROP TABLE _go_pool")
                conn.execute("ALTER TABLE _go_pool_new RENAME TO _go_pool")
                conn.execute("DROP TABLE IF EXISTS _step_selected")

        pool_after: int = conn.execute("SELECT COUNT(*) FROM _go_pool").fetchone()[0]
        step_result.pool_after = pool_after
        steps_summary.append(step_result)

    # Combine all selected rows
    combined = pd.concat(all_selected, ignore_index=True) if all_selected else pd.DataFrame()

    # Trim to target if exceeded
    if len(combined) > request.target_total_volume:
        if seed is not None:
            combined = combined.sample(n=request.target_total_volume, random_state=seed)
        else:
            combined = combined.head(request.target_total_volume)

    # Fill remaining volume with random sampling if under target
    if len(combined) < request.target_total_volume:
        remaining_needed = request.target_total_volume - len(combined)
        pool_left: int = conn.execute("SELECT COUNT(*) FROM _go_pool").fetchone()[0]
        if pool_left > 0:
            fill_count = min(remaining_needed, pool_left)
            fill_seed = (seed + len(request.sampling_steps)) if seed is not None else None
            duckdb_set_seed(conn, fill_seed)
            fill_df = conn.execute(
                f"SELECT * EXCLUDE (_go_rid) FROM _go_pool ORDER BY random() LIMIT {fill_count}"
            ).fetchdf()
            combined = pd.concat([combined, fill_df], ignore_index=True)
            steps_summary.append(StepResult(
                step_index=len(request.sampling_steps), method="random_fill",
                rows_selected=len(fill_df), pool_before=pool_left,
                pool_after=pool_left - len(fill_df),
            ))

    # Post-processing: deduplicate
    if request.deduplicate and not combined.empty:
        before = len(combined)
        combined = combined.drop_duplicates(subset=request.deduplicate_columns)
        removed = before - len(combined)
        if removed > 0:
            steps_summary.append(StepResult(
                step_index=len(steps_summary), method="post_deduplicate",
                rows_selected=len(combined), pool_before=before,
                pool_after=len(combined),
                warnings=[f"Removed {removed} duplicate rows"],
            ))

    # Post-processing: sort. The column is known to exist — validated against
    # the source above — so there is no silent-skip branch here any more.
    if request.sort_by and not combined.empty:
        combined = combined.sort_values(
            request.sort_by, ascending=not request.sort_descending, ignore_index=True)

    # Post-processing: shuffle
    if request.shuffle and not combined.empty:
        combined = combined.sample(frac=1.0, random_state=seed).reset_index(drop=True)

    # Register final result
    conn.execute("DROP TABLE IF EXISTS _go_pool")
    conn.execute("DROP VIEW IF EXISTS _pool_view")
    if not combined.empty:
        conn.execute(f"CREATE TABLE {output} AS SELECT * FROM combined")
    else:
        conn.execute(f"CREATE TABLE {output} AS SELECT * FROM {source} WHERE 1=0")

    goal_validation = validate_goals(
        conn, output, request.target_total_volume, request.distribution_goals,
    )
    sampled_count = len(combined)

    # Persist sample as parquet (write locally, publish to the storage backend).
    # Skipped for sub-samples (§24): the coordinated caller persists and
    # REGISTERS the final table itself, and an unregistered blob here would be
    # an orphan no /samples request could ever authorize.
    sample_filename: str | None = None
    if persist:
        if layout is None:  # would write to a key no registration can rebuild
            raise ValueError("persisting a sample requires an ArtifactLayout")
        storage = get_storage()
        sample_filename = f"sample_{uuid.uuid4().hex}.parquet"
        key = layout.key(sample_filename)
        with tempfile.TemporaryDirectory(prefix="accel_sample_") as td:
            local_sample = Path(td) / sample_filename
            conn.execute(f"COPY {output} TO '{local_sample}' (FORMAT PARQUET)")
            storage.put_file(key, local_sample)

    col_summaries = build_column_summaries(conn, output)

    preview_df = conn.execute(f"SELECT * FROM {output} LIMIT 5").fetchdf()
    preview = [
        {k: safe_value(v) for k, v in row.items()}
        for row in preview_df.to_dict(orient="records")
    ]

    sampled_data = None
    if request.return_data:
        sampled_df = conn.execute(f"SELECT * FROM {output}").fetchdf()
        sampled_data = sampled_df.where(pd.notnull(sampled_df), None).to_dict(orient="records")

    # Build reproducibility metadata
    repro = ReproducibilityInfo(
        seed=seed,
        target_total_volume=request.target_total_volume,
        steps_config=[s.model_dump(exclude_none=True) for s in request.sampling_steps],
        distribution_goals=request.distribution_goals.model_dump() if request.distribution_goals else None,
        post_processing={
            "deduplicate": request.deduplicate,
            "deduplicate_columns": request.deduplicate_columns,
            "shuffle": request.shuffle,
            "sort_by": request.sort_by,
            "sort_descending": request.sort_descending,
        },
        timestamp=datetime.now(timezone.utc).isoformat(),
    )

    return SampleResponse(
        success=True,
        original_count=original_count,
        sampled_count=sampled_count,
        columns=col_summaries,
        preview=preview,
        sample_file=sample_filename,
        data=sampled_data,
        steps_summary=steps_summary,
        goal_validation=goal_validation,
        reproducibility=repro,
    )


def _persist_table(conn: duckdb.DuckDBPyConnection, table: str, prefix: str,
                   layout: ArtifactLayout) -> str:
    """COPY a DuckDB table to parquet and publish it to the storage backend.

    *layout* is required rather than defaulted: a default would have to invent
    a kind, and a wrong kind means the caller registers the artifact under a
    key nothing was written to. Callers with no dataset context pass an
    ownerless layout explicitly.
    """
    storage = get_storage()
    filename = f"{prefix}_{uuid.uuid4().hex}.parquet"
    key = layout.key(filename)
    with tempfile.TemporaryDirectory(prefix="accel_coord_") as td:
        local = Path(td) / filename
        conn.execute(f"COPY {table} TO '{local}' (FORMAT PARQUET)")
        storage.put_file(key, local)
    return filename


def _physical_key(sheet_row: dict | None, selector: str | None) -> str | None:
    """Map a normalized column selector to the physical parquet name."""
    if not selector or not sheet_row:
        return selector
    for c in sheet_row.get("schema_json") or []:
        if c.get("normalized_name") == selector:
            return c["name"]
    return selector


class FkLinkCandidate(NamedTuple):
    """One foreign_key rule linking a related sheet to its parent.

    Keys are PHYSICAL parquet column names (quality-rule selectors are stored
    normalized), so callers can use them against the data directly.
    """

    rule_name: str
    left_on: str    # key column on the parent sheet
    right_on: str   # key column on the related sheet


def _fk_link_candidates(
    fk_rules: list[dict], sheet_rows: list[dict], related: str, parent: str,
) -> list[FkLinkCandidate]:
    """Every enabled foreign_key rule linking *related* to *parent*, either way.

    A rule links the pair in one of two directions: the related sheet may be
    the FK child (a rule ON it referencing the parent) or the FK parent (a rule
    ON the parent referencing it). Shared by coordinated sampling's key
    defaulting (§5) and the relationship seeder (§22), so both read the same
    edges out of the same rules.
    """
    rel_row = _find_sheet(sheet_rows, related)
    par_row = _find_sheet(sheet_rows, parent)

    def matches(selector: str | None, row: dict | None) -> bool:
        return bool(row and selector in (row["sheet_key"], row["sheet_name"]))

    candidates: list[FkLinkCandidate] = []
    for r in fk_rules:
        params = r.get("parameters") or {}
        if matches(r.get("sheet_selector"), rel_row) and matches(params.get("ref_sheet"), par_row):
            # related sheet is the FK child: its column references the parent
            candidates.append(FkLinkCandidate(
                r["name"],
                _physical_key(par_row, params.get("ref_column")),
                _physical_key(rel_row, r.get("column_selector"))))
        elif matches(r.get("sheet_selector"), par_row) and matches(params.get("ref_sheet"), rel_row):
            # related sheet is the FK parent: the parent's column references it
            candidates.append(FkLinkCandidate(
                r["name"],
                _physical_key(par_row, r.get("column_selector")),
                _physical_key(rel_row, params.get("ref_column"))))
    return candidates


def _default_link_keys(
    fk_rules: list[dict], sheet_rows: list[dict], related: str, parent: str,
) -> tuple[str, str]:
    """Default (left_on, right_on) from enabled foreign_key quality rules.

    Exactly one matching rule is required — ambiguity is the caller's to
    resolve with explicit keys (ROADMAP §5).
    """
    candidates = _fk_link_candidates(fk_rules, sheet_rows, related, parent)
    if not candidates:
        raise HTTPException(
            400, f"No foreign_key quality rule links '{related}' to '{parent}' — "
                 "specify left_on/right_on explicitly",
        )
    if len(candidates) > 1:
        names = ", ".join(sorted(c.rule_name for c in candidates))
        raise HTTPException(
            400, f"Multiple foreign_key rules link '{related}' to '{parent}' "
                 f"({names}) — specify left_on/right_on explicitly",
        )
    return candidates[0].left_on, candidates[0].right_on


async def _resolve_link_relationship(dataset_id: str, ver: dict, link) -> dict:
    """Resolve a link's ``relationship_id`` to keys and a parent sheet (§24).

    The relationship must be confirmed — an unreviewed edge is exactly the kind
    of guess coordinated sampling must not make silently — and both of its
    endpoints must exist in the version being sampled. Stored column names are
    normalized, so they are mapped back to physical parquet names here.
    """
    from app.features.relationships import repo as relationships_repo

    relationship = await relationships_repo.get_relationship(link.relationship_id)
    if not relationship or dataset_id not in (relationship["dataset_id"],
                                              relationship["to_dataset_id"]):
        raise HTTPException(
            404, f"Relationship not found: {link.relationship_id}")
    if relationship["status"] != "confirmed":
        raise ProblemException(
            400,
            "This relationship has not been confirmed — only confirmed "
            "relationships can drive coordinated sampling",
            code="relationship-not-confirmed",
            current_status=relationship["status"])

    sheet_rows = await get_version_sheet_rows(ver)
    related_row = _find_sheet(sheet_rows, link.sheet)
    if not related_row:
        raise HTTPException(404, f"Sheet not found: {link.sheet}")
    related_lsid = str(related_row.get("logical_sheet_id") or "")

    # Either endpoint may be the related sheet; the OTHER one is the parent.
    if related_lsid == relationship["from_logical_sheet_id"]:
        parent_lsid = relationship["to_logical_sheet_id"]
        related_col, parent_col = relationship["from_column"], relationship["to_column"]
    elif related_lsid == relationship["to_logical_sheet_id"]:
        parent_lsid = relationship["from_logical_sheet_id"]
        related_col, parent_col = relationship["to_column"], relationship["from_column"]
    else:
        raise ProblemException(
            400,
            f"Relationship {link.relationship_id} does not have '{link.sheet}' "
            "as an endpoint",
            code="relationship-endpoint-mismatch", sheet=link.sheet)

    parent_row = next((r for r in sheet_rows
                       if str(r.get("logical_sheet_id") or "") == parent_lsid), None)
    if not parent_row:
        raise ProblemException(
            400,
            "The relationship's other endpoint is not present in this version",
            code="relationship-endpoint-mismatch", sheet=link.sheet)

    return {
        "_parent_sheet": parent_row["sheet_name"],
        "_parent_column": _physical_key(parent_row, parent_col),
        "_related_column": _physical_key(related_row, related_col),
    }


async def _subsample_related(conn: duckdb.DuckDBPyConnection, table: str, link,
                             seed: int | None, idx: int) -> str:
    """Run the key-filtered rows of a related sheet through the sampling pipeline.

    Reuses the driver's pipeline verbatim (same steps, same seeding), just
    pointed at this sheet's table — so a sub-sample is reproducible for a fixed
    seed exactly like the driver sample is.
    """
    out = f"subsampled_rel_{idx}"
    sub_request = SampleRequest(
        target_total_volume=link.target_total_volume,
        sampling_steps=link.sampling_steps,
        seed=seed,
        return_data=False,
    )
    await _run_sampling_pipeline_inner(
        conn, sub_request, source=table, output=out, persist=False)
    return out


async def run_coordinated_sampling(
    request: CoordinatedSampleRequest, layout: ArtifactLayout,
) -> CoordinatedSampleResponse:
    """Sample a driver sheet, then filter related sheets by key.

    The driver sheet runs through the normal pipeline; each related sheet is
    semi-joined down to the rows referenced by an already-sampled parent (the
    driver by default, or another related sheet via ``parent_sheet``). All
    sheets come from a single resolved version, so the result is a
    referentially-consistent slice of the workbook. Filtering is deterministic,
    so a fixed ``seed`` reproduces the whole set.
    """
    ver = await resolve_version(
        request.dataset_id, version_id=request.version_id,
        version_number=request.version_number, tag=request.tag,
    )
    if not ver.get("path"):
        raise HTTPException(404, f"Version has no data (status: {ver.get('status', 'unknown')})")

    # Driver sheet resolves like any single sample source (explicit sheet name,
    # never silently picked — resolve_version_sheet_path enforces this).
    driver_path = await resolve_version_sheet_path(ver, request.driver_sheet)
    driver_req = SampleRequest(
        target_total_volume=request.target_total_volume,
        sampling_steps=request.sampling_steps,
        distribution_goals=request.distribution_goals,
        seed=request.seed,
        return_data=request.return_data,
        deduplicate=request.deduplicate,
        deduplicate_columns=request.deduplicate_columns,
        shuffle=request.shuffle,
        sort_by=request.sort_by,
        sort_descending=request.sort_descending,
    )

    conn = load_data(file_path=driver_path)
    try:
        driver_result = await _run_sampling_pipeline_inner(
            conn, driver_req, layout=layout)

        # Each sampled sheet's rows live in a DuckDB table; the driver's is the
        # `sampled` table left behind by the inner pipeline. Related sheets are
        # resolved in dependency order (a link may point at another link).
        sampled_tables: dict[str, str] = {request.driver_sheet: "sampled"}
        related_results: list[RelatedSheetSample] = []

        # §24 referential guard: a sheet that another link samples FROM must keep
        # every referenced row, otherwise the downstream filter would silently
        # lose rows whose parent was sub-sampled away. Reject up front rather
        # than producing a quietly-inconsistent slice.
        parents_in_use = {link.parent_sheet for link in request.related
                          if link.parent_sheet}
        for link in request.related:
            if link.sampling_steps and link.sheet in parents_in_use:
                raise ProblemException(
                    400,
                    f"'{link.sheet}' is the parent of another related sheet, so "
                    "sub-sampling it would break the rows that depend on it",
                    code="cannot-subsample-parent", sheet=link.sheet)

        pending = list(request.related)
        fk_rules: list[dict] | None = None  # fetched once, only if a link omits keys
        sheet_rows: list[dict] | None = None
        idx = 0
        while pending:
            progressed = False
            still_pending: list = []
            for link in pending:
                relationship = None
                if link.relationship_id:
                    relationship = await _resolve_link_relationship(
                        request.dataset_id, ver, link)
                parent = (link.parent_sheet
                          or (relationship or {}).get("_parent_sheet")
                          or request.driver_sheet)
                parent_table = sampled_tables.get(parent)
                if parent_table is None:
                    still_pending.append(link)
                    continue

                related_path = await resolve_version_sheet_path(ver, link.sheet)
                escaped = str(related_path).replace("'", "''")
                view = f"df_rel_{idx}"
                conn.execute(f"CREATE VIEW {view} AS SELECT * FROM read_parquet('{escaped}')")

                # Key precedence (§24): explicit columns win, then a confirmed
                # relationship, then the §5 foreign_key-rule default.
                left_on, right_on, key_source = link.left_on, link.right_on, "explicit"
                if left_on is None and relationship is not None:
                    left_on = relationship["_parent_column"]
                    right_on = relationship["_related_column"]
                    key_source = "relationship"
                if left_on is None:
                    key_source = "fk_rule"
                    if fk_rules is None:
                        fk_rules = [
                            r for r in await quality_repo.list_rules(
                                request.dataset_id, enabled_only=True)
                            if r["rule_type"] == "foreign_key"
                        ]
                        sheet_rows = await get_version_sheet_rows(ver)
                    left_on, right_on = _default_link_keys(
                        fk_rules, sheet_rows, link.sheet, parent)

                rel_cols = [r[0] for r in conn.execute(f"DESCRIBE {view}").fetchall()]
                if right_on not in rel_cols:
                    raise HTTPException(400, f"Key column not found on '{link.sheet}': {right_on}")
                parent_cols = [r[0] for r in conn.execute(f"DESCRIBE {parent_table}").fetchall()]
                if left_on not in parent_cols:
                    raise HTTPException(400, f"Key column not found on '{parent}': {left_on}")

                out_table = f"sampled_rel_{idx}"
                conn.execute(
                    f"CREATE TABLE {out_table} AS SELECT * FROM {view} "
                    f"WHERE {quote_ident(right_on)} IN "
                    f"(SELECT {quote_ident(left_on)} FROM {parent_table})"
                )

                original_count = conn.execute(f"SELECT COUNT(*) FROM {view}").fetchone()[0]
                referenced_count = conn.execute(
                    f"SELECT COUNT(*) FROM {out_table}").fetchone()[0]

                # §24: with sampling params, the key-filtered set goes through the
                # normal pipeline. Without them this is v1 — keep every referenced
                # row — so existing payloads behave identically.
                if link.sampling_steps:
                    out_table = await _subsample_related(
                        conn, out_table, link, request.seed, idx)

                sampled_tables[link.sheet] = out_table
                sampled_count = conn.execute(f"SELECT COUNT(*) FROM {out_table}").fetchone()[0]
                sample_filename = _persist_table(conn, out_table, "sample", layout)
                columns = build_column_summaries(conn, out_table)
                preview_df = conn.execute(f"SELECT * FROM {out_table} LIMIT 5").fetchdf()
                preview = [
                    {k: safe_value(v) for k, v in row.items()}
                    for row in preview_df.to_dict(orient="records")
                ]
                rel_data = None
                if request.return_data:
                    rel_df = conn.execute(f"SELECT * FROM {out_table}").fetchdf()
                    rel_data = rel_df.where(pd.notnull(rel_df), None).to_dict(orient="records")

                related_results.append(RelatedSheetSample(
                    sheet=link.sheet,
                    parent_sheet=parent,
                    left_on=left_on,
                    right_on=right_on,
                    relationship_id=link.relationship_id,
                    key_source=key_source,
                    original_count=original_count,
                    referenced_count=referenced_count,
                    sampled_count=sampled_count,
                    columns=columns,
                    preview=preview,
                    sample_file=sample_filename,
                    data=rel_data,
                ))
                idx += 1
                progressed = True

            if not progressed:
                unresolved = ", ".join(sorted({l.parent_sheet or request.driver_sheet for l in still_pending}))
                raise HTTPException(
                    400,
                    f"Unresolvable related-sheet parents (unknown sheet or cycle): {unresolved}",
                )
            pending = still_pending

        return CoordinatedSampleResponse(
            success=True,
            dataset_id=request.dataset_id,
            driver_sheet=request.driver_sheet,
            driver=driver_result,
            related=related_results,
        )
    finally:
        conn.close()
