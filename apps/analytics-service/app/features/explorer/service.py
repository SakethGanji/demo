"""Explorer service — resolve a version sheet and run a QuerySpec over it.

The heavy lifting already exists: sheet resolution (with the
``sheet-selection-required`` contract) in ``app.shared.datasets`` and
validation/compilation/paging in ``app.shared.query``. This module only wires
them together around a DuckDB connection.
"""

from __future__ import annotations

import tempfile
import uuid
from pathlib import Path

import duckdb
import pandas as pd
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from app.api.errors import ProblemException
from app.infra.db.storage import ArtifactLayout, get_storage
from app.shared.data_io import load_data
from app.shared.datasets import (
    ensure_sheet_schema,
    get_version_sheet_rows,
    resolve_version,
    resolve_version_sheet_row,
    sheet_data_path,
)
from app.features.data_accelerator.schemas import ProfileRequest, TopValue
from app.features.data_accelerator.services.profiling import (
    profile_column_duckdb,
    run_profiling,
)
from app.shared import jobs
from app.shared.duck import open_sandboxed, run_sandboxed
from app.shared.query import QueryPage, QuerySpec, execute_query
from app.shared.query.validate import (
    _is_text,
    _resolve as resolve_schema_column,
    validate_spec,
)
from app.shared.repo import list_version_sheets
from app.shared.utils.sql import quote_ident, safe_value

from . import repo
from .insights import compute_insights
from .schemas import (
    ColumnExplorerResponse,
    DatasetViewIn,
    DatasetViewOut,
    DatasetViewUpdate,
    InsightOut,
    ProfileRunOut,
    RunViewRequest,
    SqlQueryResponse,
    ViewRunResponse,
)

PROFILE_ALGORITHM_VERSION = 1


def ensure_version_has_data(ver: dict) -> None:
    """404 a version whose bytes are not on disk yet, with a distinct code.

    "The version exists but is still ingesting" and "there is no such version"
    are different situations with different remedies — poll versus navigate
    away — and a UI can only tell them apart if they carry different
    machine-readable codes. A bare ``HTTPException(404, ...)`` renders as the
    generic ``not_found``, which is exactly what a missing dataset renders as,
    so the distinction lived only in English prose. ``version_status`` is
    surfaced as a body field so the caller does not need a second request to
    ``/files/upload/status`` just to decide whether waiting will help.

    It lives here, not in ``api``, because the saved-view paths need it too:
    a version with no bytes also has no sheet rows, so without this guard the
    sheet lookup downstream reports the *sheet* as missing.
    """
    if not ver.get("path"):
        status = ver.get("status") or "unknown"
        raise ProblemException(
            404, f"Version has no data (status: {status})",
            code="version-not-ready", version_status=status,
            version_number=ver.get("version_number"))

# Materialization loads every sheet into memory; refuse plainly outsized
# versions rather than OOM-ing the service. Honest v1 limit (ROADMAP §6b).
MAX_SQL_MATERIALIZE_BYTES = 512 * 1024 * 1024


async def resolve_sheet_with_schema(ver: dict, sheet: str | None) -> dict:
    """Resolve a sheet row for *ver* with ``schema_json`` guaranteed present.

    Legacy versions without sheet rows get a synthetic single-sheet row over the
    version's canonical file, schema computed on the fly (and not persisted,
    since there is no row to persist to).
    """
    row = await resolve_version_sheet_row(ver, sheet)
    if row is None:
        row = {
            "id": None,
            "sheet_key": "data",
            "sheet_name": "data",
            "status": "ready",
            "storage_key": None,
            "schema_json": None,
        }
    return await ensure_sheet_schema(ver, row)


def _query_scope(ver: dict, row: dict) -> str:
    """Cursor identity for one (version, sheet) pair.

    ``execute_query`` treats this opaquely: it round-trips it through the
    cursor and rejects a resubmitted cursor whose scope differs. It used to be
    the bare version id, which meant a cursor minted on sheet A was accepted
    verbatim on sheet B of the same version — the caller silently resumed in
    the middle of a *different* sheet instead of getting the documented
    ``invalid-cursor``. The spec hash cannot cover this on its own, because the
    sheet is a path parameter and never enters the QuerySpec.
    """
    return f"{ver['id']}:{row.get('logical_sheet_id') or row.get('sheet_key') or ''}"


def _filter_column_refs(node: dict) -> set[str]:
    """Every column a dumped Filter/FilterGroup tree references."""
    if "conditions" in node:
        refs: set[str] = set()
        for child in node["conditions"]:
            refs |= _filter_column_refs(child)
        return refs
    return {node["column"]} if node.get("column") else set()


