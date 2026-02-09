"""RespondToWebhook node - send custom HTTP response and stop execution."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from ..base import (
    BaseNode,
    NodeTypeDescription,
    NodeInputDefinition,
    NodeOutputDefinition,
    NodeProperty,
    NodePropertyOption,
)

if TYPE_CHECKING:
    from ...engine.types import ExecutionContext, NodeDefinition, NodeExecutionResult

import json as json_module

from ...engine.types import NodeData, WebhookResponse
from ...engine.expression_engine import expression_engine, ExpressionEngine


class RespondToWebhookNode(BaseNode):
    """Send a custom HTTP response to the webhook caller and stop execution."""

    node_description = NodeTypeDescription(
        name="RespondToWebhook",
        display_name="Respond to Webhook",
        description="Send a custom HTTP response back to the webhook caller",
        icon="fa:reply",
        group=["output"],
        inputs=[NodeInputDefinition(name="main", display_name="Input")],
        outputs=[
            NodeOutputDefinition(
                name="main",
                display_name="Output",
                schema={"type": "unknown", "passthrough": True},
            ),
        ],
        properties=[
            NodeProperty(
                display_name="Response Code",
                name="statusCode",
                type="options",
                default="200",
                options=[
                    NodePropertyOption(name="200 OK", value="200"),
                    NodePropertyOption(name="201 Created", value="201"),
                    NodePropertyOption(name="204 No Content", value="204"),
                    NodePropertyOption(name="400 Bad Request", value="400"),
                    NodePropertyOption(name="401 Unauthorized", value="401"),
                    NodePropertyOption(name="403 Forbidden", value="403"),
                    NodePropertyOption(name="404 Not Found", value="404"),
                    NodePropertyOption(name="422 Unprocessable Entity", value="422"),
                    NodePropertyOption(name="500 Internal Server Error", value="500"),
                ],
                description="HTTP status code to return",
            ),
            NodeProperty(
                display_name="Response Mode",
                name="responseMode",
                type="options",
                default="lastNode",
                options=[
                    NodePropertyOption(
                        name="Use Input Data",
                        value="lastNode",
                        description="Return the input data as the response body",
                    ),
                    NodePropertyOption(
                        name="Custom Body",
                        value="custom",
                        description="Specify a custom response body",
                    ),
                    NodePropertyOption(
                        name="No Content",
                        value="noContent",
                        description="Return empty response (for 204 status)",
                    ),
                ],
            ),
            NodeProperty(
                display_name="Response Body",
                name="responseBody",
                type="json",
                default="{}",
                description="Custom response body (JSON). Supports expressions.",
                display_options={"show": {"responseMode": ["custom"]}},
                type_options={"language": "json", "rows": 8},
            ),
            NodeProperty(
                display_name="Response Field",
                name="responseField",
                type="string",
                default="",
                placeholder="result",
                description="Specific field from input to return. Leave empty to return entire input.",
                display_options={"show": {"responseMode": ["lastNode"]}},
            ),
            NodeProperty(
                display_name="Content Type",
                name="contentType",
                type="options",
                default="application/json",
                options=[
                    NodePropertyOption(name="JSON", value="application/json"),
                    NodePropertyOption(name="Text", value="text/plain"),
                    NodePropertyOption(name="HTML", value="text/html"),
                    NodePropertyOption(name="XML", value="application/xml"),
                ],
            ),
            NodeProperty(
                display_name="Wrap in Metadata",
                name="wrapResponse",
                type="boolean",
                default=True,
                description="Wrap response in {status, executionId, data} envelope",
            ),
            NodeProperty(
                display_name="Custom Headers",
                name="headers",
                type="collection",
                default=[],
                description="Additional response headers",
                type_options={"multipleValues": True},
                properties=[
                    NodeProperty(
                        display_name="Header Name",
                        name="name",
                        type="string",
                        default="",
                        placeholder="X-Custom-Header",
                    ),
                    NodeProperty(
                        display_name="Header Value",
                        name="value",
                        type="string",
                        default="",
                        placeholder="value",
                    ),
                ],
            ),
        ],
    )

    @property
    def type(self) -> str:
        return "RespondToWebhook"

    @property
    def description(self) -> str:
        return "Send a custom HTTP response back to the webhook caller"

    async def execute(
        self,
        context: ExecutionContext,
        node_definition: NodeDefinition,
        input_data: list[NodeData],
    ) -> NodeExecutionResult:
        # Check if this is actually a webhook execution
        if context.mode != "webhook":
            # Not a webhook execution, just pass through
            return self.output(input_data)

        status_code = int(self.get_parameter(node_definition, "statusCode", "200"))
        response_mode = self.get_parameter(node_definition, "responseMode", "lastNode")
        content_type = self.get_parameter(node_definition, "contentType", "application/json")
        headers_config = self.get_parameter(node_definition, "headers", [])

        # Build response body
        body: Any = None
        if response_mode == "noContent":
            body = None
        elif response_mode == "custom":
            body_template = self.get_parameter(node_definition, "responseBody", "{}")
            if input_data:
                expr_context = ExpressionEngine.create_context(
                    input_data,
                    context.node_states,
                    context.execution_id,
                    item_index=0,
                )
                body = expression_engine.resolve(body_template, expr_context)
            else:
                body = body_template
            # Parse JSON string to dict/list so JSONResponse serializes it properly
            if isinstance(body, str) and content_type == "application/json":
                try:
                    body = json_module.loads(body)
                except (json_module.JSONDecodeError, ValueError):
                    pass
        else:  # lastNode
            response_field = self.get_parameter(node_definition, "responseField", "")
            if input_data:
                if response_field:
                    # Check if it's an expression or a simple field path
                    if "{{" in response_field:
                        # It's an expression - evaluate it
                        expr_context = ExpressionEngine.create_context(
                            input_data,
                            context.node_states,
                            context.execution_id,
                            item_index=0,
                        )
                        body = expression_engine.resolve(response_field, expr_context)
                    else:
                        # Simple field path
                        body = self._get_nested_value(input_data[0].json, response_field)
                else:
                    # Return all items if multiple, single item if one
                    if len(input_data) == 1:
                        body = input_data[0].json
                    else:
                        body = [item.json for item in input_data]

        # Build headers
        headers: dict[str, str] = {}
        for header in headers_config:
            name = header.get("name", "")
            value = header.get("value", "")
            if name:
                # Resolve expressions in header values
                if input_data:
                    expr_context = ExpressionEngine.create_context(
                        input_data,
                        context.node_states,
                        context.execution_id,
                        item_index=0,
                    )
                    value = str(expression_engine.resolve(value, expr_context))
                headers[name] = value

        # Wrap response in metadata envelope if requested
        wrap_response = self.get_parameter(node_definition, "wrapResponse", True)
        if wrap_response and content_type == "application/json":
            # Determine status string based on status code
            status_str = "success" if status_code < 400 else "error"
            body = {
                "status": status_str,
                "executionId": context.execution_id,
                "data": body,
            }

        # Set the webhook response in context (only first RespondToWebhook wins)
        if not context.webhook_response:
            context.webhook_response = WebhookResponse(
                status_code=status_code,
                body=body,
                headers=headers if headers else None,
                content_type=content_type,
            )

        # Pass through input data so downstream nodes can continue executing
        output_data = input_data if input_data else [
            NodeData(json={
                "_respondedToWebhook": True,
                "statusCode": status_code,
                "contentType": content_type,
            })
        ]
        return self.output(output_data)

    def _get_nested_value(self, obj: dict[str, Any], path: str) -> Any:
        """Get value at nested path."""
        if not path:
            return obj
        current: Any = obj
        for key in path.split("."):
            if isinstance(current, dict):
                current = current.get(key)
            elif isinstance(current, list) and key.isdigit():
                idx = int(key)
                current = current[idx] if 0 <= idx < len(current) else None
            else:
                return None
        return current
