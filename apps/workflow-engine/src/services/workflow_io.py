"""What a workflow takes and gives when another workflow calls it (Stage 8, DESIGN-STAGE8-REUSE sections 2.1-2.4).

Pure helpers, no I/O. The checker (ExecuteWorkflow contracts, WF020-WF029) and convert-selection both read a
workflow's *declared inputs* and *derived output* through here.

* ``declared_input(definition)``  - the child's ``ExecuteWorkflowTrigger`` and the Shape its ``inputSource`` declares.
* ``derive_io(definition, lookups)`` - ``WorkflowIO``: input Shape, output Shape (the return node's), ``has_human``
  (an ``Approval`` anywhere in the call closure) and the reasons it is or is not callable.
* ``call_refs(definition)``       - the sub-workflow calls a definition makes (``ExecuteWorkflow`` and ``WorkflowTool``).
* ``human_in_closure(...)``       - where an ``Approval`` hides in a workflow's call closure (WF025).

``lookups`` is optional and duck-typed (the checker's ``StaticLookups`` or the DB-built one). Methods used, all optional:
``workflow_definition(workflow_id, version)`` and ``workflow_meta(workflow_id)``.

Return-node rule (section 2.3 / 2.4): with ``Respond`` nodes the output is what reaches them (equal shapes collapse to
one, otherwise a lenient union); without, the output of the terminal nodes. More than 3 distinct shapes, or any unknown
one, make the output UNKNOWN (reason recorded; the checker reports WF026).
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from typing import Any, NamedTuple

from ..engine import shapes as sh

Shape = dict[str, Any]

TRIGGER_TYPE = "ExecuteWorkflowTrigger"
#: node types that call another workflow, and the version they pin when the node says nothing
CALLER_TYPES = {"ExecuteWorkflow": "draft", "WorkflowTool": "latest"}
MAX_DISTINCT_OUTPUT_SHAPES = 3


# ── references ───────────────────────────────────────────────────────────────────────────────


class CallRef(NamedTuple):
    node: str
    node_type: str
    workflow_id: str
    spec: str  # "draft" | "latest" | "<n>"
    disabled: bool
    pin_node: str | None = None  # the name a release pins this call under at run time (an agent's Run Workflow tool)


def version_spec(raw: Any, default: str = "draft") -> str:
    """``'draft'`` | ``'latest'`` | ``'<n>'`` from a node's ``version`` parameter (anything else is ``default``)."""
    if raw is None or raw == "" or isinstance(raw, bool):
        return default
    if isinstance(raw, (int, float)) and float(raw).is_integer() and raw >= 1:
        return str(int(raw))
    if isinstance(raw, str):
        v = raw.strip().lower()
        if v in ("draft", "latest"):
            return v
        if v.isdigit() and int(v) >= 1:
            return str(int(v))
    return default


def is_expression(value: Any) -> bool:
    return isinstance(value, str) and (value.lstrip().startswith("=") or "{{" in value)


def literal_workflow_id(params: dict[str, Any] | None) -> str | None:
    """The ``workflowId`` of a call node when it is a plain id (not empty, not an expression, not a locator object)."""
    wid = (params or {}).get("workflowId")
    if isinstance(wid, dict):  # an n8n-style resource locator
        wid = wid.get("value")
    if not isinstance(wid, str) or not wid.strip() or is_expression(wid):
        return None
    return wid.strip()


