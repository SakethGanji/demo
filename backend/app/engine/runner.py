"""The job runner: one OS process per fit (`python -m app.engine.worker`), started from a small thread pool.

- `submit(run_id)` queues a run; `ENSEMBLE_MAX_PARALLEL_RUNS` (default 1) fits run at once.
- Each worker gets its own process group, so cancelling also stops anything AutoGluon started (Ray workers).
- `recover()` runs on start: runs left `queued`/`running` by a previous API process are marked failed (and a
  stray worker from it is killed), with a `run_failed` event so open event streams end.
- `shutdown()` stops running workers and marks their runs failed.

Dev reload (`ENSEMBLE_DEV_RELOAD=1`, set by `make dev` / `make api`): uvicorn's `--reload` restarts the API
process whenever backend code changes. In this mode a restart must not cost a running fit, so:
- workers watch the reloader (the API process's parent), not the API process: they keep fitting across a
  reload and still stop when `make dev` is stopped;
- `shutdown()` leaves running workers alone and keeps queued runs queued;
- `recover()` adopts a running worker that is still alive (watches its pid, can cancel it) and re-queues
  queued runs, instead of failing them. Workers write their own events and results, so nothing is lost and the
  UI's event stream resumes by replay (EventSource reconnects with Last-Event-ID).
"""

from __future__ import annotations

import logging
import os
import queue
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from .. import config
from .store import RunStore

log = logging.getLogger("ensemble.runner")

STOPPED_MSG = "The API stopped while this run was in progress."


def _is_our_worker(pid: int) -> bool:
    try:
        cmd = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
    except OSError:
        return False
    return "app.engine.worker" in cmd


def _kill_group(pid: int, sig: int = signal.SIGTERM) -> None:
    try:
        os.killpg(os.getpgid(pid), sig)
    except (ProcessLookupError, PermissionError):
        pass


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return _is_our_worker(pid)


def dev_reload() -> bool:
    return os.environ.get("ENSEMBLE_DEV_RELOAD", "") not in ("", "0", "false")


