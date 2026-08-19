"""
Feasibility-validation SDK — a small, hand-written slice of the "agent writes Python
against the node registry" design (see /AGENT-WORKFLOW-AUTHORING.md and /SDK-DESIGN.md
at the repo root).

WHAT THIS IS: 8 of the 28 node types, wired to REAL node_registry data so validation
can never hand-drift from the actual node schemas the way the earlier design mockups
did (they invented `Cron(expression=..., timezone=...)` and `Code(language=...)` —
neither parameter exists; see CronNode/CodeNode in src/nodes/). One generic validator
(`_construct`) reads `node_registry` directly, so extending this to all 28 types is
copying an 8-line pattern, not re-deriving validation logic per node.

WHAT THIS IS NOT: the production generator described in SDK-DESIGN.md §3 (no .pyi
stubs, no equality test, not derived from a build step). This is the minimum real
thing needed to find out whether the *idea* works before investing in that.

Output is the engine's REAL `Workflow`/`NodeDefinition`/`Connection` dataclasses
(src/engine/types.py) — not a POC-only shape. A script built with this SDK produces
the exact object the execution engine already consumes.
"""

from __future__ import annotations

import ast
import difflib
import re
from dataclasses import dataclass, field
from typing import Any

from .node_registry import node_registry, register_all_nodes
from .types import Connection, NodeDefinition, Workflow

# All 28 registered node types, minus AIAgent (excluded per SDK-DESIGN.md §5 —
# an agent placing agents inside the workflow it's building makes cost and
# recursion unpredictable; bind it by hand if wanted). Extending coverage from
# the original 8-type slice to all 27 cost exactly this list — the validator
# itself (_construct, below) already reads the registry generically, so there
# was no per-type logic to write. This is the concrete proof of SDK-DESIGN.md's
# central claim: a node added to the registry needs nothing hand-written here.
SDK_TYPES = [
    # Triggers
    "Start", "Webhook", "Cron", "ErrorTrigger", "ExecuteWorkflowTrigger",
    # Flow control
    "If", "Switch", "Merge", "Wait", "Loop", "Poll", "ExecuteWorkflow", "StopAndError",
    # Data / transform
    "Set", "HttpRequest", "Code", "Filter", "ItemLists", "Sample", "Profile", "Aggregate",
    # Integrations
    "SendEmail", "Postgres", "Neo4j", "MongoDB",
    # AI (AIAgent deliberately excluded)
    "LLMChat",
    # UI
    "ChatInput",
]

EXCLUDED_TYPES = {"AIAgent"}

_registered = False


def _ensure_registered() -> None:
    global _registered
    if not _registered:
        register_all_nodes()
        _registered = True


def _is_effectively_required(prop: Any) -> bool:
    """Mapping rule from SDK-DESIGN.md §5: required=True with a non-empty default
    is optional in the SDK — several nodes declare `required` but ship a default."""
    if not prop.required:
        return False
    return prop.default in (None, "", [], {})


