"""Stage 6 deterministic repairs (DESIGN-STAGE6-AI-BUILDER §6.6): small, certain fixes applied before any LLM repair round.

``apply_repairs(defn, ...) -> (defn, [AutoFix])`` is pure: the input is never changed, no I/O, no LLM. The repairs run in order FIX01..FIX12;
an ``AutoFix`` is ``{node, code, message, changed}``. ``changed`` is False for a repair that only reports (FIX11, an unfixable FIX01/FIX02).
The auto-fixes go into ``ops_log`` and into the next repair payload, so the model learns the right form.

Approved tests are never touched, and a node named by an approved test is never renamed (FIX05 renames later duplicates only).

FIX01  unknown node type -> closest catalog type (spec step type first; difflib cutoff 0.85, case-folded)
FIX02  unknown parameter key -> the catalog parameter it means (cutoff 0.85); no match -> dropped and reported
FIX03  option values case-folded / matched to the catalog options
FIX04  HttpRequest api_ref normalised to {spec_id, operation_id}; method and missing path_params filled from the catalog
FIX05  duplicate node names get " 2", " 3" suffixes
FIX06  connections to nodes that do not exist are removed (and exact duplicates)
FIX07  port names normalised against the node's real ports (True -> true, approve -> approved, timeout -> timed_out ...)
FIX08  expression braces / whitespace normalised ({{$json.a}} -> {{ $json.a }}); n8n "={{" prefix stripped
FIX09  $node["Old"] references rewritten after a rename in the same round (and exact case/spacing mismatches)
FIX10  one trigger + one other root with no input -> connected
FIX11  (report only) an If/Switch/Approval port is unconnected while a later spec step has no input
FIX12  a form trigger's input_schema filled from spec.trigger.fields
"""

from __future__ import annotations

import copy
import difflib
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from ..engine.graph import TRIGGER_NODE_TYPES
from ..engine.node_registry import node_registry

AutoFix = dict[str, Any]

CUTOFF = 0.85
#: parameter keys read by a node's contract although the catalog does not list them
KEEP_PARAM_KEYS = frozenset({"output_schema", "outputSchema", "input_schema", "inputSchema"})
_SKIP_EXPR_TYPES = frozenset({"Code", "PythonStep"})


def _fix(node: str | None, code: str, message: str, changed: bool = True) -> AutoFix:
    return {"node": node, "code": code, "message": message, "changed": changed}


def _fold(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(s).lower())


def _close(name: str, options: Iterable[str]) -> str | None:
    """Best match of ``name`` among ``options`` by folded text: exact fold first, then difflib at ``CUTOFF``."""
    opts = list(dict.fromkeys(o for o in options if isinstance(o, str)))
    by_fold: dict[str, str] = {}
    for o in opts:
        by_fold.setdefault(_fold(o), o)
    f = _fold(name)
    if f in by_fold:
        return by_fold[f]
    m = difflib.get_close_matches(f, list(by_fold), n=1, cutoff=CUTOFF)
    return by_fold[m[0]] if m else None


def approved_node_names(defn: Mapping[str, Any]) -> set[str]:
    """Names of the nodes that an approved (or locked) test mentions: those are never renamed."""
    names: set[str] = set()
    for t in defn.get("tests") or []:
        if not isinstance(t, Mapping) or not (t.get("status") == "approved" or t.get("locked")):
            continue
        names.update(k for k in (t.get("mocks") or {}) if isinstance(k, str))
        target = t.get("target") or {}
        names.update(str(target[k]) for k in ("node", "from", "to") if isinstance(target.get(k), str))
        names.update(str(c) for c in t.get("covers") or [] if isinstance(c, str))
        names.update(str(a["node"]) for a in t.get("expect") or [] if isinstance(a, Mapping) and isinstance(a.get("node"), str))
    return names


