"""Report — and, only when asked, close — ``analytics_runs`` rows stuck in ``running``.

REPORTS BY DEFAULT. It writes nothing unless you pass ``--close``.

Background
----------
``app.features.library.service.execute_definition`` opens a ``jobs`` row and an
``analytics_runs`` row, then runs the definition inline. It used to re-raise a
``fastapi.HTTPException`` from inside that block *without* bookkeeping, so any
4xx raised mid-run left both rows in ``running`` forever. That path now closes
the run and the job on every exception, so no new row gets stuck there — but
the fix is not retroactive. Rows stranded by the old code are still ``running``,
and nothing in the service ever looks at them again:

* nothing retries them — an analytics run is executed inline in the request that
  asked for it; there is no queue entry, no poller, no resume,
* nothing reaps them — the job worker only claims ``pending`` jobs,
* they are not ignorable — ``GET /runs`` lists them, they never reach a terminal
  status, and ``resolve_publishable_artifact`` rejects them with a 409 that
  says "not completed", which reads as "still working" rather than "abandoned".

"Run a one-off UPDATE" is not a remedy: it needs a human to hand-write a
predicate over a production table with no way to preview what it will hit.

Age is the discriminator
------------------------
A long-``running`` row is NOT automatically orphaned. A run that started two
seconds ago is healthy and mid-flight, and its row is indistinguishable in
*shape* from one abandoned six weeks ago. So the only gate is age, measured by
the **database** clock (``now() - started_at``, computed server-side) so a
skewed client cannot widen it.

The default of 60 minutes is deliberately far past anything real. Unlike a
transformation, an analytics run never leaves the request that started it —
``execute_definition`` has no ``worker.dispatch``, so a run's whole lifetime is
one HTTP request. Anything still open an hour later has outlived the connection
that owned it. Lower it with ``--older-than-minutes`` if you know your fleet is
idle; nothing younger than the threshold is ever reported as actionable or
closed, whatever its verdict would have been.

Verdicts
--------
``STRANDED``
    The run is ``running`` but its ``jobs`` row already reached ``completed``,
    ``failed`` or ``cancelled``. This is proof, not a heuristic: every close
    path in the service writes the run *before* the job (``fail_run`` then
    ``fail_job``; ``complete_run`` then ``complete_job``), so a terminal job
    beside an open run is a state no live code produces. Something died between
    the two writes, or the run write failed.

``LIKELY-STRANDED``
    Past the threshold, and its job is open too (or its job row is gone —
    ``analytics_runs.job_id`` is ``ON DELETE SET NULL``). Consistent, and
    consistently abandoned. A heuristic, because "old and open" is what a
    genuinely long-lived run would also look like; the argument that none can
    exist is the inline-execution one above, not something the row itself
    proves.

``IN-FLIGHT``
    Younger than the threshold. Counted in the summary so you know it is there,
    never listed as actionable, never closed.

What ``--close`` does
---------------------
Per row, in ONE transaction, so a crash can never leave the pair disagreeing:

* the run becomes ``failed`` with :data:`STRANDED_MARKER` in ``error`` and
  ``completed_at = now()``, guarded by ``WHERE status = 'running'``;
* its job becomes ``failed`` too, guarded by ``WHERE status IN ('pending',
  'running')``.

Both must move, because a run and its job are two records of ONE attempt —
leaving the job open after closing the run just recreates the inconsistency in
mirror image, which is the exact defect the ``execute_definition`` fix was
about. But a job that *already* reached a terminal status is left untouched: its
record of what happened is truthful and older than anything this script knows.

The guards make it idempotent and safe to re-run: a closed row no longer
matches the scan, and a row that legitimately completed between the report and
the close is not clobbered. It is also resumable — one commit per row, so an
interrupted pass leaves every row it did reach fully closed.

``failed`` is a claim about the *record*, not the work. A stranded run may well
have finished its computation; nothing recorded the outcome, so the only honest
terminal status is the one that means "this attempt produced no result". The
error text says so. Any artifact the run registered before dying survives —
artifacts are resolved through their own rows and have their own retention
path; closing a run neither deletes nor orphans one.

Why this is a script and not startup self-healing
-------------------------------------------------
A boot-time sweep would have no way to tell a genuinely in-flight run on
*another* instance from an abandoned one. The first instance to restart during
a rolling deploy would fail every run its siblings were still executing, and
the caller would get a completed HTTP 200 for a run its own row says failed.
The age threshold does not save it either — the safe threshold is a property of
the deployment, not of the code. This stays operator-invoked and opt-in.

    venv/bin/python -m scripts.audit_stranded_analytics_runs
    venv/bin/python -m scripts.audit_stranded_analytics_runs --older-than-minutes 15
    venv/bin/python -m scripts.audit_stranded_analytics_runs --json
    venv/bin/python -m scripts.audit_stranded_analytics_runs --close
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text

from app.infra.db.postgres import async_session_factory, dispose_engine

# Verdicts, most-actionable first.
STRANDED = "STRANDED"
LIKELY_STRANDED = "LIKELY-STRANDED"
IN_FLIGHT = "IN-FLIGHT"

_VERDICT_ORDER = {STRANDED: 0, LIKELY_STRANDED: 1, IN_FLIGHT: 2}

#: Verdicts ``--close`` will act on. ``IN-FLIGHT`` is deliberately absent.
CLOSEABLE = (STRANDED, LIKELY_STRANDED)

#: Statuses a ``jobs`` row can hold that mean the attempt is already recorded.
#: Matches the CHECK on ``jobs.status`` minus the two open states.
TERMINAL_JOB_STATUSES = frozenset({"completed", "failed", "cancelled"})
OPEN_JOB_STATUSES = frozenset({"pending", "running"})

#: Greppable prefix written into ``analytics_runs.error`` (and the job's) so a
#: human can tell an operator-closed row from one the service failed itself.
STRANDED_MARKER = "stranded run closed by scripts/audit_stranded_analytics_runs"

DEFAULT_OLDER_THAN_MINUTES = 60


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

# ``age_seconds`` is computed by Postgres, not by this process: the threshold is
# only as trustworthy as the clock it is measured against, and the DB clock is
# the one ``started_at`` was written from.
_SCAN = """
    SELECT r.id::text                                     AS run_id,
           r.status                                       AS run_status,
           r.definition_id::text                          AS definition_id,
           d.name                                         AS definition_name,
           d.kind                                         AS kind,
           d.dataset_id::text                             AS dataset_id,
           ds.name                                        AS dataset_name,
           ds.team_id::text                               AS team_id,
           r.dataset_version_id::text                     AS dataset_version_id,
           r.job_id::text                                 AS job_id,
           j.status                                       AS job_status,
           r.triggered_by::text                           AS actor_id,
           u.email                                        AS actor_email,
           r.started_at::text                             AS started_at,
           -- ::float so ``--json`` emits a number; EXTRACT returns numeric,
           -- which json.dumps can only render via ``default=str``.
           EXTRACT(EPOCH FROM (now() - r.started_at))::float AS age_seconds
      FROM analytics_runs r
      JOIN analytics_definitions d ON d.id = r.definition_id
      LEFT JOIN datasets ds ON ds.id = d.dataset_id
      LEFT JOIN jobs j     ON j.id  = r.job_id
      LEFT JOIN users u    ON u.id  = r.triggered_by
     WHERE r.status = 'running'
     ORDER BY r.started_at