def guard_masked_query(spec: QuerySpec, schema_json: list[dict],
                       masked: dict[str, str | None]) -> None:
    """Refuse to *compute over* a column this caller may only see masked.

    Masking is applied to the result rows, but ``filters``, ``search`` and
    ``sort`` are compiled into SQL that runs against the raw parquet — and
    ``total`` is a ``COUNT(*)`` over that same WHERE clause, which ``mask_rows``
    never touches. So an unprivileged caller could recover a masked value one
    answer at a time: filter ``email eq '<guess>'`` and read ``total``, or walk
    it out with ``starts_with``/``len_*``. ``search`` is the same oracle over
    every text column at once. That is precisely the bypass
    ``ensure_raw_access`` exists to close on ``/download``, so the query path
    has to close it too.

    Projecting a masked column stays allowed — the values come back masked.
    Only predicates and ordering, which leak through row *identity* rather than
    row *content*, are refused.
    """
    if not masked:
        return
    mapping = validate_spec(spec, schema_json)
    refs: set[str] = {s.column for s in spec.sort}
    if spec.filters is not None:
        refs |= _filter_column_refs(spec.filters.model_dump())
    offending = {mapping[r] for r in refs if mapping.get(r) in masked}

    if spec.search:
        # `search` compiles to icontains over every text column, masked ones
        # included; there is no way to answer it without querying them.
        offending |= {c["name"] for c in schema_json
                      if c["name"] in masked and _is_text(c.get("dtype") or "")}

    if offending:
        raise ProblemException(
            400,
            "This query filters, searches or sorts on a column the data "
            "dictionary marks sensitive, which would reveal the masked values "
            "through the row count. Remove it from `filters`/`sort` (or drop "
            "`search`); you can still project it and read it masked.",
            code="sensitive-column-not-filterable",
            columns=sorted(offending))


async def query_sheet(ver: dict, sheet: str | None, spec: QuerySpec,
                      principal=None) -> QueryPage:
    """Execute *spec* against one sheet of *ver*; cursor-paged results.

    With a *principal*, columns the data dictionary marks sensitive are masked
    for callers without elevated access (see ``shared.masking``), and the spec
    is refused up front if it would *compute* over one of them.
    """
    from app.shared.masking import mask_rows, resolve_masking

    row = await resolve_sheet_with_schema(ver, sheet)
    masked: dict[str, str | None] = {}
    if principal is not None:
        masked = await resolve_masking(str(ver["dataset_id"]), row, principal)
        guard_masked_query(spec, row["schema_json"], masked)
    path = sheet_data_path(ver, row)
    conn = load_data(path)
    try:
        page = execute_query(conn, "df", spec, row["schema_json"],
                             version_id=_query_scope(ver, row))
    finally:
        conn.close()
    if masked:
        page.items = mask_rows(page.items, masked)
        page.masked_columns = sorted(masked)
    return page


# ---------------------------------------------------------------------------
# Saved views (§10)
# ---------------------------------------------------------------------------


async def _pinned_version(dataset_id: str, selector: dict) -> tuple[dict, list[dict]]:
    """(version a selector pins, its sheet rows).

    The data guard runs *before* the sheet rows are read. A selector can land
    on a version that has no bytes yet — a ``current``/``tag`` selector follows
    a tag moved onto an in-flight upload, and a ``version`` selector can name
    one directly. Such a version has no sheet rows either, so every caller
    below concluded "the sheet is gone" (``sheet-not-in-version`` from ``run``
    and retarget, ``Sheet not found`` from create) and sent the user to repair
    a view that was never broken. The remedy is to wait for the upload, which
    only ``version-not-ready`` says.
    """
    from app.features.library.service import _selector_pin

    ver = await resolve_version(dataset_id, **_selector_pin(selector))
    ensure_version_has_data(ver)
    return ver, await get_version_sheet_rows(ver)


def _sheet_by_logical_id(rows: list[dict], logical_sheet_id: str) -> dict | None:
    """The version's row for a logical sheet — the rename-proof lookup.

    ``run_view`` and ``update_view`` must resolve a view's sheet identically;
    when ``update_view`` resolved it by *name* instead, a version-pinned view
    over a since-renamed sheet became un-editable (404 "Sheet not found") even
    though running it worked fine.
    """
    return next((r for r in rows
                 if str(r.get("logical_sheet_id") or "") == str(logical_sheet_id)),
                None)


