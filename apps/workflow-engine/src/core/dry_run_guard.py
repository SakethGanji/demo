"""Reject ``?dry_run=true`` on mutating endpoints that do not implement it (review D7).

A write endpoint without a ``dry_run`` parameter used to ignore the flag and perform the
real write — so a "preview" silently changed data. This ASGI middleware answers
``422 {"detail":{"code":"dry_run_unsupported"}}`` instead. ``dry_run=false`` (or empty) is harmless
and passes through. Support is derived from the route signatures, so new endpoints that add a
``dry_run`` query parameter are allowed automatically.
"""

from __future__ import annotations

import json
from urllib.parse import parse_qs

from starlette.routing import Match

MUTATING = {"POST", "PUT", "PATCH", "DELETE"}
TRUTHY = {"1", "true", "yes", "on", "y", "t"}


class DryRunGuard:
    def __init__(self, app, fastapi_app) -> None:
        self.app = app
        self._fastapi = fastapi_app

    def _supports(self, scope) -> bool:
        for route in self._fastapi.router.routes:
            match, _ = route.matches(scope)
            if match == Match.FULL:
                dep = getattr(route, "dependant", None)
                return bool(dep and any(q.name == "dry_run" for q in dep.query_params))
        return True  # unmatched → let the router answer 404/405

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope.get("method") in MUTATING:
            qs = parse_qs((scope.get("query_string") or b"").decode("latin-1"))
            values = [v.strip().lower() for v in qs.get("dry_run", [])]
            if any(v in TRUTHY for v in values) and not self._supports(scope):
                body = json.dumps({"detail": {"code": "dry_run_unsupported",
                                              "message": "this endpoint has no dry-run mode; nothing was changed"}}).encode()
                await send({"type": "http.response.start", "status": 422,
                            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
                await send({"type": "http.response.body", "body": body})
                return
        await self.app(scope, receive, send)
