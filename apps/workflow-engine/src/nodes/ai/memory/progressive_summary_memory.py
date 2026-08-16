"""Progressive Summary Memory - rolling summary that updates every turn."""

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
    CREATE TABLE IF NOT EXISTS progressive_summary_buffer (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        role TEXT NOT NULL,
        content TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS progressive_summaries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL UNIQUE,
        summary TEXT NOT NULL,
        message_count INTEGER DEFAULT 0,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_prog_sum_session ON progressive_summary_buffer(session_id)",
]


def _get_connection():
    return get_db_connection("progressive_summary_conn", _INIT_SQL)


class ProgressiveSummaryMemoryNode(MemoryProviderBase):
    """Progressive Summary Memory - rolling summary that evolves with each conversation."""

    node_description = NodeTypeDescription(
        name="ProgressiveSummaryMemory",
        display_name="Progressive Summary Memory",
        description="Rolling summary that updates every N messages - most aggressive compression",
        icon="fa:stream",
        group=["ai"],
        inputs=[],
        outputs=[],
        properties=[
            SESSION_ID_PROPERTY,
            NodeProperty(
                display_name="Summary Model",
                name="summaryModel",
                type="string",
                default="gemini-2.0-flash",
                description="LLM model to use for summarization",
            ),
            NodeProperty(
                display_name="Max Summary Tokens",
                name="maxSummaryTokens",
                type="number",
                default=1000,
                description="Maximum tokens for the rolling summary",
            ),
            NodeProperty(
                display_name="Update Frequency",
                name="updateFrequency",
                type="number",
                default=2,
                description="Update summary every N messages",
            ),
        ],
    )

    def get_config(self, node_definition: NodeDefinition) -> dict[str, Any]:
        """Return memory configuration with accessor functions."""
        session_id = self.get_parameter(node_definition, "sessionId", "default")
        summary_model = self.get_parameter(node_definition, "summaryModel", "gemini-2.0-flash")
        max_summary_tokens = self.get_parameter(node_definition, "maxSummaryTokens", 1000)
        update_frequency = self.get_parameter(node_definition, "updateFrequency", 2)

        return self.build_memory_config(
            memory_type="progressive_summary",
            session_id=session_id,
            get_history=lambda: self._get_history(session_id),
            add_message=lambda role, content: self._add_message(
                session_id, role, content, summary_model, max_summary_tokens, update_frequency
            ),
            clear_history=lambda: self._clear_history(session_id),
            summaryModel=summary_model,
            maxSummaryTokens=max_summary_tokens,
            updateFrequency=update_frequency,
        )

    @staticmethod
    def _get_summary(session_id: str) -> tuple[str | None, int]:
        """Get the current summary and message count."""
        conn = _get_connection()
        row = conn.execute(
            "SELECT summary, message_count FROM progressive_summaries WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        return (row[0], row[1]) if row else (None, 0)

    @staticmethod
    def _get_history(session_id: str) -> list[dict[str, str]]:
        """Get history: [rolling summary] + [buffered messages]."""
        conn = _get_connection()

        result = []

        # Get current summary
        summary, _ = ProgressiveSummaryMemoryNode._get_summary(session_id)
        if summary:
            result.append({
                "role": "system",
                "content": f"[Conversation Summary]\n{summary}",
            })

        # Get buffered messages (not yet incorporated into summary)
        buffer_rows = conn.execute(
            """
            SELECT role, content FROM progressive_summary_buffer
            WHERE session_id = ?
            ORDER BY id ASC
            """,
            (session_id,),
        ).fetchall()

        for role, content in buffer_rows:
            result.append({"role": role, "content": content})

        return result

    @staticmethod
    def _add_message(
        session_id: str,
        role: str,
        content: str,
        summary_model: str,
        max_summary_tokens: int,
        update_frequency: int,
    ) -> None:
        """Add message to buffer and update summary if frequency reached."""
        conn = _get_connection()

        # Add to buffer
        conn.execute(
            "INSERT INTO progressive_summary_buffer (session_id, role, content) VALUES (?, ?, ?)",
            (session_id, role, content),
        )
        conn.commit()

        # Count buffered messages
        count_row = conn.execute(
            "SELECT COUNT(*) FROM progressive_summary_buffer WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        buffer_count = count_row[0] if count_row else 0

        # Update summary if we've reached the frequency
        if buffer_count >= update_frequency:
            ProgressiveSummaryMemoryNode._update_progressive_summary(
                session_id, summary_model, max_summary_tokens
            )

    @staticmethod
    def _update_progressive_summary(
        session_id: str,
        summary_model: str,
        max_summary_tokens: int,
    ) -> None:
        """Incorporate buffered messages into the rolling summary."""
        conn = _get_connection()

        # Get current summary
        current_summary, message_count = ProgressiveSummaryMemoryNode._get_summary(session_id)

        # Get buffered messages
        buffer_rows = conn.execute(
            """
            SELECT id, role, content FROM progressive_summary_buffer
            WHERE session_id = ?
            ORDER BY id ASC
            """,
            (session_id,),
        ).fetchall()

        if not buffer_rows:
            return

        new_messages = [{"role": r[1], "content": r[2]} for r in buffer_rows]
        message_ids = [r[0] for r in buffer_rows]

        # Generate updated summary
        try:
            new_summary = run_async(
                call_llm_for_summary(
                    new_messages,
                    summary_model,
                    max_tokens=max_summary_tokens,
                    previous_summary=current_summary,
                )
            )
        except Exception:
            # If summarization fails, keep messages in buffer
            return

        if not new_summary:
            return

        # Clear buffer
        placeholders = ",".join("?" * len(message_ids))
        conn.execute(
            f"DELETE FROM progressive_summary_buffer WHERE id IN ({placeholders})",
            message_ids,
        )

        # Upsert summary
        new_message_count = message_count + len(new_messages)
        conn.execute(
            """
            INSERT INTO progressive_summaries (session_id, summary, message_count)
            VALUES (?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                summary = excluded.summary,
                message_count = excluded.message_count,
                updated_at = CURRENT_TIMESTAMP
            """,
            (session_id, new_summary, new_message_count),
        )

        conn.commit()

    @staticmethod
    def _clear_history(session_id: str) -> None:
        """Clear all data for session."""
        conn = _get_connection()
        conn.execute("DELETE FROM progressive_summary_buffer WHERE session_id = ?", (session_id,))
        conn.execute("DELETE FROM progressive_summaries WHERE session_id = ?", (session_id,))
        conn.commit()