def agent_tool_calls(node: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """``(tool_name, parameters)`` of each built-in "Run Workflow" tool of an AIAgent node (``builtinTools[{type: workflow}]``)."""
    if node.get("type") != "AIAgent":
        return []
    params = node.get("parameters") if isinstance(node.get("parameters"), dict) else {}
    specs = params.get("builtinTools")
    out: list[tuple[str, dict[str, Any]]] = []
    for spec in specs if isinstance(specs, list) else []:
        if not isinstance(spec, dict) or (spec.get("type") or spec.get("key")) != "workflow":
            continue
        tp = spec.get("parameters") or spec.get("params")
        tp = tp if isinstance(tp, dict) else {}
        out.append((str(tp.get("toolName") or "run_workflow"), tp))
    return out


def agent_pin_node(agent_name: str, tool_name: str) -> str:
    """The parent-node name the runtime uses for an agent tool's call (and so its release pin key)."""
    return f"{agent_name}/{tool_name}"


def call_refs(definition: dict[str, Any] | None) -> list[CallRef]:
    """Every static sub-workflow call of a definition, in node order (one per node name)."""
    out: list[CallRef] = []
    seen: set[str] = set()
    for n in (definition or {}).get("nodes") or []:
        if not isinstance(n, dict):
            continue
        default = CALLER_TYPES.get(n.get("type"))
        name = n.get("name")
        if name and n.get("type") == "AIAgent":
            for tool, tp in agent_tool_calls(n):
                wid = literal_workflow_id(tp)
                pin = agent_pin_node(str(name), tool)
                if wid is None or pin in seen:
                    continue
                seen.add(pin)
                out.append(CallRef(str(name), "WorkflowTool", wid, version_spec(tp.get("version"), "latest"), bool(n.get("disabled")), pin))
            continue
        if default is None or not name or name in seen:
            continue
        params = n.get("parameters") if isinstance(n.get("parameters"), dict) else {}
        wid = literal_workflow_id(params)
        if wid is None:
            continue
        seen.add(name)
        out.append(CallRef(str(name), str(n["type"]), wid, version_spec(params.get("version"), default), bool(n.get("disabled"))))
    return out


# ── declared inputs ──────────────────────────────────────────────────────────────────────────

_FIELD_TYPE = {"integer": "number", "number": "number", "string": "string", "boolean": "boolean", "object": "object", "array": "array"}


@dataclass
class InputDecl:
    has_trigger: bool = False
    trigger: str | None = None  # node name
    source: str | None = None  # fields | jsonSchema | passthrough | None (legacy, nothing declared)
    shape: Shape = field(default_factory=sh.unknown)  # the declared Shape (UNKNOWN when nothing is declared)
    fields: list[dict[str, Any]] = field(default_factory=list)  # [{name, type, required, description}]


def trigger_node(definition: dict[str, Any] | None) -> dict[str, Any] | None:
    """The workflow's ``ExecuteWorkflowTrigger`` (an enabled one wins), or None."""
    found = [n for n in (definition or {}).get("nodes") or [] if isinstance(n, dict) and n.get("type") == TRIGGER_TYPE]
    return next((n for n in found if not n.get("disabled")), found[0] if found else None)


def fields_of(shape: Shape | None) -> list[dict[str, Any]]:
    """``[{name, type, required, description}]`` for a closed-object Shape, in property order."""
    if not sh.is_closed_object(shape):
        return []
    req = set(shape.get("required") or [])
    out = []
    for name, s in shape["properties"].items():
        if str(name).startswith("_"):
            continue
        out.append({
            "name": name,
            "type": _FIELD_TYPE.get((s or {}).get("type"), "any"),
            "required": name in req,
            "description": (s or {}).get("description") or "",
        })
    return out


def declared_input(definition: dict[str, Any] | None) -> InputDecl:
    """What the workflow's ``ExecuteWorkflowTrigger`` declares as input (section 2.1)."""
    from ..nodes.triggers.execute_workflow_trigger import declared_input_shape, input_source

    node = trigger_node(definition)
    if node is None:
        return InputDecl()
    params = node.get("parameters") if isinstance(node.get("parameters"), dict) else {}
    try:
        shape = declared_input_shape(params)
    except Exception:  # odd parameters: nothing declared
        shape = None
    shape = shape if shape is not None else sh.unknown()
    return InputDecl(True, str(node.get("name")), input_source(params), shape, fields_of(shape))


# ── the result ───────────────────────────────────────────────────────────────────────────────


@dataclass
class WorkflowIO:
    """Everything a caller (or the picker) needs to know about a workflow as a sub-workflow."""

    input: Shape = field(default_factory=sh.unknown)  # the trigger's declared input, without the ``_`` fields
    output: Shape = field(default_factory=sh.unknown)  # the return node's items
    input_source: str | None = None
    input_fields: list[dict[str, Any]] = field(default_factory=list)
    output_fields: list[str] = field(default_factory=list)
    has_trigger: bool = False
    has_human: bool = False
    human_path: list[str] = field(default_factory=list)  # names from the child down to the workflow holding the Approval
    output_note: str | None = None  # why the output is UNKNOWN (WF026)
    callable: bool = True
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "input": self.input, "output": self.output, "input_source": self.input_source, "input_fields": self.input_fields,
            "output_fields": self.output_fields, "has_trigger": self.has_trigger, "has_human": self.has_human,
            "output_note": self.output_note, "callable": self.callable, "reasons": list(self.reasons),
        }


def _norm(shape: Shape) -> str:
    def strip(s: Any) -> Any:
        if isinstance(s, dict):
            return {k: strip(v) for k, v in s.items() if k != "x-source"}
        if isinstance(s, list):
            return [strip(v) for v in s]
        return s

    return json.dumps(strip(shape), sort_keys=True, default=str)


def _port_union(outs: dict[str, Shape]) -> Shape:
    ports = [s for p, s in outs.items() if p != "error"]
    if not ports:
        return sh.unknown()
    return copy.deepcopy(ports[0]) if len(ports) == 1 else sh.merge_all(ports)


def _reachable(definition: dict[str, Any], start: str) -> set[str]:
    adj: dict[str, list[str]] = {}
    for c in definition.get("connections") or []:
        if isinstance(c, dict):
            adj.setdefault(str(c.get("source_node")), []).append(str(c.get("target_node")))
    seen, todo = {start}, [start]
    while todo:
        for nxt in adj.get(todo.pop(), []):
            if nxt not in seen:
                seen.add(nxt)
                todo.append(nxt)
    return seen