@dataclass
class RepairContext:
    catalog: list[dict[str, Any]]
    spec: Mapping[str, Any] | None = None
    api_ops: list[dict[str, Any]] = field(default_factory=list)          # grounding.api_operations(...)
    operations: Mapping[str, Mapping[str, Mapping[str, Any]]] | None = None  # project_operations: {spec_id: {op_id: Operation}}
    renames: Mapping[str, str] = field(default_factory=dict)             # {old: new} renamed earlier in this round
    protected: set[str] = field(default_factory=set)                     # names an approved test mentions

    def __post_init__(self) -> None:
        self.by_type = {e["type"]: e for e in self.catalog if isinstance(e, Mapping) and e.get("type")}

    def step_by_name(self, name: str) -> Mapping[str, Any] | None:
        for s in (self.spec or {}).get("steps") or []:
            if isinstance(s, Mapping) and str(s.get("name", "")).strip().lower() == str(name).strip().lower():
                return s
        return None

    def spec_types(self) -> list[str]:
        return [str(s["node_type"]) for s in (self.spec or {}).get("steps") or [] if isinstance(s, Mapping) and s.get("node_type")]


def _nodes(defn: dict[str, Any]) -> list[dict[str, Any]]:
    return [n for n in defn.get("nodes") or [] if isinstance(n, dict)]


def _ports_of(node: Mapping[str, Any]) -> list[str]:
    t = node.get("type")
    if not isinstance(t, str) or not node_registry.has(t):
        return []
    try:
        return list(node_registry.get(t).output_ports(node.get("parameters") if isinstance(node.get("parameters"), dict) else {}))
    except Exception:  # noqa: BLE001 - a port lookup must never break a repair
        return []


# ── FIX01 ───────────────────────────────────────────────────────────────────────────────────


def fix01_node_types(defn: dict[str, Any], ctx: RepairContext) -> tuple[dict[str, Any], list[AutoFix]]:
    fixes: list[AutoFix] = []
    types = list(ctx.by_type)
    for n in _nodes(defn):
        t = n.get("type")
        if not isinstance(t, str) or t in ctx.by_type:
            continue
        step = ctx.step_by_name(str(n.get("name", "")))
        choice = None
        if step and step.get("node_type") in ctx.by_type:
            choice = str(step["node_type"])
        if choice is None:
            choice = _close(t, [*ctx.spec_types(), *types])
        if choice and choice in ctx.by_type:
            n["type"] = choice
            fixes.append(_fix(n.get("name"), "FIX01", f"Unknown node type '{t}' replaced by '{choice}'."))
        else:
            fixes.append(_fix(n.get("name"), "FIX01", f"Unknown node type '{t}' and no close match in the catalog; use one of the catalog types.", False))
    return defn, fixes


# ── FIX02 / FIX03 ───────────────────────────────────────────────────────────────────────────


def _props_of(ctx: RepairContext, ntype: str) -> list[dict[str, Any]]:
    entry = ctx.by_type.get(ntype) or {}
    props = entry.get("parameters")
    return [p for p in props if isinstance(p, Mapping) and p.get("name")] if isinstance(props, list) else []


def fix02_parameter_keys(defn: dict[str, Any], ctx: RepairContext) -> tuple[dict[str, Any], list[AutoFix]]:
    fixes: list[AutoFix] = []
    for n in _nodes(defn):
        props = _props_of(ctx, str(n.get("type")))
        params = n.get("parameters")
        if not props or not isinstance(params, dict):
            continue
        names = [p["name"] for p in props]
        folds = {_fold(nm): nm for nm in names}
        for key in list(params):
            if key in names or str(key).startswith("_"):
                continue
            exact = folds.get(_fold(str(key)))  # same name written another way (output_schema -> outputSchema)
            if exact is None and key in KEEP_PARAM_KEYS:
                continue
            target = exact or _close(str(key), names)
            if target and target not in params:
                params[target] = params.pop(key)
                fixes.append(_fix(n.get("name"), "FIX02", f"Parameter '{key}' is called '{target}' on {n.get('type')}; renamed."))
            else:
                params.pop(key)
                why = f"'{target}' is already set" if target else "it is not a parameter of this node type"
                fixes.append(_fix(n.get("name"), "FIX02", f"Parameter '{key}' dropped: {why}. Parameters of {n.get('type')}: {', '.join(names)}."))
    return defn, fixes


