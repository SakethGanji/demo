"""P18 (plan §6.1, C §1.1–1.2, §5.1): teams, principals and members, `/me`, and the team on every list row.

P19: routes carry require() (mode `audit` by default: would-be denials are audited, not refused). The data rules are enforced now: a team keeps at least one
owner once it has one, roles come from the fixed set, slugs are unique, a system principal is never a member.

Principals are created on first sight (the P01 backfill made them only for names the database already knew):
- `see_actor` (middleware): any request with an `X-Ensemble-Actor` name;
- every route here that takes a name (`by`, or a member added by name).

A new person joins `tm_default` as a member, as the backfill did for everyone who was not an admin.
"""

from __future__ import annotations

import json
import logging
import re
import secrets
import time
import unicodedata
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool

from ..authz import path, public, require, variant
from . import governance as G
from . import schema as S

router = APIRouter(prefix="/api")
#: P19: making someone owner or maintainer is a team setting (owners only); members below that are team.manage
OWNERS = variant("role", {"owner": "team.settings", "maintainer": "team.settings", "risk_reviewer": "team.settings"})
#: F-X4a (X4 M2): roles only an owner (or a platform admin) grants, changes or removes: C §1.3 team.manage is "members
#: below owner"; owners and maintainers are team settings; a risk reviewer approves the team's models (C §1.2).
SETTINGS_ROLES = ("owner", "maintainer", "risk_reviewer")
log = logging.getLogger(__name__)

ROLES = ("viewer", "member", "maintainer", "owner", "risk_reviewer")
SLUG = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?$")
EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[^@\s]{1,63}$")
SEEN_TTL_S = 300  # last_seen_at is written at most once per this many seconds per name
_seen: dict[tuple[str, str], float] = {}


def authz_mode() -> str:
    from ..authz import mode

    return mode()  # P19: audit by default


# -- principals ---------------------------------------------------------------------------------------------------------

def ensure_principal(con, name: str | None) -> dict[str, Any] | None:
    """The principal for a typed name, created on first sight. None when the name has no letter or digit."""
    name = (name or "").strip()[:80]
    k = G.identity_key(name)
    if not k:
        return None
    ts = S.now()
    r = con.execute("SELECT * FROM principals WHERE identity_key = ?", (k,)).fetchone()
    if r is None:
        kind = "system" if k == "system" else "person"
        con.execute("INSERT OR IGNORE INTO principals (id, display_name, identity_key, kind, is_admin, created_at, last_seen_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?)", (S.principal_id_for(k), name, k, kind, int(G.is_admin(name)), ts, ts))
        r = con.execute("SELECT * FROM principals WHERE identity_key = ?", (k,)).fetchone()
        if kind == "person":
            con.execute("INSERT OR IGNORE INTO team_members (team_id, principal_id, role, source, added_by, added_at)"
                        " VALUES (?, ?, 'member', 'manual', 'system', ?)", (S.DEFAULT_TEAM, r["id"], ts))
            _audit(con, "system", "principal_created", S.DEFAULT_TEAM, "principal", r["id"],
                   {"principal_id": r["id"], "display_name": name, "team_id": S.DEFAULT_TEAM, "role": "member",
                    "via": "first sight"})
    else:
        adm = int(G.is_admin(name))  # ENSEMBLE_ADMINS may change between starts
        con.execute("UPDATE principals SET last_seen_at = ?, is_admin = ? WHERE id = ?", (ts, adm, r["id"]))
    return dict(con.execute("SELECT * FROM principals WHERE id = ?", (r["id"],)).fetchone())


def _seen_recently(db_path: str, name: str) -> bool:
    k = (db_path, G.identity_key(name))
    t = time.monotonic()
    if not k[1] or t - _seen.get(k, -1e9) < SEEN_TTL_S:
        return True
    _seen[k] = t
    return False


