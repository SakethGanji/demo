"""F-JOBS: progress and identity of the queued job the current thread is running.

A handler (`engine/workerd.py`) binds a `Reporter` for the job it runs; code deep in a write path (an edit, a Data
Doctor apply, a Feature Lab write) asks `job_id()` (the VersionWriter's idempotency key: a re-leased job commits its
version once) and reports `mark(pct, phase)`. Outside a job every call is a no-op, so the synchronous routes are
unchanged.

`track(con, lo, hi, phase)` follows one long DuckDB statement: a thread reads `con.query_progress()` (DuckDB answers it
from another thread while the statement runs) and maps 0–100 % into [lo, hi]. Progress never goes down.

`jobs.progress` is JSON text `{pct, phase, at}` (pct 0–1), written at most once a second, only while the job is still
leased to this worker.
"""

from __future__ import annotations

import contextvars
import logging
import os
import threading
import time
from contextlib import contextmanager
from typing import Any, Iterator

log = logging.getLogger("ensemble.jobs")

CURRENT: contextvars.ContextVar["Reporter | None"] = contextvars.ContextVar("ensemble_job", default=None)


def threshold_rows() -> int:
    """Above this many rows the long writes (edit commit, Data Doctor apply) run as queued jobs: the route answers 202
    with the job id. ENSEMBLE_JOB_ROWS, default 200,000 (config.doctor_rows()' default: where the SQL paths start)."""
    try:
        return max(0, int(os.environ.get("ENSEMBLE_JOB_ROWS", "200000")))
    except ValueError:
        return 200_000


class Reporter:
    def __init__(self, db: Any, job_id: str, worker_id: str | None, attempt: int | None = None) -> None:
        self.db, self.job_id, self.worker_id, self.attempt = db, job_id, worker_id, attempt
        self.pct, self.phase = 0.0, "started"
        self.lo, self.hi = 0.0, 1.0  # band(): inner code reports 0–1 of [lo, hi]
        self._last = 0.0
        self._lock = threading.Lock()

    def set(self, pct: float, phase: str | None = None, force: bool = False) -> None:
        from . import queue as Q

        with self._lock:
            pct = max(self.pct, min(1.0, self.lo + (self.hi - self.lo) * min(1.0, max(0.0, float(pct)))))
            changed_phase = phase is not None and phase != self.phase
            self.pct, self.phase = pct, phase or self.phase
            now = time.monotonic()
            if not (force or changed_phase or now - self._last >= 1.0):
                return
            self._last = now
            body = {"pct": round(pct, 4), "phase": self.phase}
        try:
            Q.set_progress(self.db, self.job_id, self.worker_id, body, self.attempt)
        except Exception:  # noqa: BLE001 - progress is advisory
            log.exception("progress write failed for %s", self.job_id)


@contextmanager
def bind(db: Any, job_id: str, worker_id: str | None, attempt: int | None = None) -> Iterator[Reporter]:
    r = Reporter(db, job_id, worker_id, attempt)
    tok = CURRENT.set(r)
    try:
        yield r
    finally:
        CURRENT.reset(tok)


@contextmanager
def band(lo: float, hi: float) -> Iterator[None]:
    """Code inside reports 0–1; it is shown as [lo, hi] of the job (a search, then a write)."""
    r = CURRENT.get()
    if r is None:
        yield
        return
    old = (r.lo, r.hi)
    r.lo, r.hi = old[0] + (old[1] - old[0]) * lo, old[0] + (old[1] - old[0]) * hi
    try:
        yield
    finally:
        r.lo, r.hi = old


def job_id() -> str | None:
    r = CURRENT.get()
    return r.job_id if r else None


def mark(pct: float, phase: str | None = None) -> None:
    r = CURRENT.get()
    if r is not None:
        r.set(pct, phase)


@contextmanager
def track(con: Any, lo: float, hi: float, phase: str) -> Iterator[None]:
    """Report DuckDB's progress of the statements run on `con` inside the block as [lo, hi]."""
    r = CURRENT.get()
    if r is None:
        yield
        return
    r.set(lo, phase)
    try:
        con.execute("SET enable_progress_bar = true")
        con.execute("SET enable_progress_bar_print = false")
    except Exception:  # noqa: BLE001
        pass
    stop = threading.Event()

    def poll() -> None:
        while not stop.wait(0.5):
            try:
                p = float(con.query_progress())
            except Exception:  # noqa: BLE001 - no statement running, or the connection closed
                continue
            if p >= 0:
                r.set(lo + (hi - lo) * min(p, 100.0) / 100.0)

    th = threading.Thread(target=poll, name=f"progress-{r.job_id}", daemon=True)
    th.start()
    try:
        yield
    finally:
        stop.set()
        th.join(2)
        r.set(hi, phase)
