"""P1 routes: projects, the lineage tree, paired compare, tags and notes, champions, retention."""

from __future__ import annotations

import asyncio
import re
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from ..authz import body, path, query, require, with_team  # P19
from . import compare as C
from . import governance as G
from . import lineage as L
from . import retention as RT
from . import schema as S
from . import teams_api as T

router = APIRouter(prefix="/api")
_COMPARE_SLOTS = asyncio.Semaphore(2)
KINDS = ("dataset", "run", "version", "project")


def _who(v: Any, what: str = "by") -> str:
    if v is not None and not isinstance(v, str):  # F-X3a m4: a number or an object is not a name
        raise HTTPException(400, f"'{what}' is a name (text).")
    s = str(v or "").strip()
    if not s:
        raise HTTPException(400, f"'{what}' is required: say who you are (there is no sign-in; the name goes in the audit log).")
    if len(s) > 80:
        raise HTTPException(400, f"'{what}' is longer than 80 characters.")
    if not G.identity_key(s):
        raise HTTPException(400, f"'{what}' needs at least one letter or digit (names are compared on letters and digits only).")
    return s


def _version_champion_ready(v: dict[str, Any]) -> None:
    """Only a ready, approved version that is not archived can be (or become) champion."""
    name = f"{v['model_name']} v{v['version']}"
    if v["status"] != "ready":
        raise HTTPException(409, f"{name} is {v['status']}; only a ready version can be champion.")
    if v["approval"] != "approved":
        raise HTTPException(409, f"{name} is {v['approval']}: approve it in the Registry first.")
    if v.get("stage") == "Archived":
        raise HTTPException(409, f"{name} is Archived; an archived version cannot be champion.")


def _exists(request: Request, kind: str, id_: str) -> None:
    st = request.app.state
    ok = {"dataset": lambda: st.db.get_dataset(id_), "run": lambda: st.runs.get(id_),
          "version": lambda: st.registry.get_version(id_), "project": lambda: st.catalog.project(id_)}.get(kind)
    if ok is None:
        raise HTTPException(400, f"kind is one of {', '.join(KINDS)}.")
    if not ok():
        raise HTTPException(404, f"No {kind} '{id_}'.")


# -- projects --------------------------------------------------------------------------------------------------------

@router.get("/projects")
def list_projects(request: Request, team: str | None = None) -> dict[str, Any]:
    """One project per dataset root (the default), with dataset and run counts and its champion. P18: every row carries
    its `team` ({id, slug, name}); ?team= (id or slug) lists one team's projects."""
    cat = request.app.state.catalog
    with request.app.state.db.connect() as con:
        refs = T.team_refs(con)
        tid = T.team_or_404(con, team)["id"] if team else None
    out = []
    for p in cat.projects():
        if tid and p.get("team_id") != tid:
            continue
        ch = cat.champion(p["id"])
        out.append({**p, "team": refs.get(p.get("team_id") or S.DEFAULT_TEAM),
                    "champion": ch and {k: ch[k] for k in ("version_id", "run_id", "decided_by", "decided_at")}})
    return with_team(request, {"projects": out}, "projects")


@router.get("/projects/{project_id}")
def get_project(project_id: str, request: Request) -> dict[str, Any]:
    cat = request.app.state.catalog
    p = cat.project(project_id)
    if not p:
        raise HTTPException(404, f"No project '{project_id}'.")
    with request.app.state.db.connect() as con:
        team = T.team_refs(con).get(p.get("team_id") or S.DEFAULT_TEAM)
    return {**p, "team": team, "champion": cat.champion(project_id), "champion_requests": cat.champion_requests(project_id),
            **cat.annotations("project", project_id)}


def _text(body: dict[str, Any], key: str, lo: int, hi: int, required: bool) -> str | None:
    v = body.get(key)
    if v is None and not required:
        return None
    if not isinstance(v, str):
        raise HTTPException(400, f"{key} is text ({lo}–{hi:,} characters).")
    v = v.strip()
    if not lo <= len(v) <= hi:
        raise HTTPException(400, f"{key} is {lo}–{hi:,} characters.")
    return v


