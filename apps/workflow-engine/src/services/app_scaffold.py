"""App scaffold: the approved plan's app brief plus the project's routes become a complete, working app with no model call.

Each plan table whose routes follow the ``studio.resource`` convention (list, get, create, update, delete, ``POST /x/{id}/<verb>``
transitions) becomes a *resource definition* in ``api/resources.ts``: typed rows from the table's columns, a field list, the state
machine and a client that calls the generated ``api.<operation>`` functions. Each plan screen becomes a page from a template
(list + detail, form, inbox, dashboard, settings) bound to a resource through the layouts and data-bound blocks of the kit
(``kit/resource.tsx``); theme tokens go to ``index.css``. A screen that no template fits is written as a placeholder and
returned in ``Scaffold.custom`` for the model, which then writes only that screen (or copy tweaks) as patches.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from .spec_tools import PARAM_RE

RESOURCES_PATH = "api/resources.ts"
CSS_PATH = "index.css"

TS_TYPES = {"string": "string", "datetime": "string", "number": "number", "integer": "number", "boolean": "boolean", "json": "unknown"}
LONG_TEXT = re.compile(r"(description|notes?|comment|reason|details?|body|message|summary|justification)$")
COMMENT_COL = re.compile(r"(comment|reason|note)")
NEGATIVE = re.compile(r"(reject|declin|deny|denied|cancel|refus)")
POSITIVE = re.compile(r"(approv|accept|confirm|complete|finish|resolve|pay|ship|publish|submit)")
DASHBOARD = re.compile(r"(dashboard|overview|summary|stats|statistic|report|analytic|metric)")
SETTINGS = re.compile(r"(setting|preference|configur|profile)")
INBOX = re.compile(r"(approv|queue|inbox|review|triage|pending|to.?do|moderat)")
FORM = re.compile(r"^(new|create|submit|request|add|apply|file|log|raise|book|report an?)\b")
STATE_FIELDS = ("status", "state", "stage")

THEME = """/* Theme tokens: every colour, radius and font of the app comes from here (shadcn-style). Edit the values to restyle it. */
:root {
  --background: #f8fafc;
  --foreground: #0f172a;
  --card: #ffffff;
  --muted: #f1f5f9;
  --muted-foreground: #64748b;
  --border: #e2e8f0;
  --primary: #4f46e5;
  --primary-foreground: #ffffff;
  --success: #047857;
  --success-soft: #d1fae5;
  --warning: #b45309;
  --warning-soft: #fef3c7;
  --danger: #b91c1c;
  --danger-soft: #fee2e2;
  --radius: 0.5rem;
  --font-sans: ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;
}

@media (prefers-color-scheme: dark) {
  :root {
    --background: #0b1020;
    --foreground: #e2e8f0;
    --card: #131a2e;
    --muted: #1c2540;
    --muted-foreground: #94a3b8;
    --border: #263152;
    --primary: #818cf8;
    --primary-foreground: #0b1020;
    --success: #6ee7b7;
    --success-soft: #064e3b;
    --warning: #fcd34d;
    --warning-soft: #451a03;
    --danger: #fca5a5;
    --danger-soft: #450a0a;
  }
}

