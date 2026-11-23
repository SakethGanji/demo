"""Convert a selection of nodes into a sub-workflow (Stage 8, DESIGN-STAGE8-REUSE sections 7.2-7.3). Pure: no DB, no HTTP.

``extract(definition, selection, child_name, lookups) -> ExtractPlan | Refusal``

* The **child** gets an ``ExecuteWorkflowTrigger`` named ``Inputs`` (fields inferred from what reaches the selection),
  the selected nodes unchanged, and a ``Return`` NoOp when the selection leaves through a non-``main`` port.
* The **parent** gets one ``ExecuteWorkflow`` node (``version: "latest"``, ``inputMode: "map"``, ``includeItem: true``)
  wired in place of the selection.
* Anything that can't be moved faithfully is a ``Refusal`` (CS01-CS10) computed before anything is built. The route (Wave 2)
  only persists what this returns.

The new child has no id yet. The parent's ``workflowId`` holds ``CHILD_ID_PLACEHOLDER`` until the caller binds the real one
(``bind_child_id``) or passes ``child_workflow_id`` up front.

What the route has to do with the result (not done here): save the child, ``sync_refs``, try to publish it, pin ``E.version``
to ``"latest"`` or ``"draft"``, and bypass the locked-test rule when it writes ``parent_definition["tests"]`` and marks
``affected_tests`` stale.
"""

from __future__ import annotations

import copy
import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

from ..engine import shapes as sh
from ..engine.graph import TRIGGER_NODE_TYPES
from ..engine.node_registry import node_registry
from .workflow_checker import _EXPR_RE, _SKIP_EXPR_TYPES, StaticLookups, _sccs, node_shapes
from .workflow_io import CALLER_TYPES, call_refs, human_in_closure

CHILD_ID_PLACEHOLDER = "@new-subworkflow"
TRIGGER_NAME = "Inputs"
RETURN_NAME = "Return"
#: connection ports that belong to AI sub-nodes (tools, model, memory), never to the data flow
AUX_PORTS = {"tool", "tools", "model", "memory", "ai_tool", "ai_model", "ai_memory", "ai_languagemodel", "chat_model", "embedding", "retriever"}
_RESERVED = {"_triggeredAt", "_triggerType", "_executionDepth"}
_FIELD_TYPES = {"string": "string", "integer": "number", "number": "number", "boolean": "boolean", "object": "object", "array": "array"}
_SPEC_TRIGGERS = {"Webhook", "FormTrigger", "ChatInput"}  # triggers whose runs a Respond answers


# ── result types ─────────────────────────────────────────────────────────────────────────────


@dataclass
class Refusal:
    code: str  # CS01 .. CS10
    message: str
    nodes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "nodes": list(self.nodes)}


@dataclass
class ExtractPlan:
    child_name: str
    call_node: str  # the new ExecuteWorkflow node in the parent
    trigger_node: str  # the child's ExecuteWorkflowTrigger
    entry: str | None  # first selected node
    exit: dict[str, Any]  # {node, port, return_node} - what the child returns (node None = "whichever ends last")
    inputs: list[dict[str, Any]]  # the child fields: {name, type, required, origin: "item" | {node, path}}
    child_definition: dict[str, Any]
    parent_definition: dict[str, Any]
    warnings: list[dict[str, str]] = field(default_factory=list)
    affected_tests: list[dict[str, Any]] = field(default_factory=list)
    selection: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "child_name": self.child_name, "call_node": self.call_node, "trigger_node": self.trigger_node, "entry": self.entry,
            "exit": self.exit, "inputs": self.inputs, "child_definition": self.child_definition,
            "parent_definition": self.parent_definition, "warnings": self.warnings, "affected_tests": self.affected_tests,
            "selection": self.selection,
        }


class _Refuse(Exception):
    def __init__(self, code: str, message: str, nodes: list[str] | None = None) -> None:
        super().__init__(message)
        self.refusal = Refusal(code, message, nodes or [])


