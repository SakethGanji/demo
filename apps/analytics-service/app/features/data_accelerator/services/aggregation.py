"""Aggregation service."""

from __future__ import annotations

import tempfile
import uuid
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd
from fastapi import HTTPException

from app.api.errors import ProblemException
from app.shared.utils.sql import quote_ident, safe_value, sanitize_filter_expr
from app.shared.constants import AGG_SQL_MAP, ALLOWED_AGG_FUNCTIONS, MAX_AGGREGATION_ROWS
from app.shared.data_io import load_data
from app.shared.datasets import resolve_dataset_path
from app.shared.filters import compile_filter
from app.infra.db.storage import ArtifactLayout, get_storage

from ..schemas import AggregateRequest, AggregateResponse, GroupByBucket

_HAVING_OPS = {"eq": "=", "neq": "!=", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}

SORT_ORDERS = ("asc", "desc")

# Functions whose grand total is a single well-defined number over the whole
# filtered source. Everything else — max, min, mean, median, std, nunique,
# first, last — has no meaningful footer value: a "total" for them can only be
# recomputed at whatever grain the caller actually wants (that is what pivot's
# row/column totals do). Emitting one anyway is how a `max` footer came back as
# 75000 when the true maximum was 25000.
TOTALABLE_FUNCTIONS = frozenset({"sum", "count"})

#: Values of ``AggregateResponse.totals_omitted``.
TOTAL_OMITTED_NON_ADDITIVE = "non-additive"


def _filter_columns(group: dict[str, Any]) -> set[str]:
    """Every column referenced by a FilterGroup dict (recursively)."""
    cols: set[str] = set()
    for cond in group.get("conditions", []):
        if "conditions" in cond:
            cols |= _filter_columns(cond)
        elif cond.get("column"):
            cols.add(cond["column"])
    return cols


def _bucket_expr(bucket: GroupByBucket, from_target: str) -> str:
    """Compile a GroupByBucket into a SQL expression (identifiers quoted, no user values)."""
    qcol = quote_ident(bucket.column)
    if bucket.date_trunc is not None:
        # CAST covers VARCHAR ISO dates too — DuckDB has no implicit VARCHAR cast here.
        return f"date_trunc('{bucket.date_trunc}', CAST({qcol} AS TIMESTAMP))"
    if bucket.bin_width is not None:
        w = float(bucket.bin_width)
        return f"FLOOR({qcol} / {w!r}) * {w!r}"
    # bin_count: equal-width bins 1..N over min/max from the (filtered) source.
    # DuckDB has no width_bucket, so emulate it; the top value lands in bin N.
    n = int(bucket.bin_count)  # type: ignore[arg-type]
    lo = f"(SELECT MIN({qcol}) FROM {from_target})"
    hi = f"(SELECT MAX({qcol}) FROM {from_target})"
    return (
        f"CASE WHEN {qcol} IS NULL THEN NULL "
        f"WHEN {hi} = {lo} THEN 1 "
        f"ELSE CAST(LEAST(FLOOR(({qcol} - {lo}) * {n} / ({hi} - {lo})), {n} - 1) + 1 AS BIGINT) END"
    )


def _build_join_select(left_cols: list[str], right_cols: list[str], join: Any) -> list[str]:
    """Select list for the joined view: skip the duplicate join key, prefix any
    other colliding columns with ``{sheet}_`` so the joined view has unique names."""
    select_parts = [f"df.{quote_ident(c)}" for c in left_cols]
    for c in right_cols:
        if c == join.right_on:
            continue
        alias = c if c not in left_cols else f"{join.sheet}_{c}"
        select_parts.append(f"df_join.{quote_ident(c)} AS {quote_ident(alias)}")
    return select_parts


