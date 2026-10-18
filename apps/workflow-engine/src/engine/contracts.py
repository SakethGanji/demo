"""The checker's single entry point for "what does this node take and give?" (Stage 2).

``node_contract(node_def, inputs, *, table_columns=None) -> NodeContract`` resolves, in order:

1. a **disabled** node is a passthrough (it runs as one: its input items go out on ``main`` unchanged;
   a disabled Merge passes through everything it received) -- except a disabled n8n-import placeholder
   (instance contract with ``source == "import_placeholder"``), whose output is the override's (UNKNOWN);
2. otherwise the node class' ``BaseNode.contract(params, inputs, lookups)``;
3. an approved instance ``contract`` override then replaces the output ports it names
   (``x-source`` is stamped on shapes that lack one: ``"declared"`` when the override's source is declared, else ``"observed"``);
4. ``on_error == "error_output"`` adds an ``error`` output port: the input item plus
   ``error: {message, node}``.

Where context comes from
------------------------
``inputs`` maps input port -> item Shape arriving there (the checker builds it from upstream contracts).
Single-input nodes read ``inputs["main"]``; Merge reads every value, in dict order.
``BaseNode.contract(params, inputs, lookups=None)`` takes an optional third argument: a dict of
checker-supplied context. Today it holds ``{"table_columns": {table_name: [column dict, ...]}}``
(``DataTableModel.columns`` format) — that is how DataTable gets its *declared* shape without approval.
Callers pass it through ``node_contract(..., table_columns=...)``; ``{"api_operation": Operation}`` is added
the same way (``node_contract(..., api_operation=...)``) for an HttpRequest with ``api_ref``. A table that isn't in the mapping (or
a non-literal ``table`` param) leaves DataTable UNKNOWN.

Unknown node types never raise: their outputs are ``{"main": UNKNOWN}``.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from typing import Any

from .node_registry import node_registry
from .shapes import (
    NodeContract,
    Shape,
    input_shape,
    make_contract,
    merge_all,
    unknown,
    with_source,
)
from .types import NodeDefinition

__all__ = ["node_contract", "NodeContract"]


def node_contract(
    node_def: NodeDefinition,
    inputs: dict[str, Shape],
    *,
    table_columns: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
    api_operation: Mapping[str, Any] | None = None,
) -> NodeContract:
    """Contract of one node instance. Pure; never raises for odd parameters or unknown node types.

    ``api_operation`` is the catalog Operation an HttpRequest ``api_ref`` points at (the checker looks it up);
    it makes the node's ``main`` output a ``spec`` shape: ``{statusCode, headers, body: <first 2xx reply>}``.
    """
    inputs = inputs or {}
    override = getattr(node_def, "contract", None)
    placeholder = bool(override) and override.get("source") == "import_placeholder"
    if node_def.disabled and not placeholder:
        contract = make_contract(inputs, {"main": _disabled_passthrough(inputs)})
    else:
        # a disabled n8n-import placeholder (unmapped node / Code node) has no known output, so it is not a passthrough
        contract = make_contract(inputs, {"main": unknown()}) if node_def.disabled else _type_contract(node_def, inputs, table_columns, api_operation)
        if override and isinstance(override.get("outputs"), dict):
            for port, shape in override["outputs"].items():
                if isinstance(shape, dict):
                    stamp = "declared" if override.get("source") == "declared" else "observed"
                    contract["outputs"][port] = with_source(shape, stamp, only_missing=True)

    if node_def.on_error == "error_output":
        contract["outputs"]["error"] = copy.deepcopy(contract["error"])
    return contract


def _disabled_passthrough(inputs: dict[str, Shape]) -> Shape:
    return merge_all(inputs.values()) if len(inputs) > 1 else input_shape(inputs)


def _type_contract(
    node_def: NodeDefinition,
    inputs: dict[str, Shape],
    table_columns: Mapping[str, Sequence[Mapping[str, Any]]] | None,
    api_operation: Mapping[str, Any] | None = None,
) -> NodeContract:
    if not node_registry.has(node_def.type):
        return make_contract(inputs, {"main": unknown()})
    lookups: dict[str, Any] | None = {"table_columns": table_columns} if table_columns else None
    if api_operation:
        lookups = {**(lookups or {}), "api_operation": api_operation}
    try:
        return node_registry.get(node_def.type).contract(node_def.parameters or {}, inputs, lookups)
    except Exception:  # a contract must never break the checker; fall back to "can't tell"
        node = node_registry.get(node_def.type)
        ports = node.output_ports(node_def.parameters or {})
        return make_contract(inputs, {p: unknown() for p in ports})
