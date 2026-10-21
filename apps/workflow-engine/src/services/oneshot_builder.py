"""One-shot app builder: ONE model call writes a whole app (tables, seed rows, a backend module, the React files).

The role-split pipeline (planner -> module generator -> screen writers -> app builder) needed ~50 model calls and broke at its own
seams. Given the approved plan and a runtime brief generated from the platform's own references (:func:`brief`), one call returns
the whole app as a JSON bundle (``emit_bundle``); the platform then does the rest in code:

* :func:`write_app`: the model call (a big plan is split by feature area and the calls run in parallel, App.tsx / nav assembled
  by code).
* :func:`load`: tables (created / migrated additively), seed rows into EMPTY tables only, each module through the normal module
  save path, the app (kit files injected, ``definition.studio.route_ids`` = every route the app's code uses).
* :func:`check`: module checks and self-test, a smoke call of each route, TSX validation (esbuild + type-check of kit + files).
* :func:`fix_pass`: ONE more model call with the bundle and the errors, returning only the changed parts. Leftovers are
  reported, never looped on.
* :func:`edit_app`: one call that sees the whole current bundle + the request and returns the changed parts (new / changed
  columns included: the platform migrates, and the plan gets a revision recorded without an approval gate).

Everything reports wall time and model calls per phase (:class:`Timer`). Events for the Studio live view go through
``build_coordinator.emit`` (a no-op outside a build or chat stream).

Bundle (internal form): ``{tables, seed, modules: [{name, source}], app: {name, files: [{path, content}]}}``. The model's reply
carries a single ``module``; :func:`normalize_bundle` accepts both.
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import copy
import json
import logging
import os
import re
import time
from typing import Any, Awaitable, Callable, Iterator

from ..core.config import settings
from . import build_coordinator as bc
from .project_service import ProjectError

logger = logging.getLogger(__name__)

BIG_PLAN_ROUTES = 15           # a plan with more routes ...
BIG_PLAN_TABLES = 5            # ... or more tables is written feature by feature, in parallel
MAX_OUTPUT_TOKENS = 20000      # < 21333: above it the Anthropic SDK refuses a non-streaming call
MAX_ERRORS = 40                # errors kept in a report / sent to the fix pass
MAX_ERROR_CHARS = 600
COMPACT_MAX_ERRORS = 20        # errors a compact fix call is shown
SMOKE_CONCURRENCY = 4
SEED_CHUNK = 500
CLIENT_PATH = "api/client.ts"
Log = Callable[[str], Awaitable[None]]


def builder_mode() -> str:
    """``oneshot`` (default) or ``pipeline`` (the role-split pipeline), from WORKFLOW_BUILDER_MODE."""
    return "pipeline" if os.environ.get("WORKFLOW_BUILDER_MODE", "").strip().lower() == "pipeline" else "oneshot"


def model() -> str:
    return os.environ.get("WORKFLOW_ONESHOT_MODEL", "").strip() or settings.app_builder_model or settings.module_gen_model


COMPACT_KEYWORDS = ("flash", "gemma", "haiku", "mini", "lite", "nano", "small")
COMPACT_MAX_TOKENS = 12_000     # per-call output cap of the compact profile: each call writes one feature
COMPACT_SCREENS_PER_CALL = 3    # compact: a UI call writes at most this many screens
COMPACT_FIX_PASSES = 2          # the full profile keeps 1
COMPACT_BUDGET_S = 900.0        # compact: no further fix pass once a build has run this long
_COMPACT_RE = re.compile(r"(?<![a-z])(?:" + "|".join(COMPACT_KEYWORDS) + ")")      # "mini" must not match inside "gemini"
_NO_TOOLS: set[str] = set()     # models that answered a tool call with a "tools not supported" error: asked for plain-text JSON


def profile(name: str | None = None) -> str:
    """``compact`` or ``full``: WORKFLOW_ONESHOT_PROFILE (full|compact|auto, default full); auto picks compact for small/fast model
    names (:data:`COMPACT_KEYWORDS`, matched at the start of a word) and full otherwise."""
    forced = os.environ.get("WORKFLOW_ONESHOT_PROFILE", "").strip().lower()
    if forced != "auto":
        return "compact" if forced == "compact" else "full"
    return "compact" if _COMPACT_RE.search((name or model()).lower()) else "full"


def no_tools(name: str | None = None) -> bool:
    """True when the model is asked for plain-text JSON instead of a tool call: it matches ``gemma`` or it already refused tools."""
    n = (name or model()).lower()
    return "gemma" in n or n in _NO_TOOLS


def _tools_unsupported(e: BaseException) -> bool:
    msg = str(e).lower()
    return ("tool" in msg or "function" in msg) and any(k in msg for k in ("not support", "unsupported", "not enabled", "not available", "does not allow"))


def _max_tokens() -> int:
    big = 32_000 if model().startswith("gemini-") else MAX_OUTPUT_TOKENS
    return min(big, COMPACT_MAX_TOKENS) if profile() == "compact" else big


# ── timing ───────────────────────────────────────────────────────────────────


class Timer:
    """Wall time and model calls per phase (write / load / check / fix / ...)."""

    def __init__(self) -> None:
        self.t0 = time.monotonic()
        self.phases: dict[str, dict[str, float]] = {}
        self.calls = 0

    def _p(self, name: str) -> dict[str, float]:
        return self.phases.setdefault(name, {"seconds": 0.0, "model_calls": 0})

    @contextlib.contextmanager
    def phase(self, name: str) -> Iterator[None]:
        t = time.monotonic()
        try:
            yield
        finally:
            self._p(name)["seconds"] += time.monotonic() - t

    def call(self, name: str) -> None:
        self._p(name)["model_calls"] += 1
        self.calls += 1

    def summary(self) -> dict[str, Any]:
        return {"total_seconds": round(time.monotonic() - self.t0, 2), "model_calls": self.calls,
                "phases": {k: {"seconds": round(v["seconds"], 2), "model_calls": int(v["model_calls"])}
                           for k, v in self.phases.items()}}


# ── the runtime brief, generated from code ───────────────────────────────────


def _sentence(text: Any, cap: int = 110) -> str:
    one = " ".join(str(text or "").split())
    first = re.split(r"(?<=[.!?])\s", one, maxsplit=1)[0]
    return first if len(first) <= cap else first[: cap - 1].rstrip() + "…"


def _resource_hooks() -> list[str]:
    """The hook names of ``studio.resource``, read from the SDK source the sandbox ships (nothing is imported or run)."""
    try:
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sdk", "static", "resources.py")
        with open(path, encoding="utf-8") as fh:
            m = re.search(r"^HOOKS\s*=\s*\(([^)]*)\)", fh.read(), re.M)
        return re.findall(r"\"(\w+)\"", m.group(1)) if m else []
    except Exception:  # noqa: BLE001 - the brief still works without the list
        return []


def _split_top(text: str) -> list[str]:
    """``text`` split on the commas outside brackets and quotes."""
    out, depth, cur, quote = [], 0, [], ""
    for ch in text:
        if quote:
            quote = "" if ch == quote else quote
        elif ch in "\"'":
            quote = ch
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch == "," and depth == 0:
            out.append("".join(cur).strip())
            cur = []
            continue
        cur.append(ch)
    out.append("".join(cur).strip())
    return [x for x in out if x]


def _short_signature(sig: str) -> str:
    """``f(a: int, b: str | None = None, *, c: dict = ())`` -> ``f(a, b?, *, c=())``: the parameter types are dropped (names, optional
    marks and non-None defaults are what a call needs)."""
    m = re.match(r"^([^(]*)\((.*)\)$", sig.strip(), re.S)
    if not m:
        return sig
    parts = []
    for prm in _split_top(m.group(2)):
        name, _, rest = prm.partition(":")
        default = None
        if ":" not in prm and "=" in prm:
            name, _, default = prm.partition("=")
        elif "=" in rest:
            default = rest.rsplit("=", 1)[1].strip()
        name = name.strip()
        parts.append(name if default is None else (name + "?" if default in ("None", "") else f"{name}={default.strip()}"))
    return f"{m.group(1)}({', '.join(parts)})"


def _sdk_lines() -> list[str]:
    from .platform_index import sdk_symbols

    out: list[str] = []
    verbs: list[str] = []
    for e in sdk_symbols():
        if e["kind"] == "rule":
            continue
        sig = (e.get("signature") or e["name"]).replace("studio.db.table(name).", "t.").replace("studio.resource(...).", "res.")
        if re.match(r"^api\.(get|post|put|patch|delete)\(", sig):       # five identical decorators: one line
            verbs.append(sig.split("(", 1)[0][4:])
            if len(verbs) == 1:
                out.append("")
                verbs_at = len(out) - 1
                short = _short_signature(sig).split("(", 1)[1]
            continue
        if e["name"] == "studio.resource" or sig.startswith("studio.resource("):
            out.append("- `studio.resource(table, ...)`: see below")
            continue
        doc = _sentence(e.get("doc"), 52)
        if doc.lower() in (sig.split("(")[0].split(".")[-1].lower(), "table." + sig.split("(")[0].split(".")[-1].lower()):
            doc = ""
        out.append(f"- `{_short_signature(sig)}`" + (f": {doc}" if doc else ""))
    if verbs:
        out[verbs_at] = f"- `api.{'|'.join(verbs)}({short}`: declare a route handled by the decorated function(req)"
    return out


def _kit_prop(p: dict[str, Any]) -> str:
    if p["name"] == "className":
        return ""
    t = " ".join(str(p.get("type") or "").split()).replace("React.ReactNode", "node")
    t = re.sub(r"\(([^()]*)\)\s*=>", lambda m: "(" + ", ".join(x.split(":")[0].strip() for x in m.group(1).split(",") if x.strip()) + ")=>", t)
    return f"{p['name']}{'' if p.get('required') else '?'}" + (f":{t}" if 0 < len(t) <= (26 if p.get("required") else 15) and t != "node" else "")


def _kit_lines() -> tuple[list[str], list[str]]:
    from .platform_index import kit_components

    comps, helpers = [], []
    for c in kit_components():
        use = _sentence(c.get("use_when") or c.get("file_doc"), 46)
        if c["kind"] == "component":
            props = [p for p in c.get("props") or [] if p["name"] != "className"]
            shown = ", ".join(x for x in (_kit_prop(p) for p in props) if x)
            comps.append(f"- `{c['name']}({{{shown}}})`: {use}")
        else:
            sig = " ".join(str(c.get("signature") or c["name"]).split())
            if "Studio UI kit" in use:
                use = ""
            sig = re.sub(r"^(?:async )?function ", "", sig)
            head = f"`{c['name']}` ({c['kind']})" if len(sig) > 80 else f"`{sig}`"
            helpers.append(f"- {head}" + (f": {use}" if use else ""))
    return comps, helpers


def _kit_generics() -> list[str]:
    """Exported kit types that take type arguments (``ResourceDef<Row extends BaseRow>``): a bare use is a type error."""
    from . import app_builder_ai_service as ab

    out: list[str] = []
    for f in ab.kit_files():
        for m in re.finditer(r"^export (?:interface|type) (\w+)(<[^\n{=]*?>)\s*(?:\{|=)", f["content"], re.M):
            out.append(f"`{m.group(1)}{m.group(2)}`")
    return out


PREVIEW_USERS: list[dict[str, Any]] = [   # mirrors the Studio preview's "View as" presets
    {"id": "alice", "name": "Alice", "roles": ["staff"]},
    {"id": "bob", "name": "Bob", "roles": ["manager"]},
    {"id": "carol", "name": "Carol", "roles": ["admin"]},
]
PREVIEW_USERS_TEXT = "; ".join(f"`{u['id']}` ({u['name']}, roles {', '.join(u['roles'])})" for u in PREVIEW_USERS)


def brief() -> str:
    """The runtime brief the writer works from, generated from the platform (SDK reference, resource API, kit components and
    hooks, column rules), so it cannot drift from what the sandbox and the kit really provide."""
    from ..sdk.generate import reference
    from . import project_plan as pp
    from .data_table_service import MAX_BULK
    from .schema_migrations import COLUMN_KEYS, SYSTEM_COLUMNS

    rules = reference()["rules"]
    comps, helpers = _kit_lines()
    hooks = ", ".join(f"`@res.{h}`" for h in _resource_hooks())
    cols = ", ".join(k for k in COLUMN_KEYS if k != "deprecated")
    sys_cols = ", ".join(sorted(SYSTEM_COLUMNS))
    nl = "\n"
    out = f"""# Build a small internal app on Command Studio

You get an approved plan (tables, routes, screens). Write the WHOLE app in one reply: tables, seed rows, Python backend module(s), React app. Use only what is described here. Reply by calling `emit_bundle` and nothing else.

## 1. Data tables
`{{name, description, columns:[...]}}`. Column keys: {cols}. `type` is one of {", ".join(pp.COLUMN_TYPES)}. `enum`: distinct non-empty values (not for boolean/json/datetime); `ref`: `"table.column"`. Names match `[A-Za-z_][A-Za-z0-9_]{{0,62}}`. Every row also has {sys_cols}, managed by the table: read them, never write or declare them. Unknown columns in a write are rejected. Datetimes are ISO-8601 strings. Seed rows (at most {MAX_BULK} per table, only your own columns) go only into a table that is still empty.