def _compile_where(
    filters: Any, filter_expr: str | None, available: set[str],
) -> tuple[str, list[Any], bool]:
    """WHERE clause: structured filters (bound) + deprecated raw filter_expr.

    Returns (where_sql, binds, uses_filter_src) — the flag is True when a
    structured clause compiled (some operators subquery a _filter_src view).
    """
    where_parts: list[str] = []
    binds: list[Any] = []
    uses_filter_src = False
    if filters is not None:
        fdict = filters.model_dump()
        unknown = sorted(c for c in _filter_columns(fdict) if c not in available)
        if unknown:
            raise ProblemException(
                400,
                f"Filter columns not found: {unknown}. Valid options: {sorted(available)}",
                code="unknown-column", columns=unknown, available=sorted(available))
        clause = compile_filter(fdict, binds)
        if clause:
            where_parts.append(clause)
            uses_filter_src = True
    if filter_expr:
        where_parts.append(f"({sanitize_filter_expr(filter_expr)})")
    return " AND ".join(where_parts), binds, uses_filter_src


def _compile_group_entries(
    group_by: list[Any], from_target: str,
) -> list[tuple[str, str]]:
    """(output name, select expression) per group-by entry, buckets compiled."""
    entries: list[tuple[str, str]] = []
    for entry in group_by:
        if isinstance(entry, GroupByBucket):
            entries.append((entry.alias or f"{entry.column}_bucket",
                            _bucket_expr(entry, from_target)))
        else:
            entries.append((entry, quote_ident(entry)))
    return entries


def _compile_select_aggs(
    aggregations: list[Any], available: set[str],
) -> tuple[list[str], list[str], list[Any], dict[str, tuple[str, list[Any]]], bool]:
    """Aggregate select fragments, incl. per-spec FILTER (WHERE ...) clauses.

    Returns (select_fragments, aliases, binds, alias -> (expr, its binds),
    uses_filter_src); binds are in select order.
    """
    agg_parts: list[str] = []
    agg_aliases: list[str] = []
    select_binds: list[Any] = []
    agg_exprs: dict[str, tuple[str, list[Any]]] = {}
    uses_filter_src = False
    for spec in aggregations:
        alias = spec.alias or f"{spec.column}_{spec.function}"
        if alias in agg_aliases:
            # Two measures under one output name are ambiguous, and the ambiguity
            # is not survivable: the main query materialises a table so DuckDB
            # renames the collision (`metric`, `metric_1`), while the grand-total
            # query is a bare SELECT where the duplicate key silently collapses
            # and the LAST measure wins — so the footer reported one measure's
            # total under the other's column. Refuse instead of answering wrong.
            raise ProblemException(
                400,
                f"Duplicate aggregation alias {alias!r}. Give each aggregation a "
                f"distinct alias.",
                code="duplicate-alias", alias=alias,
            )
        agg_aliases.append(alias)
        qcol = quote_ident(spec.column)

        if spec.function == "nunique":
            expr = f"COUNT(DISTINCT {qcol})"
        else:
            expr = f"{AGG_SQL_MAP[spec.function]}({qcol})"

        expr_binds: list[Any] = []
        if spec.filter is not None:
            fdict = spec.filter.model_dump()
            unknown = sorted(c for c in _filter_columns(fdict) if c not in available)
            if unknown:
                raise ProblemException(
                    400,
                    f"Aggregation filter columns not found: {unknown}. "
                    f"Valid options: {sorted(available)}",
                    code="unknown-column", columns=unknown,
                    available=sorted(available))
            clause = compile_filter(fdict, expr_binds)
            if clause:
                expr += f" FILTER (WHERE {clause})"
                uses_filter_src = True

        agg_exprs[alias] = (expr, expr_binds)
        select_binds.extend(expr_binds)
        agg_parts.append(f"{expr} AS {quote_ident(alias)}")
    return agg_parts, agg_aliases, select_binds, agg_exprs, uses_filter_src


def _compile_having(
    having: list[Any],
    agg_exprs: dict[str, tuple[str, list[Any]]],
    agg_aliases: list[str],
) -> tuple[str, list[Any]]:
    """HAVING conditions over aggregation aliases, re-expanded to expressions."""
    having_parts: list[str] = []
    having_binds: list[Any] = []
    for cond in having:
        if cond.column not in agg_exprs:
            raise HTTPException(
                status_code=400,
                detail=f"HAVING column is not an aggregation alias: {cond.column}. "
                       f"Aliases: {sorted(agg_aliases)}",
            )
        expr, expr_binds = agg_exprs[cond.column]
        having_parts.append(f"{expr} {_HAVING_OPS[cond.op]} ?")
        having_binds.extend(expr_binds)
        having_binds.append(cond.value)
    return " AND ".join(having_parts), having_binds


