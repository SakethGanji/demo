"""P1 routes: projects, the lineage tree, paired compare, tags and notes, champions, retention."""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from .. import config
from . import compare as C
from . import lineage as L
from . import retention as RT

router = APIRouter(prefix="/api")
KINDS = ("dataset", "run", "version", "project")


def _who(v: Any, what: str = "by") -> str:
    s = str(v or "").strip()
    if not s:
        raise HTTPException(400, f"'{what}' is required: say who you are (there is no sign-in; the name goes in the audit log).")
    if len(s) > 80:
        raise HTTPException(400, f"'{what}' is longer than 80 characters.")
    return s


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


@router.patch("/projects/{project_id}")
def rename_project(project_id: str, request: Request, body: dict[str, Any]) -> dict[str, Any]:
    cat = request.app.state.catalog
    if not cat.project(project_id):
        raise HTTPException(404, f"No project '{project_id}'.")
    name = str((body or {}).get("name") or "").strip()
    if not name or len(name) > 120:
        raise HTTPException(400, "name is 1–120 characters.")
    cat.rename_project(project_id, name, (body or {}).get("description"))
    return cat.project(project_id)


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
    if not a or not b:
        raise HTTPException(400, 'Send {"a": "<run or version id>", "b": "<run or version id>"}.')
    try:
        return await run_in_threadpool(C.compare, request.app, str(a), str(b), int((body or {}).get("B") or 1000))
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
        return cat.annotate(kind, node_id, "tag", t, by)
    note = str(body.get("note") or "").strip()
    if not note or len(note) > 2000:
        raise HTTPException(400, 'Send {"tag": …} or {"note": …} (1–2,000 characters), with {"by": …}.')
    return cat.annotate(kind, node_id, "note", note, by)


@router.delete("/annotations/{kind}/{node_id}/tags/{tag}")
def remove_tag(kind: str, node_id: str, tag: str, request: Request, by: str = "") -> dict[str, Any]:
    _exists(request, kind, node_id)
    return request.app.state.catalog.remove_tag(kind, node_id, tag, _who(by))


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
    if v["status"] != "ready":
        raise HTTPException(409, f"{v['model_name']} v{v['version']} is {v['status']}; only a ready version can be champion.")
    if v["approval"] != "approved":
        raise HTTPException(409, f"{v['model_name']} v{v['version']} is {v['approval']}: approve it in the Registry first.")
    cur = st.catalog.champion(project_id)
    if cur and cur["version_id"] == v["id"]:
        raise HTTPException(409, "This version is already the champion.")
    req = st.catalog.request_champion(project_id, v, by, str(body.get("comment") or ""))
    st.registry.audit(actor=by, action="champion_requested", version=v, comment=str(body.get("comment") or ""),
                      details={"project_id": project_id, "request_id": req["id"], "previous": cur and cur["version_id"],
                               "needs": "approval by someone other than " + by})
    return req


@router.post("/champions/{request_id}/decide")
def decide_champion(request_id: str, request: Request, body: dict[str, Any]) -> dict[str, Any]:
    """{approve: true|false, by, comment}: four eyes, the approver is not the requester. Audited."""
    from ..registry.api import identity_key

    st = request.app.state
    req = st.catalog.get_champion_request(request_id)
    if not req:
        raise HTTPException(404, f"No champion request '{request_id}'.")
    if req["status"] != "pending":
        raise HTTPException(409, f"This request is {req['status']}.")
    body = body or {}
    by = _who(body.get("by"))
    if identity_key(by) == identity_key(req["requested_by"]):
        raise HTTPException(403, f"Four eyes: {req['requested_by']} asked for this champion, so someone else must decide.")
    approve = bool(body.get("approve"))
    out = st.catalog.decide_champion(request_id, approve, by, str(body.get("comment") or ""))
    v = st.registry.get_version(req["version_id"])
    st.registry.audit(actor=by, action="champion_approved" if approve else "champion_rejected", version=v,
                      comment=str(body.get("comment") or ""), details={"project_id": req["project_id"], "request_id": request_id,
                                                                      "requested_by": req["requested_by"]})
    return out


# -- retention -----------------------------------------------------------------------------------------------------------

@router.get("/retention/plan")
async def retention_plan(request: Request) -> dict[str, Any]:
    """Dry run: what the policy would slim and evict, the bytes it would free, and why each pinned run is kept."""
    return await run_in_threadpool(RT.plan, request.app)


@router.post("/retention/run")
async def retention_run(request: Request, body: dict[str, Any]) -> dict[str, Any]:
    """Admins only ({by} in ENSEMBLE_ADMINS). {confirm: "retention"}, optional {run_ids, tier: slim|evict} to apply a tier
    to chosen runs; pinned runs are refused either way. Every action is audited."""
    body = body or {}
    by = _who(body.get("by"))
    if by.casefold() not in config.admins():
        raise HTTPException(403, f"Retention is for admins ({', '.join(sorted(config.admins()))}); {by} is not one.")
    if body.get("confirm") != "retention":
        raise HTTPException(400, 'Send {"confirm": "retention"}: this deletes predictor files. GET /api/retention/plan first.')
    tier = body.get("tier")
    if tier not in (None, "slim", "evict"):
        raise HTTPException(400, "tier is slim or evict.")
    ids = body.get("run_ids")
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