@router.post("/projects", status_code=201, dependencies=[require("project.create", body("team_id", "team"))])
def create_project(request: Request, body: dict[str, Any]) -> dict[str, Any]:
    """{name, description?, team_id?, by}: a new, empty project in a team. P18: `team_id` (id or slug) defaults to the
    caller's only team; a caller in several teams must name one (400). Upload into it (POST /api/datasets with
    project_id) or move a dataset into it (POST /api/projects/{id}/datasets). Audited (project_created, team-scoped)."""
    body = body if isinstance(body, dict) else {}
    by = _who(body.get("by"))
    name = _text(body, "name", 1, 120, True)
    desc = _text(body, "description", 0, 2000, False) or ""
    st = request.app.state
    with st.db.connect() as con:
        p = T.ensure_principal(con, by)
        if body.get("team_id") not in (None, ""):
            if not isinstance(body["team_id"], str):
                raise HTTPException(400, "team_id is a team id (tm_…) or slug.")
            t = T.team_or_404(con, body["team_id"])
            if t["state"] != "active":
                raise HTTPException(409, f"Team {t['name']} is archived: a new project goes into an active team.")
            tid = t["id"]
        else:
            mine = [r for r in con.execute("SELECT t.id, t.name FROM team_members m JOIN teams t ON t.id = m.team_id"
                                           " WHERE m.principal_id = ? AND t.state = 'active' ORDER BY t.name", (p["id"],))]
            if len(mine) != 1:
                raise HTTPException(400, {"message": f"Say which team the project is for: send team_id ({by} is in "
                                                     f"{len(mine)} teams).", "teams": [dict(r) for r in mine]})
            tid = mine[0]["id"]
        team = T.team_refs(con)[tid]
    proj = st.catalog.create_project(name, desc, by, team_id=tid)
    T.audit(request.app, by, "project_created", tid, "project", proj["id"], {"project_id": proj["id"], "name": name, "team_id": tid})
    return {**proj, "team": team, "n_datasets": 0, "n_runs": 0, "champion": None}


@router.patch("/projects/{project_id}", dependencies=[require("project.manage", path("project_id", "project"))])
def rename_project(project_id: str, request: Request, body: dict[str, Any]) -> dict[str, Any]:
    cat = request.app.state.catalog
    if not cat.project(project_id):
        raise HTTPException(404, f"No project '{project_id}'.")
    body = body if isinstance(body, dict) else {}
    name = _text(body, "name", 1, 120, False)
    desc = _text(body, "description", 0, 2000, False)
    if name is None and desc is None:
        raise HTTPException(400, 'Send {"name": …} and/or {"description": …}.')
    cat.rename_project(project_id, name, desc)
    return cat.project(project_id)


@router.post("/projects/{project_id}/datasets", dependencies=[require("data.upload", path("project_id", "project"))])
def move_dataset(project_id: str, request: Request, body: dict[str, Any]) -> dict[str, Any]:
    """{dataset_id, by}: move an original dataset, every version derived from it and every run on them into this
    project. Only a root moves (a version goes with its root). Audited."""
    st = request.app.state
    if not st.catalog.project(project_id):
        raise HTTPException(404, f"No project '{project_id}'.")
    body = body if isinstance(body, dict) else {}
    by = _who(body.get("by"))
    did = body.get("dataset_id")
    if not isinstance(did, str) or not st.db.get_dataset(did):
        raise HTTPException(404, f"No dataset '{did}'.")
    with st.db.connect() as con:
        r = con.execute("SELECT parent_id, root_id, project_id FROM datasets WHERE id = ?", (did,)).fetchone()
    if r["parent_id"]:
        raise HTTPException(409, f"{did} is a version of {r['root_id'] or r['parent_id']}: move its original dataset instead.")
    if r["project_id"] == project_id:
        raise HTTPException(409, f"{did} is already in this project.")
    moved = st.catalog.move_dataset_tree(did, project_id)
    st.registry.audit(actor=by, action="dataset_moved", details={"dataset_id": did, "from": r["project_id"], "to": project_id, **moved})
    return {"dataset_id": did, "from": r["project_id"], "to": project_id, **moved}