async def see_actor(request: Request, call_next):
    """First sight: the X-Ensemble-Actor name gets a principal (and tm_default membership) before the route runs."""
    name = (request.headers.get("x-ensemble-actor") or "").strip()[:80]
    if name:
        db = request.app.state.db
        if not _seen_recently(str(db.path), name):
            def _go() -> None:
                with db.connect() as con:
                    ensure_principal(con, name)
            try:
                await run_in_threadpool(_go)
            except Exception:  # noqa: BLE001 — a busy DB must not fail the request; the next one retries
                _seen.pop((str(db.path), G.identity_key(name)), None)
    return await call_next(request)


def _principal_out(r: Any) -> dict[str, Any]:
    d = dict(r)
    return {k: d.get(k) for k in ("id", "display_name", "identity_key", "kind", "email", "idp", "created_at", "last_seen_at",
                                  "disabled_at")} | {"is_admin": bool(d.get("is_admin"))}


def _principal_or_404(con, ref: str) -> Any:
    """A principal by id (pr_…) or by name (matched on its identity key)."""
    r = con.execute("SELECT * FROM principals WHERE id = ?", (ref,)).fetchone()
    if r is None and G.identity_key(ref):
        r = con.execute("SELECT * FROM principals WHERE identity_key = ?", (G.identity_key(ref),)).fetchone()
    if r is None:
        raise HTTPException(404, f"No principal '{ref}'.")
    return r


def _teams_of(con, principal_id: str) -> list[dict[str, Any]]:
    rows = con.execute("SELECT t.id, t.slug, t.name, t.state, m.role, m.source, m.added_at FROM team_members m"
                       " JOIN teams t ON t.id = m.team_id WHERE m.principal_id = ? ORDER BY t.id = ? DESC, t.name",
                       (principal_id, S.DEFAULT_TEAM)).fetchall()
    return [dict(r) for r in rows]


# -- audit --------------------------------------------------------------------------------------------------------------

def _audit(con, actor: str, action: str, team_id: str, object_kind: str, object_id: str, details: dict[str, Any],
           principal_id: str | None = None) -> None:
    """A team-scoped audit row (P19: hash-chained by schema.audit)."""
    S.audit(con, actor or "system", action, details, team_id=team_id, object_kind=object_kind, object_id=object_id,
            principal_id=principal_id)


def audit(app: Any, actor: str, action: str, team_id: str, object_kind: str, object_id: str, details: dict[str, Any]) -> None:
    with app.state.db.connect() as con:
        p = ensure_principal(con, actor)
        _audit(con, actor, action, team_id, object_kind, object_id, details, p and p["id"])


# -- input --------------------------------------------------------------------------------------------------------------

def _who(v: Any) -> str:
    s = str(v or G.current_actor() or "").strip()
    if not s:
        raise HTTPException(400, "'by' is required: say who you are (there is no sign-in; the name goes in the audit log).")
    if len(s) > 80:
        raise HTTPException(400, "'by' is longer than 80 characters.")
    if not G.identity_key(s):
        raise HTTPException(400, "'by' needs at least one letter or digit (names are compared on letters and digits only).")
    return s


def _text(body: dict[str, Any], key: str, lo: int, hi: int) -> str | None:
    v = body.get(key)
    if v is None:
        return None
    if not isinstance(v, str) or not lo <= len(v.strip()) <= hi:
        raise HTTPException(400, f"{key} is text of {lo}–{hi:,} characters.")
    return v.strip()


def _role(v: Any) -> str:
    if v not in ROLES:
        raise HTTPException(400, f"role is one of {', '.join(ROLES)}.")
    return v


def slugify(name: str) -> str:
    """"Credit Risk" → "credit-risk"; accents folded; a name with no Latin letter gets a random slug."""
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")[:40].strip("-")
    return s or "team-" + secrets.token_hex(3)


# -- teams --------------------------------------------------------------------------------------------------------------

def team_or_404(con, ref: str) -> Any:
    """A team by id (tm_…) or slug."""
    r = con.execute("SELECT * FROM teams WHERE id = ? OR slug = ?", (ref, ref)).fetchone()
    if r is None:
        raise HTTPException(404, f"No team '{ref}'.")
    return r


