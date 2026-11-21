"""P19: /authz/coverage, /authz/check, and the per-team audit (C §1.7, §5.1): GET /teams/{t}/audit and …/audit/verify."""

from __future__ import annotations

import csv
import io
import json
import logging
import os
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response

from . import identity, policy
from .coverage import coverage
from .deps import mode, path, principal, public, require
from .middleware import AuthzMiddleware
from .scope import GLOBAL, resolve

router = APIRouter(prefix="/api")
log = logging.getLogger("ensemble.authz")


@router.get("/authz/coverage")
def authz_coverage(request: Request, all: bool = False) -> dict[str, Any]:
    """Every route with its method and its marker (action + object, or public). `uncovered` must be empty: every non-GET
    route and every row-returning GET carries require() or public(). ?all=true lists every route, else only the
    ones that must be covered."""
    out = coverage(request.app)
    if not all:
        out["routes"] = [r for r in out["routes"] if r["must"]]
    return {"mode": mode(), "identity_chain": [p.name for p in identity.chain()], **out}


@router.post("/authz/check", dependencies=[public("a question about a decision; it changes nothing")])
def authz_check(request: Request, body: dict[str, Any]) -> dict[str, Any]:
    """{action, object_kind?, object_id?, principal?} → {allow, reason, needed, role, team}. For the UI to grey out
    buttons. Asking on someone else's behalf (`principal`) needs a platform admin (refused only in enforce mode)."""
    body = body if isinstance(body, dict) else {}
    action = body.get("action")
    if action not in policy.ACTIONS:
        raise HTTPException(400, {"message": "Unknown action.", "actions": sorted(policy.ACTIONS)})
    kind, oid = body.get("object_kind"), body.get("object_id")
    me = principal(request, body.get("by"))
    who = me
    if body.get("principal"):
        who = identity._from_name(request.app.state.db, str(body["principal"]), "typed") if not str(body["principal"]).startswith("pr_") \
            else _by_id(request, str(body["principal"]))
        if who is None:
            raise HTTPException(404, f"No principal '{body['principal']}'.")
        if (me is None or not me.is_admin) and (me is None or me.id != who.id) and mode() == "enforce":
            raise HTTPException(403, {"code": "forbidden", "action": "authz.check", "needed": "admin",
                                      "message": "Only a platform admin may ask on someone else's behalf."})
    with request.app.state.db.connect() as con:
        if kind:
            if kind not in ("dataset", "run", "project", "team", "model_version", "model", "batch", "champion", "upload",
                            "dataset_request", "version_request", "autopilot", "principal"):
                raise HTTPException(400, f"Unknown object_kind '{kind}'.")
            sc = resolve(request.app, con, kind, oid)
            if not sc.found:
                raise HTTPException(404, f"No {kind} '{oid}'.")
        else:
            sc = GLOBAL
        d = policy.decide(con, who, action, sc)
    return {**d, "mode": mode(), "principal": who.out() if who else None}


def _by_id(request: Request, pid: str) -> identity.Principal | None:
    with request.app.state.db.connect() as con:
        r = con.execute("SELECT display_name FROM principals WHERE id = ?", (pid,)).fetchone()
    return identity._from_name(request.app.state.db, r[0], "typed") if r else None


def _team_id(request: Request, t: str) -> str:
    with request.app.state.db.connect() as con:
        r = con.execute("SELECT id FROM teams WHERE id = ? OR slug = ?", (t, t)).fetchone()
    if r is None:
        raise HTTPException(404, f"No team '{t}'.")
    return r[0]


AUDIT_OUT = ("id", "ts", "actor", "action", "team_id", "project_id", "object_kind", "object_id", "principal_id", "auth",
             "request_id", "run_id", "model_name", "version", "version_id", "from_stage", "to_stage", "comment", "details",
             "decision", "prev_hash", "row_hash")


