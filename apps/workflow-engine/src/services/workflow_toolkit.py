"""One toolkit for every editor action (Stage 3A, CONTRACTS-STAGE3A §7 "AI-ready foundations").

The studio and (later) the AI both change a workflow through these functions. The editing functions are pure:
they take a definition dict and return a new one; the input is never changed. A refused edit raises ``ToolkitError``.

Ops (``apply_ops`` / ``POST /api/workflows/{id}/apply-ops``), each ``{"op": name, "args": {...}}``:

========== ===============================================================
add_node        ``{name?, type, parameters?, position?, label?, ...}``: the node's own fields
remove_node     ``{name}``: also drops its connections and its default mock
set_parameter   ``{node, path, value}``: ``path`` is dotted with ``[i]``, e.g. ``fields[0].value``
connect         ``{source, target, source_output?="main", target_input?="main"}``
disconnect      same args as connect
rename_node     ``{old, new}``: rewrites refs, connections, tests, default mocks and pins
add_test        ``{test}``: a TestCase; it always joins as a draft
update_test     ``{test_id, changes}``: refused for a locked test
set_parameters  ``{node, parameters}``: replaces the node's whole ``parameters`` object (Stage 6)
set_node_field  ``{node, field: label|disabled|on_error, value}`` (Stage 6)
set_test_default_mock ``{node, mock}``: the node's default mock in ``test_defaults.mocks`` (Stage 6)
set_interface   ``{interface}``: the definition's ``interface`` (validated; null removes it) (Stage 6)
========== ===============================================================

``check``, ``run_tests`` and ``score`` are the read-only members (they call the checker, the test runner, the scorecard).
"""

from __future__ import annotations

import copy
import re
import uuid
from typing import Any

from pydantic import ValidationError as PydanticValidationError

from ..engine.node_registry import node_registry
from ..engine.test_mode import rename_node_in_test_defaults, rename_node_in_tests

_APPROVAL = ("status", "locked", "approved_by", "approved_at")


