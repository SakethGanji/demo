"""The mistake checker (Stage 2, CONTRACTS-STAGE2 §3): static findings about a workflow definition.

``check_workflow(definition, *, lookups) -> CheckResult`` is pure apart from the injected ``lookups``.
It never raises for odd input: anything it can't understand is skipped (a miss beats a false alarm).

Pipeline
--------
1. Topological order. A cycle is allowed only if it passes through a Loop node (the edge back into the
   Loop is ignored for ordering); any other cycle is WF008.
2. Shapes flow from the triggers through ``engine.contracts.node_contract``. Multi-input nodes (Merge)
   get one entry per incoming connection, in connection order.
3. Every string parameter (nested) is scanned for ``{{ ... }}`` exactly the way the runtime does
   (``\\{\\{(.+?)\\}\\}``), run through ``ExpressionEngine._transform_expression``, parsed with ``ast`` and
   walked for ``$json`` / ``$input`` / ``$node["X"].json`` / ``$vars.X`` chains. Everything else is ignored.
4. Findings carry stable codes (see ``CONTRACTS-STAGE2.md`` §3 for WF001..WF009, ``DESIGN-STAGE5-UIS.md`` §4.5 for
   WF013..WF019 and ``DESIGN-STAGE8-REUSE.md`` §5.4 for WF020..WF029).

Code catalog (stable; a code never changes meaning)
---------------------------------------------------
=======  ========  ======================================================================================
WF001    error     ``$node["X"]`` where X doesn't exist or isn't upstream
WF002    error*    path missing from a known shape (*warning when the shape is ``observed``)
WF003    warning   string/boolean used as a number
WF004    warning   unconnected branch port / node unreachable from a trigger
WF005    info      reference into an unknown shape
WF006    error     ``$vars`` / table / sub-workflow / credential / dataset doesn't exist; ``workflowId`` is an expression
WF007    error     HttpRequest host not on the egress allowlist
WF008    error     required property missing, invalid option, or a cycle
WF009    error     expression doesn't parse
WF013-19 -         forms, approvals and the interface (Stage 5)
WF020    error     sub-workflow cycle (including a workflow that calls itself)
WF021    error     the called workflow has no ``ExecuteWorkflowTrigger``
WF022    warn/err  pinned version missing; ``latest`` never published (warning in a draft check, error at publish)
WF023    info/err  ``version: "draft"`` pin (including legacy nodes); error at publish
WF024    warn/err  input mapping doesn't fit the child's input (error: required input unmapped in ``map`` mode)
WF025    error     the child's call closure contains an ``Approval``
WF026    warn/info child output can't be typed (warning); ``Respond`` body ignored in a sub-workflow (info)
WF027    error     the called workflow is in another project
WF028    warning   static call chain deeper than ``max_execution_depth``
WF029    info      child declares no inputs (passthrough), so the call can't be type-checked
WF030    warning   Loop "Run at a time" (``concurrency``) above the limit (20), not a whole number, or below 1
WF031    warning   Loop runs items side by side, stops on a failure, and its body calls out (network / database / ...)
=======  ========  ======================================================================================

Deliberate leniency (documented in the Stage 2 report)
------------------------------------------------------
* A missing field in an ``observed`` shape (inferred from sample runs) is a WF002 *warning*, not an error:
  optional fields are easy to miss in a sample, and an approved sample must not block publishing.
* The ``fallback`` port of Switch and every port of Loop are not subject to the "unused branch" warning.
* Option lists for ``model*`` properties are suggestions (seeds use newer model ids); they are not enforced.
* A required property only counts as missing when it has no non-empty default and is visible
  (``display_options``).
* Code / PythonStep parameters are not scanned for expressions (they hold source code, not templates).
"""

from __future__ import annotations

import ast
import copy
import difflib
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urlparse

from ..engine import shapes as sh
from ..engine.contracts import node_contract
from ..engine.expression_engine import expression_engine
from ..engine.graph import TRIGGER_NODE_TYPES
from ..engine.node_registry import node_registry
from ..engine.types import NodeDefinition

Shape = dict[str, Any]
Finding = dict[str, Any]
CheckResult = dict[str, Any]

__all__ = [
    "CheckLookups", "StaticLookups", "SubworkflowLookups", "ChildWorkflow", "check_workflow", "node_shapes", "table_shape",
    "Finding", "CheckResult",
]


# ── lookups ──────────────────────────────────────────────────────────────────────────────────


@runtime_checkable
class CheckLookups(Protocol):
    """What the checker needs to know about the outside world (all synchronous; build them up front).

    ``table_column_defs`` and ``credential_exists`` are additions to the frozen contract: the first hands
    DataTable's contract the raw column list, the second backs the credential part of WF006.
    """

    egress_allowlist: list[str]

    def vars_exist(self, name: str) -> bool: ...
    def secret_exists(self, name: str) -> bool: ...
    def table_exists(self, id_or_name: str) -> bool: ...
    def table_columns(self, id_or_name: str) -> Shape | None: ...
    def table_column_defs(self, id_or_name: str) -> list[dict[str, Any]] | None: ...
    def workflow_exists(self, workflow_id: str) -> bool: ...
    def credential_exists(self, credential_id: str) -> bool: ...
    def api_operation(self, spec_id: str, operation_id: str) -> dict[str, Any] | None: ...


def table_shape(columns: Sequence[Mapping[str, Any]]) -> Shape:
    """Shape of one row of a data table (``{id, <column>...}``) — same rule as the DataTable node's contract."""
    from ..nodes.data.data_table import _column_shape

    props: dict[str, Shape] = {"id": sh.scalar("integer", "declared")}
    required = ["id"]
    for col in columns:
        if isinstance(col, Mapping) and col.get("name"):
            props[str(col["name"])] = _column_shape(dict(col))
            if col.get("required"):
                required.append(str(col["name"]))
    return sh.object_of(props, required, "declared")


@runtime_checkable
class SubworkflowLookups(Protocol):
    """Optional lookups for sub-workflow checks (Stage 8). The checker reaches them with ``getattr`` and skips the checks
    that need one a lookups object doesn't have, so a custom ``CheckLookups`` without these keeps working.

    * ``workflow_meta(id)``            -> ``{name, project_id, published_version: int|None, versions: [int]}`` or None
    * ``workflow_definition(id, v)``   -> the definition ``v`` (``"draft"`` | ``"latest"`` | version number) stands for, or None
    * ``workflow_io(id, v)``           -> ``workflow_io.WorkflowIO`` of that definition (cached), or None
    * ``call_graph()``                 -> ``{workflow_id: [(child_id, version_spec)]}`` for the project (drafts + published)
    * ``current_workflow_id`` / ``current_project_id`` -> the workflow being checked (cycle and cross-project checks)
    * ``max_depth``                    -> ``max_execution_depth`` (WF028)
    """

    def workflow_meta(self, workflow_id: str) -> dict[str, Any] | None: ...
    def workflow_definition(self, workflow_id: str, version: str | int) -> dict[str, Any] | None: ...
    def workflow_io(self, workflow_id: str, version: str | int) -> Any: ...
    def call_graph(self) -> dict[str, list[tuple[str, str]]]: ...


@dataclass
class ChildWorkflow:
    """A workflow other workflows may call, as the checker sees it (DB-free; ``check_lookups`` fills it from the database).

    ``draft`` is the working copy, ``versions`` maps version number -> definition, ``published_version`` is the number
    ``latest`` resolves to (None = never published).
    """

    id: str
    name: str = ""
    project_id: str | None = None
    draft: dict[str, Any] | None = None
    versions: dict[int, dict[str, Any]] = field(default_factory=dict)
    published_version: int | None = None
    version_numbers: set[int] = field(default_factory=set)  # every version that exists, loaded or not (``versions`` may hold fewer)


