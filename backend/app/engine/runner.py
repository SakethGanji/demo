"""Runs on the durable job queue (P22, B §5.5). One OS process per fit (`python -m app.engine.worker`), started by
whichever worker leases the run's `train` job: a `workerd` process, or the worker embedded in the API.

API side (`JobRunner`, the interface the routes always used):
- `submit(run_id)` queues a `train` job (team from the run, for fair share; `by_principal` from the run's creator);
- `cancel(run_id)` cancels the job: a queued one never starts, a leased one is stopped by its holder within ~2 s;
- `start()` re-queues runs left `queued` without a job (runs queued before P22) and fails runs left `running` without
  one (an old API's in-process fit: nothing can resume it);
- `on_success(run_id)` (the Model Doctor warm-up) is called by a poller in the API for every train job that finished
  `succeeded`, on any worker.

Worker side (`run_job`): spawn the fit, watching the worker's pid; a re-leased job (its first holder died) resets the
run to queued, emits `run_requeued` and fits again from the start (the fit writes its own events and result, so a
second attempt overwrites the first). Under dev reload a fit started by the previous API process is adopted, not
started twice. `on_dead` fails the run once its attempts are spent.
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import Any

from .. import config
from ..jobs import queue as Q
from .jobs import alive
from .store import RunStore, now

log = logging.getLogger("ensemble.runner")

STOPPED_MSG = "The API stopped while this run was in progress."
LOST_MSG = "The worker running this fit stopped every time it was tried."


def _team_and_by(store: RunStore, run_id: str) -> tuple[str | None, str]:
    rec = store.get(run_id) or {}
    cfg = rec.get("config") or {}
    by = rec.get("created_by") or (cfg.get("patch") or {}).get("by") or cfg.get("by") or "system"
    return rec.get("team_id"), str(by)


class JobRunner:
    def __init__(self, settings: config.Settings, store: RunStore, max_parallel: int | None = None) -> None:
        self.settings = settings
        self.store = store
        self.db = store.db
        self.max_parallel = max_parallel or int(os.environ.get("ENSEMBLE_MAX_PARALLEL_RUNS", "1"))
        self.on_success = None  # callable(run_id), set by the API: warms the Model Doctor after a fit
        self._seen = Q.ts()

    # -- lifecycle ------------------------------------------------------------

    def start(self) -> None:
        self.recover()

    def recover(self) -> list[str]:
        failed = []
        for rec in self.store.list_active():
            if Q.latest_for(self.db, "run", rec["id"], "train") and Q.active_for(self.db, "run", rec["id"], "train"):
                continue  # on the queue: a worker has it or will
            if rec["status"] == "queued":
                log.info("queueing run %s (it had no job)", rec["id"])
                self.submit(rec["id"])
                continue
            if self.store.finish(rec["id"], "failed", error=STOPPED_MSG):
                self.store.emit(rec["id"], "run_failed", error=STOPPED_MSG, recovered=True)
                failed.append(rec["id"])
        if failed:
            log.info("marked %d interrupted run(s) failed: %s", len(failed), ", ".join(failed))
        return failed

    def poll_finished(self) -> None:
        """Called every ~2 s by the API's worker: on_success for fits that finished anywhere."""
        rows = Q.finished_since(self.db, "train", self._seen)
        for r in rows:
            self._seen = max(self._seen, r["finished_at"])
            if r["status"] == "succeeded" and self.on_success is not None and r.get("ref_id"):
                if (self.store.get(r["ref_id"]) or {}).get("status") == "succeeded":
                    try:  # A1 #12: the Model Doctor is computed (and cached) right after the fit
                        self.on_success(r["ref_id"])
                    except Exception:  # noqa: BLE001
                        log.exception("on_success hook failed for %s", r["ref_id"])

    def shutdown(self) -> None:
        """Nothing to stop: fits belong to workers. The embedded worker's own jobs are re-queued by main.py."""

    # -- runs -----------------------------------------------------------------

    def run_dir(self, run_id: str) -> Path:
        return self.settings.runs_dir / run_id

    def submit(self, run_id: str) -> dict[str, Any]:
        team, by = _team_and_by(self.store, run_id)
        return Q.enqueue(self.db, "train", {"run_id": run_id}, resource="train", by=by, team_id=team,
                         ref_kind="run", ref_id=run_id)

    def cancel(self, run_id: str) -> bool:
        rec = self.store.get(run_id)
        if not rec or rec["status"] not in ("queued", "running"):
            return False
        job = Q.active_for(self.db, "run", run_id, "train")
        before = Q.cancel(self.db, job["id"]) if job else None
        if before and before["status"] == "leased":
            return True  # its holder stops the fit and records the cancel
        if self.store.finish(run_id, "cancelled", error="Cancelled before it started."):
            self.store.emit(run_id, "run_cancelled", reason="cancelled while queued")
        return True


