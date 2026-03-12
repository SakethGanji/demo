"""Cross-feature job operations — tracks background tasks in the DB."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text

from app.infra.db.postgres import async_session_factory
from app.shared.repo import DEFAULT_TEAM_ID


async def create_job(
    job_type: str,
    *,
    dataset_id: str | None = None,
    dataset_version_id: str | None = None,
    team_id: str = DEFAULT_TEAM_ID,
    parameters: dict[str, Any] | None = None,
) -> dict:
    """Create a new job row (status=pending)."""
    async with async_session_factory() as s:
        row = (await s.execute(
            text("""
                INSERT INTO jobs (job_type, team_id, dataset_id, dataset_version_id, parameters)
                VALUES (:jt, :tid, :did, :dvid, CAST(:params AS jsonb))
                RETURNING *
            """),
            {
                "jt": job_type,
                "tid": team_id,
                "did": dataset_id,
                "dvid": dataset_version_id,
                "params": json.dumps(parameters) if parameters else None,
            },
        )).mappings().one()
        await s.commit()
        return dict(row)


async def start_job(job_id: str) -> None:
    """Mark a job as running."""
    async with async_session_factory() as s:
        await s.execute(
            text("UPDATE jobs SET status = 'running', started_at = now() WHERE id = :id"),
            {"id": job_id},
        )
        await s.commit()


async def update_job_progress(job_id: str, progress: int) -> None:
    """Update job progress (0-100)."""
    async with async_session_factory() as s:
        await s.execute(
            text("UPDATE jobs SET progress = :p WHERE id = :id"),
            {"id": job_id, "p": min(max(progress, 0), 100)},
        )
        await s.commit()


async def complete_job(
    job_id: str,
    *,
    result: dict[str, Any] | None = None,
) -> None:
    """Mark a job as completed with optional result summary."""
    async with async_session_factory() as s:
        await s.execute(
            text("""
                UPDATE jobs
                SET status = 'completed', progress = 100,
                    result = CAST(:result AS jsonb), completed_at = now()
                WHERE id = :id
            """),
            {"id": job_id, "result": json.dumps(result) if result else None},
        )
        await s.commit()


async def fail_job(job_id: str, error: str | None = None) -> None:
    """Mark a job as failed."""
    async with async_session_factory() as s:
        await s.execute(
            text("""
                UPDATE jobs
                SET status = 'failed', error = :error, completed_at = now()
                WHERE id = :id
            """),
            {"id": job_id, "error": error},
        )
        await s.commit()


async def get_job(job_id: str) -> dict | None:
    """Fetch a job by ID."""
    async with async_session_factory() as s:
        row = (await s.execute(
            text("SELECT * FROM jobs WHERE id = :id"),
            {"id": job_id},
        )).mappings().first()
        return dict(row) if row else None


async def get_job_for_version(dataset_version_id: str) -> dict | None:
    """Fetch the latest job for a dataset version."""
    async with async_session_factory() as s:
        row = (await s.execute(
            text("""
                SELECT * FROM jobs
                WHERE dataset_version_id = :dvid
                ORDER BY created_at DESC LIMIT 1
            """),
            {"dvid": dataset_version_id},
        )).mappings().first()
        return dict(row) if row else None


async def get_jobs_for_dataset(dataset_id: str) -> list[dict]:
    """Fetch all jobs for a dataset, newest first."""
    async with async_session_factory() as s:
        rows = (await s.execute(
            text("SELECT * FROM jobs WHERE dataset_id = :did ORDER BY created_at DESC"),
            {"did": dataset_id},
        )).mappings().all()
        return [dict(r) for r in rows]
