"""Build FastAPI responses from handler results (moved from routes/webhooks.py:_build_response)."""

from __future__ import annotations

from typing import Any

from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response

from ..engine.types import WebhookResponse


def build_response(result: dict[str, Any] | WebhookResponse, extra_headers: dict[str, str] | None = None) -> Response:
    """Convert a WebhookResponse (Respond node) or a plain dict into a Response."""
    extra = extra_headers or {}
    if isinstance(result, WebhookResponse):
        headers = {**(result.headers or {}), **extra}

        if result.body is None:
            return Response(status_code=result.status_code, headers=headers)

        if result.is_binary or isinstance(result.body, bytes):
            return Response(content=result.body, status_code=result.status_code,
                            media_type=result.content_type, headers=headers)

        if result.content_type == "text/plain":
            return PlainTextResponse(content=str(result.body), status_code=result.status_code, headers=headers)
        if result.content_type == "text/html":
            return HTMLResponse(content=str(result.body), status_code=result.status_code, headers=headers)
        if result.content_type == "application/xml":
            return Response(content=str(result.body), status_code=result.status_code,
                            media_type="application/xml", headers=headers)
        return JSONResponse(content=result.body, status_code=result.status_code, headers=headers)
    return JSONResponse(content=result, headers=extra)