body {
  margin: 0;
  background: var(--background);
  color: var(--foreground);
  font-family: var(--font-sans);
}
"""


@dataclass
class Scaffold:
    files: list[dict[str, str]] = field(default_factory=list)
    screens: list[dict[str, str]] = field(default_factory=list)     # {name, path, template, resource}
    custom: list[dict[str, Any]] = field(default_factory=list)      # {name, path, purpose, operations}: for the model
    resources: list[str] = field(default_factory=list)
    operations: list[str] = field(default_factory=list)             # api operations the scaffold calls


# ── Naming ───────────────────────────────────────────────────────────────────

def _words(s: str) -> list[str]:
    return [w for w in re.split(r"[^A-Za-z0-9]+", re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", s)) if w]


def pascal(s: str) -> str:
    return "".join(w[:1].upper() + w[1:] for w in _words(s)) or "Screen"


def camel(s: str) -> str:
    p = pascal(s)
    return p[:1].lower() + p[1:]


def title(s: str) -> str:
    t = " ".join(_words(s)).lower()
    return t[:1].upper() + t[1:]


def singular(s: str) -> str:
    s = s.lower()
    if s.endswith("ies") and len(s) > 4:
        return s[:-3] + "y"
    if s.endswith(("ses", "xes", "ches", "shes")):
        return s[:-2]
    if s.endswith("s") and not s.endswith("ss") and len(s) > 3:
        return s[:-1]
    return s


def _key(s: str) -> str:
    return singular(re.sub(r"[^a-z0-9]", "", s.lower()))


def _q(s: str) -> str:
    return json.dumps(s)


# ── Routes → resources ───────────────────────────────────────────────────────

def _segments(path: str) -> list[str]:
    segs = [s for s in path.strip("/").split("/") if s]
    return segs[1:] if segs and segs[0].lower() == "api" else segs


def _is_param(seg: str) -> bool:
    return bool(PARAM_RE.fullmatch(seg)) or seg.startswith(":")


def _param(seg: str) -> str:
    return seg.strip("{}").lstrip(":")


def _table_for(segments: list[str], tables: dict[str, dict[str, Any]]) -> str | None:
    if not segments or _is_param(segments[0]):
        return None
    base = _key(segments[0])
    return next((n for n in tables if _key(n) == base), None)


def _verb_state(verb: str, states: list[str]) -> str | None:
    v = re.sub(r"[^a-z]", "", verb.lower())
    if not v:
        return None
    for s in states:
        sk = re.sub(r"[^a-z]", "", s.lower())
        if sk == v or sk.startswith(v[:4]) or v.startswith(sk[:4]) and len(sk) >= 4:
            return s
    return None


def _ts_type(col: dict[str, Any]) -> str:
    base = " | ".join(json.dumps(v) for v in col["enum"]) if col.get("enum") else TS_TYPES.get(col.get("type") or "string", "string")
    return base if col.get("required") else f"{base} | null"


def _field_type(col: dict[str, Any]) -> str:
    t = col.get("type") or "string"
    if t == "json":
        return "text"
    if t == "string" and not col.get("enum") and LONG_TEXT.search(col["name"]):
        return "text"
    return t


def _state_machine(table: dict[str, Any], verbs: dict[str, tuple[str, dict[str, Any]]]) -> dict[str, Any] | None:
    """The table's state machine as the components take it: the plan's, else the enum ``status`` column with the transition
    routes it has. Each transition carries its route (``op``) when one exists."""
    cols = {c["name"]: c for c in table["columns"]}
    sm = table.get("state_machine")
    if sm:
        field_, initial, final = sm["field"], sm.get("initial"), list(sm.get("final") or [])
        transitions = [dict(t) for t in sm.get("transitions") or []]
    else:
        field_ = next((n for n in STATE_FIELDS if n in cols and cols[n].get("enum")), None)
        if field_ is None:
            return None
        states = list(cols[field_]["enum"])
        initial = cols[field_].get("default") or states[0]
        final = []
        transitions = []
        for verb in verbs:
            to = _verb_state(verb, states)
            if to and to != initial:
                transitions.append({"from": "*", "to": to, "route": verb})
    if not transitions:
        return {"field": field_, "initial": initial, "final": final, "transitions": []}
    comment_col = next((n for n in cols if COMMENT_COL.search(n) and n != field_ and not n.endswith(("_at", "_by"))), None)
    out = []
    for t in transitions:
        route = t.get("route") or ""
        verb = route.rsplit("/", 1)[-1] if route and "/" in route else ""
        verb = verb if verb in verbs else next((v for v, (to, _) in verbs.items() if to == t["to"]), "")
        if not verb and sm:
            verb = ""
        label = title(verb) if verb else f"Mark {t['to'].replace('_', ' ')}"
        item: dict[str, Any] = {"from": t["from"], "to": t["to"], "label": label, "verb": verb}
        txt = f"{verb} {t['to']}".lower()
        if NEGATIVE.search(txt):
            item["tone"] = "danger"
        elif POSITIVE.search(txt) and not t["from"] == "*" or POSITIVE.search(txt):
            item["tone"] = "primary"
        if comment_col and (NEGATIVE.search(txt) or POSITIVE.search(txt)):
            item["comment"] = "required" if NEGATIVE.search(txt) else "optional"
            item["commentField"] = comment_col
        out.append(item)
    return {"field": field_, "initial": initial, "final": final, "transitions": out}


def build_resources(plan: dict[str, Any], routes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One resource per plan table that has a list route: its columns, state machine and the operation behind each action."""
    tables = {t["name"]: t for t in plan["data"]["tables"]}
    ops: dict[str, dict[str, Any]] = {n: {"verbs": {}} for n in tables}
    for r in routes:
        method, path, name = str(r.get("method") or "").upper(), str(r.get("path") or ""), str(r.get("name") or "")
        segs = _segments(path)
        table = _table_for(segs, tables)
        if table is None or not name:
            continue
        rest = segs[1:]
        o = ops[table]
        if not rest:
            o["list" if method == "GET" else "create" if method == "POST" else "_"] = {"op": name, "path": path}
        elif len(rest) == 1 and _is_param(rest[0]):
            key = {"GET": "get", "PUT": "update", "PATCH": "update", "DELETE": "remove"}.get(method)
            if key and not (key == "update" and "update" in o and method == "PUT"):
                o[key] = {"op": name, "path": path, "param": _param(rest[0])}
        elif len(rest) == 2 and _is_param(rest[0]) and not _is_param(rest[1]) and method in ("POST", "PUT", "PATCH"):
            o["verbs"][rest[1]] = ("", {"op": name, "path": path, "param": _param(rest[0])})
    out = []
    for name, table in tables.items():
        o = ops[name]
        if "list" not in o:
            continue
        cols = [c for c in table["columns"] if c["name"] not in ("id",)]
        states_cols = {c["name"]: c for c in cols}
        verbs = o["verbs"]
        sm = _state_machine(table, {v: ("", i) for v, (_, i) in verbs.items()})
        if sm:
            names = list(states_cols.get(sm["field"], {}).get("enum") or [])
            for t in sm["transitions"]:
                v = t.get("verb") or ""
                if not v:
                    v = next((x for x in verbs if _verb_state(x, [t["to"]])), "")
                t["verb"] = v
                t["op"] = verbs[v][1] if v in verbs else None
            if names:
                sm["states"] = names
        out.append({"table": table, "name": name, "ops": {k: v for k, v in o.items() if k != "verbs"}, "state_machine": sm})
    return out


