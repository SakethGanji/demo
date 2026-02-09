"""Base class for memory subnodes with shared config dict pattern."""

from __future__ import annotations

from typing import Any, Callable, TYPE_CHECKING

from ...base import NodeProperty
from ..base_subnode import BaseSubnode
from ....utils.memory import format_history_text

if TYPE_CHECKING:
    from ....engine.types import NodeDefinition


# Common property shared by all memory subnodes.
SESSION_ID_PROPERTY = NodeProperty(
    display_name="Session ID",
    name="sessionId",
    type="string",
    default="default",
    description="Unique session identifier for chat history. Supports expressions.",
)


class MemorySubnodeBase(BaseSubnode):
    """Base class for memory subnodes.

    Provides ``build_memory_config`` which auto-derives ``getHistoryText``
    from the ``getHistory`` callback and ensures all memory subnodes
    expose a consistent config dict shape.
    """

    # Subclasses must set ``node_description`` on the class body.

    @staticmethod
    def build_memory_config(
        *,
        memory_type: str,
        session_id: str,
        get_history: Callable[[], list[dict[str, str]]],
        add_message: Callable[[str, str], None],
        clear_history: Callable[[], None],
        **extra,
    ) -> dict[str, Any]:
        """Build a standardised memory config dict.

        Every memory subnode returns a dict from ``get_config`` with at
        least these keys.  ``getHistoryText`` is auto-derived from
        ``getHistory`` + ``format_history_text``, so subclasses no longer
        need to implement it manually.

        Args:
            memory_type: Short identifier, e.g. ``"buffer"``, ``"summary"``.
            session_id: Session identifier.
            get_history: Callable returning ``list[dict]`` of messages.
            add_message: Callable ``(role, content) -> None``.
            clear_history: Callable ``() -> None``.
            **extra: Additional memory-specific parameters to include.
        """
        return {
            "type": memory_type,
            "sessionId": session_id,
            **extra,
            "getHistory": get_history,
            "addMessage": add_message,
            "clearHistory": clear_history,
            "getHistoryText": lambda: format_history_text(get_history()),
        }