@dataclass
class StaticLookups:
    """In-memory lookups: for tests and as the snapshot the DB builder fills.

    ``variables`` are every defined ``$vars`` key (secrets included, they live in the same table);
    ``secrets`` the subset of type ``secret``. ``tables`` maps table name (and optionally id) to its columns.
    ``credentials=None`` means "not known, don't check".
    """

    variables: set[str] = field(default_factory=set)
    secrets: set[str] = field(default_factory=set)
    tables: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    workflows: set[str] = field(default_factory=set)
    credentials: set[str] | None = None
    egress_allowlist: list[str] = field(default_factory=list)
    api_specs: dict[str, dict[str, dict[str, Any]]] = field(default_factory=dict)  # spec id -> operation id -> Operation
    # the project's datasets (link ids, analytics ids and names) a ``datasetSelector`` may name; None = not known, don't check
    datasets: set[str] | None = None
    # Stage 8: sub-workflows. ``children`` are the workflows that may be called (see ``ChildWorkflow``); ``call_edges`` is
    # an optional precomputed call graph (``{id: [(child_id, spec)]}``), otherwise it is read off ``children``.
    children: dict[str, ChildWorkflow] = field(default_factory=dict)
    call_edges: dict[str, list[tuple[str, str]]] | None = None
    current_workflow_id: str | None = None
    current_project_id: str | None = None
    max_depth: int = 10
    _io_cache: dict[tuple[str, str], Any] = field(default_factory=dict, repr=False, compare=False)
    _io_active: set[tuple[str, str]] = field(default_factory=set, repr=False, compare=False)

    def vars_exist(self, name: str) -> bool:
        return name in self.variables or name in self.secrets

    def secret_exists(self, name: str) -> bool:
        return name in self.secrets

    def table_exists(self, id_or_name: str) -> bool:
        return id_or_name in self.tables

    def table_column_defs(self, id_or_name: str) -> list[dict[str, Any]] | None:
        return self.tables.get(id_or_name)

    def table_columns(self, id_or_name: str) -> Shape | None:
        cols = self.tables.get(id_or_name)
        return None if cols is None else table_shape(cols)

    def workflow_exists(self, workflow_id: str) -> bool:
        return workflow_id in self.workflows or workflow_id in self.children

    # -- sub-workflows (Stage 8) --
    def workflow_meta(self, workflow_id: str) -> dict[str, Any] | None:
        c = self.children.get(workflow_id)
        if c is None:
            return None
        return {"name": c.name or c.id, "project_id": c.project_id, "published_version": c.published_version,
                "versions": sorted(set(c.versions) | c.version_numbers)}

    def workflow_definition(self, workflow_id: str, version: str | int) -> dict[str, Any] | None:
        c = self.children.get(workflow_id)
        if c is None:
            return None
        if version == "draft":
            return c.draft
        if version == "latest":
            return c.versions.get(c.published_version) if c.published_version is not None else None
        try:
            return c.versions.get(int(version))
        except (TypeError, ValueError):
            return None

    def call_graph(self) -> dict[str, list[tuple[str, str]]]:
        if self.call_edges is not None:
            return {k: list(v) for k, v in self.call_edges.items()}
        from .workflow_io import call_refs

        graph: dict[str, list[tuple[str, str]]] = {}
        for c in self.children.values():
            edges: list[tuple[str, str]] = []
            defs = ([c.draft] if c.draft else []) + ([c.versions[c.published_version]] if c.published_version in c.versions else [])
            for d in defs:
                for r in call_refs(d):
                    if not r.disabled and (r.workflow_id, r.spec) not in edges:
                        edges.append((r.workflow_id, r.spec))
            graph[c.id] = edges
        return graph

    def workflow_io(self, workflow_id: str, version: str | int) -> Any:
        """``WorkflowIO`` of a child at ``version``, cached per (id, version). None when it can't be resolved or is mid-derivation (a cycle)."""
        key = (workflow_id, str(version))
        if key in self._io_cache:
            return self._io_cache[key]
        definition = self.workflow_definition(workflow_id, version)
        if definition is None or key in self._io_active:
            return None
        from .workflow_io import derive_io

        self._io_active.add(key)
        try:
            io = derive_io(definition, self)
        finally:
            self._io_active.discard(key)
        self._io_cache[key] = io
        return io

    def credential_exists(self, credential_id: str) -> bool:
        return self.credentials is None or credential_id in self.credentials

    def api_operation(self, spec_id: str, operation_id: str) -> dict[str, Any] | None:
        return (self.api_specs.get(spec_id) or {}).get(operation_id)


# ── small helpers ────────────────────────────────────────────────────────────────────────────

_EXPR_RE = re.compile(r"\{\{(.+?)\}\}")  # the runtime's own pattern (no DOTALL: multi-line never matches)
_NODE_DOT_RE = re.compile(r"\$node\.([A-Za-z_]\w*)")
_NODE_REF_RE = re.compile(r"""\$node\[["']([^"']+)["']\]""")
_IDENT_RE = re.compile(r"^[A-Za-z_]\w*$")
_SKIP_EXPR_TYPES = {"Code", "PythonStep"}
_NUMERIC_CMP = (ast.Lt, ast.LtE, ast.Gt, ast.GtE)
_ARITH = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod, ast.Pow, ast.FloorDiv)


def format_path(path: Sequence[str | int]) -> str:
    out = ""
    for seg in path:
        if isinstance(seg, int):
            out += f"[{seg}]"
        elif _IDENT_RE.match(seg):
            out += f".{seg}" if out else seg
        else:
            out += f'["{seg}"]'
    return out


def _suggest(name: str, options: Iterable[str]) -> str | None:
    opts = [o for o in options if isinstance(o, str)]
    if not opts or name in opts:
        return None
    lower = {o.lower(): o for o in opts}
    if name.lower() in lower:
        return lower[name.lower()]
    m = difflib.get_close_matches(name, opts, n=1, cutoff=0.6)
    return m[0] if m else None


def _did_you_mean(candidate: str | None) -> str:
    return f" Did you mean '{candidate}'?" if candidate else ""


def _is_empty(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _walk_strings(value: Any, path: str = "") -> Iterable[tuple[str, str]]:
    """(param path, string) for every string in a nested params value (dict keys are not scanned)."""
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, dict):
        for k, v in value.items():
            yield from _walk_strings(v, f"{path}.{k}" if path else str(k))
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from _walk_strings(v, f"{path}[{i}]")


def _is_schema_key(path: str) -> bool:
    top = re.split(r"[.\[]", path, maxsplit=1)[0]
    return top.endswith("_schema") or top.endswith("Schema")


# ── graph ────────────────────────────────────────────────────────────────────────────────────


def _sccs(names: list[str], adj: dict[str, list[str]]) -> list[list[str]]:
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on: set[str] = set()
    stack: list[str] = []
    out: list[list[str]] = []
    counter = [0]

    def strong(v: str) -> None:
        index[v] = low[v] = counter[0]
        counter[0] += 1
        stack.append(v)
        on.add(v)
        for w in adj.get(v, []):
            if w not in index:
                strong(w)
                low[v] = min(low[v], low[w])
            elif w in on:
                low[v] = min(low[v], index[w])
        if low[v] == index[v]:
            comp = []
            while True:
                w = stack.pop()
                on.discard(w)
                comp.append(w)
                if w == v:
                    break
            out.append(comp)

    for n in names:
        if n not in index:
            strong(n)
    return out


@dataclass
class _Edge:
    src: str
    dst: str
    src_port: str
    dst_port: str


def _edges(definition: dict, known: set[str]) -> list[_Edge]:
    out = []
    for c in definition.get("connections") or []:
        if not isinstance(c, dict):
            continue
        s, t = c.get("source_node"), c.get("target_node")
        if s in known and t in known:
            out.append(_Edge(s, t, c.get("source_output") or "main", c.get("target_input") or "main"))
    return out


# ── expression chains ────────────────────────────────────────────────────────────────────────


