"""Tool connectors: remote tool surfaces an agent can be given access to.

    register  ->  discover  ->  select  ->  resolve

``register`` stores a URL and its headers. ``discover`` speaks the remote
protocol, flattens every schema into something four LLM providers accept, and
writes one row per tool — all of them **unselected**. ``select`` is a human
decision. ``resolve`` turns the selected rows into the engine's tool dicts.

The public surface is deliberately small; everything else is an implementation
detail of one transport.
"""

from .base import (
    CallerIdentity,
    ConnectorError,
    ConnectorManifest,
    ToolManifestEntry,
    estimate_tokens,
    schema_digest,
)
from .egress import check_egress
from .envelope import MAX_RESULT_CHARS, cap_text, error_envelope, shape_result
from .naming import assign_tool_names, derive_tool_name, is_valid_tool_name
from .schema_flatten import flatten_schema, optional_args, schema_issues

__all__ = [
    "CallerIdentity",
    "ConnectorError",
    "ConnectorManifest",
    "ToolManifestEntry",
    "MAX_RESULT_CHARS",
    "assign_tool_names",
    "cap_text",
    "check_egress",
    "derive_tool_name",
    "error_envelope",
    "estimate_tokens",
    "flatten_schema",
    "is_valid_tool_name",
    "optional_args",
    "schema_digest",
    "schema_issues",
    "shape_result",
]