# ── api/resources.ts ─────────────────────────────────────────────────────────

def _fields_ts(res: dict[str, Any], by_table: dict[str, dict[str, Any]]) -> str:
    sm = res["state_machine"]
    lines = []
    for c in res["table"]["columns"]:
        n = c["name"]
        auto = n.endswith(("_at", "_by")) or n in ("archived", "archived_at")
        parts = [f"name: {_q(n)}", f"label: {_q(title(n.removesuffix('_id')))}", f"type: {_q(_field_type(c))}"]
        if c.get("required") and not (sm and n == sm["field"]):
            parts.append("required: true")
        if c.get("enum"):
            parts.append("enum: " + json.dumps(c["enum"]))
        ref = c.get("ref")
        if ref and "." in ref:
            target = by_table.get(ref.split(".")[0])
            if target and "list" in target["ops"]:
                label = next((x["name"] for x in target["table"]["columns"] if x["name"] in ("name", "title", "label", "email", "key")
                              ), next((x["name"] for x in target["table"]["columns"] if x.get("type") == "string"), "id"))
                parts.append(f"ref: {{ client: {camel(target['name'])}Client, labelField: {_q(label)} }}")
        if auto or (sm and n == sm["field"]):
            parts.append("readOnly: true")
        if n.endswith("_by") or n in ("archived", "archived_at"):
            parts.append("hidden: true")
        lines.append("    { " + ", ".join(parts) + " },")
    return "\n".join(lines)


