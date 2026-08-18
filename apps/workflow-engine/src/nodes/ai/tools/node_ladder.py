"""Node-ladder tools — let an agent compose a workflow one call at a time.

The old shape was a single ``save_workflow`` tool that swallowed a whole JSON
graph: the model thought in private and the graph appeared fully formed. This
module replaces that with a *ladder* of seven small tools that mutate a shared
:class:`WorkflowDraft`, so a human can watch the graph grow and interrupt it.

Why a ladder and not one tool per node type: there are 28 registered node
types and their property schemas are enormous (``AIAgent`` alone has 30
properties). Tool schemas are serialised into the context window on *every*
model call, so 28 tools would cost tens of thousands of tokens per turn. Here
the catalogue is **data the agent looks up** (``list_node_types`` ->
``get_node_schema``) rather than surface area on the tool list. The whole
ladder costs a few hundred tokens.

The seven rungs::

    list_node_types(query?, group?)   compact index - type | group | summary
    get_node_schema(node_type, ...)   real NodeProperty list for ONE type
    add_node(node_type, name, ...)    place a node, get back what it still owes
    connect(from_node, to_node, ...)  wire two nodes, port names checked
    update_node(name, ...)            re-parameterise, rename, or remove=true
    validate()                        structured, actionable errors
    test_run(input?)                  DRY run - compile and count, never write

Every tool follows the workflow-engine agent-tool contract::

    {"name": str, "description": str, "input_schema": dict,
     "execute": async callable(input_data: dict, context) -> Any}

Use :func:`build_node_ladder_tools` to bind the seven tools to a per-run draft.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ....engine.types import ExecutionContext, Workflow

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Registry access (in-process, never over HTTP)
# ---------------------------------------------------------------------------

def _registry() -> Any:
    """Return the singleton node registry, registering built-ins on first use."""
    from ....engine.node_registry import node_registry, register_all_nodes

    if not node_registry.list():
        register_all_nodes()
    return node_registry


def _node_info(node_type: str) -> Any | None:
    return _registry().get_node_type_info(node_type)


def _all_infos() -> list[Any]:
    return _registry().get_node_info_full()


def _trigger_types() -> set[str]:
    """Node types that can start a workflow, derived from the registry."""
    return {i.type for i in _all_infos() if "trigger" in (i.group or [])}


def _compute_io(node_type: str, parameters: dict[str, Any]) -> dict[str, Any]:
    """Ports for a node *as configured* (Switch/Merge ports are parameter-driven)."""
    from ....services.node_service import NodeService

    return NodeService(_registry()).compute_node_io(node_type, parameters or {})


# ---------------------------------------------------------------------------
# The draft
# ---------------------------------------------------------------------------

@dataclass
class DraftNode:
    """One node on the draft canvas."""

    name: str
    type: str
    parameters: dict[str, Any] = field(default_factory=dict)
    position: dict[str, float] | None = None


@dataclass
class DraftConnection:
    """One wire on the draft canvas."""

    source_node: str
    target_node: str
    source_output: str = "main"
    target_input: str = "main"


@dataclass
class WorkflowDraft:
    """Mutable in-progress workflow the ladder builds up.

    Deliberately dumb: it holds state and knows how to render itself as an
    engine :class:`~src.engine.types.Workflow` or as the JSON definition the
    API stores. All the judgement lives in the tools.
    """

    name: str = "Untitled workflow"
    description: str = ""
    nodes: list[DraftNode] = field(default_factory=list)
    connections: list[DraftConnection] = field(default_factory=list)
    workflow_id: str | None = None
    history: list[str] = field(default_factory=list)

    # -- lookups ----------------------------------------------------------
    def find(self, name: str) -> DraftNode | None:
        return next((n for n in self.nodes if n.name == name), None)

    def node_names(self) -> list[str]:
        return [n.name for n in self.nodes]

    def unique_name(self, base: str) -> str:
        """``Fetch`` -> ``Fetch 2`` -> ``Fetch 3`` when names collide."""
        base = (base or "Node").strip() or "Node"
        if not self.find(base):
            return base
        i = 2
        while self.find(f"{base} {i}"):
            i += 1
        return f"{base} {i}"

    # -- mutation ---------------------------------------------------------
    def add(self, node: DraftNode) -> DraftNode:
        if node.position is None:
            node.position = {"x": 240.0 + 260.0 * len(self.nodes), "y": 300.0}
        self.nodes.append(node)
        self.history.append(f"add_node {node.type} '{node.name}'")
        return node

    def remove(self, name: str) -> int:
        """Drop a node and every wire touching it. Returns wires removed."""
        self.nodes = [n for n in self.nodes if n.name != name]
        before = len(self.connections)
        self.connections = [
            c for c in self.connections
            if c.source_node != name and c.target_node != name
        ]
        self.history.append(f"remove_node '{name}'")
        return before - len(self.connections)

    def rename(self, old: str, new: str) -> None:
        node = self.find(old)
        if node is None:
            return
        node.name = new
        for c in self.connections:
            if c.source_node == old:
                c.source_node = new
            if c.target_node == old:
                c.target_node = new
        self.history.append(f"rename '{old}' -> '{new}'")

    def connect(self, conn: DraftConnection) -> bool:
        """Add a wire. Returns False if that exact wire already exists."""
        for c in self.connections:
            if (c.source_node, c.target_node, c.source_output, c.target_input) == (
                conn.source_node, conn.target_node, conn.source_output, conn.target_input
            ):
                return False
        self.connections.append(conn)
        self.history.append(
            f"connect '{conn.source_node}'[{conn.source_output}] -> "
            f"'{conn.target_node}'[{conn.target_input}]"
        )
        return True

    # -- rendering --------------------------------------------------------
    def to_definition(self) -> dict[str, Any]:
        """The JSON definition shape the workflow API stores."""
        return {
            "name": self.name,
            "description": self.description,
            "nodes": [
                {
                    "name": n.name,
                    "type": n.type,
                    "parameters": n.parameters,
                    "position": n.position,
                }
                for n in self.nodes
            ],
            "connections": [
                {
                    "source_node": c.source_node,
                    "target_node": c.target_node,
                    "source_output": c.source_output,
                    "target_input": c.target_input,
                }
                for c in self.connections
            ],
            "settings": {},
        }

    def to_workflow(self) -> Workflow:
        """Compile to the engine's :class:`Workflow` dataclass."""
        from ....engine.types import Connection, NodeDefinition, Workflow

        return Workflow(
            name=self.name,
            id=self.workflow_id,
            description=self.description or None,
            nodes=[
                NodeDefinition(
                    name=n.name,
                    type=n.type,
                    parameters=dict(n.parameters),
                    position=n.position,
                )
                for n in self.nodes
            ],
            connections=[
                Connection(
                    source_node=c.source_node,
                    target_node=c.target_node,
                    source_output=c.source_output,
                    target_input=c.target_input,
                )
                for c in self.connections
            ],
        )

    def graph_lines(self) -> list[str]:
        """One line per wire plus any island nodes - cheap state echo."""
        lines = [
            f"{c.source_node} [{c.source_output}] -> {c.target_node} [{c.target_input}]"
            for c in self.connections
        ]
        wired = {c.source_node for c in self.connections} | {
            c.target_node for c in self.connections
        }
        for n in self.nodes:
            if n.name not in wired:
                lines.append(f"{n.name} ({n.type}) - not wired")
        return lines


