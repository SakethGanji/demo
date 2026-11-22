"""P24 (plan §5.10, §6.1, C §1.4): legal holds, checked in every destructive path.

A hold names one scope: a team, a project, a dataset (its root), a version, a run or a model (a registered model name,
or one model version `mv_…`). While it is active nothing it covers is destroyed:

- dataset delete (request and decide: 409 `legal_hold` naming the case), the delete a retention run would request;
- run delete, predictor slim / evict (retention);
- GC (an unreferenced blob whose former owner is held is reported as `held`, never deleted);
- purge (blocked when any version, run or team in the reference set is held);
- retention's other actions (upload sources, join caches, staging).

What a hold covers, for an object:
- a **version**: the version, its dataset, project and team; the runs trained on it (as their input or as a join
  input) and the models built from those runs (data behind a model under hold is evidence);
- a **run**: the run, its project and team, the models built from it, and the data it trained on (the version's own
  scopes: a hold on the data keeps the runs made from it).

Placing is a platform admin's (`legal_hold.place`); releasing is another admin's (`legal_hold.release`, four eyes via
`policy.four_eyes`: not the person who placed it). Both are audited into the held object's team chain. Nothing here
runs on a timer, and a hold has no end date: it lasts until a person releases it.
"""

from __future__ import annotations

import secrets
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool

from ..authz import require
from ..authz.deps import query
from . import governance as G
from .schema import DEFAULT_TEAM, audit, now

SCOPES = ("team", "project", "dataset", "version", "run", "model")


class HoldError(Exception):
    def __init__(self, status: int, payload: Any) -> None:
        self.status, self.payload = status, payload
        super().__init__(str(payload))

    def http(self) -> HTTPException:
        return HTTPException(self.status, self.payload)


# --------------------------------------------------------------------------- what a hold names


def target(con: Any, kind: str, sid: str) -> dict[str, Any]:
    """The canonical id of a hold's scope and the team it belongs to. 404 when the object does not exist."""
    if kind not in SCOPES:
        raise HoldError(400, {"code": "bad_scope", "message": f"scope_kind is one of {', '.join(SCOPES)}."})
    sid = str(sid or "").strip()
    if not sid:
        raise HoldError(400, {"code": "bad_scope", "message": "scope_id is required."})
    r = None
    if kind == "team":
        r = con.execute("SELECT id, id AS team, name AS label FROM teams WHERE id = ? OR slug = ?", (sid, sid)).fetchone()
    elif kind == "project":
        r = con.execute("SELECT id, team_id AS team, name AS label FROM projects WHERE id = ?", (sid,)).fetchone()
    elif kind in ("dataset", "version"):
        col = "COALESCE(d.root_id, d.id)" if kind == "dataset" else "d.id"
        r = con.execute(f"SELECT {col} AS id, COALESCE(d.team_id, p.team_id) AS team, d.name AS label FROM datasets d"
                        " LEFT JOIN projects p ON p.id = d.project_id WHERE d.id = ?", (sid,)).fetchone()
    elif kind == "run":
        r = con.execute("SELECT r.id, COALESCE(r.team_id, p.team_id, d.team_id) AS team, r.name AS label FROM runs r"
                        " LEFT JOIN projects p ON p.id = r.project_id LEFT JOIN datasets d ON d.id = r.dataset_id"
                        " WHERE r.id = ?", (sid,)).fetchone()
    elif kind == "model":
        r = con.execute("SELECT CASE WHEN v.id = ? THEN v.id ELSE v.model_name END AS id,"
                        " COALESCE(r.team_id, p.team_id, d.team_id) AS team, v.model_name AS label FROM model_versions v"
                        " LEFT JOIN runs r ON r.id = v.run_id LEFT JOIN projects p ON p.id = r.project_id"
                        " LEFT JOIN datasets d ON d.id = r.dataset_id WHERE v.id = ? OR v.model_name = ?"
                        " ORDER BY v.created_at DESC LIMIT 1", (sid, sid, sid)).fetchone()
    if not r:
        raise HoldError(404, {"code": "not_found", "message": f"No {kind} {sid}."})
    return {"scope_kind": kind, "scope_id": r["id"], "team_id": r["team"] or DEFAULT_TEAM, "label": r["label"]}