# -- lineage -----------------------------------------------------------------------------------------------------------

@router.get("/lineage/{kind}/{node_id}")
async def get_lineage(kind: str, node_id: str, request: Request) -> dict[str, Any]:
    """The whole project tree around a node: one recursive CTE down from the project's root dataset."""
    _exists(request, kind, node_id)
    try:
        return await run_in_threadpool(L.tree, request.app, kind, node_id)
    except KeyError as e:
        raise HTTPException(404, f"No {kind} '{node_id}'.") from e


@router.get("/runs/{run_id}/children")
def run_children(run_id: str, request: Request, source: str | None = None) -> dict[str, Any]:
    """Runs branched from this one (from the edges)."""
    _exists(request, "run", run_id)
    return {"run_id": run_id, "children": request.app.state.runs.children(run_id, source)}


# -- compare -----------------------------------------------------------------------------------------------------------

@router.post("/compare", dependencies=[require("run.read", body("a", "run"), also=[("run.read", body("b", "run"))])])
async def compare(request: Request, body: dict[str, Any]) -> dict[str, Any]:
    """{a, b}: run or version ids. Paired Δ + CI on the shared original holdout rows when both descend from the same
    root with root-row mappings; otherwise a labelled 'different holdout, not paired' table."""
    a, b = (body or {}).get("a"), (body or {}).get("b")
    if not a or not b or not isinstance(a, str) or not isinstance(b, str):
        raise HTTPException(400, 'Send {"a": "<run or version id>", "b": "<run or version id>"}.')
    nb = (body or {}).get("B", 1000)
    if nb is None:
        nb = 1000
    if isinstance(nb, bool) or not isinstance(nb, (int, float)) or nb != nb or nb in (float("inf"), float("-inf")) \
            or int(nb) != nb or not 200 <= int(nb) <= 10_000:
        raise HTTPException(400, "B (bootstrap replicates) is a whole number from 200 to 10,000.")
    async with _COMPARE_SLOTS:  # at most two bootstraps at once, so compare can't starve training of CPU
        try:
            return await run_in_threadpool(C.compare, request.app, a, b, int(nb))
        except C.CompareError as e:
            raise HTTPException(400, str(e)) from e


# -- tags and notes ----------------------------------------------------------------------------------------------------

TAG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._:/-]{0,39}$")


@router.get("/annotations/{kind}/{node_id}")
def get_annotations(kind: str, node_id: str, request: Request) -> dict[str, Any]:
    _exists(request, kind, node_id)
    return request.app.state.catalog.annotations(kind, node_id)


@router.post("/annotations/{kind}/{node_id}", status_code=201, dependencies=[require("annotate", path("node_id", "node"))])
def add_annotation(kind: str, node_id: str, request: Request, body: dict[str, Any]) -> dict[str, Any]:
    """{tag} or {note}, with {by}. Tags: letters, digits, space . _ : / -, up to 40 characters ('keep' pins a run
    against retention). Notes: up to 2,000 characters."""
    _exists(request, kind, node_id)
    body = body or {}
    by = _who(body.get("by"))
    cat = request.app.state.catalog
    if body.get("tag") is not None:
        t = str(body["tag"]).strip()
        if not TAG.match(t):
            raise HTTPException(400, "A tag is 1–40 letters, digits, spaces or . _ : / - and starts with a letter or digit.")
        _ds_audit(request, kind, node_id, by, "dataset_tagged", tag=t)
        return cat.annotate(kind, node_id, "tag", t, by)
    note = str(body.get("note") or "").strip()
    if not note or len(note) > 2000:
        raise HTTPException(400, 'Send {"tag": …} or {"note": …} (1–2,000 characters), with {"by": …}.')
    _ds_audit(request, kind, node_id, by, "dataset_note_added", note=note[:500])
    return cat.annotate(kind, node_id, "note", note, by)