# ------------------------------------------------------------------------------------------------ worker side


def _tail(d: Path, n: int = 2000) -> str:
    try:
        return (d / "worker.log").read_text(errors="replace")[-n:]
    except OSError:
        return ""


def run_job(ctx) -> dict[str, Any]:
    from .workerd import Cancelled

    store: RunStore = ctx.state.runs
    settings = ctx.state.settings
    run_id = ctx.payload["run_id"]
    rec = store.get(run_id)
    if not rec or rec["status"] not in ("queued", "running"):
        return {"skipped": f"run is {rec and rec['status']}"}
    d = Path(rec["config"].get("run_dir") or settings.runs_dir / run_id)
    pid = rec.get("pid")
    if rec["status"] == "running" and pid and alive(pid, "app.engine.worker"):
        log.info("adopting fit pid %s of run %s", pid, run_id)  # dev reload: the previous API's fit
        ctx.wait_pid(pid, "app.engine.worker")
    else:
        if ctx.attempt > 1 or rec["status"] == "running":
            store.emit(run_id, "run_requeued", attempt=ctx.attempt, worker=ctx.worker.id,
                       reason=(ctx.job.get("error") or "its previous worker stopped"))
            store.update(run_id, status="queued", pid=None)
            pp = rec["config"].get("predictor_path")
            if pp and Path(pp).is_dir():
                shutil.rmtree(pp, ignore_errors=True)  # the fit starts again from nothing
        code = ctx.run_process("app.engine.worker", [str(d), ctx.watch_pid(), "anchor"], d / "worker.log",
                               {"ENSEMBLE_DATA_DIR": str(settings.data_dir)},
                               on_start=lambda p: store.mark_running(run_id, p))
        if ctx.stop_reason in ("shutdown", "lost"):
            raise Cancelled(ctx.stop_reason)  # re-queued: another worker fits it
        if ctx.cancelled:
            if store.finish(run_id, "cancelled", error="Cancelled."):
                store.emit(run_id, "run_cancelled", reason="cancelled while running")
            raise Cancelled("cancelled")
        if (store.get(run_id) or {}).get("status") == "succeeded":
            return {"run_id": run_id, "status": "succeeded", "worker": ctx.worker.id}
        msg = f"The worker exited with code {code}. {_tail(d)}".strip()
        if store.finish(run_id, "failed", error=msg):
            store.emit(run_id, "run_failed", error=f"The worker exited with code {code}.", log_tail=_tail(d))
        raise RuntimeError(f"fit exited with code {code}")
    st = (store.get(run_id) or {}).get("status")
    if ctx.cancelled:
        if store.finish(run_id, "cancelled", error="Cancelled."):
            store.emit(run_id, "run_cancelled", reason="cancelled while running")
        raise Cancelled("cancelled")
    if st != "succeeded" and store.finish(run_id, "failed", error="The worker exited without finishing the run."):
        store.emit(run_id, "run_failed", error="The worker exited without finishing the run.")
    return {"run_id": run_id, "status": st, "adopted": True}


def on_dead(state, job: dict[str, Any]) -> None:
    run_id = (job.get("payload") or {}).get("run_id") or job.get("ref_id")
    store: RunStore = state.runs
    msg = f"{LOST_MSG} ({job.get('error')})"
    if run_id and store.finish(run_id, "failed", error=msg):
        store.emit(run_id, "run_failed", error=msg, at=now())
