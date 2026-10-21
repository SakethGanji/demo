"""Project external database connections. The URL is write-only: it is stored as a project secret and never returned."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.dependencies import get_db_session
from ..services.connection_service import ConnectionService
from ..services.project_service import ProjectError, ProjectService
from .projects import raise_project_error

router = APIRouter(prefix="/projects/{project}/connections")


class ConnectionQuery(BaseModel):
    sql: str = Field(..., max_length=20000)
    params: list[Any] | dict[str, Any] | None = None
    limit: int | None = Field(None, ge=1, le=500)


async def _ctx(project: str, session: AsyncSession):
    try:
        p = await ProjectService(session).require(project)
    except ProjectError as e:
        raise_project_error(e)
    return p, ConnectionService(session)


@router.get("")
async def list_connections(project: str, session: AsyncSession = Depends(get_db_session)):
    p, svc = await _ctx(project, session)
    return await svc.list(p)


@router.post("", status_code=201)
async def create_connection(project: str, request: Request, session: AsyncSession = Depends(get_db_session)):
    # parsed by hand: a pydantic validation error would echo the whole body, URL included
    p, svc = await _ctx(project, session)
    try:
        try:
            body = await request.json()
        except ValueError:
            body = None
        if not isinstance(body, dict):
            raise ProjectError(422, "invalid_request", "body must be a JSON object {name, kind, url}")
        return await svc.create(p, body)
    except ProjectError as e:
        raise_project_error(e)


@router.post("/{conn_id}/test")
async def test_connection(project: str, conn_id: str, session: AsyncSession = Depends(get_db_session)):
    p, svc = await _ctx(project, session)
    try:
        return await svc.test(p, conn_id)
    except ProjectError as e:
        raise_project_error(e)


@router.post("/{conn_id}/introspect")
async def introspect_connection(project: str, conn_id: str, session: AsyncSession = Depends(get_db_session)):
    p, svc = await _ctx(project, session)
    try:
        return await svc.introspect(p, conn_id)
    except ProjectError as e:
        raise_project_error(e)


@router.get("/{conn_id}/schema")
async def connection_schema(project: str, conn_id: str, session: AsyncSession = Depends(get_db_session)):
    p, svc = await _ctx(project, session)
    try:
        return await svc.schema(p, conn_id)
    except ProjectError as e:
        raise_project_error(e)


@router.post("/{conn_id}/query")
async def query_connection(project: str, conn_id: str, body: ConnectionQuery, session: AsyncSession = Depends(get_db_session)):
    p, svc = await _ctx(project, session)
    try:
        return await svc.query(p, conn_id, body.sql, body.params, body.limit)
    except ProjectError as e:
        raise_project_error(e)


@router.delete("/{conn_id}")
async def delete_connection(project: str, conn_id: str, session: AsyncSession = Depends(get_db_session)) -> dict[str, Any]:
    p, svc = await _ctx(project, session)
    try:
        await svc.delete(p, conn_id)
    except ProjectError as e:
        raise_project_error(e)
    return {"success": True}
