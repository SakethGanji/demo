"""App Builder AI endpoints — separate from workflow AI chat."""

from __future__ import annotations

import asyncio
import logging
from typing import AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException, Request
from sse_starlette.sse import EventSourceResponse, ServerSentEvent

from ..core.dependencies import get_app_builder_ai_service
from ..schemas.app_builder import AppBuilderChatRequest
from ..services.app_builder_ai_service import AppBuilderAIService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai/app-builder")


@router.post("/chat")
async def app_builder_chat(
    request: AppBuilderChatRequest,
    http_request: Request,
    service: AppBuilderAIService = Depends(get_app_builder_ai_service),
) -> EventSourceResponse:
    """Stream app builder AI response as SSE."""

    async def event_generator() -> AsyncGenerator[ServerSentEvent, None]:
        try:
            async for event in service.stream_chat(request):
                # Check if client disconnected — stop generating to save resources
                if await http_request.is_disconnected():
                    logger.info("App builder: client disconnected, stopping stream")
                    return
                yield ServerSentEvent(data=event["data"], event=event["event"])
        except asyncio.CancelledError:
            logger.info("App builder: stream cancelled (client disconnect)")
            return

    return EventSourceResponse(event_generator())


@router.get("/workflow-schema/{workflow_id}")
async def get_workflow_schema(
    workflow_id: str,
    service: AppBuilderAIService = Depends(get_app_builder_ai_service),
):
    """Extract schema from a workflow's latest execution for UI binding."""
    try:
        return await service.extract_workflow_schema(workflow_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
