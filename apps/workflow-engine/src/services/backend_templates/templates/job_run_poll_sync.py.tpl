"""Run work in the Jobs service, poll it, and persist results WITHOUT a browser polling.
Tables: runs(label string*, status string*, job_id string, case_ids json, started_by string, total integer, passed integer,
failed integer, finished_at datetime, synced boolean), run_results(run_id integer*, case_key string*, status string*,
duration_ms integer, error string)
Persistence: POST /runs/sync-pending finishes every unsynced run. Call it from a schedule (a Cron Trigger workflow with an
HTTP Request node hitting this route every minute) and the status route; both share _sync_run, which is idempotent."""
from datetime import datetime, timezone
from studio import api, Response, HTTPError
import studio

TERMINAL = {"passed", "failed", "error", "cancelled"}
JOB_FIELDS = ("type", "label", "target", "policy", "credentials", "concurrency", "cases")   # nothing else is forwarded


def _now():
    return datetime.now(timezone.utc).isoformat()


def _runs():
    return studio.db.table("runs")


def _run(req):
    try:
        row = _runs().get(int(req.params["id"]))
    except (KeyError, ValueError):
        row = None
    if row is None:
        raise HTTPError(404, {"error": "run not found"})
    return row


def _sync_run(run):
    """Copy the job's results into run_results (upsert on run_id+case_key) and close the run once. Safe to call any number
    of times: a terminal run is never re-opened, and results are upserted, never appended."""
    if run["status"] in TERMINAL and run.get("synced"):
        return run
    if run["status"] == "cancelled":
        return _runs().update(run["id"], {"synced": True})        # cancel is terminal: never overwritten by the job
    try:
        job = studio.jobs.get(run["job_id"])
    except studio.BrokerError as e:
        raise HTTPError(502, {"error": e.code})
    if job.get("status") not in TERMINAL:
        return _runs().update(run["id"], {"status": "running"}) if run["status"] == "queued" else run
    results = job.get("results") or []
    for r in results:
        studio.db.table("run_results").upsert(["run_id", "case_key"], {
            "run_id": run["id"], "case_key": str(r.get("case_id")), "status": r.get("status") or "error",
            "duration_ms": int(r.get("duration_ms") or 0), "error": r.get("error")})
    passed = sum(1 for r in results if r.get("status") == "passed")
    return _runs().update(run["id"], {"status": job["status"], "total": len(results), "passed": passed,
                                       "failed": len(results) - passed, "finished_at": _now(), "synced": True})


@api.post("/runs")
def start_run(req):
    """Start a run. Returns the run row {id, status:'queued', job_id, ...} (201)."""
    body = req.json() or {}
    cases = body.get("case_ids")
    if not isinstance(cases, list) or not cases:
        raise HTTPError(400, {"error": "case_ids must be a non-empty list", "field": "case_ids"})
    unknown = [k for k in body if k not in ("case_ids", "label", "started_by")]
    if unknown:
        raise HTTPError(400, {"error": "unknown fields: " + ", ".join(unknown)})
    # credentials are never taken from the request: reference project secrets, e.g. studio.secret('TARGET_PASSWORD')
    job = studio.jobs.start({"type": "test-run", "label": body.get("label") or "run", "cases": []})
    return Response(201, _runs().insert({"label": body.get("label") or "run", "status": "queued", "job_id": job["job_id"],
                                         "case_ids": cases, "started_by": body.get("started_by"), "synced": False}))


@api.get("/runs/{id}/status")
def run_status(req):
    """Poll a run. Returns {run: row, progress, events_next}; closes the run (once) when the job is terminal."""
    run = _sync_run(_run(req))
    return {"run": run, "id": run["id"], "status": run["status"], "synced": bool(run.get("synced"))}


@api.post("/runs/{id}/sync")
def sync_run(req):
    """Persist the job's results now. Idempotent. Returns the run row."""
    return _sync_run(_run(req))


@api.post("/runs/{id}/cancel")
def cancel_run(req):
    """Cancel a run. A run that already finished is returned unchanged (409 would also be fine); cancelled stays cancelled."""
    run = _run(req)
    if run["status"] in TERMINAL:
        return run
    try:
        studio.jobs.cancel(run["job_id"])
    except studio.BrokerError as e:
        raise HTTPError(502, {"error": e.code})
    return _runs().update(run["id"], {"status": "cancelled", "finished_at": _now(), "synced": True})


@api.post("/runs/sync-pending")
def sync_pending_runs(req):
    """Finish every run that is not synced yet (call it from a schedule). Returns {synced: [ids], failed: [ids]}."""
    done, failed = [], []
    for run in _runs().all({"synced": {"$ne": True}}):
        try:
            _sync_run(run)
            done.append(run["id"])
        except HTTPError:
            failed.append(run["id"])
    return {"synced": done, "failed": failed}