async def _resolve_view_target(dataset_id: str, sheet: str,
                               selector: dict) -> tuple[dict, dict]:
    """(pinned version, sheet row with schema) for a view's configuration."""
    from app.shared.datasets import _find_sheet

    ver, rows = await _pinned_version(dataset_id, selector)
    row = _find_sheet(rows, sheet)
    if not row:
        # Same state ``run`` and retarget report, so the same code: the sheet
        # named is not in the version the selector pins. A bare HTTPException
        # rendered as the generic ``not_found`` — indistinguishable from "this
        # dataset is gone" — so the create-view dialog could not tell "refresh
        # the sheet picker" from "navigate away". ``available`` is what the
        # picker would have to re-fetch anyway.
        raise ProblemException(
            404, f"Sheet not found: {sheet}",
            code="sheet-not-in-version",
            version_number=ver.get("version_number"),
            available=sorted(str(r.get("sheet_name") or r.get("sheet_key"))
                             for r in rows))
    if not row.get("logical_sheet_id"):
        raise ProblemException(
            400, "This sheet has no logical identity (legacy version) — "
                 "saved views require one",
            code="view-unsupported")
    return ver, await ensure_sheet_schema(ver, row)


async def create_view(ds: dict, body: DatasetViewIn, created_by: str) -> DatasetViewOut:
    dataset_id = str(ds["id"])
    selector = body.version_selector.model_dump(exclude_none=True)
    _, row = await _resolve_view_target(dataset_id, body.sheet, selector)
    # Early feedback only — the run validates again against the pinned version.
    validate_spec(body.query, row["schema_json"])
    created = await repo.create_view(
        dataset_id=dataset_id,
        logical_sheet_id=str(row["logical_sheet_id"]),
        name=body.name, description=body.description,
        version_selector=selector,
        query=body.query.model_dump(mode="json", exclude={"cursor"}),
        created_by=created_by)
    if created is None:
        raise HTTPException(
            409, f"A view named '{body.name}' already exists on this dataset")
    return DatasetViewOut(**created)


async def update_view(ds: dict, view: dict, body: DatasetViewUpdate) -> DatasetViewOut:
    dataset_id = str(ds["id"])
    fields: dict = {}
    if body.name is not None:
        # `name` is NOT NULL, so an explicit null is not a clear request — it
        # has no meaning and is ignored rather than turned into a 500.
        fields["name"] = body.name
    if "description" in body.model_fields_set:
        # ...but `description` IS nullable, and PATCH must distinguish "omitted"
        # (keep) from "explicitly null" (clear). Gating on `is not None` merged
        # the two, so clearing the description box and saving returned 200 with
        # the old text still stored, and there was no way to remove it short of
        # deleting the view — which changes its id and breaks every link to it.
        fields["description"] = body.description

    selector = (body.version_selector.model_dump(exclude_none=True)
                if body.version_selector is not None else view["version_selector"])
    query = QuerySpec(**(body.query.model_dump(mode="json", exclude={"cursor"})
                         if body.query is not None else view["query"]))

    # Any retargeting re-resolves the sheet and re-validates the query.
    if (body.sheet is not None or body.version_selector is not None
            or body.query is not None):
        if body.sheet is not None:
            # The caller is deliberately repointing the view: resolve by name.
            _, row = await _resolve_view_target(dataset_id, body.sheet, selector)
            fields["logical_sheet_id"] = str(row["logical_sheet_id"])
        else:
            # The sheet is unchanged, so keep the view's logical identity.
            # Resolving `view["sheet_key"]` (the sheet's CURRENT name) against
            # a pinned OLDER version 404s after a confirmed rename, and — worse
            # — silently repoints the view if that version happens to hold a
            # different sheet under the new name.
            ver, rows = await _pinned_version(dataset_id, selector)
            row = _sheet_by_logical_id(rows, view["logical_sheet_id"])
            if row is None:
                raise ProblemException(
                    404,
                    "The view's sheet is not present in version "
                    f"{ver['version_number']}",
                    code="sheet-not-in-version",
                    version_number=ver["version_number"])
            row = await ensure_sheet_schema(ver, row)
        validate_spec(query, row["schema_json"])
        fields["version_selector"] = selector
        fields["query"] = query.model_dump(mode="json", exclude={"cursor"})

    if not fields:
        return DatasetViewOut(**view)
    try:
        updated = await repo.update_view(dataset_id, view["id"], fields)
    except IntegrityError:
        raise HTTPException(
            409, f"A view named '{fields.get('name')}' already exists on this dataset")
    if updated is None:
        # The route's existence check and this UPDATE are not one transaction,
        # so a concurrent DELETE lands here. `**None` was a TypeError -> 500;
        # the view really is gone, which is a 404.
        raise HTTPException(404, f"View not found: {view['id']}")
    return DatasetViewOut(**updated)