## 2. Backend module (Python)
One module = one source file; `name` is a lowercase python identifier (not `studio`). `@api` decorators are read statically on save; each decorated function becomes a project route. Handlers run in a sandbox, 30 s per request.
```python
import studio
from studio import api, Response, HTTPError

@api.get("/orders/{{id}}", auth="app_token", name="getOrder")
def get_order(req):
    \"\"\"Get one order. Returns {{id, status, ...}}\"\"\"
    return {{"id": req.params["id"]}}
```
- {rules.get("decorators", "")} Handlers take exactly one argument, `req`.
- Every route has a unique camelCase `name=`: the operation name in the app's client (`api.<name>`). Use exactly the names the user message lists for planned routes. The app cannot call `api_key` routes; use `app_token` (or `none`).
- `req.params` (path, strings), `req.query` (strings: int() them), `req.json()`, `req.headers`, `req.method`, `req.path`, `req.caller` = `{{kind: user|app_token|api_key|schedule|anonymous, id, name, email?, roles?}}` (set from the verified credential; never trust identity in the body).
- {rules.get("responses", "")} Start each docstring with the response shape ("List cases. Returns {{rows: [{{id, key, title}}], total, limit, offset}}"); it becomes the route description other builders read.
- Tables: `t = studio.db.table("name")` (literal name; the table exists before the module is saved). Rows: `{{id, created_at, updated_at, ...columns}}`. No transactions: validate before the first write. Filter `{{"col": v}}` or `{{"col": {{"$eq|$ne|$gt|$gte|$lt|$lte|$in|$contains|$exists": v}}}}`, combined with `{{"$and": [...]}}` / `{{"$or": [...]}}`. Sort: `col`, or `-col` descending.
- {rules.get("secrets", "")}
- Correctness: validate up front (400/422); unknown or malformed id -> 404, never a crash; state conflict -> 409; honor every filter, sort and limit a route advertises; list routes return the envelope `{{"rows","total","limit","offset"}}` (use `t.page(...)`); real data, no stubs. Keep each module self-contained (no imports from other modules).

SDK (`t` a table, `res` a resource):
{nl.join(_sdk_lines())}

Optional `studio.resource` gives standard routes from a table: `res = studio.resource(studio.db.table("expenses"), columns=[...], filters=None, sort="-created_at", search=["title"], states={{"field": "status", "initial": "draft", "final": ["paid"], "transitions": [{{"from": "draft", "to": "submitted"}}]}}, refs=None, archive=None, audit=studio.db.table("audit_log"), audit_columns=None, idempotent_key=None)`; call its handlers from your own `@api` functions. Hooks: {hooks}. Errors: `from studio.resources import error; raise error(status, code, message, field)` (codes `not_found`, `validation_error`, `unique_violation`, `illegal_transition`). A transition sets `{{to}}_at` / `{{to}}_by` columns when they exist.

