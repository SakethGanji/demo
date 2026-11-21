"""P19: the per-request authorisation context (who, the request id, the decisions made), in a contextvar.

The middleware creates it; `require()` resolves the principal into it on first use (lazily: a GET without a
`require()` never touches the database for identity); `schema.audit()` reads the principal and request id from it.
"""

from __future__ import annotations

import contextvars
from dataclasses import dataclass, field
from typing import Any

from .identity import Principal


@dataclass
class RequestCtx:
    request_id: str
    headers: dict[str, str]
    client_host: str | None = None
    principal: Principal | None = None
    resolved: bool = False
    #: the decisions `require()` made on this request: {action, allow, needed, team, ...}
    decisions: list[dict[str, Any]] = field(default_factory=list)


CTX: contextvars.ContextVar[RequestCtx | None] = contextvars.ContextVar("ensemble_authz", default=None)


def current() -> RequestCtx | None:
    return CTX.get()
