"""P08 (plan §2.1 `table_stats`, §5.1 step 5): per-column statistics of a version table, computed once per table
content (`table_sha256`; a CSV-era table: `csv:<sha256>`) and shared by every version that carries the table.

Per column: type and kind, rows, nulls, approximate distinct, and
- numbers: min, max, mean, std, 21 quantiles (0, 5 %, …, 100 %), a 20-bin histogram, non-finite count;
- text: min / max length, `digits_only`;
- dates: min, max;
- text, bools, dates and numbers with ≤ 50 distinct values: the top 20 values with counts; `top_complete` when they are
  all the values.

They feed search (which columns can hold a query), the grid's column kinds, the diff's column statistics and PSI,
split options and Smart Intake, so none of those reads the table into pandas.

**The profile job.** `schedule()` queues a `profile` job (resource `duck`) on P22's durable job queue, deduplicated
by table content (one live job per `table_sha256`); whichever worker leases it runs `run(version_id, table)` on a job
DuckDB connection with the job memory and spill limits. Every commit schedules its new tables (`after_commit`), and a
read that finds none schedules them. `ensure(wait_s=…)` waits for a small table (the returned handle polls the job).
If the queue cannot be written (no `jobs` table), the old in-process thread runs it instead.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

from .. import frames
from . import duck
from .pages import ROW_ID, Source, kind, q, source

log = logging.getLogger("ensemble.lake.stats")

STATS_VERSION = 1
TOP_N = 20
TOP_MAX_DISTINCT = 50
QUANTILES = [i / 20 for i in range(21)]
HIST_BINS = 20
#: tables up to this many rows are profiled while the caller waits (`ensure(wait_s)`), above it only in the background
SYNC_MAX_ROWS = 5_000_000

_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="table-stats")
_running: dict[str, Future] = {}
_lock = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _db():
    return frames._db()


_cache: dict[str, dict[str, Any]] = {}


def get(identity: str) -> dict[str, Any] | None:
    """The stored stats of a table content (cached once found: a table's content never changes)."""
    hit = _cache.get(identity)
    if hit is not None:
        return hit
    from .pages import meta_con

    con = meta_con()
    try:
        r = con.execute("SELECT stats_version, computed_at, n_rows, n_cols, columns, seconds FROM table_stats WHERE table_sha256 = ?",
                        (identity,)).fetchone()
    finally:
        con.close()
    if not r or int(r["stats_version"]) != STATS_VERSION:
        return None
    out = {"table_sha256": identity, "stats_version": r["stats_version"], "computed_at": r["computed_at"],
           "n_rows": int(r["n_rows"]), "n_cols": int(r["n_cols"]), "columns": json.loads(r["columns"]), "seconds": r["seconds"]}
    if len(_cache) > 1024:
        _cache.clear()
    _cache[identity] = out
    return out


def _f(v: Any) -> Any:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return round(f, 6)


#: F-X1 C1: columns per aggregate query in compute() (up to 8 aggregates each).
STATS_CHUNK_COLS = 48


def compute(src: Source) -> dict[str, Any]:
    """Every column's statistics in a few scans (one aggregate pass, one GROUP BY per low-cardinality column, one
    histogram pass per number column)."""
    t0 = time.perf_counter()
    with duck.job() as con:
        if src.arrow is not None:
            con.register(src.rel, src.arrow)
        rel = src.rel
        sel, slots = ["count(*)"], []
        for name, t in src.columns:
            k, x = kind(t), q(name)
            base = [f"count({x})", f"approx_count_distinct({x})"]
            if k in ("integer", "number"):
                v = f"CAST({x} AS DOUBLE)"
                fin = f"isfinite({v})"
                base += [f"min({v}) FILTER (WHERE {fin})", f"max({v}) FILTER (WHERE {fin})", f"avg({v}) FILTER (WHERE {fin})",
                         f"stddev_samp({v}) FILTER (WHERE {fin})",
                         f"approx_quantile({v}, [{', '.join(str(p) for p in QUANTILES)}]) FILTER (WHERE {fin})",
                         f"count(*) FILTER (WHERE {x} IS NOT NULL AND NOT {fin})"]
            elif k == "text":
                base += [f"min(length({x}))", f"max(length({x}))", f"bool_and(regexp_full_match({x}, '[0-9]+'))"]
            elif k == "date":
                base += [f"CAST(min({x}) AS VARCHAR)", f"CAST(max({x}) AS VARCHAR)"]
            elif k == "bool":
                base += [f"count(*) FILTER (WHERE {x})"]
            slots.append((name, t, k, len(sel), len(base)))
            sel += base
        # F-X1 C1: the aggregates are run in chunks of columns. One SELECT of all of them makes DuckDB's planning
        # quadratic in the column count (a 1,000-column table took the API past 13 GB); chunks keep it linear.
        row = list(con.execute(f"SELECT {sel[0]} FROM {rel}").fetchone())
        starts = [i for _, _, _, i, _ in slots]
        for a in range(0, len(slots), STATS_CHUNK_COLS):
            lo = starts[a]
            hi = starts[a + STATS_CHUNK_COLS] if a + STATS_CHUNK_COLS < len(slots) else len(sel)
            row += list(con.execute(f"SELECT {', '.join(sel[lo:hi])} FROM {rel}").fetchone())
        n = int(row[0])
        cols = []
        for name, t, k, i, w in slots:
            v = row[i:i + w]
            c: dict[str, Any] = {"name": name, "type": t, "kind": k, "n": n, "nulls": n - int(v[0]), "distinct": int(v[1] or 0),
                                 "missing": round((n - int(v[0])) / n, 6) if n else None}
            if k in ("integer", "number"):
                c.update(min=_f(v[2]), max=_f(v[3]), mean=_f(v[4]), std=_f(v[5]),
                         quantiles=[_f(z) for z in (v[6] or [])], non_finite=int(v[7] or 0))
                c["median"] = c["quantiles"][10] if len(c["quantiles"]) == 21 else None
            elif k == "text":
                c.update(min_len=v[2], max_len=v[3], digits_only=bool(v[4]) if v[4] is not None else False)
            elif k == "date":
                c.update(min=v[2], max=v[3])
            elif k == "bool":
                c.update(n_true=int(v[2] or 0))
            cols.append(c)
        for c in cols:
            x = q(c["name"])
            if c["kind"] in ("text", "bool", "date") or c["distinct"] <= TOP_MAX_DISTINCT:
                top = con.execute(f"SELECT CAST({x} AS VARCHAR) AS v, count(*) AS k FROM {rel} WHERE {x} IS NOT NULL"
                                  f" GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT {TOP_N + 1}").fetchall()
                c["top_complete"] = len(top) <= TOP_N
                c["top"] = [{"value": r[0], "n": int(r[1])} for r in top[:TOP_N]]
            if c["kind"] in ("integer", "number") and c.get("min") is not None and c.get("max") is not None:
                lo, hi = float(c["min"]), float(c["max"])
                if hi > lo:
                    w = (hi - lo) / HIST_BINS
                    v = f"CAST({x} AS DOUBLE)"
                    h = dict(con.execute(f"SELECT least(CAST(floor(({v} - {lo!r}) / {w!r}) AS INTEGER), {HIST_BINS - 1}) AS b, count(*)"
                                         f" FROM {rel} WHERE isfinite({v}) GROUP BY 1").fetchall())
                    c["histogram"] = {"edges": [_f(lo + i * w) for i in range(HIST_BINS + 1)],
                                      "counts": [int(h.get(i, 0)) for i in range(HIST_BINS)]}
    return {"table_sha256": src.identity, "stats_version": STATS_VERSION, "computed_at": _now(), "n_rows": n,
            "n_cols": len(src.columns), "columns": cols, "seconds": round(time.perf_counter() - t0, 3)}


def _store(st: dict[str, Any]) -> None:
    with _db().connect() as con:
        con.execute("INSERT OR REPLACE INTO table_stats (table_sha256, stats_version, computed_at, n_rows, n_cols, columns, seconds)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?)", (st["table_sha256"], st["stats_version"], st["computed_at"], st["n_rows"],
                                                      st["n_cols"], json.dumps(st["columns"], default=str), st["seconds"]))


def run(version_id: str, table: str | None = None) -> dict[str, Any]:
    """The profile job body: compute and store (a no-op when the table's stats exist)."""
    src = source(version_id, table)
    _cache.pop(src.identity, None)  # the job asks the database, not this process's cache (a long-lived worker)
    have = get(src.identity)
    if have:
        return have
    st = compute(src)
    _store(st)
    log.info("table_stats %s (%s.%s): %d rows × %d columns in %.1f s", src.identity[:16], version_id, src.table, st["n_rows"],
             st["n_cols"], st["seconds"])
    return st


class _JobHandle:
    """What `schedule()` returns for a queued job: `result(timeout)` polls the job, then reads the stored stats."""

    def __init__(self, job_id: str, identity: str) -> None:
        self.job_id, self.identity = job_id, identity

    def _job(self) -> dict[str, Any] | None:
        from ..jobs import queue as Q

        return Q.get(_db(), self.job_id)

    def done(self) -> bool:
        from ..jobs import queue as Q

        j = self._job()
        return not j or j["status"] in Q.TERMINAL

    def result(self, timeout: float | None = None) -> dict[str, Any] | None:
        from ..jobs import queue as Q

        t_end = time.monotonic() + (timeout if timeout is not None else 1e9)
        while True:
            j = self._job()
            if not j or j["status"] in Q.TERMINAL:
                if j and j["status"] == "failed":
                    raise RuntimeError(j.get("error") or "the profile job failed")
                return get(self.identity)
            if time.monotonic() >= t_end:
                raise TimeoutError(f"profile job {self.job_id} still {j['status']}")
            time.sleep(0.2)


def run_job(ctx) -> dict[str, Any]:
    """P22: worker side of a `profile` job."""
    st = run(ctx.payload["version_id"], ctx.payload.get("table"))
    return {"identity": st.get("table_sha256"), "n_rows": st.get("n_rows"), "seconds": st.get("seconds")}


def schedule(version_id: str, table: str | None = None, identity: str | None = None) -> Any:
    """Queue the profile job for a table (once per content, whatever version asks)."""
    ident = identity or source_identity(version_id, table)
    try:
        from ..catalog import governance as G
        from ..jobs import queue as Q

        db = _db()
        with _lock:  # one live job per table content (across processes a duplicate is a no-op: run() finds the stats)
            live = Q.active_for(db, "table_stats", ident, "profile")
            if live is None:
                live = Q.enqueue(db, "profile", {"version_id": version_id, "table": table}, resource="duck",
                                 by=G.current_actor() or "system", ref_kind="table_stats", ref_id=ident)
        return _JobHandle(live["id"], ident)
    except Exception:  # noqa: BLE001 - no queue (an old database): profile on this process's thread
        log.exception("could not queue the profile job for %s; running it here", ident[:16])
    with _lock:
        f = _running.get(ident)
        if f is not None and not f.done():
            return f

        def job() -> dict[str, Any]:
            try:
                return run(version_id, table)
            except Exception:
                log.exception("table_stats failed for %s.%s", version_id, table)
                raise
            finally:
                with _lock:
                    _running.pop(ident, None)

        f = _pool.submit(job)
        _running[ident] = f
        return f


def source_identity(version_id: str, table: str | None = None) -> str:
    from .pages import _parquet_meta

    m = _parquet_meta(version_id, table)
    return m.identity if m is not None else frames.data_identity(version_id)


def status(identity: str) -> str:
    with _lock:
        f = _running.get(identity)
    if f is not None and not f.done():
        return "running"
    try:
        from ..jobs import queue as Q

        if Q.active_for(_db(), "table_stats", identity, "profile"):
            return "running"
    except Exception:  # noqa: BLE001
        pass
    return "missing"


def ensure(version_id: str, table: str | None = None, *, wait_s: float = 0.0, n_rows: int | None = None) -> dict[str, Any] | None:
    """The stats of a table: stored ones, else scheduled (and waited for up to `wait_s` when the table has at most
    SYNC_MAX_ROWS rows). None while they are being computed."""
    ident = source_identity(version_id, table)
    st = get(ident)
    if st:
        return st
    f = schedule(version_id, table, ident)
    if wait_s > 0 and (n_rows is None or n_rows <= SYNC_MAX_ROWS):
        try:
            return f.result(timeout=wait_s)
        except Exception:  # noqa: BLE001 - a timeout or a failure: the caller goes on without stats
            return get(ident)
    return None


def after_commit(version_id: str) -> None:
    """Schedule the profile job for each table a commit wrote (carried tables already have stats by content)."""
    try:
        with _db().connect() as con:
            rows = con.execute("SELECT table_name, table_sha256 FROM version_tables WHERE version_id = ?", (version_id,)).fetchall()
            have = {r[0] for r in con.execute("SELECT table_sha256 FROM table_stats WHERE stats_version = ?", (STATS_VERSION,))
                    } if rows else set()
        for r in rows:
            if r["table_sha256"] not in have:
                schedule(version_id, r["table_name"], r["table_sha256"])
    except Exception:  # noqa: BLE001 - stats are a cache; a commit never fails for them
        log.exception("could not schedule table_stats for %s", version_id)


def column(st: dict[str, Any] | None, name: str) -> dict[str, Any] | None:
    for c in (st or {}).get("columns") or []:
        if c["name"] == name:
            return c
    return None


def public(st: dict[str, Any] | None, version_id: str, table: str, identity: str) -> dict[str, Any]:
    """The GET …/stats body."""
    if not st:
        return {"version_id": version_id, "table": table, "table_sha256": identity, "status": status(identity) if identity else "missing",
                "columns": None}
    return {"version_id": version_id, "table": table, "status": "ready", **st}


# -- bounded frames for the pandas-only analyses (P05's note: split options, Smart Intake at 10M) ----------------------

#: a table above this many rows is analysed on a deterministic sample of about this many rows
SAMPLE_ROWS = 200_000
_PRIME = 1_000_003


def sample_frame(version_id: str, n: int = SAMPLE_ROWS, columns: list[str] | None = None) -> tuple[Any, dict[str, Any] | None]:
    """(frame, sample info or None). A table of at most `n` rows is read whole through frames; a larger Parquet table
    is read as a deterministic, engine-independent sample of about `n` rows (`(__row_id mod p) · 7919 mod p < k`, the
    same rows every time), typed as engine_v1, index = __row_id. Never a full read of a big table."""
    import pandas as pd

    src = source(version_id)
    if src.storage == "csv" or src.n_rows <= n:
        return frames.read_version(version_id, columns), None
    k = max(1, int(_PRIME * n / src.n_rows))
    names = [c for c in src.names if columns is None or c in columns]
    with duck.job(threads=max(2, (__import__("os").cpu_count() or 2))) as con:
        at = con.execute(f"SELECT {q(ROW_ID)}, {', '.join(q(c) for c in names)} FROM {src.rel}"
                         f" WHERE (({q(ROW_ID)} % {_PRIME}) * 7919) % {_PRIME} < {k} ORDER BY {q(ROW_ID)}").to_arrow_table()
    df = pd.DataFrame({c: frames._v1_column(at.column(c), c) for c in names}, columns=names)
    df.index = pd.Index(at.column(ROW_ID).to_numpy(zero_copy_only=False))
    return df, {"rows": int(len(df)), "of": src.n_rows, "how": f"a deterministic sample of {len(df):,} of {src.n_rows:,} rows by __row_id"}


_ID_NAME = r"(^id$|_id$|^id_|uuid|^key$|(customer|account|client|member)(_?(id|no|number|key|ref))?$)"


def split_columns(version_id: str, target: str | None) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any] | None]:
    """`splits.date_and_group_columns` for a big table without reading it into pandas: date columns are found on the
    sample (their range and distinct count come from the whole table when the column is a DATE / TIMESTAMP), entity
    columns from whole-table counts (exact distinct and rows per value, in DuckDB, for the candidates only)."""
    import re

    from .. import splits as splits_mod

    src = source(version_id)
    df, info = sample_frame(version_id, 20_000)
    if info is None:  # small: the original, whole-frame answer
        return (*splits_mod.date_and_group_columns(df, target), None)
    dates, _ = splits_mod.date_and_group_columns(df, target)
    st = get(src.identity)
    I = duck.interactive()
    if st:
        cnt = {c["name"]: (c["n"] - c["nulls"], c["distinct"]) for c in st["columns"]}
    else:
        aggs = ", ".join(f"count({q(c)}), approx_count_distinct({q(c)})" for c in src.names)
        r = I.query(f"SELECT {aggs} FROM {src.rel}", fetch="one", timeout_s=30)
        cnt = {c: (int(r[2 * i]), int(r[2 * i + 1])) for i, c in enumerate(src.names)}
    types = dict(src.columns)
    for d in dates:
        c = column(st, d["column"]) if st else None
        if types.get(d["column"], "").upper() in ("DATE",) or types.get(d["column"], "").upper().startswith("TIMESTAMP"):
            lo, hi, nd = I.query(f"SELECT CAST(min({q(d['column'])}) AS VARCHAR), CAST(max({q(d['column'])}) AS VARCHAR),"
                                 f" approx_count_distinct({q(d['column'])}) FROM {src.rel}", fetch="one", timeout_s=30) if not c else \
                (c.get("min"), c.get("max"), c.get("distinct"))
            d.update({"from": lo, "to": hi, "distinct": int(nd or 0)})
        else:
            d["sampled"] = True
    date_cols = {d["column"] for d in dates}
    groups = []
    n = src.n_rows
    for name, t in src.columns:
        if name == target or name in date_cols:
            continue
        nn, k = cnt.get(name, (0, 0))
        if not nn:
            continue
        id_name = bool(re.search(_ID_NAME, name, re.I))
        text = kind(t) == "text"
        if k < 15 or nn / max(k, 1) < 1.0 or not (id_name or (text and k >= 0.9 * max(50, 0.05 * n))):
            continue
        kx, nnx, mx = I.query(f"SELECT count(*), sum(n), max(n) FROM (SELECT count(*) AS n FROM {src.rel} WHERE {q(name)} IS NOT NULL"
                              f" GROUP BY {q(name)})", fetch="one", timeout_s=30)
        kx, nnx = int(kx or 0), int(nnx or 0)
        per = nnx / max(kx, 1)
        if per > 1.05 and kx >= 20 and (id_name or (text and kx >= max(50, 0.05 * n))):
            groups.append({"column": name, "groups": kx, "rows_per_group": round(per, 2), "max_rows": int(mx or 0),
                           "id_like_name": id_name})
    groups.sort(key=lambda g: (not g["id_like_name"], -g["rows_per_group"]))
    return dates, groups, info


__all__ = ["compute", "ensure", "get", "run", "schedule", "after_commit", "column", "public", "ROW_ID"]