# --------------------------------------------------------------------------- what covers an object


def _models_of_runs(con: Any, rids: list[str]) -> set[tuple[str, str]]:
    if not rids:
        return set()
    q = ",".join("?" * len(rids))
    out: set[tuple[str, str]] = set()
    for r in con.execute(f"SELECT id, model_name FROM model_versions WHERE run_id IN ({q})", rids):
        out |= {("model", r[0]), ("model", r[1])}
    return out


def _version_base(con: Any, vid: str) -> set[tuple[str, str]]:
    r = con.execute("SELECT d.id, COALESCE(d.root_id, d.id) AS root, d.project_id, COALESCE(d.team_id, p.team_id) AS team"
                    " FROM datasets d LEFT JOIN projects p ON p.id = d.project_id WHERE d.id = ?", (vid,)).fetchone()
    if not r:
        return set()
    out = {("version", r["id"]), ("dataset", r["root"]), ("team", r["team"] or DEFAULT_TEAM)}
    if r["project_id"]:
        out.add(("project", r["project_id"]))
    return out


def version_keys(con: Any, vid: str) -> set[tuple[str, str]]:
    """Every hold scope that covers version `vid` (see the module doc)."""
    out = _version_base(con, vid)
    rids = [r[0] for r in con.execute("SELECT id FROM runs WHERE dataset_id = ? UNION SELECT run_id FROM run_inputs"
                                      " WHERE version_id = ?", (vid, vid))]
    out |= {("run", x) for x in rids}
    return out | _models_of_runs(con, rids)


def run_keys(con: Any, rid: str) -> set[tuple[str, str]]:
    r = con.execute("SELECT r.id, r.project_id, r.dataset_id, COALESCE(r.team_id, p.team_id, d.team_id) AS team FROM runs r"
                    " LEFT JOIN projects p ON p.id = r.project_id LEFT JOIN datasets d ON d.id = r.dataset_id WHERE r.id = ?",
                    (rid,)).fetchone()
    if not r:
        return set()
    out = {("run", rid), ("team", r["team"] or DEFAULT_TEAM)} | _models_of_runs(con, [rid])
    if r["project_id"]:
        out.add(("project", r["project_id"]))
    vids = {r["dataset_id"]} | {x[0] for x in con.execute("SELECT version_id FROM run_inputs WHERE run_id = ? AND version_id IS NOT NULL",
                                                          (rid,))}
    for v in vids:
        if v:
            out |= _version_base(con, v)
    return out


def upload_keys(con: Any, up: str) -> set[tuple[str, str]]:
    r = con.execute("SELECT team_id, project_id, root_id, parent_version_id, committed_version_id FROM uploads WHERE id = ?",
                    (up,)).fetchone()
    if not r:
        return set()
    out = {("team", r["team_id"] or DEFAULT_TEAM)} | ({("project", r["project_id"])} if r["project_id"] else set())
    for v in (r["committed_version_id"], r["parent_version_id"]):
        if v:
            out |= _version_base(con, v)
    if r["root_id"]:
        out.add(("dataset", r["root_id"]))
    return out


def join_frame_keys(con: Any, fid: str) -> set[tuple[str, str]]:
    import json

    r = con.execute("SELECT version_id, inputs_json FROM join_frames WHERE id = ?", (fid,)).fetchone()
    if not r:
        return set()
    out = _version_base(con, r["version_id"])
    for x in json.loads(r["inputs_json"] or "[]"):
        if x.get("version_id"):
            out |= _version_base(con, x["version_id"])
    return out


def active(con: Any, keys: set[tuple[str, str]] | list[tuple[str, str]]) -> list[dict[str, Any]]:
    """The active holds whose scope is one of `keys`."""
    keys = sorted({k for k in keys if k[1]})
    out: list[dict[str, Any]] = []
    for i in range(0, len(keys), 200):  # bounded IN lists
        chunk = keys[i:i + 200]
        ors = " OR ".join("(scope_kind = ? AND scope_id = ?)" for _ in chunk)
        args = [a for k in chunk for a in k]
        out += [dict(r) for r in con.execute(f"SELECT * FROM legal_holds WHERE released_at IS NULL AND ({ors}) ORDER BY placed_at", args)]
    seen: set[str] = set()
    return [h for h in out if not (h["id"] in seen or seen.add(h["id"]))]


