"""Mongo Query tool for AI agents.

Gives an agent constrained read access to a MongoDB instance. The set of
operators, collections, and rows the agent can reach is fixed at workflow
definition time via the tool's node parameters — the LLM cannot widen that
surface area at runtime, only narrow it.

The tool is generic: it does not know about PromptLab (or any other domain).
A workflow injects domain semantics by setting ``mandatory_filter_field``
(e.g. ``experiment_id``) and ``mandatory_filter_value_expression`` (e.g.
``{{ $node['refreshExp'].json.document._id }}``).
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional, TYPE_CHECKING

from motor.motor_asyncio import AsyncIOMotorClient

from ...base import (
    NodeProperty,
    NodeTypeDescription,
)
from .._config_base import ConfigProvider
from ._mongo_query_guard import (
    apply_default_projection,
    clamp_limit,
    validate_aggregate,
    validate_find,
)

if TYPE_CHECKING:
    from ....engine.types import ExecutionContext, NodeDefinition


logger = logging.getLogger(__name__)


_DEFAULT_TOOL_NAME = "mongo_query"
_DEFAULT_DESCRIPTION = (
    "Run a constrained MongoDB query. Two operations are supported: 'find' "
    "(filter + projection + sort + limit) and 'aggregate' (pipeline). Every "
    "query MUST scope to the configured mandatory field; otherwise the query "
    "is rejected. Heavy text fields are projected out by default — request "
    "them explicitly via 'projection' when needed."
)


# ---------------------------------------------------------------------------
# Shared lazy client cache. Keyed by connection string so multiple workflows
# pointed at different deployments don't collide. Motor's AsyncIOMotorClient
# is connection-pooling, so we want exactly one per process per connstr.
# ---------------------------------------------------------------------------

_clients: dict[str, AsyncIOMotorClient] = {}


def _get_client(connection_string: str) -> AsyncIOMotorClient:
    client = _clients.get(connection_string)
    if client is None:
        client = AsyncIOMotorClient(connection_string)
        _clients[connection_string] = client
    return client


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------

try:
    from bson import ObjectId
except ImportError:  # pragma: no cover — motor pulls bson in transitively
    ObjectId = None  # type: ignore[assignment]


def _to_jsonable(value: Any) -> Any:
    """Convert bson types into vanilla JSON-friendly Python objects."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if ObjectId is not None and isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, list):
        return [_to_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {k: _to_jsonable(v) for k, v in value.items()}
    # datetime / Decimal128 / etc.
    try:
        return str(value)
    except Exception:
        return None


def _resolve_value_expression(expr: Any) -> Any:
    """Best-effort fallback resolution for ``mandatory_filter_value_expression``.

    The workflow runner pre-resolves ``$node`` / ``$env`` / etc. in node
    parameters before the AIAgent runs. By the time we read this property,
    it is normally a fully concrete value. If it is still a string with
    ``$env.X`` left over (e.g. someone constructed the tool config outside
    the runner's resolve pass) we honor that minimal fallback so the tool
    can still be exercised from tests.
    """
    if not isinstance(expr, str):
        return expr
    stripped = expr.strip()
    # Last-resort: bare {{ $env.NAME }} pattern.
    if stripped.startswith("{{") and stripped.endswith("}}"):
        inner = stripped[2:-2].strip()
        if inner.startswith("$env."):
            return os.environ.get(inner[len("$env."):], "")
    return expr


# ---------------------------------------------------------------------------
# Node
# ---------------------------------------------------------------------------


class MongoQueryToolNode(ConfigProvider):
    """Tool node giving an AIAgent constrained Mongo read access."""

    node_description = NodeTypeDescription(
        name="MongoQueryTool",
        display_name="Mongo Query Tool",
        description="Run constrained Mongo queries as an agent tool",
        icon="fa:database",
        group=["ai"],
        inputs=[],
        outputs=[],
        properties=[
            NodeProperty(
                display_name="Tool Name",
                name="tool_name",
                type="string",
                default=_DEFAULT_TOOL_NAME,
                description="The function name the LLM sees.",
            ),
            NodeProperty(
                display_name="Tool Description",
                name="tool_description",
                type="string",
                default=_DEFAULT_DESCRIPTION,
                description="Description shown to the LLM. Use this to embed the schema cheat sheet.",
                type_options={"rows": 6},
            ),
            NodeProperty(
                display_name="Connection String",
                name="connection_string",
                type="string",
                default="mongodb://admin:admin@localhost:27017",
                description="MongoDB connection string. Supports {{ $env.X }}.",
            ),
            NodeProperty(
                display_name="Database",
                name="database",
                type="string",
                default="",
                required=True,
                description="Database name.",
            ),
            NodeProperty(
                display_name="Allowed Collections",
                name="allowed_collections",
                type="json",
                default=[],
                description="JSON array of collection names the agent may query.",
            ),
            NodeProperty(
                display_name="Mandatory Filter Field",
                name="mandatory_filter_field",
                type="string",
                default="",
                required=True,
                description="Field that must appear in every filter (e.g. experiment_id).",
            ),
            NodeProperty(
                display_name="Mandatory Filter Value Expression",
                name="mandatory_filter_value_expression",
                type="string",
                default="",
                required=True,
                description="Expression evaluating to the required value (e.g. {{ $node['x'].json.id }}).",
            ),
            NodeProperty(
                display_name="Default Projection Strip",
                name="default_projection_strip",
                type="json",
                default=[],
                description="JSON array of dotted-path fields to remove when no projection is supplied.",
            ),
            NodeProperty(
                display_name="Max Pipeline Stages",
                name="max_pipeline_stages",
                type="number",
                default=8,
            ),
            NodeProperty(
                display_name="Max Limit",
                name="max_limit",
                type="number",
                default=50,
            ),
            NodeProperty(
                display_name="Max Time (ms)",
                name="max_time_ms",
                type="number",
                default=3000,
            ),
            NodeProperty(
                display_name="Max Response Bytes",
                name="max_response_bytes",
                type="number",
                default=200_000,
            ),
        ],
    )

    def get_config(self, node_definition: NodeDefinition) -> dict[str, Any]:
        tool_name = self.get_parameter(node_definition, "tool_name", _DEFAULT_TOOL_NAME)
        description = self.get_parameter(
            node_definition, "tool_description", _DEFAULT_DESCRIPTION
        )
        connection_string = self.get_parameter(node_definition, "connection_string", "")
        database = self.get_parameter(node_definition, "database", "")
        allowed_collections = self.get_parameter(node_definition, "allowed_collections", [])
        mandatory_field = self.get_parameter(node_definition, "mandatory_filter_field", "")
        mandatory_value_expr = self.get_parameter(
            node_definition, "mandatory_filter_value_expression", ""
        )
        default_strip = self.get_parameter(node_definition, "default_projection_strip", [])
        max_pipeline_stages = int(
            self.get_parameter(node_definition, "max_pipeline_stages", 8) or 8
        )
        max_limit = int(self.get_parameter(node_definition, "max_limit", 50) or 50)
        max_time_ms = int(self.get_parameter(node_definition, "max_time_ms", 3000) or 3000)
        max_response_bytes = int(
            self.get_parameter(node_definition, "max_response_bytes", 200_000) or 200_000
        )

        # Tolerate JSON-encoded strings for list/dict params.
        if isinstance(allowed_collections, str):
            try:
                allowed_collections = json.loads(allowed_collections)
            except ValueError:
                allowed_collections = []
        if isinstance(default_strip, str):
            try:
                default_strip = json.loads(default_strip)
            except ValueError:
                default_strip = []
        if not isinstance(allowed_collections, list):
            allowed_collections = []
        if not isinstance(default_strip, list):
            default_strip = []

        mandatory_value = _resolve_value_expression(mandatory_value_expr)

        async def execute(input_data: dict[str, Any], context: ExecutionContext) -> dict[str, Any]:
            return await _execute_mongo_query(
                input_data,
                context,
                connection_string=connection_string,
                database=database,
                allowed_collections=list(allowed_collections),
                mandatory_field=mandatory_field,
                mandatory_value=mandatory_value,
                default_strip=list(default_strip),
                max_pipeline_stages=max_pipeline_stages,
                max_limit=max_limit,
                max_time_ms=max_time_ms,
                max_response_bytes=max_response_bytes,
            )

        return {
            "name": tool_name,
            "description": description,
            "input_schema": {
                "type": "object",
                "properties": {
                    "collection": {
                        "type": "string",
                        "description": "Collection name; must be in the allowed list.",
                    },
                    "operation": {
                        "type": "string",
                        "enum": ["find", "aggregate"],
                    },
                    "filter": {
                        "type": "object",
                        "description": (
                            "find only; MUST include the mandatory field at top level "
                            "or in every $or branch."
                        ),
                    },
                    "projection": {
                        "type": "object",
                        "description": "find only.",
                    },
                    "sort": {
                        "type": "object",
                        "description": "find only.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "find only; capped server-side.",
                    },
                    "pipeline": {
                        "type": "array",
                        "description": (
                            "aggregate only; first stage MUST be $match containing the "
                            "mandatory field."
                        ),
                        # Gemini's FunctionDeclaration parser rejects array types
                        # without an `items` sub-schema. Each stage is a Mongo
                        # pipeline document.
                        "items": {"type": "object"},
                    },
                },
                "required": ["collection", "operation"],
            },
            "execute": execute,
        }


# ---------------------------------------------------------------------------
# Executor
# ---------------------------------------------------------------------------


async def _execute_mongo_query(
    input_data: dict[str, Any],
    context: ExecutionContext,  # noqa: ARG001 — accepted for signature parity
    *,
    connection_string: str,
    database: str,
    allowed_collections: list[str],
    mandatory_field: str,
    mandatory_value: Any,
    default_strip: list[str],
    max_pipeline_stages: int,
    max_limit: int,
    max_time_ms: int,
    max_response_bytes: int,
) -> dict[str, Any]:
    collection = input_data.get("collection")
    operation = input_data.get("operation")

    if not isinstance(collection, str) or not collection:
        return {"error": "collection is required"}
    if collection not in allowed_collections:
        return {
            "error": (
                f"collection {collection!r} not in allowed list "
                f"{allowed_collections!r}"
            )
        }
    if operation not in ("find", "aggregate"):
        return {"error": "operation must be 'find' or 'aggregate'"}
    if not mandatory_field:
        return {"error": "tool misconfigured: mandatory_filter_field is empty"}

    client = _get_client(connection_string)
    coll = client[database][collection]

    try:
        if operation == "find":
            filter_ = input_data.get("filter") or {}
            projection = input_data.get("projection")
            sort = input_data.get("sort")
            limit = input_data.get("limit")

            if not isinstance(filter_, dict):
                return {"error": "filter must be an object"}
            try:
                validate_find(filter_, projection, sort, mandatory_field, mandatory_value)
            except ValueError as e:
                return {"error": str(e)}

            effective_projection = apply_default_projection(projection, default_strip)
            effective_limit = clamp_limit(
                limit if isinstance(limit, int) else None, max_limit
            )

            cursor = coll.find(
                filter_,
                effective_projection if effective_projection else None,
            )
            if sort:
                cursor = cursor.sort(list(sort.items()))
            cursor = cursor.limit(effective_limit).max_time_ms(max_time_ms)

            documents, truncated = await _collect_with_budget(
                cursor, max_response_bytes
            )
            payload: dict[str, Any] = {"documents": documents}
            if truncated:
                payload["_truncated"] = True
            return payload

        # aggregate
        pipeline = input_data.get("pipeline")
        if not isinstance(pipeline, list):
            return {"error": "pipeline must be a list"}
        try:
            validate_aggregate(
                pipeline,
                mandatory_field,
                mandatory_value,
                max_stages=max_pipeline_stages,
            )
        except ValueError as e:
            return {"error": str(e)}

        cursor = coll.aggregate(pipeline, maxTimeMS=max_time_ms)
        results, truncated = await _collect_with_budget(cursor, max_response_bytes)
        payload = {"results": results}
        if truncated:
            payload["_truncated"] = True
        return payload

    except Exception as e:  # noqa: BLE001
        logger.exception("MongoQueryTool error")
        return {"error": f"mongo error: {e}"}


async def _collect_with_budget(
    cursor: Any, max_bytes: int
) -> tuple[list[dict[str, Any]], bool]:
    """Materialize a motor cursor with a serialized-size budget.

    Returns the list of documents (already JSON-friendly) plus a truncated
    flag. We measure size by JSON-encoding each document and accumulating —
    cheaper to compute once here than at the LLM-context layer.
    """
    documents: list[dict[str, Any]] = []
    total_bytes = 0
    truncated = False

    async for raw in cursor:
        doc = _to_jsonable(raw)
        try:
            encoded = json.dumps(doc, default=str)
        except Exception:
            encoded = str(doc)
        sz = len(encoded.encode("utf-8")) if isinstance(encoded, str) else 0
        if total_bytes + sz > max_bytes and documents:
            truncated = True
            break
        documents.append(doc)
        total_bytes += sz
        if total_bytes >= max_bytes:
            truncated = True
            break
    return documents, truncated
