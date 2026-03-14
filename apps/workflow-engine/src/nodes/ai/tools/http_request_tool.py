"""HTTP Request tool for AI agents."""

from __future__ import annotations

import ipaddress
import json
from typing import Any, TYPE_CHECKING
from urllib.parse import urlparse

from ...base import (
    NodeProperty,
    NodeTypeDescription,
)
from .._config_base import ConfigProvider

if TYPE_CHECKING:
    from ....engine.types import ExecutionContext, NodeDefinition


# IP ranges that should be blocked to prevent SSRF
_BLOCKED_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),  # Link-local / cloud metadata
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]


def _is_ssrf_target(url: str) -> bool:
    """Check if a URL targets a private/internal IP address."""
    try:
        hostname = urlparse(url).hostname
        if not hostname:
            return True
        # Resolve hostname to IP — check common dangerous hostnames directly
        if hostname in ("localhost", "metadata.google.internal"):
            return True
        addr = ipaddress.ip_address(hostname)
        return any(addr in net for net in _BLOCKED_NETWORKS)
    except ValueError:
        # hostname is a domain name, not an IP literal — allow it
        # (DNS rebinding is out of scope for this layer)
        return False


class HttpRequestToolNode(ConfigProvider):
    """HTTP Request tool - make HTTP calls as an agent tool action."""

    node_description = NodeTypeDescription(
        name="HttpRequestTool",
        display_name="HTTP Request Tool",
        description="Make HTTP requests as an agent tool",
        icon="fa:globe",
        group=["ai"],
        inputs=[],
        outputs=[],
        properties=[
            NodeProperty(
                display_name="Description",
                name="description",
                type="string",
                default=(
                    "Make an HTTP request. Provide url (required), method "
                    "(GET/POST/PUT/DELETE, default GET), headers (object), and body (string or object)."
                ),
                description="Description shown to the AI model",
                type_options={"rows": 3},
            ),
        ],
    )

    def get_config(self, node_definition: NodeDefinition) -> dict[str, Any]:
        """Return HTTP request tool configuration."""
        return {
            "name": "http_request",
            "description": self.get_parameter(
                node_definition,
                "description",
                "Make an HTTP request.",
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The URL to request",
                    },
                    "method": {
                        "type": "string",
                        "enum": ["GET", "POST", "PUT", "DELETE", "PATCH"],
                        "description": "HTTP method (default: GET)",
                    },
                    "headers": {
                        "type": "object",
                        "description": "Optional HTTP headers",
                    },
                    "body": {
                        "description": "Optional request body (string or JSON object)",
                    },
                },
                "required": ["url"],
            },
            # Async executor — receives (input_data, context)
            "execute": _execute_http_request,
        }


async def _execute_http_request(
    input_data: dict[str, Any], context: ExecutionContext
) -> dict[str, Any]:
    """Execute an HTTP request. Async executor called by AIAgentNode."""
    import httpx

    url = input_data.get("url", "")
    method = input_data.get("method", "GET").upper()
    headers = input_data.get("headers") or {}
    body = input_data.get("body")

    if not url:
        return {"error": "url is required"}

    # SSRF protection
    if _is_ssrf_target(url):
        return {"error": "Request to private/internal addresses is not allowed"}

    try:
        # Reuse the shared httpx client from context if available, otherwise create one
        owned_client = False
        client = context.http_client
        if client is None:
            client = httpx.AsyncClient(timeout=30.0)
            owned_client = True

        try:
            kwargs: dict[str, Any] = {"headers": headers}
            if body is not None and method in ("POST", "PUT", "PATCH"):
                if isinstance(body, (dict, list)):
                    kwargs["json"] = body
                else:
                    kwargs["content"] = str(body)

            response = await client.request(method, url, **kwargs)

            # Parse response body
            content_type = response.headers.get("content-type", "")
            if "json" in content_type:
                try:
                    resp_body = response.json()
                except Exception:
                    resp_body = response.text
            else:
                resp_body = response.text

            return {
                "status": response.status_code,
                "headers": dict(response.headers),
                "body": resp_body,
            }
        finally:
            if owned_client:
                await client.aclose()
    except httpx.TimeoutException:
        return {"error": "Request timed out"}
    except Exception as e:
        return {"error": str(e)}
