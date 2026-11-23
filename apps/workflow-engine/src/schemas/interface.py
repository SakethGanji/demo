"""Interface + ScreenSpec schemas and the pure rules around them (Stage 5, DESIGN-STAGE5-UIS sections 3.1, 6.3, 7).

* ``Interface`` is the ``interface`` key of a workflow definition (kind form | chat | custom).
* ``ScreenSpec`` v1 is a declarative JSON description of a custom screen: a closed set of component types and of
  binding roots (``input.*``, ``node.<Node>.<path>``, ``task.*``, ``result.*``, ``run.*``). There is no expression
  engine: a binding is a plain path lookup, so a spec can never execute anything.
* Everything below the models is pure (no I/O): structural validation, binding findings (the checker's WF017-WF019),
  the "Wired to" chips, deterministic screen generation and the list of ``node.*`` paths a task screen needs.
"""

from __future__ import annotations

import json
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError as PydanticValidationError

from ..engine import form_fields as ff
from ..engine import shapes as sh
from ..engine.graph import ancestors

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")
KINDS = ("form", "chat", "custom")
ACCESS_MODES = ("public", "studio")
COMPONENT_TYPES = (
    "heading", "text", "field", "amount", "status", "callout", "list", "table", "link", "divider", "form",
    "actions", "result", "input_form",
)
BIND_ROOTS = ("input", "node", "task", "result", "run")
TASK_FIELDS = ("title", "status", "due_at", "assignees", "context", "form")
RUN_FIELDS = ("id", "status", "started_at")
MAX_SPEC_BYTES = 128 * 1024
MAX_COMPONENTS = 200
TRIGGER_FOR_KIND = {"form": "FormTrigger", "custom": "FormTrigger", "chat": "ChatInput"}
#: nodes whose output can hold many items (WF019 / WF014 best effort)
MANY_ITEM_TYPES = frozenset({"Loop", "ItemLists", "Filter", "DataTable", "Postgres", "MongoDB", "Neo4j", "Poll"})

_SCREEN_ID_RE = re.compile(r"^(start|result|task:.{1,200})$")
_TEMPLATE_RE = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")
_TEXT = Field(None, max_length=2000)


# ── models ───────────────────────────────────────────────────────────────────────────────────


class ScreenComponent(BaseModel):
    """One component of a screen. Which keys matter depends on ``type`` (see section 6.3); unknown keys are refused."""

    model_config = ConfigDict(extra="forbid")

    type: Literal[
        "heading", "text", "field", "amount", "status", "callout", "list", "table", "link", "divider", "form",
        "actions", "result", "input_form",
    ]
    text: str | None = _TEXT
    label: str | None = Field(None, max_length=200)
    bind: str | None = Field(None, max_length=400)
    value: str | int | float | bool | None = None
    template: str | None = _TEXT
    currency: str | None = Field(None, max_length=8)
    size: Literal["sm", "md", "lg", "xl"] | None = None
    tone: Literal["neutral", "ai", "info", "success", "warn", "danger"] | None = None
    due: str | None = Field(None, max_length=400)
    item: str | None = _TEXT
    columns: list[str] | None = Field(None, max_length=30)
    approve_label: str | None = Field(None, max_length=60)
    reject_label: str | None = Field(None, max_length=60)
    help: str | None = Field(None, max_length=500)


class ScreenSpec(BaseModel):
    """ScreenSpec v1 (frozen)."""

    model_config = ConfigDict(extra="forbid")

    v: Literal[1] = 1
    title: str = Field("", max_length=200)
    layout: Literal["card", "page"] = "card"
    components: list[ScreenComponent] = Field(default_factory=list)