def fix03_option_values(defn: dict[str, Any], ctx: RepairContext) -> tuple[dict[str, Any], list[AutoFix]]:
    fixes: list[AutoFix] = []
    for n in _nodes(defn):
        params = n.get("parameters")
        if not isinstance(params, dict):
            continue
        for p in _props_of(ctx, str(n.get("type"))):
            if p.get("type") != "options" or "model" in str(p["name"]).lower() or not p.get("options"):
                continue
            v = params.get(p["name"])
            if not isinstance(v, str) or not v.strip() or "{{" in v:
                continue
            values = [str(o.get("value")) for o in p["options"] if isinstance(o, Mapping)]
            if v in values:
                continue
            by_fold = {_fold(x): x for x in values}
            by_fold.update({_fold(str(o.get("name"))): str(o.get("value")) for o in p["options"] if isinstance(o, Mapping)})
            hit = by_fold.get(_fold(v))
            if hit is not None and hit != v:
                params[p["name"]] = hit
                fixes.append(_fix(n.get("name"), "FIX03", f"'{v}' is not an option of '{p['name']}'; set to '{hit}'."))
    return defn, fixes


# ── FIX04 ───────────────────────────────────────────────────────────────────────────────────

_PLACEHOLDER_RE = re.compile(r"\{([^{}/]+)\}")


def fix04_api_refs(defn: dict[str, Any], ctx: RepairContext) -> tuple[dict[str, Any], list[AutoFix]]:
    from .ai_grounding import resolve_api_ref

    fixes: list[AutoFix] = []
    for n in _nodes(defn):
        if n.get("type") != "HttpRequest" or not isinstance(n.get("parameters"), dict):
            continue
        params = n["parameters"]
        ref = params.get("api_ref")
        if ref in (None, "", {}):
            continue
        resolved = resolve_api_ref(ctx.api_ops, ref) if ctx.api_ops else (dict(ref) if isinstance(ref, Mapping) and ref.get("spec_id") and ref.get("operation_id") else None)
        if resolved is None:
            fixes.append(_fix(n.get("name"), "FIX04", f"api_ref {ref!r} is not an operation in api_operations; copy `api_ref_param` of one.", False))
            continue
        if ref != resolved:
            params["api_ref"] = resolved
            fixes.append(_fix(n.get("name"), "FIX04", f"api_ref set to {resolved} (the catalog's form)."))
        op = ((ctx.operations or {}).get(resolved["spec_id"]) or {}).get(resolved["operation_id"])
        if op is None:
            continue
        method = str(op.get("method", "")).upper()
        if method and not params.get("method"):
            params["method"] = method
            fixes.append(_fix(n.get("name"), "FIX04", f"method set to {method} from the API operation."))
        path_params = params.get("path_params")
        path_params = dict(path_params) if isinstance(path_params, Mapping) else {}
        wanted = list(op.get("path_params") or _PLACEHOLDER_RE.findall(str(op.get("path", ""))))
        added = [p for p in wanted if path_params.get(p) in (None, "")]
        for p in added:
            path_params[p] = "{{ $json.%s }}" % p
        if added:
            params["path_params"] = path_params
            fixes.append(_fix(n.get("name"), "FIX04", f"path_params {', '.join(added)} filled with `{{{{ $json.<name> }}}}`; check each reads the right field."))
    return defn, fixes


# ── FIX05 ───────────────────────────────────────────────────────────────────────────────────


