"""Project routes (BACKEND-DESIGN §3.1, phase 1)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.dependencies import get_app_service, get_db_session, get_execution_repository, get_workflow_service
from ..core.exceptions import ValidationError
from ..core.identity import get_actor
from ..schemas.app import AppCreateRequest
from ..schemas.workflow import WorkflowCreateRequest
from ..services.project_service import ProjectError, ProjectService, project_to_dict

router = APIRouter(prefix="/projects")


class ProjectCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    slug: str | None = None
    description: str | None = Field(None, max_length=2000)


class ProjectUpdateRequest(BaseModel):
    name: str | None = Field(None, max_length=255)
    slug: str | None = None
    description: str | None = None
    environments: list[dict[str, Any]] | None = None
    default_environment: str | None = None
    live_environment: str | None = None
    settings: dict[str, Any] | None = None


def raise_project_error(e: ProjectError) -> None:
    raise HTTPException(status_code=e.status, detail=e.detail()) from e


async def get_project_service(session: AsyncSession = Depends(get_db_session)) -> ProjectService:
    return ProjectService(session)


async def require_project(project: str, svc: ProjectService):
    try:
        return await svc.require(project)
    except ProjectError as e:
        raise_project_error(e)


@router.get("")
async def list_projects(svc: ProjectService = Depends(get_project_service)):
    return [await svc.list_item(p) for p in await svc.list()]


@router.post("", status_code=201)
async def create_project(body: ProjectCreateRequest, request: Request, svc: ProjectService = Depends(get_project_service)):
    try:
        p = await svc.create(body.name, body.slug, body.description, created_by=get_actor(request))
    except ProjectError as e:
        raise_project_error(e)
    return project_to_dict(p)


@router.get("/{project}")
async def get_project(project: str, svc: ProjectService = Depends(get_project_service)):
    return project_to_dict(await require_project(project, svc))


@router.put("/{project}")
async def update_project(project: str, body: ProjectUpdateRequest, dry_run: bool = False,
                         svc: ProjectService = Depends(get_project_service)):
    if dry_run:
        try:
            return await svc.preview_update(project, body.model_dump(exclude_unset=True))
        except ProjectError as e:
            raise_project_error(e)
    try:
        p = await svc.update(project, body.model_dump(exclude_unset=True))
    except ProjectError as e:
        raise_project_error(e)
    return project_to_dict(p)


@router.delete("/{project}")
async def delete_project(project: str, svc: ProjectService = Depends(get_project_service)):
    try:
        await svc.delete(project)
    except ProjectError as e:
        raise_project_error(e)
    return {"success": True}


@router.get("/{project}/summary")
async def project_summary(project: str, svc: ProjectService = Depends(get_project_service)):
    p = await require_project(project, svc)
    last_run, health = await svc.last_run_and_health(p.id)
    missing = 0
    try:
        from ..services.secret_service import SecretService
        missing = len(await SecretService(svc._s).missing(p))
    except ImportError:
        pass
    extras = await svc.summary_extras(p)
    return {"project": project_to_dict(p), "counts": await svc.counts(p.id), "last_run": last_run,
            "health": health, "missing_secrets": missing, **extras}


@router.get("/{project}/workflows")
async def project_workflows(project: str, svc: ProjectService = Depends(get_project_service),
                            wf_service=Depends(get_workflow_service)):
    p = await require_project(project, svc)
    return await wf_service.list_workflows(project_id=p.id)


@router.post("/{project}/workflows", status_code=201)
async def project_create_workflow(project: str, body: WorkflowCreateRequest, request: Request,
                                  svc: ProjectService = Depends(get_project_service),
                                  wf_service=Depends(get_workflow_service)):
    p = await require_project(project, svc)
    body.project_id = p.id
    try:
        return await wf_service.create_workflow(body, created_by=get_actor(request))
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=e.message)


@router.get("/{project}/apps")
async def project_apps(project: str, svc: ProjectService = Depends(get_project_service),
                       app_service=Depends(get_app_service)):
    p = await require_project(project, svc)
    return await app_service.list_apps(project_id=p.id)


@router.post("/{project}/apps", status_code=201)
async def project_create_app(project: str, body: AppCreateRequest, request: Request,
                             svc: ProjectService = Depends(get_project_service),
                             app_service=Depends(get_app_service)):
    p = await require_project(project, svc)
    body.project_id = p.id
    from ..services.app_service import AppValidationError
    try:
        return await app_service.create_app(body, created_by=get_actor(request))
    except AppValidationError as e:
        raise HTTPException(status_code=422, detail=e.detail()) from e


def _run_item(e) -> dict[str, Any]:
    trigger = (e.metadata or {}).get("trigger") or {}
    route = (f"{trigger.get('method')} {trigger.get('route')}" if trigger.get("method") else trigger.get("route")) \
        if trigger.get("route") else None  # draft runs store "GET /path" in trigger.route
    duration = round((e.end_time - e.start_time).total_seconds() * 1000, 2) if e.end_time else None
    return {
        "id": e.id, "workflow_id": e.workflow_id, "workflow_name": e.workflow_name, "status": e.status,
        "mode": e.mode, "start_time": e.start_time.isoformat(), "end_time": e.end_time.isoformat() if e.end_time else None,
        "error_count": len(e.errors), "environment": e.environment, "trigger": trigger or None,
        "route_id": trigger.get("route_id"), "route": route, "app_id": trigger.get("app_id"), "duration_ms": duration,
    }


@router.get("/{project}/runs")
async def project_runs(project: str, response: Response, limit: int = Query(50, ge=1, le=500), mode: str | None = None,
                       status: str | None = None, before: str | None = None, workflow_id: str | None = None,
                       route_id: str | None = None, environment: str | None = None, trigger: str | None = None,
                       svc: ProjectService = Depends(get_project_service),
                       exec_repo=Depends(get_execution_repository)):
    p = await require_project(project, svc)
    rows = await exec_repo.list(project_id=p.id, workflow_id=workflow_id, mode=mode, status=status, limit=limit + 1,
                                before=before, route_id=route_id, environment=environment, trigger=trigger)
    if len(rows) > limit:
        rows = rows[:limit]
        response.headers["X-Next-Cursor"] = rows[-1].id
    return [_run_item(e) for e in rows]


@router.get("/{project}/runs/{execution_id}")
async def project_run_detail(project: str, execution_id: str, svc: ProjectService = Depends(get_project_service),
                             exec_repo=Depends(get_execution_repository)):
    p = await require_project(project, svc)
    e = await exec_repo.get(execution_id)
    if not e or (e.project_id or "proj_default") != p.id:
        raise HTTPException(status_code=404, detail={"code": "run_not_found", "message": "Run not found"})
    from .api_routes import run_trace
    trace = await run_trace(svc._s, execution_id)
    release = None
    rid = ((e.metadata or {}).get("trigger") or {}).get("release_id")
    if rid:  # review C3: resolve the release number for the run sheet
        from ..db.models import ReleaseModel
        rel = await svc._s.get(ReleaseModel, rid)
        release = {"id": rid, "number": rel.number if rel else None, "environment": rel.environment if rel else None}
    return {
        **_run_item(e),
        "errors": [{"node_name": x.node_name, "error": x.error, "timestamp": x.timestamp.isoformat(),
                    "error_code": x.error_code, "details": x.details} for x in e.errors],
        "node_data": {name: [{"json": d.json} for d in data] for name, data in e.node_data.items()},
        "node_metrics": e.node_metrics,
        "metadata": e.metadata,
        "trace": trace,
        "release": release,
    }
