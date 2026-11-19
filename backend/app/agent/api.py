"""G10 · Vela agent routes: the tool registry, the deciders, and conversations (start, follow, confirm, stop)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from . import loop
from .tools import TOOLS

router = APIRouter(prefix="/api/agent")


class Start(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)
    user: str = Field("analyst", max_length=40, description="Who the agent acts for; the audit log says 'Vela agent on behalf of <user>'.")
    decider: str | None = Field(None, description="auto (default): Anthropic SDK if ANTHROPIC_API_KEY, else the claude CLI, else none; "
                                                  "or anthropic-sdk | claude-cli | stub | none.")
    max_steps: int | None = Field(None, ge=1, le=60)
    max_seconds: float | None = Field(None, ge=10, le=7200)


class Say(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)


class Confirm(BaseModel):
    step_id: str
    approve: bool


def _m(request: Request) -> loop.AgentManager:
    return request.app.state.agent


def _s(request: Request, sid: str) -> dict[str, Any]:
    s = _m(request).get(sid)
    if s is None:
        raise HTTPException(404, f"No agent conversation {sid}.")
    return s


@router.get("/tools")
def tools() -> dict[str, Any]:
    """Every tool the agent can call: name, description, read/write, JSON schema. Approve and promote are not tools."""
    return {"tools": [t.public() for t in TOOLS], "never": ["approve or reject a version (four-eyes)", "promote to Production"]}


@router.get("/status")
def status() -> dict[str, Any]:
    d = loop.deciders()
    return {**d, "default": loop.pick_decider(None), "budget": {"max_steps": loop.MAX_STEPS, "max_seconds": loop.MAX_SECONDS},
            "tools": len(TOOLS), "write_tools": [t.name for t in TOOLS if t.kind == "write"]}


@router.get("/sessions")
def sessions(request: Request) -> dict[str, Any]:
    return {"sessions": _m(request).list()}


@router.post("/sessions", status_code=201)
async def start(body: Start, request: Request) -> dict[str, Any]:
    return _m(request).create(body.message, body.user.strip() or "analyst", body.decider, body.max_steps, body.max_seconds)


@router.get("/sessions/{sid}")
def get(sid: str, request: Request, since: int | None = None) -> dict[str, Any]:
    s = _s(request, sid)
    if since is not None and s.get("rev", 0) <= since:
        return {"id": sid, "rev": s.get("rev", 0), "unchanged": True}
    return s


@router.post("/sessions/{sid}/messages")
async def say(sid: str, body: Say, request: Request) -> dict[str, Any]:
    _s(request, sid)
    try:
        return _m(request).say(sid, body.message)
    except RuntimeError as e:
        raise HTTPException(409, str(e)) from e


@router.post("/sessions/{sid}/confirm")
async def confirm(sid: str, body: Confirm, request: Request) -> dict[str, Any]:
    _s(request, sid)
    try:
        return _m(request).confirm(sid, body.step_id, body.approve)
    except (ValueError, RuntimeError) as e:
        raise HTTPException(409, str(e)) from e


@router.post("/sessions/{sid}/stop")
async def stop(sid: str, request: Request) -> dict[str, Any]:
    _s(request, sid)
    return _m(request).stop(sid)
