"""AI Chat SSE endpoint."""

from __future__ import annotations

from typing import AsyncGenerator

from fastapi import APIRouter, Depends
from sse_starlette.sse import EventSourceResponse, ServerSentEvent

from ..core.dependencies import get_ai_chat_service, get_ai_chat_service_adk
from ..schemas.ai_chat import AIChatRequest

router = APIRouter(prefix="/ai")

# Toggle: "default" | "adk"
_BACKEND = "default"

_get_service = get_ai_chat_service if _BACKEND == "default" else get_ai_chat_service_adk


@router.post("/chat")
async def ai_chat(
    request: AIChatRequest,
    service=Depends(_get_service),
) -> EventSourceResponse:
    """Stream AI chat response as SSE events."""

    async def event_generator() -> AsyncGenerator[ServerSentEvent, None]:
        async for event in service.stream_chat(request):
            yield ServerSentEvent(
                data=event["data"],
                event=event["event"],
            )

    return EventSourceResponse(event_generator())
