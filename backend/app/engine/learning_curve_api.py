"""F12 routes: start, read and cancel a run's learning curve. The curve itself runs in its own process
(`python -m app.engine.learning_curve <run_dir>`, see learning_curve.py), one at a time across the workbench.

P22: a start queues an `lc` job (resource `train`) on the durable queue; whichever worker leases it runs the process
(`run_job`). The state file stays the record the UI polls; "is it running" is the job's status, not a local pid, so an
API restart costs nothing and the curve can run on another host."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from . import learning_curve as lc
from ..authz import path, require# P19
from ..jobs import queue as Q
from .store import now
from ..http import run_or_404

router = APIRouter(prefix="/api")

_DB = None  # bound at API start (main.py): active() needs it
MIN_ROWS = 30  # the smallest subsample must have at least this many rows


class LearningCurveRequest(BaseModel):
    fractions: list[float] = Field(default_factory=lambda: list(lc.DEFAULT_FRACTIONS),
                                   description="Shares of the run's training rows, nested; must include 1.0 (the run itself).")
    seeds: int = Field(lc.DEFAULT_SEEDS, ge=1, le=10, description="Subsamples drawn per fraction below 1.0 (error bars need ≥ 2).")
    time_limit: float | None = Field(None, gt=0, description="Seconds per fit; default: the run's own time_limit.")
    min_gain: float | None = Field(None, gt=0, description="ε: the smallest gain per doubling that counts, in the metric's units; "
                                                         "default 2.5 % of the remaining gap to the metric's optimum.")
    reuse_full: bool = Field(True, description="Use the run itself as the 100 % point (the same call on the same rows) "
                                               "instead of fitting it again.")


def _state(rec: dict[str, Any], store: Any) -> dict[str, Any] | None:
    """The job's state, with a running curve whose job is gone (it failed or was lost) marked failed."""
    run_dir = Path(rec["config"]["run_dir"])
    st = lc.read_state(run_dir)
    if st and st.get("status") in ("queued", "running") and not Q.active_for(store.db, "run", rec["id"], "lc"):
        st = lc.read_state(run_dir)  # the handler may have just closed it
        if st and st.get("status") in ("queued", "running"):
            j = Q.latest_for(store.db, "run", rec["id"], "lc") or {}
            st.update(status="failed", finished_at=now(),
                      error=f"The learning curve's job ended without finishing it ({j.get('error') or j.get('status') or 'no job'}).")
            store.merge_result(rec["id"], learning_curve=lc.headline(st))
            lc.write_state(run_dir, st)
    return st


def defaults(rec: dict[str, Any]) -> dict[str, Any]:
    res = rec.get("result") or {}
    n = (res.get("data") or {}).get("train_rows")
    tl = rec["config"].get("time_limit")
    fr = list(lc.DEFAULT_FRACTIONS)
    fits = (len(fr) - 1) * lc.DEFAULT_SEEDS
    return {"fractions": fr, "seeds": lc.DEFAULT_SEEDS, "time_limit": tl, "n_full": n, "fits": fits,
            "run_fit_seconds": res.get("fit_seconds"),
            # the upper bound of the wall time: each fit is capped by the time limit; small subsamples finish early
            "max_seconds": fits * tl if tl else None,
            "estimate_seconds": sum(min(tl or 1e9, (res.get("fit_seconds") or 0) * max(f, 0.2) + 5) for f in fr[:-1]) * lc.DEFAULT_SEEDS
            if res.get("fit_seconds") else None}


@router.get("/runs/{run_id}/learning-curve", dependencies=[require("run.read", path("run_id", "run"))])
def get_learning_curve(run_id: str, request: Request) -> dict[str, Any]:
    """F12: the run's learning curve: status and progress while it runs; then every point, the mean ± t-interval per
    fraction, the fitted power law with its bootstrap band, extrapolation to 2N and 4N, and the verdict. Status 'none'
    (with the defaults a start would use) when none was started."""
    rec = run_or_404(request, run_id)
    st = _state(rec, request.app.state.runs)
    if not st:
        return {"run_id": run_id, "status": "none", "defaults": defaults(rec)}
    st = {k: v for k, v in st.items() if k not in ("traceback",)}
    return {"run_id": run_id, **st, "defaults": defaults(rec)}


