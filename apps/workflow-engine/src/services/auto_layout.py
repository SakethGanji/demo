"""Deterministic node placement for AI-built workflows (Stage 6, DESIGN-STAGE6-AI-BUILDER section 6.7).

Longest-path layering from the roots (triggers first): ``x = X_STEP * layer``, ``y = Y_STEP * index`` within the layer, with
the order inside a layer taken from the order of the parent's output ports (an If's ``true`` above its ``false``).
Compact on purpose (a 64 px tile plus its label): a chain of more than ``MAX_COLS`` layers wraps into a new band below.
The studio's blueprint (``stage/blueprint.ts``) ports these rules and constants; keep the two in step.
Nodes that already have a place keep it; only added nodes are placed, in the first free slot of their layer.
Pure, no I/O, same input -> same output.
"""

from __future__ import annotations

import copy
from collections import defaultdict, deque
from typing import Any, Callable, Iterable

X_STEP = 190
Y_STEP = 110
MAX_COLS = 7  # layers per band; a longer chain wraps into the next band
BAND_GAP = 70  # extra vertical space between two bands
_FREE_Y = 60  # a kept node closer than this (vertically, in the same column) occupies the slot


def _default_ports(node_type: str, params: dict[str, Any] | None) -> list[str]:
    try:
        from ..engine.node_registry import node_registry

        if node_registry.has(node_type):
            return list(node_registry.get(node_type).output_ports(params or {}))
    except Exception:  # a layout must never fail on an exotic node
        pass
    return ["main"]


def has_place(node: dict[str, Any]) -> bool:
    """True when the node carries a real position (a missing one, or the {0, 0} that ``add_node`` sets, is 'unplaced')."""
    pos = node.get("position")
    if not isinstance(pos, dict) or "x" not in pos or "y" not in pos:
        return False
    try:
        return not (float(pos["x"]) == 0 and float(pos["y"]) == 0)
    except (TypeError, ValueError):
        return False


def layers_of(defn: dict[str, Any]) -> dict[str, int]:
    """Longest-path layer of every node (roots 0). Nodes on a cycle get the layer their acyclic predecessors imply."""
    names = [n["name"] for n in defn.get("nodes") or [] if isinstance(n, dict) and n.get("name")]
    known = set(names)
    edges = _edges(defn, known)
    incoming: dict[str, list[str]] = defaultdict(list)
    outgoing: dict[str, list[str]] = defaultdict(list)
    for s, _, t, _ in edges:
        incoming[t].append(s)
        outgoing[s].append(t)
    indeg = {n: len(incoming[n]) for n in names}
    layer: dict[str, int] = {}
    queue = deque(n for n in names if indeg[n] == 0)
    for n in queue:
        layer[n] = 0
    done: set[str] = set()
    while queue:
        n = queue.popleft()
        done.add(n)
        for t in outgoing[n]:
            layer[t] = max(layer.get(t, 0), layer[n] + 1)
            indeg[t] -= 1
            if indeg[t] == 0:
                queue.append(t)
    for n in names:  # cycles: one node at a time, after the predecessors that already have a layer, else the first column
        if n not in done:
            layer[n] = max([layer[p] + 1 for p in incoming[n] if p in layer and p != n] or [0])
            done.add(n)
    return layer


def _edges(defn: dict[str, Any], known: set[str]) -> list[tuple[str, str, str, str]]:
    seen: set[tuple[str, str, str, str]] = set()
    out: list[tuple[str, str, str, str]] = []
    for c in defn.get("connections") or []:
        if not isinstance(c, dict):
            continue
        e = (c.get("source_node"), c.get("source_output", "main"), c.get("target_node"), c.get("target_input", "main"))
        if e[0] in known and e[2] in known and e[0] != e[2] and e not in seen:  # type: ignore[comparison-overlap]
            seen.add(e)  # type: ignore[arg-type]
            out.append(e)  # type: ignore[arg-type]
    return out


def auto(
    defn: dict[str, Any], keep: Iterable[str] | None = None, ports_of: Callable[[str, dict[str, Any] | None], list[str]] | None = None,
) -> dict[str, Any]:
    """A copy of ``defn`` with every node not in ``keep`` placed.

    ``keep`` names the nodes that keep their position (for a modify proposal: the base's nodes). Default: the nodes that
    already have a real position. A kept node with no position at all is placed anyway.
    """
    out = copy.deepcopy(defn)
    nodes = [n for n in out.get("nodes") or [] if isinstance(n, dict) and n.get("name")]
    if not nodes:
        return out
    ports_of = ports_of or _default_ports
    keep_set = {n["name"] for n in nodes if has_place(n)} if keep is None else set(keep)
    keep_set = {n["name"] for n in nodes if n["name"] in keep_set and isinstance(n.get("position"), dict)
                and "x" in n["position"] and "y" in n["position"]}
    by_name = {n["name"]: n for n in nodes}
    order = {n["name"]: i for i, n in enumerate(nodes)}
    layer = layers_of(out)
    edges = _edges(out, set(by_name))

    def port_rank(src: str, port: str) -> int:
        ports = ports_of(by_name[src].get("type", ""), by_name[src].get("parameters"))
        return ports.index(port) if port in ports else len(ports)

    # order of the nodes inside each column: by the earliest parent (already ordered), then its port, then definition order
    columns: dict[int, list[str]] = defaultdict(list)
    position_in_col: dict[str, int] = {}
    for lyr in sorted(set(layer.values())):
        members = [n["name"] for n in nodes if layer[n["name"]] == lyr]

        def key(name: str) -> tuple[Any, ...]:
            parents = [(layer[s], position_in_col[s], port_rank(s, sp), i)
                       for i, (s, sp, t, _) in enumerate(edges) if t == name and s in position_in_col]
            trigger_first = 0 if by_name[name].get("type") in _TRIGGERS else 1
            return (trigger_first, min(parents) if parents else (10**9, 0, 0, 0), order[name])

        members.sort(key=key)
        for idx, name in enumerate(members):
            position_in_col[name] = idx
        columns[lyr] = members

    # wrapped bands: layer L sits in column L % MAX_COLS of band L // MAX_COLS; a band starts below the tallest column above it
    band_top: dict[int, int] = {0: 0}
    for b in range(1, max(columns) // MAX_COLS + 1):
        tallest = max([len(m) for lyr, m in columns.items() if lyr // MAX_COLS == b - 1] or [1])
        band_top[b] = band_top[b - 1] + Y_STEP * (tallest - 1) + Y_STEP + BAND_GAP

    taken: dict[int, list[float]] = defaultdict(list)  # column x -> y of nodes already standing there
    for name in keep_set:
        pos = by_name[name]["position"]
        taken[round(float(pos["x"]))].append(float(pos["y"]))
    for lyr in sorted(columns):
        x = X_STEP * (lyr % MAX_COLS)
        top = band_top[lyr // MAX_COLS]
        for idx, name in enumerate(columns[lyr]):
            if name in keep_set:
                continue
            slot = idx
            while any(abs(y - (top + Y_STEP * slot)) < _FREE_Y for y in taken[x]):
                slot += 1
            by_name[name]["position"] = {"x": x, "y": top + Y_STEP * slot}
            taken[x].append(top + Y_STEP * slot)
    return out


def _triggers() -> frozenset[str]:
    from ..engine.graph import TRIGGER_NODE_TYPES

    return frozenset(TRIGGER_NODE_TYPES)


_TRIGGERS = _triggers()
