"""Duplicate + missing-data explorers (§16) — read-only views over one sheet.

Column stats come from the persisted profile run when one exists for the
version/sheet; everything else is computed live with DuckDB over the sheet
parquet. Remediation (keep-first/last) is Wave 4 (§19), deliberately absent.

SQL builders are pure functions so the unit layer can pin them without
Postgres or the app.
"""

from __future__ import annotations

from typing import Any

from app.api.errors import ProblemException
from app.shared.data_io import load_data
from app.shared.datasets import sheet_data_path
from app.shared.masking import digest, mask_rows
from app.shared.query.validate import _resolve as resolve_schema_column
from app.shared.utils.sql import quote_ident, safe_value

from . import repo
from .schemas import (
    ColumnMissing,
    DuplicateGroup,
    DuplicatesResponse,
    MissingResponse,
    MissingRow,
)

MAX_DUPLICATE_GROUPS = 100
EXAMPLES_PER_GROUP = 5
MOST_MISSING_ROWS = 5


# ---------------------------------------------------------------------------
# Pure SQL builders (unit-tested)
# ---------------------------------------------------------------------------

def _require_columns(physical_cols: list[str]) -> str:
    """Comma-joined quoted identifiers; refuses an empty column list.

    Every one of these builders interpolates the column list into a ``GROUP BY``
    or a ``WHERE``, so an empty list does not produce an empty result — it
    produces syntactically invalid SQL (``GROUP BY  HAVING ...``) and a DuckDB
    ParserException that nothing on this path catches, i.e. a 500. Failing here
    keeps the builders honest for callers the API layer has not vetted.
    """
    if not physical_cols:
        raise ValueError("at least one column is required")
    return ", ".join(quote_ident(c) for c in physical_cols)


def duplicate_groups_sql(physical_cols: list[str], limit: int) -> str:
    """Groups with >1 row, biggest first (grouped columns tiebreak)."""
    cols = _require_columns(physical_cols)
    return (f"SELECT {cols}, COUNT(*) AS cnt FROM df "
            f"GROUP BY {cols} HAVING COUNT(*) > 1 "
            f"ORDER BY cnt DESC, {cols} LIMIT {int(limit)}")


def duplicate_totals_sql(physical_cols: list[str]) -> str:
    """(group_count, duplicate_rows) over ALL duplicate groups, uncapped."""
    cols = _require_columns(physical_cols)
    return ("SELECT COUNT(*), COALESCE(SUM(cnt), 0) FROM "
            f"(SELECT COUNT(*) AS cnt FROM df GROUP BY {cols} "
            "HAVING COUNT(*) > 1)")


def group_examples_sql(physical_cols: list[str], limit: int) -> str:
    """Rows of one group, matched null-safely (?-parameterized per column)."""
    _require_columns(physical_cols)
    where = " AND ".join(f"{quote_ident(c)} IS NOT DISTINCT FROM ?"
                         for c in physical_cols)
    return f"SELECT * FROM df WHERE {where} LIMIT {int(limit)}"


def group_examples_batch_sql(physical_cols: list[str], groups: int,
                             per_group: int) -> str:
    """Up to *per_group* rows for EACH of *groups* keys, in one query.

    Replaces one ``SELECT * FROM df WHERE ...`` per group. ``df`` is a lazy
    DuckDB view over the parquet, so the per-group version re-read the object
    once per group — up to ``MAX_DUPLICATE_GROUPS`` (100) extra scans on a
    single request, with no timeout and on the event loop. The query count is
    now constant in ``limit``.

    Keys are bound positionally, group by group, in the same
    ``IS NOT DISTINCT FROM`` null-safe form as the single-group builder.
    """
    cols = _require_columns(physical_cols)
    if groups < 1:
        raise ValueError("at least one group is required")
    one = "(" + " AND ".join(f"{quote_ident(c)} IS NOT DISTINCT FROM ?"
                             for c in physical_cols) + ")"
    where = " OR ".join([one] * int(groups))
    return (f"SELECT * FROM df WHERE {where} "
            f"QUALIFY ROW_NUMBER() OVER (PARTITION BY {cols}) <= {int(per_group)}")


def null_counts_sql(physical_cols: list[str]) -> str:
    """One row: total count, then the NON-null count of each column in order."""
    counts = ", ".join(f"COUNT({quote_ident(c)})" for c in physical_cols)
    return f"SELECT COUNT(*), {counts} FROM df"


