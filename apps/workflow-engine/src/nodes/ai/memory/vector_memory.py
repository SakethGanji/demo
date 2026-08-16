"""Vector Memory - semantic search over conversation history using embeddings."""

from __future__ import annotations

import json
from typing import Any, TYPE_CHECKING

from ...base import (
    NodeProperty,
    NodePropertyOption,
    NodeTypeDescription,
)
from .base_memory import MemoryProviderBase, SESSION_ID_PROPERTY
from ....utils.memory import get_embedding, cosine_similarity, get_db_connection, run_async

if TYPE_CHECKING:
    from ....engine.types import NodeDefinition


_INIT_SQL = [
    """
    CREATE TABLE IF NOT EXISTS vector_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        role TEXT NOT NULL,
        content TEXT NOT NULL,
        embedding TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_vector_session ON vector_messages(session_id)",
]


def _get_connection():
    return get_db_connection("vector_conn", _INIT_SQL)


class VectorMemoryNode(MemoryProviderBase):
    """Vector Memory - semantic search over conversation history using embeddings."""

    node_description = NodeTypeDescription(
        name="VectorMemory",
        display_name="Vector Memory",
        description="Semantic search over conversation history - retrieves contextually relevant messages",
        icon="fa:vector-square",
        group=["ai"],
        inputs=[],
        outputs=[],
        properties=[
            SESSION_ID_PROPERTY,
            NodeProperty(
                display_name="Top K",
                name="topK",
                type="number",
                default=5,
                description="Number of most relevant messages to retrieve",
            ),
            NodeProperty(
                display_name="Embedding Provider",
                name="embeddingProvider",
                type="options",
                default="openai",
                options=[
                    NodePropertyOption(
                        name="OpenAI",
                        value="openai",
                        description="Use OpenAI embeddings API",
                    ),
                    NodePropertyOption(
                        name="Gemini",
                        value="gemini",
                        description="Use Google Gemini embeddings",
                    ),
                ],
            ),
            NodeProperty(
                display_name="Embedding Model",
                name="embeddingModel",
                type="string",
                default="text-embedding-3-small",
                description="Embedding model ID (e.g., text-embedding-3-small, text-embedding-004)",
            ),
            NodeProperty(
                display_name="Always Include Recent",
                name="alwaysIncludeRecent",
                type="number",
                default=2,
                description="Always include the N most recent messages regardless of similarity",
            ),
            NodeProperty(
                display_name="Similarity Threshold",
                name="similarityThreshold",
                type="number",
                default=0.7,
                description="Minimum similarity score (0-1) for a message to be included",
            ),
            NodeProperty(
                display_name="Max Messages",
                name="maxMessages",
                type="number",
                default=500,
                description="Maximum messages to store (oldest are deleted when exceeded)",
            ),
        ],
    )

    def get_config(self, node_definition: NodeDefinition) -> dict[str, Any]:
        """Return memory configuration with accessor functions."""
        session_id = self.get_parameter(node_definition, "sessionId", "default")
        top_k = self.get_parameter(node_definition, "topK", 5)
        embedding_provider = self.get_parameter(node_definition, "embeddingProvider", "openai")
        embedding_model = self.get_parameter(node_definition, "embeddingModel", "text-embedding-3-small")
        always_include_recent = self.get_parameter(node_definition, "alwaysIncludeRecent", 2)
        similarity_threshold = self.get_parameter(node_definition, "similarityThreshold", 0.7)
        max_messages = self.get_parameter(node_definition, "maxMessages", 500)

        return self.build_memory_config(
            memory_type="vector",
            session_id=session_id,
            get_history=lambda query=None: self._get_history(
                session_id, query, top_k, embedding_provider, embedding_model,
                always_include_recent, similarity_threshold
            ),
            add_message=lambda role, content: self._add_message(
                session_id, role, content, embedding_provider, embedding_model, max_messages
            ),
            clear_history=lambda: self._clear_history(session_id),
            topK=top_k,
            embeddingProvider=embedding_provider,
            embeddingModel=embedding_model,
            alwaysIncludeRecent=always_include_recent,
            similarityThreshold=similarity_threshold,
            maxMessages=max_messages,
        )

    @staticmethod
    def _get_history(
        session_id: str,
        query: str | None,
        top_k: int,
        embedding_provider: str,
        embedding_model: str,
        always_include_recent: int,
        similarity_threshold: float,
    ) -> list[dict[str, str]]:
        """Get history based on semantic similarity to query + recent messages."""
        conn = _get_connection()

        # Get all messages with embeddings
        rows = conn.execute(
            """
            SELECT id, role, content, embedding FROM vector_messages
            WHERE session_id = ?
            ORDER BY id ASC
            """,
            (session_id,),
        ).fetchall()

        if not rows:
            return []

        # Always include recent messages
        all_messages = [(r[0], r[1], r[2], r[3]) for r in rows]
        recent_messages = all_messages[-always_include_recent:] if always_include_recent > 0 else []
        recent_ids = {m[0] for m in recent_messages}

        result = []

        # If query provided, do semantic search
        if query and len(all_messages) > always_include_recent:
            # Get query embedding
            try:
                query_embedding = run_async(
                    get_embedding(query, embedding_provider, embedding_model)
                )
            except Exception:
                # If embedding fails, fall back to recent messages only
                return [{"role": m[1], "content": m[2]} for m in recent_messages]

            # Calculate similarity for non-recent messages
            candidates = []
            for msg_id, role, content, embedding_json in all_messages:
                if msg_id in recent_ids:
                    continue  # Skip recent, they're always included
                if not embedding_json:
                    continue  # Skip messages without embeddings

                try:
                    embedding = json.loads(embedding_json)
                    similarity = cosine_similarity(query_embedding, embedding)
                    if similarity >= similarity_threshold:
                        candidates.append((msg_id, role, content, similarity))
                except (json.JSONDecodeError, TypeError):
                    continue

            # Sort by similarity and take top_k
            candidates.sort(key=lambda x: x[3], reverse=True)
            semantic_matches = candidates[:top_k]

            # Combine: semantically matched messages (in original order) + recent
            matched_ids = {m[0] for m in semantic_matches}
            for msg_id, role, content, _ in all_messages:
                if msg_id in matched_ids or msg_id in recent_ids:
                    result.append({"role": role, "content": content})
        else:
            # No query, just return recent
            for _, role, content, _ in recent_messages:
                result.append({"role": role, "content": content})

        return result

    @staticmethod
    def _add_message(
        session_id: str,
        role: str,
        content: str,
        embedding_provider: str,
        embedding_model: str,
        max_messages: int = 500,
    ) -> None:
        """Add message with its embedding."""
        conn = _get_connection()

        # Generate embedding
        embedding_json = None
        try:
            embedding = run_async(get_embedding(content, embedding_provider, embedding_model))
            embedding_json = json.dumps(embedding)
        except Exception:
            # If embedding fails, store message without embedding
            pass

        conn.execute(
            "INSERT INTO vector_messages (session_id, role, content, embedding) VALUES (?, ?, ?, ?)",
            (session_id, role, content, embedding_json),
        )

        # Trim oldest messages beyond limit
        conn.execute(
            """
            DELETE FROM vector_messages
            WHERE session_id = ? AND id NOT IN (
                SELECT id FROM vector_messages WHERE session_id = ? ORDER BY id DESC LIMIT ?
            )
            """,
            (session_id, session_id, max_messages),
        )
        conn.commit()

    @staticmethod
    def _clear_history(session_id: str) -> None:
        """Clear history for session."""
        conn = _get_connection()
        conn.execute("DELETE FROM vector_messages WHERE session_id = ?", (session_id,))
        conn.commit()

