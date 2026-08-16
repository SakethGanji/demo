"""``worker.dispatch(inline=False)`` must hand back the job it just enqueued.

The async branch is the one branch whose caller cannot see the outcome in the
response: the handler has not run yet. Returning ``None`` therefore discarded
the only reference to the queued work — an API answering "accepted" had nothing
to put in the response for the client to poll ``GET /jobs/{id}`` with, and the
row could only be found again by guessing at the jobs list. Callers that needed
the id (relationship discovery) had to bypass ``dispatch`` and re-implement the
enqueue to get it.
"""

from __future__ import annotations

from app.shared import jobs, worker
from app.shared.repo import DEFAULT_TEAM_ID, is_uuid


async def test_enqueuing_work_returns_the_handle_of_the_row_it_created(client):
    """The returned job_id must name the pending row, not just be non-empty."""
    handle = await worker.dispatch(
        "transform", params={"x": 1}, team_id=DEFAULT_TEAM_ID, inline=False)

    assert is_uuid(handle["job_id"])
    job = await jobs.get_job(handle["job_id"])
    assert job["job_type"] == "transform"
    assert job["status"] == "pending"
    assert job["parameters"] == {"x": 1}


async def test_the_handle_tracks_the_same_job_the_worker_later_runs(client):
    """A handle that named a different row than the worker drains would be
    worse than none: the caller would poll a job that never finishes."""
    ran: list[str] = []

    async def handler(job):
        ran.append(str(job["id"]))
        return {"ok": True}

    handle = await worker.dispatch(
        "transform", params={}, team_id=DEFAULT_TEAM_ID, inline=False)
    assert await worker.run_pending_jobs_once(handlers={"transform": handler}) == 1

    assert ran == [handle["job_id"]]
    assert (await jobs.get_job(handle["job_id"]))["status"] == "completed"
