"""Data Report tool - generate reports via analytics service."""

from __future__ import annotations

import os
from typing import Any, TYPE_CHECKING

from ...base import NodeTypeDescription
from .._config_base import ConfigProvider

if TYPE_CHECKING:
    from ....engine.types import ExecutionContext, NodeDefinition


class DataReportToolNode(ConfigProvider):
    """Generate formatted data reports — HTML or Markdown."""

    node_description = NodeTypeDescription(
        name="DataReportTool",
        display_name="Data Report Tool",
        description="Generate formatted data reports in HTML or Markdown",
        icon="fa:file-alt",
        group=["ai"],
        inputs=[],
        outputs=[],
        properties=[],
    )

    def get_config(self, node_definition: NodeDefinition) -> dict[str, Any]:
        """Return data report tool configuration."""
        return {
            "name": "generate_report",
            "description": (
                "Generate a formatted data report from a dataset. "
                "Includes overview, column profiles, aggregation tables, "
                "and data quality summary. Output as HTML or Markdown. "
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
                    "title": {
                        "type": "string",
                        "description": "Report title (default: 'Data Report')",
                    },
                    "sections": {
                        "type": "object",
                        "description": "Control which sections appear: {overview, profile, aggregation, quality}",
                    },
                    "group_by": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Columns for aggregation section",
                    },
                    "aggregations": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "column": {"type": "string"},
                                "function": {"type": "string"},
                                "alias": {"type": "string"},
                            },
                            "required": ["column", "function"],
                        },
                        "description": "Aggregations for the report's aggregation section",
                    },
                    "output_format": {
                        "type": "string",
                        "enum": ["html", "markdown"],
                        "description": "Output format (default: markdown)",
                    },
                },
                "required": [],
            },
            "execute": _execute_report,
        }


async def _execute_report(
    input_data: dict[str, Any], context: ExecutionContext
) -> dict[str, Any]:
    """Call the analytics service /report endpoint."""
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
            f"{service_url}/report",
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
