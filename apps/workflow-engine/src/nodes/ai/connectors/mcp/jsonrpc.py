"""A thin streamable-HTTP JSON-RPC client for MCP servers.

**Why not the ``mcp`` SDK.** The engine has no MCP dependency, and the SDK's
client is an async context manager that owns a session for its lifetime:
``async with streamablehttp_client(url) as (r, w, _): async with ClientSession(...)``.
The engine's tool contract is a bare ``async def execute(input_data, context)``
called ad hoc from an agent loop, with no place to hang a session that outlives
one call — so using the SDK means either opening and tearing down a session per
tool call (two extra round trips each) or building a session-pool lifecycle the
engine has no seam for. Against a *stateless, JSON-response* server —
which is exactly what ``analytics-service/app/features/mcp/asgi.py`` mounts —
``tools/list`` and ``tools/call`` are one ``POST`` each. That is this file.

It is nonetheless a real MCP client, not a curl wrapper:

* the ``initialize`` -> ``notifications/initialized`` handshake runs once per
  client instance, behind a lock, and is cached;
* ``Mcp-Session-Id`` is echoed on every later request when the server issues
  one, so a *stateful* server works too — and a ``404`` on a request carrying a
  session id is treated as "session expired", per the spec: the session is
  dropped, the handshake re-run, and the request retried once;
* the negotiated ``MCP-Protocol-Version`` is sent on every subsequent request,
  as the 2025-06-18 spec requires;
* ``Accept`` lists both ``application/json`` and ``text/event-stream`` (servers
  are entitled to answer either, and FastMCP 406s if both are not offered), and
  an SSE body is parsed rather than handed back as text;
* ``tools/list`` follows ``nextCursor`` to the end.

Redirects are never followed implicitly — see :mod:`..egress`.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from ..base import ConnectorError
from ..egress import check_egress, same_origin

logger = logging.getLogger(__name__)

#: The revision this client implements. A server that speaks an older one
#: answers `initialize` with its own version and we adopt it.
PROTOCOL_VERSION = "2025-06-18"

CLIENT_NAME = "workflow-engine"
CLIENT_VERSION = "1.0.0"

DEFAULT_TIMEOUT = 60.0
MAX_REDIRECTS = 3
MAX_LIST_PAGES = 50


class MCPProtocolError(ConnectorError):
    """The server answered with a JSON-RPC ``error`` object."""

    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(message, status=502, detail=data)
        self.code = code
        self.data = data

    def __str__(self) -> str:
        base = f"MCP error {self.code}: {self.message}"
        if self.data:
            return f"{base} ({json.dumps(self.data, default=str)[:400]})"
        return base


def _parse_sse(body: str) -> list[dict[str, Any]]:
    """Pull JSON-RPC messages out of a ``text/event-stream`` body.

    Only the ``data:`` field matters; ``event:``/``id:``/``retry:`` lines and
    comments are skipped. Multi-line data fields are joined with newlines, as
    the EventSource format specifies.
    """
    messages: list[dict[str, Any]] = []
    buffer: list[str] = []

    def flush() -> None:
        if not buffer:
            return
        raw = "\n".join(buffer)
        buffer.clear()
        try:
            parsed = json.loads(raw)
        except ValueError:
            logger.debug("MCP: unparseable SSE data frame: %.200s", raw)
            return
        if isinstance(parsed, list):
            messages.extend(m for m in parsed if isinstance(m, dict))
        elif isinstance(parsed, dict):
            messages.append(parsed)

    for line in body.splitlines():
        if not line.strip():
            flush()
            continue
        if line.startswith(":"):
            continue
        field, _, value = line.partition(":")
        if field == "data":
            buffer.append(value[1:] if value.startswith(" ") else value)
    flush()
    return messages


class MCPHttpClient:
    """One streamable-HTTP MCP endpoint, plus its handshake state."""

    def __init__(
        self,
        endpoint: str,
        *,
        headers: dict[str, str] | None = None,
        http_client: Any | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        allow_private: bool = True,
        protocol_version: str = PROTOCOL_VERSION,
        client_name: str = CLIENT_NAME,
    ) -> None:
        self.endpoint = check_egress(endpoint, allow_private=allow_private)
        self.base_headers = {k: v for k, v in (headers or {}).items() if v is not None}
        self.timeout = timeout
        self.allow_private = allow_private
        self.requested_version = protocol_version
        self.client_name = client_name

        self._external_client = http_client
        self._owned_client: Any | None = None
        self._id = 0
        self._lock = asyncio.Lock()

        # Handshake state.
        self.session_id: str | None = None
        self.protocol_version: str | None = None
        self.server_info: dict[str, Any] = {}
        self.capabilities: dict[str, Any] = {}
        self.instructions: str | None = None
        self._initialized = False

    # -- plumbing ---------------------------------------------------------

    async def _client(self) -> Any:
        if self._external_client is not None:
            return self._external_client
        if self._owned_client is None:
            import httpx

            self._owned_client = httpx.AsyncClient(
                timeout=self.timeout, follow_redirects=False
            )
        return self._owned_client

    async def aclose(self) -> None:
        """Close only a client we created. The engine's shared one is not ours."""
        if self._owned_client is not None:
            await self._owned_client.aclose()
            self._owned_client = None

    async def __aenter__(self) -> "MCPHttpClient":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()

    def _next_id(self) -> int:
        self._id += 1
        return self._id

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            # Both, always. A server may answer either, and FastMCP rejects a
            # request that does not offer both with 406.
            "Accept": "application/json, text/event-stream",
            **self.base_headers,
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        if self.protocol_version:
            headers["MCP-Protocol-Version"] = self.protocol_version
        return headers

    async def _send(self, payload: dict[str, Any]) -> Any:
        """POST one JSON-RPC message and return the raw httpx response."""
        client = await self._client()
        url = self.endpoint
        for hop in range(MAX_REDIRECTS + 1):
            # follow_redirects is set PER REQUEST because the engine's shared
            # client (workflow_runner.py:156) is built with it enabled; a 302
            # would otherwise carry the request past check_egress entirely.
            response = await client.post(
                url,
                json=payload,
                headers=self._headers(),
                timeout=self.timeout,
                follow_redirects=False,
            )
            if response.status_code not in (301, 302, 303, 307, 308):
                return response
            location = response.headers.get("location")
            if not location or hop >= MAX_REDIRECTS:
                raise ConnectorError(
                    f"MCP endpoint redirected to {location or '(no Location)'} "
                    "and the redirect could not be followed",
                    status=502,
                )
            target = str(response.url.join(location))
            # Re-run the egress check on the hop, and refuse to leave the origin
            # the operator registered: a cross-origin redirect would also leak
            # the connector's auth headers to a host they were not issued for.
            check_egress(target, allow_private=self.allow_private)
            if not same_origin(self.endpoint, target):
                raise ConnectorError(
                    f"MCP endpoint redirected off-origin to {target}; refused",
                    status=502,
                )
            url = target
        raise ConnectorError("too many redirects from the MCP endpoint", status=502)

    def _read_message(self, response: Any, request_id: int) -> dict[str, Any]:
        content_type = (response.headers.get("content-type") or "").lower()
        text = response.text
        if "text/event-stream" in content_type:
            messages = _parse_sse(text)
        else:
            try:
                parsed = json.loads(text) if text.strip() else {}
            except ValueError as exc:
                raise ConnectorError(
                    f"MCP endpoint returned a non-JSON body ({content_type or 'no content-type'}): "
                    f"{text[:300]}",
                    status=502,
                ) from exc
            messages = parsed if isinstance(parsed, list) else [parsed]

        for message in messages:
            if isinstance(message, dict) and message.get("id") == request_id:
                return message
        # Some servers omit/renumber ids on a single-response stream; fall back
        # to the first message that actually carries a result or an error.
        for message in messages:
            if isinstance(message, dict) and ("result" in message or "error" in message):
                return message
        raise ConnectorError(
            f"MCP endpoint returned no JSON-RPC response for request {request_id}: "
            f"{text[:300]}",
            status=502,
        )

    async def _call(self, method: str, params: dict[str, Any] | None = None, *, _retry: bool = True) -> Any:
        request_id = self._next_id()
        payload: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            payload["params"] = params

        response = await self._send(payload)

        # Per spec, a 404 on a request carrying a session id means the session
        # is gone. Re-handshake once rather than failing a tool call the model
        # is waiting on.
        if response.status_code == 404 and self.session_id and _retry:
            logger.info("MCP session %s expired; re-initializing", self.session_id)
            self.session_id = None
            self._initialized = False
            await self.initialize()
            return await self._call(method, params, _retry=False)

        if response.status_code >= 400:
            raise ConnectorError(
                f"MCP endpoint returned HTTP {response.status_code} for {method}: "
                f"{response.text[:300]}",
                status=502 if response.status_code >= 500 else response.status_code,
            )

        new_session = response.headers.get("mcp-session-id")
        if new_session:
            self.session_id = new_session

        message = self._read_message(response, request_id)
        if "error" in message and message["error"] is not None:
            error = message["error"] or {}
            raise MCPProtocolError(
                int(error.get("code", -32603)),
                str(error.get("message", "unknown error")),
                error.get("data"),
            )
        return message.get("result")

    async def _notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        try:
            response = await self._send(payload)
        except ConnectorError:
            raise
        if response.status_code >= 400:
            # A notification that is refused is not fatal: a stateless server
            # has nothing to notify. Logged, not raised.
            logger.debug(
                "MCP notification %s returned HTTP %s", method, response.status_code
            )

    # -- protocol ---------------------------------------------------------

    async def initialize(self) -> dict[str, Any]:
        """Run (or return the cached result of) the MCP handshake."""
        if self._initialized:
            return {
                "protocolVersion": self.protocol_version,
                "serverInfo": self.server_info,
                "capabilities": self.capabilities,
                "instructions": self.instructions,
            }
        async with self._lock:
            if self._initialized:
                return {
                    "protocolVersion": self.protocol_version,
                    "serverInfo": self.server_info,
                    "capabilities": self.capabilities,
                    "instructions": self.instructions,
                }
            result = await self._call(
                "initialize",
                {
                    "protocolVersion": self.requested_version,
                    "capabilities": {},
                    "clientInfo": {"name": self.client_name, "version": CLIENT_VERSION},
                },
            )
            if not isinstance(result, dict):
                raise ConnectorError(
                    f"MCP initialize returned {type(result).__name__}, expected an object",
                    status=502,
                )
            # Adopt whatever the server named, per the version-negotiation rule.
            self.protocol_version = str(
                result.get("protocolVersion") or self.requested_version
            )
            self.server_info = result.get("serverInfo") or {}
            self.capabilities = result.get("capabilities") or {}
            instructions = result.get("instructions")
            self.instructions = instructions if isinstance(instructions, str) else None
            self._initialized = True

            await self._notify("notifications/initialized")
            return result

    async def list_tools(self) -> list[dict[str, Any]]:
        """Every tool the server exposes, following ``nextCursor`` to the end."""
        await self.initialize()
        tools: list[dict[str, Any]] = []
        cursor: str | None = None
        for page in range(MAX_LIST_PAGES):
            params: dict[str, Any] = {"cursor": cursor} if cursor else {}
            result = await self._call("tools/list", params)
            if not isinstance(result, dict):
                break
            batch = result.get("tools")
            if isinstance(batch, list):
                tools.extend(t for t in batch if isinstance(t, dict))
            cursor = result.get("nextCursor")
            if not cursor:
                return tools
            logger.debug("MCP tools/list page %d, cursor=%s", page + 1, cursor)
        logger.warning(
            "MCP tools/list stopped at %d pages (%d tools); server kept paging",
            MAX_LIST_PAGES, len(tools),
        )
        return tools

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Invoke one tool. Returns the raw ``CallToolResult``."""
        await self.initialize()
        result = await self._call("tools/call", {"name": name, "arguments": arguments})
        if isinstance(result, dict):
            return result
        return {"content": [{"type": "text", "text": str(result)}], "isError": False}

    async def ping(self) -> bool:
        await self._call("ping", {})
        return True
