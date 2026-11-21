"""P22 (B §5.5): the durable job queue. No Redis: one `jobs` table, leases, a janitor, fair share.

Every piece of background work (a fit, a learning curve, a labelling queue, a Feature Lab search, a review, a registry
build or batch score, an agent turn) is a row in `jobs`. A worker (`python -m app.engine.workerd`, or the worker
embedded in the API process) claims rows of the resource classes it has free slots for:

- **claim** is one statement: `UPDATE jobs … WHERE id = (SELECT … LIMIT 1 [FOR UPDATE SKIP LOCKED]) RETURNING *`.
  On Postgres the inner select takes `FOR UPDATE OF j SKIP LOCKED` (rows another writer holds are skipped, never waited
  on), after a transaction advisory lock per resource class, so concurrent claims see each other's leases and fair share
  is exact (each claim is ~1 ms); on SQLite the same statement runs inside `BEGIN IMMEDIATE` (one writer anyway).
- **lease**: a claim sets `lease_until = now + LEASE_S` (60 s); the holder's heartbeat extends it every HEARTBEAT_S
  (20 s). A heartbeat that finds the row no longer leased to it (cancelled, or re-queued by the janitor) tells the
  holder to stop.
- **janitor** (every worker, every JANITOR_S): a `leased` row whose lease ran out is `queued` again while
  `attempts < max_attempts`, else `failed`; a lease held by a worker on this host whose process is gone (`kill -9`)
  is released at once, without waiting for the lease to run out. Worker rows silent for 3 leases are removed.
- **fair share**: the claim orders candidates by how many jobs of the same team hold that resource now (fewest
  first), then priority, then age; and a team that already holds `team_cap` slots of the resource is skipped while
  another team has queued work there. `team_cap` = half the live slots of the resource (at least 1),
  `ENSEMBLE_TEAM_MAX_SLOTS` overrides it.
- **cancel** sets `status = 'cancelled'`; a queued job never starts, a leased one is stopped by its holder on the next
  heartbeat check (every 2 s).

Times are ISO-8601 UTC text written by the app (portable; no `now()` in SQL), compared as strings: one fixed format.
"""

from __future__ import annotations

import json
import os
import secrets
import socket
from datetime import datetime, timedelta, timezone
from typing import Any

LEASE_S = int(os.environ.get("ENSEMBLE_JOB_LEASE_S", "60"))
HEARTBEAT_S = int(os.environ.get("ENSEMBLE_JOB_HEARTBEAT_S", "20"))
JANITOR_S = int(os.environ.get("ENSEMBLE_JOB_JANITOR_S", "10"))
RESOURCES = ("train", "duck", "light", "api")
TERMINAL = ("succeeded", "failed", "cancelled")
ACTIVE = ("queued", "leased")
HOST = socket.gethostname()


def ts(dt: datetime | None = None, plus_s: float = 0) -> str:
    dt = (dt or datetime.now(timezone.utc)) + timedelta(seconds=plus_s)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def dialect(db) -> str:
    return getattr(db, "dialect", "sqlite")


def _sql(db, q: str) -> str:
    """Named parameters are written `:name`. P23: the Postgres dialect layer (db_pg.translate) rewrites them, with the
    rest of the statement, on every Database connection; nothing to do here."""
    return q


def _row(r) -> dict[str, Any] | None:
    if r is None:
        return None
    d = dict(r)
    for k in ("payload", "result"):
        if d.get(k):
            try:
                d[k] = json.loads(d[k])
            except ValueError:
                pass
    return d


def _exec(db, q: str, params: dict[str, Any] | None = None, *, immediate: bool = False,
          serialize: str | None = None) -> list[dict[str, Any]]:
    """`immediate`: SQLite takes the write lock first. `serialize`: Postgres takes a transaction advisory lock on that
    key first, so claims of one resource class see each other's leases (exact fair share); SKIP LOCKED still skips rows
    held by other writers (cancel, janitor)."""
    with db.connect() as con:
        if immediate and dialect(db) == "sqlite":
            con.execute("BEGIN IMMEDIATE")
        if serialize and dialect(db) == "postgres":
            con.execute("SELECT pg_advisory_xact_lock(hashtext(:k))", {"k": serialize})
        cur = con.execute(_sql(db, q), params or {})
        rows = cur.fetchall() if cur.description else []
        out = [_row(r) for r in rows]
        if not cur.description:
            out = [{"rowcount": cur.rowcount}]
    return out


# ------------------------------------------------------------------------------------------------------------ write


