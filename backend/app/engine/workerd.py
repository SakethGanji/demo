"""P22 (B §5.5, §6.6): `workerd`, the process that runs queued jobs.

    python -m app.engine.workerd --slots train=1,duck=1,light=2

A worker registers itself in `workers`, then loops: run the janitor, claim jobs for the resource classes it has free
slots for, run each in a thread. Each held job's lease is extended every HEARTBEAT_S; every 2 s the holder checks the
row, and a job that was cancelled (or re-queued by a janitor after this worker stalled) is stopped: its process group
is killed. Handlers by kind (`HANDLERS`), each `fn(ctx) -> result`:

| kind | resource | handler | what it runs |
|---|---|---|---|
| train | train | engine/runner.py `run_job` | `python -m app.engine.worker <run_dir>` (one fit) |
| lc | train | engine/learning_curve_api.py `run_job` | `python -m app.engine.learning_curve <run_dir>` |
| al | train | engine/active_learning_api.py `run_job` | `python -m app.engine.active_learning <run_dir>` |
| featurelab | train | featurelab.py `run_job` | the Feature Lab search, in this process |
| review | light | vela/review.py `run_job` | the D4 review loop (async), against the API over HTTP |
| heavy | duck | engine/pool.py `run_job` | a registry build / batch score function, in this process |
| agent | api | agent/loop.py `run_job` | a Vela agent turn; only the API process holds `api` slots |
| profile | duck | lake/stats.py `run_job` | P08's table statistics for one table (`table_stats`) |

The API process always runs an **embedded** worker (`Worker(..., embedded=True)`) with `api` slots for agent turns,
the janitor and the queue view. With `ENSEMBLE_WORKERD=embedded` (the default, so `make dev` needs nothing else) it
also takes train/duck/light slots (`ENSEMBLE_WORKER_SLOTS`, default `train=<ENSEMBLE_MAX_PARALLEL_RUNS or 1>,duck=2,
light=4`); with `ENSEMBLE_WORKERD=external` it takes none and `workerd` processes do the work.

A worker's subprocesses watch the worker's pid (not the API's), so an API restart costs no fit; a worker killed with
`kill -9` takes its subprocesses with it, and its leases are re-queued by any other worker's janitor (at once on the same
host: the dead pid is noticed; otherwise when the lease runs out, ≤ 60 s + one janitor pass).
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

from ..jobs import queue as Q
from .jobs import dev_reload, kill_group, spawn

log = logging.getLogger("ensemble.workerd")

HANDLERS: dict[str, str] = {
    "train": "app.engine.runner:run_job",
    "lc": "app.engine.learning_curve_api:run_job",
    "al": "app.engine.active_learning_api:run_job",
    "featurelab": "app.featurelab_api:run_job",
    "review": "app.vela.review:run_job",
    "heavy": "app.engine.pool:run_job",
    "agent": "app.agent.loop:run_job",
    "profile": "app.lake.stats:run_job",
    "migrate": "app.lake.migrate:run_job",  # P10
}
# Called by the janitor when a job ran out of attempts (its holder died every time): close what the job drives.
DEAD_HOOKS: dict[str, str] = {
    "train": "app.engine.runner:on_dead",
    "lc": "app.engine.learning_curve_api:on_dead",
    "al": "app.engine.active_learning_api:on_dead",
    "featurelab": "app.featurelab_api:on_dead",
    "review": "app.vela.review:on_dead",
    "heavy": "app.engine.pool:on_dead",
}
POLL_S = 1.0
CHECK_S = 2.0


def _resolve(spec: str) -> Callable[..., Any]:
    mod, fn = spec.split(":")
    return getattr(importlib.import_module(mod), fn)


class Cancelled(Exception):
    """The job was cancelled (or lost its lease) while it ran."""


class JobContext:
    def __init__(self, worker: "Worker", job: dict[str, Any]) -> None:
        self.worker, self.job = worker, job
        self.payload: dict[str, Any] = job.get("payload") or {}
        self.state = worker.state
        self.embedded = worker.embedded
        self.stop_reason: str | None = None  # 'cancelled' | 'lost' | 'shutdown'
        self._procs: list[subprocess.Popen] = []
        self._lock = threading.Lock()

    @property
    def attempt(self) -> int:
        return int(self.job.get("attempts") or 1)

    @property
    def cancelled(self) -> bool:
        return self.stop_reason == "cancelled"

    @property
    def stopping(self) -> bool:
        return self.stop_reason is not None

    def check(self) -> None:
        if self.stop_reason:
            raise Cancelled(self.stop_reason)

    def watch_pid(self) -> str:
        """The pid a job subprocess watches: this worker (under dev reload in the API: the reloader above it)."""
        return str(os.getppid()) if (self.embedded and dev_reload()) else str(os.getpid())

    def run_process(self, module: str, args: list[str], log_path: Path, extra_env: dict[str, str] | None = None,
                    on_start: Callable[[int], None] | None = None) -> int:
        """Run `python -m module args` in its own process group; killed if the job is stopped. Returns the exit code."""
        p = spawn(module, args, log_path, extra_env)
        with self._lock:
            self._procs.append(p)
        if on_start:
            on_start(p.pid)
        try:
            if self.stop_reason:
                kill_group(p.pid)
            return p.wait()
        finally:
            kill_group(p.pid, signal.SIGKILL)  # anything it left behind (Ray)
            with self._lock:
                self._procs.remove(p)

    def wait_pid(self, pid: int, module: str) -> None:
        """Follow a process started by an earlier holder (dev reload), killing it if the job is stopped."""
        from .jobs import alive

        while alive(pid, module):
            if self.stop_reason:
                kill_group(pid)
            time.sleep(0.5)
        kill_group(pid, signal.SIGKILL)

    def kill(self) -> None:
        with self._lock:
            procs = list(self._procs)
        for p in procs:
            kill_group(p.pid)

    def run_async(self, coro_fn: Callable[[], Any]) -> Any:
        """Run a coroutine: on the API's own event loop when embedded (in-process ASGI calls), else in a new loop."""
        if self.embedded and self.worker.loop is not None:
            return asyncio.run_coroutine_threadsafe(coro_fn(), self.worker.loop).result()
        return asyncio.run(coro_fn())