@dataclass
class _Ref:
    kind: str  # json | input_raw | input_item | input | node_obj | node | vars
    name: str | None = None  # node name for kind == "node"
    path: list[str | int] = field(default_factory=list)
    truncated: bool = False  # a computed subscript cut the chain: path is a checked prefix only
    extras: list[ast.AST] = field(default_factory=list)  # sub-expressions to visit (computed keys, defaults)


def _extend(ref: _Ref | None, key: Any) -> _Ref | None:
    if ref is None:
        return None
    if ref.truncated:
        return ref
    k = ref.kind
    if k == "input_raw":
        if key == 0:  # the engine's `$input` is the list of item json: [0] is already the json
            return _Ref("input", extras=ref.extras)
        if key in ("item", "first"):  # n8n style `$input.item.json.x`
            return _Ref("input_item", extras=ref.extras)
        return None
    if k == "input_item":
        return _Ref("input", extras=ref.extras) if key == "json" else None
    if k == "node_obj":
        return _Ref("node", ref.name, extras=ref.extras) if key == "json" else None
    return _Ref(k, ref.name, [*ref.path, key], extras=list(ref.extras))


class _Walker:
    """Walks one parsed expression and reports chains and type mismatches through callbacks."""

    def __init__(self, node_map: dict[str, str], on_ref, on_mismatch, shape_of) -> None:
        self.node_map = node_map  # sanitized engine variable name -> real node name
        self.on_ref = on_ref
        self.on_mismatch = on_mismatch
        self.shape_of = shape_of  # _Ref -> Shape | None  (verified shape of a ref, None if unknown/missing)

    # chain -------------------------------------------------------------------------------
    def chain(self, n: ast.AST) -> _Ref | None:
        if isinstance(n, ast.Name):
            if n.id == "json_data":
                return _Ref("json")
            if n.id == "input_data":
                return _Ref("input_raw")
            if n.id == "vars":
                return _Ref("vars")
            if n.id.startswith("node_"):
                if n.id.endswith("_json") and n.id[5:-5] in self.node_map:
                    return _Ref("node", self.node_map[n.id[5:-5]])
                if n.id[5:] in self.node_map:
                    return _Ref("node_obj", self.node_map[n.id[5:]])
            return None
        if isinstance(n, ast.Attribute):
            return _extend(self.chain(n.value), n.attr)
        if isinstance(n, ast.Subscript):
            base = self.chain(n.value)
            if base is None:
                return None
            if isinstance(n.slice, ast.Constant) and isinstance(n.slice.value, (str, int)) and not isinstance(n.slice.value, bool):
                return _extend(base, n.slice.value)
            if base.kind in ("json", "input", "node", "vars") and not base.truncated:
                return _Ref(base.kind, base.name, list(base.path), True, [*base.extras, n.slice])
            return None
        if isinstance(n, ast.Call):
            f = n.func
            if isinstance(f, ast.Name) and f.id == "get" and len(n.args) >= 2:
                key = n.args[1]
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    r = _extend(self.chain(n.args[0]), key.value)
                    if r is not None:
                        r.extras = [*r.extras, *n.args[2:]]
                    return r
            if (isinstance(f, ast.Attribute) and f.attr == "get" and isinstance(f.value, ast.Name) and f.value.id == "vars"
                    and n.args and isinstance(n.args[0], ast.Constant) and isinstance(n.args[0].value, str)):
                return _Ref("vars", path=[n.args[0].value], extras=list(n.args[1:]))
        return None

    # visit -------------------------------------------------------------------------------
    def visit(self, n: ast.AST) -> None:
        if isinstance(n, (ast.Name, ast.Attribute, ast.Subscript, ast.Call)):
            r = self.chain(n)
            if r is not None and r.kind in ("json", "input", "node", "vars"):
                if r.path:
                    self.on_ref(r)
                for e in r.extras:
                    self.visit(e)
                return
            if isinstance(n, ast.Call):
                f = self.chain(n.func)
                if f is not None and f.kind in ("json", "input", "node") and f.path and not f.truncated:
                    trimmed = _Ref(f.kind, f.name, f.path[:-1], extras=f.extras)  # last segment is the method name
                    if trimmed.path:
                        self.on_ref(trimmed)
                    for e in f.extras:
                        self.visit(e)
                    for a in [*n.args, *[k.value for k in n.keywords]]:
                        self.visit(a)
                    return
        if isinstance(n, ast.Compare):
            self._compare(n)
        elif isinstance(n, ast.BinOp):
            self._binop(n)
        for child in ast.iter_child_nodes(n):
            self.visit(child)

    # types -------------------------------------------------------------------------------
    def _type(self, n: ast.AST) -> tuple[str | None, str | None]:
        """(type, label) with type in string|boolean|number; label set only for ref operands."""
        if isinstance(n, ast.Constant):
            v = n.value
            if isinstance(v, bool):
                return "boolean", None
            if isinstance(v, (int, float)):
                return "number", None
            if isinstance(v, str):
                return "string", None
            return None, None
        r = self.chain(n)
        if r is not None and r.kind in ("json", "input", "node") and r.path and not r.truncated:
            s = self.shape_of(r)
            t = (s or {}).get("type")
            if t in ("integer", "number"):
                return "number", format_path(r.path)
            if t in ("string", "boolean"):
                return t, format_path(r.path)
        return None, None

    def _compare(self, n: ast.Compare) -> None:
        left = n.left
        for op, right in zip(n.ops, n.comparators):
            if isinstance(op, _NUMERIC_CMP):
                self._pair(left, right, "compared as a number")
            left = right

    def _binop(self, n: ast.BinOp) -> None:
        op = n.op
        if not isinstance(op, _ARITH):
            return
        tl, tr = self._type(n.left), self._type(n.right)
        for (t, label), (o, _) in ((tl, tr), (tr, tl)):
            if label is None or o != "number":
                continue
            if t == "string" and isinstance(op, (ast.Mult, ast.Mod)):
                continue  # "ab" * 3 and "%d" % x are valid
            if t in ("string", "boolean"):
                self.on_mismatch(label, t, "used in arithmetic")
                return

    def _pair(self, a: ast.AST, b: ast.AST, usage: str) -> None:
        (ta, la), (tb, lb) = self._type(a), self._type(b)
        for t, label, other in ((ta, la, tb), (tb, lb, ta)):
            if label is not None and t in ("string", "boolean") and other == "number":
                self.on_mismatch(label, t, usage)
                return


# ── the checker ──────────────────────────────────────────────────────────────────────────────