def bind_child_id(plan: ExtractPlan, workflow_id: str) -> dict[str, Any]:
    """The parent definition with the real child id in place of the placeholder (a copy)."""
    parent = copy.deepcopy(plan.parent_definition)
    for n in parent.get("nodes") or []:
        if n.get("name") == plan.call_node:
            n["parameters"]["workflowId"] = workflow_id
    return parent


# ── small helpers ────────────────────────────────────────────────────────────────────────────


@dataclass
class _E:
    src: str
    dst: str
    src_port: str
    dst_port: str
    raw: dict[str, Any]


def _edges(definition: dict[str, Any], names: set[str]) -> list[_E]:
    out = []
    for c in definition.get("connections") or []:
        if isinstance(c, dict) and c.get("source_node") in names and c.get("target_node") in names:
            out.append(_E(c["source_node"], c["target_node"], c.get("source_output") or "main", c.get("target_input") or "main", c))
    return out


def _is_aux(e: _E) -> bool:
    return e.dst_port.lower() in AUX_PORTS or e.src_port.lower() in AUX_PORTS


def _unique(base: str, taken: set[str]) -> str:
    if base not in taken:
        return base
    i = 2
    while f"{base} {i}" in taken:
        i += 1
    return f"{base} {i}"


def _field_name(node: str, path: str, taken: set[str]) -> str:
    name = re.sub(r"[^A-Za-z0-9_]+", "_", f"{node}_{path}").strip("_") or "input"
    if name[0].isdigit():
        name = f"f_{name}"
    cand, i = name, 2
    while cand in taken:
        cand = f"{name}_{i}"
        i += 1
    return cand


_NODE_RE = re.compile(r"""\$node(?:\[(?P<q>["'])(?P<n1>[^"']+)(?P=q)\]|\.(?P<n2>[A-Za-z_]\w*))""")
_PATH_RE = re.compile(r"\.json((?:\.[A-Za-z_]\w*)+)")


def _map_expressions(params: Any, fn) -> Any:
    """Deep copy of ``params`` with ``fn(param_path, expression_text) -> text`` applied to every ``{{ }}`` body."""
    def rewrite(path: str, text: str) -> str:
        if "{{" not in text:
            return text
        return _EXPR_RE.sub(lambda m: "{{" + fn(path, m.group(1)) + "}}", text)

    def walk(value: Any, path: str) -> Any:
        if isinstance(value, str):
            return rewrite(path, value)
        if isinstance(value, dict):
            return {k: walk(v, f"{path}.{k}" if path else str(k)) for k, v in value.items()}
        if isinstance(value, list):
            return [walk(v, f"{path}[{i}]") for i, v in enumerate(value)]
        return value

    return walk(params, "")


def _node_refs(text: str):
    """(match, referenced node name) for every ``$node["X"]`` / ``$node.X`` in an expression body."""
    for m in _NODE_RE.finditer(text):
        yield m, m.group("n1") or m.group("n2")


def _positions(nodes: list[dict[str, Any]]) -> list[tuple[float, float]]:
    out = []
    for n in nodes:
        p = n.get("position")
        if isinstance(p, dict) and isinstance(p.get("x"), (int, float)) and isinstance(p.get("y"), (int, float)):
            out.append((float(p["x"]), float(p["y"])))
    return out


def _type_of(shape: Shape | None) -> str | None:
    return _FIELD_TYPES.get((shape or {}).get("type"))


Shape = dict[str, Any]


def _example(type_: str) -> Any:
    return {"string": "example", "number": 1, "boolean": True, "object": {}, "array": []}.get(type_, "example")


# ── the algorithm ────────────────────────────────────────────────────────────────────────────


