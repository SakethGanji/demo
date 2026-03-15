"""App CRUD routes + version history endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..core.dependencies import get_app_service
from ..schemas.app import AppCreateRequest, AppUpdateRequest, CreateVersionRequest, UpdateVersionLabelRequest
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


# ── Version endpoints ────────────────────────────────────────────────────────


@router.get("/{app_id}/versions")
async def list_versions(
    app_id: str,
    service: AppService = Depends(get_app_service),
):
    return await service.list_versions(app_id)


@router.post("/{app_id}/versions", status_code=201)
async def create_version(
    app_id: str,
    req: CreateVersionRequest,
    service: AppService = Depends(get_app_service),
):
    result = await service.create_version(
        app_id,
        source_code=req.source_code,
        trigger=req.trigger,
        label=req.label,
        prompt=req.prompt,
        message=req.message,
    )
    if not result:
        raise HTTPException(status_code=404, detail="App not found")
    return result


@router.get("/{app_id}/versions/{version_id}/files")
async def get_version_files(
    app_id: str,
    version_id: int,
    service: AppService = Depends(get_app_service),
):
    files = await service.get_version_files(app_id, version_id)
    if files is None:
        raise HTTPException(status_code=404, detail="Version not found")
    return files


@router.get("/{app_id}/versions/{version_id}")
async def get_version(
    app_id: str,
    version_id: int,
    service: AppService = Depends(get_app_service),
):
    result = await service.get_version(app_id, version_id)
    if not result:
        raise HTTPException(status_code=404, detail="Version not found")
    return result


@router.post("/{app_id}/versions/{version_id}/revert")
async def revert_to_version(
    app_id: str,
    version_id: int,
    service: AppService = Depends(get_app_service),
):
    result = await service.revert_to_version(app_id, version_id)
    if not result:
        raise HTTPException(status_code=404, detail="App or version not found")
    return result


@router.patch("/{app_id}/versions/{version_id}")
async def update_version_label(
    app_id: str,
    version_id: int,
    req: UpdateVersionLabelRequest,
    service: AppService = Depends(get_app_service),
):
    result = await service.update_version_label(app_id, version_id, req.label)
    if not result:
        raise HTTPException(status_code=404, detail="Version not found")
    return result