async def run_view(ds: dict, view: dict, overrides: RunViewRequest,
                   principal=None) -> ViewRunResponse:
    """Execute a saved view against its selector-pinned version, resolving the
    sheet by logical id (rename-proof — §1's payoff)."""
    dataset_id = str(ds["id"])
    ver, rows = await _pinned_version(dataset_id, view["version_selector"])
    row = _sheet_by_logical_id(rows, view["logical_sheet_id"])
    if row is None:
        raise ProblemException(
            404,
            f"The view's sheet is not present in version {ver['version_number']}",
            code="sheet-not-in-version", version_number=ver["version_number"])
    row = await ensure_sheet_schema(ver, row)

    spec = QuerySpec(**view["query"])
    updates = {k: v for k, v in
               (("cursor", overrides.cursor), ("limit", overrides.limit))
               if v is not None}
    if updates:
        spec = spec.model_copy(update=updates)

    masked: dict[str, str | None] = {}
    if principal is not None:
        from app.shared.masking import resolve_masking

        masked = await resolve_masking(dataset_id, row, principal)
        guard_masked_query(spec, row["schema_json"], masked)

    path = sheet_data_path(ver, row)
    conn = load_data(path)
    try:
        result = execute_query(conn, "df", spec, row["schema_json"],
                               version_id=_query_scope(ver, row))
    finally:
        conn.close()
    if masked:
        from app.shared.masking import mask_rows

        result.items = mask_rows(result.items, masked)
        result.masked_columns = sorted(masked)
    return ViewRunResponse(
        view_id=view["id"], version_number=ver["version_number"],
        sheet_name=row["sheet_name"], result=result)


# ---------------------------------------------------------------------------
# Column explorer (§7)
# ---------------------------------------------------------------------------


# Fields of ColumnProfile that are *literal cell values* rather than counts,
# and so must not survive on a column the caller may only see masked. Emitting
# min/max/quantiles of a masked column hands over real values just as directly
# as top_values does — the histogram's bin edges likewise.
_VALUE_BEARING_PROFILE_FIELDS = (
    "mean", "median", "std", "min", "max", "q25", "q75",
    "min_date", "max_date", "histogram",
)


async def explore_column(ver: dict, sheet: str | None, column: str,
                         principal=None) -> ColumnExplorerResponse:
    """Full single-column statistics: the profiler's output plus uniqueness,
    candidate-key status, rare values, and examples.

    With a *principal*, a column the data dictionary marks sensitive comes back
    with every value-bearing field masked or withheld — this endpoint is the
    single-column drawer behind the grid, and it used to return the exact cell
    values the grid was masking two clicks earlier.
    """
    from app.shared.masking import mask_value, resolve_masking

    row = await resolve_sheet_with_schema(ver, sheet)
    col = resolve_schema_column(column, row["schema_json"])  # unknown-column 400
    qcol = quote_ident(col["name"])
    path = sheet_data_path(ver, row)
    conn = load_data(path)
    try:
        row_count = conn.execute("SELECT COUNT(*) FROM df").fetchone()[0]
        base = profile_column_duckdb(
            conn, col["name"], col.get("dtype") or "",
            row_count, top_n=10, include_histogram=True)
        rare_rows = conn.execute(f"""
            SELECT {qcol}, COUNT(*) AS cnt
            FROM df WHERE {qcol} IS NOT NULL
            GROUP BY {qcol} ORDER BY cnt ASC, 1 LIMIT 10
        """).fetchall()
        examples = [safe_value(r[0]) for r in conn.execute(
            f"SELECT DISTINCT {qcol} FROM df WHERE {qcol} IS NOT NULL LIMIT 5"
        ).fetchall()]
    finally:
        conn.close()

    masked: dict[str, str | None] = {}
    if principal is not None:
        masked = await resolve_masking(str(ver["dataset_id"]), row, principal)
    semantic = masked.get(col["name"]) if col["name"] in masked else None
    hidden = col["name"] in masked

    profile = base.model_dump()
    if hidden:
        profile["top_values"] = [
            {**tv, "value": mask_value(tv["value"], semantic)}
            for tv in profile["top_values"]
        ]
        for field in _VALUE_BEARING_PROFILE_FIELDS:
            profile[field] = None

    non_null = base.count - base.null_count
    return ColumnExplorerResponse(
        **profile,
        normalized_name=col.get("normalized_name") or col["name"],
        sheet_name=row["sheet_name"],
        uniqueness=round(base.unique_count / non_null, 6) if non_null else None,
        is_candidate_key=(row_count > 0 and base.null_count == 0
                          and base.unique_count == row_count),
        rare_values=[
            TopValue(
                value=(mask_value(safe_value(r[0]), semantic) if hidden
                       else safe_value(r[0])),
                count=int(r[1]),
                percent=round(int(r[1]) / row_count * 100, 2) if row_count else 0.0)
            for r in rare_rows
        ],
        examples=([mask_value(v, semantic) for v in examples] if hidden
                  else examples),
        masked_columns=[col["name"]] if hidden else [],
    )


