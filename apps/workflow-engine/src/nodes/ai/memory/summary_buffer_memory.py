"""Summary Buffer Memory - hybrid running summary + full recent messages."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from ...base import (
    NodeProperty,
    NodeTypeDescription,
)
from .base_memory import MemoryProviderBase, SESSION_ID_PROPERTY
from ....utils.memory import call_llm_for_summary, get_db_connection, run_async

if TYPE_CHECKING:
    from ....engine.types import NodeDefinition


_INIT_SQL = [
    """
    CREATE TABLE IF NOT EXISTS summary_buffer_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        role TEXT NOT NULL,
        content TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS summary_buffer_summaries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL UNIQUE,
        summary TEXT NOT NULL,
        last_summarized_id INTEGER,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_summary_buffer_session ON summary_buffer_messages(session_id)",
]


def _get_connection():
    return get_db_connection("summary_buffer_conn", _INIT_SQL)


class SummaryBufferMemoryNode(MemoryProviderBase):
    """Summary Buffer Memory - maintains running summary + full recent messages."""

    node_description = NodeTypeDescription(
        name="SummaryBufferMemory",
        display_name="Summary Buffer Memory",
        description="Hybrid: running summary of older messages + full recent messages",
        icon="fa:layer-group",
        group=["ai"],
        inputs=[],
        outputs=[],
        properties=[
            SESSION_ID_PROPERTY,
            NodeProperty(
                display_name="Recent Messages",
                name="recentMessages",
                type="number",
                default=10,
                description="Number of full messages to keep (older ones get summarized)",
            ),
            NodeProperty(
                display_name="Summary Model",
                name="summaryModel",
                type="string",
                default="gemini-2.0-flash",
                description="LLM model to use for summarization",
            ),
            NodeProperty(
                display_name="Summary Max Tokens",
                name="summaryMaxTokens",
                type="number",
                default=500,
                description="Maximum tokens for the running summary",
            ),
        ],
    )

    def get_config(self, node_definition: NodeDefinition) -> dict[str, Any]:
        """Return memory configuration with accessor functions."""
        session_id = self.get_parameter(node_definition, "sessionId", "default")
        recent_messages = self.get_parameter(node_definition, "recentMessages", 10)
        summary_model = self.get_parameter(node_definition, "summaryModel", "gemini-2.0-flash")
        summary_max_tokens = self.get_parameter(node_definition, "summaryMaxTokens", 500)

        return self.build_memory_config(
            memory_type="summary_buffer",
            session_id=session_id,
            get_history=lambda: self._get_history(session_id, recent_messages),
            add_message=lambda role, content: self._add_message(
                session_id, role, content, recent_messages, summary_model, summary_max_tokens
            ),
            clear_history=lambda: self._clear_history(session_id),
            recentMessages=recent_messages,
            summaryModel=summary_model,
            summaryMaxTokens=summary_max_tokens,
        )

    @staticmethod
    def _get_summary(session_id: str) -> str | None:
        """Get the running summary for a session."""
        conn = _get_connection()
        row = conn.execute(
            "SELECT summary FROM summary_buffer_summaries WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        return row[0] if row else None

    @staticmethod
    def _get_history(session_id: str, recent_messages: int) -> list[dict[str, str]]:
        """Get history: [summary context] + [full recent messages]."""
        conn = _get_connection()

        result = []

        # Get running summary
        summary = SummaryBufferMemoryNode._get_summary(session_id)
        if summary:
            result.append({
                "role": "system",
                "content": f"[Conversation Context Summary]\n{summary}",
            })

        # Get recent messages (full content)
        recent_rows = conn.execute(
            """
            SELECT role, content FROM summary_buffer_messages
            WHERE session_id = ?
            ORDER BY id DESC LIMIT ?
            """,
            (session_id, recent_messages),
        ).fetchall()

        # Add in chronological order
        for role, content in reversed(recent_rows):
            result.append({"role": role, "content": content})

        return result

    @staticmethod
    def _add_message(
        session_id: str,
        role: str,
        content: str,
        recent_messages: int,
        summary_model: str,
        summary_max_tokens: int,
    ) -> None:
        """Add message and update summary if needed."""
        conn = _get_connection()

        # Insert new message
        conn.execute(
            "INSERT INTO summary_buffer_messages (session_id, role, content) VALUES (?, ?, ?)",
            (session_id, role, content),
        )
        conn.commit()

        # Get total message count
        count_row = conn.execute(
            "SELECT COUNT(*) FROM summary_buffer_messages WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        total_count = count_row[0] if count_row else 0

        # If we have more messages than recent limit, summarize the overflow
        if total_count > recent_messages:
            SummaryBufferMemoryNode._update_summary(
                session_id, recent_messages, summary_model, summary_max_tokens
            )

    @staticmethod
    def _update_summary(
        session_id: str,
        recent_messages: int,
        summary_model: str,
        summary_max_tokens: int,
    ) -> None:
        """Update the running summary with messages that will be removed."""
        conn = _get_connection()

        # Get all messages ordered by id
        all_rows = conn.execute(
            """
            SELECT id, role, content FROM summary_buffer_messages
            WHERE session_id = ?
            ORDER BY id ASC
            """,
            (session_id,),
        ).fetchall()

        if len(all_rows) <= recent_messages:
            return  # Nothing to summarize

        # Messages to summarize (will be deleted)
        to_summarize = all_rows[:-recent_messages]
        messages_to_add = [{"role": r[1], "content": r[2]} for r in to_summarize]

        # Get existing summary
        existing_summary = SummaryBufferMemoryNode._get_summary(session_id)

        # Create updated summary
        try:
            new_summary = run_async(
                call_llm_for_summary(
                    messages_to_add,
                    summary_model,
                    max_tokens=summary_max_tokens,
                    previous_summary=existing_summary,
                )
            )
        except Exception:
            # If summarization fails, keep existing summary
            new_summary = existing_summary

        # Delete old messages
        ids_to_delete = [r[0] for r in to_summarize]
        if ids_to_delete:
            placeholders = ",".join("?" * len(ids_to_delete))
            conn.execute(
                f"DELETE FROM summary_buffer_messages WHERE id IN ({placeholders})",
                ids_to_delete,
            )

        # Upsert summary
        if new_summary:
            conn.execute(
                """
                INSERT INTO summary_buffer_summaries (session_id, summary, last_summarized_id)
                VALUES (?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    summary = excluded.summary,
                    last_summarized_id = excluded.last_summarized_id,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (session_id, new_summary, ids_to_delete[-1] if ids_to_delete else None),
            )

        conn.commit()

    @staticmethod
    def _clear_history(session_id: str) -> None:
        """Clear history and summary for session."""
        conn = _get_connection()
        conn.execute("DELETE FROM summary_buffer_messages WHERE session_id = ?", (session_id,))
        conn.execute("DELETE FROM summary_buffer_summaries WHERE session_id = ?", (session_id,))
        conn.commit()