@router.post("/runs/{run_id}/learning-curve", status_code=202, dependencies=[require("run.train", path("run_id", "run"))])
def start_learning_curve(run_id: str, body: LearningCurveRequest, request: Request) -> dict[str, Any]:
    """F12: fit the run's exact config on nested subsamples of its training rows (default 10 / 25 / 50 %, 3 seeds each;
    100 % is the run itself), each scored on the run's holdout, in a separate process. Poll GET …/learning-curve."""
    rec = run_or_404(request, run_id)
    cfg = rec["config"]
    if rec["status"] != "succeeded":
        raise HTTPException(400, f"Run {run_id} is {rec['status']}; a learning curve needs a finished run.")
    if cfg.get("kind") in ("forecast", "anomaly", "expected_loss", "segmented", "survival", "reject_inference", "multilabel") or cfg.get("import"):
        raise HTTPException(400, "Learning curves refit the run's own config: only trained tabular runs have one "
                                 "(not forecast, anomaly or imported runs).")
    fr = sorted(set(float(f) for f in body.fractions))
    if any(not 0 < f <= 1 for f in fr):
        raise HTTPException(400, "fractions: each must be in (0, 1].")
    if 1.0 not in fr or len(fr) < 3:
        raise HTTPException(400, "fractions: give at least 3 sample sizes, including 1.0 (the run's own training rows).")
    n = ((rec.get("result") or {}).get("data") or {}).get("train_rows") or 0
    if fr[0] * n < MIN_ROWS:
        raise HTTPException(400, f"fractions: {fr[0]:g} of {n} training rows is {fr[0] * n:.0f} rows; the smallest "
                                 f"subsample needs at least {MIN_ROWS}.")
    metric = ((rec.get("result") or {}).get("eval_metric") or {}).get("name")
    if body.reuse_full and metric not in (((rec.get("result") or {}).get("holdout") or {}).get("metrics") or {}):
        raise HTTPException(400, "The run has no holdout score for its metric to use as the 100 % point; use reuse_full = false.")
    store = request.app.state.runs
    busy = Q.active_of_kind(store.db, "lc")
    if busy:
        other = busy[0]["ref_id"]
        raise HTTPException(409, f"A learning curve is already running (run {other}); wait for it or cancel it."
                            if other != run_id else "This run's learning curve is already running.")
    run_dir = Path(cfg["run_dir"])
    d = lc.lc_dir(run_dir)
    d.mkdir(parents=True, exist_ok=True)
    req = {**body.model_dump(), "fractions": fr}
    (d / "request.json").write_text(json.dumps(req, indent=1))
    seeds = body.seeds
    lc.write_state(run_dir, {"schema": lc.SCHEMA, "status": "queued", "request": req, "created_at": now(),
                             "fits_total": (len(fr) - 1) * seeds + (0 if body.reuse_full else 1), "fits_done": 0,
                             "points": []})
    from ..catalog import governance as G

    Q.enqueue(store.db, "lc", {"run_id": run_id}, resource="train", by=G.current_actor() or "system",
              team_id=rec.get("team_id"), ref_kind="run", ref_id=run_id)
    store.merge_result(run_id, learning_curve=lc.headline(lc.read_state(run_dir)))
    return get_learning_curve(run_id, request)


def run_job(ctx) -> dict[str, Any]:
    """Worker side: run the curve's process; close the state if the process did not."""
    from .workerd import Cancelled

    store = ctx.state.runs
    run_id = ctx.payload["run_id"]
    rec = store.get(run_id)
    run_dir = Path(rec["config"]["run_dir"])
    d = lc.lc_dir(run_dir)
    code = ctx.run_process("app.engine.learning_curve", [str(run_dir), ctx.watch_pid()], d / "learning_curve.log")
    if ctx.stop_reason in ("shutdown", "lost"):
        raise Cancelled(ctx.stop_reason)
    st = lc.read_state(run_dir) or {}
    if st.get("status") in ("queued", "running"):
        st.update(status="cancelled" if ctx.cancelled else "failed", finished_at=now(),
                  error="Cancelled." if ctx.cancelled else f"The learning curve's process exited with code {code}.")
        store.merge_result(run_id, learning_curve=lc.headline(st))  # the run payload first: readers poll the state
        lc.write_state(run_dir, st)
    return {"run_id": run_id, "status": st.get("status"), "code": code}


def on_dead(state, job: dict[str, Any]) -> None:
    rec = state.runs.get(job["ref_id"])
    if rec:
        _state(rec, state.runs)


@router.post("/runs/{run_id}/learning-curve/cancel", dependencies=[require("run.manage", path("run_id", "run"))])
def cancel_learning_curve(run_id: str, request: Request) -> dict[str, Any]:
    rec = run_or_404(request, run_id)
    db = request.app.state.db
    job = Q.active_for(db, "run", run_id, "lc")
    before = Q.cancel(db, job["id"]) if job else None
    if not before:
        raise HTTPException(400, "No learning curve is running for this run.")
    if before["status"] == "queued":  # never started: close it here (a leased one is closed by its worker)
        run_dir = Path(rec["config"]["run_dir"])
        st = lc.read_state(run_dir) or {}
        st.update(status="cancelled", finished_at=now(), error="Cancelled.")
        request.app.state.runs.merge_result(run_id, learning_curve=lc.headline(st))
        lc.write_state(run_dir, st)
    return {"run_id": run_id, "cancelled": True}


def bind(db) -> None:
    global _DB
    _DB = db


def shutdown() -> None:
    """Nothing to stop: curves run on the queue's workers and survive the API."""


def active() -> list[str]:
    """Runs whose learning curve is queued or running (any worker)."""
    return [j["ref_id"] for j in Q.active_of_kind(_DB, "lc")] if _DB is not None else []