def _ds_audit(request: Request, kind: str, node_id: str, by: str, action: str, **details: Any) -> None:
    """DL1: tags and notes on a dataset are dataset actions too (audited with its root)."""
    if kind != "dataset":
        return
    ids = request.app.state.catalog.tree_ids(node_id)
    request.app.state.registry.audit(actor=by, action=action, details={"dataset_id": node_id, "root_id": ids[0] if ids else node_id,
                                                                       **details})


@router.delete("/annotations/{kind}/{node_id}/tags/{tag:path}", dependencies=[require("annotate", path("node_id", "node"))])
def remove_tag(kind: str, node_id: str, tag: str, request: Request, by: str = "") -> dict[str, Any]:
    """Remove a tag (case-insensitive; a tag may contain '/')."""
    _exists(request, kind, node_id)
    out = request.app.state.catalog.remove_tag(kind, node_id, tag, _who(by))
    if out is None:
        raise HTTPException(404, f"No tag '{tag}' on {kind} {node_id}.")
    _ds_audit(request, kind, node_id, by, "dataset_untagged", tag=tag)
    return out


def _note_or_404(request: Request, kind: str, node_id: str, note_id: int) -> dict[str, Any]:
    _exists(request, kind, node_id)
    n = request.app.state.catalog.note(note_id)
    if not n or n["target_kind"] != kind or n["target_id"] != node_id or n["deleted_at"]:
        raise HTTPException(404, f"No note {note_id} on {kind} {node_id}.")
    return n


@router.patch("/annotations/{kind}/{node_id}/notes/{note_id}", dependencies=[require("annotate", path("node_id", "node"))])
def edit_note(kind: str, node_id: str, note_id: int, request: Request, body: dict[str, Any]) -> dict[str, Any]:
    """{note, by}: edit a note. The old text is kept (soft-deleted) and the edit is audited."""
    n = _note_or_404(request, kind, node_id, note_id)
    body = body if isinstance(body, dict) else {}
    by = _who(body.get("by"))
    text = _text(body, "note", 1, 2000, True)
    st = request.app.state
    st.catalog.edit_note(note_id, text, by)
    st.registry.audit(actor=by, action="note_edited", run_id=node_id if kind == "run" else None,
                      details={"kind": kind, "id": node_id, "note_id": note_id, "before": n["value"][:500], "after": text[:500]})
    return st.catalog.annotations(kind, node_id)


@router.delete("/annotations/{kind}/{node_id}/notes/{note_id}", dependencies=[require("annotate", path("node_id", "node"))])
def delete_note(kind: str, node_id: str, note_id: int, request: Request, by: str = "") -> dict[str, Any]:
    n = _note_or_404(request, kind, node_id, note_id)
    who = _who(by)
    st = request.app.state
    st.catalog.edit_note(note_id, None, who)
    st.registry.audit(actor=who, action="note_deleted", run_id=node_id if kind == "run" else None,
                      details={"kind": kind, "id": node_id, "note_id": note_id, "text": n["value"][:500]})
    return st.catalog.annotations(kind, node_id)


# -- champions -----------------------------------------------------------------------------------------------------------

