"""``/api/_test/*`` — test-only hooks (UI-DESIGN §10.3).

Registered **only** when the fake LLM is installed. Every request needs the header
``X-Test-Token: <WORKFLOW_FAKE_LLM_TOKEN>`` and a loopback client; anything else is a plain 404
(indistinguishable from the routes not existing). ``LoopbackOnlyTestPaths`` (always on) also 404s
any non-loopback ``/api/_test/*`` request before routing.
"""

from __future__ import annotations

import hmac
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict

LOOPBACK_CLIENTS = {"127.0.0.1", "::1", "localhost"}
TEST_PREFIX = "/api/_test"


def _is_loopback(host: str | None) -> bool:
    if not host:
        return False
    if host in LOOPBACK_CLIENTS or host.startswith("127."):
        return True
    return host.startswith("::ffff:127.")


class LoopbackOnlyTestPaths:
    """ASGI middleware: ``/api/_test/*`` from a non-loopback client → 404 (re-review N7 enforcement)."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "") if scope["type"] == "http" else ""
        if path == TEST_PREFIX or path.startswith(TEST_PREFIX + "/"):
            client = scope.get("client")
            if not _is_loopback(client[0] if client else None):
                from starlette.responses import JSONResponse
                await JSONResponse({"detail": "Not Found"}, status_code=404)(scope, receive, send)
                return
        await self.app(scope, receive, send)


def _guard(request: Request) -> None:
    from ..engine import fake_llm

    expected = fake_llm.token()
    got = request.headers.get("x-test-token") or ""
    client = request.client.host if request.client else None
    if fake_llm.ACTIVE is None or not expected or not _is_loopback(client) \
            or not hmac.compare_digest(got.encode(), expected.encode()):
        raise HTTPException(status_code=404, detail="Not Found")


router = APIRouter(prefix=TEST_PREFIX, dependencies=[Depends(_guard)], include_in_schema=False)


class StateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    unavailable: bool


class SandboxBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    available: bool


@router.put("/fake-llm/script")
async def put_script(request: Request):
    from ..engine import fake_llm

    try:
        raw = await request.json()
    except ValueError:
        raise HTTPException(status_code=422, detail={"code": "invalid_script", "message": "body must be JSON"})
    try:
        script = fake_llm.parse_script(raw, fake_llm.ACTIVE.base_dir)
    except fake_llm.ScriptError as e:
        raise HTTPException(status_code=422, detail={"code": "invalid_script", "message": str(e)})
    return {"rules": fake_llm.ACTIVE.set_script(script)}


@router.get("/fake-llm/calls")
async def get_calls() -> list[dict[str, Any]]:
    from ..engine import fake_llm

    return list(fake_llm.ACTIVE.calls)


@router.delete("/fake-llm/calls")
async def clear_calls():
    from ..engine import fake_llm

    fake_llm.ACTIVE.clear_calls()
    return {"success": True}


@router.put("/fake-llm/state")
async def put_state(body: StateBody):
    from ..engine import fake_llm

    fake_llm.ACTIVE.unavailable = body.unavailable
    return {"unavailable": body.unavailable}


@router.put("/sandbox")
async def put_sandbox(body: SandboxBody):
    from ..sandbox import backend

    backend.TEST_OVERRIDE_AVAILABLE = None if body.available else False
    return {"available": backend.availability()[0]}


@router.get("/health")
async def health():
    from ..core.config import settings

    return {"fake_llm": True, "db": settings.db_name}
