"""P21 (C §1.4, §4.4): certifications of one dataset version.

- A certification belongs to a **version**, never to the dataset: a new version is not certified automatically.
- **Request** (`data.certify`, maintainer): the dataset's active contract must pass on the version (no failed `block`
  rule; 409 contract_failed / contract_required) and its PII tags must be reviewed (no suggested tag left; 409
  pii_review_required). The request pins the contract version and its result.
- **Decide**: `certified` by an owner of the team, `approved_for_model` by a risk reviewer of the team; never the
  requester (409 four eyes, through `authz.policy.four_eyes`: names folded for punctuation, accents and confusables,
  and the request's principal too). The contract is checked again at the decision.
- An approval **supersedes** the dataset's earlier live certification of the same level (it stays readable by the runs
  that used it) and **expires** after the team policy `certify_days` (default 365).
- **`require_certified_inputs`** (team policy: false | true | "certified" | "approved_for_model"): moving a model to
  Production needs every input version of its run to hold a live certification (true / "certified": either level;
  "approved_for_model": that level only). Otherwise 409 with the list. The certifications found are recorded on the
  model version (`result.input_certifications`) and in the audit row, so a later expiry does not rewrite history.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool

from ..authz import path, require
from . import contracts as C
from . import governance as G
from .schema import audit, now

router = APIRouter(prefix="/api")

LEVELS = ("certified", "approved_for_model")
DECIDE_ACTION = {"certified": "data.certify_decide_certified", "approved_for_model": "data.certify_decide_model"}


class CertError(ValueError):
    def __init__(self, status: int, code: str, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.status, self.code, self.message, self.extra = status, code, message, extra

    def http(self) -> HTTPException:
        return HTTPException(self.status, {"code": self.code, "message": self.message, **self.extra})


def _policy(con: Any, team_id: str | None) -> dict[str, Any]:
    r = con.execute("SELECT policy FROM teams WHERE id = ?", (team_id or "tm_default",)).fetchone()
    try:
        return json.loads(r[0]) if r and r[0] else {}
    except ValueError:
        return {}


def effective(r: dict[str, Any]) -> dict[str, Any]:
    out = dict(r)
    for k in ("contract_result",):
        if isinstance(out.get(k), str):
            try:
                out[k] = json.loads(out[k])
            except ValueError:
                pass
    if out["status"] == "active" and out.get("expires_at") and out["expires_at"] <= now():
        out["status"] = "expired"
    return out


def get(con: Any, cid: str) -> dict[str, Any]:
    r = con.execute("SELECT * FROM certifications WHERE id = ?", (cid,)).fetchone()
    if not r:
        raise CertError(404, "certification_not_found", f"No certification '{cid}'.")
    return effective(dict(r))


def _version(con: Any, vid: str) -> dict[str, Any]:
    r = con.execute("SELECT d.id, COALESCE(d.root_id, d.id) AS root, d.state, d.storage, d.version_no,"
                    " COALESCE(d.team_id, p.team_id) AS team FROM datasets d LEFT JOIN projects p ON p.id = d.project_id"
                    " WHERE d.id = ?", (vid,)).fetchone()
    if not r:
        raise CertError(404, "not_found", f"No dataset version '{vid}'.")
    return dict(r)


def _contract_ok(db: Any, vid: str, root: str) -> tuple[dict[str, Any], dict[str, Any]]:
    with db.connect() as con:
        c = C.active(con, root)
    if c is None:
        raise CertError(409, "contract_required", "A certification needs the dataset's contract to pass, and it has none: "
                                                  "freeze one from a version (POST /api/datasets/{id}/contract/from-version/{v}).")
    try:
        res = C.evaluate_version(db, vid, c)
    except C.ContractError as e:
        raise CertError(e.status, e.code, e.message) from e
    assert res is not None
    if res["status"] == "block":
        raise CertError(409, "contract_failed", f"Contract v{c['version']} blocks on {vid}: "
                                                + "; ".join(r["message"] for r in res["rules"] if not r["ok"] and r["severity"] == "block")[:1000],
                        contract_result=res)
    return c, res


def _pii_pending(db: Any, vid: str, root: str) -> list[dict[str, Any]]:
    """Suggested (unreviewed) PII tags of the version's tables. The profiler runs first, so no column is unseen."""
    from ..lake import mask as M

    with db.connect() as con:
        names = [r[0] for r in con.execute("SELECT table_name FROM version_tables WHERE version_id = ?", (vid,))]
    for n in names:
        try:
            M.ensure(vid, n)
        except Exception:
            import logging

            logging.getLogger(__name__).exception("PII suggestions for %s / %s", vid, n)
    with db.connect() as con:
        q = ",".join("?" * len(names)) or "NULL"
        return [dict(r) for r in con.execute(f"SELECT table_name, column_name, tag, mask FROM column_tags WHERE root_id = ? AND"
                                             f" source = 'suggested' AND tag <> 'none' AND table_name IN ({q})", (root, *names))]