def _team_out(con, r: Any, me: str | None = None) -> dict[str, Any]:
    d = dict(r)
    try:
        d["policy"] = json.loads(d.get("policy") or "{}")
    except ValueError:
        d["policy"] = {}
    roles = dict(con.execute("SELECT role, COUNT(*) FROM team_members WHERE team_id = ? GROUP BY role", (d["id"],)).fetchall())
    d["n_members"] = sum(roles.values())
    d["n_owners"] = roles.get("owner", 0)
    d["n_projects"] = con.execute("SELECT COUNT(*) FROM projects WHERE team_id = ?", (d["id"],)).fetchone()[0]
    if me is not None:
        k = G.identity_key(me)
        m = con.execute("SELECT m.role FROM team_members m JOIN principals p ON p.id = m.principal_id"
                        " WHERE m.team_id = ? AND p.identity_key = ?", (d["id"], k)).fetchone() if k else None
        d["my_role"] = m[0] if m else None
    return d


def _members(con, team_id: str) -> list[dict[str, Any]]:
    rows = con.execute("SELECT p.id AS principal_id, p.display_name, p.identity_key, p.kind, p.email, p.is_admin, m.role, m.source,"
                       " m.added_by, m.added_at FROM team_members m JOIN principals p ON p.id = m.principal_id"
                       " WHERE m.team_id = ? ORDER BY CASE m.role WHEN 'owner' THEN 0 WHEN 'maintainer' THEN 1 WHEN 'member' THEN 2"
                       " WHEN 'risk_reviewer' THEN 3 ELSE 4 END, p.display_name", (team_id,)).fetchall()
    return [{**dict(r), "is_admin": bool(r["is_admin"])} for r in rows]


def team_refs(con) -> dict[str, dict[str, str]]:
    return {r[0]: {"id": r[0], "slug": r[1], "name": r[2]} for r in con.execute("SELECT id, slug, name FROM teams")}


@router.get("/me", dependencies=[public("the caller's own principal and teams")])
def me(request: Request, by: str | None = None) -> dict[str, Any]:
    """Who the API thinks you are (the X-Ensemble-Actor name, or ?by=): the principal, its teams with roles, and the
    authorisation mode. `default_team` is your only team when you are in exactly one (where POST /projects puts a
    project without team_id)."""
    name = (by or G.current_actor() or "").strip()[:80]
    mode = authz_mode()
    if not G.identity_key(name):
        return {"principal": None, "auth": None, "teams": [], "default_team": None, "authz_mode": mode,
                "detail": "No name: send the X-Ensemble-Actor header (the UI's 'Acting as') or ?by=."}
    with request.app.state.db.connect() as con:
        p = ensure_principal(con, name)
        teams = _teams_of(con, p["id"])
    live = [t for t in teams if t["state"] == "active"]
    return {"principal": _principal_out(p), "auth": "typed" if p["kind"] == "person" else p["kind"], "teams": teams,
            "default_team": live[0]["id"] if len(live) == 1 else None, "authz_mode": mode}


@router.get("/teams", dependencies=[public("team directory: names and counts; members need team.read")])
def list_teams(request: Request, all: bool = False, mine: bool = False) -> dict[str, Any]:
    """Teams with member, owner and project counts. `my_role` is the caller's role (X-Ensemble-Actor). all=true also
    lists archived teams; mine=true only the caller's."""
    me_ = G.current_actor()
    with request.app.state.db.connect() as con:
        rows = con.execute("SELECT * FROM teams" + ("" if all else " WHERE state = 'active'") + " ORDER BY id = ? DESC, name",
                           (S.DEFAULT_TEAM,)).fetchall()
        out = [_team_out(con, r, me_ or "") for r in rows]
    if mine:
        out = [t for t in out if t["my_role"]]
    return {"teams": out}