def _call(name: str, params: list[str], body: str | None, query: str | None) -> str:
    """The ``api.<op>(...)`` expression with the arguments the generated client takes: (params?, body?, query?)."""
    args = []
    if params:
        args.append("{ " + ", ".join(f"{p}: id" for p in params) + " }")
    if body:
        args.append(f"{body} as Parameters<typeof api.{name}>[{len(args)}]")
    if query:
        args.append(f"{query} as Parameters<typeof api.{name}>[{len(args)}]")
    return f"api.{name}({', '.join(args)})"


def _client_ts(res: dict[str, Any]) -> str:
    row, o, sm = f"{pascal(singular(res['name']))}Row", res["ops"], res["state_machine"]
    lines = [f"const {camel(res['name'])}Client: ResourceClient<{row}> = {{",
             f"  list: (q) => typed<Page<{row}>>({_call(o['list']['op'], [], None, 'q')}),"]
    if "get" in o:
        lines.append(f"  get: (id) => typed<{row}>({_call(o['get']['op'], [o['get']['param']], None, None)}),")
    if "create" in o:
        lines.append(f"  create: (body) => typed<{row}>({_call(o['create']['op'], [], 'body', None)}),")
    if "update" in o:
        lines.append(f"  update: (id, body) => typed<{row}>({_call(o['update']['op'], [o['update']['param']], 'body', None)}),")
    if "remove" in o:
        lines.append(f"  remove: (id) => typed<unknown>({_call(o['remove']['op'], [o['remove']['param']], None, None)}),")
    if sm and sm["transitions"]:
        cases = {}
        for t in sm["transitions"]:
            if t.get("op"):
                cases[t["to"]] = _call(t["op"]["op"], [t["op"]["param"]], "body", None)
            elif "update" in o and t["to"] not in cases:
                cases[t["to"]] = _call(o["update"]["op"], [o["update"]["param"]], "{ " + sm["field"] + ": to, ...body }", None)
        if cases:
            lines.append("  transition: (id, to, body) => {")
            lines.append("    switch (to) {")
            for to, expr in cases.items():
                lines.append(f"      case {_q(to)}: return typed<{row}>({expr})")
            lines.append(f"      default: return Promise.reject(new Error(`{res['table']['name']} cannot move to ${{to}}`))")
            lines.append("    }")
            lines.append("  },")
    lines.append("}")
    return "\n".join(lines)


def _def_ts(res: dict[str, Any], by_table: dict[str, dict[str, Any]]) -> str:
    table, sm = res["table"], res["state_machine"]
    cols = {c["name"]: c for c in table["columns"]}
    title_field = next((n for n in ("title", "name", "item", "subject", "label", "summary") if n in cols),
                       next((c["name"] for c in table["columns"] if c.get("type") == "string" and not c.get("enum")
                             and not c.get("ref") and not c["name"].endswith(("_at", "_by"))), "id"))
    res["title_field"] = title_field
    out = [f"export const {camel(res['name'])}: ResourceDef<{pascal(singular(res['name']))}Row> = {{",
           f"  name: {_q(res['name'])}, label: {_q(title(res['name']))}, singular: {_q(title(singular(res['name'])))}, "
           f"titleField: {_q(title_field)},",
           f"  search: {'true' if any(c.get('type') == 'string' and not c.get('enum') for c in table['columns']) else 'false'},",
           "  fields: [", _fields_ts(res, by_table), "  ],", f"  client: {camel(res['name'])}Client,"]
    if sm:
        trs = []
        for t in sm["transitions"]:
            if not (t.get("op") or "update" in res["ops"]):
                continue
            bits = [f"from: {_q(t['from'])}", f"to: {_q(t['to'])}", f"label: {_q(t['label'])}"]
            for k in ("tone", "comment", "commentField"):
                if t.get(k):
                    bits.append(f"{k}: {_q(t[k])}")
            trs.append("      { " + ", ".join(bits) + " },")
        out.append(f"  states: {{ field: {_q(sm['field'])}, initial: {_q(sm.get('initial') or '')}, final: {json.dumps(sm.get('final') or [])},")
        out.append("    transitions: [")
        out.extend(trs)
        out.append("    ] },")
    out.append("}")
    return "\n".join(out)


