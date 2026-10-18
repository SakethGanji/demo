"""Drift alerts and live smoke tests (Stage 3B, CONTRACTS-STAGE3B §4-§5)."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.dependencies import get_db_session
from ..repositories.workflow_repository import WorkflowRepository, workflow_to_definition
from ..services import drift, smoke
from ..services.project_service import resolve_project

router = APIRouter()


class DriftResolveRequest(BaseModel):
    by: str = Field(..., min_length=1)


class SmokeConfigBody(BaseModel):
    enabled: bool = False
    test_ids: list[str] = Field(default_factory=list)
    live_nodes: list[str] = Field(default_factory=list)
    schedule: str | None = None
    before_release: bool = False


async def _stored(session: AsyncSession, workflow_id: str):
    stored = await WorkflowRepository(session).get(workflow_id)
    if stored is None:
        raise HTTPException(status_code=404, detail=f"Workflow not found: {workflow_id}")
    return stored


def _smoke_http(e: smoke.SmokeError) -> HTTPException:
    return HTTPException(status_code=e.status, detail=e.message)


@router.get("/workflows/{workflow_id}/drift")
async def workflow_drift(
    workflow_id: str, status: Literal["open", "all"] = Query("open"), session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    stored = await _stored(session, workflow_id)
    rows = await drift.list_for_workflow(session, workflow_id, status)
    stale = await drift.stale_ids_for(session, workflow_id, workflow_to_definition(stored.workflow))
    return {"alerts": [drift.drift_json(r) for r in rows], "stale_test_ids": stale}


@router.post("/workflows/{workflow_id}/drift/{drift_id}/resolve")
async def resolve_drift(
    workflow_id: str, drift_id: str, body: DriftResolveRequest, session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    await _stored(session, workflow_id)
    try:
        return drift.drift_json(await drift.resolve(session, workflow_id, drift_id, body.by))
    except drift.DriftNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.message)


@router.get("/projects/{project_id}/drift")
async def project_drift(project_id: str, session: AsyncSession = Depends(get_db_session)) -> dict[str, Any]:
    project = await resolve_project(session, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found")
    return {"alerts": [drift.drift_json(r) for r in await drift.list_for_project(session, project.id)]}


@router.get("/workflows/{workflow_id}/smoke")
async def get_smoke(workflow_id: str, session: AsyncSession = Depends(get_db_session)) -> dict[str, Any]:
    await _stored(session, workflow_id)
    return await smoke.get_config(session, workflow_id)


@router.put("/workflows/{workflow_id}/smoke")
async def put_smoke(
    workflow_id: str, body: SmokeConfigBody, session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    stored = await _stored(session, workflow_id)
    try:
        return await smoke.put_config(session, stored, body.model_dump())
    except smoke.SmokeError as e:
        raise _smoke_http(e)


@router.post("/workflows/{workflow_id}/smoke/run")
async def run_smoke(workflow_id: str, session: AsyncSession = Depends(get_db_session)) -> dict[str, Any]:
    stored = await _stored(session, workflow_id)
    try:
        return await smoke.run_smoke(WorkflowRepository(session), stored, "manual")
    except smoke.SmokeError as e:
        raise _smoke_http(e)


@router.get("/workflows/{workflow_id}/smoke/runs")
async def smoke_runs(
    workflow_id: str, limit: int = Query(20, ge=1, le=200), session: AsyncSession = Depends(get_db_session),
) -> list[dict[str, Any]]:
    await _stored(session, workflow_id)
    return await smoke.list_runs(session, workflow_id, limit)
