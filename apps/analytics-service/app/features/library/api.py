"""Library API — saved analytics definitions, run history, publish, lineage.

Definitions live under their dataset for the standard RBAC pattern. Publishing
a run's output creates a new dataset (same team) or a new version of the
source dataset, with lineage recorded either way.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.exc import IntegrityError

from app.api.errors import ProblemException
from app.api.pagination import Page, PageParams, pagination
from app.features.auth.deps import Principal, ensure_dataset_permission, get_principal
from app.features.auth.permissions import Permission
from app.shared.masking import ensure_raw_access

from . import repo
from .schemas import (
    AnalyticsRunOut,
    ChartCreate,
    ChartRenderResponse,
    ChartSeries,
    ChartOut,
    ChartUpdate,
    DefinitionCreate,
    DefinitionOut,
    DefinitionUpdate,
    LineageEdge,
    LineageGraphResponse,
    LineageNode,
    LineageResponse,
    PublishRequest,
    PublishResponse,
    RunResponse,
)
from .charts import build_series
from .service import compute_definition, execute_definition, publish_run

router = APIRouter()

#: Rows a view-backed chart render reads in one page. ``RunViewRequest.limit``
#: is capped at 1000 by the explorer's schema, so this is the largest page a
#: single call can ask for; anything beyond it is reported through
#: ``truncated``/``total_rows`` rather than silently dropped.
VIEW_RENDER_LIMIT = 1000


@router.post("/datasets/{dataset_id}/analytics", response_model=DefinitionOut,
             status_code=201, tags=["library"])
async def create_definition(
    dataset_id: str, body: DefinitionCreate, principal: Principal = Depends(get_principal),
) -> DefinitionOut:
    """Save a reusable analytics definition (sample / aggregate / profile)."""
    await ensure_dataset_permission(principal, dataset_id, Permission.DATASET_WRITE)
    row = await repo.create_definition(
        dataset_id,
        {**body.model_dump(exclude={"version_selector"}),
         "version_selector": body.version_selector.model_dump(exclude_none=True)},
        created_by=principal.user_id,
    )
    if not row:
        raise HTTPException(409, f"A definition named '{body.name}' already exists on this dataset")
    return DefinitionOut(**row)


@router.get("/datasets/{dataset_id}/analytics", response_model=Page[DefinitionOut], tags=["library"])
async def list_definitions(
    dataset_id: str,
    page: PageParams = Depends(pagination),
    principal: Principal = Depends(get_principal),
) -> Page[DefinitionOut]:
    """List saved analytics definitions for a dataset, name-ordered.

    Paged like every other list route. It used to return every row and echo
    `limit = len(items)` (0 for an empty dataset), which is not a page
    description a client can act on — there was no way to ask for the next one
    and no way to tell a full page from the whole set.
    """
    await ensure_dataset_permission(principal, dataset_id, Permission.DATASET_READ)
    rows, total = await repo.list_definitions_page(
        dataset_id, limit=page.limit, offset=page.offset)
    return Page.of([DefinitionOut(**r) for r in rows], total, page)


@router.get("/datasets/{dataset_id}/analytics/{definition_id}",
            response_model=DefinitionOut, tags=["library"])
async def get_definition(
    dataset_id: str, definition_id: str, principal: Principal = Depends(get_principal),
) -> DefinitionOut:
    """Fetch one saved definition."""
    await ensure_dataset_permission(principal, dataset_id, Permission.DATASET_READ)
    row = await repo.get_definition(dataset_id, definition_id)
    if not row:
        raise HTTPException(404, f"Definition not found: {definition_id}")
    return DefinitionOut(**row)


@router.patch("/datasets/{dataset_id}/analytics/{definition_id}",
              response_model=DefinitionOut, tags=["library"])
async def update_definition(
    dataset_id: str, definition_id: str, body: DefinitionUpdate,
    principal: Principal = Depends(get_principal),
) -> DefinitionOut:
    """Update a saved definition."""
    await ensure_dataset_permission(principal, dataset_id, Permission.DATASET_WRITE)
    fields = body.model_dump(exclude_unset=True)
    if "version_selector" in fields and body.version_selector is not None:
        fields["version_selector"] = body.version_selector.model_dump(exclude_none=True)
    try:
        row = await repo.update_definition(dataset_id, definition_id, fields)
    except IntegrityError as exc:
        # (dataset_id, name) is unique. POST answers a collision with 409; a
        # rename onto a sibling's name hit the same index but had no handler,
        # so it surfaced as an opaque 500 the UI could not turn into "that name
        # is taken".
        raise ProblemException(
            409,
            f"A definition named '{fields.get('name')}' already exists on this dataset",
            code="definition-name-taken", name=fields.get("name"),
        ) from exc
    if not row:
        raise HTTPException(404, f"Definition not found: {definition_id}")
    return DefinitionOut(**row)


@router.delete("/datasets/{dataset_id}/analytics/{definition_id}", status_code=204, tags=["library"])
async def delete_definition(
    dataset_id: str, definition_id: str, principal: Principal = Depends(get_principal),
) -> None:
    """Delete a saved definition (its run history goes with it)."""
    await ensure_dataset_permission(principal, dataset_id, Permission.DATASET_WRITE)
    if not await repo.delete_definition(dataset_id, definition_id):
        raise HTTPException(404, f"Definition not found: {definition_id}")


@router.post("/datasets/{dataset_id}/analytics/{definition_id}/run",
             response_model=RunResponse, tags=["library"])
async def run_definition(
    dataset_id: str, definition_id: str, principal: Principal = Depends(get_principal),
) -> RunResponse:
    """Execute a saved definition now; result is returned and recorded."""
    ds = await ensure_dataset_permission(principal, dataset_id, Permission.DATASET_WRITE)
    # DATASET_WRITE is not DATASET_READ_SENSITIVE: an editor has the former and
    # deliberately not the latter, and this route returns the run's result rows
    # unmasked — the same computation /aggregate and /pivot refuse for that
    # seat. Gate it the same way so the saved definition isn't a second door.
    await ensure_raw_access(principal, dataset_id)
    definition = await repo.get_definition(dataset_id, definition_id)
    if not definition:
        raise HTTPException(404, f"Definition not found: {definition_id}")
    run, result = await execute_definition(ds, definition, principal)
    return RunResponse(**run, result=result)


@router.get("/datasets/{dataset_id}/analytics/{definition_id}/runs",
            response_model=Page[AnalyticsRunOut], tags=["library"])
async def list_runs(
    dataset_id: str, definition_id: str,
    page: PageParams = Depends(pagination),
    principal: Principal = Depends(get_principal),
) -> Page[AnalyticsRunOut]:
    """Run history for a definition, newest first."""
    await ensure_dataset_permission(principal, dataset_id, Permission.DATASET_READ)
    if not await repo.get_definition(dataset_id, definition_id):
        raise HTTPException(404, f"Definition not found: {definition_id}")
    rows, total = await repo.list_runs(definition_id, limit=page.limit, offset=page.offset)
    return Page.of([AnalyticsRunOut(**r) for r in rows], total, page)


@router.post("/datasets/{dataset_id}/analytics/runs/{run_id}/publish",
             response_model=PublishResponse, tags=["library"])
async def publish(
    dataset_id: str, run_id: str, body: PublishRequest,
    principal: Principal = Depends(get_principal),
) -> PublishResponse:
    """Publish a run's stored output as a new dataset or a new version."""
    ds = await ensure_dataset_permission(principal, dataset_id, Permission.DATASET_WRITE)
    run = await repo.get_run(run_id)
    if not run or run["dataset_id"] != dataset_id:
        raise HTTPException(404, f"Run not found: {run_id}")
    result = await publish_run(ds, run, mode=body.mode, name=body.name, principal=principal)
    return PublishResponse(**result)


