"""Library API — saved analytics definitions, run history, publish, lineage.

Definitions live under their dataset for the standard RBAC pattern. Publishing
a run's output creates a new dataset (same team) or a new version of the
source dataset, with lineage recorded either way.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.api.pagination import Page, PageParams, pagination
from app.features.auth.deps import Principal, ensure_dataset_permission, get_principal
from app.features.auth.permissions import Permission

from . import repo
from .schemas import (
    AnalyticsRunOut,
    DefinitionCreate,
    DefinitionOut,
    DefinitionUpdate,
    LineageResponse,
    PublishRequest,
    PublishResponse,
    RunResponse,
)
from .service import execute_definition, publish_run

router = APIRouter()


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
    dataset_id: str, principal: Principal = Depends(get_principal),
) -> Page[DefinitionOut]:
    """List saved analytics definitions for a dataset."""
    await ensure_dataset_permission(principal, dataset_id, Permission.DATASET_READ)
    rows = await repo.list_definitions(dataset_id)
    items = [DefinitionOut(**r) for r in rows]
    return Page(items=items, total=len(items), limit=len(items), offset=0)


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
    row = await repo.update_definition(dataset_id, definition_id, fields)
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
    """Where this dataset's versions came from, and what was derived from them."""
    await ensure_dataset_permission(principal, dataset_id, Permission.DATASET_READ)
    graph = await repo.get_lineage(dataset_id)
    return LineageResponse(dataset_id=dataset_id, **graph)
