"""Heavy predictor work in worker processes, so the API process stays responsive.

SHAP, on-demand permutation importance, Try-It explanations, model-version builds and batch scoring are
CPU-bound Python (Kernel SHAP's coalitions, AutoGluon's predict on many rows). Run in the API's thread pool they
hold the GIL and stall every other request (S4 saw /api/runs/{id} time out while SHAP ran). Here they run in a
small `ProcessPoolExecutor` (spawn context, `ENSEMBLE_HEAVY_WORKERS` processes, default 2).

- Each worker process loads predictors itself and keeps a few cached, keyed by path and by the modification
  times of the predictor's own files, so a predictor saved again (a new threshold, a custom ensemble) is reloaded.
- Workers exit when the API process goes away (they watch their parent's pid).
- A crashed worker (BrokenProcessPool) is replaced on the next call.
- `run(fn, *args)` is awaitable from a route; `submit(fn, *args)` returns a concurrent future (background jobs).
- P22: the background jobs that own a DB record (a registry version build, a batch score: `DURABLE`) are not run
  here but queued as `heavy` jobs on the durable queue (`jobs/queue.py`): a worker runs them, an API restart does not
  cut them off, and the future `submit` returns resolves when the job finishes (polled by the API's worker).
  Interactive calls (SHAP, Try It, importance, Autopilot's waits) stay on this pool.

Job functions must be importable top-level functions (spawn pickles them by reference).
"""

from __future__ import annotations

import asyncio
import logging
import multiprocessing as mp
import os
import threading
import time
from collections import OrderedDict
from concurrent.futures import Future, ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger("ensemble.pool")

_PARENT_PID: int | None = None


def _init_worker(parent_pid: int, backend_dir: str) -> None:
    """Runs once in each worker process."""
    global _PARENT_PID
    import sys

    _PARENT_PID = parent_pid
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)
    os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")
    try:  # the API's work comes first: a heavy job should not starve the event loop's process of CPU
        os.nice(5)
    except OSError:
        pass
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s[pool]: %(message)s")

    def watch():
        while True:
            if os.getppid() != parent_pid:
                os._exit(3)
            time.sleep(1)

    threading.Thread(target=watch, daemon=True).start()


def warm() -> dict[str, Any]:
    """Import AutoGluon (and the custom models) so the first real job does not pay for it."""
    t0 = time.time()
    from autogluon.tabular import TabularPredictor  # noqa: F401

    import ensemble_models  # noqa: F401

    return {"pid": os.getpid(), "seconds": time.time() - t0}


def ping() -> dict[str, Any]:
    return {"pid": os.getpid()}


# --------------------------------------------------------------------------- predictor cache (inside workers)

_CACHE: OrderedDict[tuple[str, float], Any] = OrderedDict()
_CACHE_SIZE = 3
_WARM: dict[str, Any] = {}


def warm_predictor(path: str, hold_s: float = 0.3) -> dict[str, Any]:
    """Load (and so warm) a predictor in this worker. Held briefly so that N submissions land on N workers."""
    t0 = time.perf_counter()
    load_predictor(path)
    time.sleep(hold_s)
    return {"pid": os.getpid(), "seconds": time.perf_counter() - t0 - hold_s, "warm": _WARM.get(str(path))}


def _signature(path: str) -> float:
    p = Path(path)
    sig = 0.0
    for name in ("predictor.pkl", "learner.pkl", "models/trainer.pkl"):
        f = p / name
        try:
            sig = max(sig, f.stat().st_mtime_ns / 1e9)
        except OSError:
            pass
    return sig


def load_predictor(path: str):
    """A TabularPredictor, loaded once per worker and reloaded when its files change."""
    key = (str(path), _signature(path))
    if key in _CACHE:
        _CACHE.move_to_end(key)
        return _CACHE[key]
    from autogluon.tabular import TabularPredictor

    import ensemble_models  # noqa: F401 — the custom models must be importable to unpickle them

    p = TabularPredictor.load(str(path), verbosity=0, require_py_version_match=False)
    # Warm on load (F3): persist the served model(s) in memory and make a call, so the first real call is not the
    # one that reads models from disk and builds the pipeline's caches.
    try:
        from ..registry.fastpath import warm as _warm

        _WARM[str(path)] = _warm(p)
    except Exception:  # noqa: BLE001 — warming is an optimisation
        pass
    for k in [k for k in _CACHE if k[0] == str(path)]:
        _CACHE.pop(k, None)
    _CACHE[key] = p
    while len(_CACHE) > _CACHE_SIZE:
        _CACHE.popitem(last=False)
    return p


# --------------------------------------------------------------------------- the pool (in the API process)


# Background functions that run as durable `heavy` jobs: "module:function" → (ref_kind, index of the ref id in args)
DURABLE: dict[str, tuple[str, int]] = {
    "app.registry.build:build_version": ("model_version", 1),
    "app.registry.scoring:score_batch": ("batch", 1),
    "app.registry.scoring:score_flow_batch": ("batch", 1),
}


def _qualname(fn: Callable[..., Any]) -> str:
    return f"{getattr(fn, '__module__', '')}:{getattr(fn, '__name__', '')}"


