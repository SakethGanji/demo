"""P21 (C §1.4, plan §6.1, §11 item 4): read-only shares of a dataset or a project to another team.

- A share is requested by a maintainer of the owning team and decided by an owner of that team, never the requester
  (four eyes through `authz.policy.four_eyes`). Revoked by a maintainer of the owning team. Optional `expires_at`.
- **Scope** picks the versions the grantee sees: `certified` (default: versions with a live certification), `default`
  (the dataset's current default), `pinned` (`pinned_version`) or `all`.
- **PII**: `masked` by default. `pii='unmasked'` is a separate, explicit grant (the decide body must say
  `grant_unmasked: true`); the grantee's unmasked read still needs a reason and is read-audited (P20's path).
- The grantee may read metadata, take masked previews and exports, and **train in its own projects** (the project rule
  in `train_project`). It never makes a version of the owner's dataset.
- `grant()` is what `authz.policy.decide` asks for every `shared=True` rule; `row_visible()` narrows the lists in
  enforce mode (`authz.lists.with_team`).

Everything is computed from the `shares` rows at request time, so a revoke or an expiry takes effect on the next call;
runs already trained keep their pinned inputs (their verify reads the bytes, not the share).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool

from ..authz import body, path, require
from . import governance as G
from .schema import audit, now

router = APIRouter(prefix="/api")

SCOPES = ("certified", "default", "pinned", "all")
LIVE = "s.status = 'active' AND (s.expires_at IS NULL OR s.expires_at > ?)"


class ShareError(ValueError):
    def __init__(self, status: int, code: str, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.status, self.code, self.message, self.extra = status, code, message, extra

    def http(self) -> HTTPException:
        return HTTPException(self.status, {"code": self.code, "message": self.message, **self.extra})


# --------------------------------------------------------------------------- grants (read side, used by authz)


def my_teams(con: Any, principal_id: str) -> set[str]:
    return {r[0] for r in con.execute("SELECT team_id FROM team_members WHERE principal_id = ?", (principal_id,))}


def sharing_teams(con: Any, teams: set[str]) -> set[str]:
    """Owner teams of live shares to any of `teams`."""
    if not teams:
        return set()
    q = ",".join("?" * len(teams))
    return {r[0] for r in con.execute(f"SELECT s.owner_team FROM shares s WHERE {LIVE} AND s.grantee_team IN ({q})",
                                      (now(), *sorted(teams)))}


def version_info(con: Any, vid: str) -> dict[str, Any] | None:
    r = con.execute("SELECT d.id, COALESCE(d.root_id, d.id) AS root, d.project_id, COALESCE(d.team_id, p.team_id) AS team,"
                    " d.state, d.version_no FROM datasets d LEFT JOIN projects p ON p.id = d.project_id WHERE d.id = ?",
                    (vid,)).fetchone()
    return dict(r) if r else None


def live_certification(con: Any, vid: str, levels: tuple[str, ...] = ("certified", "approved_for_model")) -> dict[str, Any] | None:
    q = ",".join("?" * len(levels))
    r = con.execute(f"SELECT * FROM certifications WHERE version_id = ? AND status = 'active' AND level IN ({q})"
                    " AND (expires_at IS NULL OR expires_at > ?) ORDER BY decided_at DESC LIMIT 1", (vid, *levels, now())).fetchone()
    return dict(r) if r else None


def default_version(con: Any, root: str) -> str | None:
    """The dataset's effective default (dsets.effective_default): the pointer when it names a live version, else the
    newest live version."""
    from . import dsets

    rows = [dict(r) for r in con.execute("SELECT id, state FROM datasets WHERE COALESCE(root_id, id) = ?"
                                         " ORDER BY COALESCE(version_no, 0), created_at", (root,))]
    return dsets.effective_default(rows, dsets.card(con, root))[0]


def in_scope(con: Any, s: dict[str, Any], v: dict[str, Any]) -> bool:
    """Whether share `s` lets its grantee see version `v` (version_info)."""
    if v.get("state") == "deleted":
        return False
    if s["object_kind"] == "dataset" and s["object_id"] != v["root"]:
        return False
    if s["object_kind"] == "project" and s["object_id"] != v["project_id"]:
        return False
    sc = s["scope"]
    if sc == "all":
        return True
    if sc == "pinned":
        return s.get("pinned_version") == v["id"]
    if sc == "default":
        return default_version(con, v["root"]) == v["id"]
    return live_certification(con, v["id"]) is not None  # certified


def _live_to(con: Any, teams: set[str], kinds_ids: list[tuple[str, str]], unmasked: bool = False) -> list[dict[str, Any]]:
    if not teams or not kinds_ids:
        return []
    qt = ",".join("?" * len(teams))
    ors = " OR ".join("(s.object_kind = ? AND s.object_id = ?)" for _ in kinds_ids)
    args: list[Any] = [now(), *sorted(teams)]
    for k, i in kinds_ids:
        args += [k, i]
    rows = con.execute(f"SELECT s.* FROM shares s WHERE {LIVE} AND s.grantee_team IN ({qt}) AND ({ors})"
                       + (" AND s.pii = 'unmasked'" if unmasked else "") + " ORDER BY s.decided_at", tuple(args))
    return [dict(r) for r in rows]


def grant_for_version(con: Any, teams: set[str], vid: str, *, unmasked: bool = False) -> dict[str, Any] | None:
    """The live share that lets one of `teams` see version `vid` within its scope (an unmasked one if asked)."""
    v = version_info(con, vid)
    if v is None:
        return None
    ids = [("dataset", v["root"])] + ([("project", v["project_id"])] if v.get("project_id") else [])
    for s in _live_to(con, teams - ({v["team"]} if v.get("team") else set()), ids, unmasked):
        if in_scope(con, s, v):
            return s
    return None


def grant(con: Any, principal_id: str, scope: Any, *, unmasked: bool = False) -> dict[str, Any] | None:
    """authz: the share that lets the principal's teams act on `scope`'s object (policy rules with shared=True).
    dataset → a share of its root or project that covers this version; project → a share of the project; run → a share
    of the run's project (a dataset share does not share the owner's runs). Anything else is never shared."""
    try:
        teams = my_teams(con, principal_id)
        if not teams:
            return None
        k, i = scope.object_kind, scope.object_id
        if k == "dataset" and i:
            return grant_for_version(con, teams, i, unmasked=unmasked)
        if k in ("project", "run") and scope.project_id and not unmasked:
            rows = _live_to(con, teams - {scope.team_id}, [("project", scope.project_id)])
            return next((s for s in rows if k == "project" or s["scope"] == "all"), None)
    except Exception:
        import logging

        logging.getLogger(__name__).exception("share grant lookup")
    return None


def row_visible(con: Any, teams: set[str], kind: str, rid: str) -> bool:
    """Enforce-mode lists: may a row of a sharing team be shown? dataset → its version is in a share's scope;
    project → the project is shared; run → its project is shared with scope all."""
    if kind == "dataset":
        return grant_for_version(con, teams, rid) is not None
    if kind == "project":
        return bool(_live_to(con, teams, [("project", rid)]))
    if kind == "run":
        r = con.execute("SELECT project_id FROM runs WHERE id = ?", (rid,)).fetchone()
        if not r or not r[0]:
            return False
        return any(s["scope"] == "all" for s in _live_to(con, teams, [("project", r[0])]))
    return False


# --------------------------------------------------------------------------- the project rule (training on a share)


def train_project(request: Request, dataset_id: str, project_id: str | None, by: str | None) -> str | None:
    """C §1.3 `run.train` shared: "into the caller's own project, reading the shared version". Returns the project the
    run goes into (None: the dataset's own project, as before). A version of another team needs a `project_id` (400
    project_required); the project's team must hold a live share covering the version, else the decision is a denial
    (would-deny in audit, 403 in enforce), recorded like any require()."""
    from ..authz import deps
    from ..authz.policy import role_of

    db = request.app.state.db
    with db.connect() as con:
        v = version_info(con, dataset_id)
        if v is None:
            return project_id
        if not project_id:
            p = deps.principal(request, by) if deps.mode() != "off" else None
            if p is not None and not p.is_admin and v.get("team") and role_of(con, p.id, v["team"]) is None \
                    and grant_for_version(con, my_teams(con, p.id), dataset_id) is not None:
                raise ShareError(400, "project_required", f"{dataset_id} belongs to team {v['team']} and is shared with your team: "
                                                          "train into one of your own projects (send project_id).")
            return None
        pr = con.execute("SELECT id, team_id FROM projects WHERE id = ?", (project_id,)).fetchone()
        if not pr:
            raise ShareError(404, "project_not_found", f"No project '{project_id}'.")
        if not v.get("team") or pr["team_id"] == v["team"]:
            return project_id
        s = grant_for_version(con, {pr["team_id"]}, dataset_id)
    d = {"action": "run.train", "team": pr["team_id"], "object_kind": "dataset", "object_id": dataset_id,
         "needed": f"a live share of {dataset_id} to {pr['team_id']}", "role": None,
         "allow": s is not None,
         "reason": (f"trains into {project_id} ({pr['team_id']}) on a version shared by {v['team']} ({s['id']}, scope {s['scope']})"
                    if s else f"{dataset_id} (team {v['team']}) is not shared with {pr['team_id']} in a scope that covers it: "
                              f"a run in {project_id} cannot read it")}
    deps.record(request, d, by)
    return project_id


# --------------------------------------------------------------------------- write side


def _team(con: Any, ref: str) -> dict[str, Any]:
    r = con.execute("SELECT * FROM teams WHERE id = ? OR slug = ?", (ref, ref)).fetchone()
    if not r:
        raise ShareError(404, "team_not_found", f"No team '{ref}'.")
    return dict(r)


def effective(s: dict[str, Any]) -> dict[str, Any]:
    """The row as the API shows it: an active share past expires_at reads as expired."""
    out = dict(s)
    if out["status"] == "active" and out.get("expires_at") and out["expires_at"] <= now():
        out["status"] = "expired"
    return out


def get(con: Any, sid: str) -> dict[str, Any]:
    r = con.execute("SELECT * FROM shares WHERE id = ?", (sid,)).fetchone()
    if not r:
        raise ShareError(404, "share_not_found", f"No share '{sid}'.")
    return effective(dict(r))


def _iso_future(v: Any) -> str | None:
    if v in (None, ""):
        return None
    try:
        d = datetime.fromisoformat(str(v))  # Z and offsets are read since Python 3.11
    except ValueError as e:
        raise ShareError(400, "bad_expires_at", "expires_at is an ISO date or time (2027-06-30).") from e
    if d.tzinfo is None:
        d = d.replace(tzinfo=UTC)  # a naive time is read as UTC
    out = d.astimezone(UTC).isoformat(timespec="seconds")
    if out <= now():
        raise ShareError(400, "bad_expires_at", "expires_at is in the past.")
    return out


def request_share(db: Any, b: dict[str, Any], by: str) -> dict[str, Any]:
    oid = str(b.get("object_id") or "").strip()
    kind = b.get("object_kind") or ("project" if oid.startswith("prj_") else "dataset")
    if kind not in ("dataset", "project"):
        raise ShareError(400, "bad_object_kind", "object_kind is dataset or project.")
    sc = b.get("scope") or "certified"
    if sc not in SCOPES:
        raise ShareError(400, "bad_scope", f"scope is one of {', '.join(SCOPES)}.")
    pii = b.get("pii") or "masked"
    if pii not in ("masked", "unmasked"):
        raise ShareError(400, "bad_pii", "pii is masked (default) or unmasked.")
    reason = str(b.get("reason") or "").strip()
    if len(reason) < 3:
        raise ShareError(400, "reason_required", "Say why the other team needs this (reason, 3 characters at least).")
    exp = _iso_future(b.get("expires_at"))
    with db.connect() as con:
        if kind == "project":
            r = con.execute("SELECT id, team_id FROM projects WHERE id = ?", (oid,)).fetchone()
            if not r:
                raise ShareError(404, "not_found", f"No project '{oid}'.")
            obj, owner = r["id"], r["team_id"]
        else:
            r = con.execute("SELECT COALESCE(d.root_id, d.id) AS root, COALESCE(d.team_id, p.team_id) AS team FROM datasets d"
                            " LEFT JOIN projects p ON p.id = d.project_id WHERE d.id = ?", (oid,)).fetchone()
            if not r:
                raise ShareError(404, "not_found", f"No dataset '{oid}'.")
            obj, owner = r["root"], r["team"] or "tm_default"
        pinned = b.get("pinned_version")
        if sc == "pinned":
            if kind != "dataset" or not pinned:
                raise ShareError(400, "pinned_version_required", "scope 'pinned' shares one version of a dataset: send pinned_version.")
            pv = version_info(con, str(pinned))
            if not pv or pv["root"] != obj:
                raise ShareError(400, "pinned_version_invalid", f"{pinned} is not a version of {obj}.")
        elif pinned:
            raise ShareError(400, "pinned_version_unexpected", "pinned_version goes with scope 'pinned'.")
        g = _team(con, str(b.get("grantee_team") or ""))
        if g["id"] == owner:
            raise ShareError(400, "same_team", "A team does not share with itself.")
        if g["state"] != "active":
            raise ShareError(409, "team_archived", f"Team {g['slug']} is archived.")
        dup = con.execute("SELECT id, status FROM shares s WHERE object_kind = ? AND object_id = ? AND grantee_team = ? AND"
                          " (s.status = 'pending' OR (" + LIVE + "))", (kind, obj, g["id"], now())).fetchone()
        if dup:
            raise ShareError(409, "share_exists", f"{obj} already has a {dup['status']} share to {g['slug']} ({dup['id']}): revoke it first.",
                             share_id=dup["id"])
        sid = "sh_" + uuid.uuid4().hex[:10]
        con.execute("INSERT INTO shares (id, object_kind, object_id, owner_team, grantee_team, scope, pinned_version, pii, status,"
                    " requested_by, requested_at, reason, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)",
                    (sid, kind, obj, owner, g["id"], sc, pinned if sc == "pinned" else None, pii, by, now(), reason, exp))
        audit(con, by, "share_requested", {"share_id": sid, "object_kind": kind, "object_id": obj, "grantee_team": g["id"],
                                           "scope": sc, "pinned_version": pinned, "pii": pii, "expires_at": exp, "reason": reason,
                                           **({"dataset_id": obj, "root_id": obj} if kind == "dataset" else {})},
              team_id=owner, object_kind="share", object_id=sid)
        return get(con, sid)


def decide_share(db: Any, sid: str, approve: Any, by: str, comment: str, grant_unmasked: Any) -> dict[str, Any]:
    from ..authz.policy import four_eyes

    if not isinstance(approve, bool):
        raise ShareError(400, "approve_required", 'Send {"approve": true} or {"approve": false}.')
    with db.connect() as con:
        s = get(con, sid)
        if s["status"] != "pending":
            raise ShareError(409, "not_pending", f"This share is {s['status']}.")
        msg = four_eyes("data.share_decide", s, by)
        if msg:
            raise ShareError(409, "four_eyes", msg + " (an owner of the sharing team other than the requester)")
        if approve and s["pii"] == "unmasked" and grant_unmasked is not True:
            raise ShareError(400, "grant_unmasked_required", "This share asks for PII in clear (pii='unmasked'): approve it "
                                                             "with grant_unmasked: true to grant that explicitly, or reject it and "
                                                             "ask for a masked share.")
        st = "active" if approve else "rejected"
        con.execute("UPDATE shares SET status = ?, decided_by = ?, decided_at = ? WHERE id = ? AND status = 'pending'",
                    (st, by, now(), sid))
        det = {"share_id": sid, "object_kind": s["object_kind"], "object_id": s["object_id"], "grantee_team": s["grantee_team"],
               "scope": s["scope"], "pii": s["pii"], "requested_by": s["requested_by"],
               **({"unmasked_granted": True} if approve and s["pii"] == "unmasked" else {}),
               **({"dataset_id": s["object_id"], "root_id": s["object_id"]} if s["object_kind"] == "dataset" else {})}
        act = "share_approved" if approve else "share_rejected"
        audit(con, by, act, det, comment=comment, team_id=s["owner_team"], object_kind="share", object_id=sid)
        if approve:  # the grantee team's own audit sees what it received
            audit(con, by, "share_received", det, comment=comment, team_id=s["grantee_team"], object_kind="share", object_id=sid)
        return get(con, sid)


def revoke_share(db: Any, sid: str, by: str, reason: str) -> dict[str, Any]:
    if len(reason.strip()) < 3:
        raise ShareError(400, "reason_required", "Say why the share is revoked (reason).")
    with db.connect() as con:
        s = get(con, sid)
        if s["status"] not in ("pending", "active", "expired"):
            raise ShareError(409, "not_active", f"This share is {s['status']}.")
        con.execute("UPDATE shares SET status = 'revoked', revoked_by = ?, revoked_at = ? WHERE id = ?", (by, now(), sid))
        kept = runs_on(con, s)
        det = {"share_id": sid, "object_kind": s["object_kind"], "object_id": s["object_id"], "grantee_team": s["grantee_team"],
               "runs_kept": [r["id"] for r in kept][:200], "reason": reason,
               **({"dataset_id": s["object_id"], "root_id": s["object_id"]} if s["object_kind"] == "dataset" else {})}
        audit(con, by, "share_revoked", det, comment=reason, team_id=s["owner_team"], object_kind="share", object_id=sid)
        audit(con, by, "share_revoked", det, comment=reason, team_id=s["grantee_team"], object_kind="share", object_id=sid)
        return {**get(con, sid), "runs_kept": kept}


def runs_on(con: Any, s: dict[str, Any]) -> list[dict[str, Any]]:
    """Runs of the grantee team that trained on the shared object (they keep their pinned inputs after a revoke).
    F-PII: also the grantee's runs on a join frame that read a version of it (`run_inputs` role join_input, `via_join`)."""
    col = "COALESCE(d.root_id, d.id)" if s["object_kind"] == "dataset" else "d.project_id"
    rows = con.execute("SELECT r.id, r.status, r.dataset_id, r.project_id FROM runs r JOIN datasets d ON d.id = r.dataset_id"
                       f" LEFT JOIN projects p ON p.id = r.project_id WHERE {col} = ? AND COALESCE(r.team_id, p.team_id) = ?"
                       " ORDER BY r.created_at", (s["object_id"], s["grantee_team"]))
    out = [dict(r) for r in rows]
    seen = {r["id"] for r in out}
    for r in con.execute("SELECT DISTINCT r.id, r.status, r.dataset_id, r.project_id, r.created_at FROM run_inputs i"
                         " JOIN runs r ON r.id = i.run_id JOIN datasets d ON d.id = i.version_id"
                         f" LEFT JOIN projects p ON p.id = r.project_id WHERE i.role = 'join_input' AND {col} = ?"
                         " AND COALESCE(r.team_id, p.team_id) = ? ORDER BY r.created_at", (s["object_id"], s["grantee_team"])):
        if r["id"] not in seen:
            out.append({k: r[k] for k in ("id", "status", "dataset_id", "project_id")} | {"via_join": True})
    return out


