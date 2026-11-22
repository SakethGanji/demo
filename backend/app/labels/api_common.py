"""Shared bits of the label routers."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request
from fastapi.concurrency import run_in_threadpool

from .values import LabelError


async def call(fn, *a, **k):
    try:
        return await run_in_threadpool(fn, *a, **k)
    except LabelError as e:
        raise HTTPException(e.status, e.payload()) from e


def who(request: Request, body: dict[str, Any] | None = None) -> str:
    from ..authz.context import current
    from ..catalog import governance as G

    ctx = current()
    if ctx is not None and ctx.principal is not None:
        return ctx.principal.display_name or ctx.principal.id
    s = str((body or {}).get("by") or G.current_actor() or "").strip()[:80]
    return s or "unknown"


def obj(body: Any) -> dict[str, Any]:
    if body is None:
        return {}
    if not isinstance(body, dict):
        raise HTTPException(400, {"code": "bad_body", "message": "The body is a JSON object."})
    return body
