"""P22: the job queue, read-only. `GET /api/jobs` (what is queued, who holds what), `GET /api/jobs/{id}`,
`GET /api/workers`. Cancelling goes through the owning route (a run, a learning curve, a labelling queue, an agent
session), which also closes the record the job drives."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool

from ..authz import path, public, require  # F-X4a
from ..authz.deps import can, mode

from . import queue as Q

router = APIRouter(prefix="/api")


@router.get("/jobs", dependencies=[public("list: filtered to the caller's visible teams in enforce mode")])
async def list_jobs(request: Request, status: str | None = None, kind: str | None = None, limit: int = 100) -> dict[str, Any]:
    db = request.app.state.db

    def read():
        jobs = Q.list_jobs(db, status=status, kind=kind, limit=max(1, min(limit, 1000)))
        if mode() == "enforce":  # F-X4a: only jobs of objects the caller may read
            jobs = [j for j in jobs if can(request, "data.read_meta", "job", j["id"])]
        return {"jobs": jobs, "counts": Q.counts(db),
                "workers": Q.list_workers(db), "lease_s": Q.LEASE_S, "heartbeat_s": Q.HEARTBEAT_S}

    return await run_in_threadpool(read)


@router.get("/jobs/{job_id}", dependencies=[require("data.read_meta", path("job_id", "job"))])
async def get_job(job_id: str, request: Request) -> dict[str, Any]:
    j = await run_in_threadpool(Q.get, request.app.state.db, job_id)
    if not j:
        raise HTTPException(404, f"No job {job_id}.")
    return j


@router.get("/workers", dependencies=[require("admin.config")])
async def list_workers(request: Request) -> dict[str, Any]:
    return {"workers": await run_in_threadpool(Q.list_workers, request.app.state.db)}
