"""Assertions of a test (Stage 3A, CONTRACTS-STAGE3A §4). Pure: no I/O, never raises on odd data.

``evaluate(assertion, run) -> AssertionResult`` where ``run`` is a ``RunView`` (what a test run produced).
``compare_value`` is the one comparison used by assertions and by the ``when`` of smart-mock rules.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from . import shapes as sh

OPS = (
    "eq", "neq", "gt", "gte", "lt", "lte", "contains", "matches", "exists", "not_exists",
    "count", "type", "one_of", "range", "status", "shape",
)
TYPE_NAMES = ("string", "integer", "number", "boolean", "object", "array", "null")
STATUSES = ("success", "error", "no_output", "not_run")


@dataclass
class RunView:
    """What a test run produced, as the assertions see it."""

    outputs: dict[str, dict[str, list[Any]]] = field(default_factory=dict)  # node -> port -> [item json]
    requests: dict[str, list[dict[str, Any]]] = field(default_factory=dict)  # node -> [request]
    node_status: dict[str, str] = field(default_factory=dict)  # node -> success|error|no_output|not_run
    contracts: dict[str, dict[str, Any]] = field(default_factory=dict)  # node -> port -> output Shape


# ── paths ────────────────────────────────────────────────────────────────────────────────────

_TOKEN = re.compile(r"\[(\d+)\]|([^.\[\]]+)")


def resolve_path(obj: Any, path: str | None) -> tuple[bool, Any]:
    """Follow ``a.b[0].c`` through dicts and lists. ``""`` is the object itself. Returns ``(found, value)``.
    A list index may also be written with a dot (``a.0.c``); a dict key ``"0"`` is tried first."""
    found, value = _resolve_path(obj, path)
    if not found and path and re.search(r"\.\d+(?=\.|\[|$)", path):
        return _resolve_path(obj, re.sub(r"\.(\d+)(?=\.|\[|$)", r"[\1]", path))
    return found, value


def _resolve_path(obj: Any, path: str | None) -> tuple[bool, Any]:
    cur = obj
    for m in _TOKEN.finditer(path or ""):
        if m.group(1) is not None:
            idx = int(m.group(1))
            if not isinstance(cur, (list, tuple)) or idx >= len(cur):
                return False, None
            cur = cur[idx]
        else:
            key = m.group(2)
            if isinstance(cur, dict) and key in cur:
                cur = cur[key]
            else:
                return False, None
    return True, cur


# ── value comparison ─────────────────────────────────────────────────────────────────────────


def type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, (list, tuple)):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def values_equal(a: Any, b: Any) -> bool:
    """JSON equality: ``True`` is not ``1``, ``1`` equals ``1.0``."""
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if _is_num(a) and _is_num(b):
        return a == b
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(values_equal(a[k], b[k]) for k in a)
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(values_equal(x, y) for x, y in zip(a, b))
    return type(a) is type(b) and a == b


def _fmt(v: Any) -> str:
    text = repr(v)
    return text if len(text) <= 80 else text[:77] + "..."


def compare_value(op: str, found: bool, actual: Any, expected: Any) -> tuple[bool, str]:
    """Does ``actual`` (found at the path or not) satisfy ``op`` against ``expected``? Never raises."""
    try:
        return _compare(op, found, actual, expected)
    except Exception as e:  # an assertion never crashes a run
        return False, f"could not compare: {e}"


def _compare(op: str, found: bool, actual: Any, expected: Any) -> tuple[bool, str]:
    if op == "exists":
        return found, "exists" if found else "path does not exist"
    if op == "not_exists":
        return (not found), "does not exist" if not found else f"path exists (value {_fmt(actual)})"
    if op not in OPS or op in ("count", "status", "shape"):
        return False, f"unsupported operator '{op}' here"
    if not found:
        return False, "path not found"
    if op == "eq":
        ok = values_equal(actual, expected)
        return ok, "equal" if ok else f"expected {_fmt(expected)}, got {_fmt(actual)}"
    if op == "neq":
        ok = not values_equal(actual, expected)
        return ok, "different" if ok else f"expected a value other than {_fmt(expected)}"
    if op in ("gt", "gte", "lt", "lte"):
        both_num = _is_num(actual) and _is_num(expected)
        both_str = isinstance(actual, str) and isinstance(expected, str)
        if not (both_num or both_str):
            return False, f"type mismatch: cannot compare {type_name(actual)} with {type_name(expected)}"
        ok = {"gt": actual > expected, "gte": actual >= expected, "lt": actual < expected, "lte": actual <= expected}[op]
        sym = {"gt": ">", "gte": ">=", "lt": "<", "lte": "<="}[op]
        return ok, "ok" if ok else f"expected {sym} {_fmt(expected)}, got {_fmt(actual)}"
    if op == "contains":
        if isinstance(actual, str):
            if not isinstance(expected, str):
                return False, f"type mismatch: cannot look for a {type_name(expected)} in a string"
            ok = expected in actual
        elif isinstance(actual, (list, tuple)):
            ok = any(values_equal(x, expected) for x in actual)
        elif isinstance(actual, dict):
            ok = isinstance(expected, str) and expected in actual
        else:
            return False, f"type mismatch: {type_name(actual)} cannot contain anything"
        return ok, "contains" if ok else f"{_fmt(actual)} does not contain {_fmt(expected)}"
    if op == "matches":
        if not isinstance(actual, str):
            return False, f"type mismatch: expected a string, got {type_name(actual)}"
        if not isinstance(expected, str):
            return False, "pattern must be a string"
        try:
            ok = re.search(expected, actual) is not None
        except re.error as e:
            return False, f"invalid pattern: {e}"
        return ok, "matches" if ok else f"{_fmt(actual)} does not match {expected}"
    if op == "type":
        if expected not in TYPE_NAMES:
            return False, f"unknown type '{expected}'"
        ok = type_name(actual) == expected or (expected == "number" and _is_num(actual))
        return ok, "type matches" if ok else f"expected type {expected}, got {type_name(actual)}"
    if op == "one_of":
        if not isinstance(expected, (list, tuple)):
            return False, "one_of needs a list"
        ok = any(values_equal(actual, x) for x in expected)
        return ok, "one of the options" if ok else f"{_fmt(actual)} is not one of {_fmt(list(expected))}"
    if op == "range":
        if not _is_num(actual):
            return False, f"type mismatch: expected a number, got {type_name(actual)}"
        spec = expected if isinstance(expected, dict) else {}
        lo, hi = spec.get("min"), spec.get("max")
        if (lo is not None and not _is_num(lo)) or (hi is not None and not _is_num(hi)):
            return False, "range needs numeric min/max"
        ok = (lo is None or actual >= lo) and (hi is None or actual <= hi)
        return ok, "in range" if ok else f"{_fmt(actual)} is outside [{lo}, {hi}]"
    return False, f"unsupported operator '{op}'"


# ── evaluation ───────────────────────────────────────────────────────────────────────────────


def evaluate(assertion: dict[str, Any], run: RunView) -> dict[str, Any]:
    """Evaluate one assertion against a run; returns an ``AssertionResult`` dict. Never raises."""
    subject = assertion.get("subject") or "output"
    node = str(assertion.get("node") or "")
    port = assertion.get("port") or "main"
    path = assertion.get("path") or ""
    op = assertion.get("op") or "eq"
    expected = assertion.get("value")
    result: dict[str, Any] = {
        "passed": False, "node": node, "port": port if subject == "output" else None, "path": path,
        "op": op, "expected": expected, "actual": None, "message": "",
    }
    if assertion.get("id"):
        result["id"] = assertion["id"]
    try:
        passed, actual, message, note = _evaluate(assertion, subject, node, port, path, op, expected, run)
    except Exception as e:
        passed, actual, message, note = False, None, f"could not evaluate: {e}", None
    result.update(passed=passed, actual=actual, message=message)
    if note:
        result["note"] = note
    return result


def _evaluate(a: dict[str, Any], subject: str, node: str, port: str, path: str, op: str, expected: Any, run: RunView):
    if op == "status":
        actual = run.node_status.get(node, "not_run")
        ok = actual == expected
        return ok, actual, ("status is " + actual) if ok else f"expected status {expected}, got {actual}", None

    if subject == "request":
        items: list[Any] = list(run.requests.get(node, []))
    else:
        items = list((run.outputs.get(node) or {}).get(port, []))

    if op == "count":
        ok = len(items) == expected
        return ok, len(items), "count matches" if ok else f"expected {expected} item(s), got {len(items)}", None

    if op == "shape":
        return _shape_check(a, subject, node, port, path, expected, items, run)

    which = a.get("item", 0)
    if which is None:
        which = 0
    if which in ("all", "any"):
        if not items:
            return False, None, f"no {'requests' if subject == 'request' else 'items'} to check", None
        outcomes = []
        for it in items:
            found, actual = resolve_path(it, path)
            ok, msg = compare_value(op, found, actual, expected)
            outcomes.append((ok, actual, msg))
        if which == "all":
            bad = next(((i, o) for i, o in enumerate(outcomes) if not o[0]), None)
            if bad is None:
                return True, [o[1] for o in outcomes], "every item passes", None
            return False, bad[1][1], f"item {bad[0]}: {bad[1][2]}", None
        good = next((o for o in outcomes if o[0]), None)
        if good is not None:
            return True, good[1], "at least one item passes", None
        return False, [o[1] for o in outcomes], f"no item passes ({outcomes[0][2]})", None

    idx = int(which)
    if idx >= len(items) or idx < -len(items):
        if op == "not_exists":
            return True, None, "no such item", None
        what = "request" if subject == "request" else "item"
        return False, None, f"there is no {what} {idx} (got {len(items)})", None
    found, actual = resolve_path(items[idx], path)
    ok, msg = compare_value(op, found, actual, expected)
    return ok, actual, msg, None


def _shape_check(a: dict[str, Any], subject: str, node: str, port: str, path: str, expected: Any, items: list[Any], run: RunView):
    shape = expected if isinstance(expected, dict) and expected else None
    note = None
    if shape is None:
        shape = (run.contracts.get(node) or {}).get(port)
        if sh.is_unknown(shape):
            return True, None, "no contract to check against", "no contract"
    which = a.get("item", 0)
    if which is None:
        which = 0
    if which in ("all", "any"):
        chosen = list(items)
        if not chosen:
            return False, None, "no items to check", None
    else:
        idx = int(which)
        if idx >= len(items) or idx < -len(items):
            return False, None, f"there is no item {idx} (got {len(items)})", None
        chosen = [items[idx]]
    per_item = []
    for it in chosen:
        found, value = resolve_path(it, path)
        if not found:
            per_item.append((value, [{"path": path, "expected": "present", "actual": "missing", "message": "path not found"}]))
        else:
            per_item.append((value, sh.validate(shape, value)))
    if which == "any":
        if any(not v for _, v in per_item):
            return True, None, "at least one item fits the shape", note
        viol = per_item[0][1]
    else:
        bad = next((p for p in per_item if p[1]), None)
        if bad is None:
            return True, None, "fits the shape", note
        viol = bad[1]
    first = viol[0]
    where = f" at {first['path']}" if first.get("path") else ""
    return False, first.get("actual"), f"does not fit the shape{where}: {first['message']}", note