def enqueue(db, kind: str, payload: dict[str, Any], *, resource: str, by: str, team_id: str | None = None,
            mem_mb: int = 0, priority: int = 0, max_attempts: int = 2, ref_kind: str | None = None,
            ref_id: str | None = None, job_id: str | None = None, not_before: str | None = None) -> dict[str, Any]:
    if resource not in RESOURCES:
        raise ValueError(f"resource is one of {RESOURCES}")
    jid = job_id or "jb_" + secrets.token_hex(6)
    rows = _exec(db, "INSERT INTO jobs (id, kind, team_id, resource, mem_mb, priority, payload, status, attempts, max_attempts,"
                     " created_at, not_before, by_principal, ref_kind, ref_id)"
                     " VALUES (:id, :kind, :team, :res, :mem, :prio, :payload, 'queued', 0, :max, :now, :nb, :by, :rk, :ri)"
                     " RETURNING *",
                 {"id": jid, "kind": kind, "team": team_id, "res": resource, "mem": int(mem_mb or 0), "prio": int(priority),
                  "payload": json.dumps(payload, default=str), "max": int(max_attempts), "now": ts(), "nb": not_before,
                  "by": (by or "system")[:120], "rk": ref_kind, "ri": ref_id})
    return rows[0]


def team_cap(db, resource: str) -> int:
    env = os.environ.get("ENSEMBLE_TEAM_MAX_SLOTS")
    if env:
        return max(1, int(env))
    total = 0
    for w in list_workers(db, live_only=True):
        total += int((w.get("slots") or {}).get(resource) or 0)
    return max(1, total // 2)


_CLAIM = """
UPDATE jobs SET status = 'leased', leased_by = :w, lease_until = :until, attempts = attempts + 1,
       started_at = COALESCE(started_at, :now)
WHERE id = (
  SELECT j.id FROM jobs j
  LEFT JOIN (SELECT COALESCE(team_id, '') AS t, COUNT(*) AS n FROM jobs
             WHERE status = 'leased' AND resource = :r GROUP BY COALESCE(team_id, '')) running
         ON running.t = COALESCE(j.team_id, '')
  WHERE j.status = 'queued' AND j.resource = :r AND j.mem_mb <= :free
    AND (j.not_before IS NULL OR j.not_before <= :now)
    AND (COALESCE(running.n, 0) < :cap OR NOT EXISTS (
         SELECT 1 FROM jobs o WHERE o.status = 'queued' AND o.resource = :r
            AND COALESCE(o.team_id, '') <> COALESCE(j.team_id, '')))
  ORDER BY COALESCE(running.n, 0) ASC, j.priority DESC, j.created_at ASC, j.id ASC
  LIMIT 1 {lock})
  AND status = 'queued'
RETURNING *
"""


def claim(db, worker_id: str, resource: str, free_mem_mb: int = 1 << 30, *, cap: int | None = None,
          kinds: tuple[str, ...] | None = None) -> dict[str, Any] | None:
    """Lease the next job of `resource` for `worker_id`, or None."""
    lock = "FOR UPDATE OF j SKIP LOCKED" if dialect(db) == "postgres" else ""
    q = _CLAIM.format(lock=lock)
    if kinds:
        q = q.replace("AND j.mem_mb <= :free", "AND j.mem_mb <= :free AND j.kind IN (" +
                      ", ".join(f"'{k}'" for k in kinds if k.isidentifier()) + ")")
    now = ts()
    rows = _exec(db, q, {"w": worker_id, "until": ts(plus_s=LEASE_S), "now": now, "r": resource, "free": int(free_mem_mb),
                         "cap": int(cap if cap is not None else team_cap(db, resource))}, immediate=True,
                 serialize=f"ensemble.jobs.claim.{resource}")
    return rows[0] if rows and "id" in rows[0] else None


def claim_id(db, job_id: str, worker_id: str) -> dict[str, Any] | None:
    """Lease one named queued job (the API starting an agent turn it just queued)."""
    rows = _exec(db, "UPDATE jobs SET status = 'leased', leased_by = :w, lease_until = :until, attempts = attempts + 1,"
                     " started_at = COALESCE(started_at, :now) WHERE id = :id AND status = 'queued' RETURNING *",
                 {"w": worker_id, "until": ts(plus_s=LEASE_S), "now": ts(), "id": job_id}, immediate=True)
    return rows[0] if rows and "id" in rows[0] else None


def heartbeat(db, job_id: str, worker_id: str, extend: bool = True) -> str:
    """'ok' while we hold the lease (extended by LEASE_S), else the row's status ('cancelled', or 'lost')."""
    if extend:
        n = _exec(db, "UPDATE jobs SET lease_until = :until WHERE id = :id AND leased_by = :w AND status = 'leased'",
                  {"until": ts(plus_s=LEASE_S), "id": job_id, "w": worker_id})[0]["rowcount"]
        if n:
            return "ok"
    j = get(db, job_id)
    if j and j["status"] == "leased" and j["leased_by"] == worker_id:
        return "ok"
    return "cancelled" if j and j["status"] == "cancelled" else "lost"


def finish(db, job_id: str, worker_id: str | None, status: str, *, result: Any = None, error: str | None = None) -> bool:
    """Close a job we hold. False if it was not ours any more (cancelled or re-queued meanwhile)."""
    if status not in TERMINAL:
        raise ValueError(status)
    q = ("UPDATE jobs SET status = :s, finished_at = :now, result = :res, error = :err, lease_until = NULL"
         " WHERE id = :id AND status = 'leased'" + (" AND leased_by = :w" if worker_id else ""))
    n = _exec(db, q, {"s": status, "now": ts(), "res": None if result is None else json.dumps(result, default=str),
                      "err": (error or None) and str(error)[:2000], "id": job_id, "w": worker_id})[0]["rowcount"]
    return bool(n)


def cancel(db, job_id: str, reason: str = "Cancelled.") -> dict[str, Any] | None:
    """Cancel a queued or leased job; returns the row as it was (status before), or None if it was not active."""
    before = get(db, job_id)
    if not before or before["status"] not in ACTIVE:
        return None
    n = _exec(db, "UPDATE jobs SET status = 'cancelled', finished_at = :now, error = :err WHERE id = :id"
                  " AND status IN ('queued', 'leased')", {"now": ts(), "err": reason, "id": job_id})[0]["rowcount"]
    return before if n else None


def requeue(db, job_id: str, reason: str) -> bool:
    """Put a leased job back (its holder is stopping without finishing it: a worker shutting down). A give-back does
    not use up an attempt (attempts count holders that died with the job)."""
    n = _exec(db, "UPDATE jobs SET status = 'queued', leased_by = NULL, lease_until = NULL, error = :err,"
                  " attempts = CASE WHEN attempts > 0 THEN attempts - 1 ELSE 0 END"
                  " WHERE id = :id AND status = 'leased'", {"err": reason, "id": job_id})[0]["rowcount"]
    return bool(n)


PID_CHECK = os.environ.get("ENSEMBLE_JOB_PID_CHECK", "1") not in ("0", "false", "")


def _pid_gone(worker_id: str | None) -> bool:
    """A worker id is `<role>@<host>:<pid>`; on this host a missing pid means the worker died (kill -9).
    ENSEMBLE_JOB_PID_CHECK=0 turns this off (only the lease decides, as across hosts)."""
    if not PID_CHECK or not worker_id or "@" not in worker_id:
        return False
    host_pid = worker_id.split("@", 1)[1]
    host, _, pid = host_pid.rpartition(":")
    if host != HOST or not pid.isdigit():
        return False
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    return False


def janitor(db) -> dict[str, list[dict[str, Any]]]:
    """Re-queue expired (or dead-local) leases while attempts remain, else fail them. Returns what it changed."""
    now = ts()
    leased = _exec(db, "SELECT id, leased_by, lease_until, attempts, max_attempts FROM jobs WHERE status = 'leased'")
    dead = {j["id"]: ("expired" if (j["lease_until"] or "") < now else "gone") for j in leased
            if (j["lease_until"] or "") < now or _pid_gone(j["leased_by"])}
    out: dict[str, list[dict[str, Any]]] = {"requeued": [], "failed": []}
    for jid, how in dead.items():
        j = get(db, jid)
        if not j or j["status"] != "leased":
            continue
        why = (f"lease of {j['leased_by']} expired at {j['lease_until']}" if how == "expired" else
               f"worker {j['leased_by']} is gone (its process is not running on {HOST})") + \
            f" (attempt {j['attempts']} of {j['max_attempts']}, noticed {now})"
        if j["attempts"] < j["max_attempts"]:
            n = _exec(db, "UPDATE jobs SET status = 'queued', leased_by = NULL, lease_until = NULL, error = :err"
                          " WHERE id = :id AND status = 'leased' AND leased_by = :w",
                      {"err": why, "id": jid, "w": j["leased_by"]})[0]["rowcount"]
            if n:
                out["requeued"].append({**j, "error": why})
        else:
            n = _exec(db, "UPDATE jobs SET status = 'failed', finished_at = :now, lease_until = NULL, error = :err"
                          " WHERE id = :id AND status = 'leased' AND leased_by = :w",
                      {"now": now, "err": why + "; no attempts left", "id": jid, "w": j["leased_by"]})[0]["rowcount"]
            if n:
                out["failed"].append({**j, "error": why + "; no attempts left"})
    _exec(db, "DELETE FROM workers WHERE heartbeat_at < :t", {"t": ts(plus_s=-3 * LEASE_S)})
    return out


# ------------------------------------------------------------------------------------------------------------ read


def get(db, job_id: str) -> dict[str, Any] | None:
    rows = _exec(db, "SELECT * FROM jobs WHERE id = :id", {"id": job_id})
    return rows[0] if rows else None


def active_for(db, ref_kind: str, ref_id: str, kind: str | None = None) -> dict[str, Any] | None:
    q = ("SELECT * FROM jobs WHERE ref_kind = :rk AND ref_id = :ri AND status IN ('queued', 'leased')"
         + (" AND kind = :k" if kind else "") + " ORDER BY created_at DESC LIMIT 1")
    rows = _exec(db, q, {"rk": ref_kind, "ri": ref_id, "k": kind})
    return rows[0] if rows else None


def latest_for(db, ref_kind: str, ref_id: str, kind: str | None = None) -> dict[str, Any] | None:
    q = ("SELECT * FROM jobs WHERE ref_kind = :rk AND ref_id = :ri" + (" AND kind = :k" if kind else "")
         + " ORDER BY created_at DESC LIMIT 1")
    rows = _exec(db, q, {"rk": ref_kind, "ri": ref_id, "k": kind})
    return rows[0] if rows else None


def active_of_kind(db, kind: str) -> list[dict[str, Any]]:
    return _exec(db, "SELECT * FROM jobs WHERE kind = :k AND status IN ('queued', 'leased') ORDER BY created_at",
                 {"k": kind})


def list_jobs(db, status: str | None = None, kind: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    where, p = [], {"lim": int(limit)}
    if status:
        where.append("status IN (" + ", ".join(f":s{i}" for i, _ in enumerate(status.split(","))) + ")")
        p.update({f"s{i}": s.strip() for i, s in enumerate(status.split(","))})
    if kind:
        where.append("kind = :k")
        p["k"] = kind
    q = ("SELECT id, kind, team_id, resource, mem_mb, priority, status, attempts, max_attempts, created_at, leased_by,"
         " lease_until, started_at, finished_at, error, by_principal, ref_kind, ref_id FROM jobs"
         + (" WHERE " + " AND ".join(where) if where else "")
         + " ORDER BY CASE status WHEN 'leased' THEN 0 WHEN 'queued' THEN 1 ELSE 2 END, created_at DESC LIMIT :lim")
    return _exec(db, q, p)


def counts(db) -> dict[str, int]:
    return {r["status"]: r["n"] for r in _exec(db, "SELECT status, COUNT(*) AS n FROM jobs GROUP BY status")}


def finished_since(db, kind: str, after: str) -> list[dict[str, Any]]:
    return _exec(db, "SELECT id, ref_kind, ref_id, status, finished_at FROM jobs WHERE kind = :k AND finished_at > :a"
                     " AND status IN ('succeeded', 'failed', 'cancelled') ORDER BY finished_at", {"k": kind, "a": after})


# ------------------------------------------------------------------------------------------------------------ workers


def worker_id(role: str) -> str:
    return f"{role}-{secrets.token_hex(3)}@{HOST}:{os.getpid()}"


def register_worker(db, wid: str, slots: dict[str, Any], version: str) -> None:
    now = ts()
    _exec(db, "INSERT INTO workers (id, host, started_at, heartbeat_at, slots, version) VALUES (:id, :h, :now, :now, :s, :v)"
              " ON CONFLICT (id) DO UPDATE SET heartbeat_at = excluded.heartbeat_at, slots = excluded.slots",
          {"id": wid, "h": HOST, "now": now, "s": json.dumps(slots), "v": version})


def worker_heartbeat(db, wid: str, slots: dict[str, Any], version: str) -> None:
    n = _exec(db, "UPDATE workers SET heartbeat_at = :now, slots = :s WHERE id = :id",
              {"now": ts(), "s": json.dumps(slots), "id": wid})[0]["rowcount"]
    if not n:
        register_worker(db, wid, slots, version)


def unregister_worker(db, wid: str) -> None:
    _exec(db, "DELETE FROM workers WHERE id = :id", {"id": wid})


def list_workers(db, live_only: bool = False) -> list[dict[str, Any]]:
    rows = _exec(db, "SELECT * FROM workers ORDER BY started_at")
    cutoff = ts(plus_s=-LEASE_S)
    out = []
    for r in rows:
        r["slots"] = json.loads(r["slots"]) if isinstance(r["slots"], str) else r["slots"]
        r["live"] = r["heartbeat_at"] >= cutoff and not _pid_gone(r["id"])
        if r["live"] or not live_only:
            out.append(r)
    return out