# ---------------------------------------------------------------------------
# Profile runs + insights (§8)
# ---------------------------------------------------------------------------


def _run_out(run: dict, insights: list[dict], sheet_name: str | None) -> ProfileRunOut:
    return ProfileRunOut(
        **{k: run[k] for k in ProfileRunOut.model_fields if k in run},
        sheet_name=sheet_name,
        insights=[InsightOut(**{k: i[k] for k in InsightOut.model_fields if k in i})
                  for i in insights],
    )


async def _profilable_sheets(ver: dict) -> list[dict]:
    sheets = [r for r in await get_version_sheet_rows(ver)
              if r.get("status", "ready") == "ready" and r.get("logical_sheet_id")]
    if not sheets:
        raise ProblemException(
            400, "This version has no sheets with logical identity to profile "
                 "(legacy version — re-upload to profile)",
            code="profiling-unsupported")
    return sheets


async def profile_version(ds: dict, ver: dict, principal_user_id: str) -> list[ProfileRunOut]:
    """Profile every ready sheet of *ver*, persist runs + insights.

    Idempotent per (version, sheet, algorithm_version): re-profiling resets and
    replaces the previous run. Insight rules compare against the previous ready
    version's persisted profile when one exists.
    """
    dataset_id = str(ds["id"])
    sheets = await _profilable_sheets(ver)

    prev = await repo.previous_ready_version(dataset_id, ver["version_number"])
    prev_sheet_ids: set[str] = set()
    if prev:
        prev_sheet_ids = {
            str(r["logical_sheet_id"]) for r in await list_version_sheets(prev["id"])
            if r.get("logical_sheet_id")}

    job = await jobs.create_job(
        "profiling", dataset_id=dataset_id, dataset_version_id=str(ver["id"]),
        team_id=str(ds["team_id"]), parameters={"sheets": len(sheets)})
    await jobs.start_job(str(job["id"]))

    out: list[ProfileRunOut] = []
    run = None
    try:
        for sheet_row in sheets:
            lsid = str(sheet_row["logical_sheet_id"])
            run = await repo.upsert_run(
                dataset_id=dataset_id, dataset_version_id=str(ver["id"]),
                logical_sheet_id=lsid, job_id=str(job["id"]),
                created_by=principal_user_id,
                algorithm_version=PROFILE_ALGORITHM_VERSION)
            resp = await run_profiling(ProfileRequest(
                dataset_id=dataset_id, version_id=str(ver["id"]),
                sheet=sheet_row["sheet_name"], include_correlations=True))
            profile = resp.model_dump(mode="json")

            prev_profile = None
            if prev and lsid in prev_sheet_ids:
                prev_run = await repo.get_completed_run(
                    prev["id"], lsid, PROFILE_ALGORITHM_VERSION)
                prev_profile = (prev_run or {}).get("profile")
            insights = compute_insights(
                profile, prev_profile=prev_profile,
                is_new_sheet=bool(prev) and lsid not in prev_sheet_ids,
                sheet_name=sheet_row["sheet_name"])

            run = await repo.complete_run(run["id"], profile, insights)
            out.append(_run_out(run, insights, sheet_row["sheet_name"]))
            run = None
        await jobs.complete_job(str(job["id"]), result={
            "profile_runs": [r.id for r in out]})
    except Exception as e:
        if run is not None:
            await repo.fail_run(run["id"], str(e))
        await jobs.fail_job(str(job["id"]), str(e))
        raise HTTPException(500, f"Profiling run failed: {e}")
    return out