def resources_ts(resources: list[dict[str, Any]]) -> str:
    by_table = {r["name"]: r for r in resources}
    head = ["/** Typed access to the app's data: one `ResourceDef` per table, built from the plan's columns and the project's routes",
            " * (`api/client.ts` is generated; this file only wraps it). Edit labels, field order or transitions here. */",
            "import { api } from './client'",
            "import { registerResources } from '../kit'",
            "import type { Page, ResourceClient, ResourceDef } from '../kit'", "",
            "function typed<T>(p: Promise<unknown>): Promise<T> {", "  return p as Promise<T>", "}", ""]
    for r in resources:
        row = ["export type " + f"{pascal(singular(r['name']))}Row = {{", "  id: number", "  created_at?: string", "  updated_at?: string"]
        for c in r["table"]["columns"]:
            row.append(f"  {c['name']}{'' if c.get('required') else '?'}: {_ts_type(c)}")
        row.append("}")
        head += row + [""]
    for r in resources:
        _def_ts(r, by_table)      # sets title_field
    for r in resources:
        head += [_client_ts(r), ""]
    for r in resources:
        head += [_def_ts(r, by_table), ""]
    head += ["registerResources({ " + ", ".join(camel(r["name"]) for r in resources) + " })", ""]
    return "\n".join(head)


# ── Screens → templates ──────────────────────────────────────────────────────

def _screen_resource(screen: dict[str, Any], resources: list[dict[str, Any]], tables: dict[str, Any]) -> dict[str, Any] | None:
    by_name = {r["name"]: r for r in resources}
    for key in screen["routes"]:
        m = re.match(r"^\s*[A-Za-z]+\s+(\S+)", key)
        t = _table_for(_segments(m.group(1)), tables) if m else None
        if t in by_name:
            return by_name[t]
    text = _key(screen["name"] + screen["purpose"])
    hits = [r for r in resources if _key(r["name"]) in text or _key(singular(r["name"])) in text]
    if hits:
        return max(hits, key=lambda r: len(r["name"]))
    if not screen["routes"] and DASHBOARD.search(screen["name"].lower()):     # a dashboard with no routes of its own: the main resource
        return next((r for r in resources if r["state_machine"]), resources[0])
    return None


def classify(screen: dict[str, Any], res: dict[str, Any] | None) -> str:
    """The template for a screen: dashboard, settings, inbox, form, list (list + detail) or custom."""
    if res is None:
        return "custom"
    name = screen["name"].lower()
    text = f"{name} {screen['purpose'].lower()}"
    ops, sm = res["ops"], res["state_machine"]
    if DASHBOARD.search(name) or (DASHBOARD.search(text) and "list" not in name):
        return "dashboard"
    if SETTINGS.search(name):
        return "settings" if "update" in ops or "create" in ops else "list"
    has_actions = bool(sm and any(t.get("op") or "update" in ops for t in sm["transitions"]))
    if INBOX.search(name) and has_actions:
        return "inbox"
    if FORM.search(name.strip()) and "create" in ops:
        return "form"
    return "list"


