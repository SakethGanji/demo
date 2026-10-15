"""Optional actor identity (BACKEND-DESIGN §5.7).

The studio sends no identity header at this commit. If ``X-User-Id`` is present it is
recorded in ``created_by`` columns; it is never used for authorization.
"""

from __future__ import annotations

from fastapi import Request


def get_actor(request: Request) -> str | None:
    value = (request.headers.get("x-user-id") or "").strip()
    return value[:255] or None