@router.post("/teams", status_code=201, dependencies=[require("team.create")])
def create_team(request: Request, body: dict[str, Any]) -> dict[str, Any]:
    """{name, slug?, description?, by}: a new team. Its creator becomes its owner. The slug defaults to the name in
    lower case with dashes. Audited (team_created)."""
    body = body if isinstance(body, dict) else {}
    by = _who(body.get("by"))
    name = _text(body, "name", 1, 80)
    if name is None:
        raise HTTPException(400, 'Send {"name": …} (1–80 characters).')
    desc = _text(body, "description", 0, 2000) or ""
    slug = body.get("slug") if body.get("slug") is not None else slugify(name)
    if not isinstance(slug, str) or not SLUG.match(slug):
        raise HTTPException(400, "slug is 1–40 lower-case letters, digits and dashes, starting and ending with a letter or digit.")
    tid = "tm_" + secrets.token_hex(4)
    ts = S.now()
    with request.app.state.db.connect() as con:
        if con.execute("SELECT 1 FROM teams WHERE slug = ?", (slug,)).fetchone():
            raise HTTPException(409, f"A team with the slug '{slug}' exists: choose another slug.")
        p = ensure_principal(con, by)
        con.execute("INSERT INTO teams (id, slug, name, description, created_at, created_by) VALUES (?, ?, ?, ?, ?, ?)",
                    (tid, slug, name, desc, ts, by))
        con.execute("INSERT INTO team_members (team_id, principal_id, role, source, added_by, added_at) VALUES (?, ?, 'owner', 'manual', ?, ?)",
                    (tid, p["id"], by, ts))
        _audit(con, by, "team_created", tid, "team", tid, {"team_id": tid, "slug": slug, "name": name, "owner": p["id"]}, p["id"])
        out = _team_out(con, team_or_404(con, tid), by)
        out["members"] = _members(con, tid)
    return out


@router.get("/teams/{team}", dependencies=[require("team.read", path("team", "team"))])
def get_team(team: str, request: Request) -> dict[str, Any]:
    """A team (by id or slug) with its members and projects."""
    with request.app.state.db.connect() as con:
        r = team_or_404(con, team)
        out = _team_out(con, r, G.current_actor() or "")
        out["members"] = _members(con, r["id"])
        out["projects"] = [dict(p) for p in con.execute(
            "SELECT p.id, p.name, p.state, p.created_at, (SELECT COUNT(*) FROM dataset_roots d WHERE d.project_id = p.id"
            " AND d.state <> 'deleted') AS n_datasets FROM projects p WHERE p.team_id = ? ORDER BY p.created_at", (r["id"],))]
    return out


@router.patch("/teams/{team}", dependencies=[require("team.settings", path("team", "team"))])
def update_team(team: str, request: Request, body: dict[str, Any]) -> dict[str, Any]:
    """{name?, description?, policy?, by}: rename, describe or set the team policy (a JSON object; §6.4 keys). Audited
    with the before and after of every changed field (team_updated)."""
    body = body if isinstance(body, dict) else {}
    by = _who(body.get("by"))
    new: dict[str, Any] = {}
    if (v := _text(body, "name", 1, 80)) is not None:
        new["name"] = v
    if (v := _text(body, "description", 0, 2000)) is not None:
        new["description"] = v
    if "policy" in body:
        if not isinstance(body["policy"], dict):
            raise HTTPException(400, "policy is a JSON object.")
        pol = json.dumps(body["policy"], sort_keys=True, default=str)
        if len(pol) > 20_000:
            raise HTTPException(400, "policy is at most 20,000 characters of JSON.")
        new["policy"] = pol
    if not new:
        raise HTTPException(400, 'Send {"name": …}, {"description": …} and/or {"policy": {…}}.')
    with request.app.state.db.connect() as con:
        r = team_or_404(con, team)
        diff = {k: {"before": r[k], "after": v} for k, v in new.items() if r[k] != v}
        if diff:
            con.execute(f"UPDATE teams SET {', '.join(f'{k} = ?' for k in diff)} WHERE id = ?", (*[new[k] for k in diff], r["id"]))
            p = ensure_principal(con, by)
            _audit(con, by, "team_updated", r["id"], "team", r["id"], {"team_id": r["id"], "changes": diff}, p["id"])
        return _team_out(con, team_or_404(con, r["id"]), by)


