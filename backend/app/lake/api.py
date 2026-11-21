"""P02 routes: the lake self-check, a version's manifest and verify, GC (dry run), and the admin seed that writes a
Parquet version through the VersionWriter. `install(app, settings)` is the one hook main.py calls: it runs the startup
self-check (the API refuses to start on a mismatch), opens the blob store and mounts these routes."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool

from ..authz import require  # P19
from ..catalog import governance as G
from ..catalog import schema as S
from . import duck, gc
from . import manifest as M
from . import verify as V
from .store import open_store
from .writer import ROW_ID, LakeError, VersionWriter

log = logging.getLogger("ensemble.lake")
router = APIRouter(prefix="/api")


def install(app, settings) -> None:
    r = M.assert_selfcheck()  # raises SelfCheckFailed: the API does not start
    log.info("lake self-check ok: %d vectors, %s", len(r["vectors"]), r["engine"])
    duck.set_spill_root(settings.duck_spill_dir)
    app.state.lake = open_store(settings)
    app.include_router(router)


def _err(e: LakeError) -> HTTPException:
    return HTTPException(e.status, e.payload)


def _admin(request: Request, by: str | None, action: str) -> str:
    who = (by or G.current_actor() or "").strip()
    if not G.is_admin(who):
        msg = f"Only an admin (ENSEMBLE_ADMINS) may run {action}; {who or 'nobody'} is not one."
        G.refused(request.app.state.db, who or "unknown", action, 403, msg)
        raise HTTPException(403, msg)
    return who


# -- self-check, status ---------------------------------------------------------------------------------------------

@router.get("/admin/lake/selfcheck")
async def selfcheck() -> dict[str, Any]:
    """Re-runs the startup self-check: fixed vectors hashed by this code against constants (§7 risk 3)."""
    return await run_in_threadpool(M.selfcheck)


@router.get("/admin/lake/status")
def status(request: Request) -> dict[str, Any]:
    st = request.app.state.lake
    with request.app.state.db.connect() as con:
        blobs = {r[0]: {"n": r[1], "bytes": r[2]} for r in con.execute("SELECT state, COUNT(*), SUM(bytes) FROM blobs GROUP BY state")}
        n_versions = con.execute("SELECT COUNT(*) FROM datasets WHERE storage = 'parquet'").fetchone()[0]
        armed = gc.armed(con)
    return {"store": st.root, "parquet_versions": n_versions, "blobs": blobs, "gc_armed": armed,
            "duck": duck.interactive().info()}


# -- a version's manifest and verify -------------------------------------------------------------------------------

@router.get("/datasets/{dataset_id}/manifest")
def get_manifest(dataset_id: str, request: Request) -> dict[str, Any]:
    """The version's manifest blob as stored (hashes, schemas, files), with each file's resolved location (A §2.1)."""
    db, st = request.app.state.db, request.app.state.lake
    try:
        v, m, raw = V.load_manifest(db, st, dataset_id)
    except KeyError:
        raise HTTPException(404, f"No dataset {dataset_id}.")
    if v.get("storage") != "parquet":
        raise HTTPException(409, {"code": "not_parquet", "message": f"{dataset_id} is a CSV-era version (storage "
                                  f"'{v.get('storage')}'): it has no manifest until it is migrated (P10)."})
    if m is None:
        raise HTTPException(409, {"code": "manifest_missing", "message": f"The manifest blob of {dataset_id} is missing; "
                                  f"GET /api/datasets/{dataset_id}/verify reports it."})
    files = {f["sha256"]: st.uri(f["key"]) for t in m["data"]["tables"] for f in t["files"]}
    return {"version_id": dataset_id, "manifest_sha256": v["manifest_sha256"], "data_sha256": v["data_sha256"],
            "bytes": len(raw), "manifest": m, "resolved": files}


@router.get("/datasets/{dataset_id}/verify")
def verify(dataset_id: str, request: Request) -> dict[str, Any]:
    """Re-hash a Parquet version (parts, table shas, manifest, splits). CSV-era versions keep POST …/verify (DL2)."""
    db = request.app.state.db
    with db.connect() as con:
        r = con.execute("SELECT storage, root_id FROM datasets WHERE id = ?", (dataset_id,)).fetchone()
    if not r:
        raise HTTPException(404, f"No dataset {dataset_id}.")
    if r["storage"] != "parquet":
        raise HTTPException(409, {"code": "not_parquet", "message": f"{dataset_id} is a CSV-era version: use POST "
                                  f"/api/datasets/{dataset_id}/verify."})
    out = V.verify_version(db, request.app.state.lake, dataset_id)
    if not out["ok"]:  # a failed check is evidence: audited (a passing GET is a read and is not)
        with db.connect() as con:
            S.audit(con, G.current_actor("system"), "dataset_integrity_failed",
                    {"dataset_id": dataset_id, "root_id": r["root_id"], "storage": "parquet", "problems": out["problems"][:20]})
    return out


# -- GC (dry run only in P02) ---------------------------------------------------------------------------------------

@router.post("/admin/lake/gc", dependencies=[require("admin.lake")])
async def run_gc(request: Request, dry_run: int = Query(1)) -> dict[str, Any]:
    if not dry_run:
        raise HTTPException(409, {"code": "gc_disarmed", "message": "GC deletes are not available yet (P24 builds them, "
                                  "armed only after verify-all has passed). Use ?dry_run=1."})
    return await run_in_threadpool(gc.plan, request.app.state.db, request.app.state.lake)


# -- admin seed: a deterministic table written as a Parquet version ------------------------------------------------

SEED_SQL = f"""
SELECT i::BIGINT AS {ROW_ID},
       lpad(CAST((i * 7919) % 100000 AS VARCHAR), 5, '0') AS zip,
       'A' || lpad(CAST(i AS VARCHAR), 9, '0') AS account_no,
       DATE '2020-01-01' + CAST((i * 13) % 1500 AS INTEGER) AS app_date,
       CAST((i * 37) % 100000 AS DOUBLE) / 100 AS amount,
       CAST((i * 31) % 97 AS INTEGER) AS tenure_months,
       ['retail', 'sme', 'corporate', 'private'][1 + CAST(i % 4 AS INTEGER)] AS segment,
       CASE WHEN (i * 2654435761) % 1000 < 120 THEN 1 ELSE 0 END AS y