def fix05_duplicate_names(defn: dict[str, Any], ctx: RepairContext) -> tuple[dict[str, Any], list[AutoFix]]:
    fixes: list[AutoFix] = []
    seen: set[str] = set()
    all_names = {str(n.get("name")) for n in _nodes(defn)}
    for n in _nodes(defn):
        name = n.get("name")
        if not isinstance(name, str):
            continue
        if name not in seen:
            seen.add(name)
            continue
        i = 2
        while f"{name} {i}" in all_names or f"{name} {i}" in ctx.protected or f"{name} {i}" in seen:
            i += 1
        new = f"{name} {i}"
        n["name"] = new
        seen.add(new)
        all_names.add(new)
        fixes.append(_fix(new, "FIX05", f"Duplicate node name '{name}': the later one is now '{new}'."))
    return defn, fixes


# ── FIX06 ───────────────────────────────────────────────────────────────────────────────────


def _ckey(c: Mapping[str, Any]) -> tuple[Any, ...]:
    return (c.get("source_node"), c.get("source_output", "main"), c.get("target_node"), c.get("target_input", "main"))


def fix06_dangling_connections(defn: dict[str, Any], ctx: RepairContext) -> tuple[dict[str, Any], list[AutoFix]]:
    fixes: list[AutoFix] = []
    names = {n.get("name") for n in _nodes(defn)}
    kept: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for c in defn.get("connections") or []:
        if not isinstance(c, dict):
            continue
        s, t = c.get("source_node"), c.get("target_node")
        if s not in names or t not in names:
            missing = [x for x in (s, t) if x not in names]
            fixes.append(_fix(None, "FIX06", f"Connection {s!r} -> {t!r} removed: node {', '.join(repr(m) for m in missing)} does not exist."))
            continue
        if _ckey(c) in seen:
            fixes.append(_fix(t, "FIX06", f"Duplicate connection {s} -> {t} removed."))
            continue
        seen.add(_ckey(c))
        kept.append(c)
    defn["connections"] = kept
    return defn, fixes


# ── FIX07 ───────────────────────────────────────────────────────────────────────────────────

_PORT_SYNONYMS = {
    "true": "true", "yes": "true", "y": "true", "then": "true", "pass": "true", "passed": "true", "ok": "true",
    "false": "false", "no": "false", "n": "false", "else": "false", "fail": "false", "failed": "false",
    "approve": "approved", "approved": "approved", "accept": "approved", "accepted": "approved", "approval": "approved",
    "reject": "rejected", "rejected": "rejected", "deny": "rejected", "denied": "rejected", "decline": "rejected", "declined": "rejected",
    "timeout": "timed_out", "timedout": "timed_out", "timed out": "timed_out", "expired": "timed_out", "timeouts": "timed_out",
    "default": "fallback", "other": "fallback", "otherwise": "fallback", "else_": "fallback",
    "each": "loop", "item": "loop", "iterate": "loop", "body": "loop",
    "finished": "done", "complete": "done", "completed": "done", "end": "done",
}
_SWITCH_PORT_RE = re.compile(r"^(?:output|out|case|branch|rule|route)?[ _-]*(\d+)$", re.I)


def normalize_port(port: Any, ports: list[str]) -> str | None:
    """The real port a model-written ``port`` means, or None when it is already right or cannot be told."""
    if not isinstance(port, str) or port in ports:
        return None
    key = port.strip().lower()
    if key in ports:
        return key
    mapped = _PORT_SYNONYMS.get(key) or _PORT_SYNONYMS.get(key.replace("_", " "))
    if mapped and mapped in ports:
        return mapped
    m = _SWITCH_PORT_RE.match(key)
    if m and f"output{int(m.group(1))}" in ports:
        return f"output{int(m.group(1))}"
    return None


