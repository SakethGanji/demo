"""The MCP connector: discovery, and turning discovered rows into engine tools."""

from __future__ import annotations

import logging
from copy import deepcopy
from typing import Any, Callable

from ..base import (
    CallerIdentity,
    ConnectorError,
    ConnectorManifest,
    ToolManifestEntry,
)
from ..envelope import error_envelope, shape_result
from ..naming import assign_tool_names
from ..schema_flatten import SchemaFlattenError, flatten_schema, optional_args
from .jsonrpc import MCPHttpClient, MCPProtocolError

logger = logging.getLogger(__name__)

#: Used only when a server publishes no ``annotations``. Advisory: it drives a
#: badge in the selection UI, never a permission decision.
_MUTATING_PREFIXES = (
    "write_", "create_", "update_", "delete_", "remove_", "insert_", "upsert_",
    "publish_", "manage_", "set_", "save_", "promote_", "transform_", "import_",
    "add_", "put_", "patch_", "send_", "apply_",
)


def _looks_mutating(name: str) -> bool:
    lowered = (name or "").lower()
    return lowered.startswith(_MUTATING_PREFIXES)


class MCPConnector:
    """One registered MCP server.

    Holds no state a workflow execution depends on: the JSON-RPC client is
    cached per HTTP client so that N tool calls in one agent turn share a single
    handshake, and is rebuilt if the engine hands us a different client.
    """

    kind = "mcp"

    def __init__(
        self,
        *,
        connector_id: str,
        name: str,
        base_url: str,
        headers: dict[str, str] | None = None,
        tool_prefix: str = "",
        config: dict[str, Any] | None = None,
        identity: CallerIdentity | None = None,
    ) -> None:
        self.connector_id = connector_id
        self.name = name
        self.base_url = base_url
        self.headers = dict(headers or {})
        self.tool_prefix = tool_prefix or ""
        self.config = dict(config or {})
        self.identity = identity or CallerIdentity()
        self._sessions: dict[int, MCPHttpClient] = {}

    # -- configuration ----------------------------------------------------

    @property
    def allow_private(self) -> bool:
        return bool(self.config.get("allow_private_hosts", True))

    @property
    def timeout(self) -> float:
        try:
            return float(self.config.get("timeout_seconds") or 60.0)
        except (TypeError, ValueError):
            return 60.0

    def _request_headers(self, identity: CallerIdentity | None) -> dict[str, str]:
        merged = dict(self.headers)
        merged.update((identity or self.identity).headers())
        return {k: str(v) for k, v in merged.items() if v is not None}

    def client(
        self, *, identity: CallerIdentity | None = None, http_client: Any | None = None
    ) -> MCPHttpClient:
        """A JSON-RPC client, reused across calls that share an HTTP client."""
        key = id(http_client) if http_client is not None else 0
        existing = self._sessions.get(key)
        if existing is not None:
            return existing
        client = MCPHttpClient(
            self.base_url,
            headers=self._request_headers(identity),
            http_client=http_client,
            timeout=self.timeout,
            allow_private=self.allow_private,
        )
        self._sessions[key] = client
        return client

    async def aclose(self) -> None:
        for client in self._sessions.values():
            await client.aclose()
        self._sessions.clear()

    # -- discovery --------------------------------------------------------

    async def discover(
        self,
        *,
        identity: CallerIdentity | None = None,
        http_client: Any | None = None,
        pinned: dict[str, str] | None = None,
    ) -> ConnectorManifest:
        """Handshake, list every tool, and flatten each schema.

        ``pinned`` maps ``remote_id -> tool_name`` for tools already imported.
        A re-discovery must never rename them.
        """
        client = MCPHttpClient(
            self.base_url,
            headers=self._request_headers(identity),
            http_client=http_client,
            timeout=self.timeout,
            allow_private=self.allow_private,
        )
        try:
            await client.initialize()
            raw_tools = await client.list_tools()
        finally:
            if http_client is None:
                await client.aclose()

        remote_ids = [str(t.get("name") or "") for t in raw_tools if t.get("name")]
        names = assign_tool_names(
            remote_ids, prefix=self.tool_prefix, pinned=pinned or {}
        )

        entries: list[ToolManifestEntry] = []
        for raw in raw_tools:
            remote_id = str(raw.get("name") or "")
            if not remote_id:
                continue
            entries.append(self._entry(raw, remote_id, names[remote_id]))

        return ConnectorManifest(
            connector_id=self.connector_id,
            name=self.name,
            kind=self.kind,
            base_url=self.base_url,
            tools=entries,
            # Kept verbatim. A server's `instructions` is the only place it can
            # say how its tools fit together ("search_datasets first — no other
            # tool returns a dataset_id"), and it is worth more to an agent than
            # any 27 individual descriptions.
            instructions=client.instructions,
            server_info=client.server_info,
            protocol_version=client.protocol_version,
        )

    def _entry(self, raw: dict[str, Any], remote_id: str, tool_name: str) -> ToolManifestEntry:
        raw_schema = raw.get("inputSchema") or raw.get("input_schema") or {}
        unsupported: str | None = None
        try:
            schema = flatten_schema(raw_schema)
        except (SchemaFlattenError, RecursionError, ValueError) as exc:
            logger.warning("connector %s: tool %s has an unusable schema: %s",
                           self.name, remote_id, exc)
            schema = {"type": "object", "properties": {}}
            unsupported = f"input schema could not be flattened: {exc}"

        description = (raw.get("description") or raw.get("title") or "").strip()
        if not description:
            # validate_tool_definition warns on an empty description, and a
            # model handed a nameless verb guesses. Neither is acceptable.
            description = f"{remote_id} — a tool provided by the {self.name} connector."

        annotations = raw.get("annotations") if isinstance(raw.get("annotations"), dict) else {}
        read_only_hint = annotations.get("readOnlyHint")
        if isinstance(read_only_hint, bool):
            read_only = read_only_hint
        else:
            read_only = not _looks_mutating(remote_id)

        return ToolManifestEntry(
            remote_id=remote_id,
            tool_name=tool_name,
            description=description,
            input_schema=schema,
            optional_args=optional_args(schema),
            invoke={
                "transport": "mcp",
                "method": "tools/call",
                "remote_name": remote_id,
                "endpoint": self.base_url,
            },
            read_only=read_only,
            unsupported_reason=unsupported,
            annotations=annotations or {},
        )

    # -- execution --------------------------------------------------------

    def prune_arguments(
        self, input_data: Any, entry: ToolManifestEntry
    ) -> dict[str, Any]:
        """Drop the fillers strict mode forced the model to invent.

        ``harden_schema(schema, "openai")`` sets ``required = list(properties)``
        — every optional argument becomes mandatory to *the model*, which then
        supplies ``""`` or ``null`` for the ones it does not care about. The
        remote server has no way to tell those from a deliberate empty value:
        ``search_datasets(query="sales", dataset_id="")`` is a different
        question from ``search_datasets(query="sales")``, and it answers the one
        it was asked.

        So: an argument that the *remote* schema marked optional and whose value
        is null or blank is removed. Anything else is passed through exactly as
        the model wrote it — including keys the server never declared, which its
        own argument guard rejects with a message the model can act on. Renaming
        or inventing a key here would surface as an unexplained failure.
        """
        if not isinstance(input_data, dict):
            return {}
        optional = set(entry.optional_args or [])
        pruned: dict[str, Any] = {}
        dropped: list[str] = []
        for key, value in input_data.items():
            if key in optional:
                if value is None or (isinstance(value, str) and not value.strip()):
                    dropped.append(key)
                    continue
            pruned[key] = value
        if dropped:
            logger.debug(
                "connector %s: dropped empty optional args for %s: %s",
                self.name, entry.remote_id, ", ".join(dropped),
            )
        return pruned

    def build_tools(
        self,
        entries: list[ToolManifestEntry],
        *,
        identity: CallerIdentity | None = None,
    ) -> list[dict[str, Any]]:
        """Turn manifest rows into the engine's tool dicts.

        The ``input_schema`` is deep-copied on the way out. It has to be:
        ``ensure_complete_schema`` shallow-copies the root and then writes
        ``prop_def["type"] = "string"`` *through* into the nested property dicts
        it was handed, and ``prepare_tools_for_provider`` runs on every single
        LLM call. Hand it a cached schema and the cache is silently mutated —
        after one Gemini turn, ``_normalize_schema_types`` has upper-cased types
        in the shared copy and every later call, for every provider, reads the
        corrupted version.
        """
        tools: list[dict[str, Any]] = []
        for entry in entries:
            if entry.unsupported_reason:
                logger.info(
                    "connector %s: skipping %s (%s)",
                    self.name, entry.tool_name, entry.unsupported_reason,
                )
                continue
            tools.append(
                {
                    "name": entry.tool_name,
                    "description": entry.description,
                    "input_schema": deepcopy(entry.input_schema),
                    "execute": self._make_execute(entry, identity),
                }
            )
        return tools

    def _make_execute(
        self, entry: ToolManifestEntry, identity: CallerIdentity | None
    ) -> Callable[..., Any]:
        connector = self

        async def execute(input_data: dict[str, Any], context: Any = None) -> Any:
            http_client = getattr(context, "http_client", None)
            arguments = connector.prune_arguments(input_data, entry)
            client = connector.client(identity=identity, http_client=http_client)
            try:
                result = await client.call_tool(entry.remote_id, arguments)
            except MCPProtocolError as exc:
                return error_envelope(str(exc), tool=entry.tool_name)
            except ConnectorError as exc:
                return error_envelope(exc.message, tool=entry.tool_name)
            except Exception as exc:  # noqa: BLE001 - a tool must never abort the turn
                import httpx

                if isinstance(exc, httpx.TimeoutException):
                    return error_envelope(
                        f"{entry.tool_name} timed out after {connector.timeout:.0f}s",
                        tool=entry.tool_name,
                    )
                logger.exception("connector %s: %s failed", connector.name, entry.tool_name)
                return error_envelope(f"{type(exc).__name__}: {exc}", tool=entry.tool_name)
            finally:
                if http_client is None:
                    # Nothing owns this client between calls; close it rather
                    # than leaking a connection pool per tool call.
                    await client.aclose()
                    connector._sessions.pop(0, None)
            return shape_result(result)

        execute.__name__ = f"execute_{entry.tool_name}"
        return execute