def _brief(h: dict[str, Any]) -> dict[str, Any]:
    return {k: h[k] for k in ("id", "case_ref", "scope_kind", "scope_id", "placed_by", "placed_at")}


def refusal(holds: list[dict[str, Any]], what: str) -> dict[str, Any]:
    cases = sorted({h["case_ref"] for h in holds})
    return {"code": "legal_hold", "message": f"Can't {what}: legal hold {', '.join(cases)} covers it ("
            + "; ".join(f"{h['scope_kind']} {h['scope_id']}, {h['id']}" for h in holds[:5])
            + "). A platform admin other than the one who placed it releases a hold (POST /api/legal-holds/{id}/release).",
            "cases": cases, "holds": [_brief(h) for h in holds]}


def check_versions(con: Any, vids: list[str], what: str) -> None:
    keys: set[tuple[str, str]] = set()
    for v in vids:
        keys |= version_keys(con, v)
    hs = active(con, keys)
    if hs:
        raise HoldError(409, refusal(hs, what))


def check_run(con: Any, rid: str, what: str) -> None:
    hs = active(con, run_keys(con, rid))
    if hs:
        raise HoldError(409, refusal(hs, what))


def held_versions(con: Any, vids: list[str]) -> dict[str, list[dict[str, Any]]]:
    """version id → the active holds covering it (only the held ones)."""
    out = {}
    for v in vids:
        hs = active(con, version_keys(con, v))
        if hs:
            out[v] = [_brief(h) for h in hs]
    return out


def any_active(con: Any) -> bool:
    return con.execute("SELECT 1 FROM legal_holds WHERE released_at IS NULL LIMIT 1").fetchone() is not None


# --------------------------------------------------------------------------- place / release / list


def get(con: Any, hid: str) -> dict[str, Any]:
    r = con.execute("SELECT * FROM legal_holds WHERE id = ?", (hid,)).fetchone()
    if not r:
        raise HoldError(404, {"code": "not_found", "message": f"No legal hold {hid}."})
    return {**dict(r), "active": r["released_at"] is None}


