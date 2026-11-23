"""API catalog routes (Stage 3A, CONTRACTS-STAGE3A §6)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.dependencies import get_db_session
from ..services.api_catalog import ApiCatalogError, ApiCatalogService, detail_of

router = APIRouter(prefix="/api-specs")


class ApiSpecCreate(BaseModel):
    project_id: str
    name: str | None = None
    spec_text: str | None = None
    url: str | None = None
    source: str | None = Field(None, description="paste (default) or upload; a url import is always 'url'")


class ApiSpecFromExample(BaseModel):
    project_id: str
    name: str = Field(..., min_length=1)
    method: str = "GET"
    base_url: str = ""
    path: str
    status: int = 200
    example: Any = None


def _raise(e: ApiCatalogError) -> HTTPException:
    return HTTPException(status_code=e.status, detail=e.message)


@router.get("")
async def list_api_specs(project_id: str | None = None, session: AsyncSession = Depends(get_db_session)):
    try:
        return await ApiCatalogService(session).list(project_id)
    except ApiCatalogError as e:
        raise _raise(e)


@router.post("")
async def create_api_spec(body: ApiSpecCreate, session: AsyncSession = Depends(get_db_session)):
    try:
        return detail_of(await ApiCatalogService(session).create(body.project_id, body.name, body.spec_text, body.url, body.source))
    except ApiCatalogError as e:
        raise _raise(e)


@router.post("/from-example")
async def create_api_spec_from_example(body: ApiSpecFromExample, session: AsyncSession = Depends(get_db_session)):
    try:
        row = await ApiCatalogService(session).create_from_example(
            body.project_id, body.name, body.method, body.base_url, body.path, body.status, body.example)
        return detail_of(row)
    except ApiCatalogError as e:
        raise _raise(e)


@router.get("/{spec_id}")
async def get_api_spec(spec_id: str, session: AsyncSession = Depends(get_db_session)):
    try:
        return detail_of(await ApiCatalogService(session).get(spec_id))
    except ApiCatalogError as e:
        raise _raise(e)


@router.delete("/{spec_id}")
async def delete_api_spec(spec_id: str, session: AsyncSession = Depends(get_db_session)):
    try:
        await ApiCatalogService(session).delete(spec_id)
    except ApiCatalogError as e:
        raise _raise(e)
    return {"success": True}


@router.get("/{spec_id}/operations/{operation_id:path}/mocks")
async def operation_mocks(spec_id: str, operation_id: str, session: AsyncSession = Depends(get_db_session)):
    try:
        return await ApiCatalogService(session).mocks(spec_id, operation_id)
    except ApiCatalogError as e:
        raise _raise(e)
