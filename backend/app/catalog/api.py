"""P1 routes: projects, the lineage tree, paired compare, tags and notes, champions, retention."""

from __future__ import annotations

import asyncio
import re
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from . import compare as C
from . import governance as G
from . import lineage as L
from . import retention as RT

router = APIRouter(prefix="/api")
_COMPARE_SLOTS = asyncio.Semaphore(2)
KINDS = ("dataset", "run", "version", "project")


def _who(v: Any, what: str = "by") -> str:
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
def list_projects(request: Request) -> dict[str, Any]:
    """One project per dataset root (the default), with dataset and run counts and its champion."""
    cat = request.app.state.catalog
    out = []
    for p in cat.projects():
        ch = cat.champion(p["id"])
        out.append({**p, "champion": ch and {k: ch[k] for k in ("version_id", "run_id", "decided_by", "decided_at")}})
    return {"projects": out}


@router.get("/projects/{project_id}")
def get_project(project_id: str, request: Request) -> dict[str, Any]:
    cat = request.app.state.catalog
    p = cat.project(project_id)
    if not p:
        raise HTTPException(404, f"No project '{project_id}'.")
    return {**p, "champion": cat.champion(project_id), "champion_requests": cat.champion_requests(project_id),
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


@router.post("/projects", status_code=201)
def create_project(request: Request, body: dict[str, Any]) -> dict[str, Any]:
    """{name, description?, by}: a new, empty project. Upload into it (POST /api/datasets with project_id) or move a
    dataset into it (POST /api/projects/{id}/datasets). Audited."""
    body = body if isinstance(body, dict) else {}
    by = _who(body.get("by"))
    name = _text(body, "name", 1, 120, True)
    desc = _text(body, "description", 0, 2000, False) or ""
    st = request.app.state
    p = st.catalog.create_project(name, desc, by)
    st.registry.audit(actor=by, action="project_created", details={"project_id": p["id"], "name": name})
    return {**p, "n_datasets": 0, "n_runs": 0, "champion": None}


@router.patch("/projects/{project_id}")
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


@router.post("/projects/{project_id}/datasets")
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

@router.post("/compare")
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


@router.post("/annotations/{kind}/{node_id}", status_code=201)
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


@router.delete("/annotations/{kind}/{node_id}/tags/{tag:path}")
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


@router.patch("/annotations/{kind}/{node_id}/notes/{note_id}")
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


@router.delete("/annotations/{kind}/{node_id}/notes/{note_id}")
def delete_note(kind: str, node_id: str, note_id: int, request: Request, by: str = "") -> dict[str, Any]:
    n = _note_or_404(request, kind, node_id, note_id)
    who = _who(by)
    st = request.app.state
    st.catalog.edit_note(note_id, None, who)
    st.registry.audit(actor=who, action="note_deleted", run_id=node_id if kind == "run" else None,
                      details={"kind": kind, "id": node_id, "note_id": note_id, "text": n["value"][:500]})
    return st.catalog.annotations(kind, node_id)


# -- champions -----------------------------------------------------------------------------------------------------------

@router.post("/projects/{project_id}/champion", status_code=201)
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


@router.post("/champions/{request_id}/decide")
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
    if G.same_person(by, req["requested_by"]):
        raise HTTPException(403, f"Four eyes: {req['requested_by']} asked for this champion, so someone else must decide "
                                 "(names are compared on letters and digits only; names, not authenticated identities).")
    v = st.registry.get_version(req["version_id"])
    if not v:
        raise HTTPException(409, f"Version {req['version_id']} no longer exists.")
    author = (v.get("options") or {}).get("change_by")
    if author and G.same_person(by, author):
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


# -- retention -----------------------------------------------------------------------------------------------------------

@router.get("/retention/plan")
async def retention_plan(request: Request) -> dict[str, Any]:
    """Dry run: what the policy would slim and evict, the bytes it would free, and why each pinned run is kept."""
    out = await run_in_threadpool(RT.plan, request.app)
    return {**out, "admins": {"configured": bool(G.admins()), "how": "ENSEMBLE_ADMINS (comma-separated names)",
                              "note": "names, not authenticated identities: there is no sign-in"}}


@router.post("/retention/run")
async def retention_run(request: Request, body: dict[str, Any]) -> dict[str, Any]:
    """Admins only ({by} in ENSEMBLE_ADMINS). {confirm: "retention"}, optional {run_ids, tier: slim|evict} to apply a tier
    to chosen runs; pinned runs are refused either way. Every action is audited."""
    body = body or {}
    by = _who(body.get("by"))
    if not G.is_admin(by):
        raise HTTPException(403, (f"Retention is for the admins named in ENSEMBLE_ADMINS; {by} is not one. " if G.admins() else
                                  "Retention is off: no admin is configured (set ENSEMBLE_ADMINS to a comma-separated list of names). ")
                            + "Names, not authenticated identities: there is no sign-in, names are compared on letters and digits.")
    if body.get("confirm") != "retention":
        raise HTTPException(400, 'Send {"confirm": "retention"}: this deletes predictor files. GET /api/retention/plan first.')
    tier = body.get("tier")
    if tier not in (None, "slim", "evict"):
        raise HTTPException(400, "tier is slim or evict.")
    ids = body.get("run_ids")
    if ids is not None:  # F-P2 #17: a list of known run ids, nothing else
        if not isinstance(ids, list) or not ids or not all(isinstance(i, str) and i for i in ids):
            raise HTTPException(400, 'run_ids is a non-empty list of run ids, e.g. ["run_1a2b3c4d"].')
        ids = list(dict.fromkeys(ids))
        with request.app.state.db.connect() as con:
            known = {r[0] for r in con.execute(f"SELECT id FROM runs WHERE id IN ({','.join('?' * len(ids))})", ids)}
        missing = [i for i in ids if i not in known]
        if missing:
            raise HTTPException(404, {"message": "Unknown run ids.", "unknown": missing[:50]})
    if tier and not ids:
        raise HTTPException(400, "A tier applies to chosen runs: send run_ids too.")
    if ids is not None:
        pinned = RT.pins(request.app)
        bad = {r: pinned[r] for r in ids if r in pinned}
        if bad:
            raise HTTPException(409, {"message": "Pinned runs are never slimmed or evicted.", "pinned": bad})
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
