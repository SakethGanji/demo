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
4. Findings carry stable codes WF001..WF009 (see ``CONTRACTS-STAGE2.md`` §3).

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

__all__ = ["CheckLookups", "StaticLookups", "check_workflow", "table_shape", "Finding", "CheckResult"]


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
        return workflow_id in self.workflows

    def credential_exists(self, credential_id: str) -> bool:
        return self.credentials is None or credential_id in self.credentials


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
    def __init__(self, definition: dict, lookups: CheckLookups) -> None:
        self.lookups = lookups
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
                c = node_contract(node, inputs, table_columns=table_cols or None)
            except Exception:  # a contract must never break the checker
                c = {"inputs": inputs, "outputs": {"main": sh.unknown()}, "error": sh.unknown()}
            self.shapes[name] = {"inputs": c["inputs"], "outputs": c["outputs"]}
            if any(not sh.is_unknown(s) for p, s in c["outputs"].items() if p != "error"):
                self.cov["nodes_known_output"] += 1
        for name in self.names:
            node = self.nodes[name]
            if not node.disabled:
                self._check_node(node, inputs_of[name], sources_of[name])
        self._check_branches()
        self._check_reachability()
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

        for p in desc.properties:
            if not visible(p):
                continue
            v = params.get(p.name)
            if p.required and _is_empty(v) and _is_empty(p.default) and p.type != "boolean":
                self.add("WF008", "error", f"'{p.display_name}' is required.", node=node.name, param=p.name)
            elif p.type == "options" and "model" not in p.name.lower() and p.options and isinstance(v, str) and v.strip() and "{{" not in v:
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
            if isinstance(w, str) and w.strip() and "{{" not in w and not lk.workflow_exists(w):
                self.add("WF006", "error", f"Sub-workflow '{w}' doesn't exist.", node=node.name, param="workflowId", path=w)
        cred = params.get("credentialId")
        if isinstance(cred, str) and cred.strip() and "{{" not in cred and not lk.credential_exists(cred):
            self.add("WF006", "error", f"Credential '{cred}' doesn't exist.", node=node.name, param="credentialId", path=cred)
        if node.type == "HttpRequest":
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


def _call(lookups: Any, method: str, *args: Any) -> Any:
    fn = getattr(lookups, method, None)
    return fn(*args) if fn else None


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


def check_workflow(definition: dict, *, lookups: CheckLookups) -> CheckResult:
    """Static check of a workflow definition ``{nodes, connections}``. See the module docstring."""
    if not node_registry.list():
        from ..engine.node_registry import register_all_nodes

        register_all_nodes()
    return _Checker(definition or {}, lookups).run()
