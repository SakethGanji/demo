"""App CRUD routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..core.dependencies import get_app_service
from ..schemas.app import AppCreateRequest, AppUpdateRequest
from ..services.app_service import AppService

router = APIRouter(prefix="/apps")


@router.get("")
async def list_apps(
    folder_id: str | None = None,
    service: AppService = Depends(get_app_service),
):
    return await service.list_apps(folder_id)


@router.post("", status_code=201)
async def create_app(
    req: AppCreateRequest,
    service: AppService = Depends(get_app_service),
):
    return await service.create_app(req)


@router.get("/{app_id}")
async def get_app(
    app_id: str,
    service: AppService = Depends(get_app_service),
):
    result = await service.get_app(app_id)
    if not result:
        raise HTTPException(status_code=404, detail="App not found")
    return result


@router.put("/{app_id}")
async def update_app(
    app_id: str,
    req: AppUpdateRequest,
    service: AppService = Depends(get_app_service),
):
    result = await service.update_app(app_id, req)
    if not result:
        raise HTTPException(status_code=404, detail="App not found")
    return result


@router.delete("/{app_id}")
async def delete_app(
    app_id: str,
    service: AppService = Depends(get_app_service),
):
    if not await service.delete_app(app_id):
        raise HTTPException(status_code=404, detail="App not found")
    return {"success": True}


@router.post("/{app_id}/publish")
async def publish_app(
    app_id: str,
    service: AppService = Depends(get_app_service),
):
    result = await service.publish_app(app_id)
    if not result:
        raise HTTPException(status_code=404, detail="App not found")
    return result