# ---------------------------------------------------------------------------
# Property helpers
# ---------------------------------------------------------------------------

_EMPTY = (None, "", [], {})


def _prop_default(props: list[dict[str, Any]], name: str) -> Any:
    for p in props:
        if p.get("name") == name:
            return p.get("default")
    return None


def _is_visible(prop: dict[str, Any], parameters: dict[str, Any],
                props: list[dict[str, Any]]) -> bool:
    """Honour ``displayOptions`` so we never demand a hidden field.

    A required parameter behind ``displayOptions.show`` is only actually
    required when the controlling parameter has one of the listed values.
    """
    disp = prop.get("displayOptions") or {}
    for controller, allowed in (disp.get("show") or {}).items():
        current = parameters.get(controller, _prop_default(props, controller))
        if current not in allowed:
            return False
    for controller, blocked in (disp.get("hide") or {}).items():
        current = parameters.get(controller, _prop_default(props, controller))
        if current in blocked:
            return False
    return True


def _missing_required(info: Any, parameters: dict[str, Any]) -> list[dict[str, Any]]:
    """Required-and-visible properties with no usable value and no usable default."""
    out: list[dict[str, Any]] = []
    props = info.properties or []
    for p in props:
        if not p.get("required"):
            continue
        if not _is_visible(p, parameters, props):
            continue
        value = parameters.get(p["name"])
        if value not in _EMPTY:
            continue
        if p.get("default") not in _EMPTY:
            continue  # default carries it
        out.append({
            "name": p["name"],
            "type": p.get("type"),
            "description": (p.get("description") or "")[:120],
            "options": [o["value"] for o in (p.get("options") or [])][:12],
        })
    return out


def _unknown_parameters(info: Any, parameters: dict[str, Any]) -> list[str]:
    known = {p["name"] for p in (info.properties or [])}
    return sorted(k for k in parameters if k not in known)


def _parse_params(raw: Any) -> tuple[dict[str, Any], str | None]:
    """Accept a dict or a JSON object string. Returns (params, error)."""
    if raw in (None, ""):
        return {}, None
    if isinstance(raw, dict):
        return dict(raw), None
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except ValueError as exc:
            return {}, f"parameters is not valid JSON: {exc}"
        if not isinstance(parsed, dict):
            return {}, "parameters must be a JSON object, e.g. {\"url\": \"https://...\"}"
        return parsed, None
    return {}, f"parameters must be a JSON object string, got {type(raw).__name__}"


def _one_line(text: str, limit: int = 72) -> str:
    line = (text or "").strip().split("\n")[0]
    for sep in (". ", " — ", " - "):
        if sep in line:
            line = line.split(sep)[0]
            break
    return line[:limit].rstrip()


# ---------------------------------------------------------------------------
# Side-effect classification (used by test_run)
# ---------------------------------------------------------------------------

_MONGO_WRITE_OPS = {
    "insertOne", "insertMany", "updateOne", "updateMany",
    "replaceOne", "deleteOne", "deleteMany",
}
_NEO4J_WRITE_KEYWORDS = ("CREATE", "MERGE", "DELETE", "SET ", "REMOVE", "DROP")