class JobRunner:
    def __init__(self, settings: config.Settings, store: RunStore, max_parallel: int | None = None) -> None:
        self.settings = settings
        self.store = store
        self.max_parallel = max_parallel or int(os.environ.get("ENSEMBLE_MAX_PARALLEL_RUNS", "1"))
        self._q: queue.Queue[str | None] = queue.Queue()
        self._procs: dict[str, subprocess.Popen] = {}
        self._cancelled: set[str] = set()
        self._lock = threading.Lock()
        self._threads: list[threading.Thread] = []
        self._stopping = False
        self.dev_reload = dev_reload()
        self._adopted: dict[str, int] = {}  # run id → pid of a worker started by a previous API process

    # -- lifecycle ------------------------------------------------------------

    def start(self) -> None:
        self.recover()
        for i in range(self.max_parallel):
            t = threading.Thread(target=self._loop, name=f"run-worker-{i}", daemon=True)
            t.start()
            self._threads.append(t)

    def recover(self) -> list[str]:
        failed = []
        for rec in self.store.list_active():
            pid = rec.get("pid")
            if self.dev_reload:
                if rec["status"] == "queued":
                    log.info("re-queueing run %s after a reload", rec["id"])
                    self.submit(rec["id"])
                    continue
                if pid and _alive(pid):
                    log.info("adopting worker %s of run %s after a reload", pid, rec["id"])
                    self._adopt(rec["id"], pid)
                    continue
            if pid and _is_our_worker(pid):
                log.warning("killing worker %s left over from a previous API process (run %s)", pid, rec["id"])
                _kill_group(pid, signal.SIGKILL)
            if self.store.finish(rec["id"], "failed", error=STOPPED_MSG):
                self.store.emit(rec["id"], "run_failed", error=STOPPED_MSG, recovered=True)
                failed.append(rec["id"])
        if failed:
            log.info("marked %d interrupted run(s) failed: %s", len(failed), ", ".join(failed))
        return failed

    def _adopt(self, run_id: str, pid: int) -> None:
        with self._lock:
            self._adopted[run_id] = pid

        def watch():
            while _alive(pid):
                time.sleep(0.5)
            _kill_group(pid, signal.SIGKILL)
            with self._lock:
                self._adopted.pop(run_id, None)
                cancelled = run_id in self._cancelled
                self._cancelled.discard(run_id)
            if self._stopping:
                return
            if cancelled:
                if self.store.finish(run_id, "cancelled", error="Cancelled."):
                    self.store.emit(run_id, "run_cancelled", reason="cancelled while running")
            elif self.store.finish(run_id, "failed", error="The worker exited without finishing the run."):
                self.store.emit(run_id, "run_failed", error="The worker exited without finishing the run.")

        threading.Thread(target=watch, name=f"adopted-{run_id}", daemon=True).start()

    def shutdown(self) -> None:
        self._stopping = True
        if self.dev_reload:
            # A reload: running workers keep fitting (they watch the reloader, not us) and queued runs stay
            # queued; the next API process adopts them in recover().
            for _ in self._threads:
                self._q.put(None)
            return
        with self._lock:
            procs = dict(self._procs)
        for run_id, p in procs.items():
            _kill_group(p.pid)
        for run_id, p in procs.items():
            try:
                p.wait(10)
            except subprocess.TimeoutExpired:
                _kill_group(p.pid, signal.SIGKILL)
        for _ in self._threads:
            self._q.put(None)
        # Queued and running runs are failed by recover() on the next start; doing it here too keeps the
        # record right if the API is never started again.
        for rec in self.store.list_active():
            if self.store.finish(rec["id"], "failed", error=STOPPED_MSG):
                self.store.emit(rec["id"], "run_failed", error=STOPPED_MSG)

    # -- runs -----------------------------------------------------------------

    def run_dir(self, run_id: str) -> Path:
        return self.settings.runs_dir / run_id

    def submit(self, run_id: str) -> None:
        self._q.put(run_id)

    def cancel(self, run_id: str) -> bool:
        rec = self.store.get(run_id)
        if not rec or rec["status"] not in ("queued", "running"):
            return False
        with self._lock:
            self._cancelled.add(run_id)
            p = self._procs.get(run_id)
            adopted = self._adopted.get(run_id)
        if p is not None:
            _kill_group(p.pid)
        elif adopted is not None:
            _kill_group(adopted)
        elif self.store.finish(run_id, "cancelled", error="Cancelled before it started."):
            self.store.emit(run_id, "run_cancelled", reason="cancelled while queued")
        return True

    def _loop(self) -> None:
        while True:
            run_id = self._q.get()
            if run_id is None or self._stopping:
                return
            try:
                self._run(run_id)
            except Exception:  # noqa: BLE001
                log.exception("run %s crashed in the runner", run_id)
                if self.store.finish(run_id, "failed", error="The job runner crashed; see the API log."):
                    self.store.emit(run_id, "run_failed", error="The job runner crashed; see the API log.")

    def _run(self, run_id: str) -> None:
        rec = self.store.get(run_id)
        if not rec or rec["status"] != "queued":
            return
        d = self.run_dir(run_id)
        env = {**os.environ, "PYTHONUNBUFFERED": "1", "MLFLOW_DISABLE_AGENT_HINT": "1",
               "ENSEMBLE_DATA_DIR": str(self.settings.data_dir),
               # Ray workers (parallel folds, dynamic stacking) must import app.* and ensemble_models too.
               "PYTHONPATH": os.pathsep.join(p for p in (str(config.BACKEND_DIR), os.environ.get("PYTHONPATH", "")) if p)}
        with open(d / "worker.log", "ab") as out:
            # The worker stops if the process it watches dies: the API, or under dev reload the reloader above it.
            anchor = [str(os.getppid()), "anchor"] if self.dev_reload else [str(os.getpid())]
            p = subprocess.Popen([sys.executable, "-m", "app.engine.worker", str(d), *anchor],
                                 cwd=config.BACKEND_DIR, env=env, stdout=out, stderr=subprocess.STDOUT,
                                 start_new_session=True)
        with self._lock:
            self._procs[run_id] = p
        self.store.mark_running(run_id, p.pid)
        code = p.wait()
        with self._lock:
            self._procs.pop(run_id, None)
            cancelled = run_id in self._cancelled
            self._cancelled.discard(run_id)
        _kill_group(p.pid, signal.SIGKILL)  # anything the worker left behind (Ray)
        if self._stopping:
            if self.store.finish(run_id, "failed", error=STOPPED_MSG):
                self.store.emit(run_id, "run_failed", error=STOPPED_MSG)
            return
        if cancelled:
            if self.store.finish(run_id, "cancelled", error="Cancelled."):
                self.store.emit(run_id, "run_cancelled", reason="cancelled while running")
            return
        if self.store.finish(run_id, "failed", error=f"The worker exited with code {code}. {self._tail(d)}".strip()):
            self.store.emit(run_id, "run_failed", error=f"The worker exited with code {code}.", log_tail=self._tail(d))

    @staticmethod
    def _tail(d: Path, n: int = 2000) -> str:
        try:
            return (d / "worker.log").read_text(errors="replace")[-n:]
        except OSError:
            return ""
