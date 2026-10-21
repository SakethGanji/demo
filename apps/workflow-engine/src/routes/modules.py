"""Python modules + sandbox status (BACKEND-DESIGN §3.8, phase 8)."""

from __future__ import annotations

import json
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
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
    # Tables the module declares ([{name, columns:[{name,type,required?,default?}]}]): missing ones are created on save.
    tables: list[dict[str, Any]] | None = None


class ModuleUpdate(BaseModel):
    source: str
    # Review B4: optimistic concurrency — the version the editor started from (either one; or If-Match).
    base_sha: str | None = None          # UI-DESIGN §9 B4 name
    base_source_sha: str | None = None   # alias
    base_updated_at: str | None = None
    name: str | None = None              # optional rename (UI-DESIGN §9 B4)
    tables: list[dict[str, Any]] | None = None


async def _ctx(project: str, session: AsyncSession):
    try:
        return await ProjectService(session).require(project), CodeModuleService(session)
    except ProjectError as e:
        raise_project_error(e)


async def _g(coro):
    from sqlalchemy.exc import IntegrityError
    try:
        return await coro
    except ProjectError as e:
        raise_project_error(e)
    except IntegrityError as e:  # a unique-index clash the planner did not foresee: a conflict, never a 500
        raise HTTPException(status_code=409, detail={
            "code": "route_conflict", "message": "a route or operation name in this module clashes with an existing one",
            "detail": str(getattr(e, "orig", e))[:300]}) from e


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


class CodeMapRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str
    module_id: str | None = None


@router.post("/projects/{project}/modules/code-map")
async def code_map(project: str, request: Request, session: AsyncSession = Depends(get_db_session)):
    """Analyze the exact editor draft, without persisting, importing or running it."""
    from starlette.concurrency import run_in_threadpool
    from ..services.code_map import analyze_code_map

    p, svc = await _ctx(project, session)
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > MAX_DRAFT_REQUEST_BYTES:
            return _err(413, "payload_too_large", "Code-map request is too large")
    try:
        body = CodeMapRequest.model_validate_json(bytes(raw))
    except ValidationError as e:
        return invalid_request(e)
    if len(body.source.encode("utf-8")) > MAX_SOURCE_BYTES:
        return _err(413, "payload_too_large", f"Source exceeds {MAX_SOURCE_BYTES} bytes")
    if body.module_id:
        await _g(svc.get(p, body.module_id))
    try:
        result = await run_in_threadpool(analyze_code_map, body.source)
    except RecursionError:
        return _err(422, "analysis_limit", "Source nesting exceeds the code-map analysis limit")
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


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


MAX_GENERATE_BODY_BYTES = 1024 * 1024


def _generate_error_response(ev: dict[str, Any], problems: list[dict[str, Any]]) -> JSONResponse:
    """JSON mode (§10.1): a terminal ``error`` event as an HTTP status."""
    code = ev["code"]
    if code == "generation_invalid":
        return _err(422, code, ev["message"], errors=problems, draft=ev.get("draft"), analysis=ev.get("analysis"))
    status = {"llm_failed": 502, "timeout": 504}.get(code, 500)
    return _err(status, code, ev["message"], **({"draft": ev["draft"]} if ev.get("draft") else {}))


@router.post("/projects/{project}/modules/generate")
async def generate_module(project: str, request: Request, session: AsyncSession = Depends(get_db_session)):
    """UI-DESIGN §10.1: prompt (+ current source) → a validated module draft. Never writes modules, routes,
    tables or secrets (the only rows are the self-test executions)."""
    from sse_starlette.sse import EventSourceResponse, ServerSentEvent
    from starlette.background import BackgroundTask

    from ..core.config import settings
    from ..db.models import CodeModuleModel
    from ..engine import llm_provider
    from ..services import module_generator as mg
    from ..services.draft_runner import check_module_name

    try:
        p = await ProjectService(session).require(project)
    except ProjectError as e:
        return _err(e.status, e.code, e.message, **e.extra)
    raw = await request.body()
    if len(raw) > MAX_GENERATE_BODY_BYTES:
        return _err(422, "invalid_request", f"request is larger than {MAX_GENERATE_BODY_BYTES} bytes")
    try:
        data = json.loads(raw or b"null")
    except ValueError:
        return _err(422, "invalid_request", "body must be JSON")
    try:
        body = mg.GenerateRequest.model_validate(data)
    except ValidationError as e:
        return invalid_request(e)
    if body.module_name is not None:
        try:
            check_module_name(body.module_name)
        except ProjectError as e:
            return _err(e.status, e.code, e.message, **e.extra)
    if body.module_id is not None:
        saved = await session.get(CodeModuleModel, body.module_id)
        if saved is None or saved.project_id != p.id:
            return _err(404, "module_not_found", "Module not found")
    if not llm_provider.is_configured(settings.module_gen_model):
        return _err(503, "llm_unavailable", "No AI model is configured for module generation")
    key = mg.try_acquire(p.id, get_actor(request))
    if key is None:
        return _err(409, "generation_in_progress", "Another generation is already running for this project")

    gen = mg.Generation(p.id, body, request.is_disconnected)
    accept = request.headers.get("accept", "")
    if "application/json" in accept and "text/event-stream" not in accept:
        try:
            last: dict[str, Any] | None = None
            async for ev in gen.events():
                last = ev
        except ProjectError as e:  # a project/module error escaping the generator is the caller's 4xx, not a 500
            return _err(e.status, e.code, e.message, **e.extra)
        finally:
            mg.release(key)
        if last is None or last["type"] not in ("result", "error"):   # client went away mid-generation
            return _err(499, "client_closed", "client disconnected")
        if last["type"] == "error":
            return _generate_error_response(last, gen.last_problems)
        return JSONResponse({k: v for k, v in last.items() if k != "type"})

    async def stream():
        seq = 0
        try:
            try:
                async for ev in gen.events():
                    seq += 1
                    yield ServerSentEvent(data=json.dumps({**ev, "seq": seq}, default=str), event="message")
            except ProjectError as e:
                seq += 1
                yield ServerSentEvent(data=json.dumps({"type": "error", "code": e.code, "message": e.message,
                                                       "seq": seq}), event="message")
            yield ServerSentEvent(data="{}", event="done")
        finally:
            mg.release(key)

    return EventSourceResponse(stream(), background=BackgroundTask(mg.release, key))


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
    out = await _g(svc.create(p, body.name, body.source, actor=get_actor(request), dry_run=dry_run, tables=body.tables))
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
                               base_updated_at=body.base_updated_at, new_name=body.name, tables=body.tables))


@router.delete("/projects/{project}/modules/{mid}")
async def delete_module(project: str, mid: str, dry_run: bool = False, session: AsyncSession = Depends(get_db_session)):
    p, svc = await _ctx(project, session)
    return await _g(svc.delete(p, mid, dry_run=dry_run))
