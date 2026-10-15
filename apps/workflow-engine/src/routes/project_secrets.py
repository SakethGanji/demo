"""Project secrets routes (BACKEND-DESIGN §3.2, phase 2). Secret values are write-only."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.dependencies import get_db_session
from ..services.project_service import ProjectError, ProjectService
from ..services.secret_service import SecretService
from .projects import raise_project_error

router = APIRouter(prefix="/projects/{project}/secrets")


class SecretCreateRequest(BaseModel):
    key: str
    value: str
    environment: str | None = None
    type: str = "secret"
    description: str | None = Field(None, max_length=500)
    allowed_hosts: list[str] | None = None


class SecretUpdateRequest(BaseModel):
    value: str | None = None
    description: str | None = Field(None, max_length=500)
    allowed_hosts: list[str] | None = None


async def _ctx(project: str, session: AsyncSession):
    try:
        p = await ProjectService(session).require(project)
    except ProjectError as e:
        raise_project_error(e)
    return p, SecretService(session)


@router.get("")
async def list_secrets(project: str, environment: str | None = None, session: AsyncSession = Depends(get_db_session)):
    p, svc = await _ctx(project, session)
    return await svc.effective(p, environment)


@router.get("/missing")
async def missing_secrets(project: str, environment: str | None = None, session: AsyncSession = Depends(get_db_session)):
    p, svc = await _ctx(project, session)
    return await svc.missing(p, environment)


@router.post("", status_code=201)
async def create_secret(project: str, body: SecretCreateRequest, session: AsyncSession = Depends(get_db_session)):
    p, svc = await _ctx(project, session)
    try:
        return await svc.create(p, body.model_dump())
    except ProjectError as e:
        raise_project_error(e)


@router.put("/{secret_id}")
async def update_secret(project: str, secret_id: int, body: SecretUpdateRequest, session: AsyncSession = Depends(get_db_session)):
    p, svc = await _ctx(project, session)
    try:
        return await svc.update(p, secret_id, body.model_dump(exclude_unset=True))
    except ProjectError as e:
        raise_project_error(e)


@router.delete("/{secret_id}")
async def delete_secret(project: str, secret_id: int, session: AsyncSession = Depends(get_db_session)) -> dict[str, Any]:
    p, svc = await _ctx(project, session)
    try:
        await svc.delete(p, secret_id)
    except ProjectError as e:
        raise_project_error(e)
    return {"success": True}
