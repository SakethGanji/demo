"""Pure graph helpers shared by validation and partial runs (no I/O)."""

from __future__ import annotations

from typing import Any, Iterable

TRIGGER_NODE_TYPES = {"Start", "Webhook", "Cron", "ExecuteWorkflowTrigger", "ErrorTrigger", "FormTrigger", "ChatInput"}


def disabled_trigger_message(nodes: Iterable[Any]) -> str | None:
    """Validation message for the first disabled trigger node, or None when there isn't one.

    Works on NodeDefinition and NodeDefinitionSchema alike (needs .name, .type, .disabled).
    """
    for n in nodes:
        if n.type in TRIGGER_NODE_TYPES and getattr(n, "disabled", False):
            return f"Trigger node '{n.name}' is disabled"
    return None


def upstream_connections(connections: Iterable[Any], name: str) -> list[Any]:
    """Connections that feed `name`, in definition order."""
    return [c for c in connections if c.target_node == name]


def ancestors(connections: Iterable[Any], name: str) -> list[str]:
    """Every node that can reach `name` (excluding `name` itself), in discovery order."""
    conns = list(connections)
    seen: dict[str, None] = {}
    todo = [name]
    while todo:
        cur = todo.pop(0)
        for c in conns:
            if c.target_node == cur and c.source_node != name and c.source_node not in seen:
                seen[c.source_node] = None
                todo.append(c.source_node)
    return list(seen)