class _Checker:
    def __init__(
        self,
        definition: dict,
        lookups: CheckLookups,
        output_overrides: dict[str, Shape] | None = None,
        *,
        workflow_id: str | None = None,
        for_publish: bool = False,
        shapes_only: bool = False,
    ) -> None:
        self.lookups = lookups
        self.definition = definition
        self.output_overrides = output_overrides or {}
        self.workflow_id = workflow_id or getattr(lookups, "current_workflow_id", None) or (definition.get("id") if isinstance(definition.get("id"), str) else None)
        self.for_publish = for_publish
        self.shapes_only = shapes_only  # compute node shapes only: no findings pass (used to derive a child's output)
        self._inputs_of: dict[str, dict[str, Shape]] = {}
        self._io_memo: dict[tuple[str, str], Any] = {}
        self.findings: list[Finding] = []
        self._seen: set[tuple] = set()
        self.nodes: dict[str, NodeDefinition] = {}
        for raw in definition.get("nodes") or []:
            if isinstance(raw, dict) and raw.get("name") and raw.get("type"):
                self.nodes[str(raw["name"])] = _node_def(raw)
        self.names = list(self.nodes)
        self.edges = _edges(definition, set(self.nodes))
        self.node_map = {expression_engine._sanitize_name(n): n for n in self.names}
        self.shapes: dict[str, dict[str, dict[str, Shape]]] = {}
        self.cov = {"refs_total": 0, "refs_checked": 0, "refs_unknown": 0, "nodes_total": len(self.names), "nodes_known_output": 0}
        self._ancestors: dict[str, set[str]] = {}
        self._back: set[int] = set()  # id() of loop-closing edges: they feed the next iteration, not this pass

    # findings
    def add(self, code: str, severity: str, message: str, node: str | None = None, param: str | None = None, path: str | None = None) -> None:
        key = (code, node, param, path, message)
        if key in self._seen:
            return
        self._seen.add(key)
        self.findings.append({"code": code, "severity": severity, "message": message, "node": node, "param": param, "path": path})

    # graph
    def ancestors_of(self, name: str) -> set[str]:
        if name not in self._ancestors:
            seen: set[str] = set()
            todo = [name]
            while todo:
                cur = todo.pop()
                for e in self.edges:
                    if e.dst == cur and e.src not in seen:
                        seen.add(e.src)
                        todo.append(e.src)
            self._ancestors[name] = seen
        return self._ancestors[name]

    def order(self) -> list[str]:
        adj: dict[str, list[str]] = {n: [] for n in self.names}
        for e in self.edges:
            adj[e.src].append(e.dst)
        comp_of: dict[str, int] = {}
        for i, comp in enumerate(_sccs(self.names, adj)):
            for n in comp:
                comp_of[n] = i
        forward: list[_Edge] = []
        for e in self.edges:
            back = comp_of[e.src] == comp_of[e.dst] and self.nodes[e.dst].type == "Loop"
            if back:
                self._back.add(id(e))
            else:
                forward.append(e)
        fadj: dict[str, list[str]] = {n: [] for n in self.names}
        for e in forward:
            fadj[e.src].append(e.dst)
        for comp in _sccs(self.names, fadj):
            if len(comp) > 1 or comp[0] in fadj[comp[0]]:
                members = sorted(comp, key=self.names.index)
                loop = " → ".join([*members, members[0]])
                self.add("WF008", "error", f"Workflow has a cycle ({loop}). Only a Loop node can close one.", node=members[0])
        indeg = {n: 0 for n in self.names}
        for e in forward:
            indeg[e.dst] += 1
        ready = [n for n in self.names if indeg[n] == 0]
        order: list[str] = []
        while ready:
            n = ready.pop(0)
            order.append(n)
            for e in forward:
                if e.src == n:
                    indeg[e.dst] -= 1
                    if indeg[e.dst] == 0:
                        ready.append(e.dst)
        order += [n for n in self.names if n not in order]  # cycle remnants: processed with what is known
        return order

    # shapes
    def _incoming(self, name: str) -> tuple[dict[str, Shape], list[str]]:
        """Input shapes per port (connection order) and the names of the upstream nodes."""
        node = self.nodes[name]
        multi = _is_multi_input(node.type)
        inputs: dict[str, Shape] = {}
        sources: list[str] = []
        for e in self.edges:
            if e.dst != name or id(e) in self._back:
                continue
            sources.append(e.src)
            shape = self._port_shape(e.src, e.src_port)
            key = f"{e.src}:{e.src_port}" if multi else e.dst_port
            if key in inputs:
                inputs[key] = sh.merge_shapes(inputs[key], shape)
            else:
                inputs[key] = shape
        if not inputs:
            inputs = {"main": sh.unknown()}
        elif not multi and "main" not in inputs:
            inputs = {"main": sh.merge_all(inputs.values())}
        return inputs, list(dict.fromkeys(sources))

    def _port_shape(self, src: str, port: str) -> Shape:
        outs = (self.shapes.get(src) or {}).get("outputs")
        if outs is None:
            return sh.unknown()
        if port in outs:
            return copy.deepcopy(outs[port])
        if "main" in outs:
            return copy.deepcopy(outs["main"])
        return sh.unknown()

    def _table_columns(self) -> dict[str, list[dict[str, Any]]]:
        out: dict[str, list[dict[str, Any]]] = {}
        for n in self.nodes.values():
            if n.type == "DataTable":
                t = (n.parameters or {}).get("table")
                if isinstance(t, str) and t and "{{" not in t and t not in out:
                    cols = _call(self.lookups, "table_column_defs", t)
                    if cols is not None:
                        out[t] = cols
        return out

    def _api_operation(self, node: NodeDefinition) -> dict[str, Any] | None:
        ref = _api_ref(node)
        if ref is None:
            return None
        op = _call(self.lookups, "api_operation", *ref)
        return op if isinstance(op, dict) else None

    def run(self) -> CheckResult:
        order = self.order()
        table_cols = self._table_columns()
        sources_of: dict[str, list[str]] = {}
        inputs_of: dict[str, dict[str, Shape]] = {}
        for name in order:
            node = self.nodes[name]
            inputs, sources = self._incoming(name)
            inputs_of[name], sources_of[name] = inputs, sources
            try:
                c = node_contract(node, inputs, table_columns=table_cols or None, api_operation=self._api_operation(node))
                if node.type == "ExecuteWorkflow":
                    c = self._derived_call_contract(node, inputs, c)
            except Exception:  # a contract must never break the checker
                c = {"inputs": inputs, "outputs": {"main": sh.unknown()}, "error": sh.unknown()}
            if name in self.output_overrides:  # a test run replaced this node's output (workflow-target tests: the trigger)
                c = {**c, "outputs": {**c["outputs"], "main": copy.deepcopy(self.output_overrides[name])}}
            self.shapes[name] = {"inputs": c["inputs"], "outputs": c["outputs"]}
            if any(not sh.is_unknown(s) for p, s in c["outputs"].items() if p != "error"):
                self.cov["nodes_known_output"] += 1
        self._inputs_of = inputs_of
        if self.shapes_only:
            return {"findings": [], "shapes_by_node": self.shapes, "coverage": self.cov}
        for name in self.names:
            node = self.nodes[name]
            if not node.disabled:
                self._check_node(node, inputs_of[name], sources_of[name])
        self._check_branches()
        self._check_loops()
        self._check_reachability()
        self._check_forms_and_approvals()
        self._check_interface()
        self._check_subworkflows()
        return {"findings": self.findings, "shapes_by_node": self.shapes, "coverage": self.cov}

    # per node ----------------------------------------------------------------------------
    def _check_node(self, node: NodeDefinition, inputs: dict[str, Shape], sources: list[str]) -> None:
        params = node.parameters or {}
        self._check_properties(node, params)
        item = sh.merge_all(inputs.values()) if len(inputs) > 1 else next(iter(inputs.values()))
        first = next(iter(inputs.values()))
        ctx = _Ctx(node.name, item, first, sources)
        if node.type not in _SKIP_EXPR_TYPES:
            for path, text in _walk_strings(params):
                if not _is_schema_key(path):
                    self._scan_string(ctx, path, text)
        self._check_rule_types(node, params, item, sources)
        self._check_links(node, params)

    def _check_properties(self, node: NodeDefinition, params: dict) -> None:
        if not node_registry.has(node.type):
            return
        desc = node_registry.get(node.type).node_description
        if desc is None:
            return
        props = {p.name: p for p in desc.properties}

        def value_of(key: str) -> Any:
            v = params.get(key)
            return props[key].default if v is None and key in props else v

        def visible(p) -> bool:
            opts = p.display_options or {}
            for k, vals in (opts.get("show") or {}).items():
                if value_of(k) not in vals:
                    return False
            for k, vals in (opts.get("hide") or {}).items():
                if value_of(k) in vals:
                    return False
            return True

        api_node = node.type == "HttpRequest" and _api_ref(node) is not None
        for p in desc.properties:
            if not visible(p):
                continue
            if api_node and p.name in ("url", "method"):
                continue  # an API-catalog endpoint supplies the method and the URL
            v = params.get(p.name)
            if p.required and _is_empty(v) and _is_empty(p.default) and p.type != "boolean":
                self.add("WF008", "error", f"'{p.display_name}' is required.", node=node.name, param=p.name)
            elif (p.type == "datasetSelector" and isinstance(v, str) and v.strip() and "{{" not in v
                  and getattr(self.lookups, "datasets", None) is not None and v.strip() not in self.lookups.datasets):
                self.add("WF006", "error", f"Dataset '{v.strip()}' doesn't exist in this project.", node=node.name, param=p.name, path=v.strip())
            elif (p.type == "options" and "model" not in p.name.lower() and p.options and isinstance(v, str) and v.strip() and "{{" not in v
                  and not (node.type == "ExecuteWorkflow" and p.name == "version" and _valid_version(v))):  # "3" pins version 3
                values = [o.value for o in p.options]
                if v not in values:
                    self.add("WF008", "error",
                             f"'{v}' isn't a valid value for '{p.display_name}'." + _did_you_mean(_suggest(v, values)),
                             node=node.name, param=p.name)

    # expressions -------------------------------------------------------------------------
    def _scan_string(self, ctx: "_Ctx", param: str, text: str) -> None:
        if "{{" not in text:
            return
        for m in _EXPR_RE.finditer(text):
            raw = m.group(1).strip()
            self._check_expression(ctx, param, raw)

    def _check_expression(self, ctx: "_Ctx", param: str, raw: str) -> None:
        node = ctx.node
        normalized = _NODE_DOT_RE.sub(lambda m: f'$node["{m.group(1)}"]', raw)
        # WF001 from the source text (names are matched before the engine sanitizes them)
        upstream = self.ancestors_of(node)
        for ref_name in dict.fromkeys(_NODE_REF_RE.findall(normalized)):
            if ref_name not in self.nodes:
                self.add("WF001", "error",
                         f"Node '{ref_name}' doesn't exist." + _did_you_mean(_suggest(ref_name, [n for n in self.names if n != node])),
                         node=node, param=param, path=ref_name)
            elif ref_name not in upstream:
                self.add("WF001", "error", f"Node '{ref_name}' isn't upstream of '{node}', so its output isn't available here.",
                         node=node, param=param, path=ref_name)
        try:
            tree = ast.parse(expression_engine._transform_expression(normalized), mode="eval")
        except (SyntaxError, ValueError) as e:
            self.add("WF009", "error", f"Expression doesn't parse: {{{{ {raw} }}}} ({getattr(e, 'msg', str(e))})", node=node, param=param)
            return

        def shape_of(ref: _Ref) -> Shape | None:
            status, s, _ = self._resolve(ctx, ref, upstream)
            return s if status == "ok" else None

        def on_ref(ref: _Ref) -> None:
            self._on_ref(ctx, param, ref, upstream)

        def on_mismatch(label: str, t: str, usage: str) -> None:
            self.add("WF003", "warning", f"'{label}' is a {t} but is {usage}.", node=node, param=param, path=label)

        _Walker(self.node_map, on_ref, on_mismatch, shape_of).visit(tree.body)

    def _resolve(self, ctx: "_Ctx", ref: _Ref, upstream: set[str]) -> tuple[str, Shape | None, dict]:
        """('ok'|'unknown'|'missing'|'skip', shape, info). info has the failing segment for 'missing'."""
        if ref.kind == "node":
            if ref.name not in upstream:
                return "skip", None, {}
            outs = (self.shapes.get(ref.name) or {}).get("outputs") or {}
            ports = [s for p, s in outs.items() if p != "error"]
            root = sh.merge_all(ports) if len(ports) > 1 else (ports[0] if ports else sh.unknown())
            label = ref.name
        elif ref.kind == "input":
            root, label = ctx.first, ctx.upstream_label()
        else:
            root, label = ctx.item, ctx.upstream_label()
        cur = root
        for i, seg in enumerate(ref.path):
            if sh.is_unknown(cur):
                return "unknown", None, {"label": label}
            found = sh.lookup(cur, [seg])
            if found is None:
                return "missing", None, {"label": label, "index": i, "parent": cur, "source": cur.get("x-source")}
            verified = sh.is_closed_object(cur) or (cur.get("type") == "array" and isinstance(cur.get("items"), dict) and (isinstance(seg, int) or str(seg).isdigit()))
            if not verified:
                return "unknown", None, {"label": label}
            cur = found
        return "ok", cur, {"label": label}

    def _on_ref(self, ctx: "_Ctx", param: str, ref: _Ref, upstream: set[str]) -> None:
        if ref.kind == "vars":
            name = ref.path[0] if ref.path else None
            if isinstance(name, str) and not self.lookups.vars_exist(name):
                self.add("WF006", "error", f"Variable '{name}' isn't defined in this environment.", node=ctx.node, param=param, path=name)
            return
        if ref.kind == "node" and ref.name not in upstream:
            return  # WF001 already reported
        self.cov["refs_total"] += 1
        status, _, info = self._resolve(ctx, ref, upstream)
        path = format_path(ref.path)
        if status == "missing":
            self.cov["refs_checked"] += 1
            i = info["index"]
            seg = ref.path[i]
            shown = format_path(ref.path[: i + 1])
            sib = (info["parent"].get("properties") or {}) if isinstance(info["parent"], dict) else {}
            hint = _suggest(str(seg), sib)
            if hint:
                hint = format_path([*ref.path[:i], hint])
            sev = "warning" if info.get("source") == "observed" else "error"
            self.add("WF002", sev, f"Field '{shown}' isn't in the output of '{info['label']}'." + _did_you_mean(hint),
                     node=ctx.node, param=param, path=path)
        elif status == "ok":
            self.cov["refs_checked"] += 1
        else:
            self.cov["refs_unknown"] += 1
            label = info.get("label") or ctx.upstream_label()
            self.add("WF005", "info", f"Can't check '{path}': the output of '{label}' has no known shape yet.",
                     node=ctx.node, param=param, path=path)

    # rule-style conditions (If / Filter) -------------------------------------------------
    def _check_rule_types(self, node: NodeDefinition, params: dict, item: Shape, sources: list[str]) -> None:
        if node.type not in ("If", "Filter"):
            return
        if node.type == "Filter" and params.get("mode", "rules") != "rules":
            return
        if node.type == "If" and params.get("condition"):
            return
        if params.get("operation") not in ("gt", "gte", "lt", "lte"):
            return
        fld = params.get("field")
        if not isinstance(fld, str) or not fld.strip() or "{{" in fld:
            return
        segs: list[str | int] = [int(p) if p.isdigit() else p for p in fld.strip().split(".")]
        found = sh.lookup(item, segs)
        if found is not None and found.get("type") == "string":
            self.add("WF003", "warning", f"'{fld}' is a string but the condition compares it as a number.",
                     node=node.name, param="field", path=fld)

    # references to the outside world -----------------------------------------------------
    def _check_links(self, node: NodeDefinition, params: dict) -> None:
        lk = self.lookups
        if node.type == "DataTable":
            t = params.get("table")
            if isinstance(t, str) and t.strip() and "{{" not in t and not lk.table_exists(t):
                self.add("WF006", "error", f"Table '{t}' doesn't exist in this project.", node=node.name, param="table", path=t)
        if node.type == "ExecuteWorkflow":
            w = params.get("workflowId")
            if isinstance(w, str) and w.strip() and ("{{" in w or w.lstrip().startswith("=")):
                legacy = params.get("version") in (None, "")  # nodes saved before version pinning keep working
                self.add("WF006", "warning" if legacy else "error",
                         "workflowId is an expression; sub-workflows must be chosen, not computed, so pinning and cycle checks stay static.",
                         node=node.name, param="workflowId", path=w)
            elif isinstance(w, str) and w.strip() and not lk.workflow_exists(w):
                self.add("WF006", "error", f"Sub-workflow '{w}' doesn't exist.", node=node.name, param="workflowId", path=w)
        cred = params.get("credentialId")
        if isinstance(cred, str) and cred.strip() and "{{" not in cred and not lk.credential_exists(cred):
            self.add("WF006", "error", f"Credential '{cred}' doesn't exist.", node=node.name, param="credentialId", path=cred)
        if node.type == "HttpRequest":
            ref = _api_ref(node)
            if ref is not None:
                if self._api_operation(node) is None:
                    self.add("WF006", "error", f"API endpoint '{ref[1]}' isn't in the API catalog.",
                             node=node.name, param="api_ref", path=ref[1])
            else:
                self._check_egress(node, params.get("url"))

    def _check_egress(self, node: NodeDefinition, url: Any) -> None:
        allow = list(self.lookups.egress_allowlist or [])
        if not allow or not isinstance(url, str):
            return
        m = re.match(r"^\s*(https?)://([^/?#]*)", url, re.IGNORECASE)
        if not m or "{{" in m.group(2):
            return  # no scheme, or the host contains an expression: can't tell
        host = urlparse(f"{m.group(1)}://{m.group(2)}").hostname or ""
        from ..broker.policy import host_matches

        if host and not host_matches(host, allow):
            self.add("WF007", "error", f"Host '{host}' isn't on this project's egress allowlist.", node=node.name, param="url", path=host)

    # Stage 5: forms, approvals and the interface (WF013-WF019, DESIGN-STAGE5-UIS section 4.5) ---------------
    def _check_forms_and_approvals(self) -> None:
        from ..engine import form_fields as ff

        for name, node in self.nodes.items():
            if node.disabled:
                continue
            params = node.parameters or {}
            if node.type == "FormTrigger":
                for fld in ff.unsupported_fields(params.get("input_schema") or params.get("inputSchema")):
                    self.add("WF013", "warning", f"Field '{fld}' is a list or a nested object; the form can't show it, so it is left out.",
                             node=name, param="input_schema", path=fld)
            elif node.type == "Approval":
                self._check_approval(name, params)

    def _check_approval(self, name: str, params: dict) -> None:
        from ..engine.graph import ancestors
        from types import SimpleNamespace

        conns = [SimpleNamespace(source_node=e.src, target_node=e.dst) for e in self.edges]
        if any(self.nodes[a].type == "Loop" for a in ancestors(conns, name) if a in self.nodes):
            self.add("WF014", "warning", f"'{name}' can receive several items in one run (it sits inside a loop); each arrival creates its own task.", node=name)
        used = {e.src_port for e in self.edges if e.src == name}
        if "approved" in used:
            on_timeout = params.get("on_timeout") or "timed_out"
            due = str(params.get("due_in") or "").strip()
            if "rejected" not in used:
                self.add("WF015", "warning", f"'{name}': a rejection goes nowhere (the 'rejected' output isn't connected).", node=name, path="rejected")
            if on_timeout == "timed_out" and due and "timed_out" not in used:
                self.add("WF015", "warning", f"'{name}': a timeout goes nowhere (the 'timed_out' output isn't connected).", node=name, path="timed_out")
        if params.get("four_eyes"):
            self.add("WF016", "info", f"'{name}': four-eyes review is saved but not enforced until sign-in exists.", node=name, param="four_eyes")

    def _check_interface(self) -> None:
        from ..schemas.interface import interface_findings

        for f in interface_findings(self.definition, self.shapes):
            self.add(f["code"], f["severity"], f["message"], node=f.get("node"), param=f.get("param"), path=f.get("path"))

    # Stage 8: sub-workflows (WF020-WF029, DESIGN-STAGE8-REUSE sections 2.4, 5.2, 5.4) --------------------------------
    def _workflow_io(self, workflow_id: str, version: str | int) -> Any:
        """``WorkflowIO`` of a child (cached per check). None when unknown, in another project, or the lookups can't say."""
        key = (workflow_id, str(version))
        if key not in self._io_memo:
            io = None
            fn = getattr(self.lookups, "workflow_io", None)
            if fn is not None and not self._other_project(workflow_id):
                try:
                    io = fn(workflow_id, version)
                except Exception:  # a lookup must never break the checker
                    io = None
            self._io_memo[key] = io
        return self._io_memo[key]

    def _meta(self, workflow_id: str) -> dict[str, Any] | None:
        fn = getattr(self.lookups, "workflow_meta", None)
        try:
            meta = fn(workflow_id) if fn else None
        except Exception:
            return None
        return meta if isinstance(meta, dict) else None

    def _other_project(self, workflow_id: str) -> bool:
        mine = getattr(self.lookups, "current_project_id", None)
        theirs = (self._meta(workflow_id) or {}).get("project_id")
        return bool(mine and theirs and mine != theirs)

    def _derived_call_contract(self, node: NodeDefinition, inputs: dict[str, Shape], c: dict) -> dict:
        """ExecuteWorkflow's output (and error port) typed from the child (section 2.4).

        An approved instance contract wins. The node's own ``inputs`` stay the *upstream* item here: the studio reads them
        to autocomplete ``$json`` in the mapping, and the child's input contract is checked by WF024 instead.
        """
        override = getattr(node, "contract", None)
        if node.disabled or (isinstance(override, dict) and override.get("outputs")):
            return c
        d = node_registry.get("ExecuteWorkflow").contract(node.parameters or {}, inputs, {"workflow_io": self._workflow_io})
        main = d["outputs"].get("main")
        if main is None or sh.is_unknown(main):
            return c
        c["outputs"]["main"] = copy.deepcopy(main)
        c["error"] = copy.deepcopy(d["error"])
        if node.on_error == "error_output":
            c["outputs"]["error"] = copy.deepcopy(d["error"])
        return c

    def _graph(self) -> dict[str, list[str]]:
        """Workflow id -> child ids: the lookups' call graph with *this* definition's own (possibly unsaved) edges on top."""
        fn = getattr(self.lookups, "call_graph", None)
        try:
            raw = fn() if fn else {}
        except Exception:
            raw = {}
        graph = {k: [c for c, _ in v] for k, v in (raw or {}).items()}
        if self.workflow_id:
            from .workflow_io import call_refs

            graph[self.workflow_id] = list(dict.fromkeys(r.workflow_id for r in call_refs(self.definition) if not r.disabled))
        return graph

    def _label(self, workflow_id: str) -> str:
        return str((self._meta(workflow_id) or {}).get("name") or workflow_id)

    def _check_subworkflows(self) -> None:
        from .workflow_io import call_refs

        for name, node in self.nodes.items():
            if node.disabled or node.type != "ExecuteWorkflow":
                continue
            try:
                self._check_call(node)
            except Exception:  # odd parameters never break the checker
                continue
        self._check_cycles(call_refs(self.definition))
        self._check_respond_in_child()

    def _check_cycles(self, refs: list) -> None:
        cur = self.workflow_id
        if not cur or not refs:
            return
        graph = self._graph()
        names = list(dict.fromkeys([*graph, *(c for v in graph.values() for c in v)]))
        comp = next((c for c in _sccs(names, graph) if cur in c), [cur])
        members = set(comp)
        if len(members) == 1 and cur not in graph.get(cur, []):
            return
        for r in refs:
            if r.disabled or r.workflow_id not in members:
                continue
            path = _cycle_path(graph, cur, r.workflow_id)
            shown = " → ".join(self._label(w) for w in path)
            self.add("WF020", "error",
                     f"Sub-workflow cycle ({shown}). A workflow can't call itself, directly or indirectly.",
                     node=r.node, param="workflowId", path=r.workflow_id)

    def _check_respond_in_child(self) -> None:
        from .workflow_io import trigger_node

        if trigger_node(self.definition) is None:
            return
        for name, node in self.nodes.items():
            if node.type == "Respond" and not node.disabled:
                self.add("WF026", "info",
                         f"'{name}': when this workflow is called as a sub-workflow, Respond's status code, headers and body are ignored; "
                         "it just hands its input items back.", node=name)

    def _check_call(self, node: NodeDefinition) -> None:
        from ..nodes.flow.execute_workflow import normalize_params
        from .workflow_io import literal_workflow_id

        params = node.parameters or {}
        wid = literal_workflow_id(params)
        if wid is None or not self.lookups.workflow_exists(wid):
            return  # empty / expression ids and missing targets are WF008 / WF006
        try:
            p = normalize_params(params)
        except ValueError as e:
            self.add("WF022", "error", f"{e}", node=node.name, param="version")
            return
        meta = self._meta(wid)
        if meta is None:
            return
        name = str(meta.get("name") or wid)
        if self._other_project(wid):
            self.add("WF027", "error", f"Sub-workflow '{name}' is in another project. A workflow can only call workflows of its own project.",
                     node=node.name, param="workflowId", path=wid)
            return

        spec = p["version"]
        publish_sev = "error" if self.for_publish else None
        if spec == "draft":
            tail = " This node predates version pinning, so it follows the draft." if p["legacy"] else ""
            self.add("WF023", publish_sev or "info",
                     f"'{node.name}' runs the draft of '{name}', so it follows unsaved edits. Pin 'latest' or a version number before publishing.{tail}",
                     node=node.name, param="version", path=wid)
        elif spec == "latest":
            if meta.get("published_version") is None:
                self.add("WF022", publish_sev or "warning",
                         f"Sub-workflow '{name}' has never been published, so '{node.name}' has nothing to call.",
                         node=node.name, param="version", path=wid)
                return
        else:
            if int(spec) not in (meta.get("versions") or []):
                self.add("WF022", "error", f"Sub-workflow '{name}' has no version {spec}.", node=node.name, param="version", path=wid)
                return

        io = self._workflow_io(wid, spec)
        if io is None:
            return
        if not io.has_trigger:
            self.add("WF021", "error", f"'{name}' can't be called: it has no Execute Workflow Trigger.", node=node.name, param="workflowId", path=wid)
            return
        if io.has_human:
            where = f" (in '{io.human_path[-1]}')" if io.human_path else ""
            self.add("WF025", "error",
                     f"'{name}' has an Approval step{where}; approvals must be in the top-level workflow. "
                     "Put the Approval in this workflow and the work in the sub-workflow.",
                     node=node.name, param="workflowId", path=wid)
        if io.input_source in (None, "passthrough") or sh.is_unknown(io.input) or not sh.is_closed_object(io.input):
            self.add("WF029", "info",
                     f"'{name}' accepts any input (it declares no fields), so what '{node.name}' sends can't be type-checked.",
                     node=node.name, param="inputs", path=wid)
        else:
            self._check_mapping(node, p, io, name)
        if not p["legacy"] and p["outputMode"] == "items" and sh.is_unknown(io.output):
            why = io.output_note or "its return node has no known shape yet"
            self.add("WF026", "warning", f"Can't tell what '{name}' returns ({why}), so what follows '{node.name}' can't be type-checked.",
                     node=node.name, param="workflowId", path=wid)
        self._check_depth(node, wid)

    def _check_depth(self, node: NodeDefinition, wid: str) -> None:
        limit = int(getattr(self.lookups, "max_depth", 10) or 10)
        graph = self._graph()
        memo: dict[str, int] = {}

        def depth(w: str, stack: tuple[str, ...]) -> int:  # calls below w, longest chain (cycles are WF020's business)
            if w in memo:
                return memo[w]
            best = 0
            for c in graph.get(w, []):
                if c not in stack and c != w:
                    best = max(best, 1 + depth(c, (*stack, w)))
            memo[w] = best
            return best

        chain = 1 + depth(wid, (self.workflow_id,) if self.workflow_id else ())
        if chain > limit:
            self.add("WF028", "warning",
                     f"'{node.name}' starts a chain of {chain} nested sub-workflows; runs stop at {limit} levels.",
                     node=node.name, param="workflowId", path=wid)

    # WF024 ---------------------------------------------------------------------------------
    def _check_mapping(self, node: NodeDefinition, p: dict[str, Any], io: Any, child: str) -> None:
        from ..nodes.flow.execute_workflow import _as_dict

        decl = io.input
        props: dict[str, Shape] = decl["properties"]
        required = set(decl.get("required") or [])
        incoming = self._inputs_of.get(node.name) or {"main": sh.unknown()}
        item = sh.merge_all(incoming.values()) if len(incoming) > 1 else next(iter(incoming.values()))
        legacy, mode = p["legacy"], p["inputMode"]
        name = node.name

        def mismatch(field_name: str, got: Shape, how: str) -> None:
            want = props[field_name].get("type")
            have = got.get("type")
            if sh.is_unknown(got) or want in (None, "unknown") or have in (None, "unknown", want) or got.get("nullable"):
                return
            if {want, have} <= {"integer", "number"}:
                return
            self.add("WF024", "warning", f"Input '{field_name}' of '{child}' expects {_a(want)} but {how} {_a(have)}.",
                     node=name, param="inputs" if mode == "map" else None, path=field_name)

        if mode == "map" and not legacy:
            try:
                mapped = _as_dict(p["inputs"])
            except ValueError:
                self.add("WF024", "warning", "The inputs mapping isn't valid JSON, so it can't be checked.", node=name, param="inputs")
                return
            for f in props:
                expr = mapped.get(f)
                if f not in mapped or expr is None or (isinstance(expr, str) and not expr.strip()):
                    if f not in required:
                        continue
                    if not p["includeItem"]:
                        self.add("WF024", "error", f"Required input '{f}' of '{child}' isn't mapped.", node=name, param="inputs", path=f)
                    elif sh.is_closed_object(item) and sh.lookup(item, [f]) is None:
                        self.add("WF024", "warning",
                                 f"Required input '{f}' of '{child}' isn't mapped and isn't in the incoming item.", node=name, param="inputs", path=f)
                    continue
                mismatch(f, sh.expression_value_shape(expr, item), "gets")
            for f in mapped:
                if f not in props:
                    self.add("WF024", "warning", f"'{f}' isn't an input of '{child}'; it is passed along but the sub-workflow doesn't declare it.",
                             node=name, param="inputs", path=f)
            return

        if mode == "custom":
            raw = p["customInput"]
            try:
                data = json.loads(raw) if isinstance(raw, str) else raw
            except (ValueError, TypeError):
                return
            if not isinstance(data, dict):
                return
            for f in props:
                if f in data:
                    mismatch(f, sh.expression_value_shape(data[f], item), "gets")
                elif f in required:
                    self.add("WF024", "warning", f"Required input '{f}' of '{child}' isn't in the custom input.", node=name, param="customInput", path=f)
            return

        # passThrough (and a legacy node): the incoming item goes in as it is
        if not sh.is_closed_object(item):
            return
        for f in props:
            found = sh.lookup(item, [f])
            if found is None:
                if f in required:
                    self.add("WF024", "warning", f"Required input '{f}' of '{child}' isn't in the item reaching '{name}'.", node=name, path=f)
            else:
                mismatch(f, found, "gets")

    # graph warnings ----------------------------------------------------------------------
    def _check_branches(self) -> None:
        used: dict[str, set[str]] = {}
        for e in self.edges:
            used.setdefault(e.src, set()).add(e.src_port)
        for name, node in self.nodes.items():
            if node.disabled or node.type not in ("If", "Switch") or not node_registry.has(node.type):
                continue
            ports = [p for p in node_registry.get(node.type).output_ports(node.parameters or {}) if p != "error"]
            connected = used.get(name, set())
            if len(ports) < 2 or not (connected & set(ports)):
                continue
            for p in ports:
                if p not in connected and not (node.type == "Switch" and p == "fallback"):
                    self.add("WF004", "warning", f"Output '{p}' of '{name}' isn't connected to anything.", node=name, path=p)

    def _check_loops(self) -> None:
        """WF030: a "Run at a time" the runner won't use as written. WF031: side by side + stop on failure + a body that
        calls out: items already in flight when one fails still make their calls."""
        from ..engine.effects import effect_of
        from ..engine.workflow_runner import WorkflowRunner
        from ..nodes.flow.loop import MAX_LOOP_CONCURRENCY

        for name, node in self.nodes.items():
            if node.disabled or node.type != "Loop":
                continue
            params = node.parameters or {}
            raw = params.get("concurrency")
            if raw is None or isinstance(raw, bool) or (isinstance(raw, str) and ("{{" in raw or not raw.strip())):
                continue  # default (1), or an expression only known at run time
            try:
                num = float(raw)
            except (TypeError, ValueError):
                num = None
            try:  # what the runner uses (loop_policy): int(value), else 1; at least 1
                conc = max(1, int(raw))
            except (TypeError, ValueError, OverflowError):
                conc = 1
            if num is None or num != num or not num.is_integer():
                runs = "one item" if conc == 1 else f"{min(conc, MAX_LOOP_CONCURRENCY)} items"
                self.add("WF030", "warning", f"'Run at a time' must be a whole number; '{raw}' runs {runs} at a time.",
                         node=name, param="concurrency")
            elif num < 1:
                self.add("WF030", "warning", f"'Run at a time' must be at least 1; {int(num)} runs one item at a time.",
                         node=name, param="concurrency")
                continue
            elif conc > MAX_LOOP_CONCURRENCY:
                self.add("WF030", "warning", f"'Run at a time' is {conc}; a loop runs at most {MAX_LOOP_CONCURRENCY} items "
                                             f"at a time, so it will run {MAX_LOOP_CONCURRENCY}.", node=name, param="concurrency")
            on_fail = str(params.get("onItemFailure") or "stop").strip().lower()
            if conc <= 1 or on_fail == "skip":
                continue
            body = self._loop_body(name)
            # the runner runs these bodies one after another anyway (concurrencyFallback): nothing is in flight
            if not body or any(self.nodes[b].type in WorkflowRunner._NOT_CONCURRENT for b in body):
                continue
            if any(e.dst in body and e.src not in body and e.src != name for e in self.edges):
                continue
            ext = [b for b in self.names if b in body and not self.nodes[b].disabled
                   and effect_of(self.nodes[b].type) not in ("none", "human")]
            if not ext:
                continue
            steps = ", ".join(f'"{b}"' for b in ext[:3]) + (f" and {len(ext) - 3} more" if len(ext) > 3 else "")
            self.add("WF031", "warning",
                     f"This loop runs {min(conc, MAX_LOOP_CONCURRENCY)} items at a time and stops the run when one fails, "
                     f"but items already started may still send their calls after that failure ({steps} calls out). "
                     "Set 'Run at a time' to 1 for payments and other one-time actions.",
                     node=name, param="concurrency")

    def _loop_body(self, loop: str) -> set[str]:
        """The steps reachable from the Loop's ``loop`` port without passing back through the Loop (as the runner)."""
        seen: set[str] = set()
        todo = [e.dst for e in self.edges if e.src == loop and e.src_port == "loop" and e.dst != loop]
        while todo:
            cur = todo.pop()
            if cur in seen or cur == loop:
                continue
            seen.add(cur)
            todo.extend(e.dst for e in self.edges if e.src == cur and e.dst not in seen and e.dst != loop)
        return seen

    def _check_reachability(self) -> None:
        roots = [n for n, d in self.nodes.items() if d.type in TRIGGER_NODE_TYPES]
        if not roots:
            return
        reach = set(roots)
        todo = list(roots)
        while todo:
            cur = todo.pop()
            for e in self.edges:
                if e.src == cur and e.dst not in reach:
                    reach.add(e.dst)
                    todo.append(e.dst)
        connected = {e.src for e in self.edges} | {e.dst for e in self.edges}
        for name in self.names:
            if name in connected and name not in reach:
                self.add("WF004", "warning", f"'{name}' can't be reached from any trigger.", node=name)


