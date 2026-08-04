"""Cross-cutting HTTP middleware for production hardening.

- RequestIDMiddleware       — assign/propagate an X-Request-Id per request.
- SecurityHeadersMiddleware — defensive response headers.
- AuditMiddleware           — append-only audit trail for writes + data egress.
"""

from __future__ import annotations

import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.shared import audit

_MUTATING = {"POST", "PUT", "PATCH", "DELETE"}
_SKIP_AUDIT_PATHS = {"/health", "/openapi.json", "/docs", "/redoc"}


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


class AuditMiddleware(BaseHTTPMiddleware):
    """Record writes and data egress to the append-only audit log."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        try:
            path = request.url.path
            is_download = path.endswith("/download")
            if (request.method in _MUTATING or is_download) and not any(
                path.endswith(s) for s in _SKIP_AUDIT_PATHS
            ):
                principal = getattr(request.state, "principal", None)
                await audit.record(
                    method=request.method,
                    path=path,
                    status_code=response.status_code,
                    actor_user_id=getattr(principal, "user_id", None),
                    actor_email=getattr(principal, "email", None),
                    ip=request.client.host if request.client else None,
                    user_agent=request.headers.get("user-agent"),
                    request_id=getattr(request.state, "request_id", None),
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
