"""API Tester routes — Postman-lite for capturing real request/response snapshots.

Captured executions become the LLM context fed to the app builder so the
generated app can reproduce the exact URL/method/headers/body shape.
"""

from __future__ import annotations

import base64
import logging
from time import perf_counter
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException

from ..core.dependencies import get_api_test_repository
from ..db.models import ApiTestExecutionModel
from ..repositories import ApiTestRepository
from ..schemas.api_tester import (
    HTTP_METHODS,
    ApiTestExecuteRequest,
    ApiTestExecutionListItem,
    ApiTestExecutionRenameRequest,
    ApiTestExecutionResponse,
)
from ..schemas.common import SuccessResponse
from ..services.schema_inference import summarize_response
from ..utils.ids import generate_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api-tester")

# Cap stored response bodies to keep DB usage sane and prompts focused.
MAX_RESPONSE_BYTES = 5 * 1024 * 1024  # 5 MB
HTTP_TIMEOUT_SECONDS = 30.0


ApiTestRepoDep = Annotated[ApiTestRepository, Depends(get_api_test_repository)]


def _to_response(row: ApiTestExecutionModel) -> ApiTestExecutionResponse:
    return ApiTestExecutionResponse(
        id=row.id,
        name=row.name,
        method=row.method,
        url=row.url,
        request_headers=row.request_headers or {},
        request_body_text=row.request_body_text,
        response_status=row.response_status,
        response_headers=row.response_headers or {},
        response_content_type=row.response_content_type,
        response_size=row.response_size,
        response_body_b64=row.response_body_b64,
        response_truncated=row.response_truncated,
        latency_ms=row.latency_ms,
        error=row.error,
        created_at=row.created_at,
    )


def _to_list_item(row: ApiTestExecutionModel) -> ApiTestExecutionListItem:
    return ApiTestExecutionListItem(
        id=row.id,
        name=row.name,
        method=row.method,
        url=row.url,
        response_status=row.response_status,
        response_content_type=row.response_content_type,
        latency_ms=row.latency_ms,
        error=row.error,
        created_at=row.created_at,
    )


@router.post("/execute", response_model=ApiTestExecutionResponse, status_code=201)
async def execute(body: ApiTestExecuteRequest, repo: ApiTestRepoDep) -> ApiTestExecutionResponse:
    """Run a one-off HTTP request and persist the captured snapshot."""

    method = body.method.upper().strip()
    if method not in HTTP_METHODS:
        raise HTTPException(status_code=400, detail=f"Unsupported method: {body.method}")
    if not body.url.strip():
        raise HTTPException(status_code=400, detail="URL is required")

    headers = {k: v for k, v in (body.headers or {}).items() if k and v is not None}

    row = ApiTestExecutionModel(
        id=generate_id("apit"),
        name=body.name,
        method=method,
        url=body.url,
        request_headers=headers,
        request_body_text=body.body,
    )

    start = perf_counter()
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS, follow_redirects=True) as client:
            content = body.body.encode("utf-8") if body.body is not None else None
            resp = await client.request(method, body.url, headers=headers, content=content)

        elapsed_ms = round((perf_counter() - start) * 1000, 2)

        raw = resp.content or b""
        truncated = len(raw) > MAX_RESPONSE_BYTES
        stored = raw[:MAX_RESPONSE_BYTES] if truncated else raw

        row.response_status = resp.status_code
        row.response_headers = dict(resp.headers)
        row.response_content_type = resp.headers.get("content-type")
        row.response_size = len(raw)
        row.response_body_b64 = base64.b64encode(stored).decode("ascii")
        row.response_truncated = truncated
        row.latency_ms = elapsed_ms
        # Pre-compute the LLM-context summary once, here, instead of redoing
        # the decode/parse work on every app-builder chat turn.
        try:
            row.response_summary = summarize_response(
                response_body_b64=row.response_body_b64,
                content_type=row.response_content_type,
                response_truncated=row.response_truncated,
                response_headers=row.response_headers,
            )
        except Exception as e:  # never fail capture due to summary issues
            logger.warning("response_summary computation failed: %s", e)
            row.response_summary = None
    except httpx.HTTPError as e:
        row.latency_ms = round((perf_counter() - start) * 1000, 2)
        row.error = f"{type(e).__name__}: {e}"
        logger.warning("API tester request failed: %s %s — %s", method, body.url, row.error)

    saved = await repo.create(row)
    return _to_response(saved)


@router.get("/executions", response_model=list[ApiTestExecutionListItem])
async def list_executions(repo: ApiTestRepoDep) -> list[ApiTestExecutionListItem]:
    rows = await repo.list()
    return [_to_list_item(r) for r in rows]


@router.get("/executions/{execution_id}", response_model=ApiTestExecutionResponse)
async def get_execution(execution_id: str, repo: ApiTestRepoDep) -> ApiTestExecutionResponse:
    row = await repo.get(execution_id)
    if not row:
        raise HTTPException(status_code=404, detail="Execution not found")
    return _to_response(row)


@router.patch("/executions/{execution_id}", response_model=ApiTestExecutionResponse)
async def rename_execution(
    execution_id: str,
    body: ApiTestExecutionRenameRequest,
    repo: ApiTestRepoDep,
) -> ApiTestExecutionResponse:
    row = await repo.rename(execution_id, body.name)
    if not row:
        raise HTTPException(status_code=404, detail="Execution not found")
    return _to_response(row)


@router.delete("/executions/{execution_id}", response_model=SuccessResponse)
async def delete_execution(execution_id: str, repo: ApiTestRepoDep) -> SuccessResponse:
    deleted = await repo.delete(execution_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Execution not found")
    return SuccessResponse(message="Execution deleted")
