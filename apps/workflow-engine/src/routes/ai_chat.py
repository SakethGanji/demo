"""AI Chat SSE endpoint (DESIGN-STAGE6-AI-BUILDER sections 4.13 and 8).

The chat answers questions and proposes changes; it never saves. Events: ``phase``, ``route``, ``text``, ``spec`` (a spec_draft
proposal to approve), ``proposal`` (a built modify proposal to review), ``error`` (with ``code``), ``done``. There is no ``operations``
event: a change is a proposal that a person keeps in the review panel.
"""

from __future__ import annotations

from typing import Annotated, AsyncGenerator

from fastapi import APIRouter, Depends, Header
from sse_starlette.sse import EventSourceResponse, ServerSentEvent

from ..core.dependencies import get_ai_chat_service
from ..schemas.ai_chat import AIChatRequest

router = APIRouter(prefix="/ai")


@router.post("/chat")
async def ai_chat(
    request: AIChatRequest,
    service=Depends(get_ai_chat_service),
    x_actor_name: Annotated[str | None, Header(alias="X-Actor-Name")] = None,
) -> EventSourceResponse:
    """Stream the AI chat reply as SSE events."""

    async def event_generator() -> AsyncGenerator[ServerSentEvent, None]:
        async for event in service.stream_chat(request, x_actor_name):
            yield ServerSentEvent(data=event["data"], event=event["event"])

    return EventSourceResponse(event_generator())