def _classify_effect(node: DraftNode) -> dict[str, Any]:
    """What would this node *do* if we actually ran it?

    kind is one of: write, external_call, read, compute, control.
    Only ``write`` and ``external_call`` are things a dry run must refuse.
    """
    p = node.parameters or {}
    t = node.type

    if t == "SendEmail":
        return {"kind": "write", "detail": f"send email to {p.get('toEmail') or '<unset>'}"}

    if t == "HttpRequest":
        method = str(p.get("method") or "GET").upper()
        if method in ("GET", "HEAD"):
            return {"kind": "read", "detail": f"{method} {p.get('url') or '<unset>'}"}
        return {"kind": "write", "detail": f"{method} {p.get('url') or '<unset>'}"}

    if t == "Postgres":
        if str(p.get("operation") or "query") == "transaction":
            stmts = p.get("statements")
            n = len(stmts) if isinstance(stmts, list) else 1
            return {"kind": "write", "detail": f"postgres transaction ({n} statement(s))"}
        query = str(p.get("query") or "").strip()
        verb = query.split()[0].upper() if query else ""
        if verb in ("SELECT", "WITH", "SHOW", "EXPLAIN"):
            return {"kind": "read", "detail": f"postgres {verb or 'query'}"}
        return {"kind": "write", "detail": f"postgres {verb or '<unset query>'}"}

    if t == "MongoDB":
        op = str(p.get("operation") or "find")
        kind = "write" if op in _MONGO_WRITE_OPS else "read"
        return {"kind": kind, "detail": f"mongodb {op} on {p.get('collection') or '<unset>'}"}

    if t == "Neo4j":
        if str(p.get("operation") or "query") == "transaction":
            return {"kind": "write", "detail": "neo4j transaction"}
        query = str(p.get("query") or "").upper()
        if any(k in query for k in _NEO4J_WRITE_KEYWORDS):
            return {"kind": "write", "detail": "neo4j write query"}
        return {"kind": "read", "detail": "neo4j read query"}

    if t == "ExecuteWorkflow":
        return {"kind": "write", "detail": f"run subworkflow {p.get('workflowId') or '<unset>'}"}

    if t in ("AIAgent", "LLMChat"):
        return {"kind": "external_call", "detail": f"LLM call ({p.get('model') or 'default model'})"}

    if t == "Code":
        return {"kind": "compute", "detail": "user Python (sandboxed, not run in dry mode)"}

    if t in _trigger_types():
        return {"kind": "control", "detail": "trigger"}

    if t in ("If", "Switch", "Merge", "Wait", "Loop", "Poll", "StopAndError", "Filter"):
        return {"kind": "control", "detail": t}

    return {"kind": "compute", "detail": t}


# ---------------------------------------------------------------------------
# Upstream field checking
# ---------------------------------------------------------------------------

_FIELD_READERS = {"If": "field", "Filter": "field"}


_PASSTHROUGH = "passthrough"


def _declared_output_fields(node_type: str) -> list[str] | str | None:
    """Top-level field names a node type declares it emits.

    Returns a sorted list when the type declares a concrete object output,
    the string ``"passthrough"`` when it forwards its input unchanged (If,
    Filter) so the caller should keep walking upstream, or ``None`` when the
    output shape is genuinely unknowable (Code, Set) — in which case no
    conclusion may be drawn at all.
    """
    info = _node_info(node_type)
    if info is None:
        return None
    for out in (info.outputs or []):
        schema = out.get("schema") or {}
        if schema.get("passthrough"):
            return _PASSTHROUGH
        props = schema.get("properties")
        if schema.get("type") == "object" and isinstance(props, dict) and props:
            return sorted(props)
    return None


def _upstream_field_warnings(draft: WorkflowDraft) -> list[dict[str, Any]]:
    """Warn when If/Filter reads a field the upstream node does not emit.

    This is the check that catches the classic model mistake of routing on
    ``$json.data`` when HttpRequest actually emits ``statusCode/headers/body``.
    It is a warning, not an error: a Code node upstream can emit anything, and
    those cases resolve to "unknown" and are skipped.
    """
    incoming: dict[str, list[str]] = {}
    for c in draft.connections:
        incoming.setdefault(c.target_node, []).append(c.source_node)

    out: list[dict[str, Any]] = []
    for node in draft.nodes:
        param = _FIELD_READERS.get(node.type)
        if not param:
            continue
        raw = node.parameters.get(param)
        if not isinstance(raw, str) or not raw or "{{" in raw:
            continue
        root = raw.split(".")[0].split("[")[0].strip()
        if not root:
            continue

        # Walk upstream through passthrough/unknown nodes to the nearest node
        # that actually declares what it emits.
        seen: set[str] = set()
        queue = list(incoming.get(node.name, []))
        fields: list[str] | None = None
        source: str | None = None
        unknowable = False
        while queue:
            cur = queue.pop(0)
            if cur in seen:
                continue
            seen.add(cur)
            cur_node = draft.find(cur)
            if cur_node is None:
                continue
            declared = _declared_output_fields(cur_node.type)
            if declared is None:
                unknowable = True  # e.g. a Code node — it can emit anything
                break
            if declared == _PASSTHROUGH:
                queue.extend(incoming.get(cur, []))
                continue
            fields, source = declared, cur
            break
        if not unknowable and fields and root not in fields:
            out.append(_err(
                "field_not_in_upstream_output", node.name,
                f"'{node.name}' ({node.type}) routes on field '{raw}', but its "
                f"upstream node '{source}' emits {fields}.",
                f"Call update_node(name='{node.name}', "
                f"parameters='{{\"{param}\": \"{fields[0]}\"}}') or pick another "
                f"field from {fields}.",
            ))
    return out


# ---------------------------------------------------------------------------
# Validation core (shared by validate() and test_run())
# ---------------------------------------------------------------------------

def _err(code: str, node: str | None, message: str, fix: str) -> dict[str, Any]:
    return {"code": code, "node": node, "message": message, "fix": fix}


