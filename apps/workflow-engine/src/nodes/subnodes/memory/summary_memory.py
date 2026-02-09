"""Summary Memory - LLM summarizes old messages, keeps summary + recent."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from ...base import (
    NodeProperty,
    NodeTypeDescription,
)
from ....utils.memory import call_llm_for_summary, get_db_connection, run_async
from .base_memory import MemorySubnodeBase, SESSION_ID_PROPERTY

if TYPE_CHECKING:
    from ....engine.types import NodeDefinition


_INIT_SQL = [
    """
    CREATE TABLE IF NOT EXISTS summary_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        role TEXT NOT NULL,
        content TEXT NOT NULL,
        is_summary INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_summary_session ON summary_messages(session_id)",
]


def _get_connection():
    return get_db_connection("summary_conn", _INIT_SQL)


class SummaryMemoryNode(MemorySubnodeBase):
    """Summary Memory - LLM summarizes old messages when threshold reached."""

    node_description = NodeTypeDescription(
        name="SummaryMemory",
        display_name="Summary Memory",
        description="LLM summarizes older messages, keeping summary + recent messages",
        icon="fa:compress-alt",
        group=["ai"],
        inputs=[],
        outputs=[],
        properties=[
            SESSION_ID_PROPERTY,
            NodeProperty(
                display_name="Recent Messages",
                name="recentMessages",
                type="number",
                default=5,
                description="Number of recent messages to keep unsummarized",
            ),
            NodeProperty(
                display_name="Summary Threshold",
                name="summaryThreshold",
                type="number",
                default=15,
                description="Trigger summarization when message count exceeds this",
            ),
            NodeProperty(
                display_name="Summary Model",
                name="summaryModel",
                type="string",
                default="gemini-2.0-flash",
                description="LLM model to use for summarization",
            ),
        ],
        is_subnode=True,
        subnode_type="memory",
        provides_to_slot="memory",
    )

    def get_config(self, node_definition: NodeDefinition) -> dict[str, Any]:
        """Return memory configuration with accessor functions."""
        session_id = self.get_parameter(node_definition, "sessionId", "default")
        recent_messages = self.get_parameter(node_definition, "recentMessages", 5)
        summary_threshold = self.get_parameter(node_definition, "summaryThreshold", 15)
        summary_model = self.get_parameter(node_definition, "summaryModel", "gemini-2.0-flash")

        return self.build_memory_config(
            memory_type="summary",
            session_id=session_id,
            get_history=lambda: self._get_history(session_id, recent_messages),
            add_message=lambda role, content: self._add_message(
                session_id, role, content, recent_messages, summary_threshold, summary_model
            ),
            clear_history=lambda: self._clear_history(session_id),
            recentMessages=recent_messages,
            summaryThreshold=summary_threshold,
            summaryModel=summary_model,
        )

    @staticmethod
    def _get_history(session_id: str, recent_messages: int) -> list[dict[str, str]]:
        """Get history: [summary if exists] + [recent messages]."""
        conn = _get_connection()

        result = []

        # Get any existing summary
        summary_row = conn.execute(
            "SELECT content FROM summary_messages WHERE session_id = ? AND is_summary = 1 ORDER BY id DESC LIMIT 1",
            (session_id,),
        ).fetchone()

        if summary_row:
            result.append({
                "role": "system",
                "content": f"[Conversation Summary]\n{summary_row[0]}",
            })

        # Get recent non-summary messages
        recent_rows = conn.execute(
            """
            SELECT role, content FROM summary_messages
            WHERE session_id = ? AND is_summary = 0
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
        summary_threshold: int,
        summary_model: str,
    ) -> None:
        """Add message and trigger summarization if threshold reached."""
        conn = _get_connection()

        # Insert new message
        conn.execute(
            "INSERT INTO summary_messages (session_id, role, content, is_summary) VALUES (?, ?, ?, 0)",
            (session_id, role, content),
        )
        conn.commit()

        # Count non-summary messages
        count_row = conn.execute(
            "SELECT COUNT(*) FROM summary_messages WHERE session_id = ? AND is_summary = 0",
            (session_id,),
        ).fetchone()
        message_count = count_row[0] if count_row else 0

        # Trigger summarization if over threshold
        if message_count > summary_threshold:
            SummaryMemoryNode._perform_summarization(
                session_id, recent_messages, summary_model
            )

    @staticmethod
    def _perform_summarization(session_id: str, recent_messages: int, summary_model: str) -> None:
        """Summarize older messages and replace with summary."""
        conn = _get_connection()

        # Get all non-summary messages
        all_rows = conn.execute(
            """
            SELECT id, role, content FROM summary_messages
            WHERE session_id = ? AND is_summary = 0
            ORDER BY id ASC
            """,
            (session_id,),
        ).fetchall()

        if len(all_rows) <= recent_messages:
            return  # Nothing to summarize

        # Split into messages to summarize and recent to keep
        to_summarize = all_rows[:-recent_messages]
        messages_to_summarize = [{"role": r[1], "content": r[2]} for r in to_summarize]

        # Get existing summary (if any)
        existing_summary_row = conn.execute(
            "SELECT content FROM summary_messages WHERE session_id = ? AND is_summary = 1 ORDER BY id DESC LIMIT 1",
            (session_id,),
        ).fetchone()
        existing_summary = existing_summary_row[0] if existing_summary_row else None

        # If there's an existing summary, include it in the context
        if existing_summary:
            # Prepend the existing summary as context
            context_message = {"role": "system", "content": f"[Previous Summary]\n{existing_summary}"}
            messages_to_summarize = [context_message] + messages_to_summarize

        # Call LLM for summary
        try:
            summary = run_async(call_llm_for_summary(messages_to_summarize, summary_model))
        except Exception:
            # If summarization fails, just trim old messages without summary
            summary = None

        # Delete old messages (those we summarized)
        ids_to_delete = [r[0] for r in to_summarize]
        if ids_to_delete:
            placeholders = ",".join("?" * len(ids_to_delete))
            conn.execute(
                f"DELETE FROM summary_messages WHERE id IN ({placeholders})",
                ids_to_delete,
            )

        # Delete old summary
        conn.execute(
            "DELETE FROM summary_messages WHERE session_id = ? AND is_summary = 1",
            (session_id,),
        )

        # Insert new summary
        if summary:
            conn.execute(
                "INSERT INTO summary_messages (session_id, role, content, is_summary) VALUES (?, 'system', ?, 1)",
                (session_id, summary),
            )

        conn.commit()

    @staticmethod
    def _clear_history(session_id: str) -> None:
        """Clear history for session."""
        conn = _get_connection()
        conn.execute("DELETE FROM summary_messages WHERE session_id = ?", (session_id,))
        conn.commit()

