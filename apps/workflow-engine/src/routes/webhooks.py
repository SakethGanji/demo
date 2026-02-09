"""Webhook routes for triggering workflows."""

from __future__ import annotations

import base64
import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse, HTMLResponse, Response

from ..core.exceptions import (
    WorkflowNotFoundError,
    WorkflowInactiveError,
    WebhookError,
)
from ..core.dependencies import get_workflow_repository, get_execution_repository
from ..engine.types import WebhookResponse
from ..services.webhook_service import WebhookService
from ..repositories import WorkflowRepository, ExecutionRepository

router = APIRouter()


def _build_response(result: dict[str, Any] | WebhookResponse) -> Response:
    """Build appropriate FastAPI response from service result."""
    if isinstance(result, WebhookResponse):
        headers = result.headers or {}

        if result.body is None:
            return Response(
                status_code=result.status_code,
                headers=headers,
            )

        # Binary response (bytes body or binary flag)
        if result.is_binary or isinstance(result.body, bytes):
            return Response(
                content=result.body,
                status_code=result.status_code,
                media_type=result.content_type,
                headers=headers,
            )

        if result.content_type == "text/plain":
            return PlainTextResponse(
                content=str(result.body),
                status_code=result.status_code,
                headers=headers,
            )
        elif result.content_type == "text/html":
            return HTMLResponse(
                content=str(result.body),
                status_code=result.status_code,
                headers=headers,
            )
        elif result.content_type == "application/xml":
            return Response(
                content=str(result.body),
                status_code=result.status_code,
                media_type="application/xml",
                headers=headers,
            )
        else:
            return JSONResponse(
                content=result.body,
                status_code=result.status_code,
                headers=headers,
            )
    else:
        return JSONResponse(content=result)


def get_webhook_service(
    workflow_repo: Annotated[WorkflowRepository, Depends(get_workflow_repository)],
    execution_repo: Annotated[ExecutionRepository, Depends(get_execution_repository)],
) -> WebhookService:
    """Get webhook service instance."""
    return WebhookService(workflow_repo, execution_repo)


WebhookServiceDep = Annotated[WebhookService, Depends(get_webhook_service)]


async def _extract_request_data(
    request: Request,
) -> tuple[dict[str, Any], dict[str, str], dict[str, str], bytes]:
    """Extract body, headers, query params, and raw body from request.

    Returns (parsed_body, headers, query_params, raw_body).
    """
    # Capture raw body first (Starlette caches it, so subsequent reads work)
    raw_body = await request.body()

    content_type = request.headers.get("content-type", "")

    if "multipart/form-data" in content_type:
        form = await request.form()
        body: dict[str, Any] = {}
        for key, value in form.items():
            if hasattr(value, "read"):  # It's an UploadFile
                file_bytes = await value.read()
                body[key] = {
                    "filename": value.filename,
                    "content": base64.b64encode(file_bytes).decode(),
                    "content_type": value.content_type,
                    "size": len(file_bytes),
                }
            else:
                # Try to parse as number if possible
                try:
                    body[key] = int(value)
                except ValueError:
                    try:
                        body[key] = float(value)
                    except ValueError:
                        body[key] = value
    elif "application/x-www-form-urlencoded" in content_type:
        form = await request.form()
        body = {}
        for key, value in form.items():
            try:
                body[key] = int(value)
            except ValueError:
                try:
                    body[key] = float(value)
                except ValueError:
                    body[key] = value
    else:
        try:
            body = json.loads(raw_body) if raw_body else {}
        except (json.JSONDecodeError, UnicodeDecodeError):
            body = {}

    headers = dict(request.headers)
    query_params = dict(request.query_params)

    return body, headers, query_params, raw_body


# ---------------------------------------------------------------------------
# Webhook by custom path: /webhook/p/{webhook_path}
# IMPORTANT: These routes must be declared BEFORE /webhook/{workflow_id}
# so that "/webhook/p/..." is not matched as workflow_id="p"
# ---------------------------------------------------------------------------