def _validate_draft(draft: WorkflowDraft) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (errors, warnings). Every entry names a node and a fix."""
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    if not draft.nodes:
        errors.append(_err(
            "empty_draft", None, "The draft has no nodes.",
            "Call add_node with a trigger type first (e.g. Cron, Webhook, Start).",
        ))
        return errors, warnings

    seen: set[str] = set()
    for n in draft.nodes:
        if n.name in seen:
            errors.append(_err(
                "duplicate_name", n.name, f"Two nodes are both named '{n.name}'.",
                "Call update_node with new_name to rename one of them.",
            ))
        seen.add(n.name)

    reg = _registry()
    triggers = _trigger_types()

    # per-node checks
    for n in draft.nodes:
        if not reg.has(n.type):
            errors.append(_err(
                "unknown_node_type", n.name,
                f"Node '{n.name}' has unknown type '{n.type}'.",
                "Call list_node_types to find the real type name, then remove and re-add.",
            ))
            continue
        info = reg.get_node_type_info(n.type)
        for miss in _missing_required(info, n.parameters):
            hint = f" Allowed values: {miss['options']}." if miss["options"] else ""
            errors.append(_err(
                "missing_required_parameter", n.name,
                f"Node '{n.name}' ({n.type}) is missing required parameter "
                f"'{miss['name']}' ({miss['type']}).{hint}",
                f"Call update_node(name='{n.name}', parameters='{{\"{miss['name']}\": ...}}').",
            ))
        unknown = _unknown_parameters(info, n.parameters)
        if unknown:
            warnings.append(_err(
                "unknown_parameter", n.name,
                f"Node '{n.name}' ({n.type}) has parameters not in its schema: {unknown}.",
                f"Call get_node_schema('{n.type}') and drop or rename them; they are ignored at runtime.",
            ))

    # connection checks
    names = set(draft.node_names())
    for c in draft.connections:
        label = f"{c.source_node} -> {c.target_node}"
        if c.source_node not in names:
            errors.append(_err(
                "dangling_connection", c.source_node,
                f"Connection {label} starts at '{c.source_node}', which does not exist.",
                "Add that node, or remove the connection by removing one of its endpoints.",
            ))
            continue
        if c.target_node not in names:
            errors.append(_err(
                "dangling_connection", c.target_node,
                f"Connection {label} ends at '{c.target_node}', which does not exist.",
                "Add that node, or remove the connection by removing one of its endpoints.",
            ))
            continue
        src = draft.find(c.source_node)
        dst = draft.find(c.target_node)
        if src and reg.has(src.type):
            ports = [o["name"] for o in _compute_io(src.type, src.parameters)["outputs"]]
            if ports and c.source_output not in ports:
                errors.append(_err(
                    "unknown_output_port", c.source_node,
                    f"'{c.source_node}' ({src.type}) has no output port "
                    f"'{c.source_output}'.",
                    f"Valid output ports are {ports}. Re-connect using one of them.",
                ))
        if dst and reg.has(dst.type):
            ports = [i["name"] for i in _compute_io(dst.type, dst.parameters)["inputs"]]
            if ports and c.target_input not in ports:
                errors.append(_err(
                    "unknown_input_port", c.target_node,
                    f"'{c.target_node}' ({dst.type}) has no input port "
                    f"'{c.target_input}'.",
                    f"Valid input ports are {ports}. Re-connect using one of them.",
                ))

    # trigger presence
    trigger_nodes = [n for n in draft.nodes if n.type in triggers]
    if not trigger_nodes:
        errors.append(_err(
            "no_trigger", None, "The workflow has no trigger/start node, so it can never run.",
            f"Add one of: {sorted(triggers)} — e.g. add_node('Start', 'Manual start').",
        ))

    # The publish path (WorkflowService._validate_definition_structure) uses a
    # hardcoded trigger set that is narrower than the registry's "trigger"
    # group — ChatInput registers as a trigger but is not in that set. Warn
    # rather than fail, so the agent is not surprised at publish time.
    _PUBLISHABLE_TRIGGERS = {"Start", "Webhook", "Cron", "ExecuteWorkflowTrigger", "ErrorTrigger"}
    if trigger_nodes and not any(n.type in _PUBLISHABLE_TRIGGERS for n in trigger_nodes):
        only = sorted({n.type for n in trigger_nodes})
        warnings.append(_err(
            "trigger_not_publishable", trigger_nodes[0].name,
            f"The only trigger(s) here are {only}, which the publish-time "
            f"structural check does not count as triggers.",
            "Add a Start/Webhook/Cron/ExecuteWorkflowTrigger/ErrorTrigger node "
            "if this workflow needs to be published and run on a schedule or URL.",
        ))

    # reachability from any trigger
    if trigger_nodes:
        adjacency: dict[str, set[str]] = {}
        for c in draft.connections:
            adjacency.setdefault(c.source_node, set()).add(c.target_node)
        reachable: set[str] = set()
        stack = [t.name for t in trigger_nodes]
        while stack:
            cur = stack.pop()
            if cur in reachable:
                continue
            reachable.add(cur)
            stack.extend(adjacency.get(cur, ()))
        for n in draft.nodes:
            if n.name not in reachable:
                errors.append(_err(
                    "unreachable_node", n.name,
                    f"Node '{n.name}' ({n.type}) is never reached from a trigger.",
                    f"Call connect(from_node=<an upstream node>, to_node='{n.name}').",
                ))

    warnings.extend(_upstream_field_warnings(draft))

    # Reuse the engine's own structural check so the ladder can never disagree
    # with what the save/publish path enforces. It is a pure function of the
    # workflow, so we can call it unbound.
    try:
        from ....services.workflow_service import WorkflowService

        structural = WorkflowService._validate_definition_structure(
            None, draft.to_workflow()
        )
        covered = {e["message"] for e in errors}
        for msg in structural:
            if any(word in msg for word in ("Orphan node", "no trigger", "missing source", "missing target")):
                continue  # already reported above, with a fix attached
            if msg not in covered:
                errors.append(_err("structure", None, msg, "Fix the structure and re-validate."))
    except Exception as exc:  # noqa: BLE001 - never let reuse break the tool
        logger.debug("structural reuse failed: %s", exc)

    return errors, warnings


# ---------------------------------------------------------------------------
# Tool 1 — list_node_types
# ---------------------------------------------------------------------------

def _tool_list_node_types(draft: WorkflowDraft) -> dict[str, Any]:
    async def execute(input_data: dict[str, Any], context: ExecutionContext) -> Any:
        query = str((input_data or {}).get("query") or "").strip().lower()
        group = str((input_data or {}).get("group") or "").strip().lower()

        rows: list[str] = []
        groups: set[str] = set()
        for info in _all_infos():
            gs = info.group or []
            groups.update(gs)
            if group and group not in [g.lower() for g in gs]:
                continue
            haystack = f"{info.type} {info.display_name} {info.description} {' '.join(gs)}".lower()
            if query and query not in haystack:
                continue
            rows.append(f"{info.type} | {','.join(gs) or '-'} | {_one_line(info.description)}")

        rows.sort()
        return {
            "count": len(rows),
            "types": rows,
            "groups": sorted(groups),
            "note": (
                "Format is `type | group | summary`. No schemas here on purpose. "
                "Call get_node_schema(node_type) for the parameters of the one you pick."
            ),
        }

    return {
        "name": "list_node_types",
        "description": (
            "Search the workflow node catalogue. Returns one compact line per node "
            "type (`type | group | summary`) with NO property schemas. Use this "
            "first to find the right node type, then get_node_schema for its "
            "parameters."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Case-insensitive substring filter over type name, group and summary (e.g. 'http', 'schedule', 'database').",
                },
                "group": {
                    "type": "string",
                    "description": "Exact group filter: trigger, flow, transform, ai, or ui.",
                },
            },
            "required": [],
        },
        "execute": execute,
    }


# ---------------------------------------------------------------------------
# Tool 2 — get_node_schema
# ---------------------------------------------------------------------------

_SCHEMA_PROPERTY_BUDGET = 14


def _render_property(p: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "name": p["name"],
        "type": p.get("type"),
        "required": bool(p.get("required")),
    }
    if p.get("default") not in (None, ""):
        out["default"] = p["default"]
    if p.get("description"):
        out["description"] = p["description"][:160]
    opts = [o["value"] for o in (p.get("options") or [])]
    if opts:
        out["options"] = opts[:12]
        if len(opts) > 12:
            out["options_truncated"] = f"+{len(opts) - 12} more"
    if p.get("displayOptions"):
        out["displayOptions"] = p["displayOptions"]
    nested = p.get("properties") or []
    if nested:
        out["sub_properties"] = [n["name"] for n in nested]
    return out


def _tool_get_node_schema(draft: WorkflowDraft) -> dict[str, Any]:
    async def execute(input_data: dict[str, Any], context: ExecutionContext) -> Any:
        node_type = str((input_data or {}).get("node_type") or "").strip()
        prop_filter = str((input_data or {}).get("property_filter") or "").strip().lower()
        show_all = bool((input_data or {}).get("show_all"))

        if not node_type:
            return {"ok": False, "error": "node_type is required.",
                    "fix": "Call list_node_types first and pass an exact type name."}

        info = _node_info(node_type)
        if info is None:
            close = [
                i.type for i in _all_infos()
                if node_type.lower() in i.type.lower() or i.type.lower() in node_type.lower()
            ]
            return {
                "ok": False,
                "error": f"Unknown node type '{node_type}'.",
                "did_you_mean": close[:5],
                "fix": "Call list_node_types(query=...) to get exact type names.",
            }

        props = info.properties or []
        if prop_filter:
            props = [
                p for p in props
                if prop_filter in p["name"].lower()
                or prop_filter in (p.get("description") or "").lower()
            ]

        truncated = False
        total = len(props)
        if not show_all and not prop_filter and total > _SCHEMA_PROPERTY_BUDGET:
            required = [p for p in props if p.get("required")]
            rest = [p for p in props if not p.get("required")]
            props = (required + rest)[:_SCHEMA_PROPERTY_BUDGET]
            truncated = True

        io = _compute_io(info.type, {})
        payload: dict[str, Any] = {
            "ok": True,
            "type": info.type,
            "display_name": info.display_name,
            "group": info.group,
            "description": info.description,
            "inputs": [i["name"] for i in io["inputs"]],
            "outputs": [o["name"] for o in io["outputs"]],
            "property_count": total,
            "properties": [_render_property(p) for p in props],
        }
        if truncated:
            payload["truncated"] = (
                f"TRUNCATED: showing {len(props)} of {total} properties "
                f"(required ones first). Call get_node_schema('{info.type}', "
                f"property_filter='...') to search the rest, or show_all=true for everything."
            )
        payload["note"] = (
            "displayOptions.show means the property only applies when the named "
            "parameter has one of those values; displayOptions.hide is the inverse."
        )
        return payload

    return {
        "name": "get_node_schema",
        "description": (
            "Get the full property schema for ONE node type: names, types, "
            "defaults, allowed option values, and displayOptions (which fields "
            "are conditional). Large nodes are truncated to the required "
            "properties first — use property_filter or show_all to see the rest."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "node_type": {
                    "type": "string",
                    "description": "Exact node type from list_node_types, e.g. 'HttpRequest'.",
                },
                "property_filter": {
                    "type": "string",
                    "description": "Only return properties whose name or description contains this substring.",
                },
                "show_all": {
                    "type": "boolean",
                    "description": "Return every property even for very large nodes such as AIAgent. Expensive.",
                },
            },
            "required": ["node_type"],
        },
        "execute": execute,
    }


# ---------------------------------------------------------------------------
# Tool 3 — add_node
# ---------------------------------------------------------------------------

def _tool_add_node(draft: WorkflowDraft) -> dict[str, Any]:
    async def execute(input_data: dict[str, Any], context: ExecutionContext) -> Any:
        data = input_data or {}
        node_type = str(data.get("node_type") or "").strip()
        requested_name = str(data.get("name") or "").strip()

        if not node_type:
            return {"ok": False, "error": "node_type is required.",
                    "fix": "Call list_node_types to find a type, then add_node(node_type=..., name=...)."}

        info = _node_info(node_type)
        if info is None:
            close = [i.type for i in _all_infos() if node_type.lower() in i.type.lower()]
            return {
                "ok": False,
                "error": f"Unknown node type '{node_type}'. Nothing was added.",
                "did_you_mean": close[:5],
                "fix": "Call list_node_types(query=...) for exact type names.",
            }

        params, perr = _parse_params(data.get("parameters"))
        if perr:
            return {"ok": False, "error": perr,
                    "fix": 'Pass parameters as a JSON object string, e.g. "{\\"url\\": \\"https://x\\"}".'}

        name = draft.unique_name(requested_name or info.display_name or node_type)
        renamed = bool(requested_name) and name != requested_name

        node = draft.add(DraftNode(name=name, type=node_type, parameters=params))
        io = _compute_io(node_type, params)

        result: dict[str, Any] = {
            "ok": True,
            "added": {"name": node.name, "type": node.type, "parameters": node.parameters},
            "outputs": [o["name"] for o in io["outputs"]],
            "inputs": [i["name"] for i in io["inputs"]],
            "still_needs": _missing_required(info, params),
            "node_count": len(draft.nodes),
        }
        if renamed:
            result["renamed"] = (
                f"'{requested_name}' was taken; this node is called '{name}'. "
                f"Use '{name}' in connect/update_node."
            )
        unknown = _unknown_parameters(info, params)
        if unknown:
            result["unknown_parameters"] = (
                f"{unknown} are not in {node_type}'s schema and will be ignored. "
                f"Call get_node_schema('{node_type}')."
            )
        if result["still_needs"]:
            result["next_step"] = (
                f"Set the missing parameters with "
                f"update_node(name='{name}', parameters=...) before validate()."
            )
        return result

    return {
        "name": "add_node",
        "description": (
            "Place one node on the workflow draft. Validates that the type "
            "exists and auto-renames on a name collision. Returns the node's "
            "input/output port names and `still_needs` — the required "
            "parameters that are still unset."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "node_type": {
                    "type": "string",
                    "description": "Exact node type from list_node_types, e.g. 'Cron'.",
                },
                "name": {
                    "type": "string",
                    "description": "Human-readable name for this node instance, e.g. 'Fetch export'. Must be unique; a suffix is added if it collides.",
                },
                "parameters": {
                    "type": "string",
                    "description": "JSON object string of parameters, e.g. '{\"mode\": \"cron\", \"cronExpression\": \"0 7 * * 1\"}'. Optional — you can set them later with update_node.",
                },
            },
            "required": ["node_type", "name"],
        },
        "execute": execute,
    }


# ---------------------------------------------------------------------------
# Tool 4 — connect
# ---------------------------------------------------------------------------

def _tool_connect(draft: WorkflowDraft) -> dict[str, Any]:
    async def execute(input_data: dict[str, Any], context: ExecutionContext) -> Any:
        data = input_data or {}
        from_node = str(data.get("from_node") or "").strip()
        to_node = str(data.get("to_node") or "").strip()
        from_output = str(data.get("from_output") or "main").strip() or "main"
        to_input = str(data.get("to_input") or "main").strip() or "main"

        src = draft.find(from_node)
        dst = draft.find(to_node)
        if src is None:
            return {"ok": False,
                    "error": f"No node named '{from_node}' on the draft. Nothing was connected.",
                    "existing_nodes": draft.node_names(),
                    "fix": "Use one of existing_nodes, or add_node it first."}
        if dst is None:
            return {"ok": False,
                    "error": f"No node named '{to_node}' on the draft. Nothing was connected.",
                    "existing_nodes": draft.node_names(),
                    "fix": "Use one of existing_nodes, or add_node it first."}
        if from_node == to_node:
            return {"ok": False,
                    "error": f"'{from_node}' cannot connect to itself.",
                    "fix": "Use a Loop node if you need iteration."}

        out_ports = [o["name"] for o in _compute_io(src.type, src.parameters)["outputs"]]
        if out_ports and from_output not in out_ports:
            return {
                "ok": False,
                "error": (
                    f"'{from_node}' ({src.type}) has no output port '{from_output}'. "
                    f"Nothing was connected."
                ),
                "valid_output_ports": out_ports,
                "fix": f"Re-call connect with from_output set to one of {out_ports}.",
            }

        in_ports = [i["name"] for i in _compute_io(dst.type, dst.parameters)["inputs"]]
        if in_ports and to_input not in in_ports:
            return {
                "ok": False,
                "error": (
                    f"'{to_node}' ({dst.type}) has no input port '{to_input}'. "
                    f"Nothing was connected."
                ),
                "valid_input_ports": in_ports,
                "fix": f"Re-call connect with to_input set to one of {in_ports}.",
            }

        added = draft.connect(DraftConnection(from_node, to_node, from_output, to_input))
        return {
            "ok": True,
            "connected": f"{from_node} [{from_output}] -> {to_node} [{to_input}]",
            "already_existed": not added,
            "connection_count": len(draft.connections),
            "graph": draft.graph_lines(),
        }

    return {
        "name": "connect",
        "description": (
            "Wire one node's output to another node's input on the draft. Both "
            "endpoints must already exist and the port names must be real for "
            "those node types — a rejection tells you the valid port names. "
            "Branching nodes have named outputs (If: true/false, Loop: "
            "loop/done, Switch: output0..N/fallback)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "from_node": {"type": "string", "description": "Name of the source node (as returned by add_node)."},
                "to_node": {"type": "string", "description": "Name of the target node."},
                "from_output": {
                    "type": "string",
                    "description": "Output port on the source node. Defaults to 'main'. Use 'true'/'false' for If, 'loop'/'done' for Loop.",
                },
                "to_input": {
                    "type": "string",
                    "description": "Input port on the target node. Defaults to 'main'. Merge uses 'input1'/'input2'.",
                },
            },
            "required": ["from_node", "to_node"],
        },
        "execute": execute,
    }


# ---------------------------------------------------------------------------
# Tool 5 — update_node (also removes)
# ---------------------------------------------------------------------------

def _tool_update_node(draft: WorkflowDraft) -> dict[str, Any]:
    async def execute(input_data: dict[str, Any], context: ExecutionContext) -> Any:
        data = input_data or {}
        name = str(data.get("name") or "").strip()
        node = draft.find(name)
        if node is None:
            return {"ok": False,
                    "error": f"No node named '{name}' on the draft.",
                    "existing_nodes": draft.node_names(),
                    "fix": "Use one of existing_nodes (names are case-sensitive)."}

        if bool(data.get("remove")):
            wires = draft.remove(name)
            return {
                "ok": True,
                "removed": name,
                "connections_removed": wires,
                "node_count": len(draft.nodes),
                "graph": draft.graph_lines(),
            }

        params, perr = _parse_params(data.get("parameters"))
        if perr:
            return {"ok": False, "error": perr, "fix": "Pass parameters as a JSON object string."}

        info = _node_info(node.type)
        replace = bool(data.get("replace"))
        if params:
            node.parameters = params if replace else {**node.parameters, **params}
            draft.history.append(f"update_node '{name}' {sorted(params)}")

        new_name = str(data.get("new_name") or "").strip()
        renamed_to = None
        if new_name and new_name != name:
            if draft.find(new_name):
                return {"ok": False,
                        "error": f"Cannot rename to '{new_name}': that name is taken.",
                        "fix": "Pick a different new_name."}
            draft.rename(name, new_name)
            renamed_to = new_name

        final_name = renamed_to or name
        result: dict[str, Any] = {
            "ok": True,
            "node": {"name": final_name, "type": node.type, "parameters": node.parameters},
            "still_needs": _missing_required(info, node.parameters) if info else [],
        }
        if renamed_to:
            result["renamed_to"] = renamed_to
        if info:
            unknown = _unknown_parameters(info, node.parameters)
            if unknown:
                result["unknown_parameters"] = (
                    f"{unknown} are not in {node.type}'s schema and will be ignored."
                )
        return result

    return {
        "name": "update_node",
        "description": (
            "Change a node already on the draft: set parameters (merged into "
            "the existing ones unless replace=true), rename it with new_name, "
            "or delete it with remove=true (which also drops every wire "
            "touching it). Returns what the node still needs."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Current name of the node to change."},
                "parameters": {
                    "type": "string",
                    "description": "JSON object string of parameters to set, e.g. '{\"url\": \"https://example.com\"}'.",
                },
                "replace": {
                    "type": "boolean",
                    "description": "If true, parameters replace the node's whole parameter set instead of merging. Default false.",
                },
                "new_name": {"type": "string", "description": "Rename the node to this; connections follow the rename."},
                "remove": {
                    "type": "boolean",
                    "description": "If true, delete this node and all its connections. Everything else is ignored.",
                },
            },
            "required": ["name"],
        },
        "execute": execute,
    }


# ---------------------------------------------------------------------------
# Tool 6 — validate
# ---------------------------------------------------------------------------

def _tool_validate(draft: WorkflowDraft) -> dict[str, Any]:
    async def execute(input_data: dict[str, Any], context: ExecutionContext) -> Any:
        errors, warnings = _validate_draft(draft)
        return {
            "valid": not errors,
            "error_count": len(errors),
            "errors": errors,
            "warnings": warnings,
            "node_count": len(draft.nodes),
            "connection_count": len(draft.connections),
            "graph": draft.graph_lines(),
            "next_step": (
                "Fix each error with update_node/connect/add_node, then call validate again."
                if errors else "The draft is structurally valid. Call test_run to see what it would do."
            ),
        }

    return {
        "name": "validate",
        "description": (
            "Check the whole draft and return structured, actionable errors: "
            "unknown node type, missing required parameter, unknown port, "
            "dangling connection, unreachable node, duplicate name, no trigger. "
            "Every error names the node and the exact fix. Call this after each "
            "few edits — it is the feedback loop."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
        "execute": execute,
    }


# ---------------------------------------------------------------------------
# Tool 7 — test_run (dry)
# ---------------------------------------------------------------------------

def _dry_resolve(
    parameters: dict[str, Any], sample: dict[str, Any],
) -> tuple[dict[str, Any], list[str], dict[str, str]]:
    """Resolve ``{{ }}`` expressions against a sample item.

    Returns ``(resolved, issues, shown)`` where ``shown`` maps the path of each
    parameter that *contained* an expression to what it resolved to — that is
    the feedback the agent needs to tell a working expression from a silently
    empty one. ``$env`` and ``$vars`` are deliberately empty here, so nothing
    secret can appear in ``shown``.
    """
    from ....engine.expression_engine import ExpressionContext, ExpressionEngine

    engine = ExpressionEngine()
    ctx = ExpressionContext(
        json_data=sample,
        input_data=[],
        node_data={},
        env={},
        execution={"id": "dry-run", "mode": "manual"},
        item_index=0,
        vars={},
    )
    try:
        resolved = engine.resolve(parameters, ctx)
    except Exception as exc:  # noqa: BLE001
        return dict(parameters), [f"expression resolution raised {type(exc).__name__}: {exc}"], {}

    issues: list[str] = []
    shown: dict[str, str] = {}

    def scan(raw: Any, value: Any, path: str) -> None:
        if isinstance(value, str) and "[Expression Error:" in value:
            issues.append(f"{path}: {value}")
        if isinstance(raw, str) and "{{" in raw:
            shown[path] = str(value)[:160]
        if isinstance(value, dict):
            for k, v in value.items():
                scan(raw.get(k) if isinstance(raw, dict) else None, v, f"{path}.{k}")
        elif isinstance(value, list):
            for i, v in enumerate(value):
                scan(raw[i] if isinstance(raw, list) and i < len(raw) else None, v, f"{path}[{i}]")

    scan(parameters, resolved, "parameters")
    return resolved, issues, shown


def _tool_test_run(draft: WorkflowDraft) -> dict[str, Any]:
    async def execute(input_data: dict[str, Any], context: ExecutionContext) -> Any:
        data = input_data or {}
        sample, perr = _parse_params(data.get("input"))
        if perr:
            return {"ok": False, "dry_run": True, "error": perr.replace("parameters", "input"),
                    "fix": 'Pass input as a JSON object string, e.g. "{\\"rows\\": []}".'}

        errors, warnings = _validate_draft(draft)
        if errors:
            return {
                "ok": False,
                "dry_run": True,
                "executed": False,
                "error": "The draft does not validate, so nothing was compiled or run.",
                "errors": errors,
                "fix": "Fix the errors (see validate) and call test_run again.",
            }

        workflow = draft.to_workflow()

        # Reuse the runner's own start-node priority so the dry run picks the
        # same entry point a real run would.
        start = None
        try:
            from ....engine.workflow_runner import WorkflowRunner

            start = WorkflowRunner.find_start_node(None, workflow)
        except Exception:  # noqa: BLE001
            triggers = _trigger_types()
            start = next((n for n in workflow.nodes if n.type in triggers), None) or (
                workflow.nodes[0] if workflow.nodes else None
            )
        if start is None:
            return {"ok": False, "dry_run": True, "executed": False,
                    "error": "No start node found.", "fix": "Add a trigger node."}

        adjacency: dict[str, list[DraftConnection]] = {}
        for c in draft.connections:
            adjacency.setdefault(c.source_node, []).append(c)

        plan: list[dict[str, Any]] = []
        would_write: list[dict[str, Any]] = []
        would_call: list[dict[str, Any]] = []
        expression_issues: list[dict[str, Any]] = []
        counts = {"read": 0, "write": 0, "external_call": 0, "compute": 0, "control": 0}

        seen: set[str] = set()
        queue = [(start.name, "start", 0)]
        while queue:
            name, via, depth = queue.pop(0)
            if name in seen:
                continue
            seen.add(name)
            node = draft.find(name)
            if node is None:
                continue

            resolved, issues, shown = _dry_resolve(node.parameters, sample)
            if issues:
                expression_issues.append({"node": name, "issues": issues})

            effect = _classify_effect(DraftNode(name, node.type, resolved))
            counts[effect["kind"]] = counts.get(effect["kind"], 0) + 1
            step = {
                "step": len(plan) + 1,
                "node": name,
                "type": node.type,
                "reached_via": via,
                "effect": effect["kind"],
                "would_do": effect["detail"],
                "executed": False,
            }
            if shown:
                step["expressions_resolved"] = shown
            plan.append(step)
            if effect["kind"] == "write":
                would_write.append({"node": name, "type": node.type, "would_do": effect["detail"]})
            elif effect["kind"] == "external_call":
                would_call.append({"node": name, "type": node.type, "would_do": effect["detail"]})

            for c in adjacency.get(name, []):
                queue.append((c.target_node, f"{name}[{c.source_output}]", depth + 1))

        return {
            "ok": True,
            "dry_run": True,
            "executed": False,
            "notice": (
                "DRY RUN. The graph was compiled and every parameter expression was "
                "resolved against the sample input, but NO node was executed: no HTTP "
                "call, no SQL, no email, no LLM spend. Counts below are what a real "
                "run would attempt."
            ),
            "start_node": start.name,
            "nodes_planned": len(plan),
            "counts": counts,
            "plan": plan,
            "would_write": would_write,
            "would_call_external": would_call,
            "expression_issues": expression_issues,
            "warnings": warnings,
            "branches_note": (
                "Every branch of If/Switch/Loop is walked, because a dry run cannot "
                "know which way real data would go. A real run takes one path."
            ),
            "real_run_note": (
                "There is no dry=false on this tool by design: this draft is not "
                "saved, has no credentials bound and no execution row, and the "
                "engine's runner would really hit Postgres/SMTP/HTTP. Save the "
                "workflow and run it through the normal execution API when you "
                "actually want it to happen."
            ),
        }

    return {
        "name": "test_run",
        "description": (
            "DRY RUN the draft: compile the graph, walk it from the trigger, "
            "resolve every {{ }} expression against optional sample input, and "
            "report what each node WOULD do — including a count of writes "
            "(Postgres/MongoDB/SendEmail/non-GET HTTP) that were deliberately "
            "not performed. Nothing is executed. Validates first and refuses if "
            "the draft is broken."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "input": {
                    "type": "string",
                    "description": "Optional JSON object string used as the sample item for expression resolution, e.g. '{\"rows\": 3}'.",
                },
            },
            "required": [],
        },
        "execute": execute,
    }


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

_BUILDERS = (
    _tool_list_node_types,
    _tool_get_node_schema,
    _tool_add_node,
    _tool_connect,
    _tool_update_node,
    _tool_validate,
    _tool_test_run,
)


def build_node_ladder_tools(draft: WorkflowDraft) -> list[dict[str, Any]]:
    """Build the seven ladder tools bound to ``draft``.

    Each returned dict is ``{name, description, input_schema, execute}`` where
    ``execute`` is ``async (input_data: dict, context: ExecutionContext) -> Any``
    — the contract the AIAgent node's ``_tools`` seam expects.
    """
    return [builder(draft) for builder in _BUILDERS]


__all__ = [
    "DraftConnection",
    "DraftNode",
    "WorkflowDraft",
    "build_node_ladder_tools",
]
