"""Jobs API — read-only observability over the operational job records.

Every import, validation run, and saved-analytics run leaves a jobs row; this
is where a UI polls progress and admins debug stuck work. Team-scoped:
non-superusers only see their teams' jobs, and cross-team job ids 404.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, field_validator

from app.api.pagination import Page, PageParams, pagination
from app.features.auth.deps import Principal, get_principal
from app.shared import jobs

router = APIRouter(prefix="/jobs", tags=["jobs"])


def _iso(value: Any) -> Any:
    """Normalise a job timestamp to ISO-8601 UTC.

    The two read paths hand us the same instant in two shapes: the listing
    query casts in SQL (``created_at::text`` -> ``2026-08-07 10:00:00+00``)
    while the detail query selects the column raw and gets a ``datetime``.
    Left alone they serialise as ``+00`` and ``+00:00`` respectively, both
    space-separated, so the same job reads differently depending on which
    endpoint you asked and neither value survives a strict ISO-8601 parser.
    Everything lands here instead.

    Unparseable input is passed through untouched rather than raising: this
    is a read-only observability endpoint, and a surprising string is far
    less use to an operator than a 500.
    """
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return value
    if not isinstance(value, datetime):
        return value
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


class JobOut(BaseModel):
    id: str
    team_id: str | None = None
    job_type: str
    status: str
    dataset_id: str | None = None
    dataset_version_id: str | None = None
    progress: int | None = None
    error: str | None = None
    result: dict[str, Any] | None = None
    created_at: str
    started_at: str | None = None
    completed_at: str | None = None

    @field_validator("created_at", "started_at", "completed_at", mode="before")
    @classmethod
    def _normalise_timestamps(cls, value: Any) -> Any:
        return _iso(value)


@router.get("", response_model=Page[JobOut])
async def list_jobs(
    status: str | None = Query(None, description="Filter by status"),
    job_type: str | None = Query(None, description="Filter by job type"),
    page: PageParams = Depends(pagination),
    principal: Principal = Depends(get_principal),
) -> Page[JobOut]:
    """List jobs you can see, newest first."""
    team_ids = None if principal.is_superuser else principal.team_ids
    rows, total = await jobs.list_jobs(team_ids, status=status, job_type=job_type,
                                       limit=page.limit, offset=page.offset)
    return Page.of([JobOut(**r) for r in rows], total, page)


@router.get("/{job_id}", response_model=JobOut)
async def get_job(job_id: str, principal: Principal = Depends(get_principal)) -> JobOut:
    """Fetch one job (cross-team existence hidden)."""
    from app.shared.repo import is_uuid

    row = await jobs.get_job(job_id) if is_uuid(job_id) else None
    if not row or (not principal.is_superuser
                   and str(row.get("team_id")) not in principal.team_ids):
        raise HTTPException(404, f"Job not found: {job_id}")

    def _s(key: str) -> str | None:
        return str(row[key]) if row.get(key) is not None else None

    return JobOut(
        id=str(row["id"]), team_id=_s("team_id"),
        job_type=row["job_type"], status=row["status"],
        dataset_id=_s("dataset_id"), dataset_version_id=_s("dataset_version_id"),
        progress=row.get("progress"), error=row.get("error"), result=row.get("result"),
        # Raw values on purpose: JobOut's validator owns the wire format, so
        # the detail path cannot drift from the listing path again.
        created_at=row["created_at"],
        started_at=row.get("started_at"), completed_at=row.get("completed_at"),
    )