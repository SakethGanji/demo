"""Simple in-memory chat history for AI agents."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING
from collections import defaultdict

from ...base import (
    NodeProperty,
    NodeTypeDescription,
)
from .base_memory import MemoryProviderBase, SESSION_ID_PROPERTY

if TYPE_CHECKING:
    from ....engine.types import NodeDefinition


# Global in-memory storage for chat histories (keyed by session_id)
_chat_histories: dict[str, list[dict[str, str]]] = defaultdict(list)


class SimpleMemoryNode(MemoryProviderBase):
    """Simple in-memory chat history storage."""

    node_description = NodeTypeDescription(
        name="SimpleMemory",
        display_name="Simple Memory",
        description="In-memory chat history (resets on server restart)",
        icon="fa:brain",
        group=["ai"],
        inputs=[],
        outputs=[],
        properties=[
            SESSION_ID_PROPERTY,
            NodeProperty(
                display_name="Max Messages",
                name="maxMessages",
                type="number",
                default=20,
                description="Maximum messages to keep in history",
            ),
        ],
    )

    def get_config(self, node_definition: NodeDefinition) -> dict[str, Any]:
        """Return memory configuration with accessor functions."""
        session_id = self.get_parameter(node_definition, "sessionId", "default")
        max_messages = self.get_parameter(node_definition, "maxMessages", 20)

        return self.build_memory_config(
            memory_type="simple",
            session_id=session_id,
            get_history=lambda: self._get_history(session_id, max_messages),
            add_message=lambda role, content: self._add_message(session_id, role, content, max_messages),
            clear_history=lambda: self._clear_history(session_id),
            maxMessages=max_messages,
        )

    @staticmethod
    def _get_history(session_id: str, max_messages: int) -> list[dict[str, str]]:
        """Get chat history for session."""
        history = _chat_histories[session_id]
        return history[-max_messages:] if len(history) > max_messages else history

    @staticmethod
    def _add_message(session_id: str, role: str, content: str, max_messages: int) -> None:
        """Add message to chat history."""
        _chat_histories[session_id].append({"role": role, "content": content})
        # Trim if over limit
        if len(_chat_histories[session_id]) > max_messages * 2:
            _chat_histories[session_id] = _chat_histories[session_id][-max_messages:]

    @staticmethod
    def _clear_history(session_id: str) -> None:
        """Clear chat history for session."""
        _chat_histories[session_id] = []
