"""P02 routes: the lake self-check, a version's manifest and verify, GC (dry run), and the admin seed that writes a
Parquet version through the VersionWriter. `install(app, settings)` is the one hook main.py calls: it runs the startup
self-check (the API refuses to start on a mismatch), opens the blob store and mounts these routes."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool

from ..authz import path, public, require# P19
from ..catalog import governance as G
from ..catalog import schema as S
from . import duck, gc
from . import manifest as M
from . import verify as V
from .store import open_store, store_info
from .writer import ROW_ID, LakeError, VersionWriter

log = logging.getLogger("ensemble.lake")
router = APIRouter(prefix="/api")


def install(app, settings) -> None:
    r = M.assert_selfcheck()  # raises SelfCheckFailed: the API does not start
    log.info("lake self-check ok: %d vectors, %s", len(r["vectors"]), r["engine"])
    duck.set_spill_root(settings.duck_spill_dir)
    app.state.lake = open_store(settings)
    app.include_router(router)
    from .blobcache import CacheIntegrityError

    async def _integrity(request: Request, exc: CacheIntegrityError):  # P10b: the bucket returned other bytes
        from fastapi.responses import JSONResponse

        log.error("blob integrity: %s", exc)
        return JSONResponse({"detail": {"code": "blob_integrity", "message": str(exc)}}, status_code=409)

    app.add_exception_handler(CacheIntegrityError, _integrity)


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

@router.get("/admin/lake/selfcheck", dependencies=[public("re-hashes fixed test vectors in code; no data")])
async def selfcheck() -> dict[str, Any]:
    """Re-runs the startup self-check: fixed vectors hashed by this code against constants (§7 risk 3)."""
    return await run_in_threadpool(M.selfcheck)


@router.get("/admin/lake/status", dependencies=[require("admin.lake")])
def status(request: Request) -> dict[str, Any]:
    st = request.app.state.lake
    with request.app.state.db.connect() as con:
        blobs = {r[0]: {"n": r[1], "bytes": r[2]} for r in con.execute("SELECT state, COUNT(*), SUM(bytes) FROM blobs GROUP BY state")}
        n_versions = con.execute("SELECT COUNT(*) FROM datasets WHERE storage = 'parquet'").fetchone()[0]
        armed = gc.armed(con)
    return {"store": st.root, "parquet_versions": n_versions, "blobs": blobs, "gc_armed": armed,
            "blob_store": store_info(st), "duck": duck.interactive().info()}


# -- P10b: the object store, its read cache, the local → S3 sync ---------------------------------------------------

@router.post("/admin/lake/cache/verify", dependencies=[require("admin.lake")])
async def cache_verify(request: Request) -> dict[str, Any]:
    """Re-hash every file in the S3 read cache; mismatching files are dropped (fetched again, verified, on next use)."""
    st = request.app.state.lake
    if not hasattr(st, "cache"):
        return {"store": st.root, "cache": None, "note": "the local store reads blobs in place; there is no cache"}
    return {"store": st.root, **(await run_in_threadpool(st.cache.verify_all)), "cache": st.cache.info()}


@router.post("/admin/lake/sync-s3", dependencies=[require("admin.lake")])
async def sync_s3(request: Request, dry_run: int = Query(1), deep_verify: int = Query(0), body: dict[str, Any] | None = None) -> dict[str, Any]:
    """Copy the local lake (Settings.lake_dir) into the configured bucket, verify every catalogued sha there (resumable:
    a re-run copies only what is missing). `?dry_run=1` (the default) lists what would be copied. Long lakes: run
    `python -m app.lake.sync_s3` instead (same code)."""
    from . import sync_s3 as SY
    from .store import LocalBlobStore

    body = body if isinstance(body, dict) else {}
    by = _admin(request, body.get("by"), "lake_sync_s3")
    settings = request.app.state.settings
    dst = request.app.state.lake if getattr(request.app.state.lake, "bucket", None) else SY.s3_destination(settings)
    src = LocalBlobStore(settings.lake_dir)
    return await run_in_threadpool(lambda: SY.sync(request.app.state.db, src, dst, dry_run=bool(dry_run),
                                                   deep_verify=bool(deep_verify), by=by))


# -- a version's manifest and verify -------------------------------------------------------------------------------

@router.get("/datasets/{dataset_id}/manifest", dependencies=[require("data.read_meta", path("dataset_id", "dataset"))])
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


@router.get("/datasets/{dataset_id}/verify", dependencies=[require("data.verify", path("dataset_id", "dataset"))])
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


# -- GC (P02 dry run; P24 armed deletes, started by a person only) ---------------------------------------------------

@router.post("/admin/lake/gc", dependencies=[require("admin.lake")])
async def run_gc(request: Request, dry_run: int = Query(1), body: dict[str, Any] | None = None) -> dict[str, Any]:
    """`?dry_run=1` (the default): the plan, stored as `plan_id` and audited. `?dry_run=0 {plan_id, confirm: "gc", by}`:
    delete only what that plan listed and is still a candidate (409 `gc_disarmed` until a verify-all pass). Admins only;
    nothing runs on a timer (§11 item 3)."""
    body = body if isinstance(body, dict) else {}
    by = _admin(request, body.get("by") or request.query_params.get("by"), "lake_gc")
    db, st = request.app.state.db, request.app.state.lake
    if dry_run:
        return await run_in_threadpool(gc.dry_run, db, st, by)
    if body.get("confirm") != "gc":
        raise HTTPException(400, 'Send {"plan_id": …, "confirm": "gc"}: this deletes blobs. POST ?dry_run=1 first.')
    try:
        return await run_in_threadpool(lambda: gc.run(db, st, by=by, plan_id=str(body.get("plan_id") or "")))
    except gc.GcError as e:
        G.refused(db, by, "lake_gc", e.status, e.payload)
        raise HTTPException(e.status, e.payload) from e


@router.get("/admin/lake/gc/armed", dependencies=[require("admin.lake")])
def gc_armed(request: Request) -> dict[str, Any]:
    with request.app.state.db.connect() as con:
        return gc.armed_info(con)


# -- P10: the migration of CSV-era versions (§5.11), verify-all, a run's re-verify ---------------------------------

@router.post("/admin/lake/migrate", dependencies=[require("admin.lake")])
async def migrate(request: Request, dry_run: int = Query(1), body: dict[str, Any] | None = None) -> Any:
    """`?dry_run=1` (the default): the plan, per CSV-era version (migrate / tombstone / skip and why), written nowhere.
    `?dry_run=0`: queue the resumable `migrate` job (202 with its id; a job already running is returned instead)."""
    from fastapi.responses import JSONResponse

    from ..jobs import queue as Q
    from . import migrate as MG

    body = body if isinstance(body, dict) else {}
    app = request.app
    if dry_run:
        return await run_in_threadpool(MG.plan, app.state.db, app.state.settings)
    by = _admin(request, body.get("by"), "lake_migrate")
    only = [str(x) for x in body.get("only") or []] or None
    live = await run_in_threadpool(Q.active_of_kind, app.state.db, "migrate")
    if live:
        return JSONResponse({"job": live[0], "started": False, "note": "a migration job is already queued or running"}, status_code=202)
    job = await run_in_threadpool(lambda: Q.enqueue(app.state.db, "migrate", {"by": by, "only": only}, resource="duck", by=by,
                                                    ref_kind="lake", ref_id="migrate", max_attempts=3))
    with app.state.db.connect() as con:
        S.audit(con, by, "lake_migration_started", {"job_id": job["id"], "only": only})
    return JSONResponse({"job": job, "started": True, "status_url": "/api/admin/lake/migrate"}, status_code=202)


@router.get("/admin/lake/migrate", dependencies=[require("admin.lake")])
async def migrate_status(request: Request) -> dict[str, Any]:
    """The job's progress, and one row per version handled (migrated / skipped and why / failed, the folder step)."""
    from ..jobs import queue as Q
    from . import migrate as MG

    db = request.app.state.db
    out = await run_in_threadpool(MG.status, db)
    prog = out.get("progress") or {}
    out["job"] = await run_in_threadpool(Q.get, db, prog["job_id"]) if prog.get("job_id") else None
    return out


