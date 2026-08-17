"""Aggregation service."""

from __future__ import annotations

import tempfile
import uuid
from pathlib import Path
from typing import Any, NamedTuple, Sequence

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
from .profiling import UNREPRESENTABLE, _is_representable, eval_aggregates

_HAVING_OPS = {"eq": "=", "neq": "!=", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}

SORT_ORDERS = ("asc", "desc")

#: Scratch table the overflow recovery works from — one row per source row,
#: carrying its group id and each measure's FILTER predicate as columns.
_ROWS = "_agg_rows"

# Functions whose grand total is a single well-defined number over the whole
# filtered source. Everything else — max, min, mean, median, std, nunique,
# first, last — has no meaningful footer value: a "total" for them can only be
# recomputed at whatever grain the caller actually wants (that is what pivot's
# row/column totals do). Emitting one anyway is how a `max` footer came back as
# 75000 when the true maximum was 25000.
TOTALABLE_FUNCTIONS = frozenset({"sum", "count"})

#: Values of ``AggregateResponse.totals_omitted``.
TOTAL_OMITTED_NON_ADDITIVE = "non-additive"
#: The measure has no finite double for at least one group (or in total), so
#: any footer for it would be a partial sum presented as the whole.
TOTAL_OMITTED_UNREPRESENTABLE = "unrepresentable"


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


def _agg_alias(spec: Any) -> str:
    return spec.alias or f"{spec.column}_{spec.function}"


def _base_agg_expr(spec: Any) -> str:
    """The bare ``f(column)`` for one spec, without its FILTER clause."""
    qcol = quote_ident(spec.column)
    if spec.function == "nunique":
        return f"COUNT(DISTINCT {qcol})"
    return f"{AGG_SQL_MAP[spec.function]}({qcol})"


def _spec_filter(spec: Any, available: set[str], binds: list[Any]) -> str:
    """The compiled predicate of a spec's conditional filter ("" when it has
    none); *binds* is extended with its values, in textual order.

    Pure, so the overflow recovery can compile the same predicate a second time
    rather than have it threaded through every caller.
    """
    if spec.filter is None:
        return ""
    fdict = spec.filter.model_dump()
    unknown = sorted(c for c in _filter_columns(fdict) if c not in available)
    if unknown:
        raise ProblemException(
            400,
            f"Aggregation filter columns not found: {unknown}. "
            f"Valid options: {sorted(available)}",
            code="unknown-column", columns=unknown,
            available=sorted(available))
    return compile_filter(fdict, binds)


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
        alias = _agg_alias(spec)
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
        expr = _base_agg_expr(spec)

        expr_binds: list[Any] = []
        clause = _spec_filter(spec, available, expr_binds)
        if clause:
            expr += f" FILTER (WHERE {clause})"
            uses_filter_src = True

        agg_exprs[alias] = (expr, expr_binds)
        select_binds.extend(expr_binds)
        agg_parts.append(f"{expr} AS {quote_ident(alias)}")
    return agg_parts, agg_aliases, select_binds, agg_exprs, uses_filter_src


