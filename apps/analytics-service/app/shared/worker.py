"""Background job worker — pulls the `jobs` table and runs registered handlers.

This is the POC replacement for FastAPI ``BackgroundTasks``: instead of tying
async work to a request's lifetime, features enqueue a row in ``jobs`` (via
``shared.jobs.create_job``) and register a handler for its ``job_type`` here.
The worker loop (started in the app lifespan) atomically claims pending jobs of
the registered types with ``FOR UPDATE SKIP LOCKED`` — so multiple workers, or
a worker racing the request path, can never run the same job twice — runs the
handler, and marks the row completed/failed.

Determinism in tests: the in-process ASGI test client does not run the app
lifespan, so the loop is dormant there. Tests (and any caller wanting
synchronous execution) call :func:`run_pending_jobs_once` directly. The loop
only ever claims job_types that have a registered handler, so the existing
inline paths (validation/profiling/sampling/import) are never touched.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

from sqlalchemy import text

from app.infra.db.postgres import async_session_factory
from app.shared import jobs
from app.shared.repo import DEFAULT_TEAM_ID

logger = logging.getLogger("analytics.worker")

# A handler receives the claimed job row (dict) and returns an optional result
# summary (stored on the job). Raising marks the job failed with the exception
# text — handlers should not swallow errors they want recorded.
JobHandler = Callable[[dict], Awaitable[dict | None]]

_HANDLERS: dict[str, JobHandler] = {}


def register_handler(job_type: str, handler: JobHandler) -> None:
    """Register the coroutine that runs jobs of *job_type* (idempotent)."""
    _HANDLERS[job_type] = handler


def registered_types() -> list[str]:
    return list(_HANDLERS)


async def _claim_next(job_types: list[str]) -> dict | None:
    """Atomically move one pending job of *job_types* to running, or None.

    ``FOR UPDATE SKIP LOCKED`` on the inner select makes concurrent claims
    non-blocking and exactly-once.
    """
    if not job_types:
        return None
    async with async_session_factory() as s:
        row = (await s.execute(
            text("""
                UPDATE jobs SET status = 'running', started_at = now()
                WHERE id = (
                    SELECT id FROM jobs
                    WHERE status = 'pending' AND job_type = ANY(:types)
                    ORDER BY created_at
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                )
                RETURNING *
            """),
            {"types": job_types},
        )).mappings().first()
        await s.commit()
        return dict(row) if row else None


async def _run_one(job: dict, handler: JobHandler) -> None:
    job_id = str(job["id"])
    try:
        result = await handler(job)
        await jobs.complete_job(job_id, result=result)
    except Exception as exc:  # noqa: BLE001 — the failure is recorded on the row
        logger.exception("job %s (%s) failed", job_id, job.get("job_type"))
        await jobs.fail_job(job_id, error=str(exc))


async def run_pending_jobs_once(handlers: dict[str, JobHandler] | None = None) -> int:
    """Drain every currently-claimable job for the (registered) *handlers*.

    Returns the number processed. Safe to call synchronously from a request or
    a test — this is how work completes when the background loop is not running.
    """
    handlers = _HANDLERS if handlers is None else handlers
    if not handlers:
        return 0
    types = list(handlers)
    processed = 0
    while (job := await _claim_next(types)) is not None:
        await _run_one(job, handlers[job["job_type"]])
        processed += 1
    return processed


async def dispatch(
    job_type: str,
    *,
    params: dict | None = None,
    dataset_id: str | None = None,
    dataset_version_id: str | None = None,
    team_id: str = DEFAULT_TEAM_ID,
    inline: bool,
) -> dict | None:
    """Enqueue a job and, when *inline*, run its handler in-request.

    The request path and the background loop invoke the SAME registered
    handler, so behaviour is identical either way. ``inline=True`` reproduces
    the existing synchronous contract (validation/profiling/analytics): the
    handler runs now, the job is marked completed, and the result is returned
    (a handler exception is recorded on the job and re-raised, preserving the
    current error semantics). ``inline=False`` returns immediately with
    ``{"job_id": ...}`` — the worker loop picks the job up.

    That handle is the whole point of the async branch: it is the only mode in
    which the caller cannot see the outcome in the response, so it is the one
    mode that must hand back something to poll ``GET /jobs/{id}`` with.
    Returning ``None`` here threw away the id of the row this function had just
    created, and every caller that wanted it had to re-implement the enqueue
    itself to get it back.
    """
    job = await jobs.create_job(
        job_type, dataset_id=dataset_id, dataset_version_id=dataset_version_id,
        team_id=team_id, parameters=params)
    if not inline:
        return {"job_id": str(job["id"])}
    handler = _HANDLERS.get(job_type)
    if handler is None:
        await jobs.fail_job(str(job["id"]), error=f"no handler for {job_type!r}")
        raise RuntimeError(f"no handler registered for job_type {job_type!r}")
    job_id = str(job["id"])
    await jobs.start_job(job_id)
    try:
        # ``complete_job`` is INSIDE the guard, exactly as in ``_run_one``: it
        # serializes the handler's result to JSON, so a result the handler
        # happily returned but ``json.dumps`` cannot encode raises *here* — with
        # the job already ``running`` and no one left to close it. Both entry
        # points into a handler must leave the job row terminal on every path,
        # or "which one ran it" changes whether a job can strand.
        result = await handler(job)
        await jobs.complete_job(job_id, result=result)
    except Exception as exc:  # noqa: BLE001 — recorded, then surfaced to caller
        await jobs.fail_job(job_id, error=str(exc))
        raise
    return result


async def run_worker_loop(poll_seconds: float, stop: asyncio.Event) -> None:
    """Poll for and run pending jobs until *stop* is set."""
    logger.info("job worker started (types=%s, poll=%ss)",
                registered_types(), poll_seconds)
    try:
        while not stop.is_set():
            try:
                await run_pending_jobs_once()
            except Exception:  # noqa: BLE001 — never let the loop die
                logger.exception("worker tick failed")
            try:
                await asyncio.wait_for(stop.wait(), timeout=poll_seconds)
            except asyncio.TimeoutError:
                pass
    finally:
        logger.info("job worker stopped")