"""


def humanize_age(seconds: float | None) -> str:
    """A duration a human can sanity-check at a glance."""
    if seconds is None:
        return "unknown"
    seconds = int(max(seconds, 0))
    if seconds < 60:
        return f"{seconds}s"
    minutes, sec = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {sec}s"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h {minutes}m"
    days, hours = divmod(hours, 24)
    return f"{days}d {hours}h"


def classify(row: Mapping[str, Any], *, older_than_seconds: float) -> dict:
    """Verdict + justification for one open run row.

    Pure: no DB, no clock, no I/O. ``row`` needs only ``age_seconds``,
    ``job_id`` and ``job_status`` — everything else in the scan is for the
    human reading the report.
    """
    age = row.get("age_seconds")
    age = float(age) if age is not None else 0.0
    job_status = row.get("job_status")
    pretty = humanize_age(age)

    if age < older_than_seconds:
        return {
            "verdict": IN_FLIGHT,
            "why": f"started {pretty} ago, under the {humanize_age(older_than_seconds)} "
                   f"threshold — assume it is running right now",
        }

    if job_status in TERMINAL_JOB_STATUSES:
        return {
            "verdict": STRANDED,
            "why": f"open for {pretty} while its job row is already {job_status!r}. "
                   f"Every close path writes the run before the job, so no live "
                   f"code path produces this pair",
        }

    if row.get("job_id") is None:
        return {
            "verdict": LIKELY_STRANDED,
            "why": f"open for {pretty} and has no job row (job_id is "
                   f"ON DELETE SET NULL, so its job was deleted) — nothing is "
                   f"tracking this attempt at all",
        }

    return {
        "verdict": LIKELY_STRANDED,
        "why": f"open for {pretty}; its job is still {job_status!r} too. Analytics "
               f"runs execute inline in one HTTP request — nothing polls, retries "
               f"or resumes them — so no process can still be working on it",
    }


async def collect(*, older_than_seconds: float) -> list[dict]:
    """Every ``running`` analytics run, classified. SELECT only."""
    async with async_session_factory() as s:
        rows = (await s.execute(text(_SCAN))).mappings().all()
    out = [{**dict(r), **classify(r, older_than_seconds=older_than_seconds)} for r in rows]
    out.sort(key=lambda r: (_VERDICT_ORDER[r["verdict"]], -(r["age_seconds"] or 0)))
    return out


# ---------------------------------------------------------------------------
# Closing (opt-in)
# ---------------------------------------------------------------------------

def close_reason(row: Mapping[str, Any], *, at: datetime | None = None) -> str:
    """The text written to ``error`` on both rows. Honest about what is unknown."""
    stamp = (at or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    return (
        f"{STRANDED_MARKER} at {stamp}: the run sat in 'running' for "
        f"{humanize_age(row.get('age_seconds'))} and the process that started it "
        f"never recorded an outcome. Whether the underlying work completed is "
        f"unknown; no result was stored against this run."
    )


_CLOSE_RUN = """
    UPDATE analytics_runs
       SET status = 'failed', error = :err, completed_at = now()
     WHERE id = :id AND status = 'running'
    RETURNING id::text
