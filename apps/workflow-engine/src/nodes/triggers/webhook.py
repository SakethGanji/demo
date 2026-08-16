"""Webhook node - HTTP POST webhook receiver."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from ..base import (
    BaseNode,
    NodeTypeDescription,
    NodeOutputDefinition,
    NodeProperty,
    NodePropertyOption,
)

if TYPE_CHECKING:
    from ...engine.types import ExecutionContext, NodeData, NodeDefinition, NodeExecutionResult


class WebhookNode(BaseNode):
    """Webhook trigger node - receives HTTP POST requests."""

    node_description = NodeTypeDescription(
        name="Webhook",
        display_name="Webhook",
        description="Trigger workflow via HTTP webhook",
        icon="fa:bolt",
        group=["trigger"],
        inputs=[],  # No inputs - this is a trigger
        outputs=[
            NodeOutputDefinition(
                name="main",
                display_name="Output",
                schema={
                    "type": "object",
                    "properties": {
                        "body": {"type": "unknown", "description": "Request body"},
                        "headers": {"type": "object", "description": "Request headers"},
                        "query": {"type": "object", "description": "Query parameters"},
                        "method": {"type": "string", "description": "HTTP method"},
                    },
                },
            )
        ],
        properties=[
            NodeProperty(
                display_name="HTTP Method",
                name="method",
                type="options",
                default="POST",
                options=[
                    NodePropertyOption(name="POST", value="POST"),
                    NodePropertyOption(name="GET", value="GET"),
                    NodePropertyOption(name="PUT", value="PUT"),
                    NodePropertyOption(name="PATCH", value="PATCH"),
                    NodePropertyOption(name="DELETE", value="DELETE"),
                ],
            ),
            NodeProperty(
                display_name="Webhook Path",
                name="path",
                type="string",
                default="",
                placeholder="my-api/orders",
                description="Custom webhook path. If set, the webhook is accessible at /webhook/p/{path} in addition to /webhook/{workflowId}.",
            ),
            NodeProperty(
                display_name="Response Mode",
                name="responseMode",
                type="options",
                default="onReceived",
                options=[
                    NodePropertyOption(
                        name="On Received",
                        value="onReceived",
                        description="Respond immediately when webhook is received",
                    ),
                    NodePropertyOption(
                        name="Last Node",
                        value="lastNode",
                        description="Respond with output from last node",
                    ),
                ],
            ),
        ],
    )

    @property
    def type(self) -> str:
        return "Webhook"

    @property
    def description(self) -> str:
        return "Trigger workflow via HTTP webhook"

    async def execute(
        self,
        context: ExecutionContext,
        node_definition: NodeDefinition,
        input_data: list[NodeData],
    ) -> NodeExecutionResult:
        from ...engine.types import NodeData

        # Webhook data comes from input_data (passed by the webhook handler)
        if input_data and input_data[0].json:
            return self.output(input_data)

        # Fallback for manual execution
        return self.output([
            NodeData(json={
                "body": {},
                "headers": {},
                "query": {},
                "method": "POST",
                "triggeredAt": datetime.now().isoformat(),
            })
        ])