def extract(
    definition: dict[str, Any],
    selection: list[str],
    child_name: str,
    lookups: Any = None,
    *,
    parent_workflow_id: str | None = None,
    parent_name: str | None = None,
    child_workflow_id: str = CHILD_ID_PLACEHOLDER,
    sample_input: list[dict[str, Any]] | None = None,
) -> ExtractPlan | Refusal:
    """Plan the conversion of ``selection`` (node names of ``definition``) into a sub-workflow called ``child_name``.

    ``lookups`` (optional) feeds the shape inference and the Approval / cycle checks; ``parent_workflow_id`` enables CS10.
    ``sample_input`` (items of the latest good run at the entry) seeds the child's draft test.
    """
    try:
        return _extract(definition or {}, list(selection or []), child_name, lookups, parent_workflow_id, parent_name,
                        child_workflow_id, sample_input)
    except _Refuse as r:
        return r.refusal


def _extract(definition, selection, child_name, lookups, parent_id, parent_name, child_id, sample_input) -> ExtractPlan:
    if not node_registry.list():
        from ..engine.node_registry import register_all_nodes

        register_all_nodes()
    lookups = lookups if lookups is not None else StaticLookups()
    nodes_raw = [n for n in definition.get("nodes") or [] if isinstance(n, dict) and n.get("name") and n.get("type")]
    by_name = {str(n["name"]): n for n in nodes_raw}
    order = list(by_name)
    S_list = list(dict.fromkeys(str(x) for x in selection))
    warnings: list[dict[str, str]] = []

    # CS01 -------------------------------------------------------------------------------
    missing = [n for n in S_list if n not in by_name]
    if missing:
        raise _Refuse("CS01", f"{', '.join(missing)} isn't in this workflow. Select the steps to move.", missing)
    if not S_list or (len(S_list) == 1 and by_name[S_list[0]]["type"] in ("NoOp", "StickyNote")):
        raise _Refuse("CS01", "Select the steps to move.", S_list)
    S = set(S_list)
    S_ordered = [n for n in order if n in S]
    # CS02 -------------------------------------------------------------------------------
    trig = [n for n in S_ordered if by_name[n]["type"] in TRIGGER_NODE_TYPES]
    if trig:
        raise _Refuse("CS02", "Triggers stay in the parent.", trig)
    # CS06 -------------------------------------------------------------------------------
    approvals = [n for n in S_ordered if by_name[n]["type"] == "Approval"]
    if approvals:
        raise _Refuse("CS06", "Approvals stay in the top-level workflow.", approvals)
    for n in S_ordered:
        if by_name[n]["type"] in CALLER_TYPES:
            for r in call_refs({"nodes": [by_name[n]]}):
                path = human_in_closure(_defn_of(lookups, r.workflow_id, r.spec), lookups)
                if path is not None:
                    raise _Refuse("CS06", f"Approvals stay in the top-level workflow ('{n}' calls a sub-workflow that has one).", [n])
    answers_caller = any(x["type"] in _SPEC_TRIGGERS for x in nodes_raw) or bool(definition.get("interface"))
    responds = [n for n in S_ordered if by_name[n]["type"] == "Respond"]
    if responds and answers_caller:
        raise _Refuse("CS06", "This Respond answers the caller; it can't move.", responds)

    edges = _edges(definition, set(by_name))
    # CS09 -------------------------------------------------------------------------------
    for e in edges:
        if (e.src in S) != (e.dst in S) and _is_aux(e):
            raise _Refuse("CS09", "Move the agent together with its tools.", [e.src, e.dst])
    adj: dict[str, list[str]] = {n: [] for n in order}
    for e in edges:
        adj[e.src].append(e.dst)
    for comp in _sccs(order, adj):
        loop = len(comp) > 1 or comp[0] in adj[comp[0]]
        if loop and any(by_name[n]["type"] == "Loop" for n in comp) and 0 < len(S & set(comp)) < len(comp):
            raise _Refuse("CS09", "Select the whole loop.", [n for n in order if n in comp])

    # CS05: convexity --------------------------------------------------------------------
    fwd = _reach_outside(edges, S, forward=True)
    bwd = _reach_outside(edges, S, forward=False)
    between = [n for n in order if n in fwd and n in bwd]
    if between:
        names = ", ".join(between)
        raise _Refuse("CS05", f"{names} sit{'s' if len(between) == 1 else ''} between selected steps; include {'it' if len(between) == 1 else 'them'} or leave both out.", between)

    # CS03: one entry --------------------------------------------------------------------
    inc = [e for e in edges if e.dst in S and e.src not in S]
    out_all = [e for e in edges if e.src in S and e.dst not in S]
    has_in = {e.dst for e in edges}
    entries = list(dict.fromkeys([e.dst for e in inc] + [n for n in S_ordered if n not in has_in]))
    entries = [n for n in order if n in entries]
    if len(entries) > 1:
        raise _Refuse("CS03", f"The selection has {len(entries)} starting points ({', '.join(entries)}). Include the step where they meet, or select less.", entries)
    if not entries:
        raise _Refuse("CS03", "The selection has no starting point. Select the steps from where the flow enters.", S_ordered)
    entry = entries[0]
    if _input_count(by_name[entry]["type"]) > 1:
        raise _Refuse("CS03", f"The selection has several starting points: {entry} takes more than one input. Include the step that feeds it.", [entry])

    # CS04: one exit ---------------------------------------------------------------------
    points = list(dict.fromkeys((e.src, e.src_port) for e in out_all if e.src_port != "error"))
    err_srcs = {e.src for e in out_all if e.src_port == "error"}
    inner_sinks = [n for n in S_ordered if not any(e.src == n and e.dst in S for e in edges)]
    exit_node: str | None
    exit_port = "main"
    if len(points) > 1:
        raise _Refuse("CS04", f"The selection hands data out from {len(points)} places; a sub-workflow returns one.",
                      list(dict.fromkeys(a for a, _ in points)))
    if points:
        exit_node, exit_port = points[0]
    elif len(inner_sinks) == 1:
        exit_node = inner_sinks[0]
    else:
        exit_node = None
        warnings.append({"code": "CSW1", "message": "Several end nodes; the sub-workflow returns whichever runs last."})
    if err_srcs - ({exit_node} if exit_node else set()):
        bad = sorted(err_srcs - ({exit_node} if exit_node else set()), key=order.index)
        raise _Refuse("CS04", f"The selection hands errors out from {len(bad)} place{'s' if len(bad) > 1 else ''} ({', '.join(bad)}); a sub-workflow returns one.", bad)
    if exit_node and points and [n for n in inner_sinks if n != exit_node]:
        warnings.append({"code": "CSW1", "message": "Several end nodes; the sub-workflow returns whichever runs last."})

    # CS10 -------------------------------------------------------------------------------
    if parent_id:
        graph = {k: [c for c, _ in v] for k, v in (_call(lookups, "call_graph") or {}).items()}
        for n in S_ordered:
            for r in call_refs({"nodes": [by_name[n]]}):
                if r.workflow_id == parent_id or _reaches(graph, r.workflow_id, parent_id):
                    raise _Refuse("CS10", f"This would make '{parent_name or parent_id}' and '{child_name}' call each other.", [n])

    # shapes (for the child's input fields) ----------------------------------------------
    try:
        shapes = node_shapes(definition, lookups)
    except Exception:
        shapes = {}

    # step 4: outside references in the selection -> child inputs -------------------------
    trigger_name = _unique(TRIGGER_NAME, S)
    added: list[dict[str, Any]] = []  # {name, node, path, type}
    added_by_key: dict[tuple[str, str], str] = {}
    entry_in = _entry_shape(shapes, entry)
    entry_fields = _fields_from_shape(entry_in)
    taken_fields = {f["name"] for f in entry_fields}

    def outside_ref(owner: str):
        def fn(param: str, text: str) -> str:
            out, last = [], 0
            for m, ref in _node_refs(text):
                if ref in S or ref not in by_name:
                    continue
                rest = text[m.end():]
                pm = _PATH_RE.match(rest)
                if pm is None or rest[pm.end():].lstrip().startswith("("):
                    raise _Refuse("CS07", f"'{owner}' reads \"{{{{ {text.strip()} }}}}\" from '{ref}' in a way that can't be turned into an input "
                                          f"(parameter {param or 'value'}). Map the fields it needs explicitly, or move '{ref}' too.", [owner, ref])
                path = pm.group(1)[1:]
                key = (ref, path)
                if key not in added_by_key:
                    fld = _field_name(ref, path, taken_fields | set(added_by_key.values()))
                    added_by_key[key] = fld
                    added.append({"name": fld, "node": ref, "path": path, "type": _type_of(_ref_shape(shapes, ref, path))})
                out.append(text[last:m.start()] + f'$node["{trigger_name}"].json.{added_by_key[key]}')
                last = m.end() + pm.end()
            out.append(text[last:])
            return "".join(out)
        return fn

    child_nodes: list[dict[str, Any]] = []
    for n in S_ordered:
        nd = copy.deepcopy(by_name[n])
        if nd["type"] not in _SKIP_EXPR_TYPES and isinstance(nd.get("parameters"), dict):
            nd["parameters"] = _map_expressions(nd["parameters"], outside_ref(n))
        child_nodes.append(nd)

    # step 5: outside nodes that read inner nodes ------------------------------------------
    call_name = _unique(child_name.strip() or "Sub-workflow", set(order) - S)

    def inside_ref(owner: str):
        def fn(param: str, text: str) -> str:
            out, last = [], 0
            for m, ref in _node_refs(text):
                if ref not in S:
                    continue
                if ref != exit_node:
                    raise _Refuse("CS08", f"'{owner}' reads '{ref}', which is inside the selection. Only the last step ('{exit_node or 'none'}') can be read from outside.", [owner, ref])
                out.append(text[last:m.start()] + f'$node["{call_name}"]')
                last = m.end()
            out.append(text[last:])
            return "".join(out)
        return fn

    parent_nodes: list[dict[str, Any]] = []
    for n in order:
        if n in S:
            continue
        nd = copy.deepcopy(by_name[n])
        if nd["type"] not in _SKIP_EXPR_TYPES and isinstance(nd.get("parameters"), dict):
            nd["parameters"] = _map_expressions(nd["parameters"], inside_ref(n))
        parent_nodes.append(nd)

    # step 6: the child ----------------------------------------------------------------------
    fields = [dict(f) for f in entry_fields]
    unknown_added = [a["name"] for a in added if a["type"] is None]
    for a in added:
        fields.append({"name": a["name"], "type": a["type"] or "object", "required": True, "description": f"{a['node']}.{a['path']}"})
    passthrough = entry_in is None or not sh.is_closed_object(entry_in) or any(f["type"] is None for f in fields) or bool(unknown_added)
    if passthrough:
        fields = [f for f in fields if f["type"] is not None]
        why = "what reaches the selection has no known shape yet" if (entry_in is None or not sh.is_closed_object(entry_in)) else \
            f"the type of {', '.join(unknown_added) or 'some fields'} isn't known"
        warnings.append({"code": "WF029", "message": f"The sub-workflow accepts any input because {why}; declare its fields to type-check calls."})
    param_fields = [{"name": f["name"], "type": f["type"], "required": f["required"], "description": f.get("description", "")} for f in fields]

    pos = _positions([by_name[n] for n in S_ordered])
    minx = min((p[0] for p in pos), default=0.0)
    miny = min((p[1] for p in pos), default=0.0)
    for nd in child_nodes:
        p = nd.get("position")
        if isinstance(p, dict) and isinstance(p.get("x"), (int, float)) and isinstance(p.get("y"), (int, float)):
            nd["position"] = {"x": 320.0 + p["x"] - minx, "y": 120.0 + p["y"] - miny}
    entry_pos = next((n["position"] for n in child_nodes if n["name"] == entry and n.get("position")), None)
    trigger = {
        "name": trigger_name, "type": "ExecuteWorkflowTrigger", "label": None,
        "parameters": {"inputSource": "passthrough" if passthrough else "fields", "fields": param_fields},
        "position": {"x": 60.0, "y": entry_pos["y"] if entry_pos else 120.0}, "disabled": False, "on_error": "stop",
    }
    child_conns: list[dict[str, Any]] = [{"source_node": trigger_name, "target_node": entry, "source_output": "main", "target_input": "main"}]
    for e in edges:
        if e.src in S and e.dst in S:
            child_conns.append({k: v for k, v in e.raw.items() if k != "waypoints"})
    return_node: str | None = None
    exit_name = exit_node
    if exit_node and exit_port not in ("main", "error"):
        return_node = _unique(RETURN_NAME, S | {trigger_name})
        ex_pos = next((n["position"] for n in child_nodes if n["name"] == exit_node and n.get("position")), None)
        child_nodes.append({"name": return_node, "type": "NoOp", "parameters": {}, "disabled": False, "on_error": "stop",
                            **({"position": {"x": ex_pos["x"] + 240.0, "y": ex_pos["y"]}} if ex_pos else {})})
        child_conns.append({"source_node": exit_node, "target_node": return_node, "source_output": exit_port, "target_input": "main"})
        exit_name = return_node

    exit_error_routed = bool(exit_node and err_srcs)
    if exit_error_routed:
        for nd in child_nodes:
            if nd["name"] == exit_node and nd.get("on_error") == "error_output":
                nd["on_error"] = "stop"  # the failure now travels to the call node, whose error port the old edges move to
        warnings.append({"code": "CSW2", "message": f"Errors from '{exit_node}' now leave through the '{call_name}' error output; the step itself stops on error."})

    # step 8: tests -----------------------------------------------------------------------
    tests_out, affected, moved_mocks = _remap_tests(definition.get("tests"), S, exit_node, call_name)
    defaults = copy.deepcopy(definition.get("test_defaults")) if isinstance(definition.get("test_defaults"), dict) else None
    child_default_mocks: dict[str, Any] = dict(moved_mocks)
    if defaults and isinstance(defaults.get("mocks"), dict):
        parent_mocks: dict[str, Any] = {}
        for node, mock in defaults["mocks"].items():
            if node == exit_node:
                parent_mocks[call_name] = _as_call_mock(mock)
            elif node in S:
                child_default_mocks.setdefault(node, mock)
            else:
                parent_mocks[node] = mock
        defaults["mocks"] = parent_mocks

    child_def: dict[str, Any] = {"nodes": [trigger, *child_nodes], "connections": child_conns,
                                 "settings": {"returnNode": return_node} if return_node else {}}
    if child_default_mocks:
        child_def["test_defaults"] = {"mocks": child_default_mocks}
    child_def["tests"] = [_draft_test(child_name, param_fields, sample_input, exit_name)]

    # step 7: the parent ------------------------------------------------------------------
    inputs_map = {a["name"]: '{{ $node["%s"].json.%s }}' % (a["node"], a["path"]) for a in added}
    call: dict[str, Any] = {
        "name": call_name, "type": "ExecuteWorkflow", "label": None,
        "parameters": {"workflowId": child_id, "version": "latest", "inputMode": "map", "inputs": inputs_map, "includeItem": True,
                       "mode": "once", "timeoutSeconds": 300},
        "disabled": False, "on_error": "error_output" if exit_error_routed else "stop", "retry_on_fail": 0, "retry_delay": 1000,
    }
    if pos:
        call["position"] = {"x": sum(p[0] for p in pos) / len(pos), "y": sum(p[1] for p in pos) / len(pos)}
    parent_nodes.append(call)

    parent_conns: list[dict[str, Any]] = []
    seen: set[tuple] = set()
    for e in edges:
        a, b = e.src in S, e.dst in S
        if a and b:
            continue
        if not a and not b:
            parent_conns.append(copy.deepcopy(e.raw))
            continue
        if b:  # into the selection: now into the call
            c = {"source_node": e.src, "target_node": call_name, "source_output": e.src_port, "target_input": "main"}
        else:  # out of the selection: now out of the call
            c = {"source_node": call_name, "target_node": e.dst, "source_output": "error" if e.src_port == "error" else "main", "target_input": e.dst_port}
        key = tuple(sorted(c.items()))
        if key not in seen:
            seen.add(key)
            parent_conns.append(c)

    parent_def = copy.deepcopy({k: v for k, v in definition.items() if k not in ("nodes", "connections", "tests", "test_defaults")})
    parent_def["nodes"] = parent_nodes
    parent_def["connections"] = parent_conns
    if tests_out is not None:
        parent_def["tests"] = tests_out
    if defaults is not None:
        parent_def["test_defaults"] = defaults
    iface = parent_def.get("interface")
    if isinstance(iface, dict) and iface.get("result") in S:
        if iface["result"] == exit_node:
            iface["result"] = call_name
        else:
            warnings.append({"code": "CSW4", "message": f"The interface shows '{iface['result']}', which moved into the sub-workflow."})

    inputs_info = [
        {"name": f["name"], "type": f["type"], "required": f["required"], "origin": "item"} for f in entry_fields
    ] + [
        {"name": a["name"], "type": a["type"] or "object", "required": True, "origin": {"node": a["node"], "path": a["path"]}} for a in added
    ]
    return ExtractPlan(
        child_name=child_name, call_node=call_name, trigger_node=trigger_name, entry=entry,
        exit={"node": exit_node, "port": exit_port, "return_node": return_node},
        inputs=inputs_info, child_definition=child_def, parent_definition=parent_def, warnings=warnings,
        affected_tests=affected, selection=S_ordered,
    )


