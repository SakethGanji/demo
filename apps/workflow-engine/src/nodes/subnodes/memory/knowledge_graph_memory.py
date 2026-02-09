"""Knowledge Graph Memory - store entity relationships using Neo4j."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from ...base import (
    NodeProperty,
    NodeTypeDescription,
)
from .base_memory import MemorySubnodeBase, SESSION_ID_PROPERTY
from ....utils.memory import (
    extract_relationships,
    get_db_connection,
    run_async,
    sanitize_cypher_label,
)

if TYPE_CHECKING:
    from ....engine.types import NodeDefinition


_INIT_SQL = [
    """
    CREATE TABLE IF NOT EXISTS kg_memory_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        role TEXT NOT NULL,
        content TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_kg_msg_session ON kg_memory_messages(session_id)",
]


def _get_connection():
    return get_db_connection("kg_conn", _INIT_SQL)


class KnowledgeGraphMemoryNode(MemorySubnodeBase):
    """Knowledge Graph Memory - stores entity relationships in Neo4j."""

    node_description = NodeTypeDescription(
        name="KnowledgeGraphMemory",
        display_name="Knowledge Graph Memory",
        description="Store entity relationships using Neo4j - builds a knowledge graph from conversation",
        icon="fa:project-diagram",
        group=["ai"],
        inputs=[],
        outputs=[],
        properties=[
            SESSION_ID_PROPERTY,
            NodeProperty(
                display_name="Connection URI",
                name="connectionString",
                type="string",
                default="bolt://localhost:7687",
                description="Neo4j connection URI",
            ),
            NodeProperty(
                display_name="Username",
                name="username",
                type="string",
                default="neo4j",
                description="Neo4j username",
            ),
            NodeProperty(
                display_name="Password",
                name="password",
                type="string",
                default="",
                description="Neo4j password",
            ),
            NodeProperty(
                display_name="Extraction Model",
                name="extractionModel",
                type="string",
                default="gemini-2.0-flash",
                description="LLM model for relationship extraction",
            ),
            NodeProperty(
                display_name="Max Relationships",
                name="maxRelationships",
                type="number",
                default=20,
                description="Maximum relationships to include in context",
            ),
            NodeProperty(
                display_name="Recent Messages",
                name="recentMessages",
                type="number",
                default=3,
                description="Number of recent messages to include alongside graph context",
            ),
        ],
        is_subnode=True,
        subnode_type="memory",
        provides_to_slot="memory",
    )

    def get_config(self, node_definition: NodeDefinition) -> dict[str, Any]:
        """Return memory configuration with accessor functions."""
        session_id = self.get_parameter(node_definition, "sessionId", "default")
        connection_string = self.get_parameter(node_definition, "connectionString", "bolt://localhost:7687")
        username = self.get_parameter(node_definition, "username", "neo4j")
        password = self.get_parameter(node_definition, "password", "")
        extraction_model = self.get_parameter(node_definition, "extractionModel", "gemini-2.0-flash")
        max_relationships = self.get_parameter(node_definition, "maxRelationships", 20)
        recent_messages = self.get_parameter(node_definition, "recentMessages", 3)

        return self.build_memory_config(
            memory_type="knowledge_graph",
            session_id=session_id,
            get_history=lambda: self._get_history(
                session_id, connection_string, username, password,
                max_relationships, recent_messages
            ),
            add_message=lambda role, content: self._add_message(
                session_id, role, content, connection_string, username, password,
                extraction_model
            ),
            clear_history=lambda: self._clear_history(
                session_id, connection_string, username, password
            ),
            connectionString=connection_string,
            username=username,
            password=password,
            extractionModel=extraction_model,
            maxRelationships=max_relationships,
            recentMessages=recent_messages,
        )

    @staticmethod
    async def _get_relationships_from_neo4j(
        session_id: str,
        connection_string: str,
        username: str,
        password: str,
        max_relationships: int,
    ) -> list[dict[str, str]]:
        """Query Neo4j for relationships in this session."""
        try:
            from neo4j import AsyncGraphDatabase

            driver = AsyncGraphDatabase.driver(
                connection_string,
                auth=(username, password),
            )

            try:
                async with driver.session() as session:
                    result = await session.run(
                        """
                        MATCH (a:Entity {session_id: $session_id})-[r]->(b:Entity {session_id: $session_id})
                        RETURN a.name AS subject, type(r) AS predicate, b.name AS object
                        ORDER BY r.created_at DESC
                        LIMIT $limit
                        """,
                        {"session_id": session_id, "limit": max_relationships},
                    )
                    records = await result.data()
                    return records
            finally:
                await driver.close()
        except Exception:
            return []

    @staticmethod
    async def _store_relationships_in_neo4j(
        session_id: str,
        relationships: list[tuple[str, str, str]],
        connection_string: str,
        username: str,
        password: str,
    ) -> None:
        """Store extracted relationships in Neo4j."""
        if not relationships:
            return

        try:
            from neo4j import AsyncGraphDatabase

            driver = AsyncGraphDatabase.driver(
                connection_string,
                auth=(username, password),
            )

            try:
                async with driver.session() as session:
                    for subject, predicate, obj in relationships:
                        rel_type = sanitize_cypher_label(predicate)

                        # Create or merge entities and relationship
                        await session.run(
                            f"""
                            MERGE (a:Entity {{name: $subject, session_id: $session_id}})
                            MERGE (b:Entity {{name: $object, session_id: $session_id}})
                            MERGE (a)-[r:{rel_type} {{session_id: $session_id}}]->(b)
                            ON CREATE SET r.created_at = datetime()
                            """,
                            {
                                "subject": subject,
                                "object": obj,
                                "session_id": session_id,
                            },
                        )
            finally:
                await driver.close()
        except Exception:
            # Silently fail if Neo4j is not available
            pass

    @staticmethod
    def _get_history(
        session_id: str,
        connection_string: str,
        username: str,
        password: str,
        max_relationships: int,
        recent_messages: int,
    ) -> list[dict[str, str]]:
        """Get history: [knowledge graph context] + [recent messages]."""
        conn = _get_connection()

        result = []

        # Get relationships from Neo4j
        try:
            relationships = run_async(
                KnowledgeGraphMemoryNode._get_relationships_from_neo4j(
                    session_id, connection_string, username, password, max_relationships
                )
            )
        except Exception:
            relationships = []

        # Format knowledge graph context
        if relationships:
            kg_lines = ["Knowledge Graph Context:"]
            for rel in relationships:
                subject = rel.get("subject", "?")
                predicate = rel.get("predicate", "?").lower().replace("_", " ")
                obj = rel.get("object", "?")
                kg_lines.append(f"- {subject} {predicate} {obj}")
            kg_context = "\n".join(kg_lines)
            result.append({
                "role": "system",
                "content": kg_context,
            })

        # Get recent messages from SQLite
        recent_rows = conn.execute(
            """
            SELECT role, content FROM kg_memory_messages
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
        connection_string: str,
        username: str,
        password: str,
        extraction_model: str,
    ) -> None:
        """Add message and extract relationships to Neo4j."""
        conn = _get_connection()

        # Store message in SQLite
        conn.execute(
            "INSERT INTO kg_memory_messages (session_id, role, content) VALUES (?, ?, ?)",
            (session_id, role, content),
        )
        conn.commit()

        # Extract relationships from the message
        try:
            relationships = run_async(extract_relationships(content, extraction_model))
        except Exception:
            relationships = []

        # Store relationships in Neo4j
        if relationships:
            try:
                run_async(
                    KnowledgeGraphMemoryNode._store_relationships_in_neo4j(
                        session_id, relationships, connection_string, username, password
                    )
                )
            except Exception:
                pass  # Silently fail if Neo4j is not available

    @staticmethod
    def _clear_history(
        session_id: str,
        connection_string: str,
        username: str,
        password: str,
    ) -> None:
        """Clear messages and graph data for session."""
        conn = _get_connection()

        # Clear SQLite messages
        conn.execute("DELETE FROM kg_memory_messages WHERE session_id = ?", (session_id,))
        conn.commit()

        # Clear Neo4j data
        async def clear_neo4j():
            try:
                from neo4j import AsyncGraphDatabase

                driver = AsyncGraphDatabase.driver(
                    connection_string,
                    auth=(username, password),
                )

                try:
                    async with driver.session() as session:
                        # Delete all relationships and nodes for this session
                        await session.run(
                            """
                            MATCH (n:Entity {session_id: $session_id})
                            DETACH DELETE n
                            """,
                            {"session_id": session_id},
                        )
                finally:
                    await driver.close()
            except Exception:
                pass

        try:
            run_async(clear_neo4j())
        except Exception:
            pass

