"""Python modules + sandbox status (BACKEND-DESIGN §3.8, phase 8)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.dependencies import get_db_session
from ..core.identity import get_actor
from ..services.code_module_service import CodeModuleService, module_to_dict
from ..services.project_service import ProjectError, ProjectService
from .projects import raise_project_error

router = APIRouter()


class ModuleCreate(BaseModel):
    name: str
    source: str


class ModuleUpdate(BaseModel):
    source: str
    # Review B4: optimistic concurrency — the version the editor started from (either one; or If-Match).
    base_source_sha: str | None = None
    base_updated_at: str | None = None


async def _ctx(project: str, session: AsyncSession):
    try:
        return await ProjectService(session).require(project), CodeModuleService(session)
    except ProjectError as e:
        raise_project_error(e)


async def _g(coro):
    try:
        return await coro
    except ProjectError as e:
        raise_project_error(e)


@router.get("/sandbox/status")
async def sandbox_status():
    from ..sandbox.backend import backend_status
    return backend_status()


@router.get("/projects/{project}/modules")
async def list_modules(project: str, session: AsyncSession = Depends(get_db_session)):
    p, svc = await _ctx(project, session)
    return await svc.list(p)


@router.post("/projects/{project}/modules", status_code=201)
async def create_module(project: str, body: ModuleCreate, request: Request, dry_run: bool = False,
                        session: AsyncSession = Depends(get_db_session)):
    p, svc = await _ctx(project, session)
    out = await _g(svc.create(p, body.name, body.source, actor=get_actor(request), dry_run=dry_run))
    if dry_run:  # review D7: a preview creates nothing → 200, not 201
        from fastapi.responses import JSONResponse
        return JSONResponse(out, status_code=200)
    return out


@router.get("/projects/{project}/modules/{mid}")
async def get_module(project: str, mid: str, session: AsyncSession = Depends(get_db_session)):
    from fastapi.responses import JSONResponse
    p, svc = await _ctx(project, session)
    m = await _g(svc.get(p, mid))
    return JSONResponse(module_to_dict(m), headers={"ETag": f'"{m.source_sha}"'})


@router.put("/projects/{project}/modules/{mid}")
async def update_module(project: str, mid: str, body: ModuleUpdate, request: Request, dry_run: bool = False,
                        session: AsyncSession = Depends(get_db_session)):
    p, svc = await _ctx(project, session)
    if_match = (request.headers.get("if-match") or "").strip().strip('"') or None
    return await _g(svc.update(p, mid, body.source, dry_run=dry_run, actor=get_actor(request),
                               base_source_sha=body.base_source_sha or if_match, base_updated_at=body.base_updated_at))


@router.delete("/projects/{project}/modules/{mid}")
async def delete_module(project: str, mid: str, dry_run: bool = False, session: AsyncSession = Depends(get_db_session)):
    p, svc = await _ctx(project, session)
    return await _g(svc.delete(p, mid, dry_run=dry_run))
