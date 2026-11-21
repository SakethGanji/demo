"""P02: the DuckDB runtime (plan R16, B §5.1, §7 risk 4).

- Every connection is in-memory (data stays in Parquet) with a `memory_limit`, a thread count, a spill directory and
  a **spill cap** (`max_temp_directory_size`): a query that would spill more fails instead of filling the disk.
  Extensions are never downloaded at run time.
- **Interactive** (API requests): one process-wide connection; each query runs on its own `cursor()`; at most
  `ENSEMBLE_DUCK_INTERACTIVE_SLOTS` (8) at once (a semaphore; a request that cannot get a slot within the timeout
  gets `DuckBusy`, 429); every query is interrupted after `ENSEMBLE_DUCK_INTERACTIVE_TIMEOUT_S` (10 s) → `DuckTimeout`
  (504).
- **Job**: `connect("job")` gives a fresh connection with the job limits (2 GB, cores / 2); the caller closes it.
- `disk_guard(path, need)`: before a write job, free disk must be ≥ `need` (callers pass 3× the input size).

Paging rules for callers (B §5.1): pages are key ranges on `__row_id`, never `ORDER BY … LIMIT` over a whole table.
"""

from __future__ import annotations

import shutil
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Literal

import warnings

import duckdb

from .. import config

# DuckDB 2: an Arrow result of execute() is one extra copy of a page (≤ 500 rows); the advice to use con.sql() is noise
warnings.filterwarnings("ignore", message="Arrow output on a DB-API result", category=UserWarning)


class DuckError(Exception):
    status, code = 500, "duck_error"


class DuckTimeout(DuckError):
    status, code = 504, "query_timeout"


class DuckBusy(DuckError):
    status, code = 429, "query_busy"


class DiskFull(DuckError):
    status, code = 507, "disk_full"


_spill_root: Path | None = None


def set_spill_root(p: Path) -> None:
    """Called once at startup (Settings.duck_spill_dir). Until then a per-process temp dir is used."""
    global _spill_root
    _spill_root = Path(p)
    _spill_root.mkdir(parents=True, exist_ok=True)


def _spill(purpose: str) -> Path:
    import os
    import tempfile

    base = _spill_root or Path(tempfile.gettempdir()) / "ensemble-duck"
    d = base / f"{purpose}-{os.getpid()}-{threading.get_ident()}-{time.monotonic_ns()}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def configure(con: duckdb.DuckDBPyConnection, *, mem_mb: int, threads: int, spill_dir: Path, spill_gb: float) -> None:
    sd = str(spill_dir).replace("'", "''")
    for s in (f"SET memory_limit='{int(mem_mb)}MB'", f"SET threads={int(threads)}", "SET preserve_insertion_order=false",
              f"SET temp_directory='{sd}'", f"SET max_temp_directory_size='{float(spill_gb):g}GB'",
              "SET autoinstall_known_extensions=false", "SET autoload_known_extensions=false",
              "SET enable_object_cache=true", "SET enable_progress_bar=false"):
        con.execute(s)


def connect(purpose: Literal["interactive", "job"] = "job", *, mem_mb: int | None = None, threads: int | None = None,
            spill_gb: float | None = None) -> duckdb.DuckDBPyConnection:
    """A new in-memory connection with the limits for `purpose`. Close it when done (a job: in a finally)."""
    con = duckdb.connect(":memory:")
    if purpose == "interactive":
        mem, thr = mem_mb or config.duck_interactive_mb(), threads or config.duck_interactive_threads()
    else:
        mem, thr = mem_mb or config.duck_job_mb(), threads or config.duck_job_threads()
    configure(con, mem_mb=mem, threads=thr, spill_dir=_spill(purpose), spill_gb=spill_gb or config.duck_spill_gb())
    return con


@contextmanager
def job(**kw: Any) -> Iterator[duckdb.DuckDBPyConnection]:
    """A job connection that is closed, and its spill directory removed, on exit."""
    con = connect("job", **kw)
    spill = con.execute("SELECT current_setting('temp_directory')").fetchone()[0]
    try:
        yield con
    finally:
        con.close()
        if spill and Path(spill).name.startswith("job-"):  # the per-connection directory _spill() made
            shutil.rmtree(spill, ignore_errors=True)


