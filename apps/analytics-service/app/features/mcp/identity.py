"""Request-scoped identity for the mounted MCP endpoint.

The standalone `analytics-mcp` server baked ``X-User-Id`` into one long-lived
``httpx.AsyncClient`` at construction. One process per user made that correct.
Mounted inside a multi-tenant service it would be an identity-laundering hole:
whichever user's id was baked in at startup would serve every caller.

So identity here is resolved **per HTTP request**, by the service's own
:func:`app.features.auth.deps.get_principal` — the same dependency every REST
route uses. There is no MCP-specific identity path, no env var and no header the
endpoint trusts without checking it names an active user.

Two layers, because MCP has two nested request notions:

``MCPIdentityASGIMiddleware``
    Wraps the mounted ASGI app. Runs ``get_principal`` once per HTTP request and
    stashes the result on ``request.state`` (which the audit middleware reads to
    attribute the action) — exactly what a normal route dependency does.

``identity_middleware``
    An MCP ``ServerMiddleware``, so it runs once per inbound JSON-RPC *message*,
    inside the task that will run the tool. It reads the principal off the
    Starlette request the transport attached to that message and publishes it on
    a ContextVar for the tool closures.

The second layer is what makes this safe regardless of how the transport
schedules work. The streamable-HTTP session manager can run a stateful session's
handler inside a task spawned by whichever request opened the session; a
ContextVar set only in the ASGI layer would then be the *session creator's*
identity for every later caller. Binding per message, from data carried on the
message, cannot drift.
"""

from __future__ import annotations

import logging
from contextvars import ContextVar
from dataclasses import dataclass

from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request
from starlette.types import ASGIApp, Receive, Scope, Send

from app.api.errors import problem_response
from app.features.auth.deps import Principal, get_principal

logger = logging.getLogger(__name__)

STATE_ATTR = "principal"
TEAM_STATE_ATTR = "mcp_team_id"


@dataclass(frozen=True, slots=True)
class Identity:
    """Who a single MCP message acts as."""

    principal: Principal
    team_id: str | None
    """From ``X-Team-Id``. Only needed by users in several teams with no home
    team; three write-side routes 400 without it."""

    @property
    def user_id(self) -> str:
        return self.principal.user_id

    def headers(self) -> dict[str, str]:
        headers = {"X-User-Id": self.user_id, "Accept": "application/json"}
        if self.team_id:
            headers["X-Team-Id"] = self.team_id
        return headers


_current: ContextVar[Identity | None] = ContextVar("mcp_identity", default=None)


class NoIdentityError(RuntimeError):
    """A tool ran outside a request that carried an authenticated caller.

    Never expected: the endpoint refuses unauthenticated requests before the MCP
    server sees them. Raised rather than defaulted, because every silent default
    here is somebody else's data.
    """


def current_identity() -> Identity:
    identity = _current.get()
    if identity is None:
        raise NoIdentityError(
            "No authenticated caller is bound to this MCP message. The MCP endpoint "
            "resolves identity per request; a tool must not run outside one."
        )
    return identity


def bind(identity: Identity):
    """Bind *identity* to the current context; returns the reset token."""
    return _current.set(identity)


def reset(token) -> None:
    _current.reset(token)


class MCPIdentityASGIMiddleware:
    """Authenticate an MCP HTTP request with the service's own dependency."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive)
        try:
            # Same call every REST route makes. It validates that the header
            # names an active user, loads real memberships, and stashes the
            # principal on request.state for the audit middleware.
            principal = await get_principal(request, x_user_id=request.headers.get("X-User-Id"))
        except StarletteHTTPException as exc:
            # Starlette's, not FastAPI's. ``ProblemException`` subclasses
            # Starlette's, so ``except fastapi.HTTPException`` did not match it;
            # FastAPI's own is a subclass of Starlette's, so this still catches
            # everything the narrower clause did. Today ``get_principal`` raises
            # only FastAPI's and the difference is unreachable — but that
            # function is the designated seam for the SSO/JWT swap, and a
            # problem+json 401 is exactly what a real token check would raise.
            # This is the third instance of this bug in the repo (see
            # ``library/service.py`` and ``files/services/replace.py``).
            #
            # Rendering mirrors ``app.api.errors._http_exception_handler`` field
            # for field, so a ProblemException's machine-readable ``code`` and
            # extra fields survive instead of being flattened to a bare detail.
            response = problem_response(
                exc.status_code, str(exc.detail), request.url.path,
                code=getattr(exc, "code", None),
                headers=getattr(exc, "headers", None),
                **getattr(exc, "extra", {}),
            )
            await response(scope, receive, send)
            return

        identity = Identity(principal=principal, team_id=request.headers.get("X-Team-Id"))
        # Not a ContextVar here: the per-message middleware owns that, because
        # the transport may run handlers in a task this one did not spawn.
        # Stashed on the shared ASGI scope state, which every per-message
        # Starlette Request built from this scope can see.
        request.state.mcp_identity = identity
        setattr(request.state, TEAM_STATE_ATTR, identity.team_id)

        await self.app(scope, receive, send)


async def identity_middleware(ctx, call_next):
    """MCP ``ServerMiddleware``: bind the message's caller for the handler.

    ``ctx.request`` is the Starlette request the streamable-HTTP transport
    attached to *this* JSON-RPC message, so the identity cannot be inherited
    from an earlier request that happened to open the session.
    """
    request = getattr(ctx, "request", None)
    identity = getattr(getattr(request, "state", None), "mcp_identity", None)
    if identity is None:
        # Reaching a handler with no authenticated caller means the ASGI guard
        # was bypassed. Leave the ContextVar unset so any tool that needs a
        # caller raises NoIdentityError, rather than inheriting somebody else's.
        return await call_next(ctx)
    token = bind(identity)
    try:
        return await call_next(ctx)
    finally:
        reset(token)