"""

_CLOSE_JOB = """
    UPDATE jobs
       SET status = 'failed', error = :err, completed_at = now()
     WHERE id = :id AND status = ANY(:open)
    RETURNING id::text
"""


async def close_row(row: Mapping[str, Any]) -> dict:
    """Close one run and, if it is still open, its job — in one transaction.

    Both guards are part of the contract, not defensive noise: they make a
    second pass a no-op and stop the script clobbering a row that reached a
    real terminal status between the scan and the write.
    """
    reason = close_reason(row)
    async with async_session_factory() as s:
        run_closed = (await s.execute(
            text(_CLOSE_RUN), {"id": row["run_id"], "err": reason})).scalar()
        job_closed = None
        if run_closed and row.get("job_id"):
            job_closed = (await s.execute(
                text(_CLOSE_JOB),
                {"id": row["job_id"], "err": reason, "open": sorted(OPEN_JOB_STATUSES)},
            )).scalar()
        await s.commit()
    return {"run_id": row["run_id"], "run_closed": bool(run_closed),
            "job_id": row.get("job_id"), "job_closed": bool(job_closed),
            "error": reason}


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _render_row(row: dict) -> None:
    print(f"\n{row['verdict']:<16} run {row['run_id']}")
    print(f"  {'why':<12} {row['why']}")
    print(f"  {'definition':<12} {row['definition_name']!r} "
          f"[{row['kind']}] ({row['definition_id']})")
    print(f"  {'dataset':<12} {row['dataset_name']} ({row['dataset_id']}) "
          f"team {row['team_id']}")
    print(f"  {'version':<12} {row['dataset_version_id'] or '(none)'}")
    print(f"  {'actor':<12} {row['actor_email'] or '(unknown)'} ({row['actor_id']})")
    print(f"  {'started_at':<12} {row['started_at']}  "
          f"({humanize_age(row['age_seconds'])} ago)")
    print(f"  {'job':<12} {row['job_id'] or '(none)'} "
          f"status={row['job_status'] or '(no row)'}")


def render(rows: list[dict], *, older_than_seconds: float, closed: list[dict] | None) -> None:
    actionable = [r for r in rows if r["verdict"] in CLOSEABLE]
    in_flight = [r for r in rows if r["verdict"] == IN_FLIGHT]

    for row in actionable:
        _render_row(row)

    counts = {v: sum(1 for r in rows if r["verdict"] == v) for v in _VERDICT_ORDER}
    print(f"\n{len(rows)} analytics run(s) in 'running'; "
          f"threshold {humanize_age(older_than_seconds)}.")
    for verdict in sorted(counts, key=lambda v: _VERDICT_ORDER[v]):
        print(f"  {verdict:<16} {counts[verdict]}")
    if in_flight:
        print(f"  ({len(in_flight)} younger than the threshold — not listed, "
              f"never closed. Lower --older-than-minutes to include them.)")

    if closed is None:
        if actionable:
            print("\nNothing was changed. Re-run with --close to mark these runs "
                  "'failed' (and their still-open jobs with them); the reason "
                  "recorded says the outcome is unknown, not that the work failed.")
        return

    runs = sum(1 for c in closed if c["run_closed"])
    jobs = sum(1 for c in closed if c["job_closed"])
    skipped = [c for c in closed if not c["run_closed"]]
    print(f"\nClosed {runs} run(s) and {jobs} job(s).")
    if skipped:
        print(f"  {len(skipped)} run(s) were no longer 'running' when the write ran "
              f"and were left alone: {', '.join(c['run_id'] for c in skipped)}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="scripts.audit_stranded_analytics_runs",
        description="Report analytics runs stuck in 'running'. Read-only unless --close.",
    )
    parser.add_argument(
        "--older-than-minutes", type=float, default=DEFAULT_OLDER_THAN_MINUTES,
        help="Only treat a 'running' run as abandoned once it is this old "
             f"(default: {DEFAULT_OLDER_THAN_MINUTES}). Measured by the database clock.")
    parser.add_argument("--json", action="store_true",
                        help="Machine-readable output; still read-only.")
    parser.add_argument(
        "--close", action="store_true",
        help="MUTATES. Mark every reported run 'failed' and close its still-open "
             "job. Idempotent and safe to re-run.")
    return parser.parse_args(argv)


async def _main(args: argparse.Namespace) -> int:
    older_than_seconds = args.older_than_minutes * 60
    try:
        rows = await collect(older_than_seconds=older_than_seconds)
        closed = None
        if args.close:
            closed = [await close_row(r) for r in rows if r["verdict"] in CLOSEABLE]
    finally:
        await dispose_engine()

    if args.json:
        print(json.dumps(
            {"threshold_seconds": older_than_seconds, "runs": rows, "closed": closed},
            indent=2, sort_keys=True, default=str))
        return 0
    if not rows:
        print("No analytics run is in 'running'. Nothing to review.")
        return 0
    render(rows, older_than_seconds=older_than_seconds, closed=closed)
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_main(parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