@router.get("/teams/{team}/audit", dependencies=[require("audit.read", path("team", "team"))])
def team_audit(team: str, request: Request, action: str | None = None, object_kind: str | None = None,
               object_id: str | None = None, actor: str | None = None, from_: str | None = Query(None, alias="from"),
               to: str | None = None, format: str = "json", cursor: int | None = None,
               limit: int = Query(200, ge=1, le=5000)) -> Any:
    """A page of the team's audit, newest first. `cursor` = the `next_cursor` of the previous page. format=csv."""
    tid = _team_id(request, team)
    q, a = ["team_id = ?"], [tid]
    for col, v in (("action", action), ("object_kind", object_kind), ("object_id", object_id), ("actor", actor)):
        if v:
            q.append(f"{col} = ?")
            a.append(v)
    if from_:
        q.append("ts >= ?")
        a.append(from_)
    if to:
        q.append("ts <= ?")
        a.append(to)
    if cursor:
        q.append("id < ?")
        a.append(cursor)
    with request.app.state.db.connect() as con:
        rows = con.execute(f"SELECT {', '.join(AUDIT_OUT)} FROM audit_log WHERE {' AND '.join(q)} ORDER BY id DESC LIMIT ?",
                           (*a, limit + 1)).fetchall()
    more = len(rows) > limit
    rows = [dict(r) for r in rows[:limit]]
    if format == "csv":
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=AUDIT_OUT)
        w.writeheader()
        w.writerows(rows)
        return Response(buf.getvalue(), media_type="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="audit-{tid}.csv"'})
    for r in rows:
        for k in ("details", "decision"):
            try:
                r[k] = json.loads(r[k]) if r[k] else None
            except ValueError:
                pass
    return {"team_id": tid, "entries": rows, "next_cursor": rows[-1]["id"] if more and rows else None}


@router.get("/teams/{team}/audit/verify", dependencies=[require("audit.read", path("team", "team"))])
async def team_audit_verify(team: str, request: Request) -> dict[str, Any]:
    """Walk the team's hash chain: {ok, rows, legacy_rows, head} or {ok: false, first_break}."""
    from ..catalog.schema import verify_audit_chain

    tid = _team_id(request, team)

    def go() -> dict[str, Any]:
        with request.app.state.db.connect() as con:
            return verify_audit_chain(con, tid)

    return {"team_id": tid, **(await run_in_threadpool(go))}


def install(app: Any) -> None:
    """Called at the end of create_app: the router, the middleware (outermost), the coverage log, and the start-up
    refusals of enforce mode."""
    app.include_router(router)
    app.add_middleware(AuthzMiddleware)
    m = mode()
    cov = coverage(app)
    if cov["uncovered"]:
        log.warning("P19 authz coverage: %d uncovered route(s): %s", cov["n_uncovered"],
                    ", ".join(f"{u['method']} {u['path']}" for u in cov["uncovered"]))
    else:
        log.info("P19 authz: mode %s; coverage %d/%d routes that must be covered, 0 uncovered", m, cov["n_must"], cov["n_must"])
    if m != "enforce":
        return
    typed = identity.typed_in_chain()
    if typed and os.environ.get("ENSEMBLE_AUTHZ_ALLOW_TYPED") != "1":
        raise RuntimeError("ENSEMBLE_AUTHZ=enforce refuses to start with typed names in the identity chain (ENSEMBLE_IDP="
                           f"{os.environ.get('ENSEMBLE_IDP', 'typed')}): anyone can type any name. Sign-in (SSO, T3) comes "
                           "first; for a demo only, ENSEMBLE_AUTHZ_ALLOW_TYPED=1 starts it anyway.")
    if typed:
        log.warning("!!! ENSEMBLE_AUTHZ=enforce WITH TYPED NAMES (ENSEMBLE_AUTHZ_ALLOW_TYPED=1): names are NOT verified; "
                    "anyone can act as anyone. Demo only. !!!")
    if cov["uncovered"]:
        raise RuntimeError(f"ENSEMBLE_AUTHZ=enforce refuses to start: {cov['n_uncovered']} route(s) carry neither require() "
                           "nor public(): " + ", ".join(f"{u['method']} {u['path']}" for u in cov["uncovered"][:20]))
