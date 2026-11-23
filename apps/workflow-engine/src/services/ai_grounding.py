"""Stage 6 grounding (DESIGN-STAGE6-AI-BUILDER §6.3): what the model is told about the platform, and the AI-only expression checks.

Everything here is pure (no I/O, no LLM). The caller loads the data (project operations, checker result) and passes it in.

* ``catalog_subset``      the node catalog trimmed for a prompt: full detail only for the types a spec uses plus the always-full set.
* ``request_types``       the extra full-detail types: the dataset readers when the project has datasets, the report chain when asked.
* ``dataset_entries``     the project's datasets as prompt entries (``id`` is what ``datasetId`` holds).
* ``api_operations``      the project's API operations as prompt entries; every entry carries ``api_ref`` (``"refunds.issueRefund"``)
                          AND ``api_ref_param`` (``{spec_id, operation_id}``, what ``HttpRequest.parameters.api_ref`` must hold).
* ``compact_shape(s)``    shapes as ``field -> type``, two levels deep, at most 40 fields per node.
* ``expression_help``     the evaluator's helper names with signatures, read from the live evaluator (never a stale copy).
* ``check_expressions``   AIX01-AIX04: the findings the frozen checker does not give the model in a form it acts on.

AIX01  ``$node["X"]`` names a node that does not exist or is not upstream of the node that reads it.
AIX02  ``$json.f`` / ``$node["X"].json.f`` names a field the (known, closed) upstream shape does not have.
AIX03  an expression calls a function the evaluator does not have.
AIX04  an expression is unbalanced, or does not parse after the evaluator's own ``_transform_expression``.
"""

from __future__ import annotations

import ast
import difflib
import hashlib
import json
import math
import re
from collections.abc import Iterable, Mapping
from typing import Any

from ..engine import shapes as sh

# ── catalog subset ──────────────────────────────────────────────────────────────────────────

#: Types whose parameter detail is always sent (§6.3), whatever the spec uses.
ALWAYS_FULL_TYPES: tuple[str, ...] = ("If", "Switch", "Set", "Merge", "Respond", "StopAndError", "Approval")

#: ``spec.trigger.kind`` -> trigger node type (§3.1).
TRIGGER_TYPE_BY_KIND: dict[str, str] = {
    "form": "FormTrigger", "webhook": "Webhook", "cron": "Cron", "manual": "Start", "chat": "ChatInput", "execute": "ExecuteWorkflowTrigger",
}

#: Sent in full when the project has datasets: the nodes that read one (``sourceType`` "dataset" + ``datasetId``).
DATASET_TYPES: tuple[str, ...] = ("Profile", "Sample", "Aggregate")
#: Sent in full when the request asks for a report: analyse with a model, render it, mail it (a type not registered is simply absent).
REPORT_TYPES: tuple[str, ...] = ("LLMChat", "Html", "SendEmail")
_REPORT_RE = re.compile(r"(?i)\b(report|e-?mail|summar|analy[sz])")

_CATALOG_CACHE: dict[str, list[dict[str, Any]]] = {}


def request_types(request: str | None, has_datasets: bool) -> set[str]:
    """Extra full-detail types for ``catalog_subset`` from what the project has and what the request says."""
    out = set(DATASET_TYPES) if has_datasets else set()
    if _REPORT_RE.search(request or ""):
        out |= set(REPORT_TYPES)
    return out


def dataset_entries(rows: Iterable[Any], limit: int = 25, max_columns: int = 30) -> list[dict[str, Any]]:
    """Prompt entries for the project's datasets (``ProjectDatasetModel`` rows or dicts), at most ``limit``, in the order given:
    ``{id, name, row_count, column_count, columns: [{name, type}]}`` with at most ``max_columns`` columns. ``id`` is what a
    node's ``datasetId`` holds."""
    def get(r: Any, k: str) -> Any:
        return r.get(k) if isinstance(r, Mapping) else getattr(r, k, None)

    out: list[dict[str, Any]] = []
    for r in list(rows or [])[:limit]:
        cols = [{"name": c.get("name"), "type": c.get("type")} for c in (get(r, "columns") or [])[:max_columns] if isinstance(c, Mapping)]
        out.append({"id": str(get(r, "id")), "name": get(r, "name"), "row_count": get(r, "row_count"),
                    "column_count": get(r, "column_count"), "columns": cols})
    return out