## 3. The app's client
Each route with `auth` `none`/`app_token` is an operation of the generated typed client `api/client.ts` (provided; do not write it): `import {{ api, ApiError }} from './api/client'` (from `views/X.tsx`: `'../api/client'`). Operation name = the route's `name=`, else method + camel-cased path (`POST /requests/{{id}}/decision` -> `postRequestsByIdDecision`). Call every operation as `api.<name>(...)` directly (never alias, spread or hand-write `fetch()`).
- Shape `api.<name>(params?, body?, query?)`; an argument exists only when the route needs it. `params` (`{{id: 5}}`) only when the path has placeholders; a route without path params takes the body first: `api.createRequest({{item, quantity}})`. POST/PUT/PATCH: `(params?, body?, query?)`; GET/DELETE: `(params?, query?)`: `api.getOrder({{id: 5}})`, `api.listOrders({{status: 'open'}})`, `api.decide({{id: 5}}, {{note}})`.
- Sends the app token, parses JSON, throws `ApiError {{status, body}}` on non-2xx (`body` is your route's `{{error, message, field?}}`). List routes answer `{{rows, total, limit, offset}}`. Untyped responses are `any`: use the exact fields your handlers return.

## 4. The React app
Sandboxed iframe with React 18, ReactDOM and Tailwind preloaded; it mounts the default export of `App.tsx` (`export default function App()`). No index.html/index.tsx/index.css/bootstrap; no npm packages beyond React; handle loading/error states.
Sandbox: `html, body, #root` are `height:100%; overflow:clip`. Outermost wrapper `h-full flex flex-col` (NEVER `h-screen`/`100vh`); scroll areas `flex-1 min-h-0 overflow-y-auto`. No `position:fixed` for layout (only `fixed inset-0 z-50` overlays). Scroll with `ref.current.scrollTop`, never `scrollIntoView()`/`window.scrollTo()`. In-app routing with `useState` (no pushState). No `EventSource`/`WebSocket`: poll. `fetch()` is proxied; no streaming, `AbortController` signal or `FormData`. Downloads: `Blob` + `URL.createObjectURL` + `<a download>`. Images: absolute URLs or inline SVG. Return cleanup from every `useEffect` that sets timers/listeners.
Files: relative `path` (`App.tsx`, `views/Review.tsx`); each starts with a `/** ... */` comment (what it is), every exported component has a JSDoc; keep files under ~250 lines.

### Studio UI kit (`kit/`, provided, read-only; import from `'./kit'`, from `views/X.tsx` `'../kit'`)
Props are exact; your code is type-checked (a prop shown without a type is React.ReactNode, a callback or an object type: see its use; `className?` is omitted). Backend-bound blocks (fetch, loading/empty/error, toasts built in) take `resource={{def}}` (a ResourceDef, or the name registered with `registerResources({{name: def}})`) and read lists as `{{rows,total,limit,offset}}`. ResourceDef: `{{name, label, singular, titleField, fields:[{{name,label,type:'string|text|integer|number|boolean|datetime',required?,enum?,ref?:{{client,labelField}},readOnly?,hidden?,help?,filter?}}], client:{{list(q)=>Promise<{{rows,total,limit,offset}}>, get?(id), create?(body), update?(id,body), remove?(id), transition?(id,to,body)}}, states?:{{field, initial?, final?, transitions:[{{from,to,label?,tone?,comment?:'optional|required',commentField?}}]}}, search?:boolean}}`; build `client` from `api.<operation>` calls (`list: (q) => api.listRequests(q)`). Row type `BaseRow = {{id:number, created_at?, updated_at?}}`. Blocks colour themselves from CSS variables (`--primary --card --border --radius --foreground --muted --danger --success ...`). Mount `<Toaster/>` once for `toast.success/error/info` (AppShell mounts its own host; `notify(text, 'ok'|'error')`).
Generic kit types need their type argument (a bare `ResourceDef` is a type error; use `ResourceDef<BaseRow>` or `ResourceDef<MyRow>`): {", ".join(_kit_generics())}.
Components:
{nl.join(comps)}
Hooks and helpers:
{nl.join(helpers)}

## 5. Preview users, roles, forms
The preview's "View as" switch sends requests as one of these users, so `req.caller.id` is exactly one of: {PREVIEW_USERS_TEXT}.
- Seed rows of any role table (staff / managers / approvers / admins, whatever the plan calls it) use THESE ids as the person key with the matching role (`alice` staff, `bob` manager, `carol` admin); never invent ids such as `mgr-ana`; a name column holds Alice / Bob / Carol. Role checks compare `req.caller.id` / `req.caller.roles` with that table.
- When the plan has roles, the app needs a screen to manage the role table (list, add, change role, remove), gated to admin.
- Validation feedback: every input the backend or form refuses shows its message next to that field (`ApiError.body.field` / `.message`), not only a toast; clear a field's error as soon as it changes.
- Fixed choice lists the owner may change (codes, categories, statuses to pick from) belong in a table the app can manage, not hard-coded constants.

## 5b. Scenario tests
Also emit `tests`: 4-10 scenarios the platform runs against your routes after loading, as the preview users, one per plan rule and per role path (the happy path and each refusal, e.g. a manager approves -> approved; the requester approving their own request -> refused). Scenario = `{{name, steps:[{{as, op, input, expect, save}}]}}`: `as` a preview user id, `op` a route `name=`, `input` a flat object (path placeholders by name; the rest is the query for GET/DELETE or the body), `expect` `{{status?: 201 or "2xx"/"4xx", error?: code, fields?: {{"status": "approved"}}}}` (default 2xx; `fields` keys may be dotted paths), `save` `{{var: "path.in.response"}}` to reuse a value later as `"$var"`. Use only seeded rows or rows the scenario creates, and change only rows it created. Names/codes a scenario creates must include `$uniq` (e.g. "Suite $uniq") so they never collide with seed rows or earlier runs. Assert codes and fields, never timestamps or generated text.

## 6. Output (`emit_bundle`)
```
{{"tables":[{{"name":"...","description":"...","columns":[{{"name":"...","type":"string","required":true}}]}}],
 "seed":{{"<table>":[{{"col":"value"}}]}},
 "module":{{"name":"requests","source":"<python source>"}},
 "app":{{"name":"...","files":[{{"path":"App.tsx","content":"..."}},{{"path":"views/X.tsx","content":"..."}}]}},
 "tests":[{{"name":"manager approves","steps":[{{"as":"alice","op":"createRequest","input":{{"item":"Pens"}},"expect":{{"status":201}},"save":{{"rid":"id"}}}},{{"as":"bob","op":"decide","input":{{"id":"$rid","decision":"approve"}},"expect":{{"fields":{{"status":"approved"}}}}}}]}}]}}
```
Row ids are assigned on insert: in seed rows a `ref` column holds the 1-based position of the parent's seed row (`"suite_id": 2` = the 2nd suite seeded), and scenarios never hardcode ids (list, then `save`). Tables are created in order, then seed rows, then the module is saved; its routes are available to the app by `name=`. `App.tsx` is the entry. The reply is checked by running it: tables created, module saved, every route called once, the TSX type-checked.
"""
    extras = [x for x in (fn() for fn in BRIEF_SECTIONS) if x]
    return out + ("\n" + "\n\n".join(extras) + "\n" if extras else "")


BRIEF_SECTIONS: list[Callable[[], str]] = []
"""Extension point: each callable returns a markdown section (``## ...``) appended to the end of :func:`brief`, or ``""`` for none
(e.g. database-schema context). Register with ``BRIEF_SECTIONS.append(fn)``."""


# ── tools ────────────────────────────────────────────────────────────────────

_COLUMN = {"type": "object", "required": ["name", "type"],
           "properties": {"name": {"type": "string"}, "type": {"type": "string"}, "required": {"type": "boolean"},
                          "default": {}, "enum": {"type": "array", "items": {}}, "unique": {"type": "boolean"},
                          "index": {"type": "boolean"}, "ref": {"type": "string"}, "description": {"type": "string"}}}
_TABLE = {"type": "object", "required": ["name", "columns"],
          "properties": {"name": {"type": "string"}, "description": {"type": "string"},
                         "columns": {"type": "array", "items": _COLUMN}}}
_MODULE = {"type": "object", "required": ["name", "source"],
           "properties": {"name": {"type": "string"}, "source": {"type": "string"}}}
_FILE = {"type": "object", "required": ["path", "content"],
         "properties": {"path": {"type": "string"}, "content": {"type": "string"}}}
_STEP = {"type": "object", "required": ["as", "op"],
         "properties": {"as": {"type": "string", "description": "preview user id: alice, bob or carol"},
                        "op": {"type": "string", "description": "route operation name (name=)"},
                        "input": {"type": "object"},
                        "expect": {"type": "object", "properties": {"status": {}, "error": {"type": "string"}, "fields": {"type": "object"}}},
                        "save": {"type": "object", "additionalProperties": {"type": "string"}}}}
_TESTS = {"type": "array", "description": "scenario tests the platform runs against the routes after loading",
          "items": {"type": "object", "required": ["name", "steps"],
                    "properties": {"name": {"type": "string"}, "steps": {"type": "array", "items": _STEP}}}}
_SEED = {"type": "object", "additionalProperties": {"type": "array", "items": {"type": "object"}}}

EMIT_BUNDLE: dict[str, Any] = {
    "name": "emit_bundle",
    "description": "Emit the whole app: tables, seed rows, one Python backend module and the React app files.",
    "parameters": {"type": "object", "required": ["tables", "module", "app"],
                   "properties": {"tables": {"type": "array", "items": _TABLE}, "seed": _SEED, "module": _MODULE,
                                  "app": {"type": "object", "required": ["files"],
                                          "properties": {"name": {"type": "string"},
                                                         "files": {"type": "array", "items": _FILE}}},
                                  "tests": _TESTS}}}
EMIT_VIEWS: dict[str, Any] = {
    "name": "emit_views",
    "description": "Emit the React files (views and helpers) of part of the app.",
    "parameters": {"type": "object", "required": ["app"],
                   "properties": {"app": {"type": "object", "required": ["files"], "properties": {"files": {"type": "array", "items": _FILE}}}}}}
_EDIT = {"type": "object", "required": ["old", "new"],
         "properties": {"old": {"type": "string", "description": "exact text to replace; must occur exactly once in the file as it is now"},
                        "new": {"type": "string", "description": "its replacement (empty deletes it)"}}}
_CHANGE_FILE = {"type": "object", "required": ["path"],
                "properties": {"path": {"type": "string"},
                               "content": {"type": "string", "description": "complete new content (a new or mostly rewritten file)"},
                               "edits": {"type": "array", "items": _EDIT, "description": "targeted replacements in an existing file"}}}
_CHANGE_MODULE = {"type": "object", "required": ["name"],
                  "properties": {"name": {"type": "string"},
                                 "source": {"type": "string", "description": "complete new source (a new or mostly rewritten module)"},
                                 "edits": {"type": "array", "items": _EDIT, "description": "targeted replacements in an existing module"}}}
EMIT_CHANGES: dict[str, Any] = {
    "name": "emit_changes",
    "description": ("Emit ONLY the parts of the app that change: tables (the full desired column list of a new or changed table), "
                    "seed rows for new tables, modules and app files (for an existing one `edits`: exact-string replacements, each `old` "
                    "occurring exactly once; for a new or mostly rewritten one the complete `content` / `source`; `delete` lists removed paths) "
                    "and `tests` (scenarios by name; a changed or new one replaces / joins the existing). `message` is the one-line answer to the user."),
    "parameters": {"type": "object",
                   "properties": {"message": {"type": "string"}, "tables": {"type": "array", "items": _TABLE}, "seed": _SEED,
                                  "modules": {"type": "array", "items": _CHANGE_MODULE},
                                  "tests": _TESTS,
                                  "app": {"type": "object", "properties": {"files": {"type": "array", "items": _CHANGE_FILE},
                                                                           "delete": {"type": "array", "items": {"type": "string"}}}}}}}
READ_FILES: dict[str, Any] = {
    "name": "read_files",
    "description": ("Read parts of the app that were not shown: app file paths, `backend/<module>.py`, `table:<name>`, `test:<name>`, "
                    "`kit/<file>`. Use it before editing code you have not seen."),
    "parameters": {"type": "object", "required": ["paths"], "properties": {"paths": {"type": "array", "items": {"type": "string"}}}}}
READ_ROUNDS = 3                 # extra model rounds a change call may spend reading files before it must answer
READ_CHARS = 40_000             # text returned by one read round
EDIT_CONTEXT_CHARS = 28_000     # file text sent up front with a chat edit
FIX_CONTEXT_CHARS = 40_000      # ... with a fix pass


# ── the api-call scan (route access) ─────────────────────────────────────────

_UI_EXT = (".ts", ".tsx", ".js", ".jsx")


def _strip_comments(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"(?m)(^|\s)//.*$", r"\1", src)


def scan_api_calls(files: list[dict[str, str]]) -> tuple[set[str], bool]:
    """``(operations, dynamic)``: the ``api.<op>`` operations the app's code uses, and whether it uses ``api`` in a way the scan
    cannot resolve (aliased, spread, indexed, passed on). Whitespace and line breaks around the dot are tolerated
    (``api`` newline ``.getX()``), references without a call count (``list: api.listX``), and so do destructured uses
    (``const { a, b: c } = api``) and an ``api as x`` import alias."""
    ops: set[str] = set()
    dynamic = False
    for f in files:
        if not f["path"].endswith(_UI_EXT) or f["path"].startswith("kit/") or f["path"] == CLIENT_PATH:
            continue
        code = _strip_comments(f["content"])
        names = {"api"} | set(re.findall(r"\bapi\s+as\s+(\w+)", code))
        aliases = re.findall(r"(?:const|let|var)\s+(\w+)\s*=\s*api\s*(?:;|,|\n|$)", code)
        if aliases:
            dynamic = True
            names |= set(aliases)
        body = re.sub(r"(?m)^\s*(?:import|export)\b[^;'\"]*?\bfrom\s+['\"][^'\"]+['\"]\s*;?", "", code)
        alt = "|".join(re.escape(n) for n in sorted(names))
        ops |= set(re.findall(rf"(?<![\w$.])(?:{alt})\s*\??\.\s*([A-Za-z_$][\w$]*)", body))
        for m in re.finditer(rf"\{{([^{{}}]*)\}}\s*=\s*(?:{alt})\b(?!\s*[.(\[])", body):
            for part in m.group(1).split(","):
                name = part.strip().split(":")[0].split("=")[0].strip()
                if name.startswith("..."):
                    dynamic = True
                elif name:
                    ops.add(name)
        if re.search(rf"(?<![\w$.])(?:{alt})\s*\[|\.\.\.\s*(?:{alt})\b|(?<![\w$.])(?:{alt})\s*[,)]", body):
            dynamic = True
    return ops, dynamic


# ── bundles ──────────────────────────────────────────────────────────────────


def normalize_bundle(raw: Any) -> dict[str, Any]:
    """The internal bundle form from a model reply (``module`` or ``modules``; missing parts empty)."""
    raw = raw if isinstance(raw, dict) else {}
    mods = [m for m in (raw.get("modules") or ([raw["module"]] if isinstance(raw.get("module"), dict) else [])) if isinstance(m, dict)]
    app = raw.get("app") if isinstance(raw.get("app"), dict) else {}
    seed = raw.get("seed") if isinstance(raw.get("seed"), dict) else {}
    return {"tables": [t for t in raw.get("tables") or [] if isinstance(t, dict)],
            "seed": {k: [r for r in v if isinstance(r, dict)] for k, v in seed.items() if isinstance(v, list)},
            "modules": [{"name": str(m.get("name") or ""), "source": str(m.get("source") or "")} for m in mods],
            "app": {"name": str(app.get("name") or ""),
                    "files": [{"path": str(f.get("path") or "").lstrip("./"), "content": str(f.get("content") or "")}
                              for f in app.get("files") or [] if isinstance(f, dict) and f.get("path")]},
            "tests": normalize_tests(raw.get("tests"))}


def normalize_tests(raw: Any) -> list[dict[str, Any]]:
    from .app_autotest import normalize_tests as nt

    return nt(raw)


def merge_changes(bundle: dict[str, Any], changes: Any) -> dict[str, Any]:
    """``bundle`` with the changed parts of ``changes`` applied: tables and modules by name, files by path (``app.delete``
    removes), seed rows by table. Returns a new bundle."""
    ch = normalize_bundle(changes)
    deleted = [str(p) for p in ((changes or {}).get("app") or {}).get("delete") or []] if isinstance(changes, dict) else []
    out = copy.deepcopy(bundle)
    for key in ("tables", "modules"):
        by = {x["name"]: i for i, x in enumerate(out[key])}
        for x in ch[key]:
            if x["name"] in by:
                out[key][by[x["name"]]] = x
            else:
                by[x["name"]] = len(out[key])
                out[key].append(x)
    files = {f["path"]: f for f in out["app"]["files"]}
    for f in ch["app"]["files"]:
        files[f["path"]] = f
    for p in deleted:
        files.pop(p.lstrip("./"), None)
    out["app"]["files"] = list(files.values())
    if ch["app"]["name"]:
        out["app"]["name"] = ch["app"]["name"]
    out["seed"] = {**out["seed"], **ch["seed"]}
    if ch["tests"]:
        from .app_autotest import merge_tests
        out["tests"] = merge_tests(out.get("tests") or [], ch["tests"])
    return out


def bundle_text(bundle: dict[str, Any], *, seed: bool = False) -> str:
    """The bundle as the model reads it back (tables, module sources, app files; kit and generated client excluded)."""
    files = [f for f in bundle["app"]["files"] if not f["path"].startswith("kit/") and f["path"] != CLIENT_PATH]
    body: dict[str, Any] = {"tables": bundle["tables"], "modules": bundle["modules"],
                            "app": {"name": bundle["app"].get("name"), "files": files}}
    if bundle.get("tests"):
        body["tests"] = bundle["tests"]
    if seed:
        body["seed"] = bundle["seed"]
    return json.dumps(body, ensure_ascii=False)


def _drop_trailing_commas(t: str) -> str:
    """``t`` without the commas that sit right before a closing ``}`` / ``]`` (outside string literals)."""
    out: list[str] = []
    in_str = esc = False
    for i, ch in enumerate(t):
        if in_str:
            out.append(ch)
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "," and re.match(r"\s*[}\]]", t[i + 1:i + 40]):
            continue
        out.append(ch)
    return "".join(out)


def _json_from_text(text: str | None) -> Any:
    """The JSON value in a model's plain-text reply: ``` fences stripped, the outermost ``{...}`` taken out of any prose around it,
    and trailing commas tolerated. None when nothing parses."""
    t = (text or "").strip()
    cands = [t]
    a, b = t.find("{"), t.rfind("}")
    if a != -1 and b > a:
        cands.append(t[a:b + 1])               # the outermost object, whatever fences or prose surround it
    fenced = re.search(r"```[A-Za-z0-9_-]*[ \t]*\n?(.*?)```", t, re.S)
    if fenced:
        cands.append(fenced.group(1).strip())
    for c in cands:
        for attempt in (c, None):
            try:
                return json.loads(attempt if attempt is not None else _drop_trailing_commas(c))
            except ValueError:
                continue
    return None


class WriteError(Exception):
    """The model produced no usable bundle."""

    def __init__(self, message: str, truncated: bool = False) -> None:
        super().__init__(message)
        self.truncated = truncated


async def _ask(system: str, user: str, tool: dict[str, Any], *, phase: str, timer: Timer) -> dict[str, Any]:
    """One model call that must answer with ``tool``; returns its arguments. A model that cannot do tool calls (it matches
    :func:`no_tools`, or the provider refuses the tool) is asked for the same arguments as one JSON object in plain text."""
    return (await _ask_any(system, user, [tool], phase=phase, timer=timer))[1]


async def _ask_any(system: str, user: str, tools: list[dict[str, Any]], *, phase: str, timer: Timer) -> tuple[str, dict[str, Any]]:
    """Like :func:`_ask` with several tools on offer: ``(tool name, arguments)`` of the one the model called (the first tool is the answer)."""
    from ..engine import llm_provider

    name = model()
    if not no_tools(name):
        timer.call(phase)
        try:
            resp = await llm_provider.call_llm(
                name, [{"role": "system", "content": system}, {"role": "user", "content": user}], temperature=0.2, tools=tools,
                tool_choice="required", max_tokens=_max_tokens())
        except Exception as e:  # noqa: BLE001 - only a "tools not supported" refusal changes the way we ask
            if isinstance(e, llm_provider.OutputTruncatedError) or not _tools_unsupported(e):
                raise
            logger.info("one-shot: the model does not take tool calls; asking for plain-text JSON")
            _NO_TOOLS.add(name.lower())
        else:
            return _tool_args(resp, tools, llm_provider)
    return await _ask_json(system, user, tools, phase=phase, timer=timer)


def _tool_args(resp: Any, tools: list[dict[str, Any]], llm_provider: Any) -> tuple[str, dict[str, Any]]:
    cut = llm_provider.is_truncated(resp) if hasattr(llm_provider, "is_truncated") else bool(getattr(resp, "output_truncated", False))
    names = [t["name"] for t in tools]
    call = next((tc for tc in getattr(resp, "tool_calls", None) or [] if tc.name in names), None)
    if call is not None and isinstance(call.args, dict) and call.args and not getattr(resp, "malformed_tool_call", False):
        return call.name, call.args
    args = _json_from_text(getattr(resp, "text", None)) if call is None else None
    if isinstance(args, dict) and args:
        return _which_tool(args, tools)
    raise WriteError("the reply was cut off by the output limit" if cut else f"the model did not call {names[0]} with a usable answer",
                     truncated=cut)


def _which_tool(args: dict[str, Any], tools: list[dict[str, Any]]) -> tuple[str, dict[str, Any]]:
    """The tool a plain-text JSON reply is for: ``{"read_files": [...]}`` / ``{"paths": [...]}`` is a read when that tool is on offer."""
    if len(tools) > 1 and set(args) <= {"read_files", "paths"}:
        paths = args.get("read_files", args.get("paths"))
        return "read_files", {"paths": paths if isinstance(paths, list) else paths.get("paths", []) if isinstance(paths, dict) else []}
    return tools[0]["name"], args


async def _ask_json(system: str, user: str, tools: list[dict[str, Any]] | dict[str, Any], *, phase: str, timer: Timer) -> tuple[str, dict[str, Any]]:
    """The no-tools form of :func:`_ask_any`: the answer tool's argument schema goes in the prompt, the reply is one JSON object
    (or ``{"read_files": [paths]}`` when a read is on offer)."""
    from ..engine import llm_provider

    tools = [tools] if isinstance(tools, dict) else tools
    tool = tools[0]
    sys2 = (system + "\n\n## Reply format (no tools are available)\n"
            f"Wherever this says to call `{tool['name']}`, instead reply with ONLY the arguments of that call as ONE JSON object: no prose, "
            "no markdown fences, strings escaped properly (newlines as \\n). JSON schema of the object:\n"
            + json.dumps(tool["parameters"], separators=(",", ":")))
    if len(tools) > 1:
        sys2 += ("\nTo read files before answering, reply instead with ONLY {\"read_files\": [\"path\", ...]} (paths as the file index lists them); "
                 "you are then shown their text and asked again.")
    timer.call(phase)
    resp = await llm_provider.call_llm(
        model(), [{"role": "system", "content": sys2}, {"role": "user", "content": user}], temperature=0.2, max_tokens=_max_tokens())
    cut = llm_provider.is_truncated(resp) if hasattr(llm_provider, "is_truncated") else bool(getattr(resp, "output_truncated", False))
    args = _json_from_text(getattr(resp, "text", None))
    if isinstance(args, dict) and args:
        return _which_tool(args, tools)
    raise WriteError("the reply was cut off by the output limit" if cut else "the model's reply held no usable JSON object", truncated=cut)


# ── the plan, as the writer reads it ─────────────────────────────────────────


def _prune(v: Any) -> Any:
    if isinstance(v, dict):
        return {k: p for k, x in v.items() if (p := _prune(x)) not in (None, "", [], {})}
    if isinstance(v, list):
        return [_prune(x) for x in v]
    return v


def plan_text(plan: dict[str, Any]) -> str:
    return json.dumps(_prune(plan), ensure_ascii=False, separators=(",", ":"))


def plan_size(plan: dict[str, Any]) -> tuple[int, int]:
    """``(routes, tables)`` of a plan."""
    return len(plan.get("routes") or []), len(((plan.get("data") or {}).get("tables")) or [])


def is_big(plan: dict[str, Any]) -> bool:
    r, t = plan_size(plan)
    return r > BIG_PLAN_ROUTES or t > BIG_PLAN_TABLES


def operation_names(plan: dict[str, Any]) -> list[tuple[str, str]]:
    """``[(route key, operation name)]`` for every planned route, so parallel writers agree on the client's operation names."""
    from .api_route_service import default_name

    out = []
    for r in plan.get("routes") or []:
        method, _, path = str(r.get("key") or "").partition(" ")
        if path:
            out.append((r["key"], default_name(method.upper(), path.strip())))
    return out


def primary_module(plan: dict[str, Any]) -> str | None:
    mods = [m for m in plan.get("modules") or [] if m.get("kind") != "shared"] or list(plan.get("modules") or [])
    return mods[0]["key"] if mods else None


def _pascal(text: str) -> str:
    return "".join(w[:1].upper() + w[1:] for w in re.split(r"[^A-Za-z0-9]+", text) if w) or "Screen"


