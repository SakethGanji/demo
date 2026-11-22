"""G10 · Vela agent routes: the tool registry, the deciders, and conversations (start, follow, confirm, stop)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ..authz import public, require  # P19
from ..authz.deps import principal
from ..authz.identity import Principal
from ..catalog import governance as G
from ..catalog.schema import DEFAULT_TEAM, audit
from . import loop
from .tools import TOOLS

router = APIRouter(prefix="/api/agent")


class Start(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)
    user: str | None = Field(None, max_length=80, description="Ignored for identity (F-X4b): the agent acts for the request's "
                                                              "principal. If sent, it must name that principal (409 otherwise).")
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


# F-X4b (X4 C1): a conversation belongs to the principal who started it. The agent acts on that principal's behalf
# (agent/tools.principal_headers), so the session routes are bound to it in every authz mode: only the owner reads,
# continues, confirms or stops it; an admin may read any conversation but not act in one.

def _caller(request: Request) -> Principal:
    p = principal(request)
    if p is None:
        raise HTTPException(401, {"code": "unauthenticated", "message": "Sign in (X-Ensemble-Actor) to use the agent: it acts "
                                                                       "on your behalf, with your rights."})
    if p.auth == "agent":
        raise HTTPException(403, {"code": "forbidden", "message": "The agent cannot start or drive agent conversations."})
    return p


def _refuse(request: Request, p: Principal, sid: str, s: dict[str, Any], what: str) -> None:
    with request.app.state.db.connect() as con:
        audit(con, p.display_name, "agent_session_refused", {"session": sid, "what": what, "owner_id": s.get("owner_id")},
              team_id=DEFAULT_TEAM, object_kind="agent_session", object_id=sid, principal_id=p.id, auth=p.auth)
    raise HTTPException(403, {"code": "not_session_owner", "message": f"Agent conversation {sid} belongs to someone else"
                              + ("; an admin may read it but not act in it." if p.is_admin else "."), "action": what})


def _owned(request: Request, sid: str, act: bool, what: str) -> tuple[dict[str, Any], Principal]:
    s = _s(request, sid)
    p = _caller(request)
    if s.get("owner_id") and s["owner_id"] == p.id:
        return s, p
    if not act and p.is_admin:
        return s, p
    _refuse(request, p, sid, s, what)
    raise AssertionError  # unreachable


def _audit_session(request: Request, p: Principal, action: str, s: dict[str, Any], **details: Any) -> None:
    """agent_session_started / _message / _confirmed / _declined / _stopped: who drove the conversation (the real
    principal, not the agent). A confirmed step names its object, so the row lands in that object's team chain."""
    with request.app.state.db.connect() as con:
        audit(con, p.display_name, action, {"session": s["id"], **details}, principal_id=p.id, auth=p.auth,
              **({} if details.get("run_id") or details.get("dataset_id") or details.get("version_id")
                 else {"team_id": DEFAULT_TEAM, "object_kind": "agent_session", "object_id": s["id"]}))


@router.get("/tools", dependencies=[public("the agent's tool list; no data")])
def tools() -> dict[str, Any]:
    """Every tool the agent can call: name, description, read/write, JSON schema. Approve and promote are not tools."""
    return {"tools": [t.public() for t in TOOLS], "never": ["approve or reject a version (four-eyes)", "promote to Production"]}


@router.get("/status", dependencies=[public("the agent's deciders and budget; no data")])
def status() -> dict[str, Any]:
    d = loop.deciders()
    return {**d, "default": loop.pick_decider(None), "budget": {"max_steps": loop.MAX_STEPS, "max_seconds": loop.MAX_SECONDS},
            "tools": len(TOOLS), "write_tools": [t.name for t in TOOLS if t.kind == "write"]}


@router.get("/sessions", dependencies=[require("agent.use")])
def sessions(request: Request) -> dict[str, Any]:
    """The caller's own conversations (an admin sees everyone's)."""
    p = _caller(request)
    return {"sessions": _m(request).list(None if p.is_admin else p.id)}


@router.post("/sessions", status_code=201, dependencies=[require("agent.use")])
async def start(body: Start, request: Request) -> dict[str, Any]:
    p = _caller(request)
    if body.user and body.user.strip() and not G.same_person(body.user, p.display_name):
        raise HTTPException(409, f"You are signed in as {p.display_name}; 'user' says {body.user}. The agent acts only on "
                                 "behalf of the person who starts it.")
    s = _m(request).create(body.message, p.display_name, body.decider, body.max_steps, body.max_seconds, owner_id=p.id)
    _audit_session(request, p, "agent_session_started", s, decider=s.get("decider"), message=body.message[:300])
    return s


@router.get("/sessions/{sid}", dependencies=[require("agent.use")])
def get(sid: str, request: Request, since: int | None = None) -> dict[str, Any]:
    s, _p = _owned(request, sid, act=False, what="read")
    if since is not None and s.get("rev", 0) <= since:
        return {"id": sid, "rev": s.get("rev", 0), "unchanged": True}
    return s


@router.post("/sessions/{sid}/messages", dependencies=[require("agent.use")])
async def say(sid: str, body: Say, request: Request) -> dict[str, Any]:
    s, p = _owned(request, sid, act=True, what="message")
    try:
        out = _m(request).say(sid, body.message)
    except RuntimeError as e:
        raise HTTPException(409, str(e)) from e
    _audit_session(request, p, "agent_session_message", s, message=body.message[:300])
    return out


@router.post("/sessions/{sid}/confirm", dependencies=[require("agent.use")])
async def confirm(sid: str, body: Confirm, request: Request) -> dict[str, Any]:
    s, p = _owned(request, sid, act=True, what="confirm")
    step = next((it for it in s.get("timeline", []) if it.get("type") == "step" and it.get("id") == body.step_id), None) or {}
    try:
        out = _m(request).confirm(sid, body.step_id, body.approve)
    except (ValueError, RuntimeError) as e:
        raise HTTPException(409, str(e)) from e
    args = step.get("args") if isinstance(step.get("args"), dict) else {}
    _audit_session(request, p, "agent_step_confirmed" if body.approve else "agent_step_declined", s, step_id=body.step_id,
                   tool=step.get("tool"), **{k: args[k] for k in ("run_id", "dataset_id", "version_id") if isinstance(args.get(k), str)})
    return out


@router.post("/sessions/{sid}/stop", dependencies=[require("agent.use")])
async def stop(sid: str, request: Request) -> dict[str, Any]:
    s, p = _owned(request, sid, act=True, what="stop")
    out = _m(request).stop(sid)
    _audit_session(request, p, "agent_session_stopped", s)
    return out