@router.post("/projects/{project_id}/champion", status_code=201, dependencies=[require("champion.request", path("project_id", "project"))])
def request_champion(project_id: str, request: Request, body: dict[str, Any]) -> dict[str, Any]:
    """{version_id, by, comment}: ask for a registered, ready, approved version to become the project's champion. It
    takes effect only when someone else approves it (POST /api/champions/{id}/decide). Audited."""
    st = request.app.state
    if not st.catalog.project(project_id):
        raise HTTPException(404, f"No project '{project_id}'.")
    body = body or {}
    by = _who(body.get("by"))
    v = st.registry.get_version(str(body.get("version_id") or ""))
    if not v:
        raise HTTPException(404, f"No version '{body.get('version_id')}'.")
    if st.catalog.project_of("version", v["id"]) != project_id:
        raise HTTPException(400, f"{v['model_name']} v{v['version']} was not trained in this project.")
    _version_champion_ready(v)
    cur = st.catalog.champion(project_id)
    if cur and cur["version_id"] == v["id"]:
        raise HTTPException(409, "This version is already the champion.")
    try:
        gate = G.leak_gate(request.app, v["run_id"], body.get("override_reason"), "Champion")
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
    comment = str(body.get("comment") or "")[:2000]
    req, withdrawn = st.catalog.request_champion(project_id, v, by, comment)
    for w in withdrawn:
        st.registry.audit(actor=by, action="champion_withdrawn", version=st.registry.get_version(w["version_id"]),
                          comment="withdrawn: a newer champion request", details={"project_id": project_id, "request_id": w["id"],
                                                                                  "requested_by": w["requested_by"], "superseded_by": req["id"]})
    st.registry.audit(actor=by, action="champion_requested", version=v, comment=comment,
                      details={"project_id": project_id, "request_id": req["id"], "previous": cur and cur["version_id"],
                               "needs": "approval by someone other than " + by, **gate})
    return req


@router.post("/champions/{request_id}/decide", dependencies=[require("champion.decide", path("request_id", "champion"))])
def decide_champion(request_id: str, request: Request, body: dict[str, Any]) -> dict[str, Any]:
    """{approve: true|false, by, comment}: four eyes, the approver is not the requester. Audited."""
    st = request.app.state
    req = st.catalog.get_champion_request(request_id)
    if not req:
        raise HTTPException(404, f"No champion request '{request_id}'.")
    if req["status"] != "pending":
        raise HTTPException(409, f"This request is {req['status']}.")
    body = body or {}
    by = _who(body.get("by"))
    from ..authz.policy import four_eyes  # C §1.5 (P21): every decide path goes through one check

    if four_eyes("champion.decide", {"requested_by": req["requested_by"]}, by):
        raise HTTPException(403, f"Four eyes: {req['requested_by']} asked for this champion, so someone else must decide "
                                 "(names are compared on letters and digits only; names, not authenticated identities).")
    v = st.registry.get_version(req["version_id"])
    if not v:
        raise HTTPException(409, f"Version {req['version_id']} no longer exists.")
    author = (v.get("options") or {}).get("change_by")
    if author and four_eyes("champion.decide", {"change_by": author}, by):
        raise HTTPException(403, f"Four eyes: {by} designed the change experiment behind this version ({author}).")
    approve = body.get("approve")
    if not isinstance(approve, bool):
        raise HTTPException(400, 'Send {"approve": true} or {"approve": false}.')
    gate: dict[str, Any] = {}
    if approve:
        _version_champion_ready(v)  # re-checked: it may have been archived or rejected since the request
        try:
            gate = G.leak_gate(request.app, v["run_id"], body.get("override_reason"), "Champion")
        except ValueError as e:
            raise HTTPException(409, str(e)) from e
    comment = str(body.get("comment") or "")[:2000]
    out = st.catalog.decide_champion(request_id, approve, by, comment)
    st.registry.audit(actor=by, action="champion_approved" if approve else "champion_rejected", version=v,
                      comment=comment, details={"project_id": req["project_id"], "request_id": request_id,
                                                "requested_by": req["requested_by"], **gate})
    return out


# -- retention (P24: per team, manual; §11 item 3) -----------------------------------------------------------------------

def _rt(e: Exception) -> HTTPException:
    return HTTPException(e.status, e.payload)  # type: ignore[attr-defined]


