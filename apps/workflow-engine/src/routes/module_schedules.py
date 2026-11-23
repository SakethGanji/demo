"""Scheduled functions of a project's code modules.

    GET   /projects/{project}/schedules              list with status
    POST  /projects/{project}/schedules/{id}/run     run now (409 while a run is in flight)
    PATCH /projects/{project}/schedules/{id}         {enabled}
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.dependencies import get_db_session
from ..db.models import CodeModuleModel
from ..db.schedule_models import ModuleScheduleModel
from ..services import module_schedules as ms
from ..services.project_service import ProjectError, ProjectService
from .projects import raise_project_error

router = APIRouter()


class SchedulePatch(BaseModel):
    enabled: bool


async def _load(session: AsyncSession, project: str, schedule_id: str | None = None):
    try:
        p = await ProjectService(session).require(project)
        return p, (await ms.get_for_project(session, p.id, schedule_id) if schedule_id else None)
    except ProjectError as e:
        raise_project_error(e)


async def _module_name(session: AsyncSession, module_id: str) -> str | None:
    m = await session.get(CodeModuleModel, module_id)
    return m.name if m else None


@router.get("/projects/{project}/schedules")
async def list_schedules(project: str, session: AsyncSession = Depends(get_db_session)):
    p, _ = await _load(session, project)
    names = {m.id: m.name for m in (await session.execute(select(CodeModuleModel).where(CodeModuleModel.project_id == p.id))).scalars()}
    rows = (await session.execute(select(ModuleScheduleModel).where(ModuleScheduleModel.project_id == p.id)
                                  .order_by(ModuleScheduleModel.module_id, ModuleScheduleModel.function))).scalars().all()
    return [ms.to_dict(r, names.get(r.module_id)) for r in rows]


@router.patch("/projects/{project}/schedules/{schedule_id}")
async def patch_schedule(project: str, schedule_id: str, body: SchedulePatch, session: AsyncSession = Depends(get_db_session)):
    _, s = await _load(session, project, schedule_id)
    from datetime import datetime

    s.enabled, s.updated_at = body.enabled, datetime.now()
    if body.enabled:  # re-enabling starts the clock now, never a burst of missed ticks
        s.next_run_at = ms.next_after(s.cron, s.every_seconds, datetime.now())
    await session.commit()
    return ms.to_dict(s, await _module_name(session, s.module_id))


@router.post("/projects/{project}/schedules/{schedule_id}/run")
async def run_schedule(project: str, schedule_id: str, session: AsyncSession = Depends(get_db_session)):
    from fastapi import HTTPException

    from ..db import session as db_session

    _, s = await _load(session, project, schedule_id)
    if not await ms.claim(session, s.id):
        raise HTTPException(409, detail={"code": "already_running", "message": "this schedule is still running"})
    result = await asyncio.shield(ms.execute(db_session.async_session_factory, s.id, trigger="manual"))
    await session.refresh(s)
    return {"run": result, "schedule": ms.to_dict(s, await _module_name(session, s.module_id))}
