"""Buffer Memory - simple last-N message windowing with storage options."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, TYPE_CHECKING

from ...base import (
    NodeProperty,
    NodePropertyOption,
    NodeTypeDescription,
)
from .base_memory import MemorySubnodeBase, SESSION_ID_PROPERTY
from ....utils.memory import get_db_connection

if TYPE_CHECKING:
    from ....engine.types import NodeDefinition


# Global in-memory storage for chat histories (keyed by session_id)
_chat_histories: dict[str, list[dict[str, str]]] = defaultdict(list)

_INIT_SQL = [
    """
    CREATE TABLE IF NOT EXISTS buffer_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        role TEXT NOT NULL,
        content TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_buffer_session ON buffer_messages(session_id)",
]


def _get_connection():
    return get_db_connection("buffer_conn", _INIT_SQL)


class BufferMemoryNode(MemorySubnodeBase):
    """Buffer Memory - keeps the last N messages with configurable storage."""

    node_description = NodeTypeDescription(
        name="BufferMemory",
        display_name="Buffer Memory",
        description="Simple last-N message windowing with in-memory or SQLite storage",
        icon="fa:layer-group",
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
                description="Number of messages to keep in buffer",
            ),
            NodeProperty(
                display_name="Storage",
                name="storage",
                type="options",
                default="memory",
                options=[
                    NodePropertyOption(
                        name="In Memory",
                        value="memory",
                        description="Store in memory (faster, resets on restart)",
                    ),
                    NodePropertyOption(
                        name="SQLite",
                        value="sqlite",
                        description="Store in SQLite (persistent across restarts)",
                    ),
                ],
                description="Where to store the message buffer",
            ),
        ],
        is_subnode=True,
        subnode_type="memory",
        provides_to_slot="memory",
    )

    def get_config(self, node_definition: NodeDefinition) -> dict[str, Any]:
        """Return memory configuration with accessor functions."""
        session_id = self.get_parameter(node_definition, "sessionId", "default")
        max_messages = self.get_parameter(node_definition, "maxMessages", 20)
        storage = self.get_parameter(node_definition, "storage", "memory")

        if storage == "sqlite":
            return self.build_memory_config(
                memory_type="buffer",
                session_id=session_id,
                get_history=lambda: self._get_history_sqlite(session_id, max_messages),
                add_message=lambda role, content: self._add_message_sqlite(session_id, role, content, max_messages),
                clear_history=lambda: self._clear_history_sqlite(session_id),
                maxMessages=max_messages,
                storage=storage,
            )
        else:
            return self.build_memory_config(
                memory_type="buffer",
                session_id=session_id,
                get_history=lambda: self._get_history_memory(session_id, max_messages),
                add_message=lambda role, content: self._add_message_memory(session_id, role, content, max_messages),
                clear_history=lambda: self._clear_history_memory(session_id),
                maxMessages=max_messages,
                storage=storage,
            )

    # ----- In-Memory Storage -----

    @staticmethod
    def _get_history_memory(session_id: str, max_messages: int) -> list[dict[str, str]]:
        """Get chat history from memory."""
        history = _chat_histories[session_id]
        return history[-max_messages:] if len(history) > max_messages else list(history)

    @staticmethod
    def _add_message_memory(session_id: str, role: str, content: str, max_messages: int) -> None:
        """Add message to in-memory buffer."""
        _chat_histories[session_id].append({"role": role, "content": content})
        # Trim if over limit (keep some buffer to reduce trimming frequency)
        if len(_chat_histories[session_id]) > max_messages * 2:
            _chat_histories[session_id] = _chat_histories[session_id][-max_messages:]

    @staticmethod
    def _clear_history_memory(session_id: str) -> None:
        """Clear in-memory history."""
        _chat_histories[session_id] = []

    # ----- SQLite Storage -----

    @staticmethod
    def _get_history_sqlite(session_id: str, max_messages: int) -> list[dict[str, str]]:
        """Get chat history from SQLite."""
        conn = _get_connection()
        rows = conn.execute(
            "SELECT role, content FROM buffer_messages WHERE session_id = ? ORDER BY id DESC LIMIT ?",
            (session_id, max_messages),
        ).fetchall()
        return [{"role": r[0], "content": r[1]} for r in reversed(rows)]

    @staticmethod
    def _add_message_sqlite(session_id: str, role: str, content: str, max_messages: int) -> None:
        """Add message to SQLite buffer."""
        conn = _get_connection()
        conn.execute(
            "INSERT INTO buffer_messages (session_id, role, content) VALUES (?, ?, ?)",
            (session_id, role, content),
        )
        # Trim old messages
        conn.execute(
            """
            DELETE FROM buffer_messages
            WHERE session_id = ? AND id NOT IN (
                SELECT id FROM buffer_messages WHERE session_id = ? ORDER BY id DESC LIMIT ?
            )
            """,
            (session_id, session_id, max_messages),
        )
        conn.commit()

    @staticmethod
    def _clear_history_sqlite(session_id: str) -> None:
        """Clear SQLite history."""
        conn = _get_connection()
        conn.execute("DELETE FROM buffer_messages WHERE session_id = ?", (session_id,))
        conn.commit()

