"""``MCPIdentityASGIMiddleware`` renders every auth rejection, not just FastAPI's.

The third instance of one bug. ``ProblemException`` subclasses **Starlette's**
``HTTPException``; ``fastapi.HTTPException`` is a *sibling* subclass of the same
base, not a parent. So ``except fastapi.HTTPException`` around a call that can
raise either matches only one of them. It was fixed in ``library/service.py``
(where it left analytics runs stuck in ``running``) and in
``files/services/replace.py``; this guard had the same shape.

Latent rather than live: ``get_principal`` currently raises only FastAPI's
class, and the mounted route sits inside the app's ``ExceptionMiddleware``,
whose handler for Starlette's ``HTTPException`` is found via the MRO — so an
escaping ``ProblemException`` was caught one layer out and rendered the same
way. That safety net is pinned in ``tests/test_mcp_endpoint.py``. These tests
drive the middleware with NO app around it, which is the only way to see what
the guard itself does — and the reason to fix it, since ``get_principal`` is
the designated seam for the SSO/JWT swap and a real token check raising a
problem+json 401 is exactly the case that would make this live.
"""

from __future__ import annotations

import json

import pytest
from fastapi import HTTPException

from app.api.errors import ProblemException
from app.features.auth.deps import Principal
from app.features.mcp import identity

PRINCIPAL = Principal(
    user_id="11111111-1111-1111-1111-111111111111", email="a@example.com",
    name="A", is_superuser=False, home_team_id=None, memberships={})


async def drive(monkeypatch, raises=None, *, headers=None):
    """Run one HTTP request through the middleware alone. Returns (status, body, reached)."""
    reached = []

    async def get_principal(request, x_user_id=None):
        if raises is not None:
            raise raises
        return PRINCIPAL

    async def downstream(scope, receive, send):
        reached.append(scope)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    monkeypatch.setattr(identity, "get_principal", get_principal)
    middleware = identity.MCPIdentityASGIMiddleware(downstream)

    scope = {"type": "http", "method": "POST", "path": "/api/v1/mcp",
             "query_string": b"", "state": {},
             "headers": [(k.lower().encode(), v.encode())
                         for k, v in (headers or {}).items()]}
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    await middleware(scope, receive, send)

    start = next(m for m in sent if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return start, body, reached


async def test_a_fastapi_http_exception_is_still_rendered(monkeypatch):
    """The case the narrower clause already handled — widening must not lose it.
    FastAPI's class is a subclass of Starlette's, so it takes the same branch."""
    start, body, reached = await drive(
        monkeypatch, HTTPException(401, "Missing X-User-Id header"))
    assert start["status"] == 401
    assert json.loads(body)["detail"] == "Missing X-User-Id header"
    assert not reached, "an unauthenticated request must not reach the MCP server"


async def test_a_problem_exception_is_caught_rather_than_escaping(monkeypatch):
    """Was not caught at all: it is not a ``fastapi.HTTPException``. With no app
    around the middleware there is nothing to catch it, so this raised."""
    start, body, reached = await drive(
        monkeypatch,
        ProblemException(401, "Token expired", code="token-expired",
                         expired_at="2026-08-06T00:00:00Z"))
    assert start["status"] == 401
    assert not reached


async def test_the_machine_readable_code_and_extras_survive(monkeypatch):
    """Rendering mirrors ``app.api.errors._http_exception_handler`` field for
    field. Catching the exception and then flattening it to a bare detail would
    trade one bug for a quieter one — the ``code`` is the part a client
    branches on, and it is the reason ``ProblemException`` exists."""
    _, body, _ = await drive(
        monkeypatch,
        ProblemException(403, "Team access revoked", code="team-revoked",
                         team_id="22222222-2222-2222-2222-222222222222"))
    payload = json.loads(body)
    assert payload["code"] == "team-revoked"
    assert payload["team_id"] == "22222222-2222-2222-2222-222222222222"
    assert payload["status"] == 403
    assert payload["instance"] == "/api/v1/mcp"


async def test_response_headers_survive_too(monkeypatch):
    """A 401 carrying ``WWW-Authenticate`` is precisely what the SSO/JWT swap
    would raise, and the old code dropped it — it passed only status and detail.

    Spelled with FastAPI's class because that is what ``get_principal`` raises
    today; the middleware renders exactly what ``_http_exception_handler``
    renders, deliberately, so either class behaves the same here.

    ``ProblemException`` used to be excluded from this test on purpose: its
    ``__init__`` dropped ``headers`` into ``**extra`` instead of forwarding it,
    which made ``headers=`` a *body* field and a 500. That gap is now closed —
    see ``tests/unit/test_problem_exception_headers.py`` — so the SSO/JWT swap
    can raise a problem+json 401 carrying ``WWW-Authenticate`` from either class.
    """
    start, _, _ = await drive(
        monkeypatch,
        HTTPException(401, "Bearer token required",
                      headers={"WWW-Authenticate": "Bearer"}))
    headers = {k.decode().lower(): v.decode() for k, v in start["headers"]}
    assert headers["www-authenticate"] == "Bearer"
    assert headers["content-type"].startswith("application/problem+json")


@pytest.mark.parametrize("exc", [
    RuntimeError("db is down"),
    ValueError("nonsense"),
])
async def test_a_non_http_failure_is_not_swallowed(monkeypatch, exc):
    """Widening to Starlette's base must not become ``except Exception``. An
    unexpected failure inside ``get_principal`` belongs to the app's 500
    handler, which logs it; rendering it here would hide it and risk leaking
    internals into a problem+json ``detail``."""
    with pytest.raises(type(exc)):
        await drive(monkeypatch, exc)


async def test_an_authenticated_request_reaches_the_server_with_its_identity(monkeypatch):
    _, body, reached = await drive(
        monkeypatch, headers={"X-User-Id": PRINCIPAL.user_id,
                              "X-Team-Id": "33333333-3333-3333-3333-333333333333"})
    assert body == b"ok"
    state = reached[0]["state"]
    assert state["mcp_identity"].principal is PRINCIPAL
    assert state["mcp_identity"].team_id == "33333333-3333-3333-3333-333333333333"
