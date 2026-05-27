"""Parameterized API request tool for AI agents.

The agent only sees a schema-validated body — the URL, HTTP method, headers,
and (optionally) static body keys are baked in at workflow-definition time.
This is the right primitive when you want the LLM to call a *specific*
endpoint with constrained inputs (e.g. PromptLab's
``/prompt-lab/sessions/{id}/query``) rather than the open-ended
``HttpRequestTool`` which lets the model choose any URL.

Compared to ``HttpRequestTool``:
  - URL is configured, not LLM-supplied
  - method is configured
  - input_schema is provided by the workflow definition
  - the body sent to the upstream service is exactly the validated tool input
    (optionally merged with ``static_body``)
"""

from __future__ import annotations

import ipaddress
import json
import logging
from typing import Any, TYPE_CHECKING
from urllib.parse import urlparse

from ...base import NodeProperty, NodeTypeDescription
from .._config_base import ConfigProvider

if TYPE_CHECKING:
    from ....engine.types import ExecutionContext, NodeDefinition

logger = logging.getLogger(__name__)


# IP ranges blocked for SSRF protection on PUBLIC URLs. We intentionally allow
# private addresses here because this tool is typically pointed at an internal
# sibling service (e.g. http://localhost:8001/...). The workflow author owns
# the URL — the LLM cannot widen the surface.
_PRIVATE_NETWORKS = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
)


_DEFAULT_TIMEOUT_SECONDS = 30.0


def _looks_private(host: str | None) -> bool:
    if not host:
        return False
    try:
        addr = ipaddress.ip_address(host)
        return any(addr in net for net in _PRIVATE_NETWORKS)
    except ValueError:
        return host in ("localhost",)


class ApiRequestToolNode(ConfigProvider):
    """Agent tool that calls a single, pre-configured HTTP endpoint."""

    node_description = NodeTypeDescription(
        name="ApiRequestTool",
        display_name="API Request Tool",
        description=(
            "Call a pre-configured HTTP endpoint as an agent tool. URL and "
            "method are fixed; the LLM only fills in the request body "
            "matching the provided input_schema."
        ),
        icon="fa:plug",
        group=["ai"],
        inputs=[],
        outputs=[],
        properties=[
            NodeProperty(
                display_name="Tool Name",
                name="tool_name",
                type="string",
                default="api_request",
                description="Function name the LLM sees.",
            ),
            NodeProperty(
                display_name="Tool Description",
                name="tool_description",
                type="string",
                default="Call the pre-configured API endpoint.",
                description="Description shown to the LLM.",
                type_options={"rows": 6},
            ),
            NodeProperty(
                display_name="URL",
                name="url",
                type="string",
                default="",
                required=True,
                description="The endpoint URL (supports {{ $execution.id }} etc.).",
            ),
            NodeProperty(
                display_name="HTTP Method",
                name="method",
                type="string",
                default="POST",
                description="GET / POST / PUT / PATCH / DELETE.",
            ),
            NodeProperty(
                display_name="Static Headers",
                name="static_headers",
                type="json",
                default={"Content-Type": "application/json"},
                description="JSON object of headers always sent.",
            ),
            NodeProperty(
                display_name="Static Body",
                name="static_body",
                type="json",
                default={},
                description=(
                    "JSON object merged into the LLM-supplied body before "
                    "sending."
                ),
            ),
            NodeProperty(
                display_name="Input Schema",
                name="input_schema",
                type="json",
                default={
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
                description="JSON Schema for the body the LLM provides.",
            ),
            NodeProperty(
                display_name="Allow Private Hosts",
                name="allow_private_hosts",
                type="boolean",
                default=True,
                description="Set False to enforce SSRF blocking of private IPs.",
            ),
            NodeProperty(
                display_name="Timeout (s)",
                name="timeout_seconds",
                type="number",
                default=_DEFAULT_TIMEOUT_SECONDS,
            ),
        ],
    )

    def get_config(self, node_definition: NodeDefinition) -> dict[str, Any]:
        tool_name = self.get_parameter(node_definition, "tool_name", "api_request")
        description = self.get_parameter(
            node_definition,
            "tool_description",
            "Call the pre-configured API endpoint.",
        )
        url = self.get_parameter(node_definition, "url", "")
        method = (
            self.get_parameter(node_definition, "method", "POST") or "POST"
        ).upper()
        static_headers = self.get_parameter(
            node_definition, "static_headers", {"Content-Type": "application/json"}
        )
        static_body = self.get_parameter(node_definition, "static_body", {})
        input_schema = self.get_parameter(
            node_definition,
            "input_schema",
            {"type": "object", "properties": {}, "required": []},
        )
        allow_private = bool(
            self.get_parameter(node_definition, "allow_private_hosts", True)
        )
        timeout_seconds = float(
            self.get_parameter(node_definition, "timeout_seconds", _DEFAULT_TIMEOUT_SECONDS)
            or _DEFAULT_TIMEOUT_SECONDS
        )

        # Tolerate JSON-string inputs.
        for var_name, var in (("static_headers", static_headers), ("static_body", static_body), ("input_schema", input_schema)):
            if isinstance(var, str):
                try:
                    parsed = json.loads(var)
                except ValueError:
                    parsed = {}
                if var_name == "static_headers":
                    static_headers = parsed
                elif var_name == "static_body":
                    static_body = parsed
                else:
                    input_schema = parsed

        if not isinstance(static_headers, dict):
            static_headers = {"Content-Type": "application/json"}
        if not isinstance(static_body, dict):
            static_body = {}
        if not isinstance(input_schema, dict):
            input_schema = {"type": "object", "properties": {}, "required": []}

        async def execute(
            input_data: dict[str, Any], context: ExecutionContext
        ) -> dict[str, Any]:
            return await _execute_api_request(
                input_data,
                context,
                url=url,
                method=method,
                static_headers=dict(static_headers),
                static_body=dict(static_body),
                allow_private=allow_private,
                timeout_seconds=timeout_seconds,
            )

        return {
            "name": tool_name,
            "description": description,
            "input_schema": input_schema,
            "execute": execute,
        }


async def _execute_api_request(
    input_data: dict[str, Any],
    context: ExecutionContext,
    *,
    url: str,
    method: str,
    static_headers: dict[str, str],
    static_body: dict[str, Any],
    allow_private: bool,
    timeout_seconds: float,
) -> dict[str, Any]:
    import httpx

    if not url:
        return {"error": "tool misconfigured: url is empty"}

    if not allow_private:
        host = urlparse(url).hostname
        if _looks_private(host):
            return {"error": "private host blocked"}

    # Merge static_body + LLM-supplied input_data. LLM keys win.
    payload: dict[str, Any] = {**static_body}
    if isinstance(input_data, dict):
        payload.update(input_data)

    owned = False
    client = context.http_client
    if client is None:
        client = httpx.AsyncClient(timeout=timeout_seconds)
        owned = True
    try:
        kwargs: dict[str, Any] = {"headers": static_headers, "timeout": timeout_seconds}
        if method in ("POST", "PUT", "PATCH"):
            kwargs["json"] = payload
        elif method == "GET" and payload:
            kwargs["params"] = payload
        response = await client.request(method, url, **kwargs)
        content_type = response.headers.get("content-type", "")
        if "json" in content_type:
            try:
                body = response.json()
            except Exception:
                body = response.text
        else:
            body = response.text
        return {
            "status": response.status_code,
            "headers": dict(response.headers),
            "body": body,
        }
    except httpx.TimeoutException:
        return {"error": "Request timed out"}
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)}
    finally:
        if owned:
            await client.aclose()
