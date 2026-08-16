"""Cross-cutting HTTP middleware for production hardening.

- RequestIDMiddleware       — assign/propagate an X-Request-Id per request.
- SecurityHeadersMiddleware — defensive response headers.
- AuditMiddleware           — append-only audit trail for writes + data egress.
"""

from __future__ import annotations

import re
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.shared import audit

_MUTATING = {"POST", "PUT", "PATCH", "DELETE"}
_SKIP_AUDIT_PATHS = {"/health", "/openapi.json", "/docs", "/redoc"}
# GET egress of a derived artifact: /samples/{filename} (raw bytes) and
# /samples/{filename}/data (paged rows). These are real sensitive-data egress
# but don't end in /download, so they'd otherwise leave no audit trail.
_SAMPLE_EGRESS = re.compile(r"/samples/[^/]+(/data)?$")


class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-Id") or uuid.uuid4().hex
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-Id"] = request_id
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        headers = response.headers
        headers.setdefault("X-Content-Type-Options", "nosniff")
        headers.setdefault("X-Frame-Options", "DENY")
        headers.setdefault("Referrer-Policy", "no-referrer")
        headers.setdefault("Cache-Control", "no-store")
        headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
        )
        headers.setdefault("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
        return response


def _route_template(path: str, path_params: dict) -> str:
    """``/datasets/<uuid>/tags/production`` → ``/datasets/{dataset_id}/tags/{tag_name}``.

    Substituted segment-wise, never by substring: a one-character sheet name
    would otherwise rewrite every matching character of the path.
    """
    if not path_params:
        return path
    by_value = {str(v): k for k, v in path_params.items()}
    return "/".join(f"{{{by_value[seg]}}}" if seg in by_value else seg
                    for seg in path.split("/"))


class AuditMiddleware(BaseHTTPMiddleware):
    """Record writes and data egress to the append-only audit log.

    What is recorded is deliberately more than "method + path": the matched
    route's path params (available on the scope once routing has run) give the
    row an ``action`` that is stable across ids, a ``resource_type``/
    ``resource_id`` to filter on, and — via the resource's owning team — the
    ``team_id`` that makes ``audit.query(team_ids=...)`` able to return
    anything at all. While those columns were left NULL, team-scoped audit
    reads answered zero rows for every team, and callers that wanted a
    dataset's history had to LIKE-match the path text.
    """

    async def dispatch(self, request: Request, call_next):
        started = time.monotonic()
        response = await call_next(request)
        try:
            path = request.url.path
            is_download = path.endswith("/download") or (
                request.method == "GET" and _SAMPLE_EGRESS.search(path) is not None)
            if (request.method in _MUTATING or is_download) and not any(
                path.endswith(s) for s in _SKIP_AUDIT_PATHS
            ):
                principal = getattr(request.state, "principal", None)
                # Routing runs inside `call_next` and updates the same scope
                # dict, so the matched params are visible here (an unmatched
                # path — a 404 — simply has none).
                path_params = {k: v for k, v in
                               (request.scope.get("path_params") or {}).items()
                               if v is not None}
                team_id, resource_type, resource_id = await audit.resolve_target(path_params)
                await audit.record(
                    method=request.method,
                    path=path,
                    action=f"{request.method} {_route_template(path, path_params)}",
                    status_code=response.status_code,
                    actor_user_id=getattr(principal, "user_id", None),
                    actor_email=getattr(principal, "email", None),
                    team_id=team_id,
                    resource_type=resource_type,
                    resource_id=resource_id,
                    metadata={"path_params": {k: str(v) for k, v in path_params.items()}}
                    if path_params else None,
                    ip=request.client.host if request.client else None,
                    user_agent=request.headers.get("user-agent"),
                    request_id=getattr(request.state, "request_id", None),
                    duration_ms=int((time.monotonic() - started) * 1000),
                )
        except Exception:  # never let auditing break the response
            pass
        return response


def install_middleware(app) -> None:
    """Attach hardening middleware. Order: last-added runs outermost."""
    # Innermost first so it sees the final response/status.
    app.add_middleware(AuditMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RequestIDMiddleware)  # outermost: id available to all
