"""Neo4j Query tool for AI agents.

Exposes Neo4j as a constrained tool: the LLM picks a query by name and
supplies parameters — it never writes raw Cypher.  The query registry
(a JSON property) defines available queries, and all query names are
injected into the tool description so the LLM can discover them.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, TYPE_CHECKING

from ...base import (
    NodeProperty,
    NodeTypeDescription,
)
from .._config_base import ConfigProvider

if TYPE_CHECKING:
    from ....engine.types import ExecutionContext, NodeDefinition


# ---------------------------------------------------------------------------
# Registry helpers
# ---------------------------------------------------------------------------

def _parse_registry(raw: str) -> dict[str, Any]:
    """Defensively parse the query registry JSON string."""
    if not raw or not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    return parsed


def _build_tool_description(base: str, registry: dict[str, Any]) -> str:
    """Append a formatted listing of available queries to the base description."""
    if not registry:
        return base

    lines = [base, "", "Available queries:"]
    for name, spec in registry.items():
        desc = spec.get("description", "No description")
        lines.append(f"  - {name}: {desc}")
        params = spec.get("parameters", {})
        for pname, pspec in params.items():
            ptype = pspec.get("type", "any")
            required = pspec.get("required", False)
            default = pspec.get("default")
            parts = [f"      param '{pname}': {ptype}"]
            if required:
                parts.append(" (required)")
            elif default is not None:
                parts.append(f", default={default}")
            lines.append("".join(parts))
    return "\n".join(lines)


def _validate_parameters(
    query_name: str,
    provided: dict[str, Any],
    spec: dict[str, Any],
) -> tuple[dict[str, Any], str | None]:
    """Validate and fill defaults for query parameters.

    Returns (resolved_params, error_message | None).
    """
    resolved: dict[str, Any] = {}
    type_map = {
        "string": str,
        "integer": int,
        "number": (int, float),
        "boolean": bool,
    }

    for pname, pspec in spec.items():
        if pname in provided:
            value = provided[pname]
            expected = type_map.get(pspec.get("type", ""))
            if expected and not isinstance(value, expected):
                # Attempt coercion for common cases
                try:
                    if pspec.get("type") == "integer":
                        value = int(value)
                    elif pspec.get("type") == "number":
                        value = float(value)
                    elif pspec.get("type") == "string":
                        value = str(value)
                    elif pspec.get("type") == "boolean":
                        value = bool(value)
                except (ValueError, TypeError):
                    return {}, (
                        f"Parameter '{pname}' for query '{query_name}' "
                        f"must be {pspec.get('type')}, got {type(value).__name__}"
                    )
            resolved[pname] = value
        elif pspec.get("required", False):
            return {}, (
                f"Missing required parameter '{pname}' for query '{query_name}'"
            )
        elif "default" in pspec:
            resolved[pname] = pspec["default"]

    return resolved, None


# ---------------------------------------------------------------------------
# Neo4j record serialization (reused logic from neo4j_node.py)
# ---------------------------------------------------------------------------

def _serialize_value(val: Any) -> Any:
    """Serialize Neo4j-specific types to JSON-safe values."""
    from ....utils.serialization import serialize_value as _base_serialize

    if hasattr(val, "__class__") and val.__class__.__name__ == "Node":
        return {
            "_neo4jType": "node",
            "id": val.element_id,
            "labels": list(val.labels),
            "properties": {k: _serialize_value(v) for k, v in dict(val).items()},
        }
    if hasattr(val, "__class__") and val.__class__.__name__ == "Relationship":
        return {
            "_neo4jType": "relationship",
            "id": val.element_id,
            "type": val.type,
            "startNodeId": val.start_node.element_id if val.start_node else None,
            "endNodeId": val.end_node.element_id if val.end_node else None,
            "properties": {k: _serialize_value(v) for k, v in dict(val).items()},
        }
    if hasattr(val, "__class__") and val.__class__.__name__ == "Path":
        return {
            "_neo4jType": "path",
            "nodes": [_serialize_value(n) for n in val.nodes],
            "relationships": [_serialize_value(r) for r in val.relationships],
        }
    if isinstance(val, list):
        return [_serialize_value(v) for v in val]
    if isinstance(val, dict):
        return {k: _serialize_value(v) for k, v in val.items()}
    return _base_serialize(val)


def _process_records(records_raw: list, keys: list, limit: int) -> tuple[list, bool]:
    """Convert raw neo4j record values into serialized dicts/values.

    Returns (records, truncated).
    """
    truncated = len(records_raw) > limit
    capped = records_raw[:limit]
    records = []
    for record_values in capped:
        if len(keys) == 1:
            records.append(_serialize_value(record_values[0]))
        else:
            records.append({
                keys[i]: _serialize_value(v) for i, v in enumerate(record_values)
            })
    return records, truncated


# ---------------------------------------------------------------------------
# Executor factory
# ---------------------------------------------------------------------------

def _make_neo4j_executor(
    uri: str,
    user: str,
    password: str,
    database: str,
    registry: dict[str, Any],
    result_limit: int,
    query_timeout: int,
):
    """Create an async executor closure capturing connection + registry."""

    async def execute(
        input_data: dict[str, Any], context: ExecutionContext
    ) -> dict[str, Any]:
        from neo4j import AsyncGraphDatabase

        query_name = input_data.get("query_name", "")
        provided_params = input_data.get("parameters") or {}

        # Validate query_name exists
        if query_name not in registry:
            available = ", ".join(registry.keys()) if registry else "(none)"
            return {
                "error": (
                    f"Unknown query '{query_name}'. "
                    f"Available queries: {available}"
                ),
            }

        query_spec = registry[query_name]
        cypher = query_spec.get("query", "")
        param_spec = query_spec.get("parameters", {})

        # Validate parameters
        resolved_params, error = _validate_parameters(
            query_name, provided_params, param_spec
        )
        if error:
            return {"error": error}

        # Execute against Neo4j
        driver = AsyncGraphDatabase.driver(uri, auth=(user, password))
        try:

            async def _run_query():
                async with driver.session(
                    database=database, default_access_mode="READ"
                ) as session:

                    async def _tx_func(tx):
                        result = await tx.run(cypher, resolved_params)
                        records_raw = await result.values()
                        keys = result.keys()
                        await result.consume()
                        return records_raw, keys

                    return await session.execute_read(_tx_func)

            try:
                records_raw, keys = await asyncio.wait_for(
                    _run_query(), timeout=query_timeout
                )
            except asyncio.TimeoutError:
                return {
                    "error": (
                        f"Query '{query_name}' timed out after {query_timeout}s"
                    ),
                }

            records, truncated = _process_records(
                records_raw, keys, result_limit
            )

            result: dict[str, Any] = {
                "query_name": query_name,
                "records": records,
                "record_count": len(records),
            }
            if truncated:
                result["truncated"] = True
                result["total_available"] = len(records_raw)

            return result

        except Exception as e:
            return {"error": f"Neo4j query error: {e}"}
        finally:
            await driver.close()

    return execute


# ---------------------------------------------------------------------------
# Node class
# ---------------------------------------------------------------------------

DEFAULT_URI = "bolt://localhost:7687"
DEFAULT_USER = "neo4j"
DEFAULT_PASSWORD = "testpassword"
DEFAULT_DATABASE = "neo4j"

EXAMPLE_REGISTRY = json.dumps(
    {
        "find_related": {
            "description": "Find entities related to a given entity",
            "query": "MATCH (n {id: $entity_id})-[:RELATES_TO*1..$depth]->(m) RETURN m LIMIT $limit",
            "parameters": {
                "entity_id": {"type": "string", "required": True},
                "depth": {"type": "integer", "default": 2},
                "limit": {"type": "integer", "default": 25},
            },
        }
    },
    indent=2,
)


class Neo4jQueryToolNode(ConfigProvider):
    """Neo4j Query tool - run constrained graph queries as an agent tool.

    The LLM picks a query by name from a pre-defined registry and supplies
    parameters.  It never writes raw Cypher.  All queries execute in
    read-only mode.
    """

    node_description = NodeTypeDescription(
        name="Neo4jQueryTool",
        display_name="Neo4j Query Tool",
        description="Run constrained Neo4j graph queries as an agent tool",
        icon="fa:project-diagram",
        group=["ai"],
        inputs=[],
        outputs=[],
        properties=[
            # --- Connection ---
            NodeProperty(
                display_name="URI",
                name="uri",
                type="string",
                default=DEFAULT_URI,
                required=True,
                description="Neo4j connection URI",
                placeholder="bolt://localhost:7687",
            ),
            NodeProperty(
                display_name="Username",
                name="username",
                type="string",
                default=DEFAULT_USER,
                description="Neo4j username",
            ),
            NodeProperty(
                display_name="Password",
                name="password",
                type="string",
                default=DEFAULT_PASSWORD,
                description="Neo4j password",
            ),
            NodeProperty(
                display_name="Database",
                name="database",
                type="string",
                default=DEFAULT_DATABASE,
                description="Neo4j database name",
            ),
            # --- Tool identity ---
            NodeProperty(
                display_name="Tool Name",
                name="toolName",
                type="string",
                default="neo4j_query",
                description="Name the LLM will use to call this tool",
            ),
            NodeProperty(
                display_name="Description",
                name="description",
                type="string",
                default="Run a named Neo4j graph query. Available queries are listed below.",
                description="Base description shown to the AI model",
                type_options={"rows": 3},
            ),
            # --- Query Registry ---
            NodeProperty(
                display_name="Query Registry",
                name="queryRegistry",
                type="json",
                default=EXAMPLE_REGISTRY,
                description="JSON object mapping query names to their Cypher template, description, and parameter specs",
                type_options={"language": "json", "rows": 15},
            ),
            # --- Safety ---
            NodeProperty(
                display_name="Result Limit",
                name="resultLimit",
                type="number",
                default=100,
                description="Maximum number of rows returned per query",
            ),
            NodeProperty(
                display_name="Query Timeout (s)",
                name="queryTimeout",
                type="number",
                default=30,
                description="Maximum seconds a query may run before being cancelled",
            ),
        ],
    )

    def get_config(self, node_definition: NodeDefinition) -> dict[str, Any]:
        """Return Neo4j query tool configuration."""
        uri = self.get_parameter(node_definition, "uri", DEFAULT_URI)
        user = self.get_parameter(node_definition, "username", DEFAULT_USER)
        password = self.get_parameter(node_definition, "password", DEFAULT_PASSWORD)
        database = self.get_parameter(node_definition, "database", DEFAULT_DATABASE)
        tool_name = self.get_parameter(node_definition, "toolName", "neo4j_query")
        base_description = self.get_parameter(
            node_definition,
            "description",
            "Run a named Neo4j graph query. Available queries are listed below.",
        )
        registry_raw = self.get_parameter(node_definition, "queryRegistry", "{}")
        result_limit = int(self.get_parameter(node_definition, "resultLimit", 100))
        query_timeout = int(self.get_parameter(node_definition, "queryTimeout", 30))

        registry = _parse_registry(
            registry_raw if isinstance(registry_raw, str) else json.dumps(registry_raw)
        )
        description = _build_tool_description(base_description, registry)

        # Build input schema with enum constraint on query_name
        query_name_schema: dict[str, Any] = {
            "type": "string",
            "description": "Name of the query to execute",
        }
        if registry:
            query_name_schema["enum"] = list(registry.keys())

        return {
            "name": tool_name,
            "description": description,
            "input_schema": {
                "type": "object",
                "properties": {
                    "query_name": query_name_schema,
                    "parameters": {
                        "type": "object",
                        "description": "Parameters for the selected query",
                    },
                },
                "required": ["query_name"],
            },
            "execute": _make_neo4j_executor(
                uri, user, password, database,
                registry, result_limit, query_timeout,
            ),
        }
