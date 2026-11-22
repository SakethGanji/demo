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
    #: F-X4b (X4 C1): set on the agent's principal only. `id` / roles are the caller's (the agent has at most the
    #: caller's rights, never admin); `agent_id` is the agent's own service principal (audit_log.principal_id),
    #: `on_behalf_of` the caller's principal id, `session` the conversation the call belongs to.
    on_behalf_of: str | None = None
    agent_id: str | None = None
    session: str | None = None

    def out(self) -> dict[str, Any]:
        o = {"id": self.id, "display_name": self.display_name, "auth": self.auth, "kind": self.kind, "is_admin": self.is_admin}
        if self.auth == "agent":
            o.update(on_behalf_of=self.on_behalf_of, agent_id=self.agent_id, session=self.session)
        return o


class IdentityProvider(Protocol):
    name: str

    def authenticate(self, headers: dict[str, str], db: Any) -> Principal | None: ...  # None = no credential here


def _from_name(db: Any, name: str, auth: AuthKind) -> Principal | None:
    from ..catalog.teams_api import ensure_principal

    with db.connect() as con:
        r = ensure_principal(con, name)
    if r is None or r.get("disabled_at") or r.get("kind") == "service":  # a service (the agent) is never a typed name
        return None
    return Principal(id=r["id"], display_name=r["display_name"], identity_key=r["identity_key"], auth=auth,
                     kind=r["kind"], is_admin=bool(r["is_admin"]))


class TypedNameProvider:
    """The typed name in `X-Ensemble-Actor`, upserted into `principals` by identity key on first sight."""

    name = "typed"

    def authenticate(self, headers: dict[str, str], db: Any) -> Principal | None:
        name = (headers.get("x-ensemble-actor") or "").strip()[:80]
        return _from_name(db, name, "typed") if name else None


AGENT_NAME = "Vela agent"


def agent_principal_id(db: Any) -> str:
    """The agent's own service principal (kind 'service', no team, no rights of its own): audit_log.principal_id of
    every agent row. Created on first use."""
    from ..catalog import governance as G
    from ..catalog import schema as S

    k = G.identity_key(AGENT_NAME)
    with db.connect() as con:
        r = con.execute("SELECT id FROM principals WHERE identity_key = ?", (k,)).fetchone()
        if r is None:
            con.execute("INSERT INTO principals (id, display_name, identity_key, kind, is_admin, created_at) VALUES"
                        " (?, ?, ?, 'service', 0, ?) ON CONFLICT (identity_key) DO NOTHING",
                        (S.principal_id_for(k), AGENT_NAME, k, S.now()))
            r = con.execute("SELECT id FROM principals WHERE identity_key = ?", (k,)).fetchone()
    return r[0]


def _sign(payload: str) -> str:
    return hmac.new(_SECRET, payload.encode(), hashlib.sha256).hexdigest()


def internal_headers(on_behalf_of: str, session: str = "") -> dict[str, str]:
    """F-X4b (X4 C1): the headers the in-process agent client sends: the CALLER's principal id (bound when the session
    was started, from the request principal, never from a body) and the session id, signed. Only this process can
    produce them."""
    payload = f"{(on_behalf_of or '').strip()}\x00{(session or '').strip()}"
    return {INTERNAL_HEADER: f"{payload.encode().hex()}.{_sign(payload)}"}


class AgentProvider:
    """The agent acting on behalf of the caller who started its session: the caller's roles (at most the caller's
    rights, and never admin), auth 'agent', with its own service principal and the caller recorded for the audit."""

    name = "agent"

    def authenticate(self, headers: dict[str, str], db: Any) -> Principal | None:
        v = headers.get(INTERNAL_HEADER)
        if not v or "." not in v:
            return None
        hx, sig = v.rsplit(".", 1)
        try:
            payload = bytes.fromhex(hx).decode()
        except ValueError:
            return None
        if not hmac.compare_digest(sig, _sign(payload)) or "\x00" not in payload:
            return None
        pid, session = payload.split("\x00", 1)
        if not pid:
            return None
        with db.connect() as con:
            r = con.execute("SELECT * FROM principals WHERE id = ?", (pid,)).fetchone()
        if r is None or r["disabled_at"] or r["kind"] != "person":
            return None
        return Principal(id=r["id"], display_name=r["display_name"], identity_key=r["identity_key"], auth="agent",
                         kind=r["kind"], is_admin=False, on_behalf_of=r["id"], agent_id=agent_principal_id(db),
                         session=session or None)


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