class InterfaceAccess(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["public", "studio"] = "public"
    public_tasks: bool = False
    hide_progress: bool = False


class Interface(BaseModel):
    """The ``interface`` key of a workflow definition."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["form", "chat", "custom"]
    slug: str
    title: str = Field("", max_length=200)
    description: str = Field("", max_length=2000)
    trigger_node: str | None = Field(None, max_length=255)
    result_node: str | None = Field(None, max_length=255)
    screens: dict[str, ScreenSpec] = Field(default_factory=dict)
    app_id: str | None = Field(None, max_length=100)
    access: InterfaceAccess = Field(default_factory=InterfaceAccess)


class SubmitRequest(BaseModel):
    """Body of the public / preview submit routes (strict)."""

    model_config = ConfigDict(extra="forbid")

    fields: dict[str, Any]
    idempotency_key: str | None = Field(None, max_length=128)   # fallback when a proxy strips the header (R12)


class ChatSessionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_label: str | None = Field(None, max_length=200)


class ChatSendRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(..., min_length=1)
    client_message_id: str = Field(..., min_length=1, max_length=128)


class GenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["form", "chat", "custom"]
    slug: str | None = None


class AiEditRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instruction: str = Field(..., min_length=1, max_length=2000)
    spec: dict[str, Any]


def screen_spec_json_schema() -> dict[str, Any]:
    """The JSON schema of a ScreenSpec (the AI prompt and the studio use it)."""
    return ScreenSpec.model_json_schema()


# ── structural validation ────────────────────────────────────────────────────────────────────


def _loc(loc: tuple[Any, ...], prefix: str = "") -> str:
    out = prefix
    for part in loc:
        out += f"[{part}]" if isinstance(part, int) else (f".{part}" if out else str(part))
    return out


def _errors_of(e: PydanticValidationError, prefix: str = "") -> list[dict[str, str]]:
    return [{"field": _loc(tuple(x["loc"]), prefix), "message": x["msg"]} for x in e.errors()]


def spec_size_errors(spec: Any, prefix: str = "") -> list[dict[str, str]]:
    """``SCREEN_TOO_LARGE`` conditions: more than 200 components or more than 128 KB of JSON."""
    out: list[dict[str, str]] = []
    try:
        size = len(json.dumps(spec, ensure_ascii=False).encode("utf-8"))
    except (TypeError, ValueError):
        return [{"field": prefix, "message": "screen is not JSON"}]
    comps = spec.get("components") if isinstance(spec, dict) else None
    if size > MAX_SPEC_BYTES:
        out.append({"field": prefix, "message": f"SCREEN_TOO_LARGE: {size} bytes (limit {MAX_SPEC_BYTES})"})
    if isinstance(comps, list) and len(comps) > MAX_COMPONENTS:
        out.append({"field": prefix, "message": f"SCREEN_TOO_LARGE: {len(comps)} components (limit {MAX_COMPONENTS})"})
    return out


def parse_screen_spec(raw: Any, prefix: str = "") -> tuple[dict[str, Any] | None, list[dict[str, str]]]:
    """Validate one spec. Returns ``(clean_dict, errors)``; ``clean_dict`` is None when there are errors."""
    if not isinstance(raw, dict):
        return None, [{"field": prefix, "message": "a screen must be an object"}]
    errors = spec_size_errors(raw, prefix)
    if errors:
        return None, errors
    try:
        spec = ScreenSpec.model_validate(raw)
    except PydanticValidationError as e:
        return None, _errors_of(e, prefix)
    errors = []
    for i, c in enumerate(spec.components):
        where = f"{prefix}.components[{i}]" if prefix else f"components[{i}]"
        if c.type == "link" and c.value is not None and not (isinstance(c.value, str) and c.value.lower().startswith("https://")):
            errors.append({"field": f"{where}.value", "message": "links must be https:// URLs"})
        for key in ("bind", "due"):
            b = getattr(c, key)
            if b and not _bind_shape_ok(b):
                errors.append({"field": f"{where}.{key}", "message": f"'{b}' is not a valid binding (roots: {', '.join(BIND_ROOTS)})"})
        for key in ("template", "item", "text"):
            for tok in _TEMPLATE_RE.findall(getattr(c, key) or ""):
                if not re.fullmatch(r"[A-Za-z_$][\w .:\-]*", tok):
                    errors.append({"field": f"{where}.{key}", "message": f"'{{{{{tok}}}}}' is not a plain path"})
    if errors:
        return None, errors
    return spec.model_dump(exclude_none=True), []


def _bind_shape_ok(bind: str) -> bool:
    """A binding is ``<root>`` or ``<root>.<path>`` with a root from the closed set; ``task.<x>`` / ``run.<x>`` are closed too."""
    root, _, rest = bind.partition(".")
    if root not in BIND_ROOTS:
        return False
    if root == "task":
        return rest == "" or rest.split(".", 1)[0] in TASK_FIELDS
    if root == "run":
        return rest == "" or rest in RUN_FIELDS
    if root == "node":
        return bool(rest)
    return True


def parse_interface(raw: Any) -> tuple[dict[str, Any] | None, list[dict[str, str]]]:
    """Validate an ``interface`` object. Returns ``(clean_dict, errors)`` (``errors`` map to 422 INTERFACE_INVALID fields)."""
    if not isinstance(raw, dict):
        return None, [{"field": "", "message": "interface must be an object"}]
    errors: list[dict[str, str]] = []
    slug = raw.get("slug")
    if not isinstance(slug, str) or not SLUG_RE.match(slug):
        errors.append({"field": "slug", "message": "use 2-63 lowercase letters, digits or dashes, starting with a letter or digit"})
    screens = raw.get("screens")
    if isinstance(screens, dict):
        for sid, spec in screens.items():
            if not isinstance(sid, str) or not _SCREEN_ID_RE.match(sid):
                errors.append({"field": f"screens.{sid}", "message": "screen ids are 'start', 'result' or 'task:<node name>'"})
                continue
            _, spec_errors = parse_screen_spec(spec, f"screens.{sid}")
            errors.extend(spec_errors)
    elif screens is not None:
        errors.append({"field": "screens", "message": "screens must be an object"})
    if errors:
        return None, errors
    try:
        model = Interface.model_validate(raw)
    except PydanticValidationError as e:
        return None, _errors_of(e)
    out = model.model_dump(exclude_none=True)
    out["screens"] = {sid: parse_screen_spec(spec)[0] for sid, spec in (screens or {}).items()}
    return out, []


# ── definition helpers ───────────────────────────────────────────────────────────────────────


def _nodes(definition: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {n["name"]: n for n in definition.get("nodes") or [] if isinstance(n, dict) and n.get("name")}


def trigger_input_schema(definition: dict[str, Any], interface: dict[str, Any]) -> dict[str, Any] | None:
    """The Shape of the interface's trigger input, or None when the trigger can't be found."""
    node = _nodes(definition).get(interface.get("trigger_node") or "")
    if not node or node.get("type") not in TRIGGER_FOR_KIND.values():
        return None
    params = node.get("parameters") or {}
    raw = params.get("input_schema") or params.get("inputSchema")
    return sh.normalize(raw, "declared") if raw else sh.object_of({}, [], "declared")


def find_trigger(definition: dict[str, Any], kind: str) -> str | None:
    want = TRIGGER_FOR_KIND.get(kind, "FormTrigger")
    return next((n["name"] for n in definition.get("nodes") or [] if n.get("type") == want and not n.get("disabled")), None)


def _connections(definition: dict[str, Any]) -> list[Any]:
    from types import SimpleNamespace

    return [SimpleNamespace(**{k: c.get(k) for k in ("source_node", "target_node", "source_output")})
            for c in definition.get("connections") or [] if isinstance(c, dict)]


def split_node_bind(bind: str, names: list[str]) -> tuple[str, str] | None:
    """``node.<Node name>.<path>`` -> ``(node name, path)``; node names may contain spaces and dots (longest match wins)."""
    if not bind.startswith("node."):
        return None
    rest = bind[5:]
    for n in sorted(names, key=len, reverse=True):
        if rest == n:
            return n, ""
        if rest.startswith(n + "."):
            return n, rest[len(n) + 1:]
    return None


def component_binds(spec: dict[str, Any]) -> list[str]:
    """Every binding a screen reads: ``bind`` / ``due`` of each component plus the roots used inside templates."""
    out: list[str] = []
    for c in spec.get("components") or []:
        if not isinstance(c, dict):
            continue
        for key in ("bind", "due"):
            if isinstance(c.get(key), str) and c[key]:
                out.append(c[key])
        for key in ("template", "item", "text"):
            for tok in _TEMPLATE_RE.findall(c.get(key) or ""):
                if tok.split(".", 1)[0] in BIND_ROOTS:
                    out.append(tok)
    return out


def task_screen_bindings(interface: dict[str, Any] | None, node_name: str) -> list[str]:
    """The ``node.<Node>.<path>`` bindings of the ``task:<node_name>`` screen (without the ``node.`` prefix).

    The Approval node captures exactly these (redacted) at suspend time into ``task.context._bindings``, so a public
    task page never reads node outputs (section 6.3, data exposure rule)."""
    spec = ((interface or {}).get("screens") or {}).get(f"task:{node_name}")
    if not isinstance(spec, dict):
        return []
    return list(dict.fromkeys(b[5:] for b in component_binds(spec) if b.startswith("node.")))


# ── findings (the checker's WF017-WF019) ─────────────────────────────────────────────────────


def _finding(code: str, severity: str, message: str, node: str | None = None, path: str | None = None) -> dict[str, Any]:
    return {"code": code, "severity": severity, "message": message, "node": node, "param": None, "path": path}


def interface_findings(definition: dict[str, Any], shapes_by_node: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """WF017 (error), WF018 and WF019 (warnings) for ``definition["interface"]``; nothing when there is none.

    ``shapes_by_node`` (``{node: {"outputs": {port: Shape}}}``, the checker's own) lets WF018 look inside node outputs;
    without it only node names and input fields are checked."""
    iface = definition.get("interface")
    if not isinstance(iface, dict) or not iface:
        return []
    nodes = _nodes(definition)
    out: list[dict[str, Any]] = []
    kind = iface.get("kind")
    want = TRIGGER_FOR_KIND.get(kind or "")
    trig = iface.get("trigger_node")
    if want is None:
        out.append(_finding("WF017", "error", f"The interface kind '{kind}' isn't form, chat or custom."))
    elif not trig or trig not in nodes:
        out.append(_finding("WF017", "error", f"The interface needs a {want} node, and '{trig or ''}' isn't in the workflow.", node=trig or None))
    elif nodes[trig].get("type") != want:
        out.append(_finding("WF017", "error", f"A {kind} interface starts at a {want} node, but '{trig}' is a {nodes[trig].get('type')}.", node=trig))

    result = iface.get("result_node")
    if result and (result not in nodes):
        out.append(_finding("WF019", "warning", f"The result node '{result}' isn't in the workflow.", node=result))
    elif not result:
        has_out = {c.get("source_node") for c in definition.get("connections") or [] if isinstance(c, dict)}
        last = [n for n in nodes.values() if n["name"] not in has_out and n.get("type") not in TRIGGER_FOR_KIND.values()]
        if last and any(n.get("type") in MANY_ITEM_TYPES for n in last):
            out.append(_finding("WF019", "warning",
                                "The interface has no result node and the last node can produce many items; pick a Respond node.",
                                node=next(n["name"] for n in last if n.get("type") in MANY_ITEM_TYPES)))

    if kind == "custom":
        out.extend(_binding_findings(definition, iface, nodes, shapes_by_node))
    return out


def _binding_findings(definition, iface, nodes, shapes_by_node) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    conns = _connections(definition)
    names = list(nodes)
    input_shape = trigger_input_schema(definition, iface)
    input_props = (input_shape or {}).get("properties") if isinstance((input_shape or {}).get("properties"), dict) else None
    for sid, spec in (iface.get("screens") or {}).items():
        if not isinstance(spec, dict):
            continue
        task_node = sid[5:] if sid.startswith("task:") else None
        if task_node is not None and task_node not in nodes:
            out.append(_finding("WF018", "warning", f"Screen '{sid}' is for a node that isn't in the workflow.", node=task_node, path=sid))
            continue
        upstream = set(ancestors(conns, task_node)) if task_node else set()
        for bind in dict.fromkeys(component_binds(spec)):
            root, _, rest = bind.partition(".")
            if root == "input" and input_props is not None and rest:
                first = rest.split(".", 1)[0]
                if first not in input_props:
                    out.append(_finding("WF018", "warning", f"Screen '{sid}' shows input.{first}, which isn't a field of the form.", path=f"{sid}:{bind}"))
            elif root == "node":
                hit = split_node_bind(bind, names)
                if hit is None:
                    out.append(_finding("WF018", "warning", f"Screen '{sid}' binds '{bind}', but no such node exists.", path=f"{sid}:{bind}"))
                    continue
                name, path = hit
                if task_node is None:
                    out.append(_finding("WF018", "warning", f"Screen '{sid}' binds '{bind}'; only task screens can read node outputs.", node=name, path=f"{sid}:{bind}"))
                elif name not in upstream:
                    out.append(_finding("WF018", "warning", f"Screen '{sid}' binds '{bind}', but '{name}' isn't upstream of '{task_node}'.", node=name, path=f"{sid}:{bind}"))
                elif path and shapes_by_node:
                    outs = (shapes_by_node.get(name) or {}).get("outputs") or {}
                    shape = outs.get("main") or next(iter(outs.values()), None)
                    if shape is not None and not sh.is_unknown(shape) and sh.lookup(shape, path.split(".")) is None:
                        out.append(_finding("WF018", "warning", f"Screen '{sid}' binds '{bind}', but '{path}' isn't in the output of '{name}'.", node=name, path=f"{sid}:{bind}"))
            elif root == "task" and task_node is None:
                out.append(_finding("WF018", "warning", f"Screen '{sid}' binds '{bind}'; task.* only exists on task screens.", path=f"{sid}:{bind}"))
            elif root == "result" and sid != "result":
                out.append(_finding("WF018", "warning", f"Screen '{sid}' binds '{bind}'; result.* only exists on the result screen.", path=f"{sid}:{bind}"))
    return out


# ── Wired to ─────────────────────────────────────────────────────────────────────────────────


def compute_wired(definition: dict[str, Any], iface: dict[str, Any] | None) -> dict[str, Any]:
    """The chips: ``{input: [field], tasks: [node], context: ["Node.path"], result: node|None}`` (section 6.3)."""
    if not iface:
        return {"input": [], "tasks": [], "context": [], "result": None}
    nodes = _nodes(definition)
    names = list(nodes)
    shape = trigger_input_schema(definition, iface)
    all_fields = ff.field_order(shape, _order_of(nodes.get(iface.get("trigger_node") or ""))) if shape else []
    all_fields = [f for f in all_fields if ff.is_renderable(((shape or {}).get("properties") or {}).get(f))]
    screens = iface.get("screens") or {} if iface.get("kind") == "custom" else {}
    used: list[str] = []
    context: list[str] = []
    wants_all = iface.get("kind") != "custom" or not screens
    for spec in screens.values():
        comps = spec.get("components") or [] if isinstance(spec, dict) else []
        if any(isinstance(c, dict) and c.get("type") == "input_form" for c in comps):
            wants_all = True
        for b in component_binds(spec if isinstance(spec, dict) else {}):
            if b.startswith("input."):
                used.append(b[6:].split(".", 1)[0])
            elif b.startswith("node.") and split_node_bind(b, names):
                context.append(b[5:])
    inputs = all_fields if wants_all else [f for f in all_fields if f in used]
    tasks = [sid[5:] for sid in screens if sid.startswith("task:")]
    return {"input": list(dict.fromkeys(inputs)), "tasks": tasks, "context": list(dict.fromkeys(context)),
            "result": iface.get("result_node") or None}


def _order_of(node: dict[str, Any] | None) -> list[str] | None:
    order = ((node or {}).get("parameters") or {}).get("field_order")
    return order if isinstance(order, list) else None


# ── deterministic generation (no LLM) ────────────────────────────────────────────────────────


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:60].strip("-")
    return s if SLUG_RE.match(s) else (s + "-ui" if len(s) == 1 else "workflow-ui")


def generate_interface(definition: dict[str, Any], workflow_name: str, kind: str, slug: str | None = None) -> dict[str, Any]:
    """A starting interface for ``definition`` (section 6.3 'Generate initial screens'). Pure, deterministic."""
    nodes = list(definition.get("nodes") or [])
    trig_type = TRIGGER_FOR_KIND[kind]
    trigger = next((n for n in nodes if n.get("type") == trig_type and not n.get("disabled")), None)
    responds = [n for n in nodes if n.get("type") == "Respond" and not n.get("disabled")]
    title = ((trigger or {}).get("parameters") or {}).get("title") or workflow_name or "Untitled"
    iface: dict[str, Any] = {
        "kind": kind,
        "slug": slug or slugify(workflow_name),
        "title": title,
        "description": ((trigger or {}).get("parameters") or {}).get("description") or "",
        "access": {"mode": "public"},
    }
    if trigger:
        iface["trigger_node"] = trigger["name"]
    if responds:
        iface["result_node"] = responds[-1]["name"]
    if kind != "custom":
        return iface

    shape = trigger_input_schema({"nodes": nodes}, iface) if trigger else None
    props = (shape or {}).get("properties") or {}
    order = ff.field_order(shape, _order_of(trigger)) if shape else []
    fields = [(n, props[n]) for n in order if ff.is_renderable(props.get(n))]
    iface["screens"] = {"start": {"v": 1, "title": title, "layout": "card", "components": [
        {"type": "heading", "text": title}, {"type": "input_form"}]}}
    for n in nodes:
        if n.get("type") != "Approval" or n.get("disabled"):
            continue
        comps: list[dict[str, Any]] = [{"type": "heading", "text": n["name"]}, {"type": "status", "bind": "task.status", "due": "task.due_at"}]
        for fname, fshape in fields:
            label = ff.humanize(fname)
            if fshape.get("format") == "currency" or fname == "amount":
                comps.append({"type": "amount", "label": label, "bind": f"input.{fname}", "currency": "USD", "size": "xl"})
            else:
                comps.append({"type": "field", "label": label, "bind": f"input.{fname}"})
        ctx = (n.get("parameters") or {}).get("context")
        for label in (ctx if isinstance(ctx, dict) else {}):
            comps.append({"type": "field", "label": str(label), "bind": f"task.context.{label}"})
        comps.append({"type": "form", "bind": "task.form"})
        comps.append({"type": "actions", "bind": "task"})
        iface["screens"][f"task:{n['name']}"] = {"v": 1, "title": n["name"], "layout": "card", "components": comps}
    iface["screens"]["result"] = {"v": 1, "title": "Result", "layout": "card", "components": [
        {"type": "heading", "text": "Result"}, {"type": "result", "bind": "result"}]}
    return iface