def _periods_arg(v: Any) -> dict[str, Any] | None:
    if v in (None, ""):
        return None
    if isinstance(v, str):
        import json as _json
        try:
            v = _json.loads(v)
        except ValueError:
            raise HTTPException(400, "periods is a JSON object {category: {key: days}}.")
    if not isinstance(v, dict):
        raise HTTPException(400, "periods is a JSON object {category: {key: days}}.")
    return v


def _owner_or_admin(request: Request, team: str, by: str) -> None:
    """A retention run is started by an owner of the team or a platform admin (checked in every authz mode: it is the
    person's own decision that the audit records)."""
    from ..authz.deps import principal
    from ..authz.policy import role_of

    if G.is_admin(by):
        return
    p = principal(request, by)
    with request.app.state.db.connect() as con:
        role = role_of(con, p.id, team) if p is not None else None
    if role != "owner":
        G.refused(request.app.state.db, by, "retention_run", 403, f"{by} is not an owner of {team}", {"team_id": team})
        raise HTTPException(403, {"code": "forbidden", "message": f"A retention run on {team} is started by an owner of the team or a "
                                                                   f"platform admin; {by} is {role or 'not a member'}."})


@router.get("/retention/plan", dependencies=[require("retention.plan", query("team", "team"), query("team_id", "team"), default="global")])
async def retention_plan(request: Request, team: str | None = None, team_id: str | None = None, periods: str | None = None) -> dict[str, Any]:
    """The plan, read-only. `?team=`: that team's candidates per category (versions, sources, join caches, runs,
    staging), what is kept and why, and what a legal hold covers; `periods` (JSON) overrides the team policy's periods
    for this plan. Without a team: every team's counts. No default periods: a category without one has no candidates."""
    t = team or team_id
    try:
        if t:
            return await run_in_threadpool(RT.team_plan, request.app, t, _periods_arg(periods))
        return await run_in_threadpool(RT.plan, request.app)
    except RT.RetentionError as e:
        raise _rt(e) from e


@router.post("/retention/dry-run", dependencies=[require("retention.plan", body("team", "team"))])
async def retention_dry_run(request: Request, body: dict[str, Any]) -> dict[str, Any]:
    """{team, periods?, by}: the team plan, stored under `plan_id` (24 h) and audited (`retention_dry_run`)."""
    body = body if isinstance(body, dict) else {}
    by = _who(body.get("by") or G.current_actor())
    if not body.get("team"):
        raise HTTPException(400, "team is required.")
    try:
        return await run_in_threadpool(RT.dry_run, request.app, str(body["team"]), by, _periods_arg(body.get("periods")))
    except RT.RetentionError as e:
        raise _rt(e) from e


