"""Data Aggregate tool subnode - group-by aggregations via analytics service."""

from __future__ import annotations

import os
from typing import Any, TYPE_CHECKING

from ...base import NodeTypeDescription
from ..base_subnode import BaseSubnode

if TYPE_CHECKING:
    from ....engine.types import ExecutionContext, NodeDefinition


class DataAggregateToolNode(BaseSubnode):
    """Aggregate data with group-by, sum, mean, median, count, etc."""

    node_description = NodeTypeDescription(
        name="DataAggregateTool",
        display_name="Data Aggregate Tool",
        description="Group-by aggregations: sum, mean, median, count, min, max, std, nunique",
        icon="fa:layer-group",
        group=["ai"],
        inputs=[],
        outputs=[],
        properties=[],
        is_subnode=True,
        subnode_type="tool",
        provides_to_slot="tools",
    )

    def get_config(self, node_definition: NodeDefinition) -> dict[str, Any]:
        """Return data aggregate tool configuration."""
        return {
            "name": "aggregate_data",
            "description": (
                "Aggregate a dataset by grouping rows and computing statistics. "
                "Supports sum, mean, median, count, min, max, std, nunique. "
                "Can filter rows with SQL WHERE expressions, sort, and limit results. "
                "Pass dataset_id (preferred) or raw data array."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "dataset_id": {
                        "type": "string",
                        "description": "ID of an uploaded dataset (preferred over raw data)",
                    },
                    "data": {
                        "type": "array",
                        "items": {"type": "object"},
                        "description": "Array of objects (the dataset rows) — use dataset_id instead when available",
                    },
                    "group_by": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Column names to group by",
                    },
                    "aggregations": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "column": {"type": "string", "description": "Column to aggregate"},
                                "function": {
                                    "type": "string",
                                    "enum": ["sum", "mean", "median", "count", "min", "max", "std", "nunique"],
                                    "description": "Aggregation function",
                                },
                                "alias": {"type": "string", "description": "Optional output column name"},
                            },
                            "required": ["column", "function"],
                        },
                        "description": "List of aggregations to compute",
                    },
                    "sort_by": {
                        "type": "string",
                        "description": "Column to sort results by",
                    },
                    "sort_order": {
                        "type": "string",
                        "enum": ["asc", "desc"],
                        "description": "Sort order (default: desc)",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max number of groups to return",
                    },
                    "filter_expr": {
                        "type": "string",
                        "description": "SQL WHERE filter expression, e.g. 'revenue > 1000'",
                    },
                },
                "required": ["group_by", "aggregations"],
            },
            "execute": _execute_aggregate,
        }


async def _execute_aggregate(
    input_data: dict[str, Any], context: ExecutionContext
) -> dict[str, Any]:
    """Call the analytics service /aggregate endpoint."""
    import httpx

    from ._analytics_helpers import resolve_dataset_ref

    payload = resolve_dataset_ref(input_data)
    service_url = os.getenv("ANALYTICS_SERVICE_URL", "http://localhost:8001")

    owned_client = False
    client = context.http_client
    if client is None:
        client = httpx.AsyncClient(timeout=60.0)
        owned_client = True

    try:
        response = await client.post(
            f"{service_url}/aggregate",
            json=payload,
            timeout=60.0,
        )
        if response.status_code >= 400:
            return {"error": f"Analytics service returned {response.status_code}: {response.text}"}
        return response.json()
    except httpx.TimeoutException:
        return {"error": "Analytics service request timed out"}
    except Exception as e:
        return {"error": str(e)}
    finally:
        if owned_client:
            await client.aclose()
