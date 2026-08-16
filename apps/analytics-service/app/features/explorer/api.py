"""Explorer API — row-level preview and structured queries over version sheets.

Routes follow the data-plane convention: full nested paths on the protected
router, RBAC via ``ensure_dataset_permission`` (cross-team 404, existence
hidden), and the ``sheet-selection-required`` contract when a multi-sheet
version is addressed without a sheet.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.features.auth.deps import Principal, ensure_dataset_permission, get_principal
from app.features.auth.permissions import Permission
from app.features.data_accelerator.api import _register_output_artifacts
from app.infra.db.storage import ArtifactLayout
from app.shared.datasets import resolve_version
from app.shared.masking import ensure_raw_access
from app.shared.query import QueryPage, QuerySpec

from app.api.pagination import Page, PageParams, pagination

from . import data_quality, repo, service
from .schemas import (
    ColumnExplorerResponse,
    DatasetViewIn,
    DatasetViewOut,
    DatasetViewUpdate,
    DuplicatesResponse,
    MissingResponse,
    ProfileRunDetail,
    ProfileRunOut,
    RunViewRequest,
    SqlQueryRequest,
    SqlQueryResponse,
    ViewRunResponse,
)

router = APIRouter()


# Defined in ``service`` because the saved-view paths need the same guard;
# see ``service.ensure_version_has_data`` for why it carries its own code.
_ensure_version_has_data = service.ensure_version_has_data


async def _readable_version(
    principal: Principal, dataset_id: str, version_number: int,
) -> dict:
    """Authorize dataset:read and resolve the version row (with data)."""
    await ensure_dataset_permission(principal, dataset_id, Permission.DATASET_READ)
    ver = await resolve_version(dataset_id, version_number=version_number)
    _ensure_version_has_data(ver)
    return ver


@router.get("/datasets/{dataset_id}/versions/{version_number}/preview",
            response_model=QueryPage, tags=["explorer"])
async def preview_version(
    dataset_id: str,
    version_number: int,
    limit: int = Query(default=100, ge=1, le=1000),
    principal: Principal = Depends(get_principal),
) -> QueryPage:
    """First rows of a version's data (single-sheet versions auto-resolve)."""
    ver = await _readable_version(principal, dataset_id, version_number)
    return await service.query_sheet(ver, None, QuerySpec(limit=limit), principal)


@router.get("/datasets/{dataset_id}/versions/{version_number}/sheets/{sheet_name}/preview",
            response_model=QueryPage, tags=["explorer"])
async def preview_sheet(
    dataset_id: str,
    version_number: int,
    sheet_name: str,
    limit: int = Query(default=100, ge=1, le=1000),
    principal: Principal = Depends(get_principal),
) -> QueryPage:
    """First rows of one sheet of a version."""
    ver = await _readable_version(principal, dataset_id, version_number)
    return await service.query_sheet(ver, sheet_name, QuerySpec(limit=limit), principal)


@router.post("/datasets/{dataset_id}/versions/{version_number}/query",
             response_model=QueryPage, tags=["explorer"])
async def query_version(
    dataset_id: str,
    version_number: int,
    spec: QuerySpec,
    principal: Principal = Depends(get_principal),
) -> QueryPage:
    """Run a structured query against a version (single-sheet auto-resolve)."""
    ver = await _readable_version(principal, dataset_id, version_number)
    return await service.query_sheet(ver, None, spec, principal)


async def _get_view_or_404(dataset_id: str, view_id: str) -> dict:
    view = await repo.get_view(dataset_id, view_id)
    if not view:
        raise HTTPException(404, f"View not found: {view_id}")
    return view


@router.post("/datasets/{dataset_id}/views", response_model=DatasetViewOut,
             status_code=201, tags=["explorer"])
async def create_view(
    dataset_id: str,
    body: DatasetViewIn,
    principal: Principal = Depends(get_principal),
) -> DatasetViewOut:
    """Save a reusable query over one logical sheet (rename-proof)."""
    ds = await ensure_dataset_permission(
        principal, dataset_id, Permission.DATASET_WRITE)
    return await service.create_view(ds, body, principal.user_id)


@router.get("/datasets/{dataset_id}/views", response_model=Page[DatasetViewOut],
            tags=["explorer"])