@router.get("/datasets/{dataset_id}/lineage", response_model=LineageResponse, tags=["library"])
async def lineage(
    dataset_id: str, principal: Principal = Depends(get_principal),
) -> LineageResponse:
    """Where this dataset's versions came from, and what was derived from them.

    A lineage row can name a dataset in another team (a cross-team join). Those
    entries come back with their identity blanked and `parent_visible` /
    `child_visible` false, because `GET /datasets/{id}` answers 404 for them.
    """
    await ensure_dataset_permission(principal, dataset_id, Permission.DATASET_READ)
    graph = await repo.get_lineage(dataset_id, team_ids=_visible_teams(principal))
    return LineageResponse(dataset_id=dataset_id, **graph)


def _visible_teams(principal: Principal) -> list[str] | None:
    """Teams whose dataset names this caller may see; None for a superuser."""
    return None if principal.is_superuser else sorted(principal.memberships)


# ---------------------------------------------------------------------------
# Chart definitions (§14) — thin references to a definition or saved view
# ---------------------------------------------------------------------------

async def _validate_chart_source(dataset_id: str, definition_id: str | None,
                                 view_id: str | None) -> None:
    """The referenced source must exist on this same dataset."""
    if definition_id is not None:
        if not await repo.get_definition(dataset_id, definition_id):
            raise HTTPException(404, f"Definition not found: {definition_id}")
    if view_id is not None:
        from app.features.explorer import repo as explorer_repo

        if not await explorer_repo.get_view(dataset_id, view_id):
            raise HTTPException(404, f"View not found: {view_id}")