async def runs_with_context(ver: dict, runs: list[dict]) -> list[ProfileRunOut]:
    """Attach insights + sheet names to run rows."""
    grouped = await repo.list_insights([r["id"] for r in runs])
    names = {str(r["logical_sheet_id"]): r["sheet_name"]
             for r in await get_version_sheet_rows(ver) if r.get("logical_sheet_id")}
    return [_run_out(r, grouped.get(r["id"], []),
                     names.get(str(r["logical_sheet_id"]))) for r in runs]


# ---------------------------------------------------------------------------
# Raw-SQL escape hatch (§6b)
# ---------------------------------------------------------------------------


def _materialization_bytes(ver: dict, ready: list[dict],
                           tables: dict[str, str]) -> int | None:
    """Bytes ``open_sandboxed`` is about to pull into memory, or None.

    Sheet ``size_bytes`` is the cheap answer, but it is not always there:
    ``get_version_sheet_rows`` synthesizes rows from the legacy ``source``
    JSONB with ``size_bytes: None``, and a legacy version with no sheet rows at
    all has no sheet to read it from. Summing ``or 0`` over those gave a total
    of exactly 0, so the guard was structurally unable to fire on precisely the
    versions whose size nobody recorded. Fall back to the version row, then to
    a stat of the objects that are actually about to be read.
    """
    sizes = [r.get("size_bytes") for r in ready]
    if sizes and all(s is not None for s in sizes):
        return sum(int(s) for s in sizes)
    if ver.get("size_bytes") is not None:
        return int(ver["size_bytes"])

    storage = get_storage()
    total = 0
    for path in tables.values():
        key = storage.key_of(path)
        if key is None:
            return None
        try:
            total += storage.size(key)
        except Exception:  # noqa: BLE001 — an unsizable object is "unknown"
            return None
    return total


async def _sql_tables(ver: dict) -> dict[str, str]:
    """sheet_key -> parquet path for every ready sheet, with the size guard."""
    sheets = await get_version_sheet_rows(ver)
    ready = [r for r in sheets if r.get("status", "ready") == "ready"]
    if ready:
        tables = {r["sheet_key"]: sheet_data_path(ver, r) for r in ready}
    else:  # legacy version without sheet rows: one synthetic table
        tables = {"data": str(ver["path"])}

    total = _materialization_bytes(ver, ready, tables)
    if total is None:
        raise ProblemException(
            409,
            "The size of this version's data could not be determined, so the "
            "raw-SQL materialization limit cannot be enforced against it. "
            "Re-upload the version to record its size, or use the structured "
            "query endpoints, which stream instead of materializing.",
            code="version-size-unknown", limit_bytes=MAX_SQL_MATERIALIZE_BYTES,
        )
    if total > MAX_SQL_MATERIALIZE_BYTES:
        raise ProblemException(
            413,
            f"Version data ({total} bytes) exceeds the raw-SQL materialization "
            f"limit ({MAX_SQL_MATERIALIZE_BYTES} bytes)",
            code="version-too-large-for-sql",
            size_bytes=total, limit_bytes=MAX_SQL_MATERIALIZE_BYTES,
        )
    return tables


def _persist_frame(df: pd.DataFrame, layout: ArtifactLayout) -> str:
    """Write a result frame to the artifact area as parquet; return the filename."""
    storage = get_storage()
    filename = f"query_{uuid.uuid4().hex}.parquet"
    with tempfile.TemporaryDirectory(prefix="accel_sql_") as td:
        local = Path(td) / filename
        conn = duckdb.connect()
        try:
            conn.register("_result", df)
            escaped = str(local).replace("'", "''")
            conn.execute(f"COPY _result TO '{escaped}' (FORMAT PARQUET)")
        finally:
            conn.close()
        storage.put_file(layout.key(filename), local)
    return filename


async def raw_sql_query(ver: dict, sql: str,
                        layout: ArtifactLayout) -> SqlQueryResponse:
    """Run one sandboxed SELECT over all ready sheets of *ver*."""
    tables = await _sql_tables(ver)
    conn = open_sandboxed(tables)
    try:
        df, truncated = run_sandboxed(conn, sql)
    finally:
        conn.close()
    result_file = _persist_frame(df, layout)
    columns = [str(c) for c in df.columns]
    items = [
        {c: safe_value(v) for c, v in zip(columns, row)}
        for row in df.itertuples(index=False, name=None)
    ]
    return SqlQueryResponse(
        columns=columns, items=items, row_count=len(items),
        truncated=truncated, tables=sorted(tables), result_file=result_file,
    )