async def list_views(
    dataset_id: str,
    page: PageParams = Depends(pagination),
    principal: Principal = Depends(get_principal),
) -> Page[DatasetViewOut]:
    """Saved views on a dataset."""
    await ensure_dataset_permission(principal, dataset_id, Permission.DATASET_READ)
    rows, total = await repo.list_views(dataset_id, limit=page.limit,
                                        offset=page.offset)
    return Page.of([DatasetViewOut(**r) for r in rows], total, page)


@router.get("/datasets/{dataset_id}/views/{view_id}",
            response_model=DatasetViewOut, tags=["explorer"])
async def get_view(
    dataset_id: str,
    view_id: str,
    principal: Principal = Depends(get_principal),
) -> DatasetViewOut:
    """One saved view."""
    await ensure_dataset_permission(principal, dataset_id, Permission.DATASET_READ)
    return DatasetViewOut(**await _get_view_or_404(dataset_id, view_id))


@router.patch("/datasets/{dataset_id}/views/{view_id}",
              response_model=DatasetViewOut, tags=["explorer"])
async def update_view(
    dataset_id: str,
    view_id: str,
    body: DatasetViewUpdate,
    principal: Principal = Depends(get_principal),
) -> DatasetViewOut:
    """Update a saved view; retargeting re-validates the stored query."""
    ds = await ensure_dataset_permission(
        principal, dataset_id, Permission.DATASET_WRITE)
    view = await _get_view_or_404(dataset_id, view_id)
    return await service.update_view(ds, view, body)


@router.delete("/datasets/{dataset_id}/views/{view_id}", status_code=204,
               tags=["explorer"])
async def delete_view(
    dataset_id: str,
    view_id: str,
    principal: Principal = Depends(get_principal),
) -> None:
    """Delete a saved view."""
    await ensure_dataset_permission(principal, dataset_id, Permission.DATASET_WRITE)
    if not await repo.delete_view(dataset_id, view_id):
        raise HTTPException(404, f"View not found: {view_id}")


@router.post("/datasets/{dataset_id}/views/{view_id}/run",
             response_model=ViewRunResponse, tags=["explorer"])
async def run_view(
    dataset_id: str,
    view_id: str,
    body: RunViewRequest | None = None,
    principal: Principal = Depends(get_principal),
) -> ViewRunResponse:
    """Execute a saved view against its selector-pinned version."""
    ds = await ensure_dataset_permission(
        principal, dataset_id, Permission.DATASET_READ)
    view = await _get_view_or_404(dataset_id, view_id)
    return await service.run_view(ds, view, body or RunViewRequest(), principal)


@router.post("/datasets/{dataset_id}/versions/{version_number}/profile-runs",
             response_model=list[ProfileRunOut], tags=["explorer"])
async def create_profile_runs(
    dataset_id: str,
    version_number: int,
    principal: Principal = Depends(get_principal),
) -> list[ProfileRunOut]:
    """Profile every ready sheet of a version; persist one run per sheet with
    deterministic insights (idempotent per algorithm version)."""
    ds = await ensure_dataset_permission(
        principal, dataset_id, Permission.DATASET_READ)
    ver = await resolve_version(dataset_id, version_number=version_number)
    _ensure_version_has_data(ver)
    return await service.profile_version(ds, ver, principal.user_id)


@router.get("/datasets/{dataset_id}/versions/{version_number}/profile-runs",
            response_model=Page[ProfileRunOut], tags=["explorer"])
async def list_profile_runs(
    dataset_id: str,
    version_number: int,
    status: str | None = Query(default=None,
                               description="Only runs in this status"),
    algorithm_version: int | None = Query(
        default=None, description="Only runs of this profiler version"),
    page: PageParams = Depends(pagination),
    principal: Principal = Depends(get_principal),
) -> Page[ProfileRunOut]:
    """Persisted profile runs (with insights) for a version, one per sheet.

    Enveloped and paged like every other collection in the service. It was the
    one listing that returned a bare array, so a client could not tell a short
    page from the whole set and had no way to ask for only the completed runs.

    It was also the one explorer route that skipped ``_ensure_version_has_data``
    and answered an empty 200 for a version still uploading — "nothing has been
    profiled yet", which invites the Run Profiling button that the POST on this
    very path then refuses. The two verbs now agree.
    """
    await ensure_dataset_permission(principal, dataset_id, Permission.DATASET_READ)
    ver = await resolve_version(dataset_id, version_number=version_number)
    _ensure_version_has_data(ver)
    runs, total = await repo.list_runs_for_version(
        str(ver["id"]), status=status, algorithm_version=algorithm_version,
        limit=page.limit, offset=page.offset)
    return Page.of(await service.runs_with_context(ver, runs), total, page)