@router.post("/datasets/{dataset_id}/charts", response_model=ChartOut,
             status_code=201, tags=["library"])
async def create_chart(
    dataset_id: str, body: ChartCreate, principal: Principal = Depends(get_principal),
) -> ChartOut:
    """Save a chart over a saved definition or view. Charts own no query
    logic — config is frontend-owned encoding."""
    await ensure_dataset_permission(principal, dataset_id, Permission.DATASET_WRITE)
    await _validate_chart_source(dataset_id, body.definition_id, body.view_id)
    row = await repo.create_chart(dataset_id, body.model_dump(),
                                  created_by=principal.user_id)
    if not row:
        raise HTTPException(409, f"A chart named '{body.name}' already exists on this dataset")
    return ChartOut(**row)


@router.get("/datasets/{dataset_id}/charts", response_model=Page[ChartOut], tags=["library"])
async def list_charts(
    dataset_id: str,
    page: PageParams = Depends(pagination),
    principal: Principal = Depends(get_principal),
) -> Page[ChartOut]:
    """Saved charts for a dataset, name-ordered. Paged; see list_definitions."""
    await ensure_dataset_permission(principal, dataset_id, Permission.DATASET_READ)
    rows, total = await repo.list_charts_page(
        dataset_id, limit=page.limit, offset=page.offset)
    return Page.of([ChartOut(**r) for r in rows], total, page)


@router.get("/datasets/{dataset_id}/charts/{chart_id}", response_model=ChartOut, tags=["library"])
async def get_chart(
    dataset_id: str, chart_id: str, principal: Principal = Depends(get_principal),
) -> ChartOut:
    """One saved chart."""
    await ensure_dataset_permission(principal, dataset_id, Permission.DATASET_READ)
    row = await repo.get_chart(dataset_id, chart_id)
    if not row:
        raise HTTPException(404, f"Chart not found: {chart_id}")
    return ChartOut(**row)