class WorkflowInvalid(Exception):
    """Raised by validate() — carries every problem found, not just the first."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__("; ".join(problems))


@dataclass
class _Draft:
    """The implicit workflow-under-construction. One instance per script execution,
    created fresh in execute_workflow_script() and closed over by every constructor
    — NOT a module-level global, so concurrent script executions cannot corrupt each
    other (verified against the real run_script sandbox: ai_agent.py's safe_globals
    dict is already built fresh per call, which is what makes this safe)."""

    nodes: list[NodeDefinition] = field(default_factory=list)
    connections: list[Connection] = field(default_factory=list)
    name_counts: dict[str, int] = field(default_factory=dict)
    node_meta: dict[str, dict[str, Any]] = field(default_factory=dict)  # name -> {group, sourceLine}


class NodeHandle:
    """A node placed in the draft. `a >> b` wires main->main; `a.true >> b` wires a
    named port. Both forms return the right-hand side so chains read left to right."""

    _INTERNAL_ATTRS = frozenset({"_draft", "definition", "_output_names"})

    def __init__(self, draft: _Draft, definition: NodeDefinition, output_names: list[str]):
        self._draft = draft
        self.definition = definition
        self._output_names = output_names

    @property
    def name(self) -> str:
        return self.definition.name

    def __repr__(self) -> str:
        return f"<{self.definition.type} '{self.definition.name}'>"

    def __setattr__(self, key: str, value: Any) -> None:
        # Found on self-review: a real eval trial wrote `node.someTypo = []`,
        # presumably meaning to declare a plain local variable, and Python let it
        # through as a silent, meaningless instance attribute — no error, no
        # effect, easy to miss. Blocking it turns that mistake into a clear
        # AttributeError instead of a script that "worked" but did nothing.
        if key not in self._INTERNAL_ATTRS:
            raise AttributeError(
                f"can't set '{key}' on a {self.definition.type} node handle — "
                f"node handles aren't a place to stash arbitrary data; use a "
                f"plain Python variable instead."
            )
        object.__setattr__(self, key, value)

    def __rshift__(self, other: "NodeHandle | list[NodeHandle]") -> "NodeHandle | list[NodeHandle]":
        return _wire(self._draft, self, "main", other)

    def __getattr__(self, port: str) -> "_Port":
        # only reached for names not already found as real attributes/methods
        if port in self._output_names:
            return _Port(self._draft, self, port)
        close = difflib.get_close_matches(port, self._output_names, n=1)
        hint = f" did you mean '{close[0]}'?" if close else ""
        raise AttributeError(
            f"{self.definition.type} has no output port '{port}'; "
            f"declared outputs: {self._output_names}.{hint}"
        )


class _Port:
    """A named output port, e.g. `check.true`."""

    def __init__(self, draft: _Draft, node: NodeHandle, port: str):
        self._draft = draft
        self._node = node
        self.port = port

    def __rshift__(self, other: "NodeHandle | list[NodeHandle]") -> "NodeHandle | list[NodeHandle]":
        return _wire(self._draft, self._node, self.port, other)


def _wire(draft: _Draft, source: NodeHandle, source_port: str, other: Any) -> Any:
    targets = other if isinstance(other, list) else [other]
    for t in targets:
        if not isinstance(t, NodeHandle):
            raise TypeError(f"wiring target must be a node, got {type(t).__name__}")
        draft.connections.append(Connection(
            source_node=source.name, target_node=t.name,
            source_output=source_port, target_input="main",
        ))
    return other


def _unique_name(draft: _Draft, type_name: str, requested: str | None, source_line: int | None) -> str:
    """Found on self-review (confirmed by actually running it): the previous
    version tracked a counter per base string but never checked the generated
    name against names actually in use. `a = Postgres(); b = Postgres(name="X 2");
    c = Postgres()` could give `c` the same name as `b` — draft.nodes keyed by
    name, so a connection into `b` silently resolves to `c` instead. validate()
    reported nothing wrong because it never re-derives names, only reads them."""
    used = {n.name for n in draft.nodes}

    if requested is not None:
        if requested in used:
            raise TypeError(
                f"name='{requested}' is already used by another node "
                f"at line {source_line}"
            )
        return requested

    base = type_name
    if base not in used:
        draft.name_counts[base] = 1
        return base
    n = draft.name_counts.get(base, 1)
    candidate = f"{base} {n + 1}"
    while candidate in used:
        n += 1
        candidate = f"{base} {n + 1}"
    draft.name_counts[base] = n + 1
    return candidate


def _construct(
    draft: _Draft, type_name: str, source_line: int | None, kwargs: dict[str, Any],
) -> NodeHandle:
    """The generic constructor every SDK_TYPES factory calls. Reads node_registry
    LIVE — this is the piece that makes drift structurally impossible: there is no
    hand-copied property list to go stale."""
    _ensure_registered()
    if not node_registry.has(type_name):
        raise ValueError(f"Unknown node type '{type_name}' — not in node_registry")
    instance = node_registry.get(type_name)
    desc = instance.node_description
    props_by_name = {p.name: p for p in (desc.properties if desc else [])}

    name = kwargs.pop("name", None)

    unknown = set(kwargs) - set(props_by_name)
    if unknown:
        bad = sorted(unknown)[0]
        close = difflib.get_close_matches(bad, list(props_by_name), n=1)
        hint = f" did you mean '{close[0]}'?" if close else ""
        required_list = [p.name for p in props_by_name.values() if _is_effectively_required(p)]
        raise TypeError(
            f"{type_name} has no parameter '{bad}';{hint}\n"
            f"           required: {', '.join(required_list) if required_list else '(none)'}\n"
            f"           at line {source_line}"
        )

    missing = [
        p.name for p in props_by_name.values()
        if _is_effectively_required(p) and p.name not in kwargs
    ]
    if missing:
        raise TypeError(
            f"{type_name} is missing required parameter(s): {', '.join(missing)}\n"
            f"           at line {source_line}"
        )

    # options-type params must be a declared value (or an expression — those pass through)
    for key, value in kwargs.items():
        prop = props_by_name[key]
        if prop.type == "options" and prop.options and isinstance(value, str):
            valid = {o.value for o in prop.options}
            if value not in valid and not _looks_like_expression(value):
                raise TypeError(
                    f"{type_name}.{key}='{value}' is not a valid option; "
                    f"valid values: {sorted(valid)}\n"
                    f"           at line {source_line}"
                )

    node_name = _unique_name(draft, type_name, name, source_line)
    node_def = NodeDefinition(name=node_name, type=type_name, parameters=dict(kwargs))
    draft.nodes.append(node_def)

    out_names = [o.name for o in desc.outputs] if desc and isinstance(desc.outputs, list) else ["main"]
    group = (desc.group[0] if desc and desc.group else "action")
    draft.node_meta[node_name] = {"group": group, "sourceLine": source_line, "type": type_name}

    return NodeHandle(draft, node_def, out_names)


def _looks_like_expression(value: str) -> bool:
    return bool(re.search(r"\{\{.*\}\}", value)) or value.startswith("$")


def build_sdk_namespace(draft: _Draft, current_line_getter) -> dict[str, Any]:
    """Build the callables to inject into a script's exec() globals, all closed over
    the same `draft` instance. `current_line_getter` lets constructors tag each node
    with the source line that created it (the provenance mechanism proven in the
    workflow-studio UI spike — see agent-sdk-demo.tsx)."""

    def _factory(type_name: str):
        def factory(**kwargs: Any) -> NodeHandle:
            return _construct(draft, type_name, current_line_getter(), kwargs)
        factory.__name__ = type_name
        return factory

    ns: dict[str, Any] = {t: _factory(t) for t in SDK_TYPES}

    def Node(type_name: str, **kwargs: Any) -> NodeHandle:
        """Escape hatch for any node the fixed SDK_TYPES constructors can't
        express — e.g. AIAgent (deliberately excluded above) or a type added to
        the registry that hasn't been added to SDK_TYPES yet. AGENT-WORKFLOW-
        AUTHORING.md §2.3 documents this; found on self-review that it was
        documented but never actually implemented."""
        return _construct(draft, type_name, current_line_getter(), kwargs)

    ns["Node"] = Node

    def list_nodes(group: str | None = None, query: str | None = None) -> list[str]:
        _ensure_registered()
        infos = node_registry.get_node_info_full()
        results = [i.type for i in infos if (group is None or (i.group and group in i.group))]
        if query:
            q = query.lower()
            results = [t for t in results if q in t.lower()]
        return results

    def describe(type_name: str) -> dict[str, Any]:
        _ensure_registered()
        info = node_registry.get_node_type_info(type_name)
        if info is None:
            raise ValueError(f"Unknown node type '{type_name}'")
        return {
            "type": info.type,
            "properties": [
                {"name": p["name"], "type": p["type"], "required": p.get("required", False),
                 "default": p.get("default")}
                for p in info.properties
            ],
            "outputs": [o["name"] for o in (info.outputs or [])],
        }

    def validate() -> None:
        problems = _validate_draft(draft)
        if problems:
            raise WorkflowInvalid(problems)

    def test_run(input: dict[str, Any] | None = None) -> dict[str, Any]:
        return _test_run_draft(draft, input or {})

    def call_tool(name: str, args: dict[str, Any] | None = None) -> Any:
        raise NotImplementedError(
            f"call_tool('{name}') — no connectors registered in this POC slice; "
            f"mid-build data access is not exercised by this eval."
        )

    ns.update({
        "list_nodes": list_nodes,
        "describe": describe,
        "validate": validate,
        "test_run": test_run,
        "call_tool": call_tool,
        "WorkflowInvalid": WorkflowInvalid,
    })
    return ns


_TRIGGER_TYPES = {"Cron", "Start", "Webhook", "ErrorTrigger", "ExecuteWorkflowTrigger", "ChatInput"}


def _validate_draft(draft: _Draft) -> list[str]:
    problems: list[str] = []
    by_name = {n.name: n for n in draft.nodes}

    triggers = [n for n in draft.nodes if n.type in _TRIGGER_TYPES]
    if len(triggers) == 0:
        problems.append("no trigger node — every workflow needs exactly one")
    elif len(triggers) > 1:
        problems.append(f"{len(triggers)} trigger nodes found, expected exactly 1")

    # dangling connections + bad port names against the node's declared outputs
    _ensure_registered()
    for c in draft.connections:
        if c.source_node not in by_name:
            problems.append(f"connection from unknown node '{c.source_node}'")
            continue
        if c.target_node not in by_name:
            problems.append(f"connection to unknown node '{c.target_node}'")
            continue
        src_type = by_name[c.source_node].type
        info = node_registry.get_node_type_info(src_type)
        valid_outputs = {o["name"] for o in (info.outputs or [])} if info else {"main"}
        if c.source_output not in valid_outputs:
            problems.append(
                f"{c.source_node} ({src_type}) has no output '{c.source_output}'; "
                f"valid: {sorted(valid_outputs)}"
            )

    adj = _adjacency(draft)

    # reachability from triggers
    if triggers:
        reachable = {t.name for t in triggers}
        frontier = list(reachable)
        while frontier:
            cur = frontier.pop()
            for nxt in adj.get(cur, []):
                if nxt not in reachable:
                    reachable.add(nxt)
                    frontier.append(nxt)
        unreachable = [n.name for n in draft.nodes if n.name not in reachable]
        if unreachable:
            problems.append(f"unreachable from trigger: {unreachable}")

    # cycles — almost always a mistake, EXCEPT a cycle that passes through a Loop
    # node's own back-edge (loop -> ... -> back into Loop), which is the pattern
    # Loop exists for. Anything else forming a cycle can't be a DAG the engine
    # can execute top-to-bottom.
    cycle = _find_cycle(adj)
    if cycle and not any(by_name[n].type == "Loop" for n in cycle if n in by_name):
        problems.append(f"cycle detected (not via a Loop node): {' -> '.join(cycle)}")

    return problems


def _adjacency(draft: _Draft) -> dict[str, list[str]]:
    adj: dict[str, list[str]] = {}
    for c in draft.connections:
        adj.setdefault(c.source_node, []).append(c.target_node)
    return adj


def _find_cycle(adj: dict[str, list[str]]) -> list[str] | None:
    """Iterative DFS cycle detection (no recursion-depth risk). Returns the cycle
    as a list of node names if one exists, else None."""
    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = {}
    all_nodes = set(adj) | {n for targets in adj.values() for n in targets}
    for start in all_nodes:
        if color.get(start, WHITE) != WHITE:
            continue
        stack = [(start, iter(adj.get(start, [])))]
        color[start] = GRAY
        path = [start]
        while stack:
            node, it = stack[-1]
            advanced = False
            for nxt in it:
                if color.get(nxt, WHITE) == WHITE:
                    color[nxt] = GRAY
                    path.append(nxt)
                    stack.append((nxt, iter(adj.get(nxt, []))))
                    advanced = True
                    break
                if color.get(nxt, WHITE) == GRAY:
                    return path[path.index(nxt):] + [nxt]
            if not advanced:
                color[node] = BLACK
                stack.pop()
                path.pop()
    return None


def _resolve_field(input_data: dict[str, Any], field_path: str) -> Any:
    cur: Any = input_data
    for part in field_path.split("."):
        if not part:
            continue
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


_IMPLEMENTED_IF_OPS = {"isNotEmpty", "isEmpty", "equals", "notEquals", "isTrue", "isFalse"}


def _eval_if(node: NodeDefinition, input_data: dict[str, Any]) -> tuple[bool | None, str | None]:
    """Real (not faked) evaluation of the common If operations, so test_run() branch
    decisions are honest. Returns ``(decision, None)`` when the operation is
    implemented, or ``(None, reason)`` when this slice cannot honestly decide the
    branch — the caller then walks BOTH branches at "possible" certainty rather
    than guessing. An earlier version silently defaulted unimplemented operators
    to True (confidently wrong); the version after that raised (honest but
    unusable — an agent whose dry-run errors on common graphs stops calling it).
    A labelled upper bound keeps the property that actually matters: test_run()
    never UNDERSTATES what would happen."""
    if node.parameters.get("condition"):
        return None, (
            f"{node.name}: `condition` expressions are not evaluated "
            f"(expression-language evaluation is out of scope for this slice) — "
            f"both branches counted as possibly reached."
        )
    field_path = node.parameters.get("field", "")
    op = node.parameters.get("operation", "isTrue")
    if op not in _IMPLEMENTED_IF_OPS:
        return None, (
            f"{node.name}: If.operation='{op}' is not implemented "
            f"(implemented: {sorted(_IMPLEMENTED_IF_OPS)}) — "
            f"both branches counted as possibly reached."
        )
    value = _resolve_field(input_data, field_path) if field_path else input_data
    if op == "isNotEmpty":
        return value not in (None, "", [], {}), None
    if op == "isEmpty":
        return value in (None, "", [], {}), None
    if op == "equals":
        return value == node.parameters.get("value"), None
    if op == "notEquals":
        return value != node.parameters.get("value"), None
    if op == "isFalse":
        return not bool(value), None
    return bool(value), None  # isTrue


def _test_run_draft(draft: _Draft, input_data: dict[str, Any]) -> dict[str, Any]:
    """Dry-run reachability with two levels of certainty.

    A node is DEFINITE when every branch decision on some path to it was
    actually evaluated, and POSSIBLE when it sits behind a branch this slice
    cannot decide (a Switch, a `condition` expression, an unimplemented If
    operation). The invariant that matters is directional: `writes_staged` is
    an UPPER BOUND (definite + possible) and so never understates what could
    happen, while `writes_staged_definite` never overstates what must. Each
    undecidable branch is named in `limitations` instead of raised — an earlier
    version raised here, which was honest but trained callers to stop calling
    test_run() on exactly the graphs that most need a dry run."""
    problems = _validate_draft(draft)
    if problems:
        return {"ok": False, "validate_problems": problems, "reached": [], "writes_staged": 0}

    by_name = {n.name: n for n in draft.nodes}
    triggers = [n for n in draft.nodes if n.type in _TRIGGER_TYPES]
    adj: dict[str, list[Connection]] = {}
    for c in draft.connections:
        adj.setdefault(c.source_node, []).append(c)

    DEFINITE, POSSIBLE = 2, 1
    certainty: dict[str, int] = {t.name: DEFINITE for t in triggers}
    processed_at: dict[str, int] = {}  # node -> certainty it was last expanded at
    logged: set[str] = set()           # branch/limitation entries are per-node, not per-visit
    order: list[str] = []              # first-visit order, for stable output
    branch_log: list[dict[str, Any]] = []
    limitations: list[str] = []
    frontier = [t.name for t in triggers]
    while frontier:
        cur_name = frontier.pop(0)
        cur_cert = certainty[cur_name]
        # Re-expand only on an upgrade (possible -> definite); a cycle of
        # same-certainty nodes therefore terminates after one pass each.
        if processed_at.get(cur_name, 0) >= cur_cert:
            continue
        processed_at[cur_name] = cur_cert
        if cur_name not in order:
            order.append(cur_name)
        cur = by_name[cur_name]
        outs = adj.get(cur_name, [])
        child_cert = cur_cert
        if cur.type == "If":
            decision, limitation = _eval_if(cur, input_data)
            if limitation is not None:
                if cur_name not in logged:
                    limitations.append(limitation)
                    branch_log.append({"node": cur_name, "field": cur.parameters.get("field"),
                                       "branch": "unknown", "reason": limitation})
                child_cert = POSSIBLE
            else:
                branch = "true" if decision else "false"
                if cur_name not in logged:
                    branch_log.append({"node": cur_name, "field": cur.parameters.get("field"),
                                       "branch": branch})
                outs = [c for c in outs if c.source_output == branch]
        elif cur.type == "Switch":
            # Found on self-review: this used to fall through and treat every
            # outgoing branch as (definitely) reached, overcounting writes
            # behind branches that would never actually fire. Switch's real
            # rules/expression evaluation isn't implemented here, so its
            # branches are all possible and none definite.
            limitation = (
                f"{cur_name}: Switch branch selection (mode={cur.parameters.get('mode')!r}) "
                f"is not evaluated — every downstream branch counted as possibly reached."
            )
            if cur_name not in logged:
                limitations.append(limitation)
                branch_log.append({"node": cur_name, "branch": "unknown", "reason": limitation})
            child_cert = POSSIBLE
        logged.add(cur_name)
        for c in outs:
            if certainty.get(c.target_node, 0) < child_cert:
                certainty[c.target_node] = child_cert
                frontier.append(c.target_node)

    reached = [n for n in order if certainty.get(n) == DEFINITE]
    maybe_reached = [n for n in order if certainty.get(n) == POSSIBLE]
    touched = set(certainty)

    # Neo4j runs arbitrary Cypher, read or write, same as Postgres — omitting it
    # (found on self-review) undercounted writes_staged for any workflow that
    # writes via Neo4j instead of Postgres/MongoDB.
    write_types = {"Postgres", "SendEmail", "MongoDB", "Neo4j"}
    definite_set = set(reached)
    writes_definite = sum(1 for n in draft.nodes
                          if n.name in definite_set and n.type in write_types)
    writes_upper = sum(1 for n in draft.nodes
                       if n.name in touched and n.type in write_types)

    return {
        "ok": True,
        "reached": reached,
        "maybe_reached": maybe_reached,
        "unreached": [n.name for n in draft.nodes if n.name not in touched],
        "branches": branch_log,
        "limitations": limitations,
        "writes_staged": writes_upper,
        "writes_staged_definite": writes_definite,
        "writes_executed": 0,
    }


def signature_reference(types: list[str] | None = None) -> str:
    """The compact per-type doc string injected into an eval prompt — the thing
    Tier-1/2 evals are actually testing the clarity of."""
    _ensure_registered()
    types = types or SDK_TYPES
    lines = []
    for t in types:
        info = node_registry.get_node_type_info(t)
        if info is None:
            continue
        parts = []
        for p in info.properties:
            req = "required=True with a non-empty default (SDK treats as optional)" if (p.get("required") and p.get("default") not in (None, "", [], {})) else None
            marker = "" if not p.get("required") or req else "*"
            opts = f" one of {[o['value'] for o in p['options']]}" if p.get("options") else ""
            parts.append(f"{p['name']}{marker}{opts}")
        outs = [o["name"] for o in (info.outputs or [])]
        port_note = "" if outs in (["main"], []) else f"  -> ports: {outs}"
        lines.append(f"{t}({', '.join(parts)}){port_note}")
    lines.append("(* = required)")
    lines.append("Every name above is ALREADY in scope — do not import anything. There is no")
    lines.append("module to import from; writing e.g. `from workflow_sdk import *` will fail.")
    lines.append("a >> b            wire main output -> main input, returns b")
    lines.append("a.PORTNAME >> b   wire a named output port (see -> ports above)")
    lines.append("a.PORTNAME >> [b, c, d]   fan OUT: one port to several independent nodes,")
    lines.append("                  each still fed from a's own output — NOT the same as")
    lines.append("                  b >> c >> d, which would feed c with b's OUTPUT instead")
    lines.append("                  of a's, and is almost never what you want for parallel work.")
    lines.append("validate()        raises WorkflowInvalid listing every problem")
    lines.append("test_run(input={...})   dry run: never writes; returns reached/maybe_reached nodes,")
    lines.append("                  branch decisions, and writes_staged as an UPPER BOUND — any branch")
    lines.append("                  it cannot decide (Switch, condition exprs) is named in `limitations`.")
    lines.append("")
    lines.append("This is real Python — use normal control flow. A for-loop over N similar")
    lines.append("nodes is preferred over writing N near-identical lines by hand, especially")
    lines.append("when N isn't small or fixed. Example — build several nodes from one branch,")
    lines.append("fanned OUT (each independent), not chained:")
    lines.append("    made = []")
    lines.append('    for key in ("a", "b", "c"):')
    lines.append('        n = SomeType(name=f"Node {key}")')
    lines.append("        parent.somePort >> n   # each wired directly from parent, not from each other")
    lines.append("        made.append(n)")
    if "Merge" in types:
        lines.append("")
        lines.append("Merge accepts several inputs (fan-IN). Wire each source into it individually —")
        lines.append("there is no `[a, b] >> merge` shortcut, `>>` cannot be defined on a plain list:")
        lines.append("    merge = Merge()")
        lines.append("    for source in (a, b, c):")
        lines.append("        source >> merge")
    return "\n".join(lines)


class ExecutionResult:
    def __init__(self):
        self.workflow: Workflow | None = None
        self.error: str | None = None
        self.partial: bool = False
        self.node_meta: dict[str, dict[str, Any]] = {}
        self.results: list[Any] = []


def execute_workflow_script(script: str, workflow_name: str = "sdk_script") -> ExecutionResult:
    """Exec a script against a fresh Draft, mirroring the real run_script sandbox
    pattern in ai_agent.py:1120 (curated __builtins__, fresh globals per call — no
    module-level mutable state, so this is safe under concurrent invocations).

    On error, `workflow` still holds the nodes built before the failing line —
    that partial graph is what the canvas shows next to the traceback, which is
    the design's "line 22 raised, lines 1–21 are real" state. It is NOT a
    persistable result: check `error` (or the `partial` flag it sets) before
    treating `workflow` as complete."""
    draft = _Draft()
    result = ExecutionResult()

    current_line = {"n": 0}

    def current_line_getter():
        return current_line["n"]

    ns = build_sdk_namespace(draft, current_line_getter)
    ns["results"] = result.results  # same collector-list convention as the PTC sandbox
    # Mirrors the whitelist already proven safe in ai_agent.py's PTC sandbox
    # (_execute_ptc_script, ai_agent.py:1120) — same posture, not a stricter one,
    # so a script failing here reflects an SDK-usage mistake, not a narrower sandbox.
    ns["__builtins__"] = {
        "len": len, "range": range, "enumerate": enumerate,
        "zip": zip, "map": map, "filter": filter, "sorted": sorted,
        "reversed": reversed, "list": list, "dict": dict, "set": set,
        "tuple": tuple, "str": str, "int": int, "float": float,
        "bool": bool, "type": type, "isinstance": isinstance,
        "print": lambda *a, **kw: None,
        "min": min, "max": max, "sum": sum, "abs": abs, "round": round,
        "any": any, "all": all, "hasattr": hasattr, "getattr": getattr,
        "KeyError": KeyError, "ValueError": ValueError,
        "TypeError": TypeError, "IndexError": IndexError, "AttributeError": AttributeError,
        "Exception": Exception, "StopIteration": StopIteration,
        "True": True, "False": False, "None": None,
    }

    try:
        # Parse with ast (not text-line-splitting) so multi-line calls, for-loops
        # with indented bodies, etc. are handled correctly — each TOP-LEVEL
        # statement is compiled and exec'd individually so current_line can be
        # updated between them. A node created inside a for-loop body is tagged
        # with the loop's own line (matching "one line produced many nodes" from
        # the UI provenance spike), not a fabricated per-iteration line number.
        tree = ast.parse(script, filename="<sdk_script>")
        for stmt in tree.body:
            current_line["n"] = stmt.lineno
            module = ast.Module(body=[stmt], type_ignores=[])
            ast.fix_missing_locations(module)
            code = compile(module, "<sdk_script>", "exec")
            exec(code, ns)
        result.workflow = Workflow(name=workflow_name, nodes=draft.nodes, connections=draft.connections)
        result.node_meta = draft.node_meta
    except Exception as e:  # noqa: BLE001 - deliberately broad, this IS the error channel
        result.error = f"line {current_line['n']}: {type(e).__name__}: {e}"
        result.partial = True
        result.workflow = Workflow(name=workflow_name, nodes=draft.nodes, connections=draft.connections)
        result.node_meta = draft.node_meta
    return result