@router.get("/datasets/{dataset_id}/profile-runs/{run_id}",
            response_model=ProfileRunDetail, tags=["explorer"])
async def get_profile_run(
    dataset_id: str,
    run_id: str,
    principal: Principal = Depends(get_principal),
) -> ProfileRunDetail:
    """One run with its full persisted profile JSON."""
    await ensure_dataset_permission(principal, dataset_id, Permission.DATASET_READ)
    run = await repo.get_run(dataset_id, run_id)
    if not run:
        raise HTTPException(404, f"Profile run not found: {run_id}")
    ver = await resolve_version(dataset_id, version_id=run["dataset_version_id"])
    out = (await service.runs_with_context(ver, [run]))[0]
    # The stored profile's `top_values` are verbatim cell values — the same ones
    # the grid and the column drawer mask — so a viewer could read a sensitive
    # column straight out of a profile they are allowed to create.
    from app.shared.masking import redact_profile, resolve_masking
    from app.shared.datasets import resolve_version_sheet_row, ensure_sheet_schema
    profile = run.get("profile")
    sheet_row = await resolve_version_sheet_row(ver, run.get("sheet_key"))
    if sheet_row is not None:
        sheet_row = await ensure_sheet_schema(ver, sheet_row)
        masked = await resolve_masking(dataset_id, sheet_row, principal)
        profile = redact_profile(profile, masked)
    return ProfileRunDetail(**out.model_dump(), profile=profile)


@router.get("/datasets/{dataset_id}/versions/{version_number}/columns/{column}",
            response_model=ColumnExplorerResponse, tags=["explorer"])
async def explore_version_column(
    dataset_id: str,
    version_number: int,
    column: str,
    principal: Principal = Depends(get_principal),
) -> ColumnExplorerResponse:
    """Single-column statistics for a version (single-sheet auto-resolve)."""
    ver = await _readable_version(principal, dataset_id, version_number)
    return await service.explore_column(ver, None, column, principal)


@router.get("/datasets/{dataset_id}/versions/{version_number}/sheets/{sheet_name}/columns/{column}",
            response_model=ColumnExplorerResponse, tags=["explorer"])
async def explore_sheet_column(
    dataset_id: str,
    version_number: int,
    sheet_name: str,
    column: str,
    principal: Principal = Depends(get_principal),
) -> ColumnExplorerResponse:
    """Single-column statistics — nulls, uniqueness, quantiles, histogram,
    top/rare values, candidate-key status, examples."""
    ver = await _readable_version(principal, dataset_id, version_number)
    return await service.explore_column(ver, sheet_name, column, principal)


@router.get("/datasets/{dataset_id}/versions/{version_number}/duplicates",
            response_model=DuplicatesResponse, tags=["explorer"])
async def version_duplicates(
    dataset_id: str,
    version_number: int,
    columns: str | None = Query(default=None,
                                description="Comma-separated subset to group on"),
    limit: int = Query(default=25, ge=1, le=data_quality.MAX_DUPLICATE_GROUPS),
    principal: Principal = Depends(get_principal),
) -> DuplicatesResponse:
    """Duplicate-row groups for a version (single-sheet auto-resolve)."""
    ver = await _readable_version(principal, dataset_id, version_number)
    return await data_quality.find_duplicates(ver, None, columns, limit, principal)


@router.get("/datasets/{dataset_id}/versions/{version_number}/sheets/{sheet_name}/duplicates",
            response_model=DuplicatesResponse, tags=["explorer"])
