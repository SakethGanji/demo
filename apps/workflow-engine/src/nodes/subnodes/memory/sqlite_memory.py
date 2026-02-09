"""SQLite-backed persistent chat memory for AI agents."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from ...base import (
    NodeProperty,
    NodeTypeDescription,
)
from .base_memory import MemorySubnodeBase, SESSION_ID_PROPERTY
from ....utils.memory import get_db_connection

if TYPE_CHECKING:
    from ....engine.types import NodeDefinition


_INIT_SQL = [
    """
    CREATE TABLE IF NOT EXISTS chat_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        role TEXT NOT NULL,
        content TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_chat_session ON chat_messages(session_id)",
]


def _get_connection():
    return get_db_connection("sqlite_mem_conn", _INIT_SQL)


class SQLiteMemoryNode(MemorySubnodeBase):
    """Persistent SQLite-backed chat memory that survives server restarts."""

    node_description = NodeTypeDescription(
        name="SQLiteMemory",
        display_name="SQLite Memory",
        description="Persistent chat history stored in SQLite",
        icon="fa:database",
        group=["ai"],
        inputs=[],
        outputs=[],
        properties=[
            SESSION_ID_PROPERTY,
            NodeProperty(
                display_name="Max Messages",
                name="maxMessages",
                type="number",
                default=50,
                description="Maximum messages to keep in history",
            ),
        ],
        is_subnode=True,
        subnode_type="memory",
        provides_to_slot="memory",
    )

    def get_config(self, node_definition: NodeDefinition) -> dict[str, Any]:
        """Return memory configuration with accessor functions."""
        session_id = self.get_parameter(node_definition, "sessionId", "default")
        max_messages = self.get_parameter(node_definition, "maxMessages", 50)

        return self.build_memory_config(
            memory_type="sqlite",
            session_id=session_id,
            get_history=lambda: self._get_history(session_id, max_messages),
            add_message=lambda role, content: self._add_message(session_id, role, content, max_messages),
            clear_history=lambda: self._clear_history(session_id),
            maxMessages=max_messages,
        )

    @staticmethod
    def _get_history(session_id: str, max_messages: int) -> list[dict[str, str]]:
        """Get chat history for session."""
        conn = _get_connection()
        rows = conn.execute(
            "SELECT role, content FROM chat_messages WHERE session_id = ? ORDER BY id DESC LIMIT ?",
            (session_id, max_messages),
        ).fetchall()
        # Rows come back newest-first, reverse to chronological order
        return [{"role": r[0], "content": r[1]} for r in reversed(rows)]

    @staticmethod
    def _add_message(session_id: str, role: str, content: str, max_messages: int) -> None:
        """Add message and trim old entries beyond limit."""
        conn = _get_connection()
        conn.execute(
            "INSERT INTO chat_messages (session_id, role, content) VALUES (?, ?, ?)",
            (session_id, role, content),
        )
        # Trim: keep only the latest max_messages rows for this session
        conn.execute(
            """
            DELETE FROM chat_messages
            WHERE session_id = ? AND id NOT IN (
                SELECT id FROM chat_messages WHERE session_id = ? ORDER BY id DESC LIMIT ?
            )
            """,
            (session_id, session_id, max_messages),
        )
        conn.commit()

    @staticmethod
    def _clear_history(session_id: str) -> None:
        """Clear chat history for session."""
        conn = _get_connection()
        conn.execute("DELETE FROM chat_messages WHERE session_id = ?", (session_id,))
        conn.commit()