def _compile_having(
    having: Sequence[Any],
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


# ---------------------------------------------------------------------------
# Unrepresentable measures
# ---------------------------------------------------------------------------
# DuckDB's variance family (STDDEV_SAMP behind `function: "std"`, and VAR/CORR
# behind the same accumulator) squares its input, so a single legitimate finite
# value near 1e308 overflows the sum of squares and DuckDB *raises*
# OutOfRangeException — killing the whole statement. In a grouped aggregation
# that meant one extreme value in one group took out every group and every
# other measure of the request, which surfaced as a 400 "Query error" on data
# the caller can see is perfectly valid.
#
# The policy here is `/profile`'s, because two endpoints answering the same
# arithmetic differently is its own bug: a figure with no finite double comes
# back NULL and is NAMED, and everything else still computes.


class _GroupedQuery(NamedTuple):
    """What a grouped aggregation needs to be rebuilt after an overflow."""

    source: str
    from_target: str
    where_sql: str
    where_binds: list[Any]
    group_entries: list[tuple[str, str]]
    aggregations: list[Any]
    aliases: list[str]
    available: set[str]
    having: Sequence[Any] = ()
    sort_by: str | None = None
    sort_order: str = "asc"
    limit: int | None = None


class _GroupedResult(NamedTuple):
    """Outcome of running one grouped aggregation into a table.

    ``exprs`` is None on the ordinary path (nothing was rewritten, so the
    caller's own SQL context still describes the result); after a recovery it
    carries the rewritten measure expressions, which the caller needs to keep
    its grand totals consistent with the table it just got.
    """

    unavailable: list[str]
    source: str
    exprs: dict[str, str] | None = None
    having_sql: str = ""
    having_binds: list[Any] = ()


def _measure_expr(base: str, conds: list[str]) -> str:
    return f"{base} FILTER (WHERE {' AND '.join(conds)})" if conds else base


def _null_non_finite(conn: duckdb.DuckDBPyConnection, table: str,
                     aliases: list[str]) -> list[str]:
    """NULL out — and name — any inf/NaN measure cell in *table*.

    Not every overflow raises: SUM/AVG of two values near 1e308 return +inf
    with no exception at all. inf is not JSON, and pydantic renders it as null
    on the way out, so the number would vanish from the response without
    anything saying it ever existed. Nulling it at the source keeps the
    persisted parquet honest too, and returns the alias so it can be named.
    """
    types = {r[0]: r[1] for r in conn.execute(f"DESCRIBE {table}").fetchall()}
    floats = [a for a in aliases if types.get(a) in ("DOUBLE", "FLOAT", "REAL")]
    if not floats:
        return []
    counts = conn.execute(
        "SELECT " + ", ".join(
            f"COUNT(*) FILTER (WHERE NOT isfinite({quote_ident(a)}))" for a in floats)
        + f" FROM {table}").fetchone()
    bad = [a for a, n in zip(floats, counts) if n]
    if bad:
        conn.execute(
            f"CREATE OR REPLACE TABLE {table} AS SELECT * REPLACE ("
            + ", ".join(
                f"CASE WHEN isfinite({quote_ident(a)}) THEN {quote_ident(a)} END "
                f"AS {quote_ident(a)}" for a in bad)
            + f") FROM {table}")
    return bad


def _build_rows_table(conn: duckdb.DuckDBPyConnection,
                      q: _GroupedQuery) -> list[tuple[str, str]]:
    """Materialise one row per (filtered) source row into ``_agg_rows``, with
    its group id ``_gid`` and one boolean column per conditional measure.

    Returns (bare aggregate expression, FILTER condition) per measure.

    Baking the group key and the per-measure filters into columns is what makes
    the recovery's queries free of bind parameters, so a measure can be
    restricted to a range of groups — or handed to ``eval_aggregates`` — by
    string alone. DENSE_RANK numbers the groups exactly as GROUP BY forms them
    (equal keys, NULLs included, share a rank).
    """
    cols = [f'{expr} AS "_g{i}"' for i, (_, expr) in enumerate(q.group_entries)]
    binds: list[Any] = list(q.where_binds)
    parts: list[tuple[str, str]] = []
    for i, spec in enumerate(q.aggregations):
        fbinds: list[Any] = []
        clause = _spec_filter(spec, q.available, fbinds)
        if clause:
            cols.append(f'({clause}) AS "_f{i}"')
            binds.extend(fbinds)
        parts.append((_base_agg_expr(spec), f'"_f{i}"' if clause else ""))

    inner = f"SELECT *, {', '.join(cols)} FROM {q.from_target}" if cols \
        else f"SELECT * FROM {q.from_target}"
    order = ", ".join(f'"_g{i}"' for i in range(len(q.group_entries)))
    query = (f'SELECT *, DENSE_RANK() OVER ({f"ORDER BY {order}" if order else ""}) '
             f'AS "_gid" FROM ({inner})')
    if q.where_sql:
        # Same pre-filtering CTE as the main statement, so bind order stays
        # textual: WHERE first, then the per-measure FILTERs.
        query = (f"WITH _agg_src AS (SELECT * FROM {q.source} WHERE {q.where_sql}) "
                 + query)
    conn.execute(f"CREATE OR REPLACE TABLE {_ROWS} AS {query}", binds)
    return parts


def _unrepresentable_gids(conn: duckdb.DuckDBPyConnection, exprs: list[str],
                          lo: int, hi: int) -> dict[int, set[int]]:
    """measure index -> the group ids whose value has no finite double.

    ``eval_aggregates``'s policy and its halving, applied to the group axis:
    the whole range is evaluated in ONE grouped query and only a range that
    raises is split, so isolating one bad group out of N costs O(log N)
    queries instead of one query per group. A single group is handed to
    ``eval_aggregates`` itself, which isolates WHICH of its measures overflowed
    — the two axes then answer with one shared definition of "unrepresentable",
    including the inf/NaN an aggregate can return without raising at all.
    """
    if lo == hi:
        values = eval_aggregates(
            conn, exprs, f'(SELECT * FROM {_ROWS} WHERE "_gid" = {lo})')
        return {i: {lo} for i, v in enumerate(values) if v is UNREPRESENTABLE}
    try:
        rows = conn.execute(
            f'SELECT "_gid", {", ".join(exprs)} FROM {_ROWS} '
            f'WHERE "_gid" BETWEEN {lo} AND {hi} GROUP BY "_gid"').fetchall()
    except duckdb.OutOfRangeException:
        mid = (lo + hi) // 2
        found = _unrepresentable_gids(conn, exprs, lo, mid)
        for i, gids in _unrepresentable_gids(conn, exprs, mid + 1, hi).items():
            found.setdefault(i, set()).update(gids)
        return found
    found: dict[int, set[int]] = {}
    for row in rows:
        for i, value in enumerate(row[1:]):
            if not _is_representable(value):
                found.setdefault(i, set()).add(int(row[0]))
    return found


def _recover_grouped(conn: duckdb.DuckDBPyConnection, table: str,
                     q: _GroupedQuery, error_prefix: str) -> _GroupedResult:
    """Rebuild the aggregation with the unrepresentable groups nulled out."""
    parts = _build_rows_table(conn, q)
    exprs = [_measure_expr(base, [cond] if cond else []) for base, cond in parts]
    max_gid = conn.execute(f'SELECT MAX("_gid") FROM {_ROWS}').fetchone()[0]
    bad = _unrepresentable_gids(conn, exprs, 1, int(max_gid)) if max_gid else {}

    rewritten: dict[str, str] = {}
    unavailable: list[str] = []
    for i, alias in enumerate(q.aliases):
        base, cond = parts[i]
        conds = [cond] if cond else []
        gids = sorted(bad.get(i, ()))
        if gids:
            # Excluding the group's rows from this ONE measure leaves the
            # aggregate with nothing to accumulate, so the cell is NULL while
            # every other group keeps the value DuckDB would have computed —
            # bit for bit, since it sees exactly the same rows as before.
            unavailable.append(alias)
            conds.append(f'"_gid" NOT IN ({", ".join(str(g) for g in gids)})')
        rewritten[alias] = _measure_expr(base, conds)

    group_entries = [(name, f'"_g{i}"')
                     for i, (name, _) in enumerate(q.group_entries)]
    agg_parts = [f"{rewritten[a]} AS {quote_ident(a)}" for a in q.aliases]
    having_sql, having_binds = _compile_having(
        q.having, {a: (rewritten[a], []) for a in q.aliases}, q.aliases)
    sql = _assemble_sql(_ROWS, _ROWS, "", group_entries, agg_parts, q.aliases,
                        having_sql, q.sort_by, q.sort_order, q.limit)
    try:
        conn.execute(f"CREATE TABLE {table} AS {sql}", having_binds)
    except duckdb.Error as e:
        raise HTTPException(status_code=400, detail=f"{error_prefix}: {e}")
    for alias in _null_non_finite(conn, table, q.aliases):
        if alias not in unavailable:
            unavailable.append(alias)
    return _GroupedResult([a for a in q.aliases if a in unavailable], _ROWS,
                          rewritten, having_sql, having_binds)


def _run_grouped(conn: duckdb.DuckDBPyConnection, table: str, sql: str,
                 binds: list[Any], q: _GroupedQuery,
                 error_prefix: str = "Query error") -> _GroupedResult:
    """Run a grouped aggregation into *table*, recovering from an overflow.

    The ordinary path is the statement the caller already assembled — one
    query, no added cost. Only an OutOfRangeException triggers the rebuild.
    """
    try:
        conn.execute(f"CREATE TABLE {table} AS {sql}", binds)
    except duckdb.OutOfRangeException:
        return _recover_grouped(conn, table, q, error_prefix)
    except duckdb.Error as e:
        raise HTTPException(status_code=400, detail=f"{error_prefix}: {e}")
    return _GroupedResult(_null_non_finite(conn, table, q.aliases), q.source)


def _split_totalable(aggregations: list[Any]) -> tuple[list[Any], dict[str, str]]:
    """(specs that get a grand total, alias -> why the others don't)."""
    totalable: list[Any] = []
    omitted: dict[str, str] = {}
    for spec in aggregations:
        if spec.function in TOTALABLE_FUNCTIONS:
            totalable.append(spec)
        else:
            omitted[_agg_alias(spec)] = TOTAL_OMITTED_NON_ADDITIVE
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
    recovered: dict[str, str] | None = None,
    unavailable: Sequence[str] = (),
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
    # A measure that had no finite value for some group has no trustworthy
    # grand total either: totalling what is left would be a partial sum wearing
    # the name of the whole. Named as unrepresentable instead — the same
    # distinction `totals_omitted` already draws for non-additive functions.
    kept = []
    for spec in total_specs:
        if _agg_alias(spec) in unavailable:
            omitted[_agg_alias(spec)] = TOTAL_OMITTED_UNREPRESENTABLE
        else:
            kept.append(spec)
    total_specs = kept
    if not total_specs:
        return {}, omitted

    agg_parts, aliases, select_binds, _exprs, _needs_src = _compile_select_aggs(
        total_specs, available)
    if recovered is not None:
        # After a recovery the table came from the scratch row table with
        # rewritten expressions; the footer has to be computed the same way or
        # it describes a different query than the rows above it.
        agg_parts = [f"{recovered[a]} AS {quote_ident(a)}" for a in aliases]
        select_binds = []

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
    totals: dict[str, Any] = {}
    for a, v in zip(aliases, row):
        # A total can leave the double range even when no single group did
        # (SUM over every row). inf is not an answer, so it is named rather
        # than passed off as a null that reads like "zero rows".
        if _is_representable(v):
            totals[a] = safe_value(v)
        else:
            omitted[a] = TOTAL_OMITTED_UNREPRESENTABLE
    return totals, omitted


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
        outcome = _run_grouped(conn, "_agg_probe", sql, binds, _GroupedQuery(
            source, from_target, where_sql, where_binds, group_entries,
            request.aggregations, agg_aliases, available, request.having,
            request.sort_by, request.sort_order, effective_limit))
        if outcome.exprs is not None:
            # The recovery recomputed the result from the scratch row table;
            # the grand totals below have to describe that same query.
            source = from_target = outcome.source
            where_sql, where_binds = "", []
            group_entries = [(name, f'"_g{i}"')
                             for i, (name, _) in enumerate(group_entries)]
            having_sql, having_binds = outcome.having_sql, outcome.having_binds

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
            group_entries, having_sql, having_binds,
            recovered=outcome.exprs, unavailable=outcome.unavailable)

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
            unavailable_measures=outcome.unavailable,
            truncated=truncated,
            result_file=result_filename,
        )
    finally:
        conn.close()
