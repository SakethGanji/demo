"""Planted bugs (Stage 3A, CONTRACTS-STAGE3A §7): small deliberate mistakes in copies of a workflow.

``generate_mutants(definition, seed=0, limit=30)`` returns mutants ``{id, operator, node, description, definition}``.
The scorecard runs the tests against each one; a mutant is "caught" when a test that passes on the original fails on it.

Operators:

1. ``flip_compare``: ``>``/``>=``, ``<``/``<=``, ``==``/``!=`` in an If, Filter or Switch condition. Handles the
   expression form (``{{ $json.amount > 500 }}``) and the structured form (``operation`` gt/gte/lt/lte/equals/notEquals,
   on the node itself for If and Filter, inside ``rules[]`` for Switch).
2. ``change_set_value``: a Set field value: number + 1, string + ``"_x"``, boolean negated.
3. ``swap_branches``: an If node's ``true`` and ``false`` connections trade places.
4. ``drop_node``: a non-trigger node is switched off (``disabled``).
5. ``break_expression``: one ``$json.x`` reference inside a ``{{ }}`` expression becomes ``None``.

Pure and deterministic: the same definition and seed always give the same mutants.
"""

from __future__ import annotations

import copy
import random
import re
from typing import Any

from ..engine.graph import TRIGGER_NODE_TYPES

MAX_MUTANTS = 30
OPERATORS = ("flip_compare", "change_set_value", "swap_branches", "drop_node", "break_expression")

_COMPARE = re.compile(r"(>=|<=|==|!=|(?<![-=<>!])>(?![=>])|(?<![-=<>!])<(?![=<]))")
_FLIP = {">": ">=", ">=": ">", "<": "<=", "<=": "<", "==": "!=", "!=": "=="}
_OP_FLIP = {"gt": "gte", "gte": "gt", "lt": "lte", "lte": "lt", "equals": "notEquals", "notEquals": "equals"}
_REF = re.compile(r"\$json(?:\.[A-Za-z_]\w*)+")
_COND_TYPES = ("If", "Filter", "Switch")
_NO_DROP = TRIGGER_NODE_TYPES | {"StickyNote"}
_SKIP_EXPR_TYPES = {"Code", "PythonStep", "StickyNote"}


def _nodes(defn: dict[str, Any]) -> list[dict[str, Any]]:
    return [n for n in defn.get("nodes") or [] if isinstance(n, dict) and n.get("name")]


def _mutant(op: str, node: str, description: str, defn: dict[str, Any], key: str, apply: Any) -> dict[str, Any]:
    """A candidate: ``apply(definition_copy)`` makes the change. The definition is built lazily by ``build``."""
    return {"operator": op, "node": node, "description": description, "key": key, "apply": apply}


def _node_of(defn: dict[str, Any], name: str) -> dict[str, Any]:
    return next(n for n in defn["nodes"] if n.get("name") == name)


def _params_at(node: dict[str, Any], path: tuple[Any, ...]) -> tuple[Any, Any]:
    """(container, last key) for a path into ``node["parameters"]``."""
    cur: Any = node["parameters"]
    for p in path[:-1]:
        cur = cur[p]
    return cur, path[-1]