def _view(screen: dict[str, Any], template: str, res: dict[str, Any] | None, jsx: str, blocks: list[str], uses_go: bool) -> str:
    comp = pascal(screen["name"])
    on = f" on {res['name']}" if res else ""
    lines = [f"/** {title(screen['name'])} — {screen['purpose'] or 'scaffolded page'} ({template}{on}). */",
             f"import {{ {', '.join(sorted(set(blocks)))} }} from '../kit'"]
    if res:
        lines.append(f"import {{ {camel(res['name'])} }} from '../api/resources'")
    lines += ["", "export interface Props { go: (screen: string) => void }", "",
              f"export default function {comp}({{ {'go' if uses_go else ''} }}: Props) {{", f"  return {jsx}", "}", ""]
    return "\n".join(lines)


def _attrs(screen: dict[str, Any], res: dict[str, Any], extra: str = "") -> str:
    text = f' subtitle={{{_q(screen["purpose"])}}}' if screen["purpose"] else ""
    return f"resource={{{camel(res['name'])}}} title={{{_q(title(screen['name']))}}}{text}{extra}"


def view_source(screen: dict[str, Any], template: str, res: dict[str, Any] | None, targets: dict[str, str]) -> str:
    """One page: a layout from the kit with the resource and a few props; no fetching, loading or error code."""
    empty = (screen.get("states") or {}).get("empty")
    empty_prop = f" emptyText={{{_q(empty)}}}" if empty else ""
    if template == "custom" or res is None:
        jsx = (f"<Page title={{{_q(title(screen['name']))}}} subtitle={{{_q(screen['purpose'])}}}>\n"
               f"    <Panel>{title(screen['name'])} is not built yet.</Panel>\n  </Page>")
        return _view(screen, "custom", None, jsx, ["Page", "Panel"], False)
    sm = res["state_machine"]
    if template == "list":
        return _view(screen, template, res, f"<ListDetailLayout {_attrs(screen, res, empty_prop)} />", ["ListDetailLayout"], False)
    if template == "form":
        back = targets.get(res["name"])
        done = f" onDone={{() => go({_q(back)})}}" if back else ""
        return _view(screen, template, res, f"<FormPage {_attrs(screen, res, done)} />", ["FormPage"], bool(back))
    if template == "inbox":
        return _view(screen, template, res, f"<InboxLayout {_attrs(screen, res, empty_prop)} />", ["InboxLayout"], False)
    if template == "settings":
        return _view(screen, template, res, f"<SettingsPage {_attrs(screen, res)} />", ["SettingsPage"], False)
    cols = {c["name"]: c for c in res["table"]["columns"]}
    tiles = [f"{{ label: 'All {res['name'].replace('_', ' ')}' }}"]
    group = None
    if sm:
        group = sm["field"]
        values = [x for x in (sm.get("states") or [t["to"] for t in sm["transitions"]]) if x]
        initial = sm.get("initial")
        for st in (([initial] if initial in values else []) + [x for x in values if x != initial])[:3]:
            tiles.append(f"{{ label: {_q(title(st))}, filter: {{ {sm['field']}: {_q(st)} }}, tone: {_q(_tone(st))} }}")
    else:
        group = next((n for n, c in cols.items() if c.get("enum")), None)
    money = next((n for n in ("amount", "total", "cost", "price", "value", "quantity", "qty")
                  if n in cols and cols[n].get("type") in ("number", "integer")), None)
    if money:
        tiles.append(f"{{ label: {_q('Total ' + money.replace('_', ' '))}, sum: {_q(money)} }}")
    recent = json.dumps([res.get("title_field")] + ([sm["field"]] if sm else []))
    extra = f" tiles={{[{', '.join(tiles)}]}}" + (f" groupBy={{{_q(group)}}}" if group else "") + f" recentColumns={{{recent}}}"
    return _view(screen, template, res, f"<DashboardLayout {_attrs(screen, res, extra)} />", ["DashboardLayout"], False)


