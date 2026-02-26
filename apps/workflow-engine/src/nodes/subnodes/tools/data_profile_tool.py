"""Data Profile tool subnode - profile datasets via analytics service."""

from __future__ import annotations

import os
from typing import Any, TYPE_CHECKING

from ...base import NodeTypeDescription
from ..base_subnode import BaseSubnode

if TYPE_CHECKING:
    from ....engine.types import ExecutionContext, NodeDefinition


class DataProfileToolNode(BaseSubnode):
    """Profile data columns — stats, distributions, nulls, correlations."""

    node_description = NodeTypeDescription(
        name="DataProfileTool",
        display_name="Data Profile Tool",
        description="Profile dataset columns: types, nulls, stats, distributions",
        icon="fa:chart-bar",
        group=["ai"],
        inputs=[],
        outputs=[],
        properties=[],
        is_subnode=True,
        subnode_type="tool",
        provides_to_slot="tools",
    )

    def get_config(self, node_definition: NodeDefinition) -> dict[str, Any]:
        """Return data profile tool configuration."""
        return {
            "name": "profile_data",
            "description": (
                "Profile a dataset to understand its structure and statistics. "
                "Returns column types, null counts, mean/std/min/max/quartiles, "
                "top values, histograms, and optional correlations. "
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
                    "columns": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional list of column names to profile. If omitted, all columns are profiled.",
                    },
                    "include_histograms": {
                        "type": "boolean",
                        "description": "Include histogram data for numeric columns (default: true)",
                    },
                    "include_correlations": {
                        "type": "boolean",
                        "description": "Include pairwise correlation matrix for numeric columns (default: false)",
                    },
                },
                "required": [],
            },
            "execute": _execute_profile,
        }


async def _execute_profile(
    input_data: dict[str, Any], context: ExecutionContext
) -> dict[str, Any]:
    """Call the analytics service /profile endpoint."""
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
            f"{service_url}/profile",
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
