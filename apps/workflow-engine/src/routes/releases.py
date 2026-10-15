"""Releases (BACKEND-DESIGN §3.9, phase 9; G10–G14)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.dependencies import get_db_session
from ..core.identity import get_actor
from ..services.project_service import ProjectError, ProjectService
from ..services.release_service import ReleaseService, release_to_dict
from .projects import raise_project_error

router = APIRouter(prefix="/projects/{project}")


class ReleaseCreate(BaseModel):
    environment: str | None = None
    notes: str | None = None


class PromoteBody(BaseModel):
    to_environment: str


class RollbackBody(BaseModel):
    release_id: str | None = None


async def _ctx(project: str, session: AsyncSession):
    try:
        return await ProjectService(session).require(project), ReleaseService(session)
    except ProjectError as e:
        raise_project_error(e)


async def _g(coro):
    try:
        return await coro
    except ProjectError as e:
        raise_project_error(e)


@router.get("/releases")
async def list_releases(project: str, environment: str | None = None, session: AsyncSession = Depends(get_db_session)):
    p, svc = await _ctx(project, session)
    return await svc.list(p, environment)


@router.post("/releases", status_code=201)
async def create_release(project: str, body: ReleaseCreate, request: Request, dry_run: bool = False,
                         session: AsyncSession = Depends(get_db_session)):
    p, svc = await _ctx(project, session)
    out = await _g(svc.create(p, body.environment, body.notes, dry_run=dry_run, actor=get_actor(request)))
    if dry_run:
        from fastapi.responses import JSONResponse
        return JSONResponse(out, status_code=200)
    return out


@router.get("/releases/{rid}")
async def get_release(project: str, rid: str, session: AsyncSession = Depends(get_db_session)):
    p, svc = await _ctx(project, session)
    return release_to_dict(await _g(svc.get(p, rid)), with_snapshot=True, project=p)


@router.post("/releases/{rid}/promote", status_code=201)
async def promote_release(project: str, rid: str, body: PromoteBody, request: Request, dry_run: bool = False,
                          session: AsyncSession = Depends(get_db_session)):
    p, svc = await _ctx(project, session)
    out = await _g(svc.promote(p, rid, body.to_environment, dry_run=dry_run, actor=get_actor(request)))
    if dry_run:
        from fastapi.responses import JSONResponse
        return JSONResponse(out, status_code=200)
    return out


@router.post("/environments/{env}/rollback")
async def rollback(project: str, env: str, body: RollbackBody | None = None, session: AsyncSession = Depends(get_db_session)):
    p, svc = await _ctx(project, session)
    return await _g(svc.rollback(p, env, (body.release_id if body else None)))


@router.get("/releases/{rid}/diff")
async def release_diff(project: str, rid: str, against: str = "active", environment: str | None = None,
                       session: AsyncSession = Depends(get_db_session)):
    p, svc = await _ctx(project, session)
    return await _g(svc.diff(p, rid, against, environment))
