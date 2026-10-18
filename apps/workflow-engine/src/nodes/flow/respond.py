"""Respond node — set the HTTP response of an API route / webhook (phase 5, BACKEND-DESIGN §3.5)."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from ..base import BaseNode, NodeInputDefinition, NodeOutputDefinition, NodeProperty, NodePropertyOption, NodeTypeDescription

if TYPE_CHECKING:
    from ...engine.types import ExecutionContext, NodeData, NodeDefinition, NodeExecutionResult


from ...engine import shapes as sh

class RespondNode(BaseNode):
    """Sets ``context.webhook_response`` (status, headers, body) and passes items through."""

    node_description = NodeTypeDescription(
        name="Respond",
        display_name="Respond",
        description="Return an HTTP response (status, headers, body) from an API route or webhook",
        icon="fa:reply",
        group=["flow"],
        inputs=[NodeInputDefinition(name="main", display_name="Input")],
        outputs=[NodeOutputDefinition(name="main", display_name="Output")],
        properties=[
            NodeProperty(display_name="Status Code", name="statusCode", type="number", default=200),
            NodeProperty(
                display_name="Headers", name="headers", type="collection", default=[],
                type_options={"multipleValues": True},
                properties=[
                    NodeProperty(display_name="Name", name="name", type="string", default=""),
                    NodeProperty(display_name="Value", name="value", type="string", default=""),
                ],
            ),
            NodeProperty(display_name="Body", name="body", type="json", default="{{ $json }}",
                         description="Expression for the response body (default: the first input item)"),
            NodeProperty(
                display_name="Content Type", name="contentType", type="options", default="application/json",
                options=[NodePropertyOption(name="JSON", value="application/json"),
                         NodePropertyOption(name="Text", value="text/plain"),
                         NodePropertyOption(name="HTML", value="text/html")],
            ),
        ],
    )

    @property
    def type(self) -> str:
        return "Respond"

    @property
    def description(self) -> str:
        return "Return an HTTP response"

    def contract(self, params, inputs, lookups=None):
        """Derived: passthrough (the response is a side effect)."""
        return sh.passthrough_contract(inputs)

    async def execute(self, context: ExecutionContext, node_definition: NodeDefinition,
                      input_data: list[NodeData]) -> NodeExecutionResult:
        from ...engine.expression_engine import ExpressionEngine, expression_engine
        from ...engine.types import NodeData, WebhookResponse

        items = input_data or [NodeData(json={})]
        expr = ExpressionEngine.create_context(items, context.node_states, context.execution_id, 0)
        params = node_definition.parameters
        body: Any = params.get("body", "{{ $json }}")
        body = expression_engine.resolve(body, expr)
        if isinstance(body, str) and params.get("contentType", "application/json") == "application/json":
            import json
            try:
                body = json.loads(body)
            except (ValueError, TypeError):
                pass
        status = expression_engine.resolve(params.get("statusCode", 200), expr)
        try:
            status = int(status)
        except (TypeError, ValueError):
            status = 200
        headers: dict[str, str] = {}
        for h in params.get("headers") or []:
            if isinstance(h, dict) and h.get("name"):
                headers[str(h["name"])] = str(expression_engine.resolve(h.get("value", ""), expr))
        context.webhook_response = WebhookResponse(
            status_code=status, body=body, headers=headers,
            content_type=params.get("contentType", "application/json") or "application/json",
        )
        return self.output(items, metadata={"responseStatusCode": status})
