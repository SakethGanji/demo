"""Test recipes per node type (Stage 3A, CONTRACTS-STAGE3A §7, HANDOFF-STAGE3 §4).

``recipe_for(node_type) -> Recipe`` where a ``Recipe`` has

* ``checks``: what tests of this node type check (shown in the node catalog);
* ``categories``: the grid columns that apply to the node type;
* ``suggest(node_def, shapes, ctx) -> [TestCase]``: draft tests (``status: "draft"``, ``source: "suggested"``)
  for one node. ``node_def`` is the node's raw dict from the definition, ``shapes`` is ``shapes_by_node`` of
  the checker, ``ctx`` a ``SuggestContext``.

Suggested tests must run as they are against the stored workflow:

* a boundary test of a condition is a *workflow* test when the field can be traced back to the trigger
  (through Set mappings and pass-through nodes), with a full trigger item; otherwise it is a *node* test fed
  with an item built for that node;
* expectations are predicted with the nodes' own condition code, so they are correct by construction;
* every draft has a stable id derived from its content, so suggesting twice never duplicates.

Pure: no I/O. AI/LLM node types have an empty recipe (out of scope for Stage 3, see Stage 7).
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from ..services.mock_gen import example_from_shape, mock_options
from . import shapes as sh
from .data_categories import values_for
from .effects import NODE_EFFECTS, effect_of
from .graph import TRIGGER_NODE_TYPES

SuggestFn = Callable[[dict[str, Any], dict[str, Any], "SuggestContext"], list[dict[str, Any]]]


@dataclass
class Recipe:
    checks: list[str]
    categories: list[str]
    suggest: SuggestFn


@dataclass
class SuggestContext:
    """What a recipe may look at besides the node itself."""

    definition: dict[str, Any]
    api_operation: Callable[[str, str], dict[str, Any] | None] = lambda spec_id, operation_id: None

    def nodes(self) -> dict[str, dict[str, Any]]:
        return {n["name"]: n for n in self.definition.get("nodes") or [] if isinstance(n, dict) and n.get("name")}

    def parents(self, name: str) -> list[tuple[str, str]]:
        """``[(source node, source port)]`` of the connections into ``name``."""
        out = []
        for c in self.definition.get("connections") or []:
            if isinstance(c, dict) and c.get("target_node") == name:
                out.append((c.get("source_node"), c.get("source_output") or "main"))
        return out


_DATA = ["normal", "boundary", "missing", "wrong_type", "unusual"]
_LOGIC = ["normal", "missing", "wrong_type", "unusual"]
_EXT = ["normal", "ext_success", "ext_error", "ext_timeout", "ext_empty"]


# ── small helpers ────────────────────────────────────────────────────────────────────────────

_NUMERIC_HINT = re.compile(r"(amount|count|total|price|qty|quantity|score|age|number|num|sum|cost|rate|balance|size|limit|weight|value)$", re.I)
_JSON_REF = re.compile(r"\$json((?:\.[A-Za-z_]\w*)+)")
_PURE_REF = re.compile(r"^\s*\{\{\s*\$json((?:\.[A-Za-z_]\w*)+)\s*\}\}\s*$")


def stable_id(*parts: Any) -> str:
    """``t_`` + 8 hex derived from the content (same content, same id)."""
    raw = json.dumps(parts, sort_keys=True, default=str, separators=(",", ":"))
    return "t_" + hashlib.sha1(raw.encode()).hexdigest()[:8]


def _set_path(obj: dict[str, Any], path: list[str], value: Any) -> bool:
    cur = obj
    for key in path[:-1]:
        nxt = cur.get(key)
        if not isinstance(nxt, dict):
            nxt = cur[key] = {}
        cur = nxt
    cur[path[-1]] = value
    return True


def _get_path(obj: Any, path: list[str]) -> tuple[bool, Any]:
    cur = obj
    for key in path:
        if isinstance(cur, dict) and key in cur:
            cur = cur[key]
        else:
            return False, None
    return True, cur


def _guess(name: str, shape: dict[str, Any] | None = None, numeric: bool = False) -> Any:
    """A plausible value for a field named ``name`` (the shape decides when it knows the type)."""
    if isinstance(shape, dict) and shape.get("type") not in (None, "unknown"):
        v = example_from_shape(shape, 1, name)
        if v is not None:
            return v
    if numeric or _NUMERIC_HINT.search(name):
        return 10
    return f"{name}_example"


def _refs_in(value: Any) -> list[list[str]]:
    """``$json.a.b`` references in any nested parameter value, as key lists, in order of first appearance."""
    found: list[list[str]] = []
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    for m in _JSON_REF.finditer(text):
        path = m.group(1).strip(".").split(".")
        if path not in found:
            found.append(path)
    return found


def _input_shape(shapes: dict[str, Any], name: str) -> dict[str, Any] | None:
    shape = ((shapes or {}).get(name) or {}).get("inputs", {}).get("main")
    return shape if isinstance(shape, dict) else None


def _output_shape(shapes: dict[str, Any], name: str, port: str = "main") -> dict[str, Any] | None:
    shape = ((shapes or {}).get(name) or {}).get("outputs", {}).get(port)
    return shape if isinstance(shape, dict) and not sh.is_unknown(shape) else None


def _required_out(shapes: dict[str, Any], name: str) -> set[str]:
    """Top-level fields every output of ``name`` is promised to carry (dropping one breaks the node's own contract)."""
    out: set[str] = set()
    for shape in (((shapes or {}).get(name) or {}).get("outputs") or {}).values():
        if isinstance(shape, dict) and sh.is_closed_object(shape):
            out |= set(shape.get("required") or [])
    return out


def _field_type(shapes: dict[str, Any], node: str, path: list[str]) -> str:
    shape = _input_shape(shapes, node)
    sub = sh.lookup(shape, path) if shape else None
    return (sub or {}).get("type", "unknown") if sub else "unknown"


def _draft(
    name: str, target: dict[str, Any], items: list[Any], expect: list[dict[str, Any]], category: str, *,
    mocks: dict[str, Any] | None = None, field_: str | None = None, covers: list[str] | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    name = name if len(name) <= 120 else name[:117] + "..."
    mocks = mocks or {}
    test: dict[str, Any] = {
        "id": stable_id(target, items, mocks, expect, category, field_),
        "name": name, "target": target, "input": {"items": items}, "mocks": mocks, "expect": expect,
        "category": category, "status": "draft", "locked": False, "source": "suggested",
    }
    if description:
        test["description"] = description
    if field_:
        test["field"] = field_
    if covers is not None:
        test["covers"] = covers
    return test


# ── the path from the trigger to a node ──────────────────────────────────────────────────────

_OBJECT_NAMES = {"body", "data", "payload", "json", "params", "query", "headers"}
_PASSTHROUGH = {"If", "Filter", "Switch", "Wait", "Merge", "Respond", "Sample"}


def _chain_to_trigger(ctx: SuggestContext, name: str) -> list[str] | None:
    """Node names from a trigger to ``name`` along first parents, or None when no trigger is upstream."""
    nodes = ctx.nodes()
    chain = [name]
    seen = {name}
    while True:
        node = nodes.get(chain[0])
        if node is None:
            return None
        if node.get("type") in TRIGGER_NODE_TYPES:
            return chain
        parents = [p for p, _ in ctx.parents(chain[0]) if p in nodes and p not in seen]
        if not parents:
            return None
        chain.insert(0, parents[0])
        seen.add(parents[0])


def _through(node: dict[str, Any], path: list[str]) -> list[str] | None:
    """Map a field path of ``node``'s output to the same data in its input; None when it can't be followed."""
    t = node.get("type")
    if t in _PASSTHROUGH:
        return path
    if t != "Set":
        return None
    params = node.get("parameters") or {}
    keep_only = params.get("keepOnlySet") in (True, "true", 1)
    mode = params.get("mode") or "manual"
    if mode == "manual":
        for f in params.get("fields") or []:
            if not isinstance(f, dict) or not f.get("name"):
                continue
            fpath = str(f["name"]).split(".")
            if path[: len(fpath)] == fpath:
                m = _PURE_REF.match(str(f.get("value", ""))) if isinstance(f.get("value"), str) else None
                return m.group(1).strip(".").split(".") + path[len(fpath):] if m else None
            if fpath[: len(path)] == path:
                return None  # the field is built from several set fields
    elif mode == "json":
        if isinstance(params.get("jsonData"), dict) and path[0] in params["jsonData"]:
            return None
    else:
        return None
    return None if keep_only else path


def _trace(chain: list[str], nodes: dict[str, dict[str, Any]], path: list[str]) -> list[str] | None:
    """``path`` in the input of ``chain[-1]`` as a path in the trigger's output."""
    cur: list[str] | None = path
    for name in reversed(chain[:-1]):
        if name == chain[0]:
            return cur
        cur = _through(nodes[name], cur or [])
        if cur is None:
            return None
    return cur


def _trigger_skeleton(ctx: SuggestContext, shapes: dict[str, Any], trigger: str, numeric_paths: set[tuple[str, ...]]) -> dict[str, Any]:
    """A full trigger item: every ``$json`` field the workflow reads downstream, traced back to the trigger."""
    nodes = ctx.nodes()
    item: dict[str, Any] = {}
    for name, node in nodes.items():
        if node.get("type") in TRIGGER_NODE_TYPES:
            continue
        chain = _chain_to_trigger(ctx, name)
        if not chain or chain[0] != trigger:
            continue
        for ref in _refs_in(node.get("parameters") or {}):
            tp = _trace(chain, nodes, ref)
            if tp:
                ok, _ = _get_path(item, tp)
                if not ok:
                    _set_path(item, tp, _guess(tp[-1], numeric=tuple(tp) in numeric_paths))
    shape = _output_shape(shapes, trigger) or (((shapes or {}).get(trigger) or {}).get("outputs") or {}).get("main")
    if isinstance(shape, dict) and sh.is_closed_object(shape):  # what the trigger promises, so downstream contracts hold
        required = set(shape.get("required") or [])
        for k, sub in shape["properties"].items():
            known = isinstance(sub, dict) and sub.get("type") not in (None, "unknown")
            if k not in item and (known or k in required):
                item[k] = _guess(k, sub) if known else ({} if k in _OBJECT_NAMES else _guess(k))
    return item


def _node_item(node: dict[str, Any], shapes: dict[str, Any], overrides: dict[tuple[str, ...], Any] | None = None) -> dict[str, Any]:
    """An input item for ``node`` alone: the fields its input shape names plus the ``$json`` fields it reads."""
    item: dict[str, Any] = {}
    for ref in _refs_in(node.get("parameters") or {}):
        ok, _ = _get_path(item, ref)
        if not ok:
            _set_path(item, ref, _guess(ref[-1]))
    shape = _input_shape(shapes, node["name"])
    if shape and sh.is_closed_object(shape):
        required = set(shape.get("required") or [])
        for k, sub in shape["properties"].items():
            known = isinstance(sub, dict) and sub.get("type") not in (None, "unknown")
            if k not in item and (known or k in required):
                item[k] = _guess(k, sub) if known else ({} if k in _OBJECT_NAMES else _guess(k))
    for path, value in (overrides or {}).items():
        _set_path(item, list(path), value)
    return item


def _case_input(
    ctx: SuggestContext, shapes: dict[str, Any], node: dict[str, Any], overrides: dict[tuple[str, ...], Any],
    numeric_paths: set[tuple[str, ...]] | None = None,
) -> tuple[dict[str, Any], list[Any]]:
    """``(target, items)`` that put ``overrides`` (paths in ``node``'s input) in front of ``node``."""
    nodes = ctx.nodes()
    chain = _chain_to_trigger(ctx, node["name"])
    gated = bool(chain) and any(nodes[n].get("type") in ("If", "Filter", "Switch") for n in chain[1:-1])
    if chain and len(chain) > 1 and not gated:
        traced: dict[tuple[str, ...], Any] = {}
        for path, value in overrides.items():
            tp = _trace(chain, nodes, list(path))
            if tp is None:
                traced = {}
                break
            traced[tuple(tp)] = value
        if traced or not overrides:
            numeric = set(numeric_paths or set()) | {p for p, v in traced.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}
            item = _trigger_skeleton(ctx, shapes, chain[0], numeric)
            for path, value in traced.items():
                _set_path(item, list(path), value)
            return {"kind": "workflow"}, [item]
    return {"kind": "node", "node": node["name"]}, [_node_item(node, shapes, overrides)]


# ── conditions (If / Filter / Switch) ────────────────────────────────────────────────────────

_CMP_OPS = {"==": "equals", "===": "equals", "!=": "notEquals", "!==": "notEquals",
            ">": "gt", ">=": "gte", "<": "lt", "<=": "lte"}
_FLIP = {"gt": "lt", "gte": "lte", "lt": "gt", "lte": "gte", "equals": "equals", "notEquals": "notEquals"}
_REF_EXPR = r"\$json((?:\.[A-Za-z_]\w*)+|(?:\[['\"][^'\"]+['\"]\])+)"
_LIT_EXPR = r"(-?\d+(?:\.\d+)?|'[^']*'|\"[^\"]*\"|true|false|True|False)"
_CMP_EXPR = r"(===|!==|>=|<=|==|!=|>|<)"
_COND_FWD = re.compile(rf"^\s*\{{\{{\s*{_REF_EXPR}\s*{_CMP_EXPR}\s*{_LIT_EXPR}\s*\}}\}}\s*$")
_COND_REV = re.compile(rf"^\s*\{{\{{\s*{_LIT_EXPR}\s*{_CMP_EXPR}\s*{_REF_EXPR}\s*\}}\}}\s*$")
_NO_VALUE_OPS = {"isEmpty", "isNotEmpty", "isTrue", "isFalse", "isNull", "isNotNull"}


@dataclass
class Cond:
    """One comparison of a field with a constant, in the structured operation names of If/Filter/Switch."""

    path: list[str]
    op: str
    value: Any = None
    rule: int | None = None  # Switch: index of the rule
    expr: bool = False  # read from a ``{{ ... }}`` expression (not from field / operation / value)


def _lit(text: str) -> Any:
    if text in ("true", "True"):
        return True
    if text in ("false", "False"):
        return False
    if text[0] in "'\"":
        return text[1:-1]
    return float(text) if "." in text else int(text)


def _ref_path(text: str) -> list[str]:
    if text.startswith("."):
        return text.strip(".").split(".")
    return re.findall(r"\[['\"]([^'\"]+)['\"]\]", text)


def parse_expression_condition(cond: Any) -> Cond | None:
    """``{{ $json.amount > 500 }}`` -> Cond(["amount"], "gt", 500); None for anything more complex."""
    if not isinstance(cond, str):
        return None
    m = _COND_FWD.match(cond)
    if m:
        return Cond(_ref_path(m.group(1)), _CMP_OPS[m.group(2)], _lit(m.group(3)))
    m = _COND_REV.match(cond)
    if m:
        return Cond(_ref_path(m.group(3)), _FLIP[_CMP_OPS[m.group(2)]], _lit(m.group(1)))
    return None


def _structured(field_: Any, op: Any, value: Any, rule: int | None = None) -> Cond | None:
    if not isinstance(op, str) or op == "regex":
        return None
    if isinstance(field_, str):
        pure = _PURE_REF.match(field_)
        if pure:
            path = pure.group(1).strip(".").split(".")
        elif "{{" in field_ or "(" in field_:
            return None
        else:
            path = [p for p in field_.split(".") if p]
    else:
        return None
    if not path:
        return None
    if isinstance(value, str) and "{{" in value:
        return None
    return Cond(path, op, value, rule)


def conditions_of(node: dict[str, Any]) -> list[Cond]:
    """The comparisons a node's parameters make: If / Filter (one), Switch (one per rule)."""
    t = node.get("type")
    params = node.get("parameters") or {}
    if t in ("If", "Filter"):
        expression = bool(params.get("condition")) if t == "If" else (params.get("mode") == "expression" and bool(params.get("condition")))
        if expression:
            c = parse_expression_condition(params.get("condition"))
            if c:
                c.expr = True
            return [c] if c else []
        if t == "Filter" and params.get("mode") not in (None, "rules"):
            return []
        if t == "If" and not params.get("field") and params.get("operation") in (None, "isTrue", "isFalse", "isEmpty", "isNotEmpty"):
            return []
        c = _structured(params.get("field", ""), params.get("operation") or ("isTrue" if t == "If" else "isNotEmpty"), params.get("value"))
        return [c] if c else []
    if t == "Switch" and (params.get("mode") or "rules") == "rules":
        out = []
        for i, rule in enumerate(params.get("rules") or []):
            if isinstance(rule, dict):
                c = _structured(rule.get("field", ""), rule.get("operation") or "equals", rule.get("value"), i)
                if c:
                    out.append(c)
        return out
    return []


def _num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _as_number(v: Any) -> float | None:
    if _num(v):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v)
        except ValueError:
            return None
    return None


def _fmt(v: float, like_int: bool) -> Any:
    if like_int and float(v).is_integer():
        return int(v)
    return round(v, 10)


def _step(c: float) -> float:
    s = str(c)
    decimals = len(s.split(".")[1]) if "." in s and not s.endswith(".0") else 0
    return 10 ** -max(2, decimals + 1)


def boundary_values(cond: Cond, field_type: str = "unknown") -> list[Any]:
    """Values at and just beside the constant of a numeric comparison.

    ``>`` 500: 500 and 501 for an integer field, 500 and 500.01 for a number field; a field of unknown type gets
    both (500, 501 and 500.01). ``>=`` / ``<=`` / ``<`` / ``==`` / ``!=`` mirror that.
    """
    c = _as_number(cond.value)
    if c is None or cond.op not in ("gt", "gte", "lt", "lte", "equals", "notEquals"):
        return []
    if cond.op in ("equals", "notEquals") and not _num(cond.value):
        return []
    whole = c.is_integer()
    up_side = cond.op in ("gt", "equals", "notEquals", "lte")  # the neighbour that flips the answer is above c
    sign = 1 if up_side else -1
    deltas: list[float] = []
    if field_type in ("integer", "unknown") and whole:
        deltas.append(1.0)
    if field_type in ("number", "unknown") or not whole:
        deltas.append(_step(c))
    if field_type == "integer" and not whole:
        deltas.append(1.0)
    values: list[Any] = [_fmt(c, whole)]
    for d in deltas:
        values.append(_fmt(c + sign * d, whole and d == 1.0))
    seen: list[Any] = []
    for v in values:
        if not any(type(v) is type(s) and v == s for s in seen):
            seen.append(v)
    return seen


def _far(cond: Cond) -> list[Any]:
    """One value well inside each side of a numeric comparison (for "normal" cases)."""
    c = _as_number(cond.value)
    if c is None:
        return []
    jump = max(1.0, round(abs(c) * 0.5))
    whole = c.is_integer()
    return [_fmt(c + jump, whole), _fmt(c - jump, whole)]


def _other(v: Any) -> Any:
    return v + "_other" if isinstance(v, str) else v


def _text_values(cond: Cond) -> list[Any]:
    """Matching / non-matching values for non-numeric comparisons."""
    v = cond.value
    op = cond.op
    if op in ("equals", "notEquals"):
        return [v, _other(v) if isinstance(v, str) else (not v if isinstance(v, bool) else v)]
    if op in ("contains", "notContains"):
        return [f"has {v} inside", "zzz-no-match"]
    if op == "startsWith":
        return [f"{v}-tail", "zzz"]
    if op == "endsWith":
        return [f"head-{v}", "zzz"]
    if op in ("isEmpty", "isNotEmpty"):
        return ["", "something"]
    if op in ("isNull", "isNotNull"):
        return [None, "something"]
    if op in ("isTrue", "isFalse"):
        return [True, False]
    return []


def _predict(node: dict[str, Any], cond: Cond, value: Any, ctx_nodes: Any = None) -> str | None:
    """The output port ``node`` sends an item whose field is ``value`` to, computed with the node's own code."""
    from .node_registry import node_registry

    t = node["type"]
    params = node.get("parameters") or {}
    impl = node_registry.get(t)
    item = {}
    if value is not _ABSENT:
        _set_path(item, cond.path, value)
    if t == "Switch":
        n_out = params.get("numberOfOutputs", 2)
        n_out = n_out if isinstance(n_out, int) and n_out > 0 else 2
        for rule in params.get("rules") or []:
            if isinstance(rule, dict) and "{{" in str(rule.get("field", "")) and not _PURE_REF.match(str(rule.get("field"))):
                return None
            try:
                hit = impl._evaluate_rule(_resolve_rule(rule), item, None)
            except Exception:
                return None
            if hit:
                idx = rule.get("output", 0)
                return f"output{max(0, min(idx if isinstance(idx, int) else 0, n_out - 1))}"
        return "fallback"
    found, actual = _get_path(item, cond.path)
    if cond.expr:
        hit = _compare(actual if found else None, cond.op, cond.value)
        if hit is None:
            return None
    else:
        hit = impl._evaluate(actual if found else None, cond.op, cond.value)
    if t == "If":
        return "true" if hit else "false"
    return "main" if hit else "dropped"


def _resolve_rule(rule: dict[str, Any]) -> dict[str, Any]:
    """A Switch rule whose field is ``{{ $json.x }}`` as a plain field path (what the node's lookup needs)."""
    field_ = rule.get("field", "")
    m = _PURE_REF.match(field_) if isinstance(field_, str) else None
    return {**rule, "field": m.group(1).strip(".") if m else field_}


_ABSENT = object()


def _compare(actual: Any, op: str, expected: Any) -> bool | None:
    """The result of ``{{ $json.x <op> literal }}`` in the expression engine, for the simple types we generate."""
    try:
        if op == "equals":
            return actual == expected
        if op == "notEquals":
            return actual != expected
        if actual is None or isinstance(actual, bool) or isinstance(expected, bool):
            return None
        if type(actual) is not type(expected) and not (_num(actual) and _num(expected)):
            return None
        return {"gt": actual > expected, "gte": actual >= expected, "lt": actual < expected, "lte": actual <= expected}[op]
    except Exception:
        return None


def _describe(v: Any) -> str:
    s = json.dumps(v) if not isinstance(v, str) else v
    return s if len(s) <= 24 else s[:21] + "..."


def _numeric_paths(ctx: SuggestContext) -> set[tuple[str, ...]]:
    """Trigger paths that a numeric comparison reads (so their guessed values are numbers)."""
    nodes = ctx.nodes()
    paths: set[tuple[str, ...]] = set()
    for name, node in nodes.items():
        for cond in conditions_of(node):
            if cond.op in ("gt", "gte", "lt", "lte") or _num(cond.value):
                chain = _chain_to_trigger(ctx, name)
                if chain:
                    tp = _trace(chain, nodes, cond.path)
                    if tp:
                        paths.add(tuple(tp))
    return paths


def _branch_expect(node: dict[str, Any], port: str) -> list[dict[str, Any]]:
    name, t = node["name"], node["type"]
    if t == "If":
        other = "false" if port == "true" else "true"
        return [{"node": name, "port": port, "op": "count", "value": 1}, {"node": name, "port": other, "op": "count", "value": 0}]
    if t == "Filter":
        return [{"node": name, "port": "main", "op": "count", "value": 1 if port == "main" else 0}]
    return [{"node": name, "port": port, "op": "count", "value": 1}]


def _branch_label(node: dict[str, Any], port: str) -> str:
    if node["type"] == "Filter":
        return "kept" if port == "main" else "dropped"
    return f"takes {port}"


def _gate_drafts(node: dict[str, Any], shapes: dict[str, Any], ctx: SuggestContext) -> list[dict[str, Any]]:
    """Boundary, normal (one per branch) and missing-field drafts for If / Filter / Switch."""
    drafts: list[dict[str, Any]] = []
    numeric = _numeric_paths(ctx)
    seen_cases: set[str] = set()
    conds = conditions_of(node)
    switch = node["type"] == "Switch"

    def add(cond: Cond, value: Any, category: str) -> None:
        port = _predict(node, cond, value)
        if port is None or (port == "dropped" and node["type"] != "Filter"):
            return
        if value is _ABSENT and (cond.expr or cond.path[0] in _required_out(shapes, node["name"])):
            return  # an expression's reaction to a missing field isn't predictable; a promised field can't be left out
        field_ = ".".join(cond.path)
        if value is _ABSENT:
            target, items = {"kind": "node", "node": node["name"]}, [_node_item(node, shapes, None)]
            items[0].pop(cond.path[0], None)
            label = f"{field_} missing"
        else:
            target, items = _case_input(ctx, shapes, node, {tuple(cond.path): value}, numeric)
            label = f"{field_} = {_describe(value)}"
        key = json.dumps([target, items], sort_keys=True, default=str)
        if key in seen_cases:
            return
        seen_cases.add(key)
        drafts.append(_draft(
            f"{node['name']}: {label} ({_branch_label(node, port)})", target, items, _branch_expect(node, port), category,
            field_=field_, covers=[node["name"]],
        ))

    for cond in conds:
        ftype = _field_type(shapes, node["name"], cond.path)
        for v in boundary_values(cond, ftype):
            add(cond, v, "boundary")
        numeric_cmp = cond.op in ("gt", "gte", "lt", "lte") and _as_number(cond.value) is not None
        for v in (_far(cond) if numeric_cmp else _text_values(cond)):
            add(cond, v, "normal")
        add(cond, _ABSENT, "missing")
    if switch and (node.get("parameters") or {}).get("mode", "rules") == "rules":
        # one item that matches no rule: the fallback port
        for cond in conds:
            for v in ("zzz-no-match", -987654321):
                if _predict(node, cond, v) == "fallback":
                    add(cond, v, "normal")
                    break
            else:
                continue
            break
    elif switch:
        idx = (node.get("parameters") or {}).get("outputIndex", 0)
        n_out = (node.get("parameters") or {}).get("numberOfOutputs", 2)
        port = f"output{max(0, min(idx if isinstance(idx, int) else 0, (n_out if isinstance(n_out, int) else 2) - 1))}"
        target, items = _case_input(ctx, shapes, node, {})
        drafts.append(_draft(f"{node['name']}: every item goes to {port}", target, items,
                             [{"node": node["name"], "port": port, "op": "count", "value": len(items)}], "normal",
                             covers=[node["name"]]))
    return drafts


# ── declared limits of an input shape ────────────────────────────────────────────────────────


def _limit_drafts(node: dict[str, Any], shapes: dict[str, Any], ctx: SuggestContext) -> list[dict[str, Any]]:
    """Edge values of the declared ``minimum`` / ``maximum`` / ``minLength`` / ``maxLength`` of the input shape."""
    shape = _input_shape(shapes, node["name"])
    if not shape or not sh.is_closed_object(shape):
        return []
    drafts: list[dict[str, Any]] = []
    for fname, sub in shape["properties"].items():
        if not isinstance(sub, dict) or sub.get("type") not in ("integer", "number", "string"):
            continue
        if not any(k in sub for k in ("minimum", "maximum", "minLength", "maxLength")):
            continue
        edges = []
        for v in values_for(sub, fname)["boundary"]:
            if not sh.validate(sub, v):  # only values the declared type accepts: the edges, not the first invalid value
                edges.append(v)
        for v in edges:
            item = _node_item(node, shapes, {(fname,): v})
            drafts.append(_draft(
                f"{node['name']}: {fname} = {_describe(v)} (edge of the declared limit)", {"kind": "node", "node": node["name"]},
                [item], [{"node": node["name"], "op": "status", "value": "success"}], "boundary",
                field_=fname, covers=[node["name"]],
            ))
    return drafts


# ── logic nodes ──────────────────────────────────────────────────────────────────────────────


def _set_expectations(node: dict[str, Any], item: dict[str, Any]) -> list[dict[str, Any]]:
    params = node.get("parameters") or {}
    if (params.get("mode") or "manual") != "manual":
        return []
    out = []
    for f in params.get("fields") or []:
        if not isinstance(f, dict) or not f.get("name"):
            continue
        value = f.get("value")
        if isinstance(value, str):
            m = _PURE_REF.match(value)
            if m:
                ok, actual = _get_path(item, m.group(1).strip(".").split("."))
                if ok and actual is not None:
                    out.append({"node": node["name"], "path": str(f["name"]), "op": "eq", "value": actual})
                continue
            if "{{" in value or _as_number(value) is not None or value.strip().lower() in ("true", "false"):
                continue
        elif isinstance(value, (dict, list)) or value is None:
            continue
        out.append({"node": node["name"], "path": str(f["name"]), "op": "eq", "value": value})
    return out


def _logic_suggest(node: dict[str, Any], shapes: dict[str, Any], ctx: SuggestContext) -> list[dict[str, Any]]:
    name, t = node["name"], node["type"]
    target = {"kind": "node", "node": name}
    item = _node_item(node, shapes)
    ok = {"node": name, "op": "status", "value": "success"}
    drafts = [_draft(f"{name}: typical input", target, [item], [ok, *(_set_expectations(node, item) if t == "Set" else [])],
                     "normal", covers=[name])]
    if t == "Set" and item:
        refs = [r for r in _refs_in(node.get("parameters") or {})]
        top = list(dict.fromkeys(r[0] for r in refs))[:3]
        required = _required_out(shapes, name)
        for k in [k for k in top if k not in required]:
            partial = {kk: vv for kk, vv in item.items() if kk != k}
            drafts.append(_draft(f"{name}: {k} missing", target, [partial], [ok], "missing", field_=k, covers=[name]))
    drafts.extend(_limit_drafts(node, shapes, ctx))
    return drafts


def _plain_suggest(expect_status: str = "success") -> SuggestFn:
    def suggest(node: dict[str, Any], shapes: dict[str, Any], ctx: SuggestContext) -> list[dict[str, Any]]:
        name = node["name"]
        item = _node_item(node, shapes)
        drafts = [_draft(f"{name}: typical input", {"kind": "node", "node": name}, [item],
                         [{"node": name, "op": "status", "value": expect_status}], "normal", covers=[name])]
        drafts.extend(_limit_drafts(node, shapes, ctx))
        return drafts

    return suggest


def _gate_suggest(node: dict[str, Any], shapes: dict[str, Any], ctx: SuggestContext) -> list[dict[str, Any]]:
    return [*_gate_drafts(node, shapes, ctx), *_limit_drafts(node, shapes, ctx)]


# ── triggers ─────────────────────────────────────────────────────────────────────────────────


def _trigger_suggest(node: dict[str, Any], shapes: dict[str, Any], ctx: SuggestContext) -> list[dict[str, Any]]:
    name = node["name"]
    nodes = ctx.nodes()
    first = next((n for n, d in nodes.items() if d.get("type") in TRIGGER_NODE_TYPES), None)
    if first != name:
        return []
    item = _trigger_skeleton(ctx, shapes, name, _numeric_paths(ctx))
    items = [item] if item else []
    return [_draft(f"{name}: typical trigger payload", {"kind": "workflow"}, items,
                   [{"node": name, "op": "status", "value": "success"}], "normal", covers=[name])]


# ── outside systems ──────────────────────────────────────────────────────────────────────────


def _api_options(node: dict[str, Any], ctx: SuggestContext) -> list[dict[str, Any]] | None:
    ref = (node.get("parameters") or {}).get("api_ref")
    if node.get("type") != "HttpRequest" or not isinstance(ref, dict) or not ref.get("spec_id"):
        return None
    op = ctx.api_operation(ref["spec_id"], str(ref.get("operation_id") or ""))
    return mock_options(op) if op else None


def _generic_options(node: dict[str, Any], shapes: dict[str, Any]) -> list[dict[str, Any]]:
    """Success / error / timeout / empty mocks for an outside step without an API spec."""
    t = node["type"]
    body_shape = (((_output_shape(shapes, node["name"]) or {}).get("properties") or {}).get("body")) if t == "HttpRequest" else None
    if t == "HttpRequest":
        body = example_from_shape(body_shape, 1, "body") if body_shape else None
        ok_http: dict[str, Any] = {"status": 200, "body": body if body is not None else {"ok": True}}
        return [
            {"label": "200 success", "category": "ext_success", "mock": {"http": ok_http}},
            {"label": "500 server error", "category": "ext_error", "mock": {"http": {"status": 500, "body": {"error": "mock failure"}}}},
            {"label": "Timeout", "category": "ext_timeout", "mock": {"timeout": True}},
            {"label": "200 empty", "category": "ext_empty", "mock": {"http": {"status": 200, "body": {}}}},
        ]
    shape = _output_shape(shapes, node["name"])
    row = example_from_shape(shape, 1, "row") if shape else None
    if not isinstance(row, dict) or not row:
        row = {"id": 1}
    return [
        {"label": "success", "category": "ext_success", "mock": {"items": [row]}},
        {"label": "error", "category": "ext_error", "mock": {"error": {"message": "mock failure", "code": "MOCK_ERROR"}}},
        {"label": "Timeout", "category": "ext_timeout", "mock": {"timeout": True}},
        {"label": "empty", "category": "ext_empty", "mock": {"items": []}},
    ]


def _outside_suggest(node: dict[str, Any], shapes: dict[str, Any], ctx: SuggestContext) -> list[dict[str, Any]]:
    name, t = node["name"], node["type"]
    options = _api_options(node, ctx) or _generic_options(node, shapes)
    on_error = node.get("on_error") or ("continue" if node.get("continue_on_fail") else "stop")
    item = _node_item(node, shapes)
    target = {"kind": "node", "node": name}
    drafts = []
    for opt in options:
        mock = opt["mock"]
        expect: list[dict[str, Any]]
        if "timeout" in mock or "error" in mock:
            if on_error == "error_output":
                expect = [{"node": name, "port": "error", "op": "count", "value": 1}]
            else:
                expect = [{"node": name, "op": "status", "value": "error"}]
        elif "http" in mock:
            expect = [{"node": name, "op": "status", "value": "success"},
                      {"node": name, "path": "statusCode", "op": "eq", "value": mock["http"]["status"]}]
            expect.append({"node": name, "subject": "request", "op": "count", "value": 1})
        else:
            items = mock.get("items") or []
            expect = [{"node": name, "op": "status", "value": "success" if items else "no_output"}]
        drafts.append(_draft(f"{name}: {opt['label']}", target, [item], expect, opt["category"],
                             mocks={name: mock}, covers=[name]))
    return drafts


# ── the table ────────────────────────────────────────────────────────────────────────────────

_NONE: SuggestFn = lambda node, shapes, ctx: []  # noqa: E731

_CHECKS_LOGIC = ["input -> exact output", "output matches the contract"]
_CHECKS_OUTSIDE = ["the request it would send", "how later nodes handle each reply (success, documented errors, timeout, empty)"]

_RECIPES: dict[str, Recipe] = {}


def _register(types: list[str], checks: list[str], categories: list[str], suggest: SuggestFn) -> None:
    for t in types:
        _RECIPES[t] = Recipe(checks=checks, categories=categories, suggest=suggest)


_register(["Set"], _CHECKS_LOGIC, _LOGIC, _logic_suggest)
_register(["Filter"], ["which items are kept", "boundaries read from the condition"], _DATA, _gate_suggest)
_register(["If"], ["which branch each item takes", "boundaries read from the condition"], _DATA, _gate_suggest)
_register(["Switch"], ["which output each item takes", "boundaries read from the rules"], _DATA, _gate_suggest)
_register(["ItemLists", "Wait", "Respond", "Loop"], _CHECKS_LOGIC, _LOGIC, _plain_suggest())
_register(["Merge"], _CHECKS_LOGIC, _LOGIC, _NONE)  # needs two inputs: its tests are written by hand
_register(["Code", "PythonStep"], ["input -> output"], _LOGIC, _plain_suggest())
_register(["StopAndError"], ["the run stops with the configured error"], ["normal"], _plain_suggest("error"))
_register(["ChatInput"], ["the message becomes the test input"], ["normal"], _plain_suggest())
_register(["Start", "Webhook", "Cron", "ErrorTrigger", "ExecuteWorkflowTrigger"],
          ["the payload is the test input"], ["normal"], _trigger_suggest)
_register(["LLMChat", "AIAgent"], [], [], _NONE)  # AI/LLM: out of scope for Stage 3 (Stage 7: AI step evals)
_register([t for t, e in NODE_EFFECTS.items() if e in ("network", "database", "subworkflow") and t not in _RECIPES],
          _CHECKS_OUTSIDE, _EXT, _outside_suggest)

_FALLBACK = Recipe(checks=["input -> exact output"], categories=_LOGIC, suggest=_plain_suggest())


def recipe_for(node_type: str) -> Recipe:
    """The recipe of a node type (every registered type has one; an unknown type gets a generic logic recipe)."""
    if node_type in _RECIPES:
        return _RECIPES[node_type]
    if effect_of(node_type) != "none":
        return Recipe(checks=_CHECKS_OUTSIDE, categories=_EXT, suggest=_outside_suggest)
    return _FALLBACK


def suggest_for_workflow(definition: dict[str, Any], shapes: dict[str, Any], ctx: SuggestContext, node: str | None = None) -> list[dict[str, Any]]:
    """Drafts for every node of ``definition`` (or just ``node``), deduplicated by id, in node order."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for n in definition.get("nodes") or []:
        if not isinstance(n, dict) or not n.get("name") or (node and n["name"] != node) or n.get("disabled"):
            continue
        for t in recipe_for(n.get("type", "")).suggest(copy.deepcopy(n), shapes, ctx):
            if t["id"] not in seen:
                seen.add(t["id"])
                out.append(t)
    return out
