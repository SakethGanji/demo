"""The node catalog (Stage 3A, CONTRACTS-STAGE3A §7): every node type with what it takes, gives and how it is tested.

Generated from the node classes, the effects table and the test recipes, so prompts and the studio built on it
never go stale. ``GET /api/nodes/catalog`` serves ``build_node_catalog()``.
"""

from __future__ import annotations

from typing import Any

from ..engine import shapes as sh
from ..engine.effects import effect_of
from ..engine.node_registry import node_registry, register_all_nodes
from ..engine.test_recipes import recipe_for


def _contract_kind(instance: Any) -> str:
    """How the node's output type is known: ``derived`` / ``declared`` from its parameters, else ``observed`` from runs."""
    try:
        contract = instance.contract({}, {"main": sh.object_of({}, [], "derived")}, None)
    except Exception:
        return "observed"
    sources = [s.get("x-source") for s in (contract.get("outputs") or {}).values() if isinstance(s, dict) and not sh.is_unknown(s)]
    if any(s in ("declared", "spec") for s in sources):
        return "declared"
    if any(s == "derived" for s in sources):
        return "derived"
    return "observed"


def _example(info: Any) -> dict[str, Any]:
    """A node dict with every parameter at its default, enough to paste into a workflow."""
    params = {p["name"]: p.get("default") for p in info.properties if p.get("default") not in (None, "")}
    return {"name": info.display_name, "type": info.type, "parameters": params, "position": {"x": 0, "y": 0}}


def build_node_catalog() -> list[dict[str, Any]]:
    if not node_registry.list():
        register_all_nodes()
    out = []
    for info in node_registry.get_node_info_full():
        recipe = recipe_for(info.type)
        out.append({
            "type": info.type,
            "display_name": info.display_name,
            "effect": effect_of(info.type),
            "parameters": info.properties,
            "contract_kind": _contract_kind(node_registry.get(info.type)),
            "recipe": {"checks": list(recipe.checks), "categories": list(recipe.categories)},
            "example": _example(info),
        })
    return out