def settings_of(con: duckdb.DuckDBPyConnection) -> dict[str, str]:
    names = ("memory_limit", "threads", "temp_directory", "max_temp_directory_size", "preserve_insertion_order",
             "autoinstall_known_extensions", "autoload_known_extensions")
    q = ",".join(f"'{n}'" for n in names)
    return {r[0]: r[1] for r in con.execute(f"SELECT name, value FROM duckdb_settings() WHERE name IN ({q})").fetchall()}


class Interactive:
    """The process-wide interactive connection: a semaphore of N slots, one cursor per query, an interrupt timer."""

    def __init__(self, slots: int | None = None, timeout_s: float | None = None) -> None:
        self.con = connect("interactive")
        self.slots = slots or config.duck_interactive_slots()
        self.timeout_s = timeout_s or config.duck_interactive_timeout_s()
        self.sem = threading.BoundedSemaphore(self.slots)
        self._lock = threading.Lock()
        self.running = 0
        self.stats = {"queries": 0, "timeouts": 0, "busy": 0}

    def query(self, sql: str, params: list[Any] | None = None, *, timeout_s: float | None = None,
              fetch: Literal["all", "arrow", "df", "one"] = "all", bind: dict[str, Any] | None = None) -> Any:
        """Run one read query within the limits. Raises DuckBusy (no slot in time) or DuckTimeout (interrupted).
        `bind` (P08): {name: Arrow table} registered on this query's cursor only (a CSV-era version's rows)."""
        t = timeout_s or self.timeout_s
        start = time.monotonic()
        if not self.sem.acquire(timeout=t):
            self.stats["busy"] += 1
            raise DuckBusy(f"{self.slots} interactive queries are already running; try again shortly.")
        timed_out = threading.Event()
        try:
            with self._lock:
                cur = self.con.cursor()
                self.running += 1
                self.stats["queries"] += 1
            left = max(0.05, t - (time.monotonic() - start))

            def _stop() -> None:
                timed_out.set()
                try:
                    cur.interrupt()
                except Exception:  # noqa: BLE001
                    pass

            timer = threading.Timer(left, _stop)
            timer.daemon = True
            timer.start()
            try:
                for name, obj in (bind or {}).items():
                    cur.register(name, obj)
                res = cur.execute(sql, params or [])
                if fetch == "arrow":
                    return res.to_arrow_table()
                if fetch == "df":
                    return res.df()
                if fetch == "one":
                    return res.fetchone()
                return res.fetchall()
            except duckdb.InterruptException as e:
                self.stats["timeouts"] += 1
                raise DuckTimeout(f"the query took longer than {t:g} s and was stopped; filter first.") from e
            except duckdb.Error:
                if timed_out.is_set():
                    self.stats["timeouts"] += 1
                    raise DuckTimeout(f"the query took longer than {t:g} s and was stopped; filter first.")
                raise
            finally:
                timer.cancel()
                try:
                    cur.close()
                except Exception:  # noqa: BLE001
                    pass
        finally:
            with self._lock:
                self.running -= 1
            self.sem.release()

    def info(self) -> dict[str, Any]:
        return {"slots": self.slots, "running": self.running, "timeout_s": self.timeout_s, **self.stats,
                "settings": settings_of(self.con)}


_interactive: Interactive | None = None
_ilock = threading.Lock()


def interactive() -> Interactive:
    global _interactive
    with _ilock:
        if _interactive is None:
            _interactive = Interactive()
        return _interactive


def disk_guard(path: Path, need_bytes: int) -> None:
    """Refuse a write job when free disk under `path` is below `need_bytes` (callers pass 3× the input size)."""
    p = Path(path)
    while not p.exists() and p != p.parent:
        p = p.parent
    free = shutil.disk_usage(p).free
    if free < need_bytes:
        raise DiskFull(f"not enough free disk: {free / 1e9:.1f} GB free, {need_bytes / 1e9:.1f} GB needed.")