def _assemble_sql(
    source: str,
    from_target: str,
    where_sql: str,
    group_entries: list[tuple[str, str]],
    agg_parts: list[str],
    agg_aliases: list[str],
    having_sql: str,
    sort_by: str | None,
    sort_order: str,
    effective_limit: int | None,
) -> str:
    """Full aggregation statement. A WHERE clause pre-filters in a CTE so bind
    order is textual: WHERE, then FILTERs, then HAVING. LIMIT fetches one extra
    row to detect truncation; ``effective_limit=None`` emits no LIMIT at all
    (used by the grand-total pass, which must span every group)."""
    select_sql = ", ".join(
        [f"{expr} AS {quote_ident(name)}" for name, expr in group_entries] + agg_parts)
    sql = f"SELECT {select_sql} FROM {from_target}"
    if where_sql:
        sql = f"WITH _agg_src AS (SELECT * FROM {source} WHERE {where_sql}) " + sql
    # Ordinals: bucket expressions with scalar subqueries can't be repeated verbatim.
    # No group-by entries → no GROUP BY clause: a single grand-total row.
    if group_entries:
        sql += " GROUP BY " + ", ".join(str(i + 1) for i in range(len(group_entries)))
    if having_sql:
        sql += " HAVING " + having_sql

    if sort_by:
        # An unknown sort_by used to drop the ORDER BY silently, so results came
        # back in arbitrary order while the response still said success.
        valid_sort = sorted({name for name, _ in group_entries} | set(agg_aliases))
        if sort_by not in valid_sort:
            raise ProblemException(
                400,
                f"sort_by column not found: {sort_by}. Valid options: {valid_sort}",
                code="unknown-column", columns=[sort_by], available=valid_sort)
        # Never guess a direction: an unrecognised sort_order used to mean DESC,
        # which silently inverted "ascending"/"ASC"/anything misspelled.
        if sort_order not in SORT_ORDERS:
            raise ProblemException(
                400,
                f"Invalid sort_order: {sort_order!r}. Valid options: {list(SORT_ORDERS)}",
                code="invalid-sort-order", available=list(SORT_ORDERS))
        order = "ASC" if sort_order == "asc" else "DESC"
        sql += f" ORDER BY {quote_ident(sort_by)} {order}"

    if effective_limit is not None:
        sql += f" LIMIT {int(effective_limit) + 1}"
    return sql


def _split_totalable(aggregations: list[Any]) -> tuple[list[Any], dict[str, str]]:
    """(specs that get a grand total, alias -> why the others don't)."""
    totalable: list[Any] = []
    omitted: dict[str, str] = {}
    for spec in aggregations:
        if spec.function in TOTALABLE_FUNCTIONS:
            totalable.append(spec)
        else:
            alias = spec.alias or f"{spec.column}_{spec.function}"
            omitted[alias] = TOTAL_OMITTED_NON_ADDITIVE
    return totalable, omitted


