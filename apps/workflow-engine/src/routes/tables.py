"""Data tables + rows (BACKEND-DESIGN §3.7, phase 7)."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.dependencies import get_db_session
from ..core.identity import get_actor
from ..services.data_table_service import DataTableService, row_to_dict, table_to_dict
from ..services.project_service import ProjectError, ProjectService
from .projects import raise_project_error

router = APIRouter(prefix="/projects/{project}/tables")


class TableBody(BaseModel):
    name: str | None = None
    description: str | None = None
    columns: list[dict[str, Any]] | None = None


class MigrateBody(BaseModel):
    ops: list[dict[str, Any]]
    dry_run: bool = False
    plan_version: int | None = None


class RowsBody(BaseModel):
    data: Any = None
    rows: list[Any] | None = None


class RowBody(BaseModel):
    data: Any = None


async def _ctx(project: str, session: AsyncSession, tid: str | None = None):
    try:
        p = await ProjectService(session).require(project)
        svc = DataTableService(session)
        t = await svc.get_table(p, tid) if tid else None
    except ProjectError as e:
        raise_project_error(e)
    return p, svc, t


async def _g(coro):
    try:
        return await coro
    except ProjectError as e:
        raise_project_error(e)


@router.get("")
async def list_tables(project: str, session: AsyncSession = Depends(get_db_session)):
    p, svc, _ = await _ctx(project, session)
    return await svc.list_tables(p)


@router.post("", status_code=201)
async def create_table(project: str, body: TableBody, request: Request, session: AsyncSession = Depends(get_db_session)):
    p, svc, _ = await _ctx(project, session)
    return await _g(svc.create_table(p, body.model_dump(exclude_unset=True), actor=get_actor(request)))


@router.get("/{tid}")
async def get_table(project: str, tid: str, session: AsyncSession = Depends(get_db_session)):
    p, svc, t = await _ctx(project, session, tid)
    return table_to_dict(t, await svc.row_count(t.id))


@router.put("/{tid}")
async def update_table(project: str, tid: str, body: TableBody, session: AsyncSession = Depends(get_db_session)):
    p, svc, _ = await _ctx(project, session, tid)
    return await _g(svc.update_table(p, tid, body.model_dump(exclude_unset=True)))


@router.post("/{tid}/migrate")
async def migrate_table(project: str, tid: str, body: MigrateBody, request: Request, session: AsyncSession = Depends(get_db_session)):
    """Apply additive column changes (add_column with backfill, add_enum_values, set_unique, ...). Destructive ops are 422
    ``schema_change_refused``; they are made by hand with PUT on the table."""
    p, svc, t = await _ctx(project, session, tid)
    return await _g(svc.apply_migration(p, t.id, body.ops, plan_version=body.plan_version, actor=get_actor(request),
                                        dry_run=body.dry_run))


@router.get("/{tid}/migrations")
async def table_migrations(project: str, tid: str, session: AsyncSession = Depends(get_db_session)):
    p, svc, t = await _ctx(project, session, tid)
    return await _g(svc.migrations(p, t.id))


@router.delete("/{tid}")
async def delete_table(project: str, tid: str, session: AsyncSession = Depends(get_db_session)):
    p, svc, _ = await _ctx(project, session, tid)
    await _g(svc.delete_table(p, tid))
    return {"success": True}


@router.get("/{tid}/rows")
async def list_rows(project: str, tid: str, limit: int = 50, cursor: str | None = None, sort: str | None = None,
                    filter: str | None = None, count: bool = False, session: AsyncSession = Depends(get_db_session)):
    p, svc, t = await _ctx(project, session, tid)
    flt = None
    if filter:
        try:
            flt = json.loads(filter)
        except ValueError:
            raise_project_error(ProjectError(422, "invalid_filter", "filter must be URL-encoded JSON"))
    return await _g(svc.list_rows(t, filter=flt, sort=sort, limit=limit, cursor=cursor, count=count))


@router.post("/{tid}/rows", status_code=201)
async def insert_rows(project: str, tid: str, body: RowsBody, session: AsyncSession = Depends(get_db_session)):
    p, svc, t = await _ctx(project, session, tid)
    if body.rows is not None:
        rows = await _g(svc.insert_rows(t, body.rows))
        return {"inserted": len(rows), "rows": [row_to_dict(r) for r in rows]}
    rows = await _g(svc.insert_rows(t, [body.data]))
    return row_to_dict(rows[0])


@router.get("/{tid}/rows/{row_id}")
async def get_row(project: str, tid: str, row_id: int, session: AsyncSession = Depends(get_db_session)):
    p, svc, t = await _ctx(project, session, tid)
    return row_to_dict(await _g(svc.get_row(t, row_id)))


@router.patch("/{tid}/rows/{row_id}")
async def patch_row(project: str, tid: str, row_id: int, body: RowBody, session: AsyncSession = Depends(get_db_session)):
    p, svc, t = await _ctx(project, session, tid)
    return row_to_dict(await _g(svc.update_row(t, row_id, body.data, merge=True)))


@router.put("/{tid}/rows/{row_id}")
async def put_row(project: str, tid: str, row_id: int, body: RowBody, session: AsyncSession = Depends(get_db_session)):
    p, svc, t = await _ctx(project, session, tid)
    return row_to_dict(await _g(svc.update_row(t, row_id, body.data, merge=False)))


@router.delete("/{tid}/rows/{row_id}")
async def delete_row(project: str, tid: str, row_id: int, session: AsyncSession = Depends(get_db_session)):
    p, svc, t = await _ctx(project, session, tid)
    await _g(svc.delete_row(t, row_id))
    return {"success": True}