@router.post("/admin/lake/migrate/cleanup", dependencies=[require("admin.lake")])
async def migrate_cleanup(request: Request, dry_run: int = Query(1), body: dict[str, Any] | None = None) -> dict[str, Any]:
    """Delete the `<id>.migrated` folders: only after two verify-all passes since the migration finished (409 before)."""
    from . import migrate as MG

    body = body if isinstance(body, dict) else {}
    by = _admin(request, body.get("by"), "lake_migrate_cleanup")
    try:
        return await run_in_threadpool(lambda: MG.cleanup(request.app.state.db, request.app.state.settings, dry_run=bool(dry_run), by=by))
    except MG.MigrateError as e:
        raise HTTPException(e.status, e.payload)


@router.get("/admin/lake/verify-all", dependencies=[require("admin.lake")])
async def verify_all(request: Request, by: str | None = None) -> dict[str, Any]:
    """Re-hash every version that is not deleted (DL2's verify-all, lake versions part by part, migrated versions also
    through their legacy blobs). A pass is recorded: GC (P24) and the migration cleanup wait for it."""
    from ..catalog import dsets

    who = _admin(request, by, "lake_verify_all")
    return await run_in_threadpool(dsets.verify_all, request.app, who)


@router.get("/admin/lake/runs/{run_id}/verify", dependencies=[require("admin.lake")])
async def verify_run(run_id: str, request: Request) -> dict[str, Any]:
    """Re-verify what a run trained on against the hashes it recorded: the data (a Parquet table's table_sha256, or the
    data.csv bytes of a CSV-era run, from the legacy_csv blob once its version is migrated) and its split."""
    from . import migrate as MG

    try:
        return await run_in_threadpool(MG.verify_run, request.app.state.db, request.app.state.lake, run_id)
    except MG.MigrateError as e:
        raise HTTPException(e.status, e.payload)


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
            from .writer import reserve_row_ids

            start = reserve_row_ids(app.state.db, w.root_id, table, 0, rows)  # F-X1 M1: atomic on the root counter
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