class ToolkitError(Exception):
    """An edit that can't be made; ``message`` says why."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class OpError(ToolkitError):
    """``ToolkitError`` of one op in a batch: ``{op_index, op, message}``."""

    def __init__(self, op_index: int, op: str | None, message: str) -> None:
        super().__init__(message)
        self.op_index, self.op = op_index, op

    def detail(self) -> dict[str, Any]:
        return {"op_index": self.op_index, "op": self.op, "message": self.message}


def _node(defn: dict[str, Any], name: str) -> dict[str, Any]:
    for n in defn.get("nodes") or []:
        if n.get("name") == name:
            return n
    raise ToolkitError(f"No node named '{name}'")


# ── nodes and connections ────────────────────────────────────────────────────────────────


def add_node(defn: dict[str, Any], node: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(defn)
    node = copy.deepcopy(node or {})
    ntype = node.get("type")
    if not ntype or not node_registry.has(ntype):
        raise ToolkitError(f"Unknown node type '{ntype}'")
    names = {n.get("name") for n in out.get("nodes") or []}
    name = node.get("name")
    if not name:
        i = 1
        while f"{ntype} {i}" in names:
            i += 1
        name = f"{ntype} {i}"
    if name in names:
        raise ToolkitError(f"A node named '{name}' already exists")
    node["name"] = name
    node.setdefault("parameters", {})
    node.setdefault("position", {"x": 0, "y": 0})
    out.setdefault("nodes", []).append(node)
    return out


def remove_node(defn: dict[str, Any], name: str) -> dict[str, Any]:
    out = copy.deepcopy(defn)
    _node(out, name)
    out["nodes"] = [n for n in out["nodes"] if n.get("name") != name]
    out["connections"] = [c for c in out.get("connections") or [] if name not in (c.get("source_node"), c.get("target_node"))]
    mocks = (out.get("test_defaults") or {}).get("mocks")
    if isinstance(mocks, dict):
        mocks.pop(name, None)
    return out


_PART = re.compile(r"([^.\[\]]+)|\[(\d+)\]")


def _parse_path(path: str) -> list[Any]:
    if not path or not isinstance(path, str):
        raise ToolkitError("path is required")
    parts: list[Any] = [m.group(1) if m.group(1) is not None else int(m.group(2)) for m in _PART.finditer(path)]
    if not parts:
        raise ToolkitError(f"Bad path '{path}'")
    return parts


def set_parameter(defn: dict[str, Any], node: str, path: str, value: Any) -> dict[str, Any]:
    out = copy.deepcopy(defn)
    n = _node(out, node)
    parts = _parse_path(path)
    cur: Any = n.setdefault("parameters", {})
    for i, p in enumerate(parts[:-1]):
        nxt = parts[i + 1]
        if isinstance(p, int):
            if not isinstance(cur, list) or p >= len(cur):
                raise ToolkitError(f"'{path}' has no item {p}")
            if cur[p] is None:
                cur[p] = [] if isinstance(nxt, int) else {}
            cur = cur[p]
        else:
            if not isinstance(cur, dict):
                raise ToolkitError(f"'{path}' goes through something that isn't an object")
            if not isinstance(cur.get(p), (dict, list)):
                cur[p] = [] if isinstance(nxt, int) else {}
            cur = cur[p]
    last = parts[-1]
    if isinstance(last, int):
        if not isinstance(cur, list) or last > len(cur):
            raise ToolkitError(f"'{path}' has no item {last}")
        if last == len(cur):
            cur.append(value)
        else:
            cur[last] = value
    else:
        if not isinstance(cur, dict):
            raise ToolkitError(f"'{path}' goes through something that isn't an object")
        cur[last] = value
    return out


def _conn_key(c: dict[str, Any]) -> tuple[Any, ...]:
    return (c.get("source_node"), c.get("source_output", "main"), c.get("target_node"), c.get("target_input", "main"))


def connect(defn: dict[str, Any], source: str, target: str, source_output: str = "main", target_input: str = "main") -> dict[str, Any]:
    out = copy.deepcopy(defn)
    _node(out, source)
    _node(out, target)
    new = {"source_node": source, "target_node": target, "source_output": source_output, "target_input": target_input}
    if any(_conn_key(c) == _conn_key(new) for c in out.get("connections") or []):
        raise ToolkitError(f"{source} ({source_output}) is already connected to {target} ({target_input})")
    out.setdefault("connections", []).append(new)
    return out


def disconnect(defn: dict[str, Any], source: str, target: str, source_output: str = "main", target_input: str = "main") -> dict[str, Any]:
    out = copy.deepcopy(defn)
    key = (source, source_output, target, target_input)
    kept = [c for c in out.get("connections") or [] if _conn_key(c) != key]
    if len(kept) == len(out.get("connections") or []):
        raise ToolkitError(f"{source} ({source_output}) is not connected to {target} ({target_input})")
    out["connections"] = kept
    return out


# ── rename ───────────────────────────────────────────────────────────────────────────────


def _ref_patterns(old: str) -> list[re.Pattern[str]]:
    q = re.escape(old)
    return [
        re.compile(r"(\$node\[\s*)(['\"])" + q + r"\2(\s*\])"),
        re.compile(r"(\$\(\s*)(['\"])" + q + r"\2(\s*\))"),
    ]


def rewrite_refs(text: str, old: str, new: str) -> str:
    """``$node["old"]``, ``$node['old']``, ``$node.old`` and ``$("old")`` become the same form with ``new``."""
    for pat in _ref_patterns(old):
        text = pat.sub(lambda m: f"{m.group(1)}{m.group(2)}{new}{m.group(2)}{m.group(3)}", text)
    if re.fullmatch(r"[A-Za-z_]\w*", old):
        if re.fullmatch(r"[A-Za-z_]\w*", new):
            text = re.sub(r"(\$node\.)" + re.escape(old) + r"(?!\w)", lambda m: m.group(1) + new, text)
        else:  # the dotted form can't hold this name: switch to the bracket form
            text = re.sub(r"\$node\." + re.escape(old) + r"(?!\w)", lambda m: f'$node["{new}"]', text)
    return text


def _rewrite_deep(value: Any, old: str, new: str) -> Any:
    if isinstance(value, str):
        return rewrite_refs(value, old, new)
    if isinstance(value, list):
        return [_rewrite_deep(v, old, new) for v in value]
    if isinstance(value, dict):
        return {k: _rewrite_deep(v, old, new) for k, v in value.items()}
    return value


def rename_node(defn: dict[str, Any], old: str, new: str) -> dict[str, Any]:
    out = copy.deepcopy(defn)
    _node(out, old)
    new = (new or "").strip()
    if not new:
        raise ToolkitError("The new name can't be empty")
    if old == new:
        return out
    if any(n.get("name") == new for n in out["nodes"]):
        raise ToolkitError(f"A node named '{new}' already exists")
    for n in out["nodes"]:
        if n.get("name") == old:
            n["name"] = new
        n["parameters"] = _rewrite_deep(n.get("parameters") or {}, old, new)
    for c in out.get("connections") or []:
        if c.get("source_node") == old:
            c["source_node"] = new
        if c.get("target_node") == old:
            c["target_node"] = new
    if out.get("tests"):
        out["tests"] = _rewrite_deep(rename_node_in_tests(out["tests"], old, new), old, new)
    if out.get("test_defaults"):
        out["test_defaults"] = _rewrite_deep(rename_node_in_test_defaults(out["test_defaults"], old, new), old, new)
    for key in ("pinned", "pinned_data", "pinnedData"):  # pins keyed by node name, wherever a definition keeps them
        if isinstance(out.get(key), dict) and old in out[key]:
            out[key] = {(new if k == old else k): v for k, v in out[key].items()}
    return out


# ── tests ────────────────────────────────────────────────────────────────────────────────


def _normalize(tests: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from ..schemas.workflow import normalize_tests

    try:
        return normalize_tests(tests)
    except PydanticValidationError as e:
        err = e.errors()[0]
        raise ToolkitError(f"Invalid test: {err['msg']} ({'.'.join(str(p) for p in err['loc'])})")


def add_test(defn: dict[str, Any], test: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(defn)
    test = copy.deepcopy(test or {})
    if any(k in test and test[k] not in (None, "draft", False) for k in _APPROVAL):
        raise ToolkitError("A new test joins as a draft; approve it afterwards")
    for k in _APPROVAL:
        test.pop(k, None)
    existing = _normalize(out.get("tests") or [])
    ids = {t["id"] for t in existing}
    test.setdefault("id", "t_" + uuid.uuid4().hex[:8])
    if test["id"] in ids:
        raise ToolkitError(f"A test with id '{test['id']}' already exists")
    out["tests"] = _normalize([*existing, test])
    return out


def update_test(defn: dict[str, Any], test_id: str, changes: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(defn)
    tests = _normalize(out.get("tests") or [])
    idx = next((i for i, t in enumerate(tests) if t["id"] == test_id), None)
    if idx is None:
        raise ToolkitError(f"No test with id '{test_id}'")
    if tests[idx].get("locked"):
        raise ToolkitError(f"Test '{test_id}' is approved and locked; unlock it first")
    changes = {k: v for k, v in (changes or {}).items()}
    if "id" in changes and changes["id"] != test_id:
        raise ToolkitError("A test's id can't change")
    if any(k in changes for k in _APPROVAL):
        raise ToolkitError("status, locked and approval fields change through approve / unlock")
    tests[idx] = {**tests[idx], **changes}
    out["tests"] = _normalize(tests)
    return out


# ── Stage 6 additive ops ─────────────────────────────────────────────────────────────────

_NODE_FIELDS = ("label", "disabled", "on_error")
_ON_ERROR = ("stop", "continue", "error_output")


def set_parameters(defn: dict[str, Any], node: str, parameters: Any) -> dict[str, Any]:
    """Replace the whole ``parameters`` object of ``node`` (``set_parameter`` edits one path)."""
    if not isinstance(parameters, dict):
        raise ToolkitError("parameters must be an object")
    out = copy.deepcopy(defn)
    _node(out, node)["parameters"] = copy.deepcopy(parameters)
    return out


def set_node_field(defn: dict[str, Any], node: str, field: str, value: Any) -> dict[str, Any]:
    """Set ``label`` (text or null to clear), ``disabled`` (bool) or ``on_error`` (stop | continue | error_output)."""
    if field not in _NODE_FIELDS:
        raise ToolkitError(f"Field '{field}' can't be set here; use one of {', '.join(_NODE_FIELDS)}")
    out = copy.deepcopy(defn)
    n = _node(out, node)
    if field == "label":
        if value is not None and not isinstance(value, str):
            raise ToolkitError("label must be text or null")
        if value is None or not value.strip():
            n.pop("label", None)
        else:
            n["label"] = value.strip()[:120]
    elif field == "disabled":
        if not isinstance(value, bool):
            raise ToolkitError("disabled must be true or false")
        n["disabled"] = value
    else:
        if value not in _ON_ERROR:
            raise ToolkitError(f"on_error must be one of {', '.join(_ON_ERROR)}")
        n["on_error"] = value
        n.pop("continue_on_fail", None)  # the legacy flag would contradict it
    return out


def set_test_default_mock(defn: dict[str, Any], node: str, mock: Any) -> dict[str, Any]:
    """Set the default mock of ``node`` (used by every test that doesn't mock it itself)."""
    from ..engine.test_mode import mock_kind

    out = copy.deepcopy(defn)
    _node(out, node)
    if mock_kind(mock) == "unknown":
        raise ToolkitError("mock must be an object with one of: items, http, error, timeout, rules")
    defaults = out.get("test_defaults")
    if not isinstance(defaults, dict):
        defaults = out["test_defaults"] = {}
    if not isinstance(defaults.get("mocks"), dict):
        defaults["mocks"] = {}
    defaults["mocks"][node] = copy.deepcopy(mock)
    return out


def set_interface(defn: dict[str, Any], interface: Any) -> dict[str, Any]:
    """Set the definition's ``interface`` after validating it; ``None`` removes it."""
    from ..schemas.interface import parse_interface

    out = copy.deepcopy(defn)
    if interface is None:
        out.pop("interface", None)
        return out
    clean, errors = parse_interface(interface)
    if errors:
        first = errors[0]
        raise ToolkitError(f"Invalid interface: {first['message']}" + (f" ({first['field']})" if first.get("field") else ""))
    out["interface"] = clean
    return out


# ── read-only members ────────────────────────────────────────────────────────────────────


def check(defn: dict[str, Any], lookups: Any = None) -> dict[str, Any]:
    from .workflow_checker import check_workflow

    return check_workflow(defn, lookups=lookups)


async def run_tests(workflow_repo: Any, stored: Any, definition: dict[str, Any] | None = None, *, ids: list[str] | None = None,
                    persist: bool = False) -> dict[str, Any]:
    from .test_runner import run_tests as _run

    return await _run(workflow_repo, stored, definition, ids=ids, persist=persist)


async def score(workflow_repo: Any, stored: Any, definition: dict[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
    from .scorecard import score as _score

    return await _score(workflow_repo, stored, definition, **kwargs)


# ── ops ──────────────────────────────────────────────────────────────────────────────────

_OPS: dict[str, Any] = {
    "add_node": lambda d, a: add_node(d, a),
    "remove_node": lambda d, a: remove_node(d, a["name"]),
    "set_parameter": lambda d, a: set_parameter(d, a["node"], a["path"], a["value"]),
    "connect": lambda d, a: connect(d, a["source"], a["target"], a.get("source_output", "main"), a.get("target_input", "main")),
    "disconnect": lambda d, a: disconnect(d, a["source"], a["target"], a.get("source_output", "main"), a.get("target_input", "main")),
    "rename_node": lambda d, a: rename_node(d, a["old"], a["new"]),
    "add_test": lambda d, a: add_test(d, a["test"]),
    "update_test": lambda d, a: update_test(d, a["test_id"], a["changes"]),
    "set_parameters": lambda d, a: set_parameters(d, a["node"], a["parameters"]),
    "set_node_field": lambda d, a: set_node_field(d, a["node"], a["field"], a["value"]),
    "set_test_default_mock": lambda d, a: set_test_default_mock(d, a["node"], a["mock"]),
    "set_interface": lambda d, a: set_interface(d, a["interface"]),
}


def apply_ops(defn: dict[str, Any], ops: list[dict[str, Any]]) -> dict[str, Any]:
    """Apply ``ops`` in order; the first refused op raises ``OpError`` and nothing is returned."""
    out = copy.deepcopy(defn)
    for i, op in enumerate(ops or []):
        name = op.get("op") if isinstance(op, dict) else None
        fn = _OPS.get(name)
        if fn is None:
            raise OpError(i, name, f"Unknown op '{name}'; known ops: {', '.join(_OPS)}")
        args = op.get("args") or {}
        if not isinstance(args, dict):
            raise OpError(i, name, "args must be an object")
        try:
            out = fn(out, args)
        except ToolkitError as e:
            raise OpError(i, name, e.message)
        except KeyError as e:
            raise OpError(i, name, f"Missing argument {e}")
    return out


# ── grounded sub-workflow ids (Stage 8, DESIGN-STAGE8-REUSE section 9.2) ───────────────────


def unknown_subworkflow(op: str, args: dict[str, Any], defn: dict[str, Any], allowed: set[str]) -> str | None:
    """The reason to refuse an op that points an ``ExecuteWorkflow`` node at a workflow id that is not in ``allowed``, else None.

    ``allowed`` is the set of callable workflow ids the AI was shown (plus the ones the workflow already calls). The studio's own edits
    do not use this: a person can pick any workflow; only the model is held to the list it was given. An empty or missing id is not
    refused here (the checker reports it); a workflow id held in an expression is refused, because a call must name its child (WF006).
    """
    def ntype_of(node: Any) -> str | None:
        n = next((x for x in defn.get("nodes") or [] if x.get("name") == node), None)
        return n.get("type") if n else None

    ids: list[Any] = []
    if op == "add_node" and args.get("type") == "ExecuteWorkflow":
        ids.append((args.get("parameters") or {}).get("workflowId"))
    elif op == "set_parameter" and args.get("path") == "workflowId" and ntype_of(args.get("node")) == "ExecuteWorkflow":
        ids.append(args.get("value"))
    elif op == "set_parameters" and ntype_of(args.get("node")) == "ExecuteWorkflow":
        ids.append((args.get("parameters") or {}).get("workflowId"))
    for wid in ids:
        if wid in (None, ""):
            continue
        if not isinstance(wid, str) or wid not in allowed:
            return ("unknown sub-workflow: use the id of one of the callable_subworkflows you were given; "
                    "a workflow id can't be invented or computed.")
    return None