FROM range(?, ?) t(i)
"""
SEED_REASONS = {"zip": "leading zeros: kept as text", "account_no": "identifier: text"}


def _seed(app, by: str, body: dict[str, Any]) -> dict[str, Any]:
    rows = int(body.get("rows") or 1_000_000)
    if not 1 <= rows <= 50_000_000:
        raise LakeError(400, "rows", "rows must be between 1 and 50 000 000.")
    mode = body.get("mode") or ("append" if body.get("parent_id") else "new")
    table = str(body.get("table") or "applications")
    kw = dict(op="seed" if mode != "carry" else "carry", by=by, reason=str(body.get("reason") or "admin lake seed"),
              job_id=body.get("job_id") or None)
    if mode == "new":
        w = VersionWriter(app.state.db, app.state.lake, name=str(body.get("name") or f"lake-seed-{rows}"),
                          project_id=body.get("project_id"), **kw)
        with w:
            w.write(table, SEED_SQL, [0, rows], reasons=SEED_REASONS, source_file="seed")
            w.key(table, ["account_no"], declared_by=by)
            if body.get("split", True):
                w.split(table, "y:random_stratified", f"SELECT {ROW_ID}, CASE WHEN ({ROW_ID} * 40503) % 10 < 2 THEN -1"
                        f" ELSE ({ROW_ID} % 5) END AS fold FROM {w._rel(table)}", strategy="random_stratified", seed=42,
                        holdout_share="0.2", n_folds=5, target="y")
            return w.commit(target="y")
    parent = body.get("parent_id")
    if not parent:
        raise LakeError(400, "parent_required", f"mode {mode!r} needs parent_id.")
    with VersionWriter(app.state.db, app.state.lake, parent_id=parent, **kw) as w:
        done = w.committed_for_job()
        if done:  # a re-run of a job that already committed: no work, the same version
            return w._result(done, deduped="job")
        if mode == "carry":
            w.carry(table)
        elif mode == "append":
            with app.state.db.connect() as con:
                nxt = con.execute("SELECT next_row_id FROM dataset_root_tables WHERE root_id = ? AND table_name = ?",
                                  (w.root_id, table)).fetchone()
            start = nxt[0] if nxt else 0
            w.append(table, SEED_SQL, [start, start + rows], source_file="seed")
        else:
            raise LakeError(400, "mode", "mode is new, append or carry.")
        return w.commit(target="y")


@router.post("/admin/lake/seed", status_code=201, dependencies=[require("admin.lake")])
async def seed(request: Request, body: dict[str, Any]) -> dict[str, Any]:
    """{by, rows=1e6, name?, project_id?, split=true} → a new Parquet dataset; {by, parent_id, mode: append|carry,
    rows?} → its next version. Admins only; for building and checking the lake, not a user upload path."""
    by = _admin(request, body.get("by"), "lake_seed")
    try:
        return await run_in_threadpool(_seed, request.app, by, body)
    except LakeError as e:
        raise _err(e)
