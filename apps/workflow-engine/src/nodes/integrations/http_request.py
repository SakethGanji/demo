"""HTTP Request node - makes HTTP requests to external APIs."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

import httpx

from ..base import (
    BaseNode,
    NodeTypeDescription,
    NodeInputDefinition,
    NodeOutputDefinition,
    NodeProperty,
    NodePropertyOption,
)

if TYPE_CHECKING:
    from ...engine.types import ExecutionContext, NodeData, NodeDefinition, NodeExecutionResult


class HttpRequestNode(BaseNode):
    """HTTP Request node - makes HTTP requests to external APIs."""

    node_description = NodeTypeDescription(
        name="HttpRequest",
        display_name="HTTP Request",
        description="Makes HTTP requests to external APIs",
        icon="fa:globe",
        group=["transform"],
        inputs=[NodeInputDefinition(name="main", display_name="Input")],
        outputs=[
            NodeOutputDefinition(
                name="main",
                display_name="Response",
                schema={
                    "type": "object",
                    "properties": {
                        "statusCode": {"type": "number", "description": "HTTP status code"},
                        "headers": {"type": "object", "description": "Response headers"},
                        "body": {"type": "unknown", "description": "Response body"},
                    },
                },
            )
        ],
        properties=[
            NodeProperty(
                display_name="Method",
                name="method",
                type="options",
                default="GET",
                required=True,
                options=[
                    NodePropertyOption(name="GET", value="GET"),
                    NodePropertyOption(name="POST", value="POST"),
                    NodePropertyOption(name="PUT", value="PUT"),
                    NodePropertyOption(name="PATCH", value="PATCH"),
                    NodePropertyOption(name="DELETE", value="DELETE"),
                    NodePropertyOption(name="HEAD", value="HEAD"),
                ],
            ),
            NodeProperty(
                display_name="URL",
                name="url",
                type="string",
                default="",
                required=True,
                placeholder="https://api.example.com/endpoint",
                description="The URL to make the request to. Supports expressions.",
            ),
            NodeProperty(
                display_name="Headers",
                name="headers",
                type="collection",
                default=[],
                description="HTTP headers to send with the request",
                type_options={"multipleValues": True},
                properties=[
                    NodeProperty(
                        display_name="Header Name",
                        name="name",
                        type="string",
                        default="",
                        placeholder="Content-Type",
                    ),
                    NodeProperty(
                        display_name="Header Value",
                        name="value",
                        type="string",
                        default="",
                        placeholder="application/json",
                    ),
                ],
            ),
            NodeProperty(
                display_name="Body",
                name="body",
                type="json",
                default="",
                description="Request body (for POST, PUT, PATCH)",
                type_options={"language": "json", "rows": 10},
                display_options={"show": {"method": ["POST", "PUT", "PATCH"]}},
            ),
            NodeProperty(
                display_name="Response Type",
                name="responseType",
                type="options",
                default="json",
                options=[
                    NodePropertyOption(
                        name="JSON",
                        value="json",
                        description="Parse response as JSON",
                    ),
                    NodePropertyOption(
                        name="Text",
                        value="text",
                        description="Return raw text",
                    ),
                    NodePropertyOption(
                        name="Binary",
                        value="binary",
                        description="Return binary data",
                    ),
                ],
            ),
        ],
    )

    @property
    def type(self) -> str:
        return "HttpRequest"

    @property
    def description(self) -> str:
        return "Makes HTTP requests to external APIs"

    async def execute(
        self,
        context: ExecutionContext,
        node_definition: NodeDefinition,
        input_data: list[NodeData],
    ) -> NodeExecutionResult:
        import json
        from ...engine.types import NodeData
        from ...engine.expression_engine import ExpressionEngine, expression_engine

        url_template = self.get_parameter(node_definition, "url")
        method = self.get_parameter(node_definition, "method", "GET")
        response_type = self.get_parameter(node_definition, "responseType", "json")
        headers_param = self.get_parameter(node_definition, "headers", [])
        body_template = node_definition.parameters.get("body", "")

        from time import perf_counter

        results: list[NodeData] = []
        items = input_data if input_data else [NodeData(json={})]
        # Track metrics across all requests
        last_url = ""
        last_status = 0
        total_response_time_ms = 0.0
        total_response_size = 0

        async def make_requests(client: httpx.AsyncClient) -> None:
            nonlocal last_url, last_status, total_response_time_ms, total_response_size
            for idx, item in enumerate(items):
                # Create expression context for this item
                expr_context = ExpressionEngine.create_context(
                    input_data,
                    context.node_states,
                    context.execution_id,
                    idx,
                )

                # Resolve URL expressions
                url = expression_engine.resolve(url_template, expr_context)
                last_url = url

                # Process and resolve headers
                headers: dict[str, str] = {"Content-Type": "application/json"}
                if isinstance(headers_param, list):
                    for h in headers_param:
                        if h.get("name"):
                            header_value = h.get("value", "")
                            # Resolve expressions in header values
                            resolved_value = expression_engine.resolve(header_value, expr_context)
                            headers[h["name"]] = str(resolved_value) if resolved_value else ""
                elif isinstance(headers_param, dict):
                    for name, value in headers_param.items():
                        resolved_value = expression_engine.resolve(value, expr_context)
                        headers[name] = str(resolved_value) if resolved_value else ""

                # Process body with expression resolution
                body = None
                if method in ("POST", "PUT", "PATCH") and body_template:
                    # Resolve expressions in body
                    resolved_body = expression_engine.resolve(body_template, expr_context)

                    if isinstance(resolved_body, dict):
                        # Already a dict (expression returned object)
                        body = resolved_body
                    elif isinstance(resolved_body, str) and resolved_body:
                        # Try to parse as JSON
                        try:
                            body = json.loads(resolved_body)
                        except json.JSONDecodeError:
                            body = resolved_body  # Keep as string

                req_start = perf_counter()
                response = await client.request(
                    method=method,
                    url=url,
                    headers=headers,
                    json=body if isinstance(body, dict) else None,
                    content=body if isinstance(body, str) else None,
                )
                req_elapsed = round((perf_counter() - req_start) * 1000, 2)
                total_response_time_ms += req_elapsed
                response_size = len(response.content)
                total_response_size += response_size
                last_status = response.status_code

                response_data: Any
                if response_type == "text":
                    response_data = response.text
                elif response_type == "binary":
                    response_data = {
                        "_binary": True,
                        "size": response_size,
                    }
                else:
                    try:
                        response_data = response.json()
                    except Exception:
                        response_data = {}

                results.append(
                    NodeData(json={
                        "statusCode": response.status_code,
                        "headers": dict(response.headers),
                        "body": response_data,
                    })
                )

        if hasattr(context, "http_client") and context.http_client:
             await make_requests(context.http_client)
        else:
             async with httpx.AsyncClient(follow_redirects=True) as client:
                 await make_requests(client)

        metadata = {
            "requestUrl": last_url,
            "requestMethod": method,
            "responseStatusCode": last_status,
            "responseTimeMs": round(total_response_time_ms, 2),
            "responseSizeBytes": total_response_size,
        }

        return self.output(results, metadata=metadata)