# -- members ------------------------------------------------------------------------------------------------------------

@router.get("/teams/{team}/members", dependencies=[require("team.read", path("team", "team"))])
def list_members(team: str, request: Request) -> dict[str, Any]:
    with request.app.state.db.connect() as con:
        r = team_or_404(con, team)
        return {"team_id": r["id"], "members": _members(con, r["id"])}


@router.post("/teams/{team}/members", status_code=201, dependencies=[require("team.manage", path("team", "team"), when=OWNERS)])
def add_member(team: str, request: Request, body: dict[str, Any]) -> dict[str, Any]:
    """{principal | name | email, role, by}: add a person to the team. A name not seen before gets a principal now.
    An email must belong to a known principal. Audited (team_member_added). Returns the member list."""
    body = body if isinstance(body, dict) else {}
    by = _who(body.get("by"))
    role = _role(body.get("role", "member"))
    refs = [k for k in ("principal", "name", "email") if body.get(k) not in (None, "")]
    if len(refs) != 1 or not isinstance(body[refs[0]], str):
        raise HTTPException(400, 'Send one of {"principal": "pr_…"}, {"name": …} or {"email": …}, with {"role": …}.')
    _gate_first(request, team, refs[0], body[refs[0]], role, by)  # F-X4a (X4 M2)
    with request.app.state.db.connect() as con:
        t = team_or_404(con, team)
        if t["state"] != "active":
            raise HTTPException(409, f"Team {t['name']} is archived.")
        ref = body[refs[0]].strip()
        if refs[0] == "principal":
            p = _principal_or_404(con, ref)
        elif refs[0] == "email":
            p = con.execute("SELECT * FROM principals WHERE lower(email) = lower(?)", (ref,)).fetchone()
            if p is None:
                raise HTTPException(404, f"No principal with the email '{ref}': add them by name, then set the email "
                                         "(PATCH /api/principals/{id}).")
        else:
            if len(ref) > 80 or not G.identity_key(ref):
                raise HTTPException(400, "name is 1–80 characters with at least one letter or digit.")
            p = ensure_principal(con, ref)
        if p["kind"] == "system":
            raise HTTPException(400, f"{p['display_name']} is the system principal; it is never a team member.")
        if p["disabled_at"]:
            raise HTTPException(409, f"{p['display_name']} is disabled.")
        cur = con.execute("SELECT role FROM team_members WHERE team_id = ? AND principal_id = ?", (t["id"], p["id"])).fetchone()
        if cur:
            raise HTTPException(409, f"{p['display_name']} is already {cur[0]} of {t['name']}: change the role with "
                                     f"PATCH /api/teams/{t['id']}/members/{p['id']}.")
        actor = ensure_principal(con, by)
        con.execute("INSERT INTO team_members (team_id, principal_id, role, source, added_by, added_at) VALUES (?, ?, ?, 'manual', ?, ?)",
                    (t["id"], p["id"], role, by, S.now()))
        _audit(con, by, "team_member_added", t["id"], "principal", p["id"],
               {"team_id": t["id"], "principal_id": p["id"], "display_name": p["display_name"], "role": role}, actor["id"])
        return {"team_id": t["id"], "members": _members(con, t["id"])}


def _gate_first(request: Request, team: str, kind: str, ref: Any, after: str | None, by: str) -> None:
    """Run `_member_gate` on read-only lookups, before the handler's own connection writes anything (the gate may
    audit a denial on a connection of its own). `kind`: principal (id or name) | name | email."""
    if not isinstance(ref, str) or not ref.strip():
        return
    ref = ref.strip()
    with request.app.state.db.connect() as con:
        t = team_or_404(con, team)
        if kind == "email":
            r = con.execute("SELECT * FROM principals WHERE lower(email) = lower(?)", (ref,)).fetchone()
        else:
            r = con.execute("SELECT * FROM principals WHERE id = ?", (ref,)).fetchone()
            if r is None and G.identity_key(ref):
                r = con.execute("SELECT * FROM principals WHERE identity_key = ?", (G.identity_key(ref),)).fetchone()
        target = {"id": r["id"], "identity_key": r["identity_key"]} if r else {"id": None, "identity_key": G.identity_key(ref)}
        m = con.execute("SELECT role FROM team_members WHERE team_id = ? AND principal_id = ?", (t["id"], target["id"])).fetchone() \
            if target["id"] else None
        t = {"id": t["id"], "name": t["name"]}
    _member_gate(request, t, target, m[0] if m else None, after, by)