def run_job(ctx) -> dict[str, Any]:
    """Worker side of a `heavy` job: the function, here (a workerd) or on the API's process pool (embedded worker)."""
    import importlib

    mod, name = ctx.payload["fn"].split(":")
    if ctx.payload["fn"] not in DURABLE:
        raise RuntimeError(f"{ctx.payload['fn']} is not a durable heavy function")
    fn = getattr(importlib.import_module(mod), name)
    args = ctx.payload.get("args") or []
    pool = getattr(ctx.state, "heavy", None) if ctx.embedded else None
    out = pool._executor().submit(fn, *args).result() if pool is not None else fn(*args)
    try:
        import json

        json.dumps(out, default=str)
        return {"value": out}
    except (TypeError, ValueError):
        return {"value": None}


def on_dead(state, job: dict[str, Any]) -> None:
    msg = f"The worker stopped every time this was tried ({job.get('error')})."
    with state.db.connect() as con:
        if job.get("ref_kind") == "model_version":
            con.execute("UPDATE model_versions SET status = 'failed', error = ? WHERE id = ? AND status = 'building'",
                        (msg, job["ref_id"]))
        elif job.get("ref_kind") == "batch":
            con.execute("UPDATE batch_jobs SET status = 'failed', error = ?, stage = 'failed' WHERE id = ?"
                        " AND status IN ('queued', 'running')", (msg, job["ref_id"]))


class HeavyPool:
    def __init__(self, workers: int | None = None, db: Any = None) -> None:
        from .. import config

        self.db = db  # P22: set → DURABLE functions go on the job queue
        self._durable: dict[str, Future] = {}
        self.workers = workers or int(os.environ.get("ENSEMBLE_HEAVY_WORKERS", "2"))
        self._backend = str(config.BACKEND_DIR)
        self._lock = threading.Lock()
        self._ex: ProcessPoolExecutor | None = None
        self.started_at = time.time()
        self.submitted = 0
        self.active = 0

    def _executor(self) -> ProcessPoolExecutor:
        with self._lock:
            if self._ex is None:
                self._ex = ProcessPoolExecutor(max_workers=self.workers, mp_context=mp.get_context("spawn"),
                                               initializer=_init_worker, initargs=(os.getpid(), self._backend))
            return self._ex

    def start(self) -> None:
        """Start the processes and import AutoGluon in them, in the background."""
        ex = self._executor()
        for _ in range(self.workers):
            ex.submit(warm)

    def submit(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Future:
        q = _qualname(fn)
        if self.db is not None and q in DURABLE and not kwargs:
            return self._enqueue(q, list(args))
        for attempt in (1, 2):
            ex = self._executor()
            try:
                fut = ex.submit(fn, *args, **kwargs)
            except (BrokenProcessPool, RuntimeError) as e:
                if attempt == 2:
                    raise
                log.warning("worker pool broken (%s); starting a new one", e)
                self._reset(ex)
                continue
            with self._lock:
                self.submitted += 1
                self.active += 1

            def done(f: Future, ex=ex) -> None:
                with self._lock:
                    self.active -= 1
                if isinstance(f.exception(), BrokenProcessPool):
                    self._reset(ex)

            fut.add_done_callback(done)
            return fut
        raise RuntimeError("unreachable")

    def _enqueue(self, q: str, args: list[Any]) -> Future:
        from ..catalog import governance as G
        from ..jobs import queue as Q

        ref_kind, i = DURABLE[q]
        job = Q.enqueue(self.db, "heavy", {"fn": q, "args": args}, resource="duck", by=G.current_actor() or "system",
                        ref_kind=ref_kind, ref_id=str(args[i]) if len(args) > i else None)
        fut: Future = Future()
        fut.set_running_or_notify_cancel()
        with self._lock:
            self._durable[job["id"]] = fut
            self.submitted += 1
        return fut

    def poll_durable(self) -> None:
        """Resolve the futures of finished `heavy` jobs (called every ~2 s by the API's worker)."""
        from ..jobs import queue as Q

        with self._lock:
            pending = dict(self._durable)
        for jid, fut in pending.items():
            j = Q.get(self.db, jid)
            if not j or j["status"] not in Q.TERMINAL:
                continue
            with self._lock:
                self._durable.pop(jid, None)
            if j["status"] == "succeeded":
                fut.set_result((j.get("result") or {}).get("value") if isinstance(j.get("result"), dict) else None)
            else:
                fut.set_exception(RuntimeError(j.get("error") or j["status"]))

    async def run(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        return await asyncio.wrap_future(self.submit(fn, *args, **kwargs))

    def _reset(self, broken: ProcessPoolExecutor) -> None:
        with self._lock:
            if self._ex is broken:
                self._ex = None
        broken.shutdown(wait=False, cancel_futures=True)

    def warm_path(self, path: str) -> list[Future]:
        """Warm a predictor in every worker (one submission per worker, each held briefly)."""
        return [self.submit(warm_predictor, str(path)) for _ in range(self.workers)]

    def info(self) -> dict[str, Any]:
        return {"workers": self.workers, "active_jobs": self.active, "submitted": self.submitted,
                "durable_pending": len(self._durable)}

    def shutdown(self) -> None:
        with self._lock:
            ex, self._ex = self._ex, None
        if ex is not None:
            ex.shutdown(wait=False, cancel_futures=True)
            for p in list((getattr(ex, "_processes", None) or {}).values()):
                try:
                    p.terminate()
                except Exception:  # noqa: BLE001
                    pass