def fix07_ports(defn: dict[str, Any], ctx: RepairContext) -> tuple[dict[str, Any], list[AutoFix]]:
    fixes: list[AutoFix] = []
    by_name = {n.get("name"): n for n in _nodes(defn)}
    for c in defn.get("connections") or []:
        src = by_name.get(c.get("source_node"))
        if src is None:
            continue
        ports = _ports_of(src)
        if not ports:
            continue
        new = normalize_port(c.get("source_output", "main"), ports)
        if new is not None:
            fixes.append(_fix(src.get("name"), "FIX07", f"Port '{c.get('source_output', 'main')}' of {src.get('type')} is called '{new}'; connection to '{c.get('target_node')}' fixed."))
            c["source_output"] = new
    # normalising can create exact duplicates
    seen: set[tuple[Any, ...]] = set()
    kept = []
    for c in defn.get("connections") or []:
        if _ckey(c) not in seen:
            seen.add(_ckey(c))
            kept.append(c)
    defn["connections"] = kept
    return defn, fixes


# ── FIX08 ───────────────────────────────────────────────────────────────────────────────────

_ONE_CLOSE = re.compile(r"\{\{([^{}]*?)\}(?!\})")
_WS = re.compile(r"\{\{\s*([^{}\n]*?)\s*\}\}")


def normalize_expression_text(s: str) -> str:
    """Brace / whitespace normalisation of one string (see FIX08). Strings without ``{{`` are returned unchanged."""
    if "{{" not in s:
        return s
    out = s
    if out.startswith("={{") or (out.startswith("=") and "{{" in out[1:3]):
        out = out[1:]
    out = _ONE_CLOSE.sub(lambda m: "{{" + m.group(1) + "}}", out)
    if out.count("{{") > out.count("}}") and "}" not in out[out.rfind("{{"):]:
        out = out.rstrip() + " }}"
    return _WS.sub(lambda m: "{{ " + m.group(1) + " }}", out)


def _map_strings(value: Any, fn: Callable[[str], str], counter: list[int]) -> Any:
    if isinstance(value, str):
        new = fn(value)
        if new != value:
            counter[0] += 1
        return new
    if isinstance(value, dict):
        return {k: (v if (str(k).endswith("_schema") or str(k).endswith("Schema")) else _map_strings(v, fn, counter)) for k, v in value.items()}
    if isinstance(value, list):
        return [_map_strings(v, fn, counter) for v in value]
    return value


def fix08_expression_syntax(defn: dict[str, Any], ctx: RepairContext) -> tuple[dict[str, Any], list[AutoFix]]:
    fixes: list[AutoFix] = []
    for n in _nodes(defn):
        if n.get("type") in _SKIP_EXPR_TYPES or not isinstance(n.get("parameters"), dict):
            continue
        counter = [0]
        n["parameters"] = _map_strings(n["parameters"], normalize_expression_text, counter)
        if counter[0]:
            fixes.append(_fix(n.get("name"), "FIX08", f"Normalised {counter[0]} expression string(s) to the `{{{{ expr }}}}` form."))
    return defn, fixes


# ── FIX09 ───────────────────────────────────────────────────────────────────────────────────

_NODE_REF = re.compile(r"""(\$node\[)(["'])([^"']+)\2(\])""")


def fix09_node_references(defn: dict[str, Any], ctx: RepairContext) -> tuple[dict[str, Any], list[AutoFix]]:
    from .workflow_toolkit import rewrite_refs

    fixes: list[AutoFix] = []
    names = [str(n.get("name")) for n in _nodes(defn)]
    by_fold: dict[str, list[str]] = {}
    for nm in names:
        by_fold.setdefault(re.sub(r"\s+", " ", nm.strip().lower()), []).append(nm)

    def fn(text: str) -> str:
        for old, new in ctx.renames.items():
            if old != new and old not in names:
                text = rewrite_refs(text, old, new)

        def sub(m: re.Match[str]) -> str:
            ref = m.group(3)
            if ref in names:
                return m.group(0)
            hits = by_fold.get(re.sub(r"\s+", " ", ref.strip().lower()), [])
            return f"{m.group(1)}{m.group(2)}{hits[0]}{m.group(2)}{m.group(4)}" if len(hits) == 1 else m.group(0)

        return _NODE_REF.sub(sub, text) if "$node[" in text else text

    for n in _nodes(defn):
        if not isinstance(n.get("parameters"), dict):
            continue
        counter = [0]
        n["parameters"] = _map_strings(n["parameters"], fn, counter)
        if counter[0]:
            fixes.append(_fix(n.get("name"), "FIX09", f"Fixed {counter[0]} $node[\"...\"] reference(s) to renamed or differently-cased nodes."))
    return defn, fixes