def _screen_id(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "screen"


def _stem(path: str) -> str:
    return path.rsplit("/", 1)[-1].rsplit(".", 1)[0]


def features(plan: dict[str, Any], app_key: str | None = None) -> list[dict[str, Any]]:
    """The plan split by feature area: one per non-shared module, with the tables it owns and the screens whose routes are mostly
    its own (a screen without routes goes to the first). Empty when the plan has fewer than two such modules."""
    mods = [m for m in plan.get("modules") or [] if m.get("kind") != "shared"]
    if len(mods) < 2:
        return []
    keys = [m["key"] for m in mods]
    route_module = {r["key"]: r.get("module") for r in plan.get("routes") or []}
    feats: dict[str, dict[str, Any]] = {k: {"key": k, "tables": [], "screens": [], "routes": []} for k in keys}
    for t in ((plan.get("data") or {}).get("tables")) or []:
        owner = t.get("owner") if t.get("owner") in feats else next((m["key"] for m in mods if t["name"] in (m.get("owns") or [])), keys[0])
        feats[owner]["tables"].append(t["name"])
    for r in plan.get("routes") or []:
        if r.get("module") in feats:
            feats[r["module"]]["routes"].append(r["key"])
    app = next((a for a in plan.get("apps") or [] if app_key in (None, a.get("key"))), None) or {}
    for sc in app.get("screens") or []:
        votes = [route_module.get(k) for k in sc.get("routes") or [] if route_module.get(k) in feats]
        owner = max(dict.fromkeys(votes), key=votes.count) if votes else keys[0]
        feats[owner]["screens"].append({"name": sc["name"], "id": _screen_id(sc["name"]), "path": f"views/{_pascal(sc['name'])}.tsx",
                                        "routes": sc.get("routes") or [], "purpose": sc.get("purpose") or ""})
    return list(feats.values())


def assemble_app(title: str, screens: list[dict[str, Any]]) -> str:
    """App.tsx for a split build: the AppShell over every feature's view (code, not a model call)."""
    imports = "\n".join(f"import {_pascal(_stem(s['path']))} from './{s['path'][:-4]}';" for s in screens)
    items = ",\n".join(f"        {{ id: {json.dumps(s['id'])}, label: {json.dumps(s['name'])}, "
                       f"render: (go) => <{_pascal(_stem(s['path']))} go={{go}} /> }}" for s in screens)
    return ("/** App entry: the navigation shell over the feature views (assembled by the builder from the plan's screens). */\n"
            f"import {{ AppShell }} from './kit';\n{imports}\n\n"
            "/** The app: side navigation with one screen per plan screen. */\n"
            f"export default function App() {{\n  return (\n    <AppShell\n      title={{{json.dumps(title)}}}\n"
            f"      screens={{[\n{items},\n      ]}}\n    />\n  );\n}}\n")


# ── write ────────────────────────────────────────────────────────────────────


def _context(plan: dict[str, Any], app_key: str | None, existing: dict[str, Any] | None, module_name: str | None) -> str:
    names = "\n".join(f"- {k} -> `name=\"{n}\"`" for k, n in operation_names(plan))
    parts = ["## Approved plan (JSON)", plan_text(plan), "",
             "## Operation names (use `name=` exactly as listed for these planned routes)", names or "(no planned routes)", ""]
    if module_name:
        parts += [f"## Module name\nName the backend module `{module_name}`. Fold any shared helper module of the plan into it.", ""]
    parts += [f"## App\nBuild the plan app `{app_key}`." if app_key else "## App\nThe plan has no app: write the tables and the module only.", ""]
    if existing:
        parts += ["## Already built (do not rewrite): the tables, modules and app files the project has", bundle_text(existing), ""]
    parts += extra_context(plan, existing)         # extra sections registered in CONTEXT_SECTIONS
    return "\n".join(parts)


async def _write_single(plan: dict[str, Any], app_key: str | None, existing: dict[str, Any] | None, timer: Timer) -> dict[str, Any]:
    user = (_context(plan, app_key, existing, primary_module(plan)) +
            "\nWrite the whole app now: call `emit_bundle`. Seed a few realistic rows only where the plan calls for reference data.")
    return normalize_bundle(await _ask(brief(), user, EMIT_BUNDLE, phase="write", timer=timer))


async def _write_feature(plan: dict[str, Any], feat: dict[str, Any], all_features: list[dict[str, Any]], app_key: str | None,
                         timer: Timer) -> dict[str, Any]:
    others = "\n".join(f"- `{f['key']}`: tables {f['tables'] or '-'}; {len(f['routes'])} route(s); screens {[s['name'] for s in f['screens']] or '-'}"
                       for f in all_features if f is not feat)
    views = "\n".join(f"- `{s['path']}` (screen id `{s['id']}`, \"{s['name']}\"): routes {s['routes']}. {s['purpose']}"
                      for s in feat["screens"]) or "(none)"
    user = (_context(plan, app_key, None, feat["key"]) +
            f"\n## Your part: the `{feat['key']}` feature area\n"
            "Other engineers write the other feature areas at the same time from the same plan; you write ONLY yours:\n"
            f"- the module `{feat['key']}` with the routes of the plan whose module is `{feat['key']}`;\n"
            f"- the tables you own: {feat['tables'] or 'none'} (the other tables are created by the others and exist when your code runs):\n{others or '(none)'}\n"
            "- seed rows only for your tables;\n"
            f"- these view files, and any helper files under `lib/{feat['key']}/` (no other paths):\n{views}\n"
            "Each view is `export default function <Name>({ go }: { go?: (screenId: string) => void })` (go switches screen by id). "
            "Do NOT write App.tsx (it is assembled from the screens). The other areas' routes are available through the client under the "
            "operation names above.\nCall `emit_bundle`.")
    out = normalize_bundle(await _ask(brief(), user, EMIT_BUNDLE, phase="write", timer=timer))
    out["modules"] = [{**m, "name": feat["key"]} for m in out["modules"][:1]] + out["modules"][1:]
    own = tuple(["views/", f"lib/{feat['key']}/"])
    out["app"]["files"] = [f for f in out["app"]["files"] if f["path"].startswith(own)]
    return out


def _tables_line(tables: list[dict[str, Any]]) -> str:
    return "\n".join(f"- {t.get('name')}(" + ", ".join(f"{c.get('name')}:{c.get('type')}" for c in t.get("columns") or [] if isinstance(c, dict)) + ")"
                     for t in tables) or "(none)"


def route_digest(plan: dict[str, Any], modules: list[dict[str, str]]) -> str:
    """The routes the UI calls, from the written module source(s) (operation name, verb, path, response shape from the docstring);
    the plan's operation names when a source cannot be parsed."""
    from .api_route_service import default_name
    from .code_module_service import discover_routes

    lines: list[str] = []
    for m in modules:
        try:
            found = discover_routes(m["source"])
        except ProjectError:
            continue
        for d in found:
            if d["auth"] == "api_key":
                continue
            doc = " ".join(str(d.get("description") or "").split())[:170]
            lines.append(f"- `api.{d['name'] or default_name(d['method'], d['path'])}`: {d['method']} {d['path']}" + (f" -> {doc}" if doc else ""))
    return "\n".join(lines) or "\n".join(f"- `api.{n}`: {k}" for k, n in operation_names(plan))


async def _write_staged(plan: dict[str, Any], app_key: str | None, existing: dict[str, Any] | None, timer: Timer) -> dict[str, Any]:
    """Compact profile for a plan without feature areas: the backend (tables, seed, the module, scenarios) in one call, then the
    screens in groups of :data:`COMPACT_SCREENS_PER_CALL`, in parallel, each given the module's route list. App.tsx is code."""
    mod = primary_module(plan)
    user = (_context(plan, None, existing, mod) +
            "\nWrite the data tables, seed rows, the backend module and the scenario tests now: call `emit_bundle` with `app` empty "
            "(the screens are written next, from your routes). Seed a few realistic rows only where the plan calls for reference data.")
    bundle = normalize_bundle(await _ask(brief(), user, EMIT_BUNDLE, phase="write", timer=timer))
    if mod and len(bundle["modules"]) == 1:
        bundle["modules"][0]["name"] = mod
    bundle["app"] = {"name": "", "files": []}
    if app_key is None:                 # the plan has no app to write
        return bundle
    app = next((a for a in plan.get("apps") or [] if a.get("key") == app_key), None) or {}
    screens = [{"name": sc["name"], "id": _screen_id(sc["name"]), "path": f"views/{_pascal(sc['name'])}.tsx",
                "routes": sc.get("routes") or [], "purpose": sc.get("purpose") or ""} for sc in app.get("screens") or []]
    groups: list[list[dict[str, Any]]] = [screens[i:i + COMPACT_SCREENS_PER_CALL] for i in range(0, len(screens), COMPACT_SCREENS_PER_CALL)] or [[]]
    digest = route_digest(plan, bundle["modules"])
    title = app.get("name") or plan.get("title") or "App"
    sem = asyncio.Semaphore(bc.concurrency())

    async def ui(group: list[dict[str, Any]]) -> list[dict[str, str]]:
        views = "\n".join(f"- `{s['path']}` (screen id `{s['id']}`, \"{s['name']}\"): routes {s['routes']}. {s['purpose']}" for s in group)
        user = (f"## App\nThe app is \"{title}\". The backend is written; its tables:\n{_tables_line(bundle['tables'])}\n\n"
                f"## Backend routes (call them as `api.<name>(...)`)\n{digest}\n\n"
                + ("## Your part: these files\n" + views + "\nEach view is `export default function <Name>({ go }: { go?: (screenId: string) => void })` "
                   "(go switches screen by id). Helper files go under `lib/`. Do NOT write App.tsx (it is assembled from the screens).\n"
                   if group else "## Your part\nWrite `App.tsx` (the entry) and any view files it needs under `views/` and `lib/`.\n") +
                "\nCall `emit_views` with the complete files.")
        async with sem:
            out = normalize_bundle(await _ask(brief(), user, EMIT_VIEWS, phase="write", timer=timer))
        keep = ("views/", "lib/") if group else ("views/", "lib/", "App.tsx")
        return [f for f in out["app"]["files"] if f["path"].startswith(keep)]

    parts = await asyncio.gather(*[ui(g) for g in groups])
    paths: set[str] = set()
    files = [f for p in parts for f in p if f["path"] not in paths and not paths.add(f["path"])]
    if screens:
        files.insert(0, {"path": "App.tsx", "content": assemble_app(title, [s for s in screens if s["path"] in paths])})
    bundle["app"] = {"name": title, "files": files}
    return bundle


async def write_app(plan: dict[str, Any], *, app_key: str | None = None, existing: dict[str, Any] | None = None,
                    timer: Timer | None = None, log: Log | None = None, split: bool | None = None) -> dict[str, Any]:
    """The bundle for ``plan`` (normalized plan body). One model call; a big plan (> ``BIG_PLAN_ROUTES`` routes or
    > ``BIG_PLAN_TABLES`` tables) is written feature by feature, one call each, in parallel, and the entry file is assembled by
    code. A reply cut off by the output limit falls back to the split once. Raises :class:`WriteError`."""
    timer = timer or Timer()
    feats = features(plan, app_key)
    compact = profile() == "compact"
    use_split = bool(feats) and (True if compact and split is None else is_big(plan) if split is None else split)

    async def say(msg: str) -> None:
        if log is not None:
            await log(msg)

    logger.info("one-shot: %s profile", profile())
    with timer.phase("write"):
        if compact and not use_split:
            await say("Compact profile: writing the backend first, then the screens")
            return await _write_staged(plan, app_key, existing, timer)
        if not use_split:
            await say("Writing the whole app in one model call")
            try:
                return await _write_single(plan, app_key, existing, timer)
            except WriteError as e:
                if not (e.truncated and feats):
                    raise
                await say("The reply was cut off; writing it feature by feature instead")
        await say(f"Writing {len(feats)} feature area(s) in parallel: " + ", ".join(f["key"] for f in feats))
        sem = asyncio.Semaphore(bc.concurrency())

        async def one(f: dict[str, Any]) -> dict[str, Any]:
            async with sem:
                return await _write_feature(plan, f, feats, app_key, timer)

        parts = await asyncio.gather(*[one(f) for f in feats])
        bundle: dict[str, Any] = {"tables": [], "seed": {}, "modules": [], "app": {"name": "", "files": []}, "tests": []}
        seen_t: set[str] = set()
        paths: set[str] = set()
        for p in parts:
            bundle["tables"] += [t for t in p["tables"] if t.get("name") not in seen_t and not seen_t.add(t.get("name"))]
            bundle["seed"] = {**p["seed"], **bundle["seed"]}
            bundle["modules"] += p["modules"]
            bundle["tests"] += p["tests"]
            bundle["app"]["files"] += [f for f in p["app"]["files"] if f["path"] not in paths and not paths.add(f["path"])]
        screens = [s for f in feats for s in f["screens"] if s["path"] in paths]
        app = next((a for a in plan.get("apps") or [] if app_key in (None, a.get("key"))), None) or {}
        bundle["app"]["name"] = app.get("name") or plan.get("title") or ""
        if app_key is not None:
            bundle["app"]["files"].insert(0, {"path": "App.tsx", "content": assemble_app(bundle["app"]["name"] or "App", screens)})
        return bundle


# ── load ─────────────────────────────────────────────────────────────────────


def _err(where: str, code: str, message: Any) -> dict[str, str]:
    return {"where": where, "code": code, "message": str(message)[:MAX_ERROR_CHARS]}


def _project_error(where: str, e: ProjectError) -> list[dict[str, str]]:
    items = e.extra.get("errors") or e.extra.get("violations") or e.extra.get("refused") or []
    lines = [str(i.get("message") or i) if isinstance(i, dict) else str(i) for i in items][:6]
    return [_err(where, e.code, e.message + ("; " + "; ".join(lines) if lines else ""))]


def _route_event(module: str, method: str, path: str, name: str, status: str, known: set[str] | None) -> None:
    ev: dict[str, Any] = {"type": "route", "method": method, "path": path, "name": name, "status": status}
    if known is not None and module in known:
        ev["step_key"] = f"module:{module}"
    bc.emit(ev)


def _ref_table(col: dict[str, Any]) -> str | None:
    ref = col.get("ref")
    return ref.split(".", 1)[0] if isinstance(ref, str) and ref.endswith(".id") else None


def seed_order(seed: dict[str, Any], tables: list[dict[str, Any]]) -> list[str]:
    """Seeded table names, each after the seeded tables its ``ref`` columns point at (otherwise in the model's order)."""
    deps = {t.get("name"): {_ref_table(c) for c in t.get("columns") or [] if isinstance(c, dict)} for t in tables}
    out: list[str] = []

    def visit(name: str, stack: tuple[str, ...]) -> None:
        if name in out or name in stack:
            return
        for d in sorted(x for x in deps.get(name, ()) if x and x != name and x in seed):
            visit(d, stack + (name,))
        out.append(name)

    for name in seed:
        visit(name, ())
    return out


def link_seed_rows(rows: list[dict[str, Any]], columns: list[dict[str, Any]], seed_ids: dict[str, list[int]]) -> list[dict[str, Any]]:
    """Row ids are assigned on insert, so a seed row points at a parent seeded in the same load by the parent's 1-based POSITION
    in its seed list (``"suite_id": 2`` = the second suite row). Those positions become the real ids, typed like the column."""
    links = [(c["name"], seed_ids[p], c.get("type")) for c in columns
             if isinstance(c, dict) and (p := _ref_table(c)) and p in seed_ids]
    if not links:
        return rows
    out = []
    for row in rows:
        row = dict(row)
        for col, ids, typ in links:
            v = row.get(col)
            n = v if isinstance(v, int) and not isinstance(v, bool) else int(v) if isinstance(v, str) and v.isdigit() else None
            if n is not None and 1 <= n <= len(ids):
                row[col] = str(ids[n - 1]) if typ == "string" else ids[n - 1]
        out.append(row)
    return out


async def load(project_id: str, bundle: dict[str, Any], *, app_id: str | None = None, app_name: str | None = None,
               save_app: bool = True, known_modules: set[str] | None = None, actor: str = "build",
               version_prompt: str = "one-shot build", log: Log | None = None) -> dict[str, Any]:
    """Put ``bundle`` into the project. Idempotent: tables are created or migrated additively, seed rows go into EMPTY tables
    only, a module whose source is unchanged is not saved again. Returns ``{errors, modules, app_id, version_id, route_ids,
    tables}``; errors are collected (``{where, code, message}``), never raised."""
    from sqlalchemy import select
    from sqlalchemy.orm.attributes import flag_modified

    from ..db.models import AppModel, CodeModuleModel, ProjectModel
    from ..schemas.app import AppCreateRequest, AppFilePayload, AppUpdateRequest
    from . import app_builder_ai_service as ab
    from . import project_files as pf
    from .api_route_service import ApiRouteService, default_name
    from .code_module_service import CodeModuleService, discover_routes
    from .data_table_service import DataTableService

    async def say(msg: str) -> None:
        if log is not None:
            await log(msg)

    errors: list[dict[str, str]] = []
    out: dict[str, Any] = {"errors": errors, "modules": [], "app_id": app_id, "version_id": None, "route_ids": [], "tables": []}
    async with bc._factory()() as s:
        project = await s.get(ProjectModel, project_id)
        if project is None:
            raise ProjectError(404, "project_not_found", "Project not found")
        # 1. tables
        await say(f"Loading {len(bundle['tables'])} table(s)")
        tsvc = DataTableService(s)
        try:
            res = await tsvc.ensure_tables(project, bundle["tables"], actor=actor, migrate=True)
            errors += [_err(f"table:{e.get('name')}", "table_invalid", e.get("message")) for e in res["errors"]]
            errors += [_err(f"table:{r.get('table')}", "schema_change_refused", r.get("reason") or r.get("op")) for r in res["refused"]]
            out["tables"] = res["created"] + [m["table"] for m in res["migrated"]]
        except ProjectError as e:
            errors += _project_error("tables", e)
        # 2. seed rows, only into tables that are still empty
        seeded: list[str] = []
        seed = bundle.get("seed") or {}
        seed_ids: dict[str, list[int]] = {}      # table -> ids of the rows seeded now, in seed order
        for name in seed_order(seed, bundle["tables"]):
            rows = seed[name]
            t = await tsvc.table_by_name(project.id, name)
            if t is None:
                errors.append(_err(f"seed:{name}", "table_missing", f"seed rows for '{name}', which is not a table"))
                continue
            if not rows or await tsvc.row_count(t.id) > 0:
                continue
            rows = link_seed_rows(rows, t.columns, seed_ids)
            try:
                ids: list[int] = []
                for i in range(0, len(rows), SEED_CHUNK):
                    ids += [m.id for m in await tsvc.insert_rows(t, rows[i:i + SEED_CHUNK])]
                seed_ids[name] = ids
                seeded.append(name)
            except ProjectError as e:
                await s.rollback()
                errors += _project_error(f"seed:{name}", e)
        await bc._emit_tables(s, project, out["tables"] + seeded)
        # 3. modules, through the normal module save path
        await say("Saving the backend module(s)")
        msvc = CodeModuleService(s)
        module_ids: dict[str, str] = {}
        for m in bundle["modules"]:
            where = f"module:{m['name']}"
            bc.emit_file("backend", f"backend/{m['name']}.py", m["source"], done=False)
            row = (await s.execute(select(CodeModuleModel).where(CodeModuleModel.project_id == project.id,
                                                                CodeModuleModel.name == m["name"]))).scalars().first()
            try:
                if row is not None and row.source == m["source"]:
                    module_ids[m["name"]] = row.id
                else:
                    saved = (await msvc.update(project, row.id, m["source"], actor=actor) if row is not None
                             else await msvc.create(project, m["name"], m["source"], actor=actor))
                    await s.commit()
                    module_ids[m["name"]] = saved["module"]["id"]
                    for w in saved.get("warnings") or []:
                        if w.get("code") in ("unknown_column", "table_missing"):
                            errors.append(_err(where, w["code"], w.get("message")))
                    errors += [_err(where, "table_error", e.get("message")) for e in saved.get("table_errors") or []]
                bc.emit_file("backend", f"backend/{m['name']}.py", m["source"])
                routes = []
                for d in discover_routes(m["source"]):
                    name = d["name"] or default_name(d["method"], d["path"])
                    routes.append({"method": d["method"], "path": d["path"], "name": name})
                    _route_event(m["name"], d["method"], d["path"], name, "built", known_modules)
                out["modules"].append({"name": m["name"], "id": module_ids[m["name"]], "routes": routes})
            except ProjectError as e:
                await s.rollback()
                errors += _project_error(where, e)
        # 3b. the project's typed client, regenerated now that the modules' routes exist: the app (and its preview) is saved after this
        await _refresh_client(s, project, out, errors)
        # 4. the app
        app = bundle.get("app") or {}
        files = [f for f in app.get("files") or [] if not f["path"].startswith("kit/") and f["path"] != CLIENT_PATH]
        if files or app_id:
            svc = bc._app_service(s)
            if app_id is None:
                created = await svc.create_app(AppCreateRequest(name=app_name or app.get("name") or project.name,
                                                                definition={"sourceCode": None}, project_id=project.id), created_by=actor)
                app_id = created.id
                out["app_id"] = app_id
            row = await s.get(AppModel, app_id)
            if row is None or row.project_id != project.id:
                errors.append(_err("app", "app_not_found", f"app {app_id} is not in this project"))
                return out
            called, dynamic = scan_api_calls(files)
            routes_all = await ApiRouteService(s).rows(project.id)
            mine = set(module_ids.values())
            wanted = [r.id for r in routes_all if r.name in called]
            if dynamic:           # when in doubt: every route this app's module(s) own
                wanted += [r.id for r in routes_all if r.handler_module_id in mine and r.id not in wanted]
            definition = dict(row.draft_definition or {"sourceCode": None})
            studio = dict(definition.get("studio") or {})
            current = [str(x) for x in studio.get("route_ids") or []]
            merged = current + [i for i in wanted if i not in current]
            oneshot = dict(studio.get("oneshot") or {})
            names = list(dict.fromkeys(list(oneshot.get("modules") or []) + [m["name"] for m in bundle["modules"]]))
            oneshot_def: dict[str, Any] = {"modules": names}
            tests = bundle.get("tests") or oneshot.get("tests")
            if tests:
                oneshot_def["tests"] = tests
            definition["studio"] = {**studio, "route_ids": merged, "oneshot": oneshot_def}
            out["route_ids"] = merged
            if save_app and files:
                await say("Saving the app")
                payload = [AppFilePayload(path=f["path"], content=f["content"],
                                          file_type=(f["path"].rsplit(".", 1)[-1] if "." in f["path"] else "tsx"))
                           for f in files + ab.kit_files()]
                entry = next((f for f in files if re.fullmatch(r"(src/)?[Aa]pp\.tsx", f["path"])), files[0])
                slug = await pf.app_slug(s, project.id, app_id)
                for f in files:
                    bc.emit_file("ui", f"apps/{slug}/{f['path']}", f["content"])
                detail = await svc.update_app(app_id, AppUpdateRequest(
                    definition=definition, source_code=entry["content"], files=payload, create_version=True,
                    version_trigger="ai", version_prompt=version_prompt[:2000]))
                cv = getattr(detail, "current_version", None)
                out["version_id"] = str(cv.id) if cv is not None else None
                bc.emit({"type": "preview", "app_id": app_id, "version_id": out["version_id"]})
            elif definition != (row.draft_definition or {}):
                row.draft_definition = definition
                flag_modified(row, "draft_definition")
                await s.commit()
    return out


async def _refresh_client(s: Any, project: Any, out: dict[str, Any], errors: list[dict[str, str]]) -> None:
    """Regenerate the project's typed client (``api/client.ts``) after the modules were saved and check it carries every route the
    saved modules own. The order that matters to the preview: module saved -> client regenerated -> app saved -> preview event."""
    from ..routes import api_routes

    names = [r["name"] for m in out["modules"] for r in m["routes"] if r.get("name")]
    if not names:
        return
    try:
        await s.commit()                 # the route rows the module saves wrote are visible to the generator
        src, _ = await api_routes.client_source(s, project, project.default_environment)
    except Exception as e:  # noqa: BLE001 - a client that cannot be built is a finding, not a crash
        logger.warning("one-shot: the client could not be regenerated", exc_info=True)
        errors.append(_err("client", "client_failed", f"{type(e).__name__}: {e}"))
        return
    missing = [n for n in names if not re.search(rf"\b{re.escape(n)}\b", src)]
    if missing:
        errors.append(_err("client", "client_stale", "the regenerated client lacks: " + ", ".join(missing[:8])))
    out["client_ops"] = len(names) - len(missing)


# ── check ────────────────────────────────────────────────────────────────────


async def _smoke(project_id: str, routes: list[dict[str, Any]]) -> tuple[list[dict[str, str]], dict[tuple[str, str], str]]:
    """Call each route once in the draft environment, with no body and placeholder ids. A 4xx answer is a fine validation answer;
    a 5xx, ``handler_failed``, ``not_implemented`` or ``route_not_found`` is an error. Only GET routes and routes that take an
    id (answered 404 for id 0) are called, so the smoke never writes rows."""
    from ..db.models import ProjectModel
    from ..sandbox.backend import availability
    from .route_dispatcher import RouteDispatcher, RouteRequest

    if not availability()[0]:
        return [], {}
    errors: list[dict[str, str]] = []
    status: dict[tuple[str, str], str] = {}
    sem = asyncio.Semaphore(SMOKE_CONCURRENCY)

    async def one(r: dict[str, Any]) -> None:
        method, path = r["method"], r["path"]
        if method != "GET" and "{" not in path:
            return
        concrete = re.sub(r"\{[^}]+\}", "1" if method == "GET" else "0", path)
        async with sem:
            async with bc._factory()() as s:
                project = await s.get(ProjectModel, project_id)
                try:
                    res = await asyncio.wait_for(RouteDispatcher(s).handle(
                        project, RouteRequest(method=method, path=concrete), forced_env=project.default_environment), timeout=40)
                except Exception as e:  # noqa: BLE001 - a crash of the call is the finding
                    errors.append(_err(f"route:{method} {path}", "smoke_crashed", f"{type(e).__name__}: {e}"))
                    status[(method, path)] = "failing"
                    return
        body = res.body if isinstance(res.body, dict) else {}
        code = body.get("error")
        if res.status >= 500 or code in ("handler_failed", "not_implemented", "route_not_found"):
            errors.append(_err(f"route:{method} {path}", str(code or f"http_{res.status}"),
                               f"{method} {concrete} answered {res.status}: {json.dumps(body, default=str)[:400]}"))
            status[(method, path)] = "failing"
        else:
            status[(method, path)] = "passing"

    await asyncio.gather(*[one(r) for r in routes])
    return errors, status


async def check(project_id: str, bundle: dict[str, Any], loaded: dict[str, Any], *, smoke: bool = True,
                known_modules: set[str] | None = None, log: Log | None = None) -> dict[str, Any]:
    """Check what :func:`load` put in: module checks / self-test, a smoke call of each route, the app's route use and its TSX.
    Returns ``{errors, routes}`` (``routes``: ``{module, method, path, name, status}``); route events are emitted."""
    from . import builder_gates as bg
    from .api_route_service import ApiRouteService
    from .backend_templates import selftest
    from .project_service import resolve_project

    async def say(msg: str) -> None:
        if log is not None:
            await log(msg)

    errors: list[dict[str, str]] = []
    routes: list[dict[str, Any]] = []
    statuses: dict[tuple[str, str], str] = {}
    await say("Checking the backend routes")
    for m in loaded.get("modules") or []:
        where = f"module:{m['name']}"
        routes += [{"module": m["name"], **r} for r in m["routes"]]
        try:
            out = await bg.check_module(project_id, m["id"])
        except Exception as e:  # noqa: BLE001 - a check that cannot run is a finding, not a crash
            logger.warning("one-shot: the check of module %s crashed", m["name"], exc_info=True)
            errors.append(_err(where, "check_crashed", f"{type(e).__name__}: {e}"))
            continue
        errors += [_err(where, str(p.get("code") or "static"), p.get("message")) for p in out["static"]]
        errors += [_err(where, str(p.get("code") or "analysis"), p.get("message")) for p in out["analysis"]["errors"]]
        errors += [_err(where, "self_test_failed", line.lstrip("- ")) for line in selftest.failure_lines(out["self_test"].get("checks") or [])]
        statuses.update(bc.route_statuses([(r["method"], r["path"]) for r in m["routes"]], out["self_test"].get("checks") or []))
    if smoke and routes:
        serr, sstatus = await _smoke(project_id, routes)
        errors += serr
        for k, v in sstatus.items():
            if statuses.get(k) != "failing":
                statuses[k] = v
    files = [f for f in (bundle.get("app") or {}).get("files", []) if not f["path"].startswith("kit/") and f["path"] != CLIENT_PATH]
    if files:
        await say("Type-checking the app")
        called, _ = scan_api_calls(files)
        async with bc._factory()() as s:
            by_name = {r.name: r for r in await ApiRouteService(s).rows(project_id)}
            for op in sorted(called):
                r = by_name.get(op)
                if r is None:
                    errors.append(_err("app", "unknown_route", f"api.{op}() is called but the project has no route named '{op}'"))
                elif r.auth == "api_key":
                    errors.append(_err("app", "route_not_for_app", f"api.{op}: the route's auth is api_key (never in the app's client); use app_token"))
            try:
                gate = await bg.check_app_files(s, await resolve_project(s, project_id), files)
                errors += [_err("app", "tsc", m) for m in gate["tsc"]["errors"]]
                for k in ("syntax", "contract", "call_shape", "invented_literals"):
                    errors += [_err("app", k, m) for m in gate[k]]
            except Exception as e:  # noqa: BLE001
                errors.append(_err("app", "check_crashed", f"{type(e).__name__}: {e}"))
    for r in routes:
        failing = any(e["where"] in (f"module:{r['module']}", f"route:{r['method']} {r['path']}") and e["code"] not in ("self_test_failed",) for e in errors)
        r["status"] = statuses.get((r["method"], r["path"])) or ("failing" if failing else "passing")
        _route_event(r["module"], r["method"], r["path"], r["name"], r["status"], known_modules)
    return {"errors": errors[:MAX_ERRORS * 2], "routes": routes}


# ── lean change calls: index, selection, read tool, targeted edits ───────────


CONTEXT_SECTIONS: list[Callable[[dict[str, Any] | None, dict[str, Any] | None], str]] = []
"""Extension point: each callable gets ``(plan, bundle)`` (``plan`` is None in change calls) and returns a markdown section
(``## ...``) added to the user message of :func:`write_app` and of every edit / fix call, or ``""`` for none."""


_DB_CONTEXT: contextvars.ContextVar[str] = contextvars.ContextVar("oneshot_db_context", default="")


def _db_section(plan: dict[str, Any] | None, bundle: dict[str, Any] | None) -> str:
    text = _DB_CONTEXT.get()
    if not text:
        return ""
    return ("## External databases (read-only, already connected to this project)\n"
            "Query them from the module with `studio.connection(\"key\").query(sql, params, limit)`; never create tables for this data "
            "or copy it into seed rows. Use the exact table and column names below.\n" + text)


CONTEXT_SECTIONS.append(_db_section)


async def _load_db_context(project_id: str, plan: dict[str, Any] | None = None) -> None:
    """Puts the project's external-database schema summary where :func:`_db_section` reads it for this build or edit."""
    try:
        from . import connection_service
        text = await connection_service.connections_context(project_id, tables=_plan_table_mentions(plan))
    except Exception:  # noqa: BLE001 — schema context is a hint; never fail a build over it
        logger.warning("external database context unavailable", exc_info=True)
        text = ""
    _DB_CONTEXT.set(text)


def _plan_table_mentions(plan: dict[str, Any] | None) -> list[str] | None:
    if not plan:
        return None
    names = set(re.findall(r"\b([a-z_][a-z0-9_]{2,})\b", plan_text(plan).lower()))
    return sorted(names) or None


def extra_context(plan: dict[str, Any] | None, bundle: dict[str, Any] | None) -> list[str]:
    out: list[str] = []
    for fn in CONTEXT_SECTIONS:
        text = fn(plan, bundle)
        if text:
            out += [text, ""]
    return out


_CHANGE_RULES = (
    "Call `emit_changes` with ONLY what changes. For an existing file or module give `edits`: exact-string replacements `{old, new}`, each "
    "`old` occurring exactly once in the file as it is now (take enough surrounding lines to be unique; keep edits small, never re-send "
    "unchanged code). Give the complete `content` / `source` only for a NEW file or when most of a file changes. A new or changed table "
    "carries its full desired column list (the platform migrates new columns). Put the one-line answer in `message`; for a question change "
    "nothing. You see only part of the app: call `read_files` (paths from the index, `table:<name>`, `test:<name>`, `kit/<file>`) before relying "
    "on code you have not seen; never guess a file's text.")
_EXPORT_RE = re.compile(r"^export\s+(default\s+)?(?:async\s+)?(?:function|const|class|interface|type|enum)\s+(\w+)", re.M)
_LEAD_COMMENT_RE = re.compile(r"\A\s*/\*\*?(.*?)\*/", re.S)
_ROLE_NOISE = {"role", "roles", "caller", "id", "name", "email", "kind", "user", "error", "message", "detail", "forbidden"}


def _kb(text: str) -> str:
    return f"{len(text) / 1024:.1f}KB"


def _file_line(path: str, content: str) -> str:
    out = f"- {path} ({_kb(content)})"
    m = _LEAD_COMMENT_RE.match(content)
    if m:
        doc = _sentence(re.sub(r"(?m)^\s*\*\s?", "", m.group(1)), 90)
        out += f": {doc}" if doc else ""
    names = [("default " if d else "") + n for d, n in _EXPORT_RE.findall(content)]
    return out + (" [exports " + ", ".join(names[:8]) + "]" if names else "")


def _module_routes(source: str) -> list[str]:
    """One line per route of a module source: ``METHOD path -> opName [auth] roles: ... - docstring first sentence``."""
    import ast

    from .api_route_service import default_name
    from .code_module_service import discover_routes

    try:
        found = discover_routes(source)
        tree = ast.parse(source)
    except (ProjectError, SyntaxError):
        return []
    segs = {n.name: ast.get_source_segment(source, n) or "" for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    out = []
    for d in found:
        roles: list[str] = []
        for line in segs.get(d["function"], "").splitlines():
            if "role" in line.lower():
                roles += [w for w in re.findall(r"[\"']([a-z][a-z_]{2,19})[\"']", line) if w not in _ROLE_NOISE]
        doc = _sentence(d.get("description"), 100)
        out.append(f"  - {d['method']} {d['path']} -> {d['name'] or default_name(d['method'], d['path'])} [{d['auth']}]"
                   + (f" roles: {', '.join(dict.fromkeys(roles))}" if roles else "") + (f" - {doc}" if doc else ""))
    return out


def _table_line(t: dict[str, Any]) -> str:
    cols = []
    for c in t.get("columns") or []:
        if not isinstance(c, dict):
            continue
        x = f"{c.get('name')}:{c.get('type')}" + ("!" if c.get("required") else "")
        if c.get("enum"):
            x += "{" + "|".join(str(v) for v in c["enum"][:6]) + ("|…" if len(c["enum"]) > 6 else "") + "}"
        if c.get("ref"):
            x += f"->{c['ref']}"
        cols.append(x)
    return f"- {t.get('name')}(" + ", ".join(cols) + ")"


def bundle_index(bundle: dict[str, Any]) -> str:
    """A compact map of the app: tables with columns (``!`` required), backend modules with their routes (method, path, operation
    name, auth, roles, response shape), app files with a one-line summary and exports, and the scenario names."""
    files = [f for f in bundle["app"]["files"] if not f["path"].startswith("kit/") and f["path"] != CLIENT_PATH]
    parts = ["Tables:", *([_table_line(t) for t in bundle["tables"]] or ["(none)"]), "Backend modules (routes):"]
    for m in bundle["modules"]:
        parts += [f"- backend/{m['name']}.py ({_kb(m['source'])})", *_module_routes(m["source"])]
    parts += ["App files:", *([_file_line(f["path"], f["content"]) for f in files] or ["(none)"])]
    if bundle.get("tests"):
        parts += ["Scenarios: " + ", ".join(t["name"] for t in bundle["tests"])]
    return "\n".join(parts)


def _fit(items: list[tuple[str, str]], cap: int) -> list[tuple[str, str]]:
    """The leading ``items`` ``(path, text)`` that fit ``cap`` characters (the first always stays)."""
    out, used = [], 0
    for path, text in items:
        if out and used + len(text) > cap:
            continue
        out.append((path, text))
        used += len(text)
    return out


def _file_blocks(items: list[tuple[str, str]]) -> str:
    return "\n".join(f'<file path="{p}">\n{t}\n</file>' for p, t in items) or "(none)"


_STOP = {"the", "and", "for", "with", "that", "this", "from", "into", "when", "make", "should", "each", "every", "page", "can", "has",
         "have", "show", "need", "want", "all", "any", "are", "not", "but", "its", "their", "also", "use", "add", "new", "app", "please"}
_DATA_RE = re.compile(r"\b(field|column|table|data|database|rule|validat\w*|route|endpoint|api|backend|permission|role|roles|seed|sort|"
                      r"filter|search|calculat\w*|requir\w*|only|allow\w*|forbid\w*|approv\w*|reject\w*|notif\w*|email|unique|duplicate|"
                      r"enum|track|audit|history|paginat\w*|export|import|delete|remove|manager|admin|staff|owner|limit|over|under|more than|less than)\b", re.I)


_VERBS = {"list", "get", "create", "update", "delete", "post", "put", "patch", "set", "fetch", "load", "save", "read", "remove", "view", "show"}


def _tokens(text: str) -> set[str]:
    """Lower-case word stems of ``text`` (camel / snake / kebab split, a plural reduced to its singular), stop words and short words dropped."""
    out: set[str] = set()
    for w in re.findall(r"[A-Za-z][A-Za-z0-9]*", text):
        for part in re.findall(r"[A-Z]?[a-z]+[0-9]*|[A-Z]+(?![a-z])|[0-9]+", w):
            low = part.lower()
            out.add(low[:-1] if low.endswith("s") and len(low) > 3 and not low.endswith("ss") else low)
    return {w for w in out if len(w) >= 3 and w not in _STOP}


def _resolve_import(frm: str, spec: str, paths: set[str]) -> str | None:
    base = os.path.normpath(os.path.join(os.path.dirname(frm), spec)).lstrip("./")
    for cand in (base, *[base + e for e in (".tsx", ".ts", ".jsx", ".js")], *[base + "/index" + e for e in (".tsx", ".ts")]):
        if cand in paths:
            return cand
    return None


def _import_graph(files: dict[str, str]) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """``(imports, imported_by)`` between the app's own files (relative imports only)."""
    paths = set(files)
    imp: dict[str, set[str]] = {p: set() for p in files}
    by: dict[str, set[str]] = {p: set() for p in files}
    for p, src in files.items():
        for spec in re.findall(r"""(?:from|import)\s*\(?\s*['"](\.[^'"]*)['"]""", src):
            t = _resolve_import(p, spec, paths)
            if t and t != p:
                imp[p].add(t)
                by[t].add(p)
    return imp, by


def select_for_request(bundle: dict[str, Any], request: str, cap: int = EDIT_CONTEXT_CHARS) -> list[tuple[str, str]]:
    """The ``(path, text)`` pairs sent up front with an edit request, by a cheap deterministic heuristic: app files whose name,
    exports or screen name the request mentions (or that call a mentioned route / use a mentioned table), the module source when the
    request touches data, rules or routes, then the files those import or that import them; capped at ``cap`` characters. When
    nothing matches: the entry file and the module. Modules are named ``backend/<name>.py``."""
    req_low = request.lower()
    req = _tokens(request)
    files = {f["path"]: f["content"] for f in bundle["app"]["files"] if not f["path"].startswith("kit/") and f["path"] != CLIENT_PATH}
    mods = {f"backend/{m['name']}.py": m["source"] for m in bundle["modules"]}
    ops = {n for m in bundle["modules"] for n in re.findall(r"name\s*=\s*[\"'](\w+)[\"']", m["source"])
           if n.lower() in req_low or (_tokens(n) - _VERBS and (_tokens(n) - _VERBS) <= req)}
    tables = [t["name"] for t in bundle["tables"] if t["name"].lower() in req_low or (_tokens(t["name"]) and _tokens(t["name"]) <= req)]
    columns = {c["name"] for t in bundle["tables"] for c in t.get("columns") or [] if isinstance(c, dict) and len(str(c.get("name"))) >= 4
               and re.search(rf"\b{re.escape(str(c['name']))}\b", request, re.I)}
    scores: dict[str, int] = {}
    for path, src in files.items():
        stem_tokens = _tokens(_stem(path)) - {"index", "view", "views", "app"} if path != "App.tsx" else set()
        sc = 3 * len(stem_tokens & req) + (3 if _stem(path).lower() in req_low and path != "App.tsx" else 0)
        sc += sum(4 for _, n in _EXPORT_RE.findall(src) if len(n) >= 4 and n.lower() in req_low)
        sc += sum(2 for o in ops if re.search(rf"\bapi\s*\.\s*{re.escape(o)}\b", src))
        sc += sum(2 for t in tables if t in src)
        if path == "App.tsx" and re.search(r"\b(nav\w*|menu|tabs?|sidebar|screens?|pages?|route)\b", req_low):
            sc += 2
        if sc:
            scores[path] = sc
    direct = sorted(scores, key=lambda p: (-scores[p], p))
    wants_module = bool(ops or tables or columns or _DATA_RE.search(request)) or not direct
    order: list[str] = []
    if wants_module:
        order += list(mods)
    order += direct
    imp, by = _import_graph(files)
    for p in direct:
        order += sorted(imp[p]) + (sorted(by[p]) if len(by[p]) <= 3 else [])       # not the importers of a hub file
    if not direct and "App.tsx" in files:
        order.insert(len(mods) if wants_module else 0, "App.tsx")
    items = [(p, mods.get(p) or files[p]) for p in dict.fromkeys(order) if p in mods or p in files]
    return _fit(items, cap)


def edit_prompt(bundle: dict[str, Any], request: str) -> tuple[str, list[str]]:
    """``(user message, paths shown in full)`` of an edit call: the request, the index, the selected files, the change rules."""
    shown = select_for_request(bundle, request)
    req = _tokens(request)
    mentioned = [t for t in bundle["tables"] if t["name"].lower() in request.lower() or (_tokens(t["name"]) and _tokens(t["name"]) <= req)]
    parts = ["## Request", request.strip(), "", "## The app (index)", bundle_index(bundle), ""]
    if mentioned:
        parts += ["## Tables the request mentions (full definitions)", json.dumps(mentioned, ensure_ascii=False, separators=(",", ":")), ""]
    parts += ["## Files shown in full (current text)", _file_blocks(shown), ""]
    parts += extra_context(None, bundle)
    parts += [_CHANGE_RULES]
    return "\n".join(parts), [p for p, _ in shown]


def read_section(bundle: dict[str, Any], paths: Any, shown: set[str], cap: int = READ_CHARS) -> str:
    """The text of the files a ``read_files`` call asked for (app files, ``backend/<name>.py``, ``table:<name>``, ``test:<name>``,
    ``kit/<file>``), for the next round; a path that does not exist says so."""
    files = {f["path"]: f["content"] for f in bundle["app"]["files"]}
    mods = {f"backend/{m['name']}.py": m["source"] for m in bundle["modules"]} | {m["name"]: m["source"] for m in bundle["modules"]}
    out, used = [], 0
    for raw in [str(x).strip() for x in (paths if isinstance(paths, list) else [])][:12]:
        path = raw.lstrip("./") if not raw.startswith(("table:", "test:")) else raw
        text: str | None = None
        if path.startswith("table:"):
            t = next((t for t in bundle["tables"] if t["name"] == path[6:]), None)
            text = json.dumps(t, ensure_ascii=False, separators=(",", ":")) if t else None
        elif path.startswith("test:"):
            t = next((t for t in bundle.get("tests") or [] if t["name"] == path[5:]), None)
            text = json.dumps(t, ensure_ascii=False, separators=(",", ":")) if t else None
        elif path in mods:
            text = mods[path]
        elif path in files and path != CLIENT_PATH:
            text = files[path]
        elif path.startswith("kit/") or path == CLIENT_PATH:
            try:
                from . import app_builder_ai_service as ab
                text = next((f["content"] for f in ab.kit_files() if f["path"] == path), None)
            except Exception:  # noqa: BLE001
                text = None
            if text is None:
                out.append(f'<file path="{path}">(generated or provided by the platform; not available)</file>')
                continue
        if text is None:
            out.append(f'<file path="{raw}">(no such file; use a path from the index)</file>')
        elif path in shown:
            out.append(f'<file path="{path}">(already shown above)</file>')
        elif out and used + len(text) > cap:
            out.append(f'<file path="{path}">(too large for this round; ask for it alone)</file>')
        else:
            used += len(text)
            shown.add(path)
            out.append(f'<file path="{path}">\n{text}\n</file>')
    return "## Files you asked to read\n" + "\n".join(out or ["(no paths given)"])


def apply_edits(text: str, edits: Any) -> tuple[str, list[str]]:
    """``(new text, problems)``: the ``{old, new}`` replacements applied in order; an ``old`` that is missing, empty or occurs more
    than once is skipped and described."""
    problems: list[str] = []
    for i, e in enumerate(edits if isinstance(edits, list) else [], 1):
        old, new = (e.get("old"), e.get("new")) if isinstance(e, dict) else (None, None)
        if not isinstance(old, str) or not old:
            problems.append(f"edit {i}: `old` is empty")
            continue
        n = text.count(old)
        if n != 1:
            problems.append(f"edit {i}: `old` {'is not found in the file' if n == 0 else f'occurs {n} times; include more surrounding text'}: {old[:80]!r}")
            continue
        text = text.replace(old, new if isinstance(new, str) else "", 1)
    return text, problems


def resolve_edits(bundle: dict[str, Any], raw: dict[str, Any]) -> tuple[dict[str, Any], dict[str, list[str]]]:
    """``(changes, problems)``: ``raw`` with every module / file carrying ``edits`` turned into complete ``source`` / ``content``
    (full content wins when both are given), and ``{path: [problem, ...]}`` for the ones whose edits did not all apply (those are left
    out of ``changes``). An entry with neither content nor edits is dropped."""
    out = copy.deepcopy(raw)
    problems: dict[str, list[str]] = {}
    cur_files = {f["path"]: f["content"] for f in bundle["app"]["files"]}
    cur_mods = {m["name"]: m["source"] for m in bundle["modules"]}

    def one(items: Any, key: str, id_key: str, cur: dict[str, str], label: str) -> list[dict[str, Any]]:
        kept = []
        for it in items if isinstance(items, list) else []:
            if not isinstance(it, dict):
                continue
            ident = str(it.get(id_key) or "")
            ident = ident.lstrip("./") if id_key == "path" else ident
            if isinstance(it.get(key), str) and it.get(key):
                kept.append({**{k: v for k, v in it.items() if k != "edits"}})
            elif it.get("edits"):
                if ident not in cur:
                    problems[label(ident)] = ["does not exist; send its complete content instead of edits"]
                    continue
                text, probs = apply_edits(cur[ident], it["edits"])
                if probs:
                    problems[label(ident)] = probs
                    continue
                kept.append({**{k: v for k, v in it.items() if k != "edits"}, key: text})
        return kept

    if "modules" in out:
        out["modules"] = one(out["modules"], "source", "name", cur_mods, lambda n: f"backend/{n}.py")
    app = out.get("app")
    if isinstance(app, dict) and "files" in app:
        app["files"] = one(app["files"], "content", "path", cur_files, lambda n: n)
    return out, problems


def _combine(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """``a`` with the changes of ``b`` laid over it (tables / modules by name, files by path, tests by name)."""
    out = copy.deepcopy(a)
    for key, ident in (("tables", "name"), ("modules", "name"), ("tests", "name")):
        have = {x.get(ident): i for i, x in enumerate(out.get(key) or [])}
        for x in b.get(key) or []:
            if x.get(ident) in have:
                out[key][have[x.get(ident)]] = x
            else:
                out.setdefault(key, []).append(x)
    oa, ob_ = out.setdefault("app", {}), b.get("app") or {}
    have = {f.get("path"): i for i, f in enumerate(oa.get("files") or [])}
    for f in ob_.get("files") or []:
        if f.get("path") in have:
            oa["files"][have[f["path"]]] = f
        else:
            oa.setdefault("files", []).append(f)
    oa["delete"] = list(dict.fromkeys((oa.get("delete") or []) + (ob_.get("delete") or [])))
    out["seed"] = {**(out.get("seed") or {}), **(b.get("seed") or {})}
    if b.get("message"):
        out["message"] = b["message"]
    return out


async def ask_changes(user: str, shown: list[str], bundle: dict[str, Any], *, phase: str, timer: Timer) -> dict[str, Any]:
    """The change call (edit or fix pass): ``emit_changes`` with ``read_files`` on offer. A read adds the files' text and asks again
    (at most :data:`READ_ROUNDS` times). The reply's ``edits`` are applied to ``bundle`` here, so the result is a plain changes
    dict with complete ``content`` / ``source``; an edit whose ``old`` text is not found is reported back ONCE and the model
    corrects it (the edits that applied are kept). Raises :class:`WriteError` when the correction fails too."""
    seen = set(shown)
    extra = ""
    reads = 0
    kept: dict[str, Any] | None = None
    asked_fix = False
    while True:
        offer = [EMIT_CHANGES] + ([READ_FILES] if reads < READ_ROUNDS else [])
        name, args = await _ask_any(brief(), user + extra, offer, phase=phase, timer=timer)
        if name == "read_files":
            reads += 1
            extra += "\n\n" + read_section(bundle, args.get("paths"), seen)
            continue
        changes, problems = resolve_edits(bundle, args)
        if not problems:
            return _combine(kept, changes) if kept is not None else changes
        if asked_fix:
            raise WriteError("some edits could not be applied: " + "; ".join(f"{p}: {', '.join(v)}" for p, v in problems.items())[:600])
        asked_fix = True
        kept = changes
        files = {f["path"]: f["content"] for f in bundle["app"]["files"]} | {f"backend/{m['name']}.py": m["source"] for m in bundle["modules"]}
        extra += ("\n\n## Some of your edits could not be applied\n" + "\n".join(f"- {p}: {'; '.join(v)}" for p, v in problems.items()) +
                  "\nThe other changes you sent are kept. Call `emit_changes` again with ONLY corrected changes for these files (`old` must match the "
                  "current text exactly and occur once; or send the complete content). Their current text:\n" +
                  _file_blocks([(p, files[p]) for p in problems if p in files and p not in seen]))
        seen |= set(problems)


# ── fix pass, build, edit ────────────────────────────────────────────────────


def _errors_text(errors: list[dict[str, str]]) -> str:
    return "\n".join(f"- [{e['where']}] {e['code']}: {e['message']}" for e in errors[:MAX_ERRORS])


def failing_parts(bundle: dict[str, Any], errors: list[dict[str, str]]) -> dict[str, set[str]]:
    """Which modules, app files, tables and scenarios the ``errors`` point at (by their ``where`` and message). An error that
    resolves to nothing selects every module and app file, so a fix call is never starved of the code it must change."""
    mods = {m["name"]: m["source"] for m in bundle["modules"]}
    files = {f["path"]: f["content"] for f in bundle["app"]["files"] if not f["path"].startswith("kit/") and f["path"] != CLIENT_PATH}
    tests = {t["name"]: t for t in bundle.get("tests") or []}
    out: dict[str, set[str]] = {"modules": set(), "files": set(), "tables": set(), "tests": set()}
    for e in errors:
        where, msg = e["where"], e["message"]
        hit: set[str] = set()
        if where.startswith("module:") and where[7:] in mods:
            out["modules"].add(where[7:])
            hit.add("m")
        elif where.startswith("route:"):
            path = where.partition(" ")[2]
            found = {n for n, src in mods.items() if f'"{path}"' in src or f"'{path}'" in src}
            out["modules"] |= found
            hit |= {"m"} if found else set()
        elif where.startswith(("table:", "seed:")):
            out["tables"].add(where.partition(":")[2])
            hit.add("t")
        elif where.startswith("scenario:"):
            name = where[9:]
            if name in tests:
                out["tests"].add(name)
                hit.add("s")
            ops = {str(st.get("op")) for st in (tests.get(name) or {}).get("steps") or []}
            found = {n for n, src in mods.items() if any(re.search(rf"name\s*=\s*[\"']{re.escape(o)}[\"']", src) for o in ops)}
            out["modules"] |= found
            hit |= {"m"} if found else set()
        elif where == "app":
            found = {p for p in files if p in msg}
            for op in re.findall(r"api\.(\w+)", msg):
                found |= {p for p, c in files.items() if re.search(rf"\bapi\s*\.\s*{op}\b", c)}
            out["files"] |= found
            hit |= {"f"} if found else set()
        if not hit and not where.startswith(("table:", "seed:")):
            out["modules"] |= set(mods)
            out["files"] |= set(files)
    return out


def fix_text(bundle: dict[str, Any], errors: list[dict[str, str]]) -> tuple[str, list[str]]:
    """``(user message, paths shown in full)`` of the fix prompt (both profiles): the errors, an index of the app, and the full text of only the failing files (capped)."""
    sel = failing_parts(bundle, errors)
    n = COMPACT_MAX_ERRORS if profile() == "compact" else MAX_ERRORS
    blob = _errors_text(errors[:n])
    mentions = lambda path: blob.count(path)           # noqa: E731 - files the errors name come first
    mods = [m for m in bundle["modules"] if m["name"] in sel["modules"]]
    files = sorted((f for f in bundle["app"]["files"] if f["path"] in sel["files"]), key=lambda f: -mentions(f["path"]))
    items = [(f"backend/{m['name']}.py", m["source"]) for m in mods] + [(f["path"], f["content"]) for f in files]
    shown = _fit(items, FIX_CONTEXT_CHARS)
    tables = [t for t in bundle["tables"] if t["name"] in sel["tables"]]
    tests = [t for t in bundle.get("tests") or [] if t["name"] in sel["tests"]]
    parts = ["## Errors found by loading and checking the app", blob, "", "## The app (index)", bundle_index(bundle), ""]
    if tables:
        parts += ["## Tables the errors point at (full definitions)", json.dumps(tables, ensure_ascii=False, separators=(",", ":")), ""]
    if tests:
        parts += ["## Scenarios the errors point at", json.dumps(tests, ensure_ascii=False, separators=(",", ":")), ""]
    parts += ["## The files that have errors (current text)", _file_blocks(shown), ""]
    parts += extra_context(None, bundle)
    parts += [_CHANGE_RULES, "", "Fix every error."]
    return "\n".join(parts), [p for p, _ in shown]


async def fix_pass(bundle: dict[str, Any], errors: list[dict[str, str]], *, timer: Timer) -> dict[str, Any]:
    """One fix call; the reply carries only the changed parts (``emit_changes``). It is sent the errors, an index of the app and the
    failing files only; any other file can be read through ``read_files``."""
    user, shown = fix_text(bundle, errors)
    return await ask_changes(user, shown, bundle, phase="fix", timer=timer)


_BLOCKING = ("module:", "route:", "table", "seed:", "client")     # errors after which the routes cannot be exercised


async def autotest(project_id: str, app_id: str | None, bundle: dict[str, Any], errors: list[dict[str, str]], *, timer: Timer,
                   log: Log | None = None) -> dict[str, Any]:
    """Run the bundle's scenario tests against the loaded routes. Returns ``{total, passed, results, skipped?}``; ``skipped`` says
    why nothing ran (toggle off, no tests, a code error keeps the routes from working, no sandbox). Never raises."""
    from . import app_autotest as at

    empty: dict[str, Any] = {"total": 0, "passed": 0, "results": []}
    if not at.enabled():
        return {**empty, "skipped": "scenario tests are off (WORKFLOW_ONESHOT_AUTOTEST=0)"}
    if not bundle.get("tests"):
        return {**empty, "skipped": "the bundle has no tests"}
    if any(e["where"].startswith(_BLOCKING) for e in errors):
        return {**empty, "skipped": "the code checks failed first, so the scenarios were not run"}
    try:
        with timer.phase("autotest"):
            rep = await at.run_project(project_id, app_id, bundle["tests"])
    except Exception as e:  # noqa: BLE001 - a runner that cannot run is reported, never a crash of the build
        logger.warning("one-shot: the scenario tests crashed", exc_info=True)
        return {**empty, "skipped": f"the scenario runner crashed: {type(e).__name__}: {str(e)[:150]}"}
    if rep["total"]:
        if log is not None:
            await log(f"Checks: {rep['passed']}/{rep['total']} scenarios passing")
        bc.emit({"type": "autotest", "passed": rep["passed"], "total": rep["total"],
                 "failed": [r["name"] for r in rep["results"] if not r["passed"]]})
    return rep


def _tests_changed(before: list[dict[str, Any]], after: list[dict[str, Any]]) -> list[str]:
    old = {t["name"]: t for t in before}
    return [t["name"] for t in after if old.get(t["name"]) != t]


async def announce_files(project_id: str, bundle: dict[str, Any], app_id: str | None) -> None:
    """Emit a ``file`` event (``done=False``) for every module and app file of ``bundle``. Never raises. The model call returns
    the whole bundle at once (the provider helper does not stream tool-call arguments), so this is the earliest the code exists."""
    try:
        for m in bundle.get("modules") or []:
            bc.emit_file("backend", f"backend/{m['name']}.py", m["source"], done=False)
        files = [f for f in (bundle.get("app") or {}).get("files", []) if not f["path"].startswith("kit/") and f["path"] != CLIENT_PATH]
        if files and app_id:
            from . import project_files as pf
            async with bc._factory()() as s:
                slug = await pf.app_slug(s, project_id, app_id)
            for f in files:
                bc.emit_file("ui", f"apps/{slug}/{f['path']}", f["content"], done=False)
    except Exception:  # noqa: BLE001 - progress is a courtesy
        logger.debug("one-shot: announcing files failed", exc_info=True)


async def _load_check(project_id: str, bundle: dict[str, Any], *, app_id: str | None, app_name: str | None, save_app: bool,
                      known: set[str] | None, timer: Timer, phase: str, prompt: str, smoke: bool,
                      log: Log | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    await announce_files(project_id, bundle, app_id)        # code is visible as soon as it is parsed, before any check runs
    with timer.phase(phase + "_load"):
        loaded = await load(project_id, bundle, app_id=app_id, app_name=app_name, save_app=save_app, known_modules=known,
                            version_prompt=prompt, log=log)
    with timer.phase(phase + "_check"):
        chk = await check(project_id, bundle, loaded, smoke=smoke, known_modules=known, log=log)
    return loaded, chk


async def build(project_id: str, plan: dict[str, Any], *, app_key: str | None = None, app_id: str | None = None,
                existing: dict[str, Any] | None = None, known_modules: set[str] | None = None, log: Log | None = None,
                timer: Timer | None = None, smoke: bool = True, fix: bool = True) -> dict[str, Any]:
    """write -> load -> check -> (one fix pass -> load -> check). Returns ``{ok, bundle, app_id, version_id, errors, routes,
    modules, fixed, timing}``. Raises :class:`WriteError` when the model produces nothing usable."""
    timer = timer or Timer()

    async def say(msg: str) -> None:
        if log is not None:
            await log(msg)

    await say(f"Builder profile: {profile()}")
    await _load_db_context(project_id, plan)
    bundle = await write_app(plan, app_key=app_key, existing=existing, timer=timer, log=log)
    app = next((a for a in plan.get("apps") or [] if app_key in (None, a.get("key"))), None) or {}
    name = app.get("name") or plan.get("title") or None
    await say(f"Wrote {len(bundle['tables'])} table(s), {len(bundle['modules'])} module(s), {len(bundle['app']['files'])} app file(s); loading")
    loaded, chk = await _load_check(project_id, bundle, app_id=app_id, app_name=name, save_app=True, known=known_modules, timer=timer,
                                    phase="first", prompt="one-shot build", smoke=smoke, log=log)
    errors = loaded["errors"] + chk["errors"]
    auto = await autotest(project_id, loaded["app_id"], bundle, errors, timer=timer, log=log)
    from .app_autotest import failure_errors
    fixed = False
    tests_fixed: list[str] = []
    passes = COMPACT_FIX_PASSES if profile() == "compact" else 1
    for n in range(passes):
        problems = errors + failure_errors(auto)
        if not problems or not fix:
            break
        if n and time.monotonic() - timer.t0 > COMPACT_BUDGET_S:
            await say("The build has run long; no further fix pass")
            break
        await say(f"{len(problems)} problem(s) found; fixing" + (f" (pass {n + 1} of {passes})" if passes > 1 else ""))
        try:
            raw = await fix_pass(bundle, problems, timer=timer)
            before = bundle.get("tests") or []
            bundle = merge_changes(bundle, raw)
            tests_fixed = list(dict.fromkeys(tests_fixed + _tests_changed(before, bundle.get("tests") or [])))
            fixed = True
            if tests_fixed:
                await say("The fix pass changed test(s): " + ", ".join(tests_fixed) + (f". {raw.get('message')}" if raw.get("message") else ""))
        except WriteError as e:
            await say(f"The fix pass produced nothing usable: {e}")
            break
        loaded, chk = await _load_check(project_id, bundle, app_id=loaded["app_id"], app_name=name, save_app=True, known=known_modules,
                                        timer=timer, phase="fix" if n == 0 else f"fix{n + 1}", prompt="one-shot build (fix pass)",
                                        smoke=smoke, log=log)
        errors = loaded["errors"] + chk["errors"]
        auto = await autotest(project_id, loaded["app_id"], bundle, errors, timer=timer, log=log)
    auto = {**auto, "tests_changed_by_fix": tests_fixed}
    errors = errors + failure_errors(auto)
    return {"ok": not errors, "bundle": bundle, "app_id": loaded["app_id"], "version_id": loaded["version_id"],
            "errors": errors[:MAX_ERRORS], "routes": chk["routes"], "modules": loaded["modules"], "fixed": fixed,
            "autotest": auto, "timing": timer.summary()}


async def current_bundle(project_id: str, app_id: str) -> dict[str, Any]:
    """The app as it is now: its files, the module(s) behind its routes and the tables those modules use."""
    from sqlalchemy import select

    from ..db.models import AppModel, CodeModuleModel
    from .api_route_service import ApiRouteService
    from .data_table_service import DataTableService
    from .module_generator import scan_usage
    from .project_service import resolve_project

    async with bc._factory()() as s:
        project = await resolve_project(s, project_id)
        app = await s.get(AppModel, app_id)
        studio = ((app.draft_definition or {}).get("studio") or {}) if app is not None else {}
        rids = {str(x) for x in studio.get("route_ids") or []}
        wanted = set((studio.get("oneshot") or {}).get("modules") or [])
        mids = {r.handler_module_id for r in await ApiRouteService(s).rows(project.id) if r.id in rids and r.handler_module_id}
        mods = [m for m in (await s.execute(select(CodeModuleModel).where(CodeModuleModel.project_id == project.id))).scalars().all()
                if m.id in mids or m.name in wanted]
        tsvc = DataTableService(s)
        tables = []
        for n in dict.fromkeys(t for m in mods for t in scan_usage(m.source)["tables"]):
            t = await tsvc.table_by_name(project.id, n)
            if t is not None:
                keep = ("name", "type", "required", "default", "enum", "unique", "index", "ref", "description")
                tables.append({"name": n, "description": t.description or "",
                               "columns": [{k: v for k, v in c.items() if k in keep} for c in t.columns or []]})
        detail = await bc._app_service(s).get_app(app_id)
        files = [{"path": f.path, "content": f.content} for f in (detail.files if detail is not None else [])
                 if not f.path.startswith("kit/") and f.path != CLIENT_PATH]
        if not files and detail is not None and detail.source_code:
            files = [{"path": "App.tsx", "content": detail.source_code}]
        return {"tables": tables, "seed": {}, "modules": [{"name": m.name, "source": m.source} for m in sorted(mods, key=lambda m: m.name)],
                "app": {"name": app.name if app is not None else "", "files": files},
                "tests": normalize_tests((studio.get("oneshot") or {}).get("tests"))}


def plan_with_tables(body: dict[str, Any], tables: list[dict[str, Any]]) -> dict[str, Any] | None:
    """A copy of plan ``body`` with ``tables`` (new or changed; full column lists) merged in additively: new columns are appended,
    new tables added (owned by the first module). None when the plan already has all of it."""
    from .schema_migrations import column as norm_column

    nb = copy.deepcopy(body)
    plan_tables = nb.setdefault("data", {}).setdefault("tables", [])
    owner = primary_module(nb)
    changed = False
    for t in tables:
        pt = next((x for x in plan_tables if x.get("name") == t["name"]), None)
        if pt is None:
            plan_tables.append({"name": t["name"], "owner": owner, "description": t.get("description") or "",
                                "columns": [norm_column(c) for c in t.get("columns") or []]})
            changed = True
            continue
        have = {c["name"] for c in pt.get("columns") or []}
        for c in t.get("columns") or []:
            if c.get("name") not in have:
                pt.setdefault("columns", []).append(norm_column(c))
                changed = True
    return nb if changed else None


async def record_plan_revision(project_id: str, tables: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Record the data change of an edit as a new plan version (revise + approve, no approval gate). None when the project has no
    approved plan or the plan already holds the change; never raises (the edit stands without it)."""
    from . import project_plan as pp

    try:
        async with bc._factory()() as s:
            rec = await pp.load_approved(s, project_id)
            if rec is None:
                return None
            nb = plan_with_tables(rec["body"], tables)
            if nb is None:
                return None
            draft = await pp.revise(s, project_id, nb, actor="edit")
            done = await pp.approve(s, project_id, draft["version"], accept_breaking=True, actor="edit")
            return {"version": done["version"], "from": rec["version"]}
    except Exception as e:  # noqa: BLE001
        logger.warning("edit: could not record the plan revision", exc_info=True)
        return {"error": f"{type(e).__name__}: {str(e)[:200]}"}


async def edit_app(project_id: str, app_id: str, request: str, *, save_app: bool = True, log: Log | None = None,
                   timer: Timer | None = None, smoke: bool = True, known_modules: set[str] | None = None,
                   bundle: dict[str, Any] | None = None) -> dict[str, Any]:
    """One model call that sees the request, an index of the app and the files the request is about (:func:`select_for_request`; any
    other file through ``read_files``), and returns the changed parts as targeted ``edits`` or whole files (new / changed table columns
    included); then load + check + one fix pass. A data change records a plan revision.
    Returns ``{ok, message, changes, bundle, errors, plan_revision, version_id, timing}``; ``changes`` names what changed."""
    timer = timer or Timer()
    await _load_db_context(project_id)
    current = bundle or await current_bundle(project_id, app_id)
    user, shown = edit_prompt(current, request)
    with timer.phase("edit"):
        raw = await ask_changes(user, shown, current, phase="edit", timer=timer)
    merged = merge_changes(current, raw)
    ch = normalize_bundle(raw)
    cur_tables = {t["name"]: t for t in current["tables"]}
    cur_files = {f["path"]: f["content"] for f in current["app"]["files"]}
    cur_mods = {m["name"]: m["source"] for m in current["modules"]}
    changed_tables = [t for t in ch["tables"] if cur_tables.get(t["name"]) != t]
    changes = {"tables": [t["name"] for t in changed_tables],
               "modules": [m["name"] for m in ch["modules"] if cur_mods.get(m["name"]) != m["source"]],
               "files": [f["path"] for f in ch["app"]["files"] if cur_files.get(f["path"]) != f["content"]],
               "deleted": [p for p in ((raw.get("app") or {}).get("delete") or []) if p in cur_files]}
    message = str(raw.get("message") or "").strip()
    if not any(changes.values()):
        return {"ok": True, "message": message, "changes": changes, "bundle": current, "errors": [], "plan_revision": None,
                "version_id": None, "route_ids": [], "timing": timer.summary()}
    revision = await record_plan_revision(project_id, changed_tables) if changed_tables else None
    loaded, chk = await _load_check(project_id, merged, app_id=app_id, app_name=None,
                                    save_app=save_app and bool(changes["files"] or changes["deleted"]), known=known_modules,
                                    timer=timer, phase="edit", prompt=request, smoke=smoke)
    errors = loaded["errors"] + chk["errors"]
    auto = await autotest(project_id, app_id, merged, errors, timer=timer)       # the existing tests plus any the edit added
    from .app_autotest import failure_errors
    if errors or failure_errors(auto):
        try:
            merged = merge_changes(merged, await fix_pass(merged, errors + failure_errors(auto), timer=timer))
        except WriteError:
            pass
        else:
            loaded, chk = await _load_check(project_id, merged, app_id=app_id, app_name=None, save_app=save_app, known=known_modules,
                                            timer=timer, phase="fix", prompt=request + " (fix pass)", smoke=smoke)
            errors = loaded["errors"] + chk["errors"]
            auto = await autotest(project_id, app_id, merged, errors, timer=timer)
            changes["files"] = [f["path"] for f in merged["app"]["files"] if cur_files.get(f["path"]) != f["content"]]
            changes["modules"] = [m["name"] for m in merged["modules"] if cur_mods.get(m["name"]) != m["source"]]
    errors = errors + failure_errors(auto)
    return {"ok": not errors, "message": message, "changes": changes, "bundle": merged, "errors": errors[:MAX_ERRORS], "autotest": auto,
            "plan_revision": revision, "version_id": loaded["version_id"], "route_ids": loaded["route_ids"], "timing": timer.summary()}