def parse_slots(text: str | None, default: dict[str, int]) -> dict[str, int]:
    if not text:
        return dict(default)
    out: dict[str, int] = {}
    for part in text.split(","):
        if not part.strip():
            continue
        k, _, v = part.partition("=")
        k = k.strip()
        if k not in Q.RESOURCES and k != "mem_mb":
            raise SystemExit(f"--slots: unknown resource {k!r}; one of {', '.join(Q.RESOURCES)} (and mem_mb)")
        out[k] = int(v or 1)
    return out


class Worker:
    def __init__(self, state: Any, slots: dict[str, int], *, role: str = "workerd", embedded: bool = False,
                 loop: asyncio.AbstractEventLoop | None = None) -> None:
        from .. import __version__

        self.state, self.slots, self.embedded, self.loop = state, dict(slots), embedded, loop
        self.db = state.db
        self.id = Q.worker_id(role)
        self.version = __version__
        self.held: dict[str, JobContext] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self.hooks: list[Callable[[], None]] = []  # extra periodic work (the API's success poller)
        self.app = None  # embedded: the FastAPI app (in-process ASGI calls, the agent manager)

    # -- lifecycle

    def start(self) -> None:
        Q.register_worker(self.db, self.id, self.slots, self.version)
        log.info("worker %s up, slots %s", self.id, self.slots)
        for name, fn in (("claim", self._claim_loop), ("beat", self._beat_loop), ("janitor", self._janitor_loop)):
            t = threading.Thread(target=fn, name=f"{self.id.split('@')[0]}-{name}", daemon=True)
            t.start()
            self._threads.append(t)

    def stop(self, requeue: bool = True, kill: bool = True) -> None:
        """Stop claiming; put held jobs back on the queue (another worker resumes them) and stop their processes.
        `kill=False` (the API under dev reload): job processes keep running; the next holder adopts them."""
        self._stop.set()
        with self._lock:
            held = dict(self.held)
        for jid, ctx in held.items():
            ctx.stop_reason = "shutdown"
            if kill:
                ctx.kill()
            if requeue:
                Q.requeue(self.db, jid, f"worker {self.id} stopped")
        try:
            Q.unregister_worker(self.db, self.id)
        except Exception:  # noqa: BLE001
            pass

    def free(self, resource: str) -> int:
        with self._lock:
            used = sum(1 for c in self.held.values() if c.job["resource"] == resource)
        return max(0, int(self.slots.get(resource, 0)) - used)

    def info(self) -> dict[str, Any]:
        with self._lock:
            return {"id": self.id, "slots": self.slots, "held": [{"id": j, "kind": c.job["kind"], "ref_id": c.job.get("ref_id")}
                                                               for j, c in self.held.items()]}

    # -- loops

    def _claim_loop(self) -> None:
        while not self._stop.is_set():
            got = False
            for res in Q.RESOURCES:
                if self.free(res) <= 0:  # `api` slots exist only in the API's embedded worker
                    continue
                try:
                    mem_free = int(self.slots.get("mem_mb") or (1 << 30)) - self._mem_held()
                    job = Q.claim(self.db, self.id, res, mem_free)
                except Exception:  # noqa: BLE001
                    log.exception("claim failed")
                    job = None
                if job:
                    got = True
                    self._launch(job)
            if not got:
                self._stop.wait(POLL_S)

    def _mem_held(self) -> int:
        with self._lock:
            return sum(int(c.job.get("mem_mb") or 0) for c in self.held.values())

    def _launch(self, job: dict[str, Any]) -> JobContext:
        ctx = JobContext(self, job)
        with self._lock:
            self.held[job["id"]] = ctx
        t = threading.Thread(target=self._execute, args=(ctx,), name=f"job-{job['kind']}-{job['id']}", daemon=True)
        t.start()
        return ctx

    def adopt(self, job: dict[str, Any]) -> JobContext:
        """Run a job this process leased itself (claim_id)."""
        return self._launch(job)

    def _execute(self, ctx: JobContext) -> None:
        job = ctx.job
        log.info("%s: %s %s (%s %s, attempt %s)", self.id, job["kind"], job["id"], job.get("ref_kind"), job.get("ref_id"),
                 job["attempts"])
        try:
            spec = HANDLERS.get(job["kind"])
            if not spec:
                raise RuntimeError(f"no handler for job kind {job['kind']!r}")
            result = _resolve(spec)(ctx)
            if ctx.stop_reason == "shutdown":
                return  # requeued by stop()
            if ctx.cancelled:
                Q.finish(self.db, job["id"], None, "cancelled", result=result, error="Cancelled.")
            else:
                Q.finish(self.db, job["id"], self.id, "succeeded", result=result)
        except Cancelled:
            if ctx.stop_reason == "cancelled":
                Q.finish(self.db, job["id"], None, "cancelled", error="Cancelled.")
        except Exception as e:  # noqa: BLE001
            if ctx.stop_reason == "shutdown":
                return
            log.exception("job %s (%s) failed", job["id"], job["kind"])
            Q.finish(self.db, job["id"], self.id, "failed", error=f"{type(e).__name__}: {e}")
        finally:
            with self._lock:
                self.held.pop(job["id"], None)

    def _beat_loop(self) -> None:
        last_extend = 0.0
        last_worker = 0.0
        while not self._stop.is_set():
            now = time.monotonic()
            extend = now - last_extend >= Q.HEARTBEAT_S
            with self._lock:
                held = dict(self.held)
            for jid, ctx in held.items():
                if ctx.stop_reason:
                    continue
                try:
                    st = Q.heartbeat(self.db, jid, self.id, extend=extend)
                except Exception:  # noqa: BLE001
                    log.exception("heartbeat failed")
                    continue
                if st != "ok":
                    log.warning("job %s is %s: stopping it", jid, st)
                    ctx.stop_reason = "cancelled" if st == "cancelled" else "lost"
                    ctx.kill()
            if extend:
                last_extend = now
            if now - last_worker >= Q.HEARTBEAT_S:
                try:
                    Q.worker_heartbeat(self.db, self.id, self.slots, self.version)
                except Exception:  # noqa: BLE001
                    log.exception("worker heartbeat failed")
                last_worker = now
            for h in self.hooks:
                try:
                    h()
                except Exception:  # noqa: BLE001
                    log.exception("worker hook failed")
            self._stop.wait(CHECK_S)

    def _janitor_loop(self) -> None:
        while not self._stop.is_set():
            try:
                out = Q.janitor(self.db)
                for j in out["requeued"]:
                    log.warning("janitor: re-queued %s %s (%s)", j["id"], j.get("leased_by"), j["error"])
                for j in out["failed"]:
                    log.warning("janitor: failed %s (%s)", j["id"], j["error"])
                    spec = DEAD_HOOKS.get(Q.get(self.db, j["id"])["kind"])
                    if spec:
                        try:
                            _resolve(spec)(self.state, Q.get(self.db, j["id"]))
                        except Exception:  # noqa: BLE001
                            log.exception("dead hook for %s failed", j["id"])
            except Exception:  # noqa: BLE001
                log.exception("janitor failed")
            self._stop.wait(Q.JANITOR_S)