def _grand_totals(
    conn: duckdb.DuckDBPyConnection,
    request: AggregateRequest,
    available: set[str],
    source: str,
    from_target: str,
    where_sql: str,
    where_binds: list[Any],
    group_entries: list[tuple[str, str]],
    having_sql: str,
    having_binds: list[Any],
) -> tuple[dict[str, Any], dict[str, str]]:
    """Grand totals over the FULL filtered source — never the returned page.

    Two rules, both of which ``SUM(alias) FROM agg_result`` broke:

    * Only additive functions get a total. Summing a ``max`` / ``mean`` /
      ``std`` / ``nunique`` column yields a number that is simply wrong, so
      those aliases go into the omission map instead — a caller can then tell
      "there is no total" from "the total is zero".
    * The total spans every group, not the LIMITed page. Without ``having``
      that is a no-GROUP-BY re-aggregation of the filtered source, exactly the
      shape pivot uses for its grand totals (`_aggregate_into(..., [])`). With
      ``having`` the table only shows the groups that passed, so the total is
      summed over the *unlimited* grouped result instead; re-aggregating the
      raw source there would total rows the caller never sees. For the additive
      functions this is restricted to, SUM-of-per-group equals the grand value.
    """
    total_specs, omitted = _split_totalable(request.aggregations)
    if not total_specs:
        return {}, omitted

    agg_parts, aliases, select_binds, _exprs, _needs_src = _compile_select_aggs(
        total_specs, available)

    if having_sql:
        inner = _assemble_sql(source, from_target, where_sql, group_entries,
                              agg_parts, aliases, having_sql, None, "asc", None)
        outer = ", ".join(
            f"SUM({quote_ident(a)}) AS {quote_ident(a)}" for a in aliases)
        sql = f"SELECT {outer} FROM ({inner})"
        binds = where_binds + select_binds + having_binds
    else:
        sql = _assemble_sql(source, from_target, where_sql, [], agg_parts,
                            aliases, "", None, "asc", None)
        binds = where_binds + select_binds

    try:
        row = conn.execute(sql, binds).fetchone()
    except duckdb.Error as e:
        raise HTTPException(status_code=400, detail=f"Query error: {e}")
    return {a: safe_value(v) for a, v in zip(aliases, row)}, omitted


def _effective_limit(user_limit: int | None, cap: int) -> int:
    """Server-side output row cap vs the caller's own limit.

    Floored at 0 so a negative limit (a UI sending -1 as a "no limit" sentinel)
    becomes an empty page rather than a raw ``LIMIT -1`` DuckDB rejects — which,
    on the un-try'd second statement, surfaced as a 500."""
    return max(0, min(user_limit, cap)) if user_limit is not None else cap


def _is_truncated(probe_row_count: int, user_limit: int | None, cap: int) -> bool:
    """The probe's extra row proves a limit cut results; it's the server cap's
    doing only when the caller didn't ask for a smaller limit themselves."""
    return probe_row_count > _effective_limit(user_limit, cap) and (
        user_limit is None or user_limit >= cap)


