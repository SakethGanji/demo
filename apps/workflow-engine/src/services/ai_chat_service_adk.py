"""AI Chat service — ADK-based implementation.

Same interface as AIChatService so the route can swap between them.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any, AsyncGenerator

if TYPE_CHECKING:
    from .workflow_service import WorkflowService
from ..engine.node_registry import NodeRegistryClass
from ..schemas.ai_chat import AIChatRequest

logger = logging.getLogger(__name__)


def _sse(event_type: str, data: dict[str, Any]) -> dict[str, Any]:
    return {"event": event_type, "data": json.dumps(data)}


class AIChatServiceADK:
    """ADK-based workflow assistant.

    TODO: implement using Google ADK.
    """

    def __init__(
        self,
        node_registry: NodeRegistryClass,
        workflow_service: WorkflowService | None = None,
    ) -> None:
        self._registry = node_registry
        self._workflow_service = workflow_service

    async def stream_chat(
        self, request: AIChatRequest
    ) -> AsyncGenerator[dict[str, Any], None]:
        # TODO: replace with ADK agent implementation
        yield _sse("text", {"type": "text", "content": "ADK backend is not yet implemented."})
        yield _sse("done", {"type": "done"})
