"""Entity Memory - extract and track entities mentioned in conversation."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from ...base import (
    NodeProperty,
    NodeTypeDescription,
)
from .base_memory import MemorySubnodeBase, SESSION_ID_PROPERTY
from ....utils.memory import extract_entities, get_db_connection, run_async

if TYPE_CHECKING:
    from ....engine.types import NodeDefinition


_INIT_SQL = [
    """
    CREATE TABLE IF NOT EXISTS entity_memory_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        role TEXT NOT NULL,
        content TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS entity_memory_entities (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        name TEXT NOT NULL,
        type TEXT NOT NULL,
        description TEXT,
        first_mentioned TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(session_id, name)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_entity_msg_session ON entity_memory_messages(session_id)",
    "CREATE INDEX IF NOT EXISTS idx_entity_session ON entity_memory_entities(session_id)",
]


def _get_connection():
    return get_db_connection("entity_conn", _INIT_SQL)


class EntityMemoryNode(MemorySubnodeBase):
    """Entity Memory - extracts and tracks entities mentioned in conversation."""

    node_description = NodeTypeDescription(
        name="EntityMemory",
        display_name="Entity Memory",
        description="Extract and track entities (people, places, facts) mentioned in conversation",
        icon="fa:user-tag",
        group=["ai"],
        inputs=[],
        outputs=[],
        properties=[
            SESSION_ID_PROPERTY,
            NodeProperty(
                display_name="Extraction Model",
                name="extractionModel",
                type="string",
                default="gemini-2.0-flash",
                description="LLM model for entity extraction",
            ),
            NodeProperty(
                display_name="Entity Types",
                name="entityTypes",
                type="string",
                default="person,place,organization,concept,fact",
                description="Comma-separated list of entity types to track",
            ),
            NodeProperty(
                display_name="Max Entities",
                name="maxEntities",
                type="number",
                default=50,
                description="Maximum number of entities to maintain per session",
            ),
            NodeProperty(
                display_name="Recent Messages",
                name="recentMessages",
                type="number",
                default=5,
                description="Number of recent messages to include alongside entity summary",
            ),
        ],
        is_subnode=True,
        subnode_type="memory",
        provides_to_slot="memory",
    )

    def get_config(self, node_definition: NodeDefinition) -> dict[str, Any]:
        """Return memory configuration with accessor functions."""
        session_id = self.get_parameter(node_definition, "sessionId", "default")
        extraction_model = self.get_parameter(node_definition, "extractionModel", "gemini-2.0-flash")
        entity_types_str = self.get_parameter(node_definition, "entityTypes", "person,place,organization,concept,fact")
        entity_types = [t.strip() for t in entity_types_str.split(",")]
        max_entities = self.get_parameter(node_definition, "maxEntities", 50)
        recent_messages = self.get_parameter(node_definition, "recentMessages", 5)

        return self.build_memory_config(
            memory_type="entity",
            session_id=session_id,
            get_history=lambda: self._get_history(session_id, recent_messages),
            add_message=lambda role, content: self._add_message(
                session_id, role, content, extraction_model, entity_types, max_entities
            ),
            clear_history=lambda: self._clear_history(session_id),
            extractionModel=extraction_model,
            entityTypes=entity_types,
            maxEntities=max_entities,
            recentMessages=recent_messages,
        )

    @staticmethod
    def _get_entities(session_id: str) -> list[dict[str, Any]]:
        """Get all entities for a session."""
        conn = _get_connection()
        rows = conn.execute(
            """
            SELECT name, type, description, first_mentioned, last_updated
            FROM entity_memory_entities
            WHERE session_id = ?
            ORDER BY last_updated DESC
            """,
            (session_id,),
        ).fetchall()

        return [
            {
                "name": r[0],
                "type": r[1],
                "description": r[2],
                "first_mentioned": r[3],
                "last_updated": r[4],
            }
            for r in rows
        ]

    @staticmethod
    def _get_history(session_id: str, recent_messages: int) -> list[dict[str, str]]:
        """Get history: [entity summary] + [recent messages]."""
        conn = _get_connection()

        result = []

        # Get entities and format as context
        entities = EntityMemoryNode._get_entities(session_id)
        if entities:
            entity_lines = ["Known entities:"]
            for entity in entities:
                desc = entity["description"] or "No description"
                entity_lines.append(f"- {entity['name']} ({entity['type']}): {desc}")
            entity_context = "\n".join(entity_lines)
            result.append({
                "role": "system",
                "content": entity_context,
            })

        # Get recent messages
        recent_rows = conn.execute(
            """
            SELECT role, content FROM entity_memory_messages
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
        extraction_model: str,
        entity_types: list[str],
        max_entities: int,
    ) -> None:
        """Add message and extract entities."""
        conn = _get_connection()

        # Store message
        conn.execute(
            "INSERT INTO entity_memory_messages (session_id, role, content) VALUES (?, ?, ?)",
            (session_id, role, content),
        )
        conn.commit()

        # Extract entities from the message
        try:
            extracted = run_async(extract_entities(content, extraction_model, entity_types))
        except Exception:
            extracted = []

        # Upsert entities
        for entity in extracted:
            name = entity.get("name", "").strip()
            etype = entity.get("type", "unknown")
            description = entity.get("description", "")

            if not name:
                continue

            conn.execute(
                """
                INSERT INTO entity_memory_entities (session_id, name, type, description)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(session_id, name) DO UPDATE SET
                    type = excluded.type,
                    description = CASE
                        WHEN excluded.description != '' THEN excluded.description
                        ELSE entity_memory_entities.description
                    END,
                    last_updated = CURRENT_TIMESTAMP
                """,
                (session_id, name, etype, description),
            )

        # Enforce max entities limit (keep most recently updated)
        conn.execute(
            """
            DELETE FROM entity_memory_entities
            WHERE session_id = ? AND id NOT IN (
                SELECT id FROM entity_memory_entities
                WHERE session_id = ?
                ORDER BY last_updated DESC
                LIMIT ?
            )
            """,
            (session_id, session_id, max_entities),
        )

        conn.commit()

    @staticmethod
    def _clear_history(session_id: str) -> None:
        """Clear messages and entities for session."""
        conn = _get_connection()
        conn.execute("DELETE FROM entity_memory_messages WHERE session_id = ?", (session_id,))
        conn.execute("DELETE FROM entity_memory_entities WHERE session_id = ?", (session_id,))
        conn.commit()

