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
from starlette.middleware.base import BaseHTTPMiddleware

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
    # 460 is not an IANA status: it is the TUS checksum-mismatch convention
    # this service implements. Listed so the envelope says what happened
    # instead of falling through to the "Error" default.
    460: "Checksum Mismatch",
    500: "Internal Server Error",
    502: "Bad Gateway",
    503: "Service Unavailable",
    507: "Insufficient Storage",
}


def _code_for(status: int) -> str:
    return _STATUS_TITLES.get(status, "Error").lower().replace(" ", "_")


class ProblemException(StarletteHTTPException):
    """HTTPException carrying a custom problem+json ``code`` and extra fields.

    Raise where a machine-readable error contract matters, e.g.::

        raise ProblemException(
            400, "Dataset has 3 sheets — specify one",
            code="sheet-selection-required", sheets=["Revenue", "Expenses"],
        )

    ``headers`` is a transport concern and is forwarded to the base class, not
    collected into ``extra``. Everything in ``extra`` is rendered as a *body*
    field, so a ``headers=`` kwarg that fell through to it would have been
    serialized into the JSON document while ``_http_exception_handler`` read
    ``exc.headers`` (``None``, from the base) and passed it positionally —
    ``problem_response(..., headers=None, headers={...})``, a ``TypeError`` and
    a 500 in place of the response that was asked for. Forwarding here rather
    than popping in the handler fixes the object itself, so ``exc.headers``
    is correct for *every* reader — Starlette's own middleware included — not
    just for the one handler that happens to unpack it.
    """

    def __init__(self, status_code: int, detail: str, *, code: str | None = None,
                 headers: dict[str, str] | None = None, **extra: object):
        super().__init__(status_code=status_code, detail=detail, headers=headers)
        self.code = code
        self.extra = extra


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
        code=getattr(exc, "code", None),
        headers=getattr(exc, "headers", None),
        **getattr(exc, "extra", {}),
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


class UnhandledExceptionMiddleware(BaseHTTPMiddleware):
    """Turn an escaping exception into problem+json *inside* the middleware stack.

    ``add_exception_handler(Exception, ...)`` alone is not enough, and the
    reason is structural. Starlette does not treat ``Exception`` (or ``500``)
    as a normal handler: ``build_middleware_stack`` pops it out and hands it to
    ``ServerErrorMiddleware``, which it then places at the very TOP of the
    stack — above every user middleware, CORSMiddleware included. So the 500 it
    writes is emitted after the response has already left the CORS layer, and
    nothing ever adds ``Access-Control-Allow-Origin`` to it.

    In a browser that is not a cosmetic difference. A cross-origin response
    without ACAO is never handed to JS at all: fetch rejects with "blocked by
    CORS policy" / ``net::ERR_FAILED``, with no status and no body. The client
    therefore cannot read the ``code`` this service documents for every error,
    and every 5xx collapses into an indistinguishable network failure — exactly
    when a caller most needs to tell "the server broke" from "you're offline".
    A 4xx is unaffected (it is raised as an ``HTTPException`` and handled by
    ``ExceptionMiddleware``, well below CORS), which is what makes the gap so
    easy to miss.

    Catching here — as the INNERMOST user middleware — makes the 500 an
    ordinary response on the way out, so every outer layer decorates it the way
    it decorates a 200: CORS attaches its headers, ``RequestIDMiddleware``
    attaches the ``X-Request-Id`` a bug report quotes, ``SecurityHeaders``
    attaches its set, and ``AuditMiddleware`` records the failure with its real
    status instead of losing the row.

    ``ServerErrorMiddleware`` stays as the backstop for anything raised by the
    middleware ABOVE this one, which by definition cannot be repaired here.
    A failure that happens after a streaming body has started sending is also
    left alone: it surfaces while the response is being iterated, not inside
    ``dispatch``, and the status line is already on the wire by then.
    """

    async def dispatch(self, request: Request, call_next):
        try:
            return await call_next(request)
        except Exception as exc:  # noqa: BLE001 — deliberate catch-all; re-rendered
            # Same handler ServerErrorMiddleware would have used, so the body
            # is byte-for-byte the envelope callers already branch on, and the
            # traceback still reaches the server log and only the server log.
            return await _unhandled_exception_handler(request, exc)


def install_error_handlers(app: FastAPI) -> None:
    """Register the problem+json handlers on the app."""
    app.add_exception_handler(StarletteHTTPException, _http_exception_handler)
    app.add_exception_handler(RequestValidationError, _validation_exception_handler)
    # Backstop only — see UnhandledExceptionMiddleware for why this alone
    # cannot produce a CORS-decorated 500.
    app.add_exception_handler(Exception, _unhandled_exception_handler)
