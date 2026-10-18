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


from ...engine import shapes as sh

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
                display_name="API endpoint",
                name="api_ref",
                type="json",
                default=None,
                description="Pick an endpoint from the API catalog instead of typing a URL: {spec_id, operation_id}. "
                            "The method comes from the endpoint and the URL is its base URL plus path.",
            ),
            NodeProperty(
                display_name="Path parameters",
                name="path_params",
                type="json",
                default={},
                description="Values for {name} placeholders in the endpoint's path. Supports expressions.",
                display_options={"hide": {"api_ref": [None]}},
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

    # HttpRequest: observed -> default contract (UNKNOWN unless the instance has an approved contract),
    # except a node on a catalog endpoint (api_ref): its reply is declared by the spec.

    def contract(self, params, inputs, lookups=None):
        from ...engine.shapes import make_contract
        from ...services.mock_gen import first_success

        op = (lookups or {}).get("api_operation")
        ref = (params or {}).get("api_ref")
        if not (isinstance(ref, dict) and ref.get("spec_id") and isinstance(op, dict)):
            return super().contract(params, inputs, lookups)
        ok = first_success(op)
        body = _api_body_shape(op, ok[1].get("shape") if ok else None)
        main = sh.object_of(
            {"statusCode": sh.scalar("integer", "spec"), "headers": sh.scalar("object", "spec"), "body": body},
            ["statusCode", "headers", "body"], "spec",
        )
        return make_contract(inputs, {"main": main})

    async def execute(
        self,
        context: ExecutionContext,
        node_definition: NodeDefinition,
        input_data: list[NodeData],
    ) -> NodeExecutionResult:
        import json
        from ...engine.types import NodeData
        from ...engine.expression_engine import ExpressionEngine, expression_engine

        api_ref = node_definition.parameters.get("api_ref")
        if isinstance(api_ref, dict) and api_ref.get("spec_id"):
            method, path_template, path_param_templates = self._resolve_api_operation(context, node_definition, api_ref)
            url_template = ""
        else:
            api_ref, path_template, path_param_templates = None, None, {}
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

        allowlist = list((getattr(context, "extras", None) or {}).get("egress_allowlist") or [])

        def check_egress(url: str) -> None:
            from urllib.parse import urlparse

            from ...broker.policy import host_matches

            host = urlparse(str(url)).hostname or ""
            if allowlist and not host_matches(host, allowlist):
                raise ValueError(f"Host '{host}' is not on this project's egress allowlist")

        async def check_hop(request: httpx.Request) -> None:
            check_egress(str(request.url))  # every redirect hop is a new request

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

                # Resolve URL expressions (an API endpoint: base URL + path with {name} filled from path_params)
                if path_template is not None:
                    url = self._fill_path(path_template, path_param_templates, expr_context, node_definition)
                else:
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

                check_egress(url)
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
             async with httpx.AsyncClient(follow_redirects=True, event_hooks={"request": [check_hop]}) as client:
                 await make_requests(client)

        # Redact $vars secret values out of the URL before exposing it via
        # node metadata — otherwise tokens used in URL query params (e.g.
        # `?api_key={{ $vars.MY_TOKEN }}`) end up in node_outputs and the
        # execution-log UI.
        from ...engine.logging import execution_secrets_var
        from ...services.vars_substitution import redact_secrets
        displayed_url = redact_secrets(last_url, execution_secrets_var.get(set()))

        metadata = {
            "requestUrl": displayed_url,
            "requestMethod": method,
            "responseStatusCode": last_status,
            "responseTimeMs": round(total_response_time_ms, 2),
            "responseSizeBytes": total_response_size,
        }

        return self.output(results, metadata=metadata)

    # ── API catalog endpoint (api_ref) ───────────────────────────────────────────────────────

    @staticmethod
    def _resolve_api_operation(context, node_definition, api_ref: dict) -> tuple[str, str, dict]:
        """(method, base_url + path, path_params templates) of the endpoint ``api_ref`` points at."""
        specs = (getattr(context, "extras", None) or {}).get("api_specs")
        if specs is None:
            raise ValueError(
                f'Node "{node_definition.name}" uses an API endpoint but the API catalog was not loaded for this run '
                "(context.extras['api_specs'] is missing)"
            )
        spec = specs.get(api_ref["spec_id"])
        if not spec:
            raise ValueError(f'API "{api_ref["spec_id"]}" not found in the API catalog (was it deleted?)')
        op = next((o for o in spec.get("operations") or [] if o.get("operation_id") == api_ref.get("operation_id")), None)
        if op is None:
            raise ValueError(f'Endpoint "{api_ref.get("operation_id")}" not found in API "{spec.get("name") or api_ref["spec_id"]}"')
        base = str(spec.get("base_url") or "").rstrip("/")
        if not base:
            raise ValueError(f'API "{spec.get("name") or api_ref["spec_id"]}" has no base URL')
        raw = node_definition.parameters.get("path_params")
        return str(op["method"]).upper(), base + str(op["path"]), raw if isinstance(raw, dict) else {}

    @staticmethod
    def _fill_path(template: str, param_templates: dict, expr_context, node_definition) -> str:
        import re
        from urllib.parse import quote

        from ...engine.expression_engine import expression_engine

        def fill(m: re.Match) -> str:
            name = m.group(1)
            raw = param_templates.get(name)
            if raw is None or raw == "":
                raise ValueError(f'Path parameter "{name}" is missing on node "{node_definition.name}"')
            value = expression_engine.resolve(raw, expr_context) if isinstance(raw, str) else raw
            if value is None or value == "":
                raise ValueError(f'Path parameter "{name}" resolved to an empty value on node "{node_definition.name}"')
            return quote(str(value), safe="")

        return re.sub(r"\{([^{}/]+)\}", fill, template)


def _api_body_shape(op: dict, ok_shape: dict | None) -> dict:
    """``body`` of the node output: the first 2xx reply's shape, widened so documented error replies fit.

    Error replies (4xx/5xx) carry their own fields, so when any documents an object shape the body becomes the
    union: every property stays, error-only ones are added as optional, and ``required`` is dropped
    (a non-2xx reply doesn't carry the 2xx fields). Without error shapes it is exactly the 2xx shape.
    """
    import copy

    if not isinstance(ok_shape, dict):
        return sh.unknown()
    errors = [
        r["shape"] for k, r in (op.get("responses") or {}).items()
        if isinstance(k, str) and k.isdigit() and int(k) >= 400 and isinstance(r, dict)
        and isinstance(r.get("shape"), dict) and r["shape"].get("type") == "object" and isinstance(r["shape"].get("properties"), dict)
    ]
    if not errors or ok_shape.get("type") != "object" or not isinstance(ok_shape.get("properties"), dict):
        return copy.deepcopy(ok_shape)
    body = copy.deepcopy(ok_shape)
    body.pop("required", None)
    for err in errors:
        for name, shape in err["properties"].items():
            if name not in body["properties"]:
                body["properties"][name] = copy.deepcopy(shape)
    return body
