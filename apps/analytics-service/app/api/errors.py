"""RFC 7807 ``application/problem+json`` error envelope.

Every error the API returns — raised ``HTTPException``, request-validation
failure, or an unhandled exception — is rendered as one consistent shape:

    {
      "type": "about:blank",
      "title": "Not Found",
      "status": 404,
      "detail": "Dataset not found: abc",
      "instance": "/api/v1/datasets/abc",
      "code": "not_found"
    }

Consumers can branch on the machine-readable ``code`` and always find a
human-readable ``detail``. Validation errors add an ``errors`` array.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.infra.config import settings

logger = logging.getLogger(__name__)

PROBLEM_CONTENT_TYPE = "application/problem+json"

# Human titles + machine codes for the statuses this service actually returns.
_STATUS_TITLES: dict[int, str] = {
    400: "Bad Request",
    401: "Unauthorized",
    403: "Forbidden",
    404: "Not Found",
    405: "Method Not Allowed",
    409: "Conflict",
    413: "Payload Too Large",
    415: "Unsupported Media Type",
    422: "Unprocessable Entity",
    423: "Locked",
    429: "Too Many Requests",
    500: "Internal Server Error",
    502: "Bad Gateway",
    503: "Service Unavailable",
}


def _code_for(status: int) -> str:
    return _STATUS_TITLES.get(status, "Error").lower().replace(" ", "_")


def problem_response(
    status: int,
    detail: str,
    instance: str,
    *,
    title: str | None = None,
    code: str | None = None,
    headers: dict[str, str] | None = None,
    **extra: object,
) -> JSONResponse:
    """Build a problem+json JSONResponse."""
    body: dict[str, object] = {
        "type": "about:blank",
        "title": title or _STATUS_TITLES.get(status, "Error"),
        "status": status,
        "detail": detail,
        "instance": instance,
        "code": code or _code_for(status),
    }
    body.update(extra)
    return JSONResponse(
        status_code=status,
        content=body,
        media_type=PROBLEM_CONTENT_TYPE,
        headers=headers,
    )


async def _http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    detail = exc.detail if isinstance(exc.detail, str) else _STATUS_TITLES.get(exc.status_code, "Error")
    return problem_response(
        exc.status_code,
        str(detail),
        request.url.path,
        headers=getattr(exc, "headers", None),
    )


async def _validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    return problem_response(
        422,
        "Request validation failed",
        request.url.path,
        errors=jsonable_encoder(exc.errors()),
    )


async def _unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
    # Never leak internals to clients unless explicitly running in debug.
    detail = f"{type(exc).__name__}: {exc}" if settings.debug else "An unexpected error occurred."
    return problem_response(500, detail, request.url.path)


def install_error_handlers(app: FastAPI) -> None:
    """Register the problem+json handlers on the app."""
    app.add_exception_handler(StarletteHTTPException, _http_exception_handler)
    app.add_exception_handler(RequestValidationError, _validation_exception_handler)
    app.add_exception_handler(Exception, _unhandled_exception_handler)
