"""App CRUD routes + version history endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from ..core.config import settings
from ..core.dependencies import get_app_service, get_execution_service
from ..schemas.app import (
    AppCreateRequest,
    AppPublishRequest,
    AppUpdateRequest,
    CreateVersionRequest,
    UpdateVersionLabelRequest,
)
from ..services.app_service import AppPublishError, AppService, AppValidationError

router = APIRouter(prefix="/apps")


@router.get("")
async def list_apps(
    folder_id: str | None = None,
    project_id: str | None = None,
    service: AppService = Depends(get_app_service),
):
    return await service.list_apps(folder_id, project_id=project_id)


@router.post("", status_code=201)
async def create_app(
    req: AppCreateRequest,
    service: AppService = Depends(get_app_service),
):
    try:
        return await service.create_app(req)
    except AppValidationError as e:
        raise HTTPException(status_code=422, detail=e.detail()) from e
    except AppPublishError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


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
    try:
        result = await service.update_app(app_id, req)
    except AppValidationError as e:
        raise HTTPException(status_code=422, detail=e.detail()) from e
    except AppPublishError as e:
        if str(e).startswith("project "):
            raise HTTPException(status_code=422, detail=str(e)) from e
        raise
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
    request: Request,
    req: AppPublishRequest | None = None,
    service: AppService = Depends(get_app_service),
):
    try:
        result = await service.publish_app(app_id, req)
    except AppPublishError as e:
        msg = str(e)
        if msg == "app not found":
            raise HTTPException(status_code=404, detail=msg) from e
        # Bad slug, missing source, bundle error etc. — all client-actionable.
        raise HTTPException(status_code=400, detail=msg) from e

    # Build the absolute public URL. Prefer the configured public_base_url;
    # fall back to the request's own scheme+host so dev / single-host setups
    # work without extra config.
    if result.slug:
        base = settings.public_base_url
        if not base:
            base = f"{request.url.scheme}://{request.url.netloc}"
        result.public_url = f"{base.rstrip('/')}/a/{result.slug}"
    return result


@router.post("/{app_id}/unpublish")
async def unpublish_app(
    app_id: str,
    service: AppService = Depends(get_app_service),
):
    try:
        return await service.unpublish_app(app_id)
    except AppPublishError as e:
        msg = str(e)
        if msg == "app not found":
            raise HTTPException(status_code=404, detail=msg) from e
        raise HTTPException(status_code=400, detail=msg) from e


# ── App ↔ API (phase 6) ───────────────────────────────────────────────────────


@router.get("/{app_id}/api-usage")
async def app_api_usage(app_id: str, service: AppService = Depends(get_app_service)):
    from ..db.models import AppModel
    from ..services.api_usage import ApiUsageService
    app = await service._session.get(AppModel, app_id)
    if not app:
        raise HTTPException(status_code=404, detail="App not found")
    return await ApiUsageService(service._session).app_usage(app)


@router.post("/{app_id}/workflow/{workflow_id}/run")
async def preview_workflow_run(app_id: str, workflow_id: str, request: Request,
                               service: AppService = Depends(get_app_service),
                               exec_service=Depends(get_execution_service)):
    """G20: the studio preview's equivalent of /a/{slug}/api/workflow/{id}/run (preview token)."""
    from ..core.public_tokens import PublicTokenError, verify_token
    from ..db.models import AppModel, ProjectModel
    from .public_apps import _run_app_workflow

    app = await service._session.get(AppModel, app_id)
    if not app:
        raise HTTPException(status_code=404, detail={"code": "app_not_found", "message": "App not found"})
    auth = request.headers.get("authorization") or ""
    if not auth.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail={"code": "unauthorized", "message": "preview token required"})
    try:
        claims = verify_token(auth.split(" ", 1)[1].strip())
    except PublicTokenError as e:
        code = "token_expired" if "expired" in str(e) else "unauthorized"
        raise HTTPException(status_code=401, detail={"code": code, "message": str(e)}) from e
    if not claims.preview or claims.app_id != app.id:
        raise HTTPException(status_code=401, detail={"code": "unauthorized", "message": "not a preview token for this app"})
    if workflow_id not in set(app.workflow_ids or []):
        raise HTTPException(status_code=403, detail={"code": "workflow_not_allowed", "message": "workflow not allowed by this app"})
    try:
        body = await request.json()
    except Exception:
        body = {}
    project = await service._session.get(ProjectModel, app.project_id)
    return await _run_app_workflow(app, workflow_id, body if isinstance(body, dict) else {"value": body}, exec_service,
                                   environment=claims.env or (project.default_environment if project else "default"),
                                   mode="app")


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