@router.patch("/datasets/{dataset_id}/charts/{chart_id}", response_model=ChartOut, tags=["library"])
async def update_chart(
    dataset_id: str, chart_id: str, body: ChartUpdate,
    principal: Principal = Depends(get_principal),
) -> ChartOut:
    """Update a chart; retargeting to a new source clears the old one."""
    await ensure_dataset_permission(principal, dataset_id, Permission.DATASET_WRITE)
    fields = body.model_dump(exclude_unset=True)
    if fields.get("definition_id") is not None and fields.get("view_id") is not None:
        raise HTTPException(400, "A chart renders exactly one source — "
                                 "set definition_id or view_id, not both")
    # An explicitly-null source is a request to leave the chart with nothing to
    # render. `chart_definitions` has `CHECK ((definition_id IS NULL) <>
    # (view_id IS NULL))`, and the repo writes on key presence, so
    # `{"definition_id": null}` emitted `definition_id = NULL` and the CHECK
    # turned a caller mistake into a 500. It is a 400 with the same wording the
    # both-sources case uses.
    if ("definition_id" in fields or "view_id" in fields) and \
            fields.get("definition_id") is None and fields.get("view_id") is None:
        raise ProblemException(
            400,
            "A chart renders exactly one source — set definition_id or view_id "
            "to retarget it; a chart cannot be left with no source.",
            code="chart-source-required")
    await _validate_chart_source(dataset_id, fields.get("definition_id"),
                                 fields.get("view_id"))
    if fields.get("definition_id") is not None:
        fields["view_id"] = None
    elif fields.get("view_id") is not None:
        fields["definition_id"] = None
    try:
        row = await repo.update_chart(dataset_id, chart_id, fields)
    except IntegrityError as exc:
        # (dataset_id, name) is unique; see update_definition.
        raise ProblemException(
            409,
            f"A chart named '{fields.get('name')}' already exists on this dataset",
            code="chart-name-taken", name=fields.get("name"),
        ) from exc
    if not row:
        raise HTTPException(404, f"Chart not found: {chart_id}")
    return ChartOut(**row)


@router.delete("/datasets/{dataset_id}/charts/{chart_id}", status_code=204, tags=["library"])
async def delete_chart(
    dataset_id: str, chart_id: str, principal: Principal = Depends(get_principal),
) -> None:
    """Delete a saved chart."""
    await ensure_dataset_permission(principal, dataset_id, Permission.DATASET_WRITE)
    if not await repo.delete_chart(dataset_id, chart_id):
        raise HTTPException(404, f"Chart not found: {chart_id}")


def _axis_labels_collapse(field: str, rows: list[dict]) -> bool:
    """True when *field*'s values in *rows* have duplicates.

    Called only for grouped sources (aggregate/pivot), where each row is a
    distinct group — so duplicate labels mean masking merged distinct groups
    into one axis category (a data-dropping collapse), not a legitimate repeat.
    """
    labels = ["" if r.get(field) is None else str(r.get(field)) for r in rows]
    return len(set(labels)) < len(labels)


@router.post("/datasets/{dataset_id}/charts/{chart_id}/render",
             response_model=ChartRenderResponse, tags=["library"])
