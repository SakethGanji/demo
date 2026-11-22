"""P19 (C §1.6): the enforcement point, a FastAPI dependency per route.

    @router.post("/datasets/{dataset_id}/edit", dependencies=[require("data.version", path("dataset_id", "dataset"))])
    @router.post("/runs", dependencies=[require("run.train", body("dataset_id", "dataset"))])

`require(action, *objs, also=[(action, obj)…], when=…)`:
- the object is the first of `objs` that carries a value (path / body / query / form); with no `objs` the action is
  a platform action (admins); objects without a value (no `project_id` sent) scope to tm_default, or to the
  platform with `default="global"`; kind `node` reads the node kind from the `kind` path parameter;
- an id that does not exist records no decision: the route answers its own 404;
- `when=variant(field, {value: action})` picks a stricter action from the body (`stage: Production`);
- the decision then follows the mode (`ENSEMBLE_AUTHZ`):
  - `off`: nothing is computed;
  - `audit` (default): a denial is audited as `authz_would_deny` (hash-chained, team-scoped) and the request goes
    through with `X-Ensemble-Authz: would-deny <action>`;
  - `enforce`: 401 without credentials, 403 `{code:'forbidden', action, team, needed}`, and a body `by` that is not
    the principal is refused (409 "you are signed in as X").

`public(reason)` marks a route that deliberately has no check (the coverage gate accepts it).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from fastapi import Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool

from . import identity, policy
from .context import RequestCtx, current
from .scope import GLOBAL, Scope, resolve


def mode() -> str:
    m = os.environ.get("ENSEMBLE_AUTHZ", "audit").strip().lower()
    return m if m in ("off", "audit", "enforce") else "audit"


@dataclass(frozen=True)
class Obj:
    source: str  # path | body | query
    name: str
    kind: str

    def __str__(self) -> str:
        return f"{self.source}:{self.name}→{self.kind}"


def path(name: str, kind: str) -> Obj:
    return Obj("path", name, kind)


def body(name: str, kind: str) -> Obj:
    """A JSON body key or a form field (multipart / urlencoded). Never use on a route that streams its raw body."""
    return Obj("body", name, kind)


def query(name: str, kind: str) -> Obj:
    return Obj("query", name, kind)


@dataclass(frozen=True)
class Variant:
    field: str
    actions: tuple[tuple[str, str], ...]


def variant(field: str, actions: dict[str, str]) -> Variant:
    return Variant(field, tuple(actions.items()))


BY_FIELDS = ("by", "actor", "created_by", "requested_by")


async def _body(request: Request) -> dict[str, Any]:
    ct = request.headers.get("content-type", "")
    try:
        if "application/json" in ct:
            b = await request.json()
            return b if isinstance(b, dict) else {}
        if "multipart/form-data" in ct or "application/x-www-form-urlencoded" in ct:
            f = await request.form()
            return {k: v for k, v in f.items() if isinstance(v, str)}
    except Exception:  # noqa: BLE001 - a malformed body is the route's 400 to give
        return {}
    return {}


def _value(o: Obj, request: Request, b: dict[str, Any]) -> Any:
    if o.kind == "node":  # /annotations/{kind}/{node_id}: the kind is a path parameter too
        v = request.path_params.get(o.name)
        return f"{request.path_params.get('kind')}\x00{v}" if v else None
    if o.source == "path":
        return request.path_params.get(o.name)
    if o.source == "query":
        return request.query_params.get(o.name)
    return b.get(o.name)


def principal(request: Request, by: str | None = None) -> identity.Principal | None:
    """The request's principal (resolved once). With typed names in the chain, a body/query `by` stands in when the
    header is missing: both are unverified typed names."""
    ctx = current()
    if ctx is None:
        ctx = RequestCtx(request_id="-", headers={k.lower(): v for k, v in request.headers.items()})
    if not ctx.resolved:
        ctx.principal = identity.authenticate(ctx.headers, request.app.state.db)
        ctx.resolved = True
    if ctx.principal is None and by and identity.typed_in_chain():
        ctx.principal = identity._from_name(request.app.state.db, str(by), "typed")
    return ctx.principal


def _scope(app: Any, con: Any, o: Obj, v: Any) -> Scope:
    if o.kind == "node":
        k, i = str(v).split("\x00", 1)
        return resolve(app, con, "node:" + k, i)
    return resolve(app, con, o.kind, v)


def _decide(request: Request, action: str, objs: tuple[Obj, ...], also: tuple[tuple[str, Obj], ...],
            vals: dict[str, Any], by: str | None, m: str, default: str = "tm_default") -> list[dict[str, Any]]:
    app = request.app
    p = principal(request, by)
    out: list[dict[str, Any]] = []
    with app.state.db.connect() as con:
        for act, specs in ((action, objs), *((a, (o,)) for a, o in also)):
            if not specs:
                sc = GLOBAL
            else:
                o = next((o for o in specs if vals.get(str(o)) not in (None, "")), None)
                sc = _scope(app, con, o, vals[str(o)]) if o else \
                    GLOBAL if default == "global" else Scope("tm_default", object_kind=specs[0].kind)
                if not sc.found:
                    continue  # the route answers 404
            d = policy.decide(con, p, act, sc)
            d.update(mode=m, method=request.method, path=request.url.path)
            out.append(d)
        denied = [d for d in out if not d["allow"]]
        for d in denied:
            from ..catalog.schema import audit
            audit(con, p.display_name if p else (by or "anonymous"), "authz_would_deny" if m == "audit" else "authz_denied",
                  {k: d.get(k) for k in ("action", "needed", "role", "reason", "method", "path", "mode")},
                  team_id=d["team"] or "tm_default", object_kind=d.get("object_kind"), object_id=d.get("object_id"),
                  principal_id=p.id if p else None, auth=p.auth if p else "none",
                  decision={k: d.get(k) for k in ("allow", "action", "needed", "role", "team", "mode")})
    return out


AGENT_PREFIX = "Vela agent on behalf of "


def by_ok(by: str, p: identity.Principal) -> bool:
    """A body `by` names the principal. F-X4b (X4 C1): the agent is no longer exempt: its `by` must be the caller who
    started the session ("Vela agent on behalf of <caller>" or the caller's name)."""
    from ..catalog.governance import same_person

    if p.auth == "agent" and by.startswith(AGENT_PREFIX):
        by = by[len(AGENT_PREFIX):]
    return same_person(by, p.display_name)


def require(action: str, *objs: Obj, also: list[tuple[str, Obj]] | None = None, when: Variant | None = None,
            default: str = "tm_default") -> Any:
    if action not in policy.ACTIONS:
        raise ValueError(f"require(): unknown action {action!r}")
    for a, _ in also or []:
        if a not in policy.ACTIONS:
            raise ValueError(f"require(): unknown action {a!r}")
    also_t = tuple(also or ())

    async def authz_dependency(request: Request) -> None:
        m = mode()
        if m == "off":
            return
        need_body = any(o.source == "body" for o in objs) or any(o.source == "body" for _, o in also_t) or when is not None
        b = await _body(request) if need_body or request.method in ("POST", "PUT", "PATCH") and \
            "application/json" in request.headers.get("content-type", "") else {}
        vals = {str(o): _value(o, request, b) for o in (*objs, *(o for _, o in also_t))}
        act = action
        if when is not None:
            act = dict(when.actions).get(str(b.get(when.field)), action)
        by = next((str(b[k]) for k in BY_FIELDS if isinstance(b.get(k), str) and b[k].strip()), None) \
            or request.query_params.get("by")
        decisions = await run_in_threadpool(_decide, request, act, objs, also_t, vals, by, m, default)
        ctx = current()
        if ctx is not None:
            ctx.decisions.extend(decisions)
        if m != "enforce":
            return
        p = ctx.principal if ctx is not None else None
        for d in decisions:
            if d["allow"]:
                continue
            if p is None:
                raise HTTPException(401, {"code": "unauthenticated", "action": d["action"], "message": d["reason"]},
                                    headers={"WWW-Authenticate": 'Typed realm="ensemble"'})
            raise HTTPException(403, {"code": "forbidden", "action": d["action"], "team": d["team"], "needed": d["needed"],
                                      "message": d["reason"]})
        if p is not None and by and not by_ok(by, p):
            raise HTTPException(409, f"You are signed in as {p.display_name}; 'by' says {by}.")

    authz_dependency.authz = {"action": action, "objs": [str(o) for o in objs],  # type: ignore[attr-defined]
                              "also": [[a, str(o)] for a, o in also_t],
                              "when": {"field": when.field, "actions": dict(when.actions)} if when else None,
                              "default": default if objs else None}
    return Depends(authz_dependency)


def public(reason: str) -> Any:
    async def authz_public() -> None:
        return None

    authz_public.authz = {"public": reason}  # type: ignore[attr-defined]
    return Depends(authz_public)


def visible_teams(con: Any, p: identity.Principal | None) -> set[str] | None:
    """The teams whose rows `p` may list: its own, plus teams with an active (unexpired) share to them. None = every team
    (an admin). P21: a sharing team's rows are then narrowed to the shared objects (`shares.row_visible`)."""
    if p is not None and p.is_admin:
        return None
    if p is None:
        return set()
    from ..catalog import shares

    mine = shares.my_teams(con, p.id)
    return mine | shares.sharing_teams(con, mine)


def own_teams(con: Any, p: identity.Principal | None) -> set[str] | None:
    """The principal's own teams (None = an admin: every team)."""
    if p is not None and p.is_admin:
        return None
    if p is None:
        return set()
    from ..catalog import shares

    return shares.my_teams(con, p.id)


def record(request: Request, d: dict[str, Any], by: str | None = None) -> dict[str, Any]:
    """P21: a decision made in a handler (a second, finer check than the route's, e.g. the level of a certification or
    the project rule for training on a shared version) follows the mode exactly like require(): recorded in the request
    context and the response header, a denial audited (`authz_would_deny` / `authz_denied`), 401 / 403 in enforce."""
    m = mode()
    if m == "off":
        return d
    p = principal(request, by)
    d = {**d, "mode": m, "method": request.method, "path": request.url.path}
    ctx = current()
    if ctx is not None:
        ctx.decisions.append(d)
    if not d["allow"]:
        from ..catalog.schema import audit

        with request.app.state.db.connect() as con:
            audit(con, p.display_name if p else (by or "anonymous"), "authz_would_deny" if m == "audit" else "authz_denied",
                  {k: d.get(k) for k in ("action", "needed", "role", "reason", "method", "path", "mode")},
                  team_id=d.get("team") or "tm_default", object_kind=d.get("object_kind"), object_id=d.get("object_id"),
                  principal_id=p.id if p else None, auth=p.auth if p else "none",
                  decision={k: d.get(k) for k in ("allow", "action", "needed", "role", "team", "mode")})
        if m == "enforce":
            if p is None:
                raise HTTPException(401, {"code": "unauthenticated", "action": d["action"], "message": d["reason"]})
            raise HTTPException(403, {"code": "forbidden", "action": d["action"], "team": d.get("team"),
                                      "needed": d.get("needed"), "message": d["reason"]})
    return d


def check(request: Request, action: str, kind: str, object_id: str, by: str | None = None) -> dict[str, Any] | None:
    """policy.decide for (action, object) inside a handler, then record() it. None when the mode is off or the object
    does not resolve."""
    if mode() == "off":
        return None
    p = principal(request, by)
    with request.app.state.db.connect() as con:
        sc = resolve(request.app, con, kind, object_id)
        if not sc.found:
            return None
        d = policy.decide(con, p, action, sc)
    return record(request, d, by)