def _tone(state: str) -> str:
    s = state.lower()
    if re.search(r"(approv|paid|done|complete|success|accept|active|resolved|closed|shipped|deliver|fulfil)", s):
        return "good"
    if re.search(r"(reject|fail|error|cancel|declin|denied|overdue|blocked)", s):
        return "bad"
    if re.search(r"(pending|review|submitted|waiting|open|progress|queued|new)", s):
        return "warn"
    return "none"


def app_source(app_title: str, screens: list[dict[str, str]]) -> str:
    imports = [f"import {s['component']} from './views/{s['file']}'" for s in screens]
    items = ",\n".join(f"      {{ id: {_q(s['id'])}, label: {_q(s['name'])}, render: (go) => <{s['component']} go={{go}} /> }}" for s in screens)
    return f"""/** {app_title} — navigation shell. Each screen is a file in views/; data access is in api/resources.ts. */
import './index.css'
import {{ AppShell }} from './kit'
import './api/resources'
{chr(10).join(imports)}

export default function App() {{
  return (
    <AppShell title={{{_q(app_title)}}} screens={{[
{items},
    ]}} />
  )
}}
"""


def build(plan: dict[str, Any], app_key: str, routes: list[dict[str, Any]]) -> Scaffold | None:
    """The scaffold for plan app ``app_key`` over the project's ``routes`` (dicts with name, method, path), or None when the
    plan has no such app, no screens, or no table with a list route to bind to."""
    from . import project_plan as pp

    p = pp.normalize(plan)
    app = pp._find_app(p, app_key)
    if app is None or not app["screens"]:
        return None
    resources = build_resources(p, routes)
    if not resources:
        return None
    tables = {t["name"]: t for t in p["data"]["tables"]}
    resources_src = resources_ts(resources)              # also fills in each resource's title_field
    by_name = {r["name"]: r for r in resources}
    classified = []
    for s in app["screens"]:
        res = _screen_resource(s, resources, tables)
        classified.append((s, res, classify(s, res)))
    targets: dict[str, str] = {}
    for s, res, tpl in classified:
        if res and tpl == "list":
            targets.setdefault(res["name"], _slug(s["name"]))
    out = Scaffold(resources=list(by_name))
    screens_meta: list[dict[str, str]] = []
    seen: set[str] = set()
    for s, res, tpl in classified:
        comp = pascal(s["name"])
        while comp in seen:
            comp += "X"
        seen.add(comp)
        path = f"views/{comp}.tsx"
        out.files.append({"path": path, "content": view_source(s, tpl, res, targets)})
        meta = {"name": title(s["name"]), "id": _slug(s["name"]), "component": comp, "file": comp}
        screens_meta.append(meta)
        out.screens.append({"name": s["name"], "path": path, "template": tpl, "resource": res["name"] if res else ""})
        if tpl == "custom":
            ops = [r["name"] for r in routes if r.get("name") and any(
                _route_norm(k) == _route_norm(f"{r.get('method')} {r.get('path')}") for k in s["routes"])]
            out.custom.append({"name": s["name"], "path": path, "purpose": s["purpose"], "operations": ops})
    out.files += [{"path": CSS_PATH, "content": THEME},
                  {"path": RESOURCES_PATH, "content": resources_src},
                  {"path": "App.tsx", "content": app_source(app["name"] or p["title"] or "App", screens_meta)}]
    out.operations = sorted({m for m in re.findall(r"\bapi\.(\w+)\(", resources_src)})
    return out


def _slug(s: str) -> str:
    return "-".join(w.lower() for w in _words(s)) or "screen"


def _route_norm(key: str) -> str:
    m = re.match(r"^\s*([A-Za-z]+)\s+(\S+)", key or "")
    return f"{m.group(1).upper()} {PARAM_RE.sub('{}', m.group(2))}" if m else key.strip()
