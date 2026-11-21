"""P22: the job queue, read-only. `GET /api/jobs` (what is queued, who holds what), `GET /api/jobs/{id}`,
`GET /api/workers`. Cancelling goes through the owning route (a run, a learning curve, a labelling queue, an agent
session), which also closes the record the job drives."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool

from . import queue as Q

router = APIRouter(prefix="/api")


@router.get("/jobs")
async def list_jobs(request: Request, status: str | None = None, kind: str | None = None, limit: int = 100) -> dict[str, Any]:
    db = request.app.state.db

    def read():
        return {"jobs": Q.list_jobs(db, status=status, kind=kind, limit=max(1, min(limit, 1000))), "counts": Q.counts(db),
                "workers": Q.list_workers(db), "lease_s": Q.LEASE_S, "heartbeat_s": Q.HEARTBEAT_S}

    return await run_in_threadpool(read)


@router.get("/jobs/{job_id}")
async def get_job(job_id: str, request: Request) -> dict[str, Any]:
    j = await run_in_threadpool(Q.get, request.app.state.db, job_id)
    if not j:
        raise HTTPException(404, f"No job {job_id}.")
    return j


@router.get("/workers")
async def list_workers(request: Request) -> dict[str, Any]:
    return {"workers": await run_in_threadpool(Q.list_workers, request.app.state.db)}