@dataclass
class _Ctx:
    node: str
    item: Shape  # what $json sees
    first: Shape  # what $input sees
    sources: list[str]

    def upstream_label(self) -> str:
        return " or ".join(self.sources) if self.sources else self.node


def _api_ref(node: NodeDefinition) -> tuple[str, str] | None:
    """(spec_id, operation_id) of an HttpRequest on a catalog endpoint, else None."""
    if node.type != "HttpRequest":
        return None
    ref = (node.parameters or {}).get("api_ref")
    if isinstance(ref, dict) and isinstance(ref.get("spec_id"), str) and ref["spec_id"]:
        return ref["spec_id"], str(ref.get("operation_id") or "")
    return None


def _call(lookups: Any, method: str, *args: Any) -> Any:
    fn = getattr(lookups, method, None)
    return fn(*args) if fn else None


def _valid_version(v: Any) -> bool:
    return isinstance(v, str) and v.strip().lower() in ("draft", "latest") or (isinstance(v, str) and v.strip().isdigit() and int(v) >= 1)


def _a(type_: str | None) -> str:
    return f"{'an' if (type_ or '')[:1] in 'aeiou' else 'a'} {type_}"


def _cycle_path(graph: dict[str, list[str]], start: str, via: str) -> list[str]:
    """``start -> via -> ... -> start`` (shortest way back from ``via``), for the WF020 message."""
    if via == start:
        return [start, start]
    prev: dict[str, str | None] = {via: None}
    todo = [via]
    while todo:
        cur = todo.pop(0)
        if cur == start:
            break
        for nxt in graph.get(cur, []):
            if nxt not in prev:
                prev[nxt] = cur
                todo.append(nxt)
    if start not in prev:
        return [start, via, start]
    back: list[str] = []
    cur: str | None = start
    while cur is not None:
        back.append(cur)
        cur = prev[cur]
    return [start, *reversed(back)]


