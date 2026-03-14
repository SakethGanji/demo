"""Data Sample tool - sample datasets via analytics service."""

from __future__ import annotations

import os
from typing import Any, TYPE_CHECKING

from ...base import NodeTypeDescription
from .._config_base import ConfigProvider

if TYPE_CHECKING:
    from ....engine.types import ExecutionContext, NodeDefinition


class DataSampleToolNode(ConfigProvider):
    """Sample rows from a dataset — random, stratified, systematic, first/last N."""

    node_description = NodeTypeDescription(
        name="DataSampleTool",
        display_name="Data Sample Tool",
        description="Sample rows: random, stratified, systematic, first_n, last_n",
        icon="fa:filter",
        group=["ai"],
        inputs=[],
        outputs=[],
        properties=[],
    )

    def get_config(self, node_definition: NodeDefinition) -> dict[str, Any]:
        """Return data sample tool configuration."""
        return {
            "name": "sample_data",
            "description": (
                "Sample rows from a dataset. Methods: random (uniform), "
                "stratified (proportional by column), systematic (every Nth row), "
                "first_n, last_n. Returns column summaries, a 5-row preview, and "
                "a download_url for the full sample. "
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
                    "method": {
                        "type": "string",
                        "enum": ["random", "stratified", "systematic", "first_n", "last_n"],
                        "description": "Sampling method",
                    },
                    "sample_size": {
                        "type": "integer",
                        "description": "Number of rows to sample",
                    },
                    "stratify_column": {
                        "type": "string",
                        "description": "Column for stratified sampling (required when method is 'stratified')",
                    },
                    "seed": {
                        "type": "integer",
                        "description": "Random seed for reproducibility",
                    },
                },
                "required": ["method", "sample_size"],
            },
            "execute": _execute_sample,
        }


async def _execute_sample(
    input_data: dict[str, Any], context: ExecutionContext
) -> dict[str, Any]:
    """Call the analytics service /sample endpoint."""
    import httpx

    from ._analytics_helpers import resolve_dataset_ref

    payload = resolve_dataset_ref(input_data)
    payload["return_data"] = False
    service_url = os.getenv("ANALYTICS_SERVICE_URL", "http://localhost:8001")

    owned_client = False
    client = context.http_client
    if client is None:
        client = httpx.AsyncClient(timeout=60.0)
        owned_client = True

    try:
        response = await client.post(
            f"{service_url}/sample",
            json=payload,
            timeout=60.0,
        )
        if response.status_code >= 400:
            return {"error": f"Analytics service returned {response.status_code}: {response.text}"}
        result = response.json()
        result.pop("download_url", None)
        result.pop("data", None)
        result.pop("output_path", None)
        return result
    except httpx.TimeoutException:
        return {"error": "Analytics service request timed out"}
    except Exception as e:
        return {"error": str(e)}
    finally:
        if owned_client:
            await client.aclose()