@router.post("/retention/run", dependencies=[require("retention.run", body("team", "team"), body("team_id", "team"), default="global")])
async def retention_run(request: Request, body: dict[str, Any]) -> dict[str, Any]:
    """{team, plan_id, items?, confirm: "retention", by}: act on a dry run's items that are still candidates (an owner of
    the team or an admin). Or, admins only, {run_ids, tier: slim|evict, confirm}: the explicit slim / evict of chosen
    runs. Pinned and held items are refused either way; every action is audited. Nothing runs on a timer."""
    body = body or {}
    by = _who(body.get("by"))
    if body.get("confirm") != "retention":
        raise HTTPException(400, 'Send {"confirm": "retention"}: this deletes or requests deletes. POST /api/retention/dry-run first.')
    if body.get("plan_id"):
        with request.app.state.db.connect() as con:
            try:
                rec = RT._load_plan(con, str(body["plan_id"]))
            except RT.RetentionError as e:
                raise _rt(e) from e
            sent = body.get("team") or body.get("team_id")
            if sent and con.execute("SELECT 1 FROM teams WHERE (id = ? OR slug = ?) AND id = ?", (sent, sent, rec["team"])).fetchone() is None:
                raise HTTPException(400, f"Plan {body['plan_id']} is for team {rec['team']}, not {sent}.")
        _owner_or_admin(request, rec["team"], by)
        items = body.get("items")
        if items is not None and (not isinstance(items, list) or not all(isinstance(i, str) for i in items)):
            raise HTTPException(400, "items is a list of item ids from the plan.")
        try:
            return await run_in_threadpool(RT.run_plan, request.app, str(body["plan_id"]), by, items)
        except RT.RetentionError as e:
            raise _rt(e) from e
    if not G.is_admin(by):
        raise HTTPException(403, (f"An explicit slim / evict is for the admins named in ENSEMBLE_ADMINS; {by} is not one. " if G.admins() else
                                  "No admin is configured (ENSEMBLE_ADMINS). ") + "A team's retention runs from a dry run's plan_id.")
    tier = body.get("tier")
    if tier not in ("slim", "evict"):
        raise HTTPException(400, "Send {plan_id} (from POST /api/retention/dry-run), or {run_ids, tier: slim|evict}.")
    ids = body.get("run_ids")
    if not isinstance(ids, list) or not ids or not all(isinstance(i, str) and i for i in ids):
        raise HTTPException(400, 'run_ids is a non-empty list of run ids, e.g. ["run_1a2b3c4d"].')
    ids = list(dict.fromkeys(ids))
    with request.app.state.db.connect() as con:
        known = {r[0] for r in con.execute(f"SELECT id FROM runs WHERE id IN ({','.join('?' * len(ids))})", ids)}
    missing = [i for i in ids if i not in known]
    if missing:
        raise HTTPException(404, {"message": "Unknown run ids.", "unknown": missing[:50]})
    pinned = RT.pins(request.app)
    bad = {r: pinned[r] for r in ids if r in pinned}
    if bad:
        raise HTTPException(409, {"message": "Pinned runs are never slimmed or evicted.", "pinned": bad})
    from . import holds as H

    with request.app.state.db.connect() as con:
        held = {r: [h["case_ref"] for h in H.active(con, H.run_keys(con, r))] for r in ids}
    held = {r: v for r, v in held.items() if v}
    if held:
        raise HTTPException(409, {"code": "legal_hold", "message": "A legal hold covers these runs: " + "; ".join(
            f"{r}: {', '.join(v)}" for r, v in held.items()), "held": held})
    return await run_in_threadpool(RT.execute, request.app, by, ids, tier)


# -- the 410 guard ---------------------------------------------------------------------------------------------------------

RUN_PATH = re.compile(r"^/api/(?:[a-z-]+/)?runs/(run_[0-9a-f]+)(/.*)?$")


async def evicted_guard(request: Request, call_next):
    """Requests that would load an evicted run's predictor get 410 're-fit from config' (allow-list of stored-only routes)."""
    m = RUN_PATH.match(request.url.path)
    if m:
        sub = m.group(2) or ""
        meth = request.method
        if (meth, sub) not in RT.STORED_ONLY and not any(meth == a and sub.startswith(p) for a, p in RT.STORED_PREFIX):
            st = request.app.state
            with st.db.connect() as con:
                r = con.execute("SELECT predictor_state FROM runs WHERE id = ?", (m.group(1),)).fetchone()
            if r and r["predictor_state"] == "evicted":
                run = st.runs.get(m.group(1))
                key = RT.STORED_IF_CACHED.get(sub) if meth == "GET" else None
                if key and run and (run.get("result") or {}).get(key) and request.query_params.get("refresh") not in ("true", "1"):
                    return await call_next(request)  # the stored copy, no predictor needed
                return JSONResponse(RT.gone_message(m.group(1), RT.tombstone(run) if run else None), status_code=410)
    return await call_next(request)


def evicted_handler(request: Request, exc: RT.EvictedError) -> JSONResponse:
    return JSONResponse(RT.gone_message(exc.run_id, exc.info), status_code=410)
