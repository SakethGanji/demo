"""Gate endpoints: run the builders' own gates on saved artifacts (read-only; see services/builder_gates.py).

    POST /projects/{project}/modules/{module}/check   static checks + scratch-table self-test
    POST /projects/{project}/check                    the same across ALL modules in one scratch copy of all tables: scenario,
                                                      fixtures / clone_rows, fake Jobs, and the standing job-flow scenario
    POST /projects/{project}/plan/check               the saved project against the approved plan (read-only, no model): missing /
                                                      extra routes, tables, columns, screens, duplicate helpers, stale modules,
                                                      plus the integration preflight (hosts, secrets, egress, Jobs case ids)
    POST /projects/{project}/smoke                    user-triggered: start ONE real job of a planned integration on scratch tables
    POST /apps/{app_id}/check                         tsc, syntax, data contract, call shape, invented literals
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.dependencies import get_app_service, get_db_session
from ..services import builder_gates as bg
from ..services.project_service import ProjectError, ProjectService, resolve_project
from .projects import raise_project_error

router = APIRouter()


class ModuleCheck(BaseModel):
    scenario: list[dict[str, Any]] | None = None


class ProjectCheck(BaseModel):
    scenario: list[dict[str, Any]] | None = None
    fixtures: dict[str, list[dict[str, Any]]] | None = None      # {table: [row, ...]} seeded into scratch
    clone_rows: int | dict[str, int] = 0                         # N real rows (or {table: N}) copied into scratch; read-only
    standing: bool | dict[str, Any] | None = None                # None = auto for job-backed projects, False = off, dict = role overrides
    jobs: dict[str, Any] | None = None                           # fake Jobs: {fail: first|all|none|[case ids], failed_index}
    baseline: bool = True


class SmokeBody(BaseModel):
    integration: str
    environment: str | None = None


@router.post("/projects/{project}/plan/check")
async def check_plan(project: str, session: AsyncSession = Depends(get_db_session)):
    try:
        p = await ProjectService(session).require(project)
        return await bg.check_plan(p.id)
    except ProjectError as e:
        raise_project_error(e)


@router.post("/projects/{project}/smoke")
async def smoke_run(project: str, body: SmokeBody, session: AsyncSession = Depends(get_db_session)):
    try:
        p = await ProjectService(session).require(project)
        out = await bg.smoke_run(p.id, body.integration, body.environment)
    except ProjectError as e:
        raise_project_error(e)
    await bg.record_check(session, p.id, "smoke", body.integration, out)
    return out


@router.post("/projects/{project}/check")
async def check_project(project: str, body: ProjectCheck | None = None, session: AsyncSession = Depends(get_db_session)):
    b = body or ProjectCheck()
    try:
        p = await ProjectService(session).require(project)
        out = await bg.check_project(p.id, scenario=b.scenario, fixtures=b.fixtures, clone_rows=b.clone_rows, standing=b.standing,
                                     jobs=b.jobs, baseline=b.baseline)
    except ProjectError as e:
        raise_project_error(e)
    await bg.record_check(session, p.id, "project", p.id, out)
    return out


@router.post("/projects/{project}/modules/{module}/check")
async def check_module(project: str, module: str, body: ModuleCheck | None = None, session: AsyncSession = Depends(get_db_session)):
    try:
        p = await ProjectService(session).require(project)
        out = await bg.check_module(p.id, module, body.scenario if body else None)
    except ProjectError as e:
        raise_project_error(e)
    await bg.record_check(session, p.id, "module", out["module"]["name"], out)
    return out


@router.post("/apps/{app_id}/check")
async def check_app(app_id: str, service=Depends(get_app_service)):
    app = await service.get_app(app_id)
    if app is None:
        raise HTTPException(status_code=404, detail="App not found")
    files = [{"path": f.path, "content": f.content} for f in app.files]
    if not files and app.source_code:
        files = [{"path": "App.tsx", "content": app.source_code}]
    session = service._session
    project = await resolve_project(session, app.project_id) if app.project_id else None
    out = await bg.check_app_files(session, project, files)
    out["app"] = {"id": app.id, "name": app.name, "version": app.current_version.id if app.current_version else None}
    if project is not None:
        await bg.record_check(session, project.id, "app", app.id, out)
    return out