async def run_aggregation(request: AggregateRequest, layout: ArtifactLayout,
                          *, persist: bool = True) -> AggregateResponse:
    """Aggregate data with group-by, sort, and optional filtering."""
    file_path = request.file_path
    if not file_path and request.dataset_id:
        file_path = await resolve_dataset_path(
            request.dataset_id, sheet=request.sheet,
            version_id=request.version_id, version_number=request.version_number, tag=request.tag,
        )
    conn = load_data(file_path=file_path, data=request.data)
    try:
        source = "df"
        if request.join:
            if not request.dataset_id:
                raise HTTPException(400, "join requires a dataset_id source")
            join_path = await resolve_dataset_path(
                request.dataset_id, sheet=request.join.sheet,
                version_id=request.version_id, version_number=request.version_number,
                tag=request.tag,
            )
            escaped = str(join_path).replace("'", "''")
            conn.execute(f"CREATE VIEW df_join AS SELECT * FROM read_parquet('{escaped}')")

            left_cols = [r[0] for r in conn.execute("DESCRIBE df").fetchall()]
            right_cols = [r[0] for r in conn.execute("DESCRIBE df_join").fetchall()]
            if request.join.left_on not in left_cols:
                raise HTTPException(400, f"Join column not found on base sheet: {request.join.left_on}")
            if request.join.right_on not in right_cols:
                raise HTTPException(400, f"Join column not found on '{request.join.sheet}': {request.join.right_on}")

            select_parts = _build_join_select(left_cols, right_cols, request.join)
            how = "LEFT" if request.join.how == "left" else "INNER"
            conn.execute(
                f"CREATE VIEW df_joined AS SELECT {', '.join(select_parts)} "
                f"FROM df {how} JOIN df_join "
                f"ON df.{quote_ident(request.join.left_on)} = df_join.{quote_ident(request.join.right_on)}"
            )
            source = "df_joined"

        original_count = conn.execute(f"SELECT COUNT(*) FROM {source}").fetchone()[0]

        # Validate columns exist
        available = {r[0] for r in conn.execute(f"DESCRIBE {source}").fetchall()}

        # Every "you named a column that isn't there" answer on this endpoint
        # publishes the shared `unknown-column` contract (code + columns +
        # available), the same one `sort_by` and the filter compiler use. These
        # three used to be bare HTTPExceptions, which the problem+json envelope
        # renders as the generic `bad_request` with no `columns`/`available` —
        # so a column picker could not repopulate itself, and the MCP guidance
        # path (which branches on code == "unknown-column") silently degraded.
        sorted_available = sorted(available)
        missing = [c for c in (e.column if isinstance(e, GroupByBucket) else e
                               for e in request.group_by) if c not in available]
        if missing:
            raise ProblemException(
                400,
                f"Group-by columns not found: {missing}. Valid options: {sorted_available}",
                code="unknown-column", columns=missing, available=sorted_available)

        for spec in request.aggregations:
            if spec.column not in available:
                raise ProblemException(
                    400,
                    f"Aggregation column not found: {spec.column}. "
                    f"Valid options: {sorted_available}",
                    code="unknown-column", columns=[spec.column],
                    available=sorted_available)
            if spec.function not in ALLOWED_AGG_FUNCTIONS:
                raise ProblemException(
                    400,
                    f"Unknown aggregation function: {spec.function}. "
                    f"Allowed: {sorted(ALLOWED_AGG_FUNCTIONS)}",
                    code="unknown-aggregation-function",
                    available=sorted(ALLOWED_AGG_FUNCTIONS))

        # ---- WHERE: structured filters (bound) + deprecated raw filter_expr ----
        where_sql, where_binds, needs_filter_src = _compile_where(
            request.filters, request.filter_expr, available)
        # Pre-filter in a CTE so bind order is textual: WHERE, then FILTERs, then HAVING.
        from_target = "_agg_src" if where_sql else source

        # ---- Build SQL ----
        group_entries = _compile_group_entries(request.group_by, from_target)
        agg_parts, agg_aliases, select_binds, agg_exprs, aggs_use_filter_src = \
            _compile_select_aggs(request.aggregations, available)
        needs_filter_src = needs_filter_src or aggs_use_filter_src
        having_sql, having_binds = _compile_having(request.having, agg_exprs, agg_aliases)

        effective_limit = _effective_limit(request.limit, MAX_AGGREGATION_ROWS)
        sql = _assemble_sql(
            source, from_target, where_sql, group_entries, agg_parts, agg_aliases,
            having_sql, request.sort_by, request.sort_order, effective_limit)

        binds = where_binds + select_binds + having_binds

        if needs_filter_src:
            conn.execute(f"CREATE OR REPLACE VIEW _filter_src AS SELECT * FROM {source}")
        try:
            conn.execute(f"CREATE TABLE _agg_probe AS {sql}", binds)
        except duckdb.Error as e:
            raise HTTPException(status_code=400, detail=f"Query error: {e}")

        fetched: int = conn.execute("SELECT COUNT(*) FROM _agg_probe").fetchone()[0]
        truncated = _is_truncated(fetched, request.limit, MAX_AGGREGATION_ROWS)
        conn.execute(
            f"CREATE TABLE agg_result AS SELECT * FROM _agg_probe LIMIT {int(effective_limit)}")

        group_count: int = conn.execute("SELECT COUNT(*) FROM agg_result").fetchone()[0]
        result_cols = [r[0] for r in conn.execute("DESCRIBE agg_result").fetchall()]

        # Grand totals — re-aggregated over every filtered row, not over the
        # returned page, and only for functions a total means something for.
        totals, totals_omitted = _grand_totals(
            conn, request, available, source, from_target, where_sql, where_binds,
            group_entries, having_sql, having_binds)

        # Persist result as parquet (write locally, publish to the storage backend)
        result_filename: str | None = None
        if persist:
            storage = get_storage()
            result_filename = f"agg_{uuid.uuid4().hex}.parquet"
            with tempfile.TemporaryDirectory(prefix="accel_agg_") as td:
                local_result = Path(td) / result_filename
                conn.execute(f"COPY agg_result TO '{local_result}' (FORMAT PARQUET)")
                storage.put_file(layout.key(result_filename), local_result)

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
            totals_omitted=totals_omitted if totals_omitted else None,
            truncated=truncated,
            result_file=result_filename,
        )
    finally:
        conn.close()