def _member_gate(request: Request, t: Any, target: Any, before: str | None, after: str | None, by: str) -> None:
    """F-X4a (X4 M2), the checks a route-level variant cannot make (it sees only the body's new role):
    - nobody grants themselves a role or changes their own (403 `self_grant`, every mode: like four eyes);
    - the target's CURRENT role counts too: changing or removing an owner, a maintainer or a risk reviewer is
      team.settings (owner or platform admin), as is granting one; below that team.manage (the route's check).
    The decision follows the mode exactly like require() (deps.check: would-deny in audit, 403 in enforce)."""
    from ..authz import deps

    if deps.mode() == "off":
        return
    p = deps.principal(request, by or None)
    if p is not None and after is not None and (target["id"] == p.id or target.get("identity_key") == p.identity_key):
        raise HTTPException(403, {"code": "self_grant", "message": f"{p.display_name} cannot give themselves a role in "
                                  f"{t['name']}: another owner (or a platform admin) must make the change."})
    if before in SETTINGS_ROLES or after in SETTINGS_ROLES:
        deps.check(request, "team.settings", "team", t["id"], by or None)


def _member_or_404(con, t: Any, ref: str) -> tuple[Any, str]:
    p = _principal_or_404(con, ref)
    m = con.execute("SELECT role FROM team_members WHERE team_id = ? AND principal_id = ?", (t["id"], p["id"])).fetchone()
    if not m:
        raise HTTPException(404, f"{p['display_name']} is not a member of {t['name']}.")
    return p, m[0]


def _keep_an_owner(con, t: Any, p: Any, role_before: str) -> None:
    if role_before != "owner":
        return
    n = con.execute("SELECT COUNT(*) FROM team_members WHERE team_id = ? AND role = 'owner'", (t["id"],)).fetchone()[0]
    if n <= 1:
        raise HTTPException(409, f"{p['display_name']} is the last owner of {t['name']}: make someone else owner first.")


@router.patch("/teams/{team}/members/{principal}", dependencies=[require("team.manage", path("team", "team"), when=OWNERS)])
def change_member(team: str, principal: str, request: Request, body: dict[str, Any]) -> dict[str, Any]:
    """{role, by}: change a member's role (principal by id or name). The last owner cannot be demoted. Audited."""
    body = body if isinstance(body, dict) else {}
    by = _who(body.get("by"))
    role = _role(body.get("role"))
    _gate_first(request, team, "principal", principal, role, by)  # F-X4a (X4 M2)
    with request.app.state.db.connect() as con:
        t = team_or_404(con, team)
        p, before = _member_or_404(con, t, principal)
        if before != role:
            if role != "owner":
                _keep_an_owner(con, t, p, before)
            con.execute("UPDATE team_members SET role = ? WHERE team_id = ? AND principal_id = ?", (role, t["id"], p["id"]))
            actor = ensure_principal(con, by)
            _audit(con, by, "team_member_role_changed", t["id"], "principal", p["id"],
                   {"team_id": t["id"], "principal_id": p["id"], "display_name": p["display_name"], "from": before, "to": role},
                   actor["id"])
        return {"team_id": t["id"], "members": _members(con, t["id"])}


