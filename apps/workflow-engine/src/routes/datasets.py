"""Project datasets: import (CSV, Parquet, XLSX, XLS) into the analytics service, list / preview / rename / delete
by the project's link row id. Profiling is the Profile node's job, not an endpoint here. See ``services.project_dataset_service``."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.dependencies import get_db_session
from ..core.identity import get_actor
from ..services.project_dataset_service import AnalyticsUnavailable, ProjectDatasetService
from ..services.project_service import ProjectError, ProjectService
from .projects import raise_project_error

router = APIRouter(prefix="/projects/{project}/datasets")


class RenameBody(BaseModel):
    name: str | None = None




def _raise(e: Exception) -> None:
    if isinstance(e, AnalyticsUnavailable):
        raise HTTPException(status_code=503, detail=str(e)) from e
    if isinstance(e, ProjectError):
        raise_project_error(e)
    raise e


async def _ctx(project: str, session: AsyncSession, did: str | None = None):
    try:
        p = await ProjectService(session).require(project)
        svc = ProjectDatasetService(session)
        d = await svc.get(p, did) if did else None
    except ProjectError as e:
        raise_project_error(e)
    return p, svc, d


async def _g(coro):
    try:
        return await coro
    except (ProjectError, AnalyticsUnavailable) as e:
        _raise(e)


@router.get("")
async def list_datasets(project: str, session: AsyncSession = Depends(get_db_session)):
    p, svc, _ = await _ctx(project, session)
    return await svc.list(p)


@router.post("", status_code=201)
async def import_dataset(project: str, request: Request, file: UploadFile = File(...), name: str | None = Form(None),
                         session: AsyncSession = Depends(get_db_session)):
    p, svc, _ = await _ctx(project, session)
    try:
        return await _g(svc.upload(p, file.filename or "", file.file, file.content_type, name=name, actor=get_actor(request)))
    finally:
        await file.close()


@router.get("/{did}")
async def get_dataset(project: str, did: str, session: AsyncSession = Depends(get_db_session)):
    p, svc, d = await _ctx(project, session, did)
    return await _g(svc.detail(d))


@router.patch("/{did}")
async def rename_dataset(project: str, did: str, body: RenameBody, session: AsyncSession = Depends(get_db_session)):
    p, svc, _ = await _ctx(project, session, did)
    return await _g(svc.rename(p, did, body.name))


@router.delete("/{did}", status_code=204)
async def delete_dataset(project: str, did: str, session: AsyncSession = Depends(get_db_session)):
    p, svc, _ = await _ctx(project, session, did)
    await _g(svc.delete(p, did))
    return Response(status_code=204)