# ── FIX10 ───────────────────────────────────────────────────────────────────────────────────


def _incoming(defn: Mapping[str, Any]) -> set[str]:
    return {str(c.get("target_node")) for c in defn.get("connections") or []}


def fix10_connect_trigger(defn: dict[str, Any], ctx: RepairContext) -> tuple[dict[str, Any], list[AutoFix]]:
    nodes = [n for n in _nodes(defn) if not n.get("disabled")]
    triggers = [n for n in nodes if n.get("type") in TRIGGER_NODE_TYPES and n.get("type") != "ErrorTrigger"]
    if len(triggers) != 1:
        return defn, []
    trigger = triggers[0]
    has_out = any(c.get("source_node") == trigger.get("name") for c in defn.get("connections") or [])
    incoming = _incoming(defn)
    roots = [n for n in nodes if n.get("type") not in TRIGGER_NODE_TYPES and n.get("name") not in incoming]
    if has_out or len(roots) != 1:
        return defn, []
    root = roots[0]
    if not node_registry.has(str(root.get("type"))) or getattr(node_registry.get(str(root["type"])), "input_count", 1) < 1:
        return defn, []
    ports = _ports_of(trigger) or ["main"]
    defn.setdefault("connections", []).append({"source_node": trigger["name"], "target_node": root["name"],
                                               "source_output": ports[0], "target_input": "main"})
    return defn, [_fix(root.get("name"), "FIX10", f"'{root.get('name')}' had no input; connected the trigger '{trigger['name']}' to it.")]


# ── FIX11 (report only) ─────────────────────────────────────────────────────────────────────

_BRANCHING = ("If", "Switch", "Approval")


def fix11_unconnected_ports(defn: dict[str, Any], ctx: RepairContext) -> tuple[dict[str, Any], list[AutoFix]]:
    step_names = [str(s.get("name")) for s in (ctx.spec or {}).get("steps") or [] if isinstance(s, Mapping)]
    if not step_names:
        return defn, []
    incoming = _incoming(defn)
    nodes = {str(n.get("name")): n for n in _nodes(defn)}
    orphans = [nm for nm in step_names if nm in nodes and nm not in incoming and nodes[nm].get("type") not in TRIGGER_NODE_TYPES]
    if not orphans:
        return defn, []
    used = {(c.get("source_node"), c.get("source_output", "main")) for c in defn.get("connections") or []}
    fixes: list[AutoFix] = []
    for n in _nodes(defn):
        if n.get("type") not in _BRANCHING:
            continue
        free = [p for p in _ports_of(n) if p != "fallback" and (n.get("name"), p) not in used]
        if not free:
            continue
        # only steps listed AFTER the branching step can be waiting for one of its ports
        idx = step_names.index(str(n["name"])) if str(n.get("name")) in step_names else -1
        later = [o for o in orphans if idx < 0 or step_names.index(o) > idx]
        if later:
            fixes.append(_fix(n.get("name"), "FIX11", f"Port(s) {', '.join(free)} of '{n['name']}' are not connected while step(s) {', '.join(later)} have no input. "
                                                       "Connect the port that should lead there; nothing was changed.", False))
    return defn, fixes


# ── FIX12 ───────────────────────────────────────────────────────────────────────────────────

_FIELD_TYPES = {"string": "string", "number": "number", "integer": "integer", "boolean": "boolean", "bool": "boolean", "int": "integer",
                "float": "number", "text": "string", "email": "string", "date": "string"}