def most_missing_rows_sql(physical_cols: list[str], limit: int) -> str:
    """Rows with at least one null, most nulls first."""
    nulls = " + ".join(f"(CASE WHEN {quote_ident(c)} IS NULL THEN 1 ELSE 0 END)"
                       for c in physical_cols)
    return (f"SELECT *, ({nulls}) AS __null_count FROM df "
            f"WHERE ({nulls}) > 0 "
            f"ORDER BY __null_count DESC LIMIT {int(limit)}")


def missing_from_profile(profile: dict, name_map: dict[str, str]) -> list[ColumnMissing]:
    """Per-column missingness lifted from a persisted profile JSON."""
    return [
        ColumnMissing(
            column=name_map.get(c["name"], c["name"]),
            null_count=c.get("null_count") or 0,
            null_percent=round(c.get("null_percent") or 0.0, 2),
        )
        for c in profile.get("columns", [])
    ]


def _sorted_missing(columns: list[ColumnMissing]) -> list[ColumnMissing]:
    return sorted(columns, key=lambda c: (-c.null_percent, c.column))


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------

def _schema_columns(schema_json: list[dict]) -> list[dict]:
    return sorted(schema_json, key=lambda c: c.get("position", 0))


def _normalized(col: dict) -> str:
    return col.get("normalized_name") or col["name"]


def _row_dicts(cursor_result: Any, name_map: dict[str, str]) -> list[dict[str, Any]]:
    """Fetched rows -> dicts keyed by normalized column names."""
    names = [name_map.get(d[0], d[0]) for d in cursor_result.description]
    return [{n: safe_value(v) for n, v in zip(names, row)}
            for row in cursor_result.fetchall()]


def _bucket_key(values: Any) -> tuple:
    """A hashable identity for one group's values.

    ``repr`` rather than the values themselves so NULL and float NaN — which
    SQL groups separately — do not collapse into the same bucket, and so
    unhashable values cannot blow up the grouping.
    """
    return tuple(repr(v) for v in values)


async def _masked_by_normalized(ver: dict, row: dict,
                                principal) -> dict[str, str | None]:
    """Masked columns for this caller, keyed by NORMALIZED name.

    ``resolve_masking`` answers in physical parquet names, but every row dict
    this module emits is keyed by the normalized name, so the two have to be
    reconciled before ``mask_rows`` can match anything.
    """
    if principal is None:
        return {}
    from app.shared.masking import resolve_masking

    physical_masked = await resolve_masking(str(ver["dataset_id"]), row, principal)
    if not physical_masked:
        return {}
    name_map = {c["name"]: _normalized(c) for c in row["schema_json"]}
    return {name_map.get(k, k): v for k, v in physical_masked.items()}