async def sheet_duplicates(
    dataset_id: str,
    version_number: int,
    sheet_name: str,
    columns: str | None = Query(default=None,
                                description="Comma-separated subset to group on"),
    limit: int = Query(default=25, ge=1, le=data_quality.MAX_DUPLICATE_GROUPS),
    principal: Principal = Depends(get_principal),
) -> DuplicatesResponse:
    """Duplicate-row groups for one sheet — exact by default, or grouped on a
    ``columns=`` subset; capped, with example rows per group."""
    ver = await _readable_version(principal, dataset_id, version_number)
    return await data_quality.find_duplicates(ver, sheet_name, columns, limit,
                                               principal)


@router.get("/datasets/{dataset_id}/versions/{version_number}/missing",
            response_model=MissingResponse, tags=["explorer"])
async def version_missing(
    dataset_id: str,
    version_number: int,
    principal: Principal = Depends(get_principal),
) -> MissingResponse:
    """Missing-data report for a version (single-sheet auto-resolve)."""
    ver = await _readable_version(principal, dataset_id, version_number)
    return await data_quality.missing_report(ver, None, principal)


@router.get("/datasets/{dataset_id}/versions/{version_number}/sheets/{sheet_name}/missing",
            response_model=MissingResponse, tags=["explorer"])
async def sheet_missing(
    dataset_id: str,
    version_number: int,
    sheet_name: str,
    principal: Principal = Depends(get_principal),
) -> MissingResponse:
    """Missing-data report for one sheet: per-column null stats (from the
    persisted profile run when present) + rows-most-missing probe."""
    ver = await _readable_version(principal, dataset_id, version_number)
    return await data_quality.missing_report(ver, sheet_name, principal)


@router.post("/datasets/{dataset_id}/versions/{version_number}/sql",
             response_model=SqlQueryResponse, tags=["explorer"])
async def sql_query(
    dataset_id: str,
    version_number: int,
    request: SqlQueryRequest,
    principal: Principal = Depends(get_principal),
) -> SqlQueryResponse:
    """Ad-hoc sandboxed SQL over a version — every ready sheet is a table
    named by its sheet_key. Single SELECT only; results are row-capped and
    persisted as a `query_output` artifact.

    Datasets that declare a sensitive column require `dataset:read_sensitive`
    here, exactly as `/download` does: arbitrary SQL can alias, express or
    aggregate a masked column, so there is no per-column masking that would
    hold. Without this gate the SQL console was a one-line bypass of masking —
    and it persisted the unmasked rows as a fetchable parquet artifact.
    """
    ds = await ensure_dataset_permission(
        principal, dataset_id, Permission.DATASET_READ)
    await ensure_raw_access(principal, dataset_id)
    ver = await resolve_version(dataset_id, version_number=version_number)
    _ensure_version_has_data(ver)
    # `str(ds["id"])`, never the raw `dataset_id` path parameter. Postgres
    # compares uuids by value, so `/datasets/{DS_UPPER}/...` authorizes and
    # runs identically — but a storage key is a plain string. Writing under the
    # URL's spelling while `_register_output_artifacts` registers under the
    # canonical id yields an artifact row pointing at nothing, and the parquet
    # that was written is unreachable forever (the row is the only mapping from
    # filename to key). Nothing fails at write time; the download 404s later.
    resp = await service.raw_sql_query(
        ver, request.sql,
        ArtifactLayout("query_output", team_id=str(ds["team_id"]),
                       dataset_id=str(ds["id"])))
    await _register_output_artifacts(principal, ds, "query_output",
                                     [resp.result_file])
    return resp


@router.post("/datasets/{dataset_id}/versions/{version_number}/sheets/{sheet_name}/query",
             response_model=QueryPage, tags=["explorer"])
async def query_sheet(
    dataset_id: str,
    version_number: int,
    sheet_name: str,
    spec: QuerySpec,
    principal: Principal = Depends(get_principal),
) -> QueryPage:
    """Run a structured query — projection, filters, search, multi-sort,
    cursor paging — against one sheet of a version.

    Columns the data dictionary marks sensitive come back masked unless the
    caller has elevated access; `masked_columns` says which.
    """
    ver = await _readable_version(principal, dataset_id, version_number)
    return await service.query_sheet(ver, sheet_name, spec, principal)
