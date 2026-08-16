"""Ops — the background job worker (pulls the `jobs` table).

Drives the real claim→run→complete/fail path synchronously via
``run_pending_jobs_once`` (the app lifespan loop is dormant under the ASGI test
client). Uses the ``transform`` job_type since it's in the table's CHECK.
"""

from __future__ import annotations

import pytest

from app.shared import jobs, worker
from app.shared.repo import DEFAULT_TEAM_ID


async def test_worker_runs_and_completes_a_job(client):
    seen = {}

    async def handler(job):
        seen["job_id"] = str(job["id"])
        seen["params"] = job["parameters"]
        return {"ok": True, "n": job["parameters"]["n"] * 2}

    job = await jobs.create_job("transform", team_id=DEFAULT_TEAM_ID,
                                parameters={"n": 21})
    assert job["status"] == "pending"

    processed = await worker.run_pending_jobs_once(handlers={"transform": handler})
    assert processed == 1
    assert seen["job_id"] == str(job["id"])

    done = await jobs.get_job(str(job["id"]))
    assert done["status"] == "completed"
    assert done["progress"] == 100
    assert done["result"] == {"ok": True, "n": 42}
    assert done["started_at"] is not None and done["completed_at"] is not None


async def test_worker_records_handler_failure(client):
    async def boom(job):
        raise ValueError("nope")

    job = await jobs.create_job("transform", team_id=DEFAULT_TEAM_ID)
    processed = await worker.run_pending_jobs_once(handlers={"transform": boom})
    assert processed == 1

    done = await jobs.get_job(str(job["id"]))
    assert done["status"] == "failed"
    assert "nope" in (done["error"] or "")


async def test_claim_is_exactly_once(client):
    """A claimed (running) job is not re-claimed; a second drain is a no-op."""
    calls = []

    async def handler(job):
        calls.append(str(job["id"]))
        return None

    await jobs.create_job("transform", team_id=DEFAULT_TEAM_ID)
    assert await worker.run_pending_jobs_once(handlers={"transform": handler}) == 1
    # Nothing left pending -> second drain does no work.
    assert await worker.run_pending_jobs_once(handlers={"transform": handler}) == 0
    assert len(calls) == 1


async def test_dispatch_inline_runs_now_and_returns_result(client):
    """dispatch(inline=True) reproduces the synchronous contract: handler runs
    in-request, job completes, result is returned; a failure is recorded AND
    re-raised (preserving the current inline error semantics)."""
    async def handler(job):
        return {"echo": job["parameters"]["x"]}

    worker.register_handler("export", handler)
    try:
        result = await worker.dispatch("export", params={"x": 7}, inline=True)
        assert result == {"echo": 7}

        async def boom(job):
            raise ValueError("kaboom")

        worker.register_handler("export", boom)
        with pytest.raises(ValueError, match="kaboom"):
            await worker.dispatch("export", params={}, inline=True)
    finally:
        worker._HANDLERS.pop("export", None)


async def test_dispatch_async_enqueues_pending(client):
    """dispatch(inline=False) returns immediately with the handle for the row
    it enqueued; the row is pending until a worker tick drains it."""
    async def handler(job):
        return {"done": True}

    result = await worker.dispatch("export", params={}, inline=False)
    assert (await jobs.get_job(result["job_id"]))["status"] == "pending"
    n = await worker.run_pending_jobs_once(handlers={"export": handler})
    assert n == 1
    assert (await jobs.get_job(result["job_id"]))["status"] == "completed"


async def test_unregistered_types_are_left_untouched(client):
    """The loop must never claim job_types it has no handler for — this is why
    the existing inline validation/profiling/sampling jobs are safe."""
    job = await jobs.create_job("sampling", team_id=DEFAULT_TEAM_ID)
    # Only a transform handler is registered here.
    processed = await worker.run_pending_jobs_once(
        handlers={"transform": lambda j: None})
    assert processed == 0
    assert (await jobs.get_job(str(job["id"])))["status"] == "pending"