async def find_duplicates(ver: dict, sheet: str | None, columns: str | None,
                          limit: int, principal=None) -> DuplicatesResponse:
    """Exact (all-column) or subset duplicate groups with example rows.

    ``examples`` are whole dataset rows and ``key`` holds the grouped-on values,
    so both are masked for a caller without elevated access — this endpoint was
    handing back exactly the values ``/download`` 403s and ``/preview`` masks.
    Masked key values become a stable pseudonym rather than a constant, or every
    duplicate group would render identically.
    """
    from .service import resolve_sheet_with_schema

    row = await resolve_sheet_with_schema(ver, sheet)
    schema = row["schema_json"]
    if columns:
        seen: dict[str, dict] = {}
        for ref in (c.strip() for c in columns.split(",") if c.strip()):
            col = resolve_schema_column(ref, schema)  # unknown-column 400
            seen.setdefault(col["name"], col)
        cols = list(seen.values())
        if not cols:
            # e.g. ``?columns=,`` or ``?columns=%20``: truthy, so it does not
            # fall back to every column, but resolves to nothing. That used to
            # build `GROUP BY  HAVING ...` and 500 on a DuckDB ParserException.
            raise ProblemException(
                400,
                "`columns` was supplied but names no column — it contained only "
                "separators or whitespace. Omit it to group on every column.",
                code="empty-column-selection", columns=columns,
                available=[_normalized(c) for c in _schema_columns(schema)])
    else:
        cols = _schema_columns(schema)
    exact = len(cols) == len(schema)
    physical = [c["name"] for c in cols]
    name_map = {c["name"]: _normalized(c) for c in _schema_columns(schema)}
    masked = await _masked_by_normalized(ver, row, principal)

    conn = load_data(sheet_data_path(ver, row))
    try:
        row_count = conn.execute("SELECT COUNT(*) FROM df").fetchone()[0]
        group_count, duplicate_rows = conn.execute(
            duplicate_totals_sql(physical)).fetchone()
        group_rows = conn.execute(
            duplicate_groups_sql(physical, limit)).fetchall()

        # One query for every group's examples, not one query per group.
        examples_by_key: dict[tuple, list[dict]] = {}
        if group_rows:
            binds = [v for g in group_rows for v in g[:-1]]
            cursor = conn.execute(
                group_examples_batch_sql(physical, len(group_rows),
                                         EXAMPLES_PER_GROUP),
                binds)
            physical_names = [d[0] for d in cursor.description]
            key_positions = [physical_names.index(c) for c in physical]
            names = [name_map.get(n, n) for n in physical_names]
            for raw in cursor.fetchall():
                examples_by_key.setdefault(
                    _bucket_key([raw[i] for i in key_positions]), []
                ).append({n: safe_value(v) for n, v in zip(names, raw)})
    finally:
        conn.close()

    groups = []
    for g in group_rows:
        key_values, count = list(g[:-1]), int(g[-1])
        examples = examples_by_key.get(_bucket_key(key_values), [])
        groups.append(DuplicateGroup(
            key={name_map[c]: (digest(safe_value(v))
                               if name_map[c] in masked else safe_value(v))
                 for c, v in zip(physical, key_values)},
            count=count,
            examples=mask_rows(examples, masked) if masked else examples))

    return DuplicatesResponse(
        sheet_name=row["sheet_name"],
        columns=[name_map[c] for c in physical],
        exact=exact,
        row_count=int(row_count),
        group_count=int(group_count),
        duplicate_rows=int(duplicate_rows),
        groups=groups,
        truncated=int(group_count) > len(groups),
        masked_columns=sorted(masked),
    )


async def missing_report(ver: dict, sheet: str | None,
                         principal=None) -> MissingResponse:
    """Per-column null stats (persisted profile when present, else computed)
    plus a live rows-most-missing probe.

    ``rows_most_missing`` carries whole dataset rows, so it is masked for a
    caller without elevated access. The per-column null counts are not values
    and stay as they are — missingness is exactly what this report is for, and
    ``mask_value`` deliberately leaves NULL as NULL for the same reason.
    """
    from .service import PROFILE_ALGORITHM_VERSION, resolve_sheet_with_schema

    row = await resolve_sheet_with_schema(ver, sheet)
    schema = _schema_columns(row["schema_json"])
    physical = [c["name"] for c in schema]
    name_map = {c["name"]: _normalized(c) for c in schema}
    masked = await _masked_by_normalized(ver, row, principal)

    run = None
    if row.get("logical_sheet_id"):
        run = await repo.get_completed_run(
            str(ver["id"]), str(row["logical_sheet_id"]), PROFILE_ALGORITHM_VERSION)

    conn = load_data(sheet_data_path(ver, row))
    try:
        if run and run.get("profile"):
            profile = run["profile"]
            source, run_id = "profile_run", str(run["id"])
            row_count = profile.get("row_count") or 0
            columns = missing_from_profile(profile, name_map)
        else:
            source, run_id = "computed", None
            counts = conn.execute(null_counts_sql(physical)).fetchone()
            row_count = int(counts[0])
            columns = [
                ColumnMissing(
                    column=name_map[c],
                    null_count=row_count - int(counts[i + 1]),
                    null_percent=round((row_count - int(counts[i + 1]))
                                       / row_count * 100, 2) if row_count else 0.0,
                )
                for i, c in enumerate(physical)
            ]
        probe = conn.execute(most_missing_rows_sql(physical, MOST_MISSING_ROWS))
        names = [name_map.get(d[0], d[0]) for d in probe.description]
        rows_most_missing = [
            MissingRow(
                null_count=int(raw[-1]),
                row=(mask_rows([{n: safe_value(v)
                                 for n, v in zip(names[:-1], raw[:-1])}],
                               masked)[0] if masked else
                     {n: safe_value(v) for n, v in zip(names[:-1], raw[:-1])}),
            )
            for raw in probe.fetchall()
        ]
    finally:
        conn.close()

    return MissingResponse(
        sheet_name=row["sheet_name"],
        source=source,
        profile_run_id=run_id,
        row_count=int(row_count),
        columns=_sorted_missing(columns),
        rows_most_missing=rows_most_missing,
        masked_columns=sorted(masked),
    )