async def render_chart(
    dataset_id: str, chart_id: str, principal: Principal = Depends(get_principal),
) -> ChartRenderResponse:
    """Compute this chart's data by running the definition or view it references.

    A chart stores which source to render plus frontend encoding — never its own
    query. This runs that source now and returns `{categories, series}` ready to
    plot. Field selection comes from the chart's config, and anything the config
    omits is inferred, so a chart saved with an empty config still renders.

    Read-only: no run row and no artifact, unlike executing the definition.

    Masked like every other raw read path: columns the data dictionary marks
    sensitive come back masked unless the caller holds elevated access, and
    `masked_columns` names them.
    """
    ds = await ensure_dataset_permission(principal, dataset_id, Permission.DATASET_READ)
    chart = await repo.get_chart(dataset_id, chart_id)
    if not chart:
        raise HTTPException(404, f"Chart not found: {chart_id}")

    config = chart.get("config") or {}
    total_rows: int | None = None
    source_truncated = False
    if chart.get("definition_id"):
        definition = await repo.get_definition(dataset_id, chart["definition_id"])
        if not definition:
            raise HTTPException(409, "The chart's definition no longer exists")
        result = await compute_definition(ds, definition, principal)
        columns, rows, masked_columns = (
            result.columns, result.rows, result.masked_columns)
        # The aggregate/pivot services cap their own output and say so. Dropping
        # that flag here reported a server-clipped chart as complete — the same
        # class of lie the view branch below was fixed for. `total_rows` is None
        # rather than len(rows) when clipped: the source knows there are more
        # rows, not how many.
        source_truncated = result.truncated
        total_rows = result.total_rows
        source = {"type": "definition", "id": definition["id"],
                  "name": definition["name"], "kind": definition["kind"]}
    else:
        from app.features.explorer import repo as explorer_repo
        from app.features.explorer.service import run_view
        from app.features.explorer.schemas import RunViewRequest

        view = await explorer_repo.get_view(dataset_id, chart["view_id"])
        if not view:
            raise HTTPException(409, "The chart's view no longer exists")
        # The principal is load-bearing: run_view only masks when it gets one,
        # so omitting it returned the raw values /views/{id}/run withholds.
        result = await run_view(ds, view, RunViewRequest(limit=VIEW_RENDER_LIMIT),
                                principal)
        page = result.result
        rows = page.items
        columns = list(rows[0].keys()) if rows else []
        masked_columns = list(page.masked_columns)
        # RunViewRequest's limit maxes out at 1000, so a view over a larger
        # result set is cut here and the category cap (also 1000) can never
        # fire. Reporting the page's own total and cursor is what keeps
        # `truncated` honest — otherwise the chart asserted completeness for a
        # row set it had silently clipped.
        total_rows = page.total
        source_truncated = page.next_cursor is not None or (
            page.total is not None and page.total > len(rows))
        source = {"type": "view", "id": view["id"], "name": view["name"]}

    data = build_series(rows, columns, config)
    # A masked category/series axis whose mask collapses distinct values into one
    # token (a plainly-sensitive column masks every value to "***") makes
    # build_series de-dupe the axis and drop the merged groups' measures — a
    # chart that silently omits data, not merely obscures it. For a grouped
    # source each row is a distinct group, so duplicate masked labels prove a
    # collapse. Refuse, mirroring the pivot-dimension guard, rather than return
    # a data-dropping chart. Distinctness-preserving masks (email/identifier)
    # keep the labels apart and render fine.
    if chart.get("definition_id") and source.get("kind") in ("aggregate", "pivot"):
        for field in (data.x_field, data.series_field):
            if field and field in masked_columns and _axis_labels_collapse(field, rows):
                raise ProblemException(
                    403,
                    f"This chart's axis '{field}' is masked and its values collapse "
                    "to a single token, which would silently drop groups. Viewing "
                    "it requires elevated access.",
                    code="sensitive-data-restricted", column=field)
    return ChartRenderResponse(
        chart_id=chart["id"], chart_type=chart["chart_type"],
        categories=data.categories,
        series=[ChartSeries(**s) for s in data.series],
        x_field=data.x_field, y_fields=data.y_fields,
        series_field=data.series_field, row_count=len(rows),
        total_rows=total_rows, masked_columns=sorted(masked_columns),
        source=source, truncated=data.truncated or source_truncated)


@router.get("/datasets/{dataset_id}/lineage/graph",
            response_model=LineageGraphResponse, tags=["library"])
async def lineage_graph(
    dataset_id: str,
    max_depth: int = Query(default=10, ge=1, le=25),
    principal: Principal = Depends(get_principal),
) -> LineageGraphResponse:
    """The full derivation DAG around this dataset, upstream and downstream.

    `/lineage` gives the immediate parents and children; this walks the whole
    chain, which is what makes lineage a story rather than a list — "this came
    from a join of a transformation of an upload". Cycle-safe and depth-capped.

    `truncated` means the DAG really does continue past `max_depth`; datasets in
    teams you cannot read are omitted and counted in `hidden_nodes`.
    """
    await ensure_dataset_permission(principal, dataset_id, Permission.DATASET_READ)
    graph = await repo.lineage_graph(dataset_id, max_depth=max_depth,
                                     team_ids=_visible_teams(principal))
    return LineageGraphResponse(
        dataset_id=dataset_id, max_depth=max_depth,
        nodes=[LineageNode(**n) for n in graph["nodes"]],
        edges=[LineageEdge(**e) for e in graph["edges"]],
        truncated=graph["truncated"], hidden_nodes=graph["hidden_nodes"])
