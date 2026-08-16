"""Transformation API — saved pipelines, preview, runs, and publishing (§19–§21).

Definitions live under their dataset, so RBAC is the standard
``ensure_dataset_permission`` pattern (cross-team 404 hides existence, in-team
permission failures are 403). Publishing is always non-destructive: it writes a
new dataset or a new version, never touching the immutable source.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.pagination import Page, PageParams, pagination
from app.features.auth.deps import Principal, ensure_dataset_permission, get_principal
from app.features.auth.permissions import Permission
from app.shared.masking import ensure_raw_access
from app.features.library.schemas import PublishResponse

from . import repo, service
from .schemas import (
    DEFAULT_PREVIEW_ROWS,
    DEFAULT_PREVIEW_ROWS_DOC,
    MAX_PREVIEW_ROWS,
    TransformationCompile,
    TransformationCompileResult,
    TransformationCreate,
    TransformationOut,
    TransformationRunDetail,
    TransformationRunOut,
    TransformationUpdate,
    TransformPreview,
    TransformPublishRequest,
)

router = APIRouter()


async def _definition_or_404(dataset_id: str, definition_id: str) -> dict:
    definition = await repo.get_definition(dataset_id, definition_id)
    if not definition:
        raise HTTPException(404, f"Transformation not found: {definition_id}")
    return definition


@router.post("/datasets/{dataset_id}/transformations", response_model=TransformationOut,
             status_code=201, tags=["transform"])
async def create_transformation(
    dataset_id: str, body: TransformationCreate,
    principal: Principal = Depends(get_principal),
) -> TransformationOut:
    """Save a transformation pipeline over one logical sheet.

    The pipeline is compiled against the sheet's schema before it is stored, so
    a definition can never be saved in a state that is guaranteed to fail.
    """
    ds = await ensure_dataset_permission(principal, dataset_id, Permission.DATASET_WRITE)
    return TransformationOut(**await service.create_transformation(
        ds, body, principal.user_id))


@router.post("/datasets/{dataset_id}/transformations/compile",
             response_model=TransformationCompileResult, tags=["transform"])
async def compile_transformation(
    dataset_id: str, body: TransformationCompile,
    principal: Principal = Depends(get_principal),
) -> TransformationCompileResult:
    """Validate an unsaved pipeline and fold its schema. Persists nothing.

    Registered before the ``{definition_id}`` routes so ``compile`` is never
    read as an id. Read permission, not write: nothing is created, and a user
    who may read the data may ask what a pipeline over it would produce.

    ``rows`` additionally samples the result — the same bounded ``USING SAMPLE``
    dry run as the saved-definition preview.
    """
    ds = await ensure_dataset_permission(principal, dataset_id, Permission.DATASET_READ)
    if body.rows:
        # `rows` turns compile into a data read: it returns sampled OUTPUT rows.
        # A pipeline can rename or derive columns, so masking the result by
        # source column name is not sound — a `compute` step could copy a
        # sensitive column into a new name. Gate it the way /aggregate, /pivot,
        # /sql and /sample already gate raw reads. Schema-only compile (no
        # `rows`) stays open: it exposes column names, not values.
        await ensure_raw_access(principal, dataset_id)
    return await service.compile_transformation(ds, body)


@router.get("/datasets/{dataset_id}/transformations",
            response_model=Page[TransformationOut], tags=["transform"])
async def list_transformations(
    dataset_id: str, page: PageParams = Depends(pagination),
    principal: Principal = Depends(get_principal),
) -> Page[TransformationOut]:
    """Saved transformations on a dataset."""
    await ensure_dataset_permission(principal, dataset_id, Permission.DATASET_READ)
    rows, total = await repo.list_definitions(dataset_id, limit=page.limit,
                                              offset=page.offset)
    return Page.of([TransformationOut(**r) for r in rows], total, page)


@router.get("/datasets/{dataset_id}/transformations/{definition_id}",
            response_model=TransformationOut, tags=["transform"])
async def get_transformation(
    dataset_id: str, definition_id: str,
    principal: Principal = Depends(get_principal),
) -> TransformationOut:
    """One saved transformation."""
    await ensure_dataset_permission(principal, dataset_id, Permission.DATASET_READ)
    return TransformationOut(**await _definition_or_404(dataset_id, definition_id))


@router.patch("/datasets/{dataset_id}/transformations/{definition_id}",
              response_model=TransformationOut, tags=["transform"])
async def update_transformation(
    dataset_id: str, definition_id: str, body: TransformationUpdate,
    principal: Principal = Depends(get_principal),
) -> TransformationOut:
    """Update a transformation; any retargeting re-validates the pipeline."""
    ds = await ensure_dataset_permission(principal, dataset_id, Permission.DATASET_WRITE)
    definition = await _definition_or_404(dataset_id, definition_id)
    return TransformationOut(**await service.update_transformation(ds, definition, body))


@router.delete("/datasets/{dataset_id}/transformations/{definition_id}",
               status_code=204, tags=["transform"])
async def delete_transformation(
    dataset_id: str, definition_id: str,
    principal: Principal = Depends(get_principal),
) -> None:
    """Delete a transformation (its run history goes with it)."""
    await ensure_dataset_permission(principal, dataset_id, Permission.DATASET_WRITE)
    if not await repo.delete_definition(dataset_id, definition_id):
        raise HTTPException(404, f"Transformation not found: {definition_id}")


@router.post("/datasets/{dataset_id}/transformations/{definition_id}/preview",
             response_model=TransformPreview, tags=["transform"])
async def preview_transformation(
    dataset_id: str, definition_id: str,
    rows: int = Query(default=DEFAULT_PREVIEW_ROWS, ge=1, le=MAX_PREVIEW_ROWS,
                      description=DEFAULT_PREVIEW_ROWS_DOC),
    principal: Principal = Depends(get_principal),
) -> TransformPreview:
    """Dry-run the pipeline over a sample. Persists nothing — no job, no artifact."""
    ds = await ensure_dataset_permission(principal, dataset_id, Permission.DATASET_READ)
    # A preview renders real output rows; same reasoning as compile-with-rows.
    await ensure_raw_access(principal, dataset_id)
    definition = await _definition_or_404(dataset_id, definition_id)
    return await service.preview_transformation(ds, definition, rows)


@router.post("/datasets/{dataset_id}/transformations/{definition_id}/run",
             response_model=TransformationRunDetail, tags=["transform"])
async def run_transformation(
    dataset_id: str, definition_id: str,
    sync: bool = Query(default=True,
                       description="Run in-request; false enqueues for the worker"),
    principal: Principal = Depends(get_principal),
) -> TransformationRunDetail:
    """Execute the pipeline and store the result as a `transform_output` artifact.

    With ``sync=false`` the run row comes back still ``running`` and the job
    worker picks it up; poll the run for completion.
    """
    ds = await ensure_dataset_permission(principal, dataset_id, Permission.DATASET_WRITE)
    definition = await _definition_or_404(dataset_id, definition_id)
    run = await service.start_run(ds, definition, principal, sync=sync)
    return TransformationRunDetail(**run)


@router.get("/datasets/{dataset_id}/transformations/{definition_id}/runs",
            response_model=Page[TransformationRunOut], tags=["transform"])
async def list_runs(
    dataset_id: str, definition_id: str,
    page: PageParams = Depends(pagination),
    principal: Principal = Depends(get_principal),
) -> Page[TransformationRunOut]:
    """Run history for a transformation, newest first."""
    await ensure_dataset_permission(principal, dataset_id, Permission.DATASET_READ)
    await _definition_or_404(dataset_id, definition_id)
    rows, total = await repo.list_runs(definition_id, limit=page.limit,
                                       offset=page.offset)
    return Page.of([TransformationRunOut(**r) for r in rows], total, page)


@router.get("/datasets/{dataset_id}/transformations/runs/{run_id}",
            response_model=TransformationRunDetail, tags=["transform"])
async def get_run(
    dataset_id: str, run_id: str, principal: Principal = Depends(get_principal),
) -> TransformationRunDetail:
    """One run, with its §21 output profile and drift-vs-source."""
    await ensure_dataset_permission(principal, dataset_id, Permission.DATASET_READ)
    run = await repo.get_run(run_id)
    if not run or run["dataset_id"] != dataset_id:
        raise HTTPException(404, f"Run not found: {run_id}")
    return TransformationRunDetail(**run)


@router.post("/datasets/{dataset_id}/transformations/runs/{run_id}/publish",
             response_model=PublishResponse, tags=["transform"])
async def publish_run(
    dataset_id: str, run_id: str, body: TransformPublishRequest,
    principal: Principal = Depends(get_principal),
) -> PublishResponse:
    """Publish a run's output as a new dataset or a new version of this one."""
    ds = await ensure_dataset_permission(principal, dataset_id, Permission.DATASET_WRITE)
    run = await repo.get_run(run_id)
    if not run or run["dataset_id"] != dataset_id:
        raise HTTPException(404, f"Run not found: {run_id}")
    result = await service.publish_transformation_run(
        ds, run, mode=body.mode, name=body.name, principal=principal)
    return PublishResponse(**result)