def place(db: Any, b: dict[str, Any], by: str) -> dict[str, Any]:
    if not G.is_admin(by):
        raise HoldError(403, {"code": "forbidden", "message": f"Only a platform admin (ENSEMBLE_ADMINS) places a legal hold; {by} is not one."})
    case = str(b.get("case_ref") or "").strip()[:200]
    reason = str(b.get("reason") or "").strip()[:1000]
    if not case:
        raise HoldError(400, {"code": "case_required", "message": "case_ref (the legal case or matter) is required."})
    if len(reason) < 5:
        raise HoldError(400, {"code": "reason_required", "message": "reason is required (at least 5 characters)."})
    with db.connect() as con:
        t = target(con, str(b.get("scope_kind") or ""), str(b.get("scope_id") or ""))
        dup = con.execute("SELECT id FROM legal_holds WHERE released_at IS NULL AND scope_kind = ? AND scope_id = ? AND case_ref = ?",
                          (t["scope_kind"], t["scope_id"], case)).fetchone()
        if dup:
            raise HoldError(409, {"code": "hold_exists", "message": f"{case} already holds {t['scope_kind']} {t['scope_id']} ({dup[0]}).",
                                  "id": dup[0]})
        hid = "lh_" + secrets.token_hex(5)
        con.execute("INSERT INTO legal_holds (id, scope_kind, scope_id, case_ref, reason, placed_by, placed_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (hid, t["scope_kind"], t["scope_id"], case, reason, by, now()))
        audit(con, by, "legal_hold_placed", {"hold_id": hid, "case_ref": case, **t}, comment=reason, team_id=t["team_id"],
              object_kind="legal_hold", object_id=hid)
        return {**get(con, hid), "team_id": t["team_id"], "label": t["label"]}


def release(db: Any, hid: str, by: str, reason: str) -> dict[str, Any]:
    from ..authz.policy import four_eyes

    if not G.is_admin(by):
        raise HoldError(403, {"code": "forbidden", "message": f"Only a platform admin releases a legal hold; {by} is not one."})
    if len(reason.strip()) < 5:
        raise HoldError(400, {"code": "reason_required", "message": "reason is required (at least 5 characters)."})
    with db.connect() as con:
        h = get(con, hid)
        if not h["active"]:
            raise HoldError(409, {"code": "released", "message": f"{hid} was released on {h['released_at']} by {h['released_by']}."})
        msg = four_eyes("legal_hold.release", h, by)
        if msg:
            raise HoldError(409, {"code": "four_eyes", "message": msg})
        try:
            team = target(con, h["scope_kind"], h["scope_id"])["team_id"]
        except HoldError:
            team = DEFAULT_TEAM
        con.execute("UPDATE legal_holds SET released_by = ?, released_at = ? WHERE id = ? AND released_at IS NULL", (by, now(), hid))
        audit(con, by, "legal_hold_released", {"hold_id": hid, "case_ref": h["case_ref"], "scope_kind": h["scope_kind"],
                                               "scope_id": h["scope_id"], "placed_by": h["placed_by"]},
              comment=reason[:1000], team_id=team, object_kind="legal_hold", object_id=hid)
        return get(con, hid)


def listing(db: Any, team: str | None, include_released: bool) -> list[dict[str, Any]]:
    with db.connect() as con:
        rows = [dict(r) for r in con.execute("SELECT * FROM legal_holds" + ("" if include_released else " WHERE released_at IS NULL")
                                             + " ORDER BY placed_at DESC")]
        out = []
        for h in rows:
            try:
                t = target(con, h["scope_kind"], h["scope_id"])
            except HoldError:
                t = {"team_id": None, "label": None}
            if team and t["team_id"] != team and not (h["scope_kind"] == "team" and h["scope_id"] == team):
                continue
            out.append({**h, "active": h["released_at"] is None, "team_id": t["team_id"], "label": t["label"]})
    return out


# --------------------------------------------------------------------------- routes

router = APIRouter(prefix="/api")


def _by(request: Request, b: dict[str, Any]) -> str:
    from .api import _who

    return _who(b.get("by") or G.current_actor())


async def _run(fn, *a):
    try:
        return await run_in_threadpool(fn, *a)
    except HoldError as e:
        raise e.http() from e


@router.post("/legal-holds", status_code=201, dependencies=[require("legal_hold.place")])
async def place_hold(request: Request, b: dict[str, Any]) -> dict[str, Any]:
    """{scope_kind: team|project|dataset|version|run|model, scope_id, case_ref, reason, by}: a platform admin places a
    hold. Every destructive path then refuses what it covers (409 `legal_hold` naming the case)."""
    b = b if isinstance(b, dict) else {}
    return await _run(place, request.app.state.db, b, _by(request, b))


@router.get("/legal-holds", dependencies=[require("data.read_meta", query("team", "team"), default="global")])
async def list_holds(request: Request, team: str | None = None, include_released: bool = False) -> dict[str, Any]:
    db = request.app.state.db
    if team:
        with db.connect() as con:
            r = con.execute("SELECT id FROM teams WHERE id = ? OR slug = ?", (team, team)).fetchone()
        if not r:
            raise HTTPException(404, f"No team {team}.")
        team = r[0]
    rows = await run_in_threadpool(listing, db, team, include_released)
    return {"holds": rows, "n_active": sum(1 for h in rows if h["active"])}


@router.post("/legal-holds/{hid}/release", dependencies=[require("legal_hold.release")])
async def release_hold(hid: str, request: Request, b: dict[str, Any]) -> dict[str, Any]:
    """{reason, by}: a platform admin other than the one who placed it (four eyes) releases the hold."""
    b = b if isinstance(b, dict) else {}
    return await _run(release, request.app.state.db, hid, _by(request, b), str(b.get("reason") or ""))
