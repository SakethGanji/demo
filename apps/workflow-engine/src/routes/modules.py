"""Python modules + sandbox status (BACKEND-DESIGN §3.8, phase 8)."""

from __future__ import annotations

import json
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
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
    base_sha: str | None = None          # UI-DESIGN §9 B4 name
    base_source_sha: str | None = None   # alias
    base_updated_at: str | None = None
    name: str | None = None              # optional rename (UI-DESIGN §9 B4)


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


MAX_SOURCE_BYTES = 200_000
MAX_DRAFT_BODY_BYTES = 1024 * 1024
MAX_DRAFT_REQUEST_BYTES = 2 * 1024 * 1024


class RunDraftRequest(BaseModel):
    """UI-DESIGN §10.2 (unknown fields rejected)."""
    model_config = ConfigDict(extra="forbid")

    source: str
    module_name: str
    module_id: str | None = None
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE", "get", "post", "put", "patch", "delete"]
    path: str = Field(min_length=1, max_length=2048)
    query: dict[str, Any] = {}
    headers: dict[str, str] = {}
    body: Any = None
    virtual_tables: list[str] = []

    @field_validator("source")
    @classmethod
    def _source_size(cls, v: str) -> str:
        if len(v.encode("utf-8")) > MAX_SOURCE_BYTES:
            raise ValueError(f"source is larger than {MAX_SOURCE_BYTES} bytes")
        return v


def _err(status: int, code: str, message: str, **extra: Any) -> JSONResponse:
    return JSONResponse({"detail": {"code": code, "message": message, **extra}}, status_code=status)


def invalid_request(e: ValidationError) -> JSONResponse:
    return _err(422, "invalid_request", "invalid request",
                errors=[{"loc": list(x.get("loc", ())), "message": x.get("msg")} for x in e.errors()])


@router.post("/projects/{project}/modules/run-draft")
async def run_draft(project: str, request: Request, session: AsyncSession = Depends(get_db_session)):
    """UI-DESIGN §10.2: run unsaved module source for one request, in the sandbox, in default_environment."""
    from ..services.draft_runner import run_draft as _run

    try:
        p = await ProjectService(session).require(project)
    except ProjectError as e:
        return _err(e.status, e.code, e.message, **e.extra)
    raw = await request.body()
    if len(raw) > MAX_DRAFT_REQUEST_BYTES:
        return _err(413, "payload_too_large", f"request is larger than {MAX_DRAFT_REQUEST_BYTES} bytes")
    try:
        data = json.loads(raw or b"null")
    except ValueError:
        return _err(422, "invalid_request", "body must be JSON")
    if isinstance(data, dict) and "body" in data and \
            len(json.dumps(data["body"], default=str).encode()) > MAX_DRAFT_BODY_BYTES:
        return _err(413, "payload_too_large", f"body is larger than {MAX_DRAFT_BODY_BYTES} bytes serialized")
    try:
        body = RunDraftRequest.model_validate(data)
    except ValidationError as e:
        return invalid_request(e)
    try:
        return await _run(session, p, source=body.source, module_name=body.module_name, module_id=body.module_id,
                          method=body.method.upper(), path=body.path, query=body.query, headers=body.headers,
                          body=body.body, virtual_tables=body.virtual_tables)
    except ProjectError as e:
        return _err(e.status, e.code, e.message, **e.extra)


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
                               base_source_sha=body.base_sha or body.base_source_sha or if_match,
                               base_updated_at=body.base_updated_at, new_name=body.name))


@router.delete("/projects/{project}/modules/{mid}")
async def delete_module(project: str, mid: str, dry_run: bool = False, session: AsyncSession = Depends(get_db_session)):
    p, svc = await _ctx(project, session)
    return await _g(svc.delete(p, mid, dry_run=dry_run))
