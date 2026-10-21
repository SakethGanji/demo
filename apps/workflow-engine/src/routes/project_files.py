"""One project file tree (LIVE-FILES §1): plan, backend modules, tables, routes and app files as one tree per project.

    GET /projects/{p}/files                      {files: FileEntry[]}
    GET /projects/{p}/files/content?path=        {path, content, language, kind, writable, updated_at}
    PUT /projects/{p}/files/content              {path, content, base_updated_at?} -> {path, updated_at, result}
                                                 (409 read_only / conflict; a module save's own errors as the module save gives them)
    GET /projects/{p}/files/history?path=&limit= Change[] for one path, newest first
    GET /projects/{p}/changes?limit=&build_id=   Change[] for the project (its commit log), newest first
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.dependencies import get_db_session
from ..core.identity import get_actor
from ..services import project_files as pf
from ..services.project_service import ProjectError, ProjectService

router = APIRouter()


def _perr(e: ProjectError) -> JSONResponse:
    return JSONResponse({"detail": {"code": e.code, "message": e.message, **e.extra}}, status_code=e.status)


class FileWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str
    content: str
    base_updated_at: str | None = None


async def _body(request: Request) -> FileWrite:
    raw = await request.body()
    try:
        return FileWrite.model_validate(json.loads(raw) if raw.strip() else {})
    except ValueError as e:
        errors = e.errors() if isinstance(e, ValidationError) else [{"loc": (), "msg": "body must be JSON"}]
        raise ProjectError(422, "invalid_request", "invalid request",
                           errors=[{"loc": list(x.get("loc", ())), "message": x.get("msg")} for x in errors]) from e


@router.get("/projects/{project}/files")
async def list_files(project: str, session: AsyncSession = Depends(get_db_session)):
    try:
        p = await ProjectService(session).require(project)
        return {"files": await pf.list_files(session, p)}
    except ProjectError as e:
        return _perr(e)


@router.get("/projects/{project}/files/content")
async def file_content(project: str, path: str = "", session: AsyncSession = Depends(get_db_session)):
    try:
        p = await ProjectService(session).require(project)
        return await pf.read_file(session, p, path)
    except ProjectError as e:
        return _perr(e)


@router.put("/projects/{project}/files/content")
async def write_file(project: str, request: Request, session: AsyncSession = Depends(get_db_session)):
    try:
        req = await _body(request)
        p = await ProjectService(session).require(project)
        out: dict[str, Any] = await pf.write_file(session, p, req.path, req.content, base_updated_at=req.base_updated_at,
                                                  actor=get_actor(request))
        return JSONResponse(json.loads(json.dumps(out, default=str)))
    except ProjectError as e:
        return _perr(e)


@router.get("/projects/{project}/files/history")
async def file_history(project: str, path: str = "", limit: int = 50, session: AsyncSession = Depends(get_db_session)):
    try:
        p = await ProjectService(session).require(project)
        return await pf.history(session, p.id, path, limit)
    except ProjectError as e:
        return _perr(e)


@router.get("/projects/{project}/changes")
async def project_changes(project: str, limit: int = 100, build_id: str | None = None,
                          session: AsyncSession = Depends(get_db_session)):
    try:
        p = await ProjectService(session).require(project)
        return await pf.changes(session, p.id, limit, build_id)
    except ProjectError as e:
        return _perr(e)