def _is_multi_input(node_type: str) -> bool:
    if not node_registry.has(node_type):
        return False
    try:
        return node_registry.get(node_type).input_count > 1
    except Exception:
        return False


def _node_def(raw: dict) -> NodeDefinition:
    on_error = raw.get("on_error") or "stop"
    return NodeDefinition(
        name=str(raw["name"]),
        type=str(raw["type"]),
        parameters=raw.get("parameters") if isinstance(raw.get("parameters"), dict) else {},
        disabled=bool(raw.get("disabled", False)),
        on_error=on_error,
        continue_on_fail=bool(raw.get("continue_on_fail", False)),
        contract=raw.get("contract") if isinstance(raw.get("contract"), dict) else None,
    )


def check_workflow(
    definition: dict,
    *,
    lookups: CheckLookups,
    output_overrides: dict[str, Shape] | None = None,
    workflow_id: str | None = None,
    for_publish: bool = False,
) -> CheckResult:
    """Static check of a workflow definition ``{nodes, connections}``. See the module docstring.

    ``output_overrides`` ``{node: shape}`` replaces a node's ``main`` output shape before it propagates downstream
    (used by workflow-target tests, whose trigger output is the test's input).

    ``workflow_id`` is the workflow being checked (default ``lookups.current_workflow_id``): without it WF020 can't
    place the workflow in the call graph. ``for_publish=True`` raises WF022 (``latest`` never published) and WF023
    (``draft`` pin) from warning / info to error, the way a publish treats them (DESIGN-STAGE8-REUSE section 4.3)."""
    if not node_registry.list():
        from ..engine.node_registry import register_all_nodes

        register_all_nodes()
    return _Checker(definition or {}, lookups, output_overrides, workflow_id=workflow_id, for_publish=for_publish).run()


def node_shapes(definition: dict, lookups: CheckLookups) -> dict[str, dict[str, dict[str, Shape]]]:
    """``{node: {"inputs": {port: Shape}, "outputs": {port: Shape}}}`` without the findings pass (cheap; used by ``workflow_io``)."""
    if not node_registry.list():
        from ..engine.node_registry import register_all_nodes

        register_all_nodes()
    return _Checker(definition or {}, lookups, shapes_only=True).run()["shapes_by_node"]