# --------------------------------------------------------------------------- routes


def _by(b: dict[str, Any]) -> str:
    from .api import _who

    return _who((b.get("by") or G.current_actor() or "").strip())


async def _run(fn, *a):
    try:
        return await run_in_threadpool(fn, *a)
    except ShareError as e:
        raise e.http() from e


@router.post("/shares", status_code=201, dependencies=[require("data.share", body("object_id", "shareable"))])
async def create_share(request: Request, b: dict[str, Any]) -> dict[str, Any]:
    """P21: {object_id (a dataset or prj_…), object_kind?, grantee_team (id or slug), scope?: certified|default|pinned|all,
    pinned_version?, pii?: masked|unmasked, expires_at?, reason, by?}: a pending share; an owner of the sharing team
    (not the requester) decides it. Audited share_requested."""
    b = b if isinstance(b, dict) else {}
    return await _run(request_share, request.app.state.db, b, _by(b))


@router.get("/shares")
async def list_shares(request: Request, team: str | None = None, object_id: str | None = None, status: str | None = None) -> dict[str, Any]:
    """P21: shares (newest first), each with its effective status (expired past expires_at). `team` (id or slug): shares
    from or to that team. In enforce mode only shares from or to the caller's teams (admins: all)."""
    from ..authz import deps

    db = request.app.state.db

    def go() -> dict[str, Any]:
        with db.connect() as con:
            where, args = [], []
            if team:
                t = _team(con, team)
                where.append("(owner_team = ? OR grantee_team = ?)")
                args += [t["id"], t["id"]]
            if object_id:
                v = version_info(con, object_id)
                where.append("object_id = ?")
                args.append(v["root"] if v else object_id)
            rows = [effective(dict(r)) for r in con.execute("SELECT * FROM shares" + (" WHERE " + " AND ".join(where) if where else "")
                                                            + " ORDER BY requested_at DESC LIMIT 500", tuple(args))]
            if status:
                rows = [r for r in rows if r["status"] == status]
            if deps.mode() == "enforce":
                mine = deps.own_teams(con, deps.principal(request))
                if mine is not None:
                    rows = [r for r in rows if r["owner_team"] in mine or r["grantee_team"] in mine]
        return {"shares": rows, "total": len(rows)}

    return await _run(go)