async def _handle_by_path(
    webhook_path: str, method: str, request: Request, service: WebhookService
) -> Response:
    """Shared handler for path-based webhook routes."""
    body, headers, query_params, raw_body = await _extract_request_data(request)

    try:
        result = await service.handle_webhook_by_path(
            path=webhook_path,
            method=method,
            body=body,
            headers=headers,
            query_params=query_params,
            raw_body=raw_body,
        )
        return _build_response(result)
    except WorkflowNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.message)
    except WorkflowInactiveError as e:
        raise HTTPException(status_code=400, detail=e.message)
    except WebhookError as e:
        raise HTTPException(status_code=405, detail=e.message)


@router.post("/webhook/p/{webhook_path:path}")
async def handle_path_webhook_post(
    webhook_path: str, request: Request, service: WebhookServiceDep
) -> Response:
    """Handle POST webhook by custom path."""
    return await _handle_by_path(webhook_path, "POST", request, service)


@router.get("/webhook/p/{webhook_path:path}")
async def handle_path_webhook_get(
    webhook_path: str, request: Request, service: WebhookServiceDep
) -> Response:
    """Handle GET webhook by custom path."""
    return await _handle_by_path(webhook_path, "GET", request, service)


@router.put("/webhook/p/{webhook_path:path}")
async def handle_path_webhook_put(
    webhook_path: str, request: Request, service: WebhookServiceDep
) -> Response:
    """Handle PUT webhook by custom path."""
    return await _handle_by_path(webhook_path, "PUT", request, service)


@router.patch("/webhook/p/{webhook_path:path}")
async def handle_path_webhook_patch(
    webhook_path: str, request: Request, service: WebhookServiceDep
) -> Response:
    """Handle PATCH webhook by custom path."""
    return await _handle_by_path(webhook_path, "PATCH", request, service)


@router.delete("/webhook/p/{webhook_path:path}")
async def handle_path_webhook_delete(
    webhook_path: str, request: Request, service: WebhookServiceDep
) -> Response:
    """Handle DELETE webhook by custom path."""
    return await _handle_by_path(webhook_path, "DELETE", request, service)


# ---------------------------------------------------------------------------
# Webhook by workflow ID: /webhook/{workflow_id}
# ---------------------------------------------------------------------------


async def _handle_by_id(
    workflow_id: str, method: str, request: Request, service: WebhookService
) -> Response:
    """Shared handler for ID-based webhook routes."""
    body, headers, query_params, raw_body = await _extract_request_data(request)

    try:
        result = await service.handle_webhook(
            workflow_id=workflow_id,
            method=method,
            body=body,
            headers=headers,
            query_params=query_params,
            raw_body=raw_body,
        )
        return _build_response(result)
    except WorkflowNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.message)
    except WorkflowInactiveError as e:
        raise HTTPException(status_code=400, detail=e.message)
    except WebhookError as e:
        raise HTTPException(status_code=405, detail=e.message)


@router.post("/webhook/{workflow_id}")
async def handle_webhook_post(
    workflow_id: str, request: Request, service: WebhookServiceDep
) -> Response:
    """Handle POST webhook to trigger a workflow."""
    return await _handle_by_id(workflow_id, "POST", request, service)


@router.get("/webhook/{workflow_id}")
async def handle_webhook_get(
    workflow_id: str, request: Request, service: WebhookServiceDep
) -> Response:
    """Handle GET webhook to trigger a workflow."""
    return await _handle_by_id(workflow_id, "GET", request, service)


@router.put("/webhook/{workflow_id}")
async def handle_webhook_put(
    workflow_id: str, request: Request, service: WebhookServiceDep
) -> Response:
    """Handle PUT webhook to trigger a workflow."""
    return await _handle_by_id(workflow_id, "PUT", request, service)


@router.patch("/webhook/{workflow_id}")
async def handle_webhook_patch(
    workflow_id: str, request: Request, service: WebhookServiceDep
) -> Response:
    """Handle PATCH webhook to trigger a workflow."""
    return await _handle_by_id(workflow_id, "PATCH", request, service)


@router.delete("/webhook/{workflow_id}")
async def handle_webhook_delete(
    workflow_id: str, request: Request, service: WebhookServiceDep
) -> Response:
    """Handle DELETE webhook to trigger a workflow."""
    return await _handle_by_id(workflow_id, "DELETE", request, service)
