"""Shared types for tool connectors.

A *connector* is a registered remote tool surface (an MCP server today, an
OpenAPI spec later). Discovery turns it into a :class:`ConnectorManifest` of
:class:`ToolManifestEntry` rows; selection decides which of those rows an agent
is allowed to see; :mod:`.registry` turns selected rows back into the engine's
one and only tool shape::

    {"name": str, "description": str, "input_schema": dict,
     "execute": async callable(input_data: dict, context) -> Any}

Nothing here imports the engine. The connector layer is deliberately a leaf:
the engine calls into it, never the other way round.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any


class ConnectorError(Exception):
    """A connector could not be reached, or answered in a way we cannot use.

    Carries an HTTP-ish ``status`` so the route layer can map it without
    re-deriving the cause from the message text.
    """

    def __init__(self, message: str, *, status: int = 502, detail: Any = None) -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.detail = detail

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.message


@dataclass(frozen=True, slots=True)
class CallerIdentity:
    """Who a connector call acts as.

    The analytics MCP server authenticates with a bare ``X-User-Id`` header
    (plus an optional ``X-Team-Id``); other servers may want a bearer token.
    Both are expressed here so a connector never has to know which it is
    talking to.
    """

    user_id: str | None = None
    team_id: str | None = None
    extra_headers: dict[str, str] = field(default_factory=dict)

    def headers(self) -> dict[str, str]:
        out: dict[str, str] = {}
        if self.user_id:
            out["X-User-Id"] = self.user_id
        if self.team_id:
            out["X-Team-Id"] = self.team_id
        out.update({k: v for k, v in self.extra_headers.items() if v is not None})
        return out


@dataclass
class ToolManifestEntry:
    """One discovered tool, in the form the ``connector_tools`` table stores.

    ``optional_args`` is the load-bearing field. It is captured here, at
    discovery time, from the *pristine* remote schema — before
    ``harden_schema(provider="openai")`` rewrites ``required`` to list every
    property. Without it there is no way, at call time, to tell a value the
    model meant from a filler it was forced to invent.
    """

    remote_id: str
    tool_name: str
    description: str = ""
    input_schema: dict[str, Any] = field(default_factory=dict)
    optional_args: list[str] = field(default_factory=list)
    invoke: dict[str, Any] = field(default_factory=dict)
    read_only: bool = True
    unsupported_reason: str | None = None
    schema_hash: str = ""
    est_tokens: int = 0
    annotations: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.schema_hash:
            self.schema_hash = schema_digest(self.description, self.input_schema)
        if not self.est_tokens:
            self.est_tokens = estimate_tokens(
                self.tool_name, self.description, self.input_schema
            )

    def to_row(self) -> dict[str, Any]:
        return {
            "remote_id": self.remote_id,
            "tool_name": self.tool_name,
            "description": self.description,
            "input_schema": self.input_schema,
            "optional_args": list(self.optional_args),
            "invoke": self.invoke,
            "read_only": self.read_only,
            "unsupported_reason": self.unsupported_reason,
            "schema_hash": self.schema_hash,
            "est_tokens": self.est_tokens,
        }


@dataclass
class ConnectorManifest:
    """Everything one discovery run learned about a connector."""

    connector_id: str
    name: str
    kind: str
    base_url: str
    tools: list[ToolManifestEntry] = field(default_factory=list)
    instructions: str | None = None
    server_info: dict[str, Any] = field(default_factory=dict)
    protocol_version: str | None = None
    source_hash: str = ""

    def __post_init__(self) -> None:
        if not self.source_hash:
            self.source_hash = hashlib.sha256(
                json.dumps(
                    sorted(t.schema_hash + "|" + t.remote_id for t in self.tools)
                ).encode()
            ).hexdigest()[:32]

    @property
    def est_tokens(self) -> int:
        return sum(t.est_tokens for t in self.tools)


def schema_digest(description: str, schema: dict[str, Any]) -> str:
    """Stable hash of the parts of a tool a re-discovery might change."""
    blob = json.dumps(
        {"d": description or "", "s": schema or {}}, sort_keys=True, default=str
    )
    return hashlib.sha256(blob.encode()).hexdigest()[:32]


def estimate_tokens(name: str, description: str, schema: dict[str, Any]) -> int:
    """Rough serialized cost of a tool definition, in tokens.

    Every selected tool's schema is serialized into context on *every* model
    call, so this is the number a selection UI has to show. ~4 chars/token is
    the usual English approximation and is good enough to rank by.
    """
    blob = (name or "") + (description or "") + json.dumps(schema or {}, default=str)
    return max(1, len(blob) // 4)