@router.get("/shares/{sid}")
async def get_share(sid: str, request: Request) -> dict[str, Any]:
    def go() -> dict[str, Any]:
        with request.app.state.db.connect() as con:
            s = get(con, sid)
            return {**s, "runs": runs_on(con, s)}

    return await _run(go)


@router.post("/shares/{sid}/decide", dependencies=[require("data.share_decide", path("sid", "share"))])
async def decide(sid: str, request: Request, b: dict[str, Any]) -> dict[str, Any]:
    """P21: {approve, comment?, grant_unmasked?, by?}: an owner of the sharing team, not the requester (409 four eyes).
    A pii='unmasked' share is granted only with grant_unmasked: true."""
    b = b if isinstance(b, dict) else {}
    by = _by(b)
    try:
        return await run_in_threadpool(decide_share, request.app.state.db, sid, b.get("approve"), by,
                                       str(b.get("comment") or "")[:2000], b.get("grant_unmasked"))
    except ShareError as e:
        if e.status in (403, 409):
            G.refused(request.app.state.db, by, "share_decide", e.status, e.message, {"share_id": sid})
        raise e.http() from e


@router.post("/shares/{sid}/revoke", dependencies=[require("data.share", path("sid", "share"))])
async def revoke(sid: str, request: Request, b: dict[str, Any]) -> dict[str, Any]:
    """P21: {reason, by?}: the share stops at once (every check reads the shares rows). Runs the grantee already
    trained stay, with their pinned inputs, and are listed (runs_kept)."""
    b = b if isinstance(b, dict) else {}
    return await _run(revoke_share, request.app.state.db, sid, _by(b), str(b.get("reason") or ""))
