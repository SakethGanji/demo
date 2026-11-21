"""P19: the authorisation middleware. It only establishes the request context (who is asking is resolved lazily by
the first `require()`, so a GET with no check never touches the database for identity) and writes the response
headers: `X-Request-Id` always, `X-Ensemble-Authz: would-deny <action>[, …]` in `audit` mode.

A plain ASGI middleware (not BaseHTTPMiddleware): streamed downloads pass through untouched.
"""

from __future__ import annotations

import secrets
from typing import Any

from .context import CTX, RequestCtx


class AuthzMiddleware:
    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers") or []}
        rid = (headers.get("x-request-id") or "")[:64] or "rq_" + secrets.token_hex(6)
        ctx = RequestCtx(request_id=rid, headers=headers, client_host=(scope.get("client") or (None,))[0])
        token = CTX.set(ctx)

        async def send_wrapper(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                extra = [(b"x-request-id", rid.encode())]
                deny = [d["action"] for d in ctx.decisions if not d.get("allow") and d.get("mode") == "audit"]
                if deny:
                    extra.append((b"x-ensemble-authz", ("would-deny " + ", ".join(dict.fromkeys(deny))).encode()))
                message = {**message, "headers": [*message.get("headers", []), *extra]}
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            CTX.reset(token)
