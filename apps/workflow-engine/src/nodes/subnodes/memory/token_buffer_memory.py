"""Token Buffer Memory - token-aware windowing instead of message count."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from ...base import (
    NodeProperty,
    NodePropertyOption,
    NodeTypeDescription,
)
from .base_memory import MemorySubnodeBase, SESSION_ID_PROPERTY
from ....utils.memory import count_message_tokens, get_db_connection

if TYPE_CHECKING:
    from ....engine.types import NodeDefinition


_INIT_SQL = [
    """
    CREATE TABLE IF NOT EXISTS token_buffer_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        role TEXT NOT NULL,
        content TEXT NOT NULL,
        token_count INTEGER NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_token_buffer_session ON token_buffer_messages(session_id)",
]


def _get_connection():
    return get_db_connection("token_buffer_conn", _INIT_SQL)


class TokenBufferMemoryNode(MemorySubnodeBase):
    """Token Buffer Memory - manages context by token budget instead of message count."""

    node_description = NodeTypeDescription(
        name="TokenBufferMemory",
        display_name="Token Buffer Memory",
        description="Token-aware windowing - keeps messages up to a token budget",
        icon="fa:coins",
        group=["ai"],
        inputs=[],
        outputs=[],
        properties=[
            SESSION_ID_PROPERTY,
            NodeProperty(
                display_name="Max Tokens",
                name="maxTokens",
                type="number",
                default=4000,
                description="Maximum token budget for conversation history",
            ),
            NodeProperty(
                display_name="Token Method",
                name="tokenMethod",
                type="options",
                default="tiktoken",
                options=[
                    NodePropertyOption(
                        name="Tiktoken",
                        value="tiktoken",
                        description="Accurate token counting using tiktoken library",
                    ),
                    NodePropertyOption(
                        name="Character Estimate",
                        value="chars",
                        description="Fast estimate (~4 chars per token)",
                    ),
                ],
                description="Method for counting tokens",
            ),
            NodeProperty(
                display_name="Tiktoken Model",
                name="tiktokenModel",
                type="string",
                default="gpt-4",
                description="Model name for tiktoken encoding (only used with tiktoken method)",
            ),
        ],
        is_subnode=True,
        subnode_type="memory",
        provides_to_slot="memory",
    )

    def get_config(self, node_definition: NodeDefinition) -> dict[str, Any]:
        """Return memory configuration with accessor functions."""
        session_id = self.get_parameter(node_definition, "sessionId", "default")
        max_tokens = self.get_parameter(node_definition, "maxTokens", 4000)
        token_method = self.get_parameter(node_definition, "tokenMethod", "tiktoken")
        tiktoken_model = self.get_parameter(node_definition, "tiktokenModel", "gpt-4")

        return self.build_memory_config(
            memory_type="token_buffer",
            session_id=session_id,
            get_history=lambda: self._get_history(session_id, max_tokens, token_method, tiktoken_model),
            add_message=lambda role, content: self._add_message(session_id, role, content, max_tokens, token_method, tiktoken_model),
            clear_history=lambda: self._clear_history(session_id),
            maxTokens=max_tokens,
            tokenMethod=token_method,
            tiktokenModel=tiktoken_model,
        )

    @staticmethod
    def _get_history(
        session_id: str,
        max_tokens: int,
        token_method: str,
        tiktoken_model: str,
    ) -> list[dict[str, str]]:
        """Get chat history within token budget."""
        conn = _get_connection()
        rows = conn.execute(
            "SELECT role, content, token_count FROM token_buffer_messages WHERE session_id = ? ORDER BY id DESC",
            (session_id,),
        ).fetchall()

        # Build history from newest to oldest, stop when budget exceeded
        result = []
        total_tokens = 0
        for role, content, token_count in rows:
            if total_tokens + token_count > max_tokens:
                break
            result.append({"role": role, "content": content})
            total_tokens += token_count

        # Return in chronological order
        return list(reversed(result))

    @staticmethod
    def _add_message(
        session_id: str,
        role: str,
        content: str,
        max_tokens: int,
        token_method: str,
        tiktoken_model: str,
    ) -> None:
        """Add message and trim old entries to stay within token budget."""
        conn = _get_connection()

        # Count tokens for new message
        token_count = count_message_tokens(
            {"role": role, "content": content},
            method=token_method,
            model=tiktoken_model,
        )

        # Insert new message
        conn.execute(
            "INSERT INTO token_buffer_messages (session_id, role, content, token_count) VALUES (?, ?, ?, ?)",
            (session_id, role, content, token_count),
        )

        # Get all messages with token counts (newest first)
        rows = conn.execute(
            "SELECT id, token_count FROM token_buffer_messages WHERE session_id = ? ORDER BY id DESC",
            (session_id,),
        ).fetchall()

        # Find messages to keep (within budget)
        total_tokens = 0
        ids_to_keep = []
        for msg_id, msg_tokens in rows:
            if total_tokens + msg_tokens <= max_tokens:
                ids_to_keep.append(msg_id)
                total_tokens += msg_tokens
            else:
                break

        # Delete messages outside budget
        if ids_to_keep:
            placeholders = ",".join("?" * len(ids_to_keep))
            conn.execute(
                f"DELETE FROM token_buffer_messages WHERE session_id = ? AND id NOT IN ({placeholders})",
                (session_id, *ids_to_keep),
            )

        conn.commit()

    @staticmethod
    def _clear_history(session_id: str) -> None:
        """Clear history for session."""
        conn = _get_connection()
        conn.execute("DELETE FROM token_buffer_messages WHERE session_id = ?", (session_id,))
        conn.commit()