# ── pieces ───────────────────────────────────────────────────────────────────────────────────


def _call(lookups: Any, method: str, *args: Any) -> Any:
    fn = getattr(lookups, method, None)
    try:
        return fn(*args) if fn else None
    except Exception:
        return None


def _defn_of(lookups: Any, workflow_id: str, spec: str) -> dict[str, Any] | None:
    d = _call(lookups, "workflow_definition", workflow_id, int(spec) if spec.isdigit() else spec)
    return d if isinstance(d, dict) else None


def _reaches(graph: dict[str, list[str]], start: str, target: str) -> bool:
    seen, todo = {start}, [start]
    while todo:
        cur = todo.pop()
        if cur == target:
            return True
        for nxt in graph.get(cur, []):
            if nxt not in seen:
                seen.add(nxt)
                todo.append(nxt)
    return False


def _input_count(node_type: str) -> int | float:
    try:
        return node_registry.get(node_type).input_count if node_registry.has(node_type) else 1
    except Exception:
        return 1


def _reach_outside(edges: list[_E], S: set[str], *, forward: bool) -> set[str]:
    """Nodes outside S reachable from S (forward) / that reach S (backward) through outside nodes only."""
    nxt: dict[str, list[str]] = {}
    for e in edges:
        a, b = (e.src, e.dst) if forward else (e.dst, e.src)
        nxt.setdefault(a, []).append(b)
    seen: set[str] = set()
    todo = [b for s in S for b in nxt.get(s, []) if b not in S]
    while todo:
        cur = todo.pop()
        if cur in seen:
            continue
        seen.add(cur)
        todo.extend(b for b in nxt.get(cur, []) if b not in S and b not in seen)
    return seen