@router.delete("/teams/{team}/members/{principal}", dependencies=[require("team.manage", path("team", "team"))])
def remove_member(team: str, principal: str, request: Request, by: str = "") -> dict[str, Any]:
    """?by=: remove a member. The last owner cannot be removed. Audited (team_member_removed)."""
    who = _who(by)
    _gate_first(request, team, "principal", principal, None, who)  # F-X4a (X4 M2)
    with request.app.state.db.connect() as con:
        t = team_or_404(con, team)
        p, before = _member_or_404(con, t, principal)
        _keep_an_owner(con, t, p, before)
        con.execute("DELETE FROM team_members WHERE team_id = ? AND principal_id = ?", (t["id"], p["id"]))
        actor = ensure_principal(con, who)
        _audit(con, who, "team_member_removed", t["id"], "principal", p["id"],
               {"team_id": t["id"], "principal_id": p["id"], "display_name": p["display_name"], "role": before}, actor["id"])
        return {"team_id": t["id"], "members": _members(con, t["id"])}


# -- principals ---------------------------------------------------------------------------------------------------------

@router.get("/principals", dependencies=[public("people directory for the member picker")])
def list_principals(request: Request, q: str = "", kind: str | None = None, limit: int = 200) -> dict[str, Any]:
    """People (and service / system principals) by name or email: the member picker."""
    limit = max(1, min(int(limit), 1000))
    args: list[Any] = []
    where = []
    if q.strip():
        where.append("(identity_key LIKE ? OR lower(COALESCE(email, '')) LIKE ?)")
        args += [f"%{G.identity_key(q)}%", f"%{q.strip().lower()}%"]
    if kind:
        where.append("kind = ?")
        args.append(kind)
    with request.app.state.db.connect() as con:
        rows = con.execute("SELECT *, (SELECT COUNT(*) FROM team_members m WHERE m.principal_id = principals.id) AS n_teams"
                           " FROM principals" + (" WHERE " + " AND ".join(where) if where else "")
                           + " ORDER BY display_name LIMIT ?", (*args, limit)).fetchall()
    return {"principals": [{**_principal_out(r), "n_teams": r["n_teams"]} for r in rows]}


@router.get("/principals/{principal}", dependencies=[public("people directory: one principal and its team names")])
def get_principal(principal: str, request: Request) -> dict[str, Any]:
    with request.app.state.db.connect() as con:
        p = _principal_or_404(con, principal)
        return {**_principal_out(p), "teams": _teams_of(con, p["id"])}


@router.patch("/principals/{principal}", dependencies=[require("principal.manage", path("principal", "principal"))])
def update_principal(principal: str, request: Request, body: dict[str, Any]) -> dict[str, Any]:
    """{email?, display_name?, by}: set the email (for adding members by email) or re-spell the display name. A new
    display name must fold to the same identity key (the four-eyes key never changes). Audited."""
    body = body if isinstance(body, dict) else {}
    by = _who(body.get("by"))
    with request.app.state.db.connect() as con:
        p = _principal_or_404(con, principal)
        new: dict[str, Any] = {}
        if "email" in body:
            e = body["email"]
            if e not in (None, "") and (not isinstance(e, str) or not EMAIL.match(e.strip())):
                raise HTTPException(400, "email looks like name@example.com.")
            new["email"] = e.strip() if e else None
        if body.get("display_name") is not None:
            dn = _text(body, "display_name", 1, 80)
            if G.identity_key(dn) != p["identity_key"]:
                raise HTTPException(409, f"'{dn}' is a different name from '{p['display_name']}' (names are compared on letters and "
                                         "digits only): add the other person by that name instead.")
            new["display_name"] = dn
        if not new:
            raise HTTPException(400, 'Send {"email": …} and/or {"display_name": …}.')
        diff = {k: {"before": p[k], "after": v} for k, v in new.items() if p[k] != v}
        if diff:
            con.execute(f"UPDATE principals SET {', '.join(f'{k} = ?' for k in diff)} WHERE id = ?", (*[new[k] for k in diff], p["id"]))
            actor = ensure_principal(con, by)
            for t in _teams_of(con, p["id"]) or [{"id": S.DEFAULT_TEAM}]:
                _audit(con, by, "principal_updated", t["id"], "principal", p["id"], {"principal_id": p["id"], "changes": diff},
                       actor["id"])
        p = _principal_or_404(con, p["id"])
        return {**_principal_out(p), "teams": _teams_of(con, p["id"])}