def catalog_hash(catalog: list[dict[str, Any]]) -> str:
    return hashlib.sha256(json.dumps(catalog, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def full_catalog() -> list[dict[str, Any]]:
    """``node_catalog.build_node_catalog()``, built once per process (the catalog only changes with a code change)."""
    from .node_catalog import build_node_catalog

    if "full" not in _CATALOG_CACHE:
        _CATALOG_CACHE["full"] = build_node_catalog()
    return _CATALOG_CACHE["full"]


def _step_types(spec: Mapping[str, Any] | None) -> set[str]:
    types: set[str] = set()
    for step in (spec or {}).get("steps") or []:
        if isinstance(step, Mapping) and isinstance(step.get("node_type"), str):
            types.add(step["node_type"])
    kind = ((spec or {}).get("trigger") or {}).get("kind") if isinstance((spec or {}).get("trigger"), Mapping) else None
    if kind in TRIGGER_TYPE_BY_KIND:
        types.add(TRIGGER_TYPE_BY_KIND[kind])
    return types


def catalog_subset(spec: Mapping[str, Any] | None = None, catalog: list[dict[str, Any]] | None = None,
                   extra_types: Iterable[str] = ()) -> list[dict[str, Any]]:
    """Full entries for the spec's step types, its trigger type, ``extra_types`` and ``ALWAYS_FULL_TYPES``; every other type as
    ``{type, display_name, effect}`` only. Order follows the catalog."""
    catalog = full_catalog() if catalog is None else catalog
    full = _step_types(spec) | set(ALWAYS_FULL_TYPES) | set(extra_types)
    # the trigger types are always full too: the model picks one even before a spec exists
    full |= set(TRIGGER_TYPE_BY_KIND.values()) if spec is None else set()
    out: list[dict[str, Any]] = []
    for entry in catalog:
        if entry.get("type") in full:
            out.append(_prompt_entry(entry))
        else:
            out.append({"type": entry.get("type"), "display_name": entry.get("display_name"), "effect": entry.get("effect")})
    return out


def _prompt_entry(entry: Mapping[str, Any]) -> dict[str, Any]:
    """A full catalog entry for a prompt: drops what the model cannot use (the recipe checks, the paste-in example position)."""
    out = {k: entry[k] for k in ("type", "display_name", "effect", "parameters", "contract_kind") if k in entry}
    ex = entry.get("example")
    if isinstance(ex, Mapping) and ex.get("parameters"):
        out["example_parameters"] = ex["parameters"]
    return out


# ── compact shapes ──────────────────────────────────────────────────────────────────────────

MAX_FIELDS = 40


def _leaf(shape: Mapping[str, Any], optional: bool) -> str:
    t = str(shape.get("type", "unknown"))
    if isinstance(shape.get("enum"), list) and shape["enum"]:
        t += "(" + "|".join(str(v) for v in shape["enum"][:8]) + ")"
    elif shape.get("format"):
        t += f"({shape['format']})"
    return t + ("?" if optional or shape.get("nullable") else "")


def compact_shape(shape: Mapping[str, Any] | None, depth: int = 2, max_fields: int = MAX_FIELDS) -> Any:
    """``field -> type`` for an object shape (``"string?"`` marks an optional field), nested ``depth`` levels; ``[item]`` for arrays.

    An unknown or open shape is the string ``"unknown"`` / ``"object"`` so the model knows not to rely on field names."""
    if not isinstance(shape, Mapping) or sh.is_unknown(dict(shape)):
        return "unknown"
    t = shape.get("type")
    if t == "object":
        props = shape.get("properties")
        if not isinstance(props, Mapping):
            return "object"
        required = set(shape.get("required") or [])
        out: dict[str, Any] = {}
        for i, (name, sub) in enumerate(props.items()):
            if i >= max_fields:
                out["..."] = f"{len(props) - max_fields} more fields"
                break
            optional = name not in required
            if isinstance(sub, Mapping) and sub.get("type") in ("object", "array") and depth > 1:
                inner = compact_shape(sub, depth - 1, max_fields)
                out[name] = inner if isinstance(inner, (dict, list)) else _leaf(sub, optional)
            else:
                out[name] = _leaf(sub, optional) if isinstance(sub, Mapping) else "unknown"
        return out
    if t == "array":
        items = shape.get("items")
        inner = compact_shape(items, depth - 1, max_fields) if isinstance(items, Mapping) and depth > 1 else (
            _leaf(items, False) if isinstance(items, Mapping) else "unknown")
        return [inner]
    return _leaf(shape, False)


def compact_shapes(shapes_by_node: Mapping[str, Any] | None, depth: int = 2, max_fields: int = MAX_FIELDS) -> dict[str, Any]:
    """``{node: compact shape of what the node emits}`` from the checker's ``shapes_by_node`` (first non-error output port)."""
    out: dict[str, Any] = {}
    for name, entry in (shapes_by_node or {}).items():
        outputs = (entry or {}).get("outputs") or {}
        port = "main" if "main" in outputs else next((p for p in outputs if p != "error"), None)
        out[name] = compact_shape(outputs.get(port), depth, max_fields) if port else "unknown"
    return out


# ── API operations ──────────────────────────────────────────────────────────────────────────


def api_prefix(name: str | None, fallback: str = "api") -> str:
    """``"Refunds API"`` -> ``"refunds_api"``: the part of ``api_ref`` before the dot."""
    s = re.sub(r"[^a-z0-9]+", "_", str(name or "").strip().lower()).strip("_")
    return s or fallback


def _spec_meta(specs: Any) -> dict[str, dict[str, Any]]:
    """``{spec_id: {name, base_url}}`` from a dict of those or a list of ApiCatalog summaries ``[{id, name, base_url}]``."""
    if isinstance(specs, Mapping):
        return {str(k): dict(v) if isinstance(v, Mapping) else {"name": str(v)} for k, v in specs.items()}
    out: dict[str, dict[str, Any]] = {}
    for s in specs or []:
        if isinstance(s, Mapping) and s.get("id"):
            out[str(s["id"])] = {"name": s.get("name"), "base_url": s.get("base_url")}
    return out


def _first_2xx(op: Mapping[str, Any]) -> tuple[int, Mapping[str, Any]] | None:
    entries = sorted(((int(k), v) for k, v in (op.get("responses") or {}).items()
                      if isinstance(k, str) and k.isdigit() and isinstance(v, Mapping)), key=lambda e: e[0])
    return next(((s, r) for s, r in entries if 200 <= s < 300), None)


def api_operations(project_ops: Mapping[str, Mapping[str, Mapping[str, Any]]] | None, specs: Any = None) -> list[dict[str, Any]]:
    """Prompt entries for ``project_operations(project_id)`` (``{spec_id: {operation_id: Operation}}``).

    ``specs`` gives each spec's name (``{spec_id: {name, base_url}}`` or a list of summaries ``[{id, name, ...}]``); the name
    makes the ``api_ref`` readable (``refunds.issueRefund``), without it the spec id is the prefix. Entry::

        {api_ref, api_ref_param: {spec_id, operation_id}, api: <name>, method, path, untrusted_summary, path_params,
         request_shape, response_shape, response_status, error_statuses}

    ``untrusted_summary`` comes from an uploaded OpenAPI document, so it is data and never instructions (§10.5).
    Specs are listed in id order, operations in id order, so the prompt hash is stable.
    """
    meta = _spec_meta(specs)
    used: dict[str, str] = {}
    out: list[dict[str, Any]] = []
    for spec_id in sorted(project_ops or {}):
        name = (meta.get(spec_id) or {}).get("name")
        prefix = api_prefix(name, fallback=api_prefix(spec_id))
        if used.get(prefix, spec_id) != spec_id:  # two APIs with one name: keep the refs unique
            prefix = f"{prefix}_{api_prefix(spec_id)}"
        used[prefix] = spec_id
        for op_id in sorted(project_ops[spec_id]):
            op = project_ops[spec_id][op_id]
            ok = _first_2xx(op)
            out.append({
                "api_ref": f"{prefix}.{op_id}",
                "api_ref_param": {"spec_id": spec_id, "operation_id": op_id},
                "api": name or spec_id,
                "method": str(op.get("method", "")).upper(),
                "path": op.get("path"),
                "untrusted_summary": op.get("summary") or "",
                "path_params": list(op.get("path_params") or []),
                "request_shape": compact_shape(op["request_shape"], 3) if isinstance(op.get("request_shape"), Mapping) else None,
                "response_shape": compact_shape(ok[1].get("shape"), 3) if ok and isinstance(ok[1].get("shape"), Mapping) else None,
                "response_status": ok[0] if ok else None,
                "error_statuses": sorted(int(k) for k in (op.get("responses") or {}) if isinstance(k, str) and k.isdigit() and int(k) >= 400),
            })
    return out


def api_ref_index(api_ops: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, str]]:
    """``{api_ref string: {spec_id, operation_id}}`` for validation and repair."""
    return {str(o["api_ref"]): dict(o["api_ref_param"]) for o in api_ops if o.get("api_ref") and isinstance(o.get("api_ref_param"), Mapping)}


def resolve_api_ref(api_ops: Iterable[Mapping[str, Any]], ref: Any) -> dict[str, str] | None:
    """The ``{spec_id, operation_id}`` a model-written reference means, or None. Accepts the ``api_ref`` string (case-insensitive),
    a ``{spec_id, operation_id}`` dict that exists, or a bare ``operation_id`` that is unique across the APIs."""
    ops = list(api_ops)
    index = api_ref_index(ops)
    if isinstance(ref, Mapping):
        sid, oid = ref.get("spec_id"), ref.get("operation_id")
        if any(p["spec_id"] == sid and p["operation_id"] == oid for p in index.values()):
            return {"spec_id": sid, "operation_id": oid}
        if oid and not sid:
            ref = str(oid)
        else:
            return None
    if not isinstance(ref, str) or not ref.strip():
        return None
    ref = ref.strip()
    if ref in index:
        return dict(index[ref])
    folded = {k.lower(): v for k, v in index.items()}
    if ref.lower() in folded:
        return dict(folded[ref.lower()])
    hits = [p for p in index.values() if p["operation_id"].lower() == ref.lower()]
    return dict(hits[0]) if len(hits) == 1 else None


# ── expression help ─────────────────────────────────────────────────────────────────────────


def evaluator_function_names() -> set[str]:
    """The function names the live evaluator accepts inside ``{{ }}``."""
    from ..engine.expression_engine import expression_engine

    return set(expression_engine.evaluator.functions)


def expression_help() -> dict[str, Any]:
    """Helper names with signatures, taken from the live evaluator (shared with the prompts so there is one list)."""
    from .ai_builder_prompts import default_expression_help

    return default_expression_help()


def expression_help_drift() -> tuple[set[str], set[str]]:
    """``(missing, extra)``: evaluator functions the help does not list, and listed names the evaluator does not have.
    Both must be empty; a unit test pins it."""
    listed = set((expression_help().get("functions") or {}))
    live = evaluator_function_names()
    return live - listed, listed - live


# ── AIX01-AIX04 ─────────────────────────────────────────────────────────────────────────────

_EXPR_RE = re.compile(r"\{\{(.+?)\}\}")  # the runtime's own pattern
_NODE_FIELD_RE = re.compile(r"""\$node\[["']([^"']+)["']\]\.json((?:\.\w+)*)(\()?""")
_NODE_REF_RE = re.compile(r"""\$node\[["']([^"']+)["']\]""")
_NODE_DOT_RE = re.compile(r"\$node\.([A-Za-z_]\w*)")
_JSON_FIELD_RE = re.compile(r"\$json((?:\.\w+)+)(\()?")
_SKIP_TYPES = {"Code", "PythonStep"}


def _walk_strings(value: Any, path: str = "") -> Iterable[tuple[str, str]]:
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


def _ancestors(name: str, parents: dict[str, set[str]]) -> set[str]:
    seen: set[str] = set()
    stack = list(parents.get(name, ()))
    while stack:
        n = stack.pop()
        if n not in seen:
            seen.add(n)
            stack.extend(parents.get(n, ()))
    return seen


def _suggest(name: str, options: Iterable[str]) -> str:
    opts = [o for o in options if isinstance(o, str)]
    lower = {o.lower(): o for o in opts}
    if name.lower() in lower:
        return lower[name.lower()]
    m = difflib.get_close_matches(name, opts, n=1, cutoff=0.6)
    return m[0] if m else ""


def _hint(candidate: str) -> str:
    return f" Did you mean '{candidate}'?" if candidate else ""


def _item_shape(entry: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(entry, Mapping):
        return None
    inputs = entry.get("inputs") or {}
    if not inputs:
        return None
    return sh.merge_all(inputs.values()) if len(inputs) > 1 else next(iter(inputs.values()))


def _main_output(entry: Mapping[str, Any] | None) -> dict[str, Any] | None:
    outputs = (entry or {}).get("outputs") or {}
    if "main" in outputs:
        return outputs["main"]
    port = next((p for p in outputs if p != "error"), None)
    return outputs.get(port) if port else None


def check_expression(expression: str, *, node: str, param: str, node_names: set[str], upstream: set[str],
                     shapes_by_node: Mapping[str, Any] | None, item_shape: dict[str, Any] | None,
                     functions: set[str] | None = None) -> list[dict[str, Any]]:
    """AIX findings for ONE expression body (the text between ``{{`` and ``}}``)."""
    from ..engine.expression_engine import expression_engine

    functions = evaluator_function_names() if functions is None else functions
    where = f"{param}: " if param else ""
    out: list[dict[str, Any]] = []

    def add(code: str, message: str) -> None:
        out.append({"code": code, "node": node, "param": param, "message": where + message})

    # AIX04: parse like the runtime does
    try:
        transformed = expression_engine._transform_expression(expression.strip())
        tree = ast.parse(transformed.strip(), mode="eval")
    except SyntaxError as e:
        add("AIX04", f"'{expression.strip()}' does not parse ({e.msg}). Use Python-style syntax: and / or / not, length(x) instead of .length, no && || ===.")
        return out
    except Exception as e:  # noqa: BLE001 - a transform failure is the same finding
        add("AIX04", f"'{expression.strip()}' could not be read ({type(e).__name__}).")
        return out

    # AIX03: unknown helper
    seen_fn: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name):
            fn = n.func.id
            if fn not in functions and fn not in seen_fn:
                seen_fn.add(fn)
                add("AIX03", f"'{fn}(...)' is not an expression function." + _hint(_suggest(fn, functions)))

    # AIX01: $node["X"] must exist and be upstream
    referenced: list[str] = [m.group(1) for m in _NODE_REF_RE.finditer(expression)] + [m.group(1) for m in _NODE_DOT_RE.finditer(expression)]
    bad_nodes: set[str] = set()
    for ref in dict.fromkeys(referenced):
        if ref not in node_names:
            bad_nodes.add(ref)
            add("AIX01", f"$node[\"{ref}\"] is not a node of this workflow." + _hint(_suggest(ref, node_names - {node})))
        elif ref == node or ref not in upstream:
            bad_nodes.add(ref)
            add("AIX01", f"$node[\"{ref}\"] is not upstream of '{node}', so it has not run yet when this expression is evaluated.")

    # AIX02: fields that the upstream shape (when known and closed) does not have
    def check_path(shape: dict[str, Any] | None, segs: list[str], label: str, node_label: str = "") -> None:
        if not segs or shape is None or sh.is_unknown(shape):
            return
        found = sh.lookup(shape, segs)
        if found is not None:
            return
        # find the first missing segment for the message
        for i in range(len(segs)):
            if sh.lookup(shape, segs[: i + 1]) is None:
                parent = sh.lookup(shape, segs[:i]) or {}
                options = list((parent.get("properties") or {}).keys()) if isinstance(parent, dict) else []
                shown = ".".join(segs[: i + 1])
                source = f"the data from '{node_label}'" if node_label else "the incoming item"
                add("AIX02", f"{label}.{shown} is not a field of {source}."
                    + _hint(_suggest(segs[i], options)) + (f" Fields: {', '.join(options[:12])}." if options else ""))
                return

    masked = expression
    for m in _NODE_FIELD_RE.finditer(expression):
        ref, tail, call = m.group(1), m.group(2), m.group(3)
        if ref in bad_nodes or ref not in node_names:
            continue
        segs = [s for s in tail.split(".") if s]
        if call and segs:
            segs = segs[:-1]  # the last segment is a method call, not a field
        check_path(_main_output((shapes_by_node or {}).get(ref)), segs, f'$node["{ref}"].json', ref)
    masked = _NODE_FIELD_RE.sub(" ", masked)
    for m in _JSON_FIELD_RE.finditer(masked):
        segs = [s for s in m.group(1).split(".") if s]
        if m.group(2) and segs:
            segs = segs[:-1]
        check_path(item_shape, segs, "$json")
    return out


def check_expressions(defn: Mapping[str, Any], shapes_by_node: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    """AIX01-AIX04 over every ``{{ }}`` in every node's parameters (Code / PythonStep bodies and ``*_schema`` values are skipped).

    ``shapes_by_node`` is the checker's ``result["shapes_by_node"]``; without it only AIX01, AIX03 and AIX04 run.
    Returns ``[{code, node, param, message}]`` in node order."""
    nodes = [n for n in (defn or {}).get("nodes") or [] if isinstance(n, Mapping) and n.get("name")]
    names = {str(n["name"]) for n in nodes}
    parents: dict[str, set[str]] = {n: set() for n in names}
    for c in (defn or {}).get("connections") or []:
        if not isinstance(c, Mapping):
            continue
        s, t = c.get("source_node"), c.get("target_node")
        if s in names and t in names:
            parents[t].add(s)
    functions = evaluator_function_names()
    findings: list[dict[str, Any]] = []
    for n in nodes:
        name = str(n["name"])
        if n.get("disabled") or n.get("type") in _SKIP_TYPES:
            continue
        upstream = _ancestors(name, parents)
        item = _item_shape((shapes_by_node or {}).get(name))
        for path, text in _walk_strings(n.get("parameters") or {}):
            if "{{" not in text and "}}" not in text:
                continue
            if _is_schema_key(path):
                continue
            if text.count("{{") != text.count("}}"):
                findings.append({"code": "AIX04", "node": name, "param": path,
                                 "message": f"{path}: unbalanced braces in '{text[:80]}'. Every {{{{ needs a matching }}}}."})
                continue
            for m in _EXPR_RE.finditer(text):
                findings.extend(check_expression(m.group(1), node=name, param=path, node_names=names, upstream=upstream,
                                                 shapes_by_node=shapes_by_node, item_shape=item, functions=functions))
    return findings


# ═══════════════════════════════════════════════════════════════════════════════════════════
# Stage 8 (DESIGN-STAGE8-REUSE section 9): templates as a starting point, and callable sub-workflows
# ═══════════════════════════════════════════════════════════════════════════════════════════
#
# Both are pure. The caller loads the catalog / the project's sub-workflows and passes them in.

#: A template is suggested only at or above this score (section 9.4 asks for a threshold; the doc's 0.8 was for a model's judgement,
#: this is a lexical score, calibrated on the 15 eval requests so that a request that merely shares generic words ("form", "email",
#: "customer") with a template never gets a suggestion).
TEMPLATE_MATCH_THRESHOLD = 0.35

#: At most this many callable sub-workflows go into a prompt (section 9.1).
MAX_SUBWORKFLOWS = 20
_MAX_SUBWORKFLOW_FIELDS = 15

_MATCH_STOP = frozenset(
    "the and for with from that this their them then than into out are was were has have had not but any all can will would should must over "
    "under upon each every when what which who whom how why also just only some more most other such use used using via per our your you its "
    "his her they new one two get got let set make made need needs want wants like end sub workflow workflows pre".split())


#: Words that name the bank's everyday objects rather than what a template does; they never make a template's subject.
_GENERIC_SUBJECT = frozenset({"customer", "account", "payment", "client", "user", "bank"})


def _stem(word: str) -> str:
    if word.endswith("ies") and len(word) > 4:
        word = word[:-3] + "y"
    elif word.endswith("s") and not word.endswith("ss") and len(word) > 3:
        word = word[:-1]
    if word.endswith("ing") and len(word) > 5:
        word = word[:-3]
    elif word.endswith("ed") and len(word) > 4:
        word = word[:-2]
    if word.endswith("e") and len(word) > 3:
        word = word[:-1]
    return word


def match_terms(text: str | None) -> list[str]:
    """Lower-cased word stems of ``text`` without stop words and words shorter than 3 characters (order kept, duplicates kept)."""
    return [_stem(w) for w in re.findall(r"[a-z0-9]+", str(text or "").lower()) if len(w) >= 3 and w not in _MATCH_STOP]


def _template_terms(bundle: Any) -> tuple[dict[str, int], dict[str, int], set[str], dict[str, str]]:
    """``(core, text, primary, shown)``: core terms (tags 3, name and slug 2), text terms (summary and few-shot request, 1), the primary
    terms (first tag, slug parts) and the original word that each stem came from."""
    core: dict[str, int] = {}
    text: dict[str, int] = {}
    shown: dict[str, str] = {}

    def add(target: dict[str, int], source: str | None, weight: int) -> list[str]:
        out: list[str] = []
        for w in re.findall(r"[a-z0-9]+", str(source or "").lower()):
            if len(w) < 3 or w in _MATCH_STOP:
                continue
            s = _stem(w)
            target[s] = max(target.get(s, 0), weight)
            shown.setdefault(s, w)
            out.append(s)
        return out

    tags = list(getattr(bundle, "tags", None) or [])
    primary = set(add(core, tags[0], 3)) if tags else set()
    for tag in tags[1:]:
        add(core, tag, 3)
    add(core, getattr(bundle, "name", ""), 2)
    primary |= set(add(core, getattr(bundle, "slug", ""), 2))
    summary: dict[str, int] = {}
    add(summary, getattr(bundle, "summary", ""), 1)
    few = getattr(bundle, "few_shot", None)
    add(summary, getattr(few, "request", "") if few is not None else "", 1)
    text = {k: v for k, v in summary.items() if k not in core}
    return core, text, primary, shown


def match_template(request: str, catalog: Mapping[str, Any], threshold: float = TEMPLATE_MATCH_THRESHOLD) -> dict[str, Any] | None:
    """The one catalog template that fits ``request`` best, or None. Deterministic: same request and catalog, same answer.

    ``catalog`` is ``{slug: LoadedTemplate}`` (anything with a ``.bundle`` of slug, name, summary, tags, version, few_shot).

    Score (0 to 1) = 0.75 * core coverage + 0.25 * text coverage. Coverage is the share of a template's term weight that the request
    contains, weighted by how rare the term is across the catalog (idf), so "refund" counts for more than "form". Core terms are the tags
    and the name; text terms are the summary and the few-shot request. A template is only suggested when its score reaches
    ``threshold``, at least two terms matched, and at least one *primary* term matched (its first tag or a word of its slug that at most one other template uses, i.e. what
    it is about; "customer" or "form" never count); a tie goes to the lower slug. This never installs anything: it names a template for a person to accept or decline.
    """
    req = set(match_terms(request))
    if not req or not catalog:
        return None
    parsed = {slug: _template_terms(lt.bundle) for slug, lt in catalog.items()}
    n = len(parsed)
    df: dict[str, int] = {}
    for core, text, _, _ in parsed.values():
        for t in set(core) | set(text):
            df[t] = df.get(t, 0) + 1

    def idf(t: str) -> float:
        return math.log(1 + n / df.get(t, 1))

    best: tuple[float, str] | None = None
    best_info: tuple[list[str], dict[str, str]] = ([], {})
    for slug in sorted(parsed):
        core, text, primary, shown = parsed[slug]
        core_total = sum(w * idf(t) for t, w in core.items())
        text_total = sum(w * idf(t) for t, w in text.items())
        core_hit = [t for t in core if t in req]
        text_hit = [t for t in text if t in req]
        if len(core_hit) + len(text_hit) < 2 or not {t for t in primary if df.get(t, 0) <= 2 and t not in _GENERIC_SUBJECT} & set(core_hit):
            continue
        core_cov = sum(core[t] * idf(t) for t in core_hit) / core_total if core_total else 0.0
        text_cov = sum(text[t] * idf(t) for t in text_hit) / text_total if text_total else 0.0
        score = round(0.75 * core_cov + 0.25 * text_cov, 3)
        if score >= threshold and (best is None or score > best[0]):
            best, best_info = (score, slug), (core_hit + text_hit, shown)
    if best is None:
        return None
    score, slug = best
    bundle = catalog[slug].bundle
    words = [best_info[1].get(t, t) for t in best_info[0]][:5]
    return {
        "kind": "template", "slug": slug, "name": bundle.name, "version": bundle.version, "summary": bundle.summary, "score": score,
        "reason": f"Your request mentions {', '.join(words)}, which is what the '{bundle.name}' template does: {bundle.summary}",
    }


# ── callable sub-workflows ──────────────────────────────────────────────────────────────────


def subworkflow_entries(rows: Iterable[Mapping[str, Any]], request: str = "", *, exclude: Iterable[str] = (), keep: Iterable[str] = (),
                        limit: int = MAX_SUBWORKFLOWS) -> list[dict[str, Any]]:
    """Prompt entries for the project's sub-workflows (``SubworkflowApi.list_for_project`` rows): only callable ones that have a published
    version (a call pins ``latest``, and an unpublished workflow has none), never ``exclude`` (the workflow being edited: a call to
    itself is a cycle), at most ``limit``, most similar to ``request`` first (word overlap on name and description; ties by name, then id).
    Ids in ``keep`` (calls the workflow being modified already makes) are listed first whatever their score.

    Entry: ``{id, name, untrusted_description, published_version, inputs: [{name, type, required}], output_fields: [name]}``."""
    skip = {str(x) for x in exclude}
    keep_ids = {str(x) for x in keep}
    want = set(match_terms(request))
    scored: list[tuple[int, int, str, str, dict[str, Any]]] = []
    for row in rows or []:
        wid = str(row.get("id") or "")
        if not wid or wid in skip or not row.get("callable") or row.get("published_version") is None:
            continue
        name = str(row.get("name") or "")
        desc = str(row.get("description") or "")
        overlap = len(want & set(match_terms(f"{name} {desc}")))
        entry = {
            "id": wid, "name": name, "untrusted_description": desc[:160], "published_version": row.get("published_version"),
            "inputs": [{"name": f.get("name"), "type": f.get("type", "any"), "required": bool(f.get("required"))}
                       for f in (row.get("inputs") or [])[:_MAX_SUBWORKFLOW_FIELDS] if isinstance(f, Mapping)],
            "output_fields": [str(f) for f in (row.get("output_fields") or [])[:_MAX_SUBWORKFLOW_FIELDS]],
        }
        scored.append((0 if wid in keep_ids else 1, -overlap, name, wid, entry))
    scored.sort(key=lambda s: s[:4])
    return [s[4] for s in scored[:limit]]
