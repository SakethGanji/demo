"""Run-scoped broker tokens (BACKEND-DESIGN §5.1).

Opaque ``brk_<32 random bytes>`` tokens; only sha256(token) is kept, in-process.
The engine is single-process; a multi-worker deployment would need a shared store.
"""

from __future__ import annotations

import hashlib
import secrets
import threading
import time
from dataclasses import dataclass, field


@dataclass
class BrokerClaims:
    project_id: str
    environment: str
    execution_id: str | None
    origin: str  # code_node | python_step | python_route | agent | dev
    team_id: str = "default"
    allowed_functions: set[str] | None = None  # node types (or snake names); None = every function-exposed node
    # Review D5: read-only tokens (generate self-tests) may only read: no DataTable writes, no
    # send_email, no non-GET/HEAD http_request, no opaque-egress function. Denials → error_code read_only.
    read_only: bool = False
    max_calls: int = 500
    calls_used: int = 0
    expires_at: float = 0.0
    trace: list[str] = field(default_factory=list)  # broker call ids made with this token


class BrokerTokenRegistry:
    def __init__(self) -> None:
        self._tokens: dict[str, BrokerClaims] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _h(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    def mint(self, claims: BrokerClaims, ttl_s: float) -> str:
        token = "brk_" + secrets.token_urlsafe(32)
        claims.expires_at = time.time() + ttl_s
        with self._lock:
            self._purge()
            self._tokens[self._h(token)] = claims
        return token

    def verify(self, token: str | None) -> BrokerClaims | None:
        if not token or not token.startswith("brk_"):
            return None
        with self._lock:
            c = self._tokens.get(self._h(token))
            if c is None:
                return None
            if c.expires_at < time.time():
                self._tokens.pop(self._h(token), None)
                return None
            return c

    def revoke(self, token: str | None) -> None:
        if not token:
            return
        with self._lock:
            self._tokens.pop(self._h(token), None)

    def _purge(self) -> None:
        now = time.time()
        for k in [k for k, c in self._tokens.items() if c.expires_at < now]:
            self._tokens.pop(k, None)


registry = BrokerTokenRegistry()
