"""Sampling service — pipeline orchestration, goal validation, column summaries."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import duckdb
import pandas as pd
from fastapi import HTTPException

from app.infra.db.storage import get_storage, sample_key
from app.shared.utils.sql import quote_ident, safe_value
from app.shared.data_io import load_data
from app.shared.datasets import resolve_dataset_path
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
    DistributionGoals,
    GoalValidationResult,
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
            join_conds = " AND ".join(
                f"s.{quote_ident(c)} IS NOT DISTINCT FROM p.{quote_ident(c)}"
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


async def run_sampling_pipeline(request: SampleRequest) -> SampleResponse:
    """Execute the full sampling pipeline."""
    file_path = request.file_path
    if not file_path and request.dataset_id:
        file_path = await resolve_dataset_path(
            request.dataset_id, sheet=request.sheet,
            version_id=request.version_id, version_number=request.version_number, tag=request.tag,
        )
    conn = load_data(file_path=file_path, data=request.data)
    try:
        return await _run_sampling_pipeline_inner(conn, request)
    finally:
        conn.close()


async def _run_sampling_pipeline_inner(
    conn: duckdb.DuckDBPyConnection, request: SampleRequest,
) -> SampleResponse:
    """Inner pipeline logic — assumes caller manages connection lifecycle."""
    original_count: int = conn.execute("SELECT COUNT(*) FROM df").fetchone()[0]

    if original_count == 0:
        raise HTTPException(400, "Dataset is empty (0 rows)")

    seed = request.seed

    # Create pool with row IDs for tracking
    conn.execute("CREATE TABLE _go_pool AS SELECT *, ROW_NUMBER() OVER () AS _go_rid FROM df")
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
                join_conds = " AND ".join(
                    f"p.{quote_ident(c)} IS NOT DISTINCT FROM s.{quote_ident(c)}"
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

    # Post-processing: sort
    if request.sort_by and not combined.empty:
        if request.sort_by in combined.columns:
            combined = combined.sort_values(request.sort_by, ascending=not request.sort_descending, ignore_index=True)

    # Post-processing: shuffle
    if request.shuffle and not combined.empty:
        combined = combined.sample(frac=1.0, random_state=seed).reset_index(drop=True)

    # Register final result
    conn.execute("DROP TABLE IF EXISTS _go_pool")
    conn.execute("DROP VIEW IF EXISTS _pool_view")
    if not combined.empty:
        conn.execute("CREATE TABLE sampled AS SELECT * FROM combined")
    else:
        conn.execute("CREATE TABLE sampled AS SELECT * FROM df WHERE 1=0")

    goal_validation = validate_goals(
        conn, "sampled", request.target_total_volume, request.distribution_goals,
    )
    sampled_count = len(combined)

    # Persist sample as parquet
    storage = get_storage()
    sample_filename = f"sample_{uuid.uuid4().hex}.parquet"
    key = sample_key(sample_filename)
    storage.ensure_dir("samples")
    sample_path = storage.resolve(key)
    conn.execute(f"COPY sampled TO '{sample_path}' (FORMAT PARQUET)")

    col_summaries = build_column_summaries(conn, "sampled")

    preview_df = conn.execute("SELECT * FROM sampled LIMIT 5").fetchdf()
    preview = [
        {k: safe_value(v) for k, v in row.items()}
        for row in preview_df.to_dict(orient="records")
    ]

    sampled_data = None
    if request.return_data:
        sampled_df = conn.execute("SELECT * FROM sampled").fetchdf()
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
