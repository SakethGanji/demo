"""Agent session + run routes.

Two prefixes live here because a run is addressed both through its session
(``POST /agent-sessions/{id}/runs``) and directly (``GET /agent-runs/{id}``).
They are combined into a single ``router`` for registration.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from ..core.exceptions import ValidationError
from ..schemas.agent import (
    RunEventItem,
    RunListItem,
    RunResponse,
    RunTriggerRequest,
    SessionResponse,
)
from ..services.agent_run_service import (
    AgentRunService,
    RunNotFoundError,
    SessionBusyError,
    SessionNotFoundError,
)
from ..services.agent_service import AgentNotFoundError
from .agents import get_agent_run_service

AgentRunServiceDep = Annotated[AgentRunService, Depends(get_agent_run_service)]

sessions_router = APIRouter(prefix="/agent-sessions")
runs_router = APIRouter(prefix="/agent-runs")


# --- Sessions --------------------------------------------------------------


@sessions_router.get("/{session_id}", response_model=SessionResponse)
async def get_session(session_id: str, service: AgentRunServiceDep) -> SessionResponse:
    """One session, including the agent config it pinned."""
    try:
        return await service.get_session(session_id)
    except SessionNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@sessions_router.post("/{session_id}/runs", response_model=RunResponse, status_code=202)
async def trigger_run(
    session_id: str,
    request: RunTriggerRequest,
    service: AgentRunServiceDep,
) -> RunResponse:
    """Queue a turn. Returns 202 with a queued run; poll or tail its events."""
    try:
        return await service.trigger(request, session_id=session_id)
    except SessionNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except SessionBusyError as e:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "session-busy",
                "message": str(e),
                "run_id": e.run_id,
            },
        )
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=e.message)


# --- Runs ------------------------------------------------------------------


@runs_router.get("", response_model=list[RunListItem])
async def list_runs(
    service: AgentRunServiceDep,
    agent_id: str | None = None,
    session_id: str | None = None,
    status: str | None = None,
    limit: int = Query(50, ge=1, le=500),
) -> list[RunListItem]:
    """List runs, newest first."""
    return await service.list_runs(
        agent_id=agent_id, session_id=session_id, status=status, limit=limit
    )


@runs_router.post("", response_model=RunResponse, status_code=202)
async def trigger_run_without_session(
    request: RunTriggerRequest,
    service: AgentRunServiceDep,
) -> RunResponse:
    """Queue a turn for an agent, creating a session of one."""
    try:
        return await service.trigger(request)
    except AgentNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.message)
    except SessionBusyError as e:
        raise HTTPException(
            status_code=409,
            detail={"error": "session-busy", "message": str(e), "run_id": e.run_id},
        )
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=e.message)


@runs_router.get("/{run_id}", response_model=RunResponse)
async def get_run(run_id: str, service: AgentRunServiceDep) -> RunResponse:
    """One run, with its terminal metrics once it finishes."""
    try:
        return await service.get_run(run_id)
    except RunNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@runs_router.get("/{run_id}/events", response_model=list[RunEventItem])
async def list_run_events(
    run_id: str,
    service: AgentRunServiceDep,
    after_seq: int = Query(0, ge=0),
    limit: int = Query(500, ge=1, le=2000),
) -> list[RunEventItem]:
    """Recorded ``agent:*`` events, ascending by ``seq``. Poll with ``after_seq``."""
    try:
        return await service.list_events(run_id, after_seq=after_seq, limit=limit)
    except RunNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@runs_router.post("/{run_id}/cancel", response_model=RunResponse)
async def cancel_run(run_id: str, service: AgentRunServiceDep) -> RunResponse:
    """Cancel a live run. Terminal runs are returned unchanged."""
    try:
        return await service.cancel(run_id)
    except RunNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


# Single router for registration.
router = APIRouter()
router.include_router(sessions_router)
router.include_router(runs_router)
