"""P19 (C §1.6): who is calling. `Principal`, the `IdentityProvider` protocol and the providers built now.

Providers form an ordered chain (`ENSEMBLE_IDP`, default `typed`); the first that returns a principal wins. The
in-process agent provider always comes first: it is not configurable and cannot be reached from outside the process.

- `TypedNameProvider`: today's `X-Ensemble-Actor` header (the UI's "Acting as"). Unverified, so `enforce` mode refuses
  to start with it in the chain unless `ENSEMBLE_AUTHZ_ALLOW_TYPED=1` (a demo flag, logged loudly).
- `AgentProvider`: the Vela agent calls the API in-process (httpx ASGI transport) with the session owner's name, signed
  with a per-process secret only the in-process client knows (`internal_headers()`). The agent therefore acts as its
  user, never as a service identity, and can never do more than its user.

Trusted-header, OIDC, Kerberos and service tokens are T3 (SSO); they plug into the same chain.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from dataclasses import dataclass
from typing import Any, Literal, Protocol

AuthKind = Literal["typed", "oidc", "saml", "kerberos", "header", "system", "agent", "service"]

INTERNAL_HEADER = "x-ensemble-internal"
_SECRET = secrets.token_bytes(32)  # per process: only the in-process agent client can sign


@dataclass(frozen=True)
class Principal:
    id: str
    display_name: str
    identity_key: str
    auth: AuthKind
    kind: str = "person"
    groups: tuple[str, ...] = ()
    is_admin: bool = False

    def out(self) -> dict[str, Any]:
        return {"id": self.id, "display_name": self.display_name, "auth": self.auth, "kind": self.kind, "is_admin": self.is_admin}


class IdentityProvider(Protocol):
    name: str

    def authenticate(self, headers: dict[str, str], db: Any) -> Principal | None: ...  # None = no credential here


def _from_name(db: Any, name: str, auth: AuthKind) -> Principal | None:
    from ..catalog.teams_api import ensure_principal

    with db.connect() as con:
        r = ensure_principal(con, name)
    if r is None or r.get("disabled_at"):
        return None
    return Principal(id=r["id"], display_name=r["display_name"], identity_key=r["identity_key"], auth=auth,
                     kind=r["kind"], is_admin=bool(r["is_admin"]))


class TypedNameProvider:
    """The typed name in `X-Ensemble-Actor`, upserted into `principals` by identity key on first sight."""

    name = "typed"

    def authenticate(self, headers: dict[str, str], db: Any) -> Principal | None:
        name = (headers.get("x-ensemble-actor") or "").strip()[:80]
        return _from_name(db, name, "typed") if name else None


def _sign(name: str) -> str:
    return hmac.new(_SECRET, name.encode(), hashlib.sha256).hexdigest()


def internal_headers(user: str) -> dict[str, str]:
    """The headers the in-process agent client sends: its user's name, signed (agent/tools.call)."""
    user = (user or "").strip()[:80]
    return {INTERNAL_HEADER: f"{user.encode().hex()}.{_sign(user)}"}


class AgentProvider:
    name = "agent"

    def authenticate(self, headers: dict[str, str], db: Any) -> Principal | None:
        v = headers.get(INTERNAL_HEADER)
        if not v or "." not in v:
            return None
        hx, sig = v.rsplit(".", 1)
        try:
            user = bytes.fromhex(hx).decode()
        except ValueError:
            return None
        if not user or not hmac.compare_digest(sig, _sign(user)):
            return None
        return _from_name(db, user, "agent")


PROVIDERS: dict[str, type] = {"typed": TypedNameProvider}


def chain() -> list[IdentityProvider]:
    names = [n.strip() for n in os.environ.get("ENSEMBLE_IDP", "typed").split(",") if n.strip()]
    unknown = [n for n in names if n not in PROVIDERS]
    if unknown:
        raise RuntimeError(f"ENSEMBLE_IDP names unknown identity providers {unknown}; built now: {sorted(PROVIDERS)} "
                           "(trusted header, OIDC, Kerberos and service tokens come with SSO).")
    return [AgentProvider(), *(PROVIDERS[n]() for n in names)]


def typed_in_chain() -> bool:
    return any(p.name == "typed" for p in chain())


def authenticate(headers: dict[str, str], db: Any) -> Principal | None:
    for p in chain():
        got = p.authenticate(headers, db)
        if got is not None:
            return got
    return None
