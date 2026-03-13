"""App Builder AI endpoints — separate from workflow AI chat."""

from __future__ import annotations

from typing import AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException
from sse_starlette.sse import EventSourceResponse, ServerSentEvent

from ..core.dependencies import get_app_builder_ai_service
from ..schemas.app_builder import AppBuilderChatRequest
from ..services.app_builder_ai_service import AppBuilderAIService

router = APIRouter(prefix="/ai/app-builder")


@router.post("/chat")
async def app_builder_chat(
    request: AppBuilderChatRequest,
    service: AppBuilderAIService = Depends(get_app_builder_ai_service),
) -> EventSourceResponse:
    """Stream app builder AI response as SSE."""

    async def event_generator() -> AsyncGenerator[ServerSentEvent, None]:
        async for event in service.stream_chat(request):
            yield ServerSentEvent(data=event["data"], event=event["event"])

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