def fix12_form_schema(defn: dict[str, Any], ctx: RepairContext) -> tuple[dict[str, Any], list[AutoFix]]:
    trigger = (ctx.spec or {}).get("trigger") if isinstance((ctx.spec or {}).get("trigger"), Mapping) else None
    fields = [f for f in (trigger or {}).get("fields") or [] if isinstance(f, Mapping) and f.get("name")]
    if not trigger or trigger.get("kind") != "form" or not fields:
        return defn, []
    fixes: list[AutoFix] = []
    for n in _nodes(defn):
        if n.get("type") != "FormTrigger":
            continue
        params = n.setdefault("parameters", {})
        if not isinstance(params, dict):
            continue
        schema = params.get("input_schema")
        if isinstance(schema, Mapping) and schema.get("properties"):
            continue
        props: dict[str, Any] = {}
        required: list[str] = []
        for f in fields:
            prop: dict[str, Any] = {"type": _FIELD_TYPES.get(str(f.get("type", "string")).lower(), "string")}
            if f.get("label"):
                prop["title"] = str(f["label"])
            props[str(f["name"])] = prop
            if f.get("required"):
                required.append(str(f["name"]))
        params["input_schema"] = {"type": "object", "properties": props, "required": required}
        if not params.get("field_order"):
            params["field_order"] = [str(f["name"]) for f in fields]
        fixes.append(_fix(n.get("name"), "FIX12", f"input_schema of the form filled from the spec's trigger fields ({', '.join(props)})."))
    return defn, fixes


# ── entry point ─────────────────────────────────────────────────────────────────────────────

REPAIRS: tuple[tuple[str, Callable[[dict[str, Any], RepairContext], tuple[dict[str, Any], list[AutoFix]]]], ...] = (
    ("FIX01", fix01_node_types), ("FIX02", fix02_parameter_keys), ("FIX03", fix03_option_values), ("FIX04", fix04_api_refs),
    ("FIX05", fix05_duplicate_names), ("FIX06", fix06_dangling_connections), ("FIX07", fix07_ports), ("FIX08", fix08_expression_syntax),
    ("FIX09", fix09_node_references), ("FIX10", fix10_connect_trigger), ("FIX11", fix11_unconnected_ports), ("FIX12", fix12_form_schema),
)


def apply_repairs(defn: Mapping[str, Any], *, spec: Mapping[str, Any] | None = None, catalog: list[dict[str, Any]] | None = None,
                  api_ops: list[dict[str, Any]] | None = None, operations: Mapping[str, Mapping[str, Mapping[str, Any]]] | None = None,
                  renames: Mapping[str, str] | None = None, only: Iterable[str] | None = None) -> tuple[dict[str, Any], list[AutoFix]]:
    """Apply FIX01..FIX12 in order to a COPY of ``defn``; return ``(new_definition, auto_fixes)``.

    ``spec`` is the approved spec (step types for FIX01, trigger fields for FIX12, steps for FIX11); ``catalog`` the full node catalog
    (default: the generated one); ``api_ops`` the prompt entries of ``ai_grounding.api_operations``; ``operations`` the checker's
    ``{spec_id: {operation_id: Operation}}``; ``renames`` the ``{old: new}`` renames done earlier in the same round (FIX09);
    ``only`` limits the run to some codes. Approved tests in ``defn["tests"]`` are never modified."""
    if catalog is None:
        from .ai_grounding import full_catalog

        catalog = full_catalog()
    out = copy.deepcopy(dict(defn))
    ctx = RepairContext(catalog=catalog, spec=spec, api_ops=list(api_ops or []), operations=operations, renames=dict(renames or {}),
                        protected=approved_node_names(out))
    wanted = set(only) if only is not None else None
    fixes: list[AutoFix] = []
    for code, fn in REPAIRS:
        if wanted is not None and code not in wanted:
            continue
        out, got = fn(out, ctx)
        fixes.extend(got)
    return out, fixes