def _entry_shape(shapes: dict[str, Any], entry: str) -> Shape | None:
    ins = (shapes.get(entry) or {}).get("inputs") or {}
    if not ins:
        return None
    s = sh.merge_all(ins.values()) if len(ins) > 1 else next(iter(ins.values()))
    return None if sh.is_unknown(s) else s


def _fields_from_shape(shape: Shape | None) -> list[dict[str, Any]]:
    if not sh.is_closed_object(shape):
        return []
    req = set(shape.get("required") or [])
    return [
        {"name": k, "type": _type_of(v), "required": k in req, "description": (v or {}).get("description") or ""}
        for k, v in shape["properties"].items() if k not in _RESERVED
    ]


def _ref_shape(shapes: dict[str, Any], node: str, path: str) -> Shape | None:
    outs = (shapes.get(node) or {}).get("outputs") or {}
    ports = [s for p, s in outs.items() if p != "error"]
    if not ports:
        return None
    root = sh.merge_all(ports) if len(ports) > 1 else ports[0]
    return sh.lookup(root, path.split(".")) if not sh.is_unknown(root) else None


def _as_call_mock(mock: Any) -> Any:
    """A mock on the last selected step, as a mock of the call node (an ``http`` mock becomes the items it would produce)."""
    if isinstance(mock, dict) and isinstance(mock.get("http"), dict):
        h = mock["http"]
        return {"items": [{"statusCode": h.get("status", 200), "headers": h.get("headers") or {}, "body": h.get("body")}]}
    return copy.deepcopy(mock)