def output_of(definition: dict[str, Any], shapes_by_node: dict[str, dict[str, Any]]) -> tuple[Shape, str | None]:
    """(output Shape, reason it is UNKNOWN or None) from the checker's node shapes - the return-node rule of section 2.3."""
    trig = trigger_node(definition)
    nodes = {str(n["name"]): n for n in definition.get("nodes") or [] if isinstance(n, dict) and n.get("name")}
    reach = _reachable(definition, str(trig["name"])) if trig else set(nodes)
    sources = {str(c.get("source_node")) for c in definition.get("connections") or [] if isinstance(c, dict)}

    responds = [n for n, d in nodes.items() if d.get("type") == "Respond" and not d.get("disabled") and n in reach]
    if responds:
        cands = [sh.merge_all((shapes_by_node.get(n) or {}).get("inputs", {}).values()) if (shapes_by_node.get(n) or {}).get("inputs") else sh.unknown()
                 for n in responds]
    else:
        terminals = [n for n in nodes if n in reach and n not in sources and nodes[n].get("type") != "StickyNote"]
        cands = [_port_union((shapes_by_node.get(n) or {}).get("outputs") or {}) for n in terminals]
    if not cands:
        return sh.unknown(), None
    if any(sh.is_unknown(c) for c in cands):
        return sh.unknown(), "some of what it returns has no known shape yet"
    distinct: dict[str, Shape] = {}
    for c in cands:
        distinct.setdefault(_norm(c), c)
    if len(distinct) == 1:
        return copy.deepcopy(next(iter(distinct.values()))), None
    if len(distinct) > MAX_DISTINCT_OUTPUT_SHAPES:
        return sh.unknown(), f"it can return {len(distinct)} different shapes"
    return sh.merge_all(distinct.values()), None


# ── Approval anywhere below ──────────────────────────────────────────────────────────────────


def _definition_of(lookups: Any, workflow_id: str, spec: str) -> dict[str, Any] | None:
    fn = getattr(lookups, "workflow_definition", None)
    if fn is None:
        return None
    try:
        d = fn(workflow_id, int(spec) if spec.isdigit() else spec)
    except Exception:
        return None
    return d if isinstance(d, dict) else None


def _name_of(lookups: Any, workflow_id: str) -> str:
    fn = getattr(lookups, "workflow_meta", None)
    meta = fn(workflow_id) if fn else None
    return str((meta or {}).get("name") or workflow_id)


def human_in_closure(definition: dict[str, Any] | None, lookups: Any = None, _seen: set[tuple[str, str]] | None = None) -> list[str] | None:
    """The path to an ``Approval`` below ``definition`` (the definition itself first), or None.

    A path is the names of the workflows crossed, ending with the one that holds the Approval; ``[]`` means the
    definition itself has it. Like the runtime guard, a disabled Approval still counts (the scan is by node type).
    """
    if any(isinstance(n, dict) and n.get("type") == "Approval" for n in (definition or {}).get("nodes") or []):
        return []
    seen = _seen if _seen is not None else set()
    for ref in call_refs(definition):
        key = (ref.workflow_id, ref.spec)
        if key in seen:
            continue
        seen.add(key)
        child = _definition_of(lookups, ref.workflow_id, ref.spec)
        if child is None:
            continue
        below = human_in_closure(child, lookups, seen)
        if below is not None:
            return [_name_of(lookups, ref.workflow_id), *below]
    return None


# ── derive_io ────────────────────────────────────────────────────────────────────────────────


def derive_io(definition: dict[str, Any] | None, lookups: Any = None) -> WorkflowIO:
    """The ``WorkflowIO`` of a definition (section 2.4). Never raises for odd input."""
    from .workflow_checker import StaticLookups, node_shapes

    definition = definition or {}
    decl = declared_input(definition)
    io = WorkflowIO(input=copy.deepcopy(decl.shape), input_source=decl.source, input_fields=decl.fields, has_trigger=decl.has_trigger)

    try:
        shapes = node_shapes(definition, lookups if lookups is not None else StaticLookups())
    except Exception:
        shapes = {}
    io.output, io.output_note = output_of(definition, shapes)
    io.output_fields = [f["name"] for f in fields_of(io.output)]

    path = human_in_closure(definition, lookups)
    if path is not None:
        io.has_human, io.human_path = True, path

    if not io.has_trigger:
        io.callable = False
        io.reasons.append("It has no Execute Workflow Trigger.")
    if io.has_human:
        io.callable = False
        via = f" (in '{path[-1]}')" if path else ""
        io.reasons.append(f"It has an Approval step{via}; approvals must be in the top-level workflow.")
    return io
