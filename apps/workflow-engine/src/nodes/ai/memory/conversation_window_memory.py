"""Conversation Window Memory - keeps N complete conversation turns."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from ...base import (
    NodeProperty,
    NodeTypeDescription,
)
from .base_memory import MemoryProviderBase, SESSION_ID_PROPERTY
from ....utils.memory import get_db_connection

if TYPE_CHECKING:
    from ....engine.types import NodeDefinition


_INIT_SQL = [
    """
    CREATE TABLE IF NOT EXISTS conversation_window_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        role TEXT NOT NULL,
        content TEXT NOT NULL,
        turn_number INTEGER NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_conv_window_session ON conversation_window_messages(session_id)",
]


def _get_connection():
    return get_db_connection("conv_window_conn", _INIT_SQL)


class ConversationWindowMemoryNode(MemoryProviderBase):
    """Conversation Window Memory - keeps N complete conversation turns (user+assistant pairs)."""

    node_description = NodeTypeDescription(
        name="ConversationWindowMemory",
        display_name="Conversation Window Memory",
        description="Keep N complete conversation turns (user+assistant pairs)",
        icon="fa:comments",
        group=["ai"],
        inputs=[],
        outputs=[],
        properties=[
            SESSION_ID_PROPERTY,
            NodeProperty(
                display_name="Max Turns",
                name="maxTurns",
                type="number",
                default=10,
                description="Number of conversation turns to keep (1 turn = user + assistant message)",
            ),
            NodeProperty(
                display_name="Include Partial Turn",
                name="includePartial",
                type="boolean",
                default=True,
                description="Include unpaired last message (e.g., user message awaiting response)",
            ),
        ],
    )

    def get_config(self, node_definition: NodeDefinition) -> dict[str, Any]:
        """Return memory configuration with accessor functions."""
        session_id = self.get_parameter(node_definition, "sessionId", "default")
        max_turns = self.get_parameter(node_definition, "maxTurns", 10)
        include_partial = self.get_parameter(node_definition, "includePartial", True)

        return self.build_memory_config(
            memory_type="conversation_window",
            session_id=session_id,
            get_history=lambda: self._get_history(session_id, max_turns, include_partial),
            add_message=lambda role, content: self._add_message(session_id, role, content, max_turns),
            clear_history=lambda: self._clear_history(session_id),
            maxTurns=max_turns,
            includePartial=include_partial,
        )

    @staticmethod
    def _get_current_turn(session_id: str) -> int:
        """Get the current turn number for a session."""
        conn = _get_connection()
        row = conn.execute(
            "SELECT MAX(turn_number) FROM conversation_window_messages WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        return row[0] if row[0] is not None else 0

    @staticmethod
    def _get_history(session_id: str, max_turns: int, include_partial: bool) -> list[dict[str, str]]:
        """Get chat history by complete turns."""
        conn = _get_connection()

        # Get all messages ordered by turn and id
        rows = conn.execute(
            """
            SELECT role, content, turn_number
            FROM conversation_window_messages
            WHERE session_id = ?
            ORDER BY turn_number ASC, id ASC
            """,
            (session_id,),
        ).fetchall()

        if not rows:
            return []

        # Group messages by turn
        turns: dict[int, list[dict[str, str]]] = {}
        for role, content, turn_number in rows:
            if turn_number not in turns:
                turns[turn_number] = []
            turns[turn_number].append({"role": role, "content": content})

        # Identify complete turns (have both user and assistant)
        sorted_turn_nums = sorted(turns.keys())
        complete_turns = []
        partial_turn = None

        for turn_num in sorted_turn_nums:
            turn_msgs = turns[turn_num]
            has_user = any(m["role"] == "user" for m in turn_msgs)
            has_assistant = any(m["role"] == "assistant" for m in turn_msgs)

            if has_user and has_assistant:
                complete_turns.append(turn_num)
            elif has_user or has_assistant:
                # This is a partial turn (typically the latest)
                partial_turn = turn_num

        # Keep only the last max_turns complete turns
        turns_to_include = complete_turns[-max_turns:]

        # Add partial turn if requested and exists
        if include_partial and partial_turn is not None:
            turns_to_include.append(partial_turn)

        # Build result
        result = []
        for turn_num in sorted(turns_to_include):
            result.extend(turns[turn_num])

        return result

    @staticmethod
    def _add_message(session_id: str, role: str, content: str, max_turns: int) -> None:
        """Add message to conversation history."""
        conn = _get_connection()

        # Determine turn number
        # A new turn starts when we get a user message after an assistant message
        # or when there are no messages yet
        current_turn = ConversationWindowMemoryNode._get_current_turn(session_id)

        # Get the last message to determine if we need a new turn
        last_row = conn.execute(
            "SELECT role FROM conversation_window_messages WHERE session_id = ? ORDER BY id DESC LIMIT 1",
            (session_id,),
        ).fetchone()

        if last_row is None:
            # First message
            turn_number = 1
        elif role == "user" and last_row[0] == "assistant":
            # New turn starts with user message after assistant
            turn_number = current_turn + 1
        else:
            # Continue current turn
            turn_number = current_turn

        # Insert message
        conn.execute(
            "INSERT INTO conversation_window_messages (session_id, role, content, turn_number) VALUES (?, ?, ?, ?)",
            (session_id, role, content, turn_number),
        )

        # Cleanup: remove old complete turns beyond max_turns
        # Get all unique turn numbers with complete pairs
        complete_turns = conn.execute(
            """
            SELECT turn_number,
                   SUM(CASE WHEN role = 'user' THEN 1 ELSE 0 END) as user_count,
                   SUM(CASE WHEN role = 'assistant' THEN 1 ELSE 0 END) as asst_count
            FROM conversation_window_messages
            WHERE session_id = ?
            GROUP BY turn_number
            HAVING user_count > 0 AND asst_count > 0
            ORDER BY turn_number ASC
            """,
            (session_id,),
        ).fetchall()

        # If we have more complete turns than max, delete oldest
        if len(complete_turns) > max_turns:
            turns_to_delete = [t[0] for t in complete_turns[:-max_turns]]
            if turns_to_delete:
                placeholders = ",".join("?" * len(turns_to_delete))
                conn.execute(
                    f"DELETE FROM conversation_window_messages WHERE session_id = ? AND turn_number IN ({placeholders})",
                    (session_id, *turns_to_delete),
                )

        conn.commit()

    @staticmethod
    def _clear_history(session_id: str) -> None:
        """Clear history for session."""
        conn = _get_connection()
        conn.execute("DELETE FROM conversation_window_messages WHERE session_id = ?", (session_id,))
        conn.commit()

