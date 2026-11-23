"""Builds: build a project from its approved plan, one builder at a time (ORCHESTRATOR.md).

    POST /projects/{p}/builds                    {app_key?, rebuild?} -> 202 Build (409 build_in_progress / no_approved_plan)
    GET  /projects/{p}/builds                    Build[], newest first (steps without their log)
    GET  /projects/{p}/builds/{id}               one Build (steps with their last log lines)
    GET  /projects/{p}/builds/{id}/events        SSE: snapshot, then step / log, then done (and the stream ends)
    POST /projects/{p}/builds/{id}/cancel        -> Build
    POST /projects/{p}/builds/{id}/continue      {skip?} -> Build; resumes a build waiting for input (409 build_not_waiting)
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.dependencies import get_db_session
from ..core.identity import get_actor
from ..services import build_coordinator as bc
from ..services.project_service import ProjectError, ProjectService

router = APIRouter()


def _err(status: int, code: str, message: str, **extra: Any) -> JSONResponse:
    return JSONResponse({"detail": {"code": code, "message": message, **extra}}, status_code=status)


def _perr(e: ProjectError) -> JSONResponse:
    return _err(e.status, e.code, e.message, **e.extra)


class BuildStart(BaseModel):
    model_config = ConfigDict(extra="forbid")
    app_key: str | None = None
    rebuild: bool = False


class BuildContinue(BaseModel):
    model_config = ConfigDict(extra="forbid")
    skip: bool = False


async def _body(request: Request, model: type[BaseModel]) -> Any:
    raw = await request.body()
    try:
        return model.model_validate(json.loads(raw) if raw.strip() else {})
    except ValueError as e:
        errors = e.errors() if isinstance(e, ValidationError) else [{"loc": (), "msg": "body must be JSON"}]
        raise ProjectError(422, "invalid_request", "invalid request",
                           errors=[{"loc": list(x.get("loc", ())), "message": x.get("msg")} for x in errors]) from e


@router.post("/projects/{project}/builds", status_code=202)
async def start_build(project: str, request: Request, session: AsyncSession = Depends(get_db_session)):
    try:
        req = await _body(request, BuildStart)
        p = await ProjectService(session).require(project)
        return JSONResponse(await bc.start_build(session, p.id, app_key=req.app_key, rebuild=req.rebuild,
                                                 actor=get_actor(request)), status_code=202)
    except ProjectError as e:
        return _perr(e)


@router.get("/projects/{project}/builds")
async def list_builds(project: str, session: AsyncSession = Depends(get_db_session)):
    try:
        p = await ProjectService(session).require(project)
        return await bc.list_builds(session, p.id)
    except ProjectError as e:
        return _perr(e)


@router.get("/projects/{project}/builds/{build_id}")
async def get_build(project: str, build_id: str, session: AsyncSession = Depends(get_db_session)):
    try:
        p = await ProjectService(session).require(project)
        return bc.public_build(await bc.get_row(session, p.id, build_id))
    except ProjectError as e:
        return _perr(e)


@router.post("/projects/{project}/builds/{build_id}/cancel")
async def cancel_build(project: str, build_id: str, session: AsyncSession = Depends(get_db_session)):
    try:
        p = await ProjectService(session).require(project)
        return await bc.cancel_build(session, p.id, build_id)
    except ProjectError as e:
        return _perr(e)


@router.post("/projects/{project}/builds/{build_id}/continue")
async def continue_build(project: str, build_id: str, request: Request, session: AsyncSession = Depends(get_db_session)):
    try:
        req = await _body(request, BuildContinue)
        p = await ProjectService(session).require(project)
        return await bc.continue_build(session, p.id, build_id, skip=req.skip)
    except ProjectError as e:
        return _perr(e)


@router.get("/projects/{project}/builds/{build_id}/events")
async def build_events(project: str, build_id: str, request: Request, session: AsyncSession = Depends(get_db_session)):
    from sse_starlette.sse import EventSourceResponse, ServerSentEvent

    try:
        p = await ProjectService(session).require(project)
        await bc.get_row(session, p.id, build_id)
    except ProjectError as e:
        return _perr(e)
    pid = p.id
    q = bc.subscribe(build_id)          # before the snapshot, so nothing between the two is lost

    async def current() -> dict[str, Any]:
        from ..db import session as dbs

        async with dbs.async_session_factory() as s:
            return bc.public_build(await bc.get_row(s, pid, build_id))

    def msg(payload: dict[str, Any]) -> ServerSentEvent:
        return ServerSentEvent(data=json.dumps(payload, default=str), event="message")

    async def stream():
        try:
            snap = await current()
            yield msg({"type": "snapshot", "build": snap})
            if snap["status"] in bc.TERMINAL:
                yield msg({"type": "done", "build": snap})
                return
            while True:
                if await request.is_disconnected():
                    return
                try:
                    ev = await asyncio.wait_for(q.get(), bc.SSE_POLL_S)
                except asyncio.TimeoutError:
                    # no event for a while: the run may have ended in another process (or been swept); re-read the row
                    snap = await current()
                    if snap["status"] in bc.TERMINAL:
                        yield msg({"type": "done", "build": snap})
                        return
                    continue
                yield msg(ev)
                if ev["type"] == "done":
                    return
        finally:
            bc.unsubscribe(build_id, q)

    return EventSourceResponse(stream(), ping=15)