def _flip_compare(defn: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for node in _nodes(defn):
        if node.get("type") not in _COND_TYPES or node.get("disabled"):
            continue
        name = node["name"]
        params = node.get("parameters") or {}
        # expression conditions (If/Filter "condition", Switch rules[].field when it holds an expression)
        spots: list[tuple[Any, ...]] = []
        if isinstance(params.get("condition"), str):
            spots.append(("condition",))
        for i, rule in enumerate(params.get("rules") or []):
            if isinstance(rule, dict) and isinstance(rule.get("field"), str) and _COMPARE.search(rule["field"]) and "{{" in rule["field"]:
                spots.append(("rules", i, "field"))
        for path in spots:
            container, key = _params_at(node, path)
            text = container[key]
            for m in _COMPARE.finditer(text):
                old, new = m.group(0), _FLIP[m.group(0)]

                def apply(d: dict[str, Any], name=name, path=path, span=m.span(), new=new) -> None:
                    c, k = _params_at(_node_of(d, name), path)
                    c[k] = c[k][: span[0]] + new + c[k][span[1]:]

                out.append(_mutant("flip_compare", name, f"In {name}, change '{old}' to '{new}' in the condition",
                                   defn, f"{name}|{'.'.join(map(str, path))}|{m.start()}", apply))
        # structured conditions
        op_spots: list[tuple[Any, ...]] = []
        if node.get("type") in ("If", "Filter") and params.get("operation") in _OP_FLIP:
            if not (node["type"] == "If" and params.get("condition")) and not (node["type"] == "Filter" and params.get("mode") == "expression"):
                op_spots.append(("operation",))
        for i, rule in enumerate(params.get("rules") or []):
            if isinstance(rule, dict) and rule.get("operation") in _OP_FLIP:
                op_spots.append(("rules", i, "operation"))
        for path in op_spots:
            container, key = _params_at(node, path)
            old, new = container[key], _OP_FLIP[container[key]]

            def apply_op(d: dict[str, Any], name=name, path=path, new=new) -> None:
                c, k = _params_at(_node_of(d, name), path)
                c[k] = new

            out.append(_mutant("flip_compare", name, f"In {name}, change the comparison '{old}' to '{new}'",
                               defn, f"{name}|{'.'.join(map(str, path))}", apply_op))
    return out


def _change_set_value(defn: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for node in _nodes(defn):
        if node.get("type") != "Set" or node.get("disabled"):
            continue
        name = node["name"]
        for i, field in enumerate((node.get("parameters") or {}).get("fields") or []):
            if not isinstance(field, dict) or "value" not in field:
                continue
            value = field["value"]
            if isinstance(value, bool):
                new, what = (not value), f"flip {value} to {not value}"
            elif isinstance(value, (int, float)):
                new, what = value + 1, f"change {value} to {value + 1}"
            elif isinstance(value, str):
                new, what = value + "_x", "append '_x'"
            else:
                continue

            def apply(d: dict[str, Any], name=name, i=i, new=new) -> None:
                _node_of(d, name)["parameters"]["fields"][i]["value"] = new

            out.append(_mutant("change_set_value", name, f"In {name}, {what} for field '{field.get('name', i)}'",
                               defn, f"{name}|{i}", apply))
    return out


def _swap_branches(defn: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for node in _nodes(defn):
        if node.get("type") != "If" or node.get("disabled"):
            continue
        name = node["name"]
        conns = [c for c in defn.get("connections") or [] if c.get("source_node") == name]
        if not any(c.get("source_output") in ("true", "false") for c in conns):
            continue

        def apply(d: dict[str, Any], name=name) -> None:
            for c in d.get("connections") or []:
                if c.get("source_node") == name:
                    if c.get("source_output") == "true":
                        c["source_output"] = "false"
                    elif c.get("source_output") == "false":
                        c["source_output"] = "true"

        out.append(_mutant("swap_branches", name, f"Swap the true and false branches of {name}", defn, name, apply))
    return out


def _drop_node(defn: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for node in _nodes(defn):
        if node.get("type") in _NO_DROP or node.get("disabled"):
            continue
        name = node["name"]

        def apply(d: dict[str, Any], name=name) -> None:
            _node_of(d, name)["disabled"] = True

        out.append(_mutant("drop_node", name, f"Switch off {name}", defn, name, apply))
    return out


def _string_paths(value: Any, prefix: tuple[Any, ...] = ()) -> list[tuple[tuple[Any, ...], str]]:
    found: list[tuple[tuple[Any, ...], str]] = []
    if isinstance(value, str):
        found.append((prefix, value))
    elif isinstance(value, dict):
        for k, v in value.items():
            found.extend(_string_paths(v, (*prefix, k)))
    elif isinstance(value, list):
        for i, v in enumerate(value):
            found.extend(_string_paths(v, (*prefix, i)))
    return found


def _break_expression(defn: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for node in _nodes(defn):
        if node.get("type") in _SKIP_EXPR_TYPES or node.get("disabled") or node.get("type") in TRIGGER_NODE_TYPES:
            continue
        name = node["name"]
        for path, text in _string_paths(node.get("parameters") or {}):
            if "{{" not in text:
                continue
            for m in _REF.finditer(text):
                ref = m.group(0)

                def apply(d: dict[str, Any], name=name, path=path, span=m.span()) -> None:
                    c, k = _params_at(_node_of(d, name), path)
                    c[k] = c[k][: span[0]] + "None" + c[k][span[1]:]

                out.append(_mutant("break_expression", name, f"In {name}, replace {ref} with None",
                                   defn, f"{name}|{'.'.join(map(str, path))}|{m.start()}", apply))
    return out


_GENERATORS = (_flip_compare, _change_set_value, _swap_branches, _drop_node, _break_expression)


def generate_mutants(definition: dict[str, Any], seed: int = 0, limit: int = MAX_MUTANTS) -> list[dict[str, Any]]:
    """Up to ``limit`` (max 30) planted bugs of ``definition``, chosen deterministically by ``seed``.

    Each mutant is ``{id, operator, node, description, definition}``; ``definition`` is a full copy with the one change.
    More candidates than ``limit`` are thinned out by a seeded sample (kept in generation order), spread so that
    every operator keeps a share.
    """
    limit = max(0, min(int(limit), MAX_MUTANTS))
    cands: list[dict[str, Any]] = []
    for gen in _GENERATORS:
        cands.extend(gen(definition))
    for i, c in enumerate(cands):
        c["order"] = i
    if len(cands) > limit:
        rng = random.Random(seed)
        by_op: dict[str, list[dict[str, Any]]] = {}
        for c in cands:
            by_op.setdefault(c["operator"], []).append(c)
        for lst in by_op.values():
            rng.shuffle(lst)
        picked: list[dict[str, Any]] = []
        while len(picked) < limit:  # round-robin over operators so none is crowded out
            for op in OPERATORS:
                if by_op.get(op) and len(picked) < limit:
                    picked.append(by_op[op].pop())
        cands = sorted(picked, key=lambda c: c["order"])
    out = []
    for c in cands:
        d = copy.deepcopy(definition)
        c["apply"](d)
        out.append({
            "id": f"{c['operator']}:{c['key']}", "operator": c["operator"], "node": c["node"],
            "description": c["description"], "definition": d,
        })
    return out
