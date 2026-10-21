"""The project plan: one requirements conversation, a reviewed and versioned plan, and its approval.

    GET  /projects/{p}/plan                     the approved plan and the latest draft (each with validation issues)
    GET  /projects/{p}/plan/versions            every version, newest first (no bodies)
    GET  /projects/{p}/plan/{v}                 one version, with issues
    POST /projects/{p}/plan/draft               {prompt} round 1 (compact plan + requirements checklist) | {version, answers}
                                                next round | {version, accept_suggestions: true} take every suggested pick |
                                                {body} save as a draft; results carry ``timing`` (wall time, model calls)
                                                (SSE by default; JSON with ``Accept: application/json``)
    PUT  /projects/{p}/plan/{v}                 {body, base_updated_at}: save edits to a draft (409 plan_conflict if stale)
    POST /projects/{p}/plan/{v}/approve         {accept_breaking?}: approve a draft (409 open questions / breaking, 422 errors)
    POST /projects/{p}/plan/revise              {prompt?, body?}: a new draft from the approved plan, with its diff
    GET  /projects/{p}/plan/{v}/diff?against=   the diff between two versions (default: its base, else the approved one)
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.dependencies import get_db_session
from ..core.identity import get_actor
from ..services import plan_clarify as pc
from ..services import project_plan as pp
from ..services.project_service import ProjectError, ProjectService

router = APIRouter()

MAX_PLAN_REQUEST_BYTES = 2 * 1024 * 1024


def _err(status: int, code: str, message: str, **extra: Any) -> JSONResponse:
    return JSONResponse({"detail": {"code": code, "message": message, **extra}}, status_code=status)


def _perr(e: ProjectError) -> JSONResponse:
    return _err(e.status, e.code, e.message, **e.extra)


def _invalid(e: ValidationError) -> JSONResponse:
    return _err(422, "invalid_request", "invalid request",
                errors=[{"loc": list(x.get("loc", ())), "message": x.get("msg")} for x in e.errors()])


async def _json(request: Request) -> Any:
    raw = await request.body()
    if len(raw) > MAX_PLAN_REQUEST_BYTES:
        raise ProjectError(413, "payload_too_large", f"request is larger than {MAX_PLAN_REQUEST_BYTES} bytes")
    try:
        return json.loads(raw or b"null")
    except ValueError as e:
        raise ProjectError(422, "invalid_request", "body must be JSON") from e


class PlanEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    body: dict[str, Any]
    base_updated_at: str | None = None


class PlanApprove(BaseModel):
    model_config = ConfigDict(extra="forbid")
    accept_breaking: bool = False


class PlanRevise(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prompt: str | None = Field(default=None, max_length=pc.MAX_PROMPT_CHARS)
    body: dict[str, Any] | None = None
    sources: list[pc.PlanSource] | None = Field(default=None, max_length=10)


@router.get("/projects/{project}/plan")
async def get_plan(project: str, session: AsyncSession = Depends(get_db_session)):
    try:
        p = await ProjectService(session).require(project)
        facts = await pp.project_facts(session, p.id)
        rows = await pp._rows(session, p.id)
    except ProjectError as e:
        return _perr(e)
    approved = next((r for r in rows if r.status == "approved"), None)
    draft = next((r for r in rows if r.status == "draft"), None)
    return {"approved": pp.plan_to_dict(approved, pp.validate_plan(approved.body, facts)) if approved else None,
            "draft": pp.plan_to_dict(draft, pp.validate_plan(draft.body, facts)) if draft else None,
            "versions": [pp.version_summary(r) for r in rows]}


@router.get("/projects/{project}/plan/versions")
async def list_plan_versions(project: str, session: AsyncSession = Depends(get_db_session)):
    try:
        p = await ProjectService(session).require(project)
        return await pp.list_versions(session, p.id)
    except ProjectError as e:
        return _perr(e)


@router.get("/projects/{project}/plan/{version:int}")
async def get_plan_version(project: str, version: int, session: AsyncSession = Depends(get_db_session)):
    try:
        p = await ProjectService(session).require(project)
        row = await pp.get_version(session, p.id, version)
        return pp.plan_to_dict(row, pp.validate_plan(row.body, await pp.project_facts(session, p.id)))
    except ProjectError as e:
        return _perr(e)


@router.get("/projects/{project}/plan/{version:int}/diff")
async def diff_plan_version(project: str, version: int, against: int | None = Query(default=None),
                            session: AsyncSession = Depends(get_db_session)):
    try:
        p = await ProjectService(session).require(project)
        row = await pp.get_version(session, p.id, version)
        if against is not None:
            prev_body = (await pp.get_version(session, p.id, against)).body
        elif row.base_version is not None:
            against = row.base_version
            prev_body = (await pp.get_version(session, p.id, against)).body
        else:
            approved = await pp.load_approved(session, p.id)
            against = approved["version"] if approved and approved["version"] != version else None
            prev_body = approved["body"] if against is not None else None
    except ProjectError as e:
        return _perr(e)
    return {"version": version, "against": against, **pp.diff_plans(prev_body, row.body)}


@router.put("/projects/{project}/plan/{version:int}")
async def edit_plan(project: str, version: int, request: Request, session: AsyncSession = Depends(get_db_session)):
    try:
        p = await ProjectService(session).require(project)
        req = PlanEdit.model_validate(await _json(request))
        row = await pp.update_draft(session, p.id, version, body=req.body, base_updated_at=req.base_updated_at)
        return pp.plan_to_dict(row, pp.validate_plan(row.body, await pp.project_facts(session, p.id)))
    except ValidationError as e:
        return _invalid(e)
    except ProjectError as e:
        return _perr(e)


@router.post("/projects/{project}/plan/{version:int}/approve")
async def approve_plan(project: str, version: int, request: Request, session: AsyncSession = Depends(get_db_session)):
    try:
        p = await ProjectService(session).require(project)
        data = await _json(request)
        req = PlanApprove.model_validate(data if data is not None else {})
        return await pp.approve(session, p.id, version, accept_breaking=req.accept_breaking, actor=get_actor(request))
    except ValidationError as e:
        return _invalid(e)
    except ProjectError as e:
        return _perr(e)


def _wants_json(request: Request) -> bool:
    accept = request.headers.get("accept", "")
    return "application/json" in accept and "text/event-stream" not in accept


async def _converse(request: Request, project_id: str, conv: pc.PlanConversation, *, needs_model: bool):
    """Run one conversation step as SSE (``message`` events with a ``seq``, then ``done``) or, for JSON callers, as one
    response: the result, or the terminal error as its HTTP status."""
    from sse_starlette.sse import EventSourceResponse, ServerSentEvent
    from starlette.background import BackgroundTask

    from ..core.config import settings
    from ..engine import llm_provider

    if needs_model and not llm_provider.is_configured(settings.module_gen_model):
        return _err(503, "llm_unavailable", "No AI model is configured for planning")
    key = pc.try_acquire(project_id)
    if key is None:
        return _err(409, "plan_in_progress", "Another planning step is already running for this project")

    if _wants_json(request):
        last: dict[str, Any] | None = None
        try:
            async for ev in conv.events():
                last = ev
        finally:
            pc.release(key)
        if last is None or last["type"] not in ("result", "error"):
            return _err(499, "client_closed", "client disconnected")
        if last["type"] == "error":
            extra = {k: v for k, v in last.items() if k not in ("type", "code", "message", "status")}
            status = last.get("status") or {"llm_failed": 502, "timeout": 504}.get(last["code"], 500)
            return _err(status, last["code"], last["message"], **extra)
        return JSONResponse(json.loads(json.dumps({k: v for k, v in last.items() if k != "type"}, default=str)))

    async def stream():
        seq = 0
        try:
            async for ev in conv.events():
                seq += 1
                yield ServerSentEvent(data=json.dumps({**ev, "seq": seq}, default=str), event="message")
            yield ServerSentEvent(data="{}", event="done")
        finally:
            pc.release(key)

    return EventSourceResponse(stream(), background=BackgroundTask(pc.release, key))


@router.post("/projects/{project}/plan/draft")
async def draft_plan(project: str, request: Request, session: AsyncSession = Depends(get_db_session)):
    try:
        p = await ProjectService(session).require(project)
        req = pc.PlanDraftRequest.model_validate(await _json(request))
    except ValidationError as e:
        return _invalid(e)
    except ProjectError as e:
        return _perr(e)
    conv = pc.PlanConversation(p.id, req, get_actor(request), request.is_disconnected)
    return await _converse(request, p.id, conv, needs_model=req.body is None)


@router.post("/projects/{project}/plan/revise")
async def revise_plan(project: str, request: Request, session: AsyncSession = Depends(get_db_session)):
    try:
        p = await ProjectService(session).require(project)
        data = await _json(request)
        req = PlanRevise.model_validate(data if data is not None else {})
        if not (req.prompt or "").strip():      # an edited copy (or a plain copy) of the approved plan: no model
            out = await pp.revise(session, p.id, req.body, actor=get_actor(request))
            out["issues"] = pp.validate_plan(out["body"], await pp.project_facts(session, p.id))
            return out
    except ValidationError as e:
        return _invalid(e)
    except ProjectError as e:
        return _perr(e)
    conv = pc.PlanConversation(p.id, None, get_actor(request), request.is_disconnected, revise_prompt=req.prompt,
                               sources=req.sources)
    return await _converse(request, p.id, conv, needs_model=True)
