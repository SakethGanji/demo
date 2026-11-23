"""Rate-limit hook for the public interface routes (Stage 5, DESIGN-STAGE5-UIS section 7.2).

``RateLimiter.check(bucket, key, cost=1)`` raises HTTP 429 ``RATE_LIMITED`` (with ``Retry-After``) when a caller is over
its budget. The default implementation never limits (``NoopRateLimiter``); a Postgres token bucket or a gateway
implementation plugs in later with ``set_rate_limiter(...)`` - the calls are already wired at every public route and
at task decide, so adding limits is configuration, not code.

Buckets: ``iface.submit:{project}:{slug}``, ``iface.poll:{project}:{slug}``, ``chat.send:{project}:{slug}``,
``task.decide``. ``key`` is the client address (see ``client_key``).
"""

from __future__ import annotations

import os
from typing import Protocol

from fastapi import HTTPException, Request


class RateLimiter(Protocol):
    def check(self, bucket: str, key: str, cost: int = 1) -> None:
        """Raise ``rate_limited(retry_after)`` when ``key`` is over budget for ``bucket``; return None otherwise."""
        ...


def rate_limited(retry_after: int = 1) -> HTTPException:
    return HTTPException(
        status_code=429,
        detail={"code": "RATE_LIMITED", "message": "Too many requests. Try again shortly."},
        headers={"Retry-After": str(max(1, int(retry_after)))},
    )


class NoopRateLimiter:
    """The default: every request passes."""

    def check(self, bucket: str, key: str, cost: int = 1) -> None:
        return None


_limiter: RateLimiter = NoopRateLimiter()


def get_rate_limiter() -> RateLimiter:
    return _limiter


def set_rate_limiter(limiter: RateLimiter | None) -> RateLimiter:
    """Install ``limiter`` (``None`` restores the no-op). Returns the previous one (tests restore it)."""
    global _limiter
    previous = _limiter
    _limiter = limiter or NoopRateLimiter()
    return previous


def client_key(request: Request) -> str:
    """The caller's address. ``X-Forwarded-For`` is honoured only when ``TRUSTED_PROXY`` is set (it is spoofable otherwise)."""
    if os.environ.get("TRUSTED_PROXY"):
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def check(bucket: str, request: Request | None, cost: int = 1) -> None:
    """Run the installed limiter for ``bucket`` keyed by the caller's address."""
    _limiter.check(bucket, client_key(request) if request is not None else "internal", cost)