def request_cert(db: Any, dataset_id: str, b: dict[str, Any], by: str) -> dict[str, Any]:
    vid = str(b.get("version_id") or dataset_id)
    level = b.get("level") or "certified"
    if level not in LEVELS:
        raise CertError(400, "bad_level", "level is certified or approved_for_model.")
    reason = str(b.get("reason") or "").strip()
    if len(reason) < 3:
        raise CertError(400, "reason_required", "Say why this version should be certified (reason).")
    with db.connect() as con:
        v = _version(con, vid)
        if v["root"] != _version(con, dataset_id)["root"]:
            raise CertError(404, "not_found", f"{vid} is not a version of {dataset_id}.")
        if v["state"] == "deleted":
            raise CertError(409, "version_deleted", f"{vid} is deleted.")
        dup = con.execute("SELECT id, status, expires_at FROM certifications WHERE version_id = ? AND level = ? AND status IN"
                          " ('pending', 'active')", (vid, level)).fetchall()
        for d in dup:
            e = effective(dict(d))
            if e["status"] in ("pending", "active"):
                raise CertError(409, "certification_exists", f"{vid} already has a {e['status']} {level} certification ({e['id']}).",
                                certification_id=e["id"])
    c, res = _contract_ok(db, vid, v["root"])
    pend = _pii_pending(db, vid, v["root"])
    if pend:
        raise CertError(409, "pii_review_required", f"{len(pend)} PII tag(s) are still suggestions: a maintainer reviews them "
                                                    "first (PUT /api/datasets/{id}/pii, e.g. {confirm: 'suggested'}).",
                        pending=pend)
    cid = "cert_" + uuid.uuid4().hex[:10]
    with db.connect() as con:
        con.execute("INSERT INTO certifications (id, root_id, version_id, level, status, contract_id, contract_result, requested_by,"
                    " requested_at, reason) VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?)",
                    (cid, v["root"], vid, level, c["id"], json.dumps(res, default=str), by, now(), reason))
        audit(con, by, "certification_requested", {"dataset_id": vid, "root_id": v["root"], "certification_id": cid, "level": level,
                                                   "contract_id": c["id"], "contract_version": c["version"],
                                                   "contract_status": res["status"], "reason": reason},
              comment=reason, object_kind="dataset", object_id=vid)
        return get(con, cid)


def decide_cert(db: Any, cid: str, approve: Any, by: str, comment: str) -> dict[str, Any]:
    from ..authz.policy import four_eyes

    if not isinstance(approve, bool):
        raise CertError(400, "approve_required", 'Send {"approve": true} or {"approve": false}.')
    with db.connect() as con:
        r = get(con, cid)
    if r["status"] != "pending":
        raise CertError(409, "not_pending", f"This certification is {r['status']}.")
    msg = four_eyes("data.certify_decide", r, by)
    if msg:
        raise CertError(409, "four_eyes", msg)
    if not approve and not comment.strip():
        raise CertError(400, "comment_required", "A rejection needs a comment saying why.")
    det: dict[str, Any] = {"dataset_id": r["version_id"], "root_id": r["root_id"], "certification_id": cid, "level": r["level"],
                           "requested_by": r["requested_by"]}
    if not approve:
        with db.connect() as con:
            con.execute("UPDATE certifications SET status = 'rejected', decided_by = ?, decided_at = ?, comment = ? WHERE id = ?"
                        " AND status = 'pending'", (by, now(), comment, cid))
            audit(con, by, "certification_rejected", det, comment=comment, object_kind="dataset", object_id=r["version_id"])
            return get(con, cid)
    c, res = _contract_ok(db, r["version_id"], r["root_id"])  # checked again: the contract or the tags may have changed
    with db.connect() as con:
        v = _version(con, r["version_id"])
        days = int(_policy(con, v["team"]).get("certify_days") or 365)
        exp = (datetime.now(UTC) + timedelta(days=days)).isoformat(timespec="seconds")
        old = [dict(x) for x in con.execute("SELECT id, version_id FROM certifications WHERE root_id = ? AND level = ? AND"
                                            " status = 'active' AND id <> ?", (r["root_id"], r["level"], cid))]
        con.execute("UPDATE certifications SET status = 'superseded' WHERE root_id = ? AND level = ? AND status = 'active' AND id <> ?",
                    (r["root_id"], r["level"], cid))
        con.execute("UPDATE certifications SET status = 'active', decided_by = ?, decided_at = ?, comment = ?, expires_at = ?,"
                    " contract_id = ?, contract_result = ? WHERE id = ? AND status = 'pending'",
                    (by, now(), comment, exp, c["id"], json.dumps(res, default=str), cid))
        audit(con, by, "certification_approved", {**det, "expires_at": exp, "certify_days": days, "contract_id": c["id"],
                                                  "contract_version": c["version"], "contract_status": res["status"],
                                                  "superseded": old},
              comment=comment, object_kind="dataset", object_id=r["version_id"])
        return get(con, cid)


def for_root(con: Any, root: str) -> list[dict[str, Any]]:
    return [effective(dict(r)) for r in con.execute("SELECT * FROM certifications WHERE root_id = ? ORDER BY requested_at DESC", (root,))]