def _remap_tests(tests: Any, S: set[str], exit_node: str | None, call_name: str) -> tuple[list[dict[str, Any]] | None, list[dict[str, Any]], dict[str, Any]]:
    """(rewritten tests, affected tests, mocks that moved into the child). Tests that never touch S are returned as they were."""
    if not isinstance(tests, list):
        return None, [], {}
    out: list[dict[str, Any]] = []
    affected: list[dict[str, Any]] = []
    moved: dict[str, Any] = {}

    def m(name: Any) -> Any:
        return call_name if name == exit_node and exit_node is not None else name

    for t in tests:
        if not isinstance(t, dict):
            out.append(t)
            continue
        t = copy.deepcopy(t)
        touched: set[str] = set()
        tgt = t.get("target") if isinstance(t.get("target"), dict) else {}
        for k in ("node", "from", "to"):
            if tgt.get(k) in S:
                touched.add(tgt[k])
                tgt[k] = m(tgt[k])
        stale_nodes: set[str] = set()  # the exit step stays "mapped" to the call, but a request it made is no longer made by the call
        for a in t.get("expect") or []:
            if isinstance(a, dict) and a.get("node") in S:
                touched.add(a["node"])
                if a.get("subject") == "request":
                    stale_nodes.add(a["node"])
                a["node"] = m(a["node"])
        if isinstance(t.get("covers"), list):
            seen = []
            for c in t["covers"]:
                if c in S:
                    touched.add(c)
                c = m(c)
                if c not in seen:
                    seen.append(c)
            t["covers"] = seen
        mocks = t.get("mocks") if isinstance(t.get("mocks"), dict) else {}
        for node in list(mocks):
            if node in S:
                mock = mocks.pop(node)
                if node == exit_node:
                    mocks[call_name] = _as_call_mock(mock)
                else:
                    moved.setdefault(node, mock)
        left = sorted({n for n in touched if n != exit_node} | stale_nodes)
        if left:
            affected.append({"id": t.get("id"), "name": t.get("name"), "nodes": left})
        out.append(t)
    return out, affected, moved


def _draft_test(child_name: str, fields: list[dict[str, Any]], sample: list[dict[str, Any]] | None, exit_name: str | None) -> dict[str, Any]:
    items = [dict(s) for s in sample if isinstance(s, dict)] if sample else [{f["name"]: _example(f["type"]) for f in fields}]
    expect: list[dict[str, Any]] = []
    if exit_name:
        expect = [{"node": exit_name, "op": "status", "value": "success"}, {"node": exit_name, "op": "shape"}]
    return {
        "id": "t_" + hashlib.sha1(child_name.encode()).hexdigest()[:8],
        "name": "Runs with a typical input", "description": "Generated when the steps were moved; review it before relying on it.",
        "target": {"kind": "workflow"}, "input": {"items": items or [{}]}, "mocks": {}, "expect": expect,
        "category": "normal", "status": "draft", "locked": False, "source": "suggested",
    }