def embedded_slots() -> dict[str, int]:
    """The API's own worker: `api` always; the rest only with ENSEMBLE_WORKERD=embedded (the default)."""
    mode = os.environ.get("ENSEMBLE_WORKERD", "embedded").strip().lower()
    if mode == "external":
        return {"api": 16}
    default = {"train": int(os.environ.get("ENSEMBLE_MAX_PARALLEL_RUNS", "1")), "duck": int(os.environ.get("ENSEMBLE_HEAVY_WORKERS", "2")),
               "light": 4}
    return {**parse_slots(os.environ.get("ENSEMBLE_WORKER_SLOTS"), default), "api": 16}


def build_state(settings) -> SimpleNamespace:
    """What handlers read from `app.state`, built without the web app (an external worker)."""
    from ..datasets import DatasetStore
    from ..db import Database
    from ..lake import duck
    from ..lake.store import open_store
    from ..registry.store import RegistryStore
    from .store import RunStore

    db = Database(settings.db_path)
    duck.set_spill_root(settings.duck_spill_dir)
    st = SimpleNamespace(settings=settings, db=db, runs=RunStore(db), registry=RegistryStore(db))
    st.datasets = DatasetStore(settings, db)
    st.catalog = st.datasets.catalog
    st.lake = open_store(settings)
    return st


def main() -> None:
    ap = argparse.ArgumentParser(prog="python -m app.engine.workerd")
    ap.add_argument("--slots", default=os.environ.get("ENSEMBLE_WORKER_SLOTS"),
                    help="resource=count,… (train, duck, light; mem_mb=N caps the sum of reservations)")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s[workerd %(process)d]: %(message)s")
    os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")
    from .. import config, llm

    settings = config.load_settings()
    llm.configure(settings.data_dir)
    slots = parse_slots(a.slots, {"train": 1, "duck": 1, "light": 2})
    slots.pop("api", None)
    w = Worker(build_state(settings), slots)
    done = threading.Event()

    def bye(signum, _frame):
        log.info("signal %s: stopping; held jobs go back on the queue", signum)
        w.stop(requeue=True)
        done.set()

    signal.signal(signal.SIGTERM, bye)
    signal.signal(signal.SIGINT, bye)
    w.start()
    while not done.wait(1):
        pass
    sys.exit(0)


if __name__ == "__main__":
    main()