# --------------------------------------------------------------------------- require_certified_inputs


def run_input_versions(con: Any, run_id: str) -> list[str]:
    vids = [r[0] for r in con.execute("SELECT DISTINCT version_id FROM run_inputs WHERE run_id = ? AND role IN ('train', 'join_input')"
                                      " AND version_id IS NOT NULL", (run_id,))]
    if not vids:
        r = con.execute("SELECT dataset_id FROM runs WHERE id = ?", (run_id,)).fetchone()
        vids = [r[0]] if r and r[0] else []
    return vids


def inputs_gate(db: Any, run_id: str) -> dict[str, Any]:
    """{} when the run's team policy does not ask; else {required, inputs: [...]} or CertError 409 with the list."""
    from .shares import live_certification

    with db.connect() as con:
        r = con.execute("SELECT COALESCE(r.team_id, p.team_id) FROM runs r LEFT JOIN projects p ON p.id = r.project_id WHERE r.id = ?",
                        (run_id,)).fetchone()
        need = _policy(con, r[0] if r else None).get("require_certified_inputs")
        if not need:
            return {}
        levels = ("approved_for_model",) if need == "approved_for_model" else LEVELS
        inputs, missing = [], []
        for vid in run_input_versions(con, run_id):
            c = live_certification(con, vid, levels)
            tabs = [t[0] for t in con.execute("SELECT DISTINCT table_name FROM run_inputs WHERE run_id = ? AND version_id = ?",
                                              (run_id, vid)) if t[0]]
            row = {"version_id": vid, "tables": tabs,
                   "certification": {k: c[k] for k in ("id", "level", "decided_by", "decided_at", "expires_at", "contract_id")} if c else None}
            inputs.append(row)
            if not c:
                missing.append(row)
    req = {"required": need if isinstance(need, str) else "certified", "levels": list(levels), "inputs": inputs}
    if missing:
        raise CertError(409, "inputs_not_certified", "The team policy require_certified_inputs asks for "
                        + " or ".join(levels) + " input versions; not certified: "
                        + ", ".join(m["version_id"] + (f" ({', '.join(m['tables'])})" if m["tables"] else "") for m in missing) + ".",
                        missing=missing, policy=req)
    return req


# --------------------------------------------------------------------------- routes


def _by(b: dict[str, Any]) -> str:
    from .api import _who

    return _who((b.get("by") or G.current_actor() or "").strip())


@router.post("/datasets/{dataset_id}/certifications", status_code=201, dependencies=[require("data.certify", path("dataset_id", "dataset"))])
async def create(dataset_id: str, request: Request, b: dict[str, Any]) -> dict[str, Any]:
    """P21: {version_id?, level?: certified|approved_for_model, reason, by?}: a pending certification of one version
    (default: `dataset_id` itself). 409 contract_required / contract_failed / pii_review_required / certification_exists."""
    b = b if isinstance(b, dict) else {}
    try:
        return await run_in_threadpool(request_cert, request.app.state.db, dataset_id, b, _by(b))
    except CertError as e:
        raise e.http() from e


@router.get("/datasets/{dataset_id}/certifications", dependencies=[require("data.read_meta", path("dataset_id", "dataset_root"))])
async def list_for(dataset_id: str, request: Request) -> dict[str, Any]:
    """P21: the dataset's certifications (every version), newest first, with their effective status."""
    def go() -> dict[str, Any]:
        with request.app.state.db.connect() as con:
            root = _version(con, dataset_id)["root"]
            rows = for_root(con, root)
        return {"root_id": root, "certifications": rows,
                "live": [r for r in rows if r["status"] == "active"]}

    try:
        return await run_in_threadpool(go)
    except CertError as e:
        raise e.http() from e


@router.get("/certifications/{cid}", dependencies=[require("data.read_meta", path("cid", "certification"))])
async def get_one(cid: str, request: Request) -> dict[str, Any]:
    def go() -> dict[str, Any]:
        with request.app.state.db.connect() as con:
            return get(con, cid)

    try:
        return await run_in_threadpool(go)
    except CertError as e:
        raise e.http() from e


@router.post("/certifications/{cid}/decide", dependencies=[require("data.certify_decide", path("cid", "certification"))])
async def decide(cid: str, request: Request, b: dict[str, Any]) -> dict[str, Any]:
    """P21: {approve, comment?, by?}. `certified`: an owner of the dataset's team; `approved_for_model`: a risk reviewer
    of the team; never the requester (409). Approving supersedes the earlier live certification of that level."""
    from ..authz import deps

    b = b if isinstance(b, dict) else {}
    by = _by(b)
    db = request.app.state.db
    try:
        with db.connect() as con:
            lvl = get(con, cid)["level"]
        await run_in_threadpool(deps.check, request, DECIDE_ACTION[lvl], "certification", cid, by)
        return await run_in_threadpool(decide_cert, db, cid, b.get("approve"), by, str(b.get("comment") or "")[:2000])
    except CertError as e:
        if e.status in (403, 409):
            G.refused(db, by, "certification_decide", e.status, e.message, {"certification_id": cid})
        raise e.http() from e
