"""Prompt → validated module draft (UI-DESIGN §10.1, ``POST /projects/{p}/modules/generate``).

Never writes modules, routes, tables or secrets; the only rows it creates are the self-test executions.

One generation = at most 3 attempts inside a wall-clock budget (``settings.module_gen_timeout_s``):
LLM → static analysis (the same ``CodeModuleService._plan`` the dry run uses) → repair turn when there are
problems → read-only self-test of the GET routes. Every outgoing message list is scrubbed of the project's
and team's secret values (all environments, all encodings) before it reaches ``call_llm``; self-test output,
traces and table rows are never put into LLM messages.
"""

from __future__ import annotations

import ast
import asyncio
import json
import logging
import re
from typing import Any, AsyncIterator, Awaitable, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select

from ..core.config import settings
from ..db.models import CodeModuleModel, ProjectModel, VariableModel
from ..db.session import async_session_factory
from .project_service import ProjectError, env_names

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
MAX_PROMPT_CHARS = 8000
MAX_TURN_CHARS = 8000
MAX_SOURCE_BYTES = 200_000
KEEP_TURNS = 6
SELF_TEST_MAX_ROUTES = 5
SELF_TEST_ROUTE_TIMEOUT_S = 10.0
SELF_TEST_MAX_CALLS = 20
BODY_PREVIEW_CHARS = 2048
POLL_S = 0.25
REPAIR_HEADER = "Fix these problems in the module you emitted:"
SCRUB_WARNING = {"code": "secret_scrubbed",
                 "message": "A secret value in your code or prompt was removed before sending it to the AI"}

# One in-flight generation per (project, actor). The actor is client-supplied (X-User-Id or "local"), so this
# is best-effort per client. In-process on purpose: the engine is a single process; a second worker would
# need a DB lock.
_ACTIVE: set[tuple[str, str]] = set()


def try_acquire(project_id: str, actor: str | None) -> tuple[str, str] | None:
    key = (project_id, actor or "local")
    if key in _ACTIVE:
        return None
    _ACTIVE.add(key)
    return key


def release(key: tuple[str, str] | None) -> None:
    if key is not None:
        _ACTIVE.discard(key)


# ── request ──────────────────────────────────────────────────────────────────


class Turn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["user", "assistant"]
    content: str = Field(max_length=MAX_TURN_CHARS)


class GenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(min_length=1, max_length=MAX_PROMPT_CHARS)
    module_id: str | None = None
    module_name: str | None = None
    current_source: str | None = None
    conversation: list[Turn] = []
    self_test: bool = True

    @field_validator("current_source")
    @classmethod
    def _size(cls, v: str | None) -> str | None:
        if v is not None and len(v.encode("utf-8")) > MAX_SOURCE_BYTES:
            raise ValueError(f"current_source is larger than {MAX_SOURCE_BYTES} bytes")
        return v


EMIT_MODULE_TOOL: dict[str, Any] = {
    "name": "emit_module",
    "description": "Emit the complete Python module (all routes) together with any new data tables it needs.",
    "parameters": {
        "type": "object", "required": ["module_name", "source", "summary"],
        "properties": {
            "module_name": {"type": "string", "description": "snake_case module name, e.g. refunds"},
            "source": {"type": "string", "description": "The complete module source."},
            "summary": {"type": "string", "maxLength": 300},
            "tables": {"type": "array", "items": {"type": "object", "required": ["name", "columns"], "properties": {
                "name": {"type": "string"},
                "columns": {"type": "array", "items": {"type": "object", "required": ["name", "type"], "properties": {
                    "name": {"type": "string"},
                    "type": {"enum": ["string", "number", "integer", "boolean", "datetime", "json"]},
                    "required": {"type": "boolean"}}}}}}},
        },
    },
}


# ── internal control flow ────────────────────────────────────────────────────


class _Disconnected(Exception):
    pass


class _Timeout(Exception):
    pass


# ── system prompt ────────────────────────────────────────────────────────────

RULES = """You write one Python backend module for a studio project. Reply by calling emit_module with the COMPLETE module.
- The module is a standalone file: start it with the imports it needs, e.g. `from studio import api, Response, HTTPError`
  and `import studio` (api, Response, HTTPError and studio are NOT available unless imported).
- Routes are plain functions decorated with @api.get / @api.post / @api.put / @api.patch / @api.delete. Decorator
  arguments are string literals only (path positionally; auth= and name= as keywords; auth is none, app_token or api_key).
- Secrets: studio.secret('K') with a string literal (f-strings like f"Bearer {studio.secret('K')}" are fine). Never
  transform a secret (.upper(), slicing, hashing, encoding).
- Tables: studio.db.table("<literal name>"). Declare in `tables` every table your code uses that is not in the
  project's table list.
- Call integrations only through studio.nodes.<function>(...) (listed below). No file or network access otherwise.
- Every route handler takes exactly one argument, `req`: `def get_order(req):`. Path parameters are read from
  `req.params["id"]` (never as extra function arguments), the JSON body from `req.json()`, and query values from `req.query`.
- Return JSON-serialisable values (dict or list), or Response(...); raise HTTPError(status, body) for errors.
- Each request has a 30 s limit. pandas is available.
- Do not invent secrets: use the secret names listed in the project facts, or choose new UPPER_SNAKE names."""


def _fmt_columns(cols: list[dict[str, Any]] | None) -> str:
    return ", ".join(f"{c['name']} {c['type']}{'' if not c.get('required') else '*'}" for c in cols or []) or "(no columns)"


def build_system_prompt(reference: dict[str, Any], catalogue: list[dict[str, Any]], facts: dict[str, Any]) -> str:
    out = [RULES, "", "## SDK reference"]
    for e in reference.get("entries", []):
        line = f"- {e['signature']}" if e.get("signature") else f"- {e['name']}"
        out.append(f"{line} — {e.get('doc', '')}".rstrip(" —"))
    for k, v in (reference.get("rules") or {}).items():
        out.append(f"- rule ({k}): {v}")
    out += ["", "## studio.nodes functions (keyword arguments)"]
    for f in catalogue:
        py = f.get("python") or {}
        sig = py.get("signature") or f"{f['function']}(...)"
        detail = []
        for p in py.get("params", []):
            bits = [p["name"], p["type"]] + (["required"] if p["required"] else []) + \
                   ([f"default={json.dumps(p['default'])}"] if p.get("default") is not None else []) + \
                   ([f"one of {p['enum']}"] if p.get("enum") else [])
            detail.append("  · " + " ".join(str(b) for b in bits))
        out.append(f"- studio.nodes.{sig}{' [network egress]' if f.get('egress') else ''} — {f.get('description', '')}")
        out += detail[:12]
    out += ["", "## Project facts", f"project: {facts['slug']}", f"environments: {', '.join(facts['environments'])}"]
    out.append("tables: " + ("; ".join(f"{t['name']} ({_fmt_columns(t['columns'])}; {t['row_count']} rows)"
                                       for t in facts["tables"]) or "(none)"))
    out.append("existing routes:")
    out += [f"  - {r['method']} {r['path']} ({r['name']}) handled by {r['handler']}" for r in facts["routes"]] or ["  (none)"]
    out.append("other modules: " + (", ".join(facts["modules"]) or "(none)"))
    out.append("secrets (names only, never values):")
    for k, envs in sorted(facts["secrets"].items()):
        hosts = envs.get("hosts")
        out.append(f"  - {k}: set in {', '.join(envs['present']) or 'no environment'}; "
                   f"allowed hosts {', '.join(hosts) if hosts else 'NOT BOUND (Python calls are refused until bound)'}")
    if not facts["secrets"]:
        out.append("  (none)")
    out.append("project egress allowlist: " + (", ".join(facts["egress"]) or "(empty)"))
    if "workflows" in facts:
        out.append("workflows (call with studio.nodes.run_workflow(workflow='<name>', input={...}); only callable ones "
                   "have an Execute Workflow Trigger):")
        out += [f"  - {w['name']}{'' if w['callable'] else ' (NOT callable: no Execute Workflow Trigger)'}"
                f"{'' if w['published'] else ' (never published: a release cannot run it)'}"
                for w in facts["workflows"]] or ["  (none)"]
    return "\n".join(out)


async def gather_facts(session: Any, project: ProjectModel) -> dict[str, Any]:
    from .api_route_service import ApiRouteService
    from .data_table_service import DataTableService
    from .secret_service import SecretService

    tables = await DataTableService(session).list_tables(project)
    mods = (await session.execute(select(CodeModuleModel).where(CodeModuleModel.project_id == project.id)
                                  .order_by(CodeModuleModel.name))).scalars().all()
    by_id = {m.id: m.name for m in mods}
    routes = []
    for r in await ApiRouteService(session).rows(project.id):
        handler = r.handler_type if r.handler_type != "python" else f"python module {by_id.get(r.handler_module_id, '?')}"
        routes.append({"method": r.method, "path": r.path, "name": r.name, "handler": handler,
                       "module": by_id.get(r.handler_module_id)})
    envs = env_names(project)
    secrets: dict[str, dict[str, Any]] = {}
    for row in await SecretService(session).effective(project):
        if row["type"] != "secret" or row.get("overridden"):
            continue
        s = secrets.setdefault(row["key"], {"present": [], "hosts": None, "by_env": {}})
        s["present"].append(row["environment"])
        s["by_env"][row["environment"]] = row.get("allowed_hosts")
    for s in secrets.values():
        s["present"] = [e for e in envs if e in s["present"]]
        s["hosts"] = s["by_env"].get(project.default_environment) or next(
            (h for h in s["by_env"].values() if h), None)
    from ..db.models import WorkflowModel
    from ..engine.subworkflow import TRIGGER_TYPE

    wfs = (await session.execute(select(WorkflowModel).where(WorkflowModel.project_id == project.id)
                                 .order_by(WorkflowModel.name))).scalars().all()
    workflows = [{"name": w.name, "published": w.published_version_id is not None,
                  "callable": any(isinstance(n, dict) and n.get("type") == TRIGGER_TYPE
                                  for n in (w.draft_definition or {}).get("nodes") or [])} for w in wfs]
    return {"slug": project.slug, "environments": envs, "default_environment": project.default_environment,
            "tables": tables, "routes": routes, "modules": [m.name for m in mods], "secrets": secrets,
            "egress": list((project.settings or {}).get("egress_allowlist") or []), "workflows": workflows}


async def secret_values(session: Any, project: ProjectModel) -> set[str]:
    """Decrypted values of every secret of the project and its team, in ALL environments."""
    from .variable_crypto import decrypt_secret

    rows = (await session.execute(select(VariableModel).where(
        VariableModel.team_id == project.team_id, VariableModel.type == "secret",
        (VariableModel.project_id.is_(None)) | (VariableModel.project_id == project.id)))).scalars().all()
    return {decrypt_secret(v.value) for v in rows if v.value}


# ── static analysis ──────────────────────────────────────────────────────────


def _attr_chain(node: ast.AST) -> str:
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def scan_usage(source: str) -> dict[str, Any]:
    """Literal table / secret names (with lines) and the dynamic ones, from the AST. No code runs."""
    tables: dict[str, list[int]] = {}
    secrets: dict[str, list[int]] = {}
    dyn_tables: list[int] = []
    dyn_secrets: list[int] = []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {"tables": tables, "secrets": secrets, "dynamic_tables": dyn_tables, "dynamic_secrets": dyn_secrets}
    for n in ast.walk(tree):
        if not isinstance(n, ast.Call):
            continue
        chain = _attr_chain(n.func)
        if chain in ("studio.db.table", "db.table"):
            lits, dyn = tables, dyn_tables
        elif chain in ("studio.secret", "secret"):
            lits, dyn = secrets, dyn_secrets
        else:
            continue
        arg = n.args[0] if n.args else None
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            lits.setdefault(arg.value, []).append(n.lineno)
        else:
            dyn.append(n.lineno)
    return {"tables": tables, "secrets": secrets, "dynamic_tables": dyn_tables, "dynamic_secrets": dyn_secrets}


SDK_NAMES = {"api": "from studio import api", "studio": "import studio",
             "Response": "from studio import Response", "HTTPError": "from studio import HTTPError"}


def undefined_sdk_names(source: str) -> list[tuple[str, int]]:
    """SDK names (api, studio, Response, HTTPError) used but never imported or defined: ``[(name, first line)]``.
    Models often forget the imports; the handler would die with NameError on its first request."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    bound: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            bound |= {(a.asname or a.name).split(".")[0] for a in n.names}
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(n.name)
        elif isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
            bound.add(n.id)
        elif isinstance(n, ast.arg):
            bound.add(n.arg)
    first: dict[str, int] = {}
    for n in ast.walk(tree):
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load) and n.id in SDK_NAMES and n.id not in bound:
            first[n.id] = min(first.get(n.id, n.lineno), n.lineno)
    return sorted(first.items(), key=lambda kv: (kv[1], kv[0]))


def _route_item(d: dict[str, Any], name: str | None = None) -> dict[str, Any]:
    return {"method": d["method"], "path": d["path"], "name": name or d.get("name"), "function": d["function"],
            "line": d.get("line"), "auth": d.get("auth", "none")}


def _errors_of(e: ProjectError) -> list[dict[str, Any]]:
    items = e.extra.get("errors") or [{"line": e.extra.get("line"), "column": None, "message": e.message}]
    return [{"code": e.code, "line": i.get("line"), "column": i.get("column"), "message": i.get("message") or e.message}
            for i in items]


def declared_tables(raw: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(valid declared tables, errors) from emit_module.tables."""
    from .data_table_service import NAME_RE, DataTableService

    out, errors = [], []
    for t in raw if isinstance(raw, list) else []:
        name = t.get("name") if isinstance(t, dict) else None
        if not isinstance(name, str) or not NAME_RE.match(name):
            errors.append({"code": "invalid_table", "line": None, "column": None,
                           "message": f"table name {name!r} must be an identifier"})
            continue
        try:
            cols = DataTableService._validate_columns(t.get("columns") or [])
        except ProjectError as e:
            errors.append({"code": "invalid_table", "line": None, "column": None, "message": f"{name}: {e.message}"})
            continue
        out.append({"name": name, "columns": cols})
    return out, errors


async def analyse(session: Any, project: ProjectModel, *, source: str, module_id: str | None, module_name: str,
                  name_from_model: bool, declared: list[dict[str, Any]], declared_errors: list[dict[str, Any]],
                  scrubbed: bool, facts: dict[str, Any]) -> dict[str, Any]:
    """§10.1 step 2. Returns the ``analysis`` event body plus ``problems`` (what a repair turn lists)."""
    from .code_module_service import CodeModuleService
    from .draft_runner import check_module_name, draft_routes

    errors: list[dict[str, Any]] = list(declared_errors)
    conflicts: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    routes: dict[str, list] = {"created": [], "updated": [], "removed": []}
    problems: list[dict[str, Any]] = []

    try:
        check_module_name(module_name)
    except ProjectError as e:
        errors.append({"code": "invalid_module_name", "line": None, "column": None, "message": e.message})

    for name, line in undefined_sdk_names(source):
        errors.append({"code": "undefined_name", "line": line, "column": None,
                       "message": f"'{name}' is used but not imported (add: {SDK_NAMES[name]})"})

    decls: list[dict[str, Any]] = []
    try:
        decls = draft_routes(source)
    except ProjectError as e:
        errors += _errors_of(e)
    if decls and not any(x["code"] in ("syntax_error", "invalid_decorator") for x in errors):
        saved = await session.get(CodeModuleModel, module_id) if module_id else None
        clashes: list[ProjectError] = []
        plan = await CodeModuleService(session)._plan(project, module_id, source,
                                                      old_source=saved.source if saved else None, collect=clashes)
        svc = CodeModuleService(session)
        summary = svc._summary(project, plan)
        by_fn = {d["function"]: d for d in decls}
        for kind in ("created", "updated"):
            routes[kind] = [_route_item({**by_fn[i["function"]]}, i["name"]) for i in summary[kind]]
        routes["removed"] = [{"method": r["method"], "path": r["path"], "name": r["name"], "function": None,
                              "line": None, "auth": None} for r in summary["removed"]]
        for c in clashes:
            handler = c.extra.get("handler")
            who = f" ({handler['type']} '{handler.get('name')}')" if handler and handler.get("name") else ""
            line = c.extra.get("line")
            msg = (f"{c.extra['conflicts'][0]['method']} {c.extra['conflicts'][0]['path']} already exists{who}"
                   if c.code == "route_conflict" and c.extra.get("conflicts") else c.message)
            errors.append({"code": "route_conflict", "line": line, "column": None, "message": msg})
            if c.code == "route_conflict" and c.extra.get("conflicts"):
                k = c.extra["conflicts"][0]
                conflicts.append({"kind": "route", "method": k["method"], "path": k["path"], "line": k["line"],
                                  "existing_route_id": k["existing_route_id"], "handler": k["handler"]})
    elif decls:
        routes["created"] = [_route_item(d) for d in decls]

    if module_id is None and module_name in facts["modules"]:
        conflicts.append({"kind": "module_name", "name": module_name})
        if name_from_model:
            problems.append({"code": "module_name", "line": None,
                             "message": f"'{module_name}' is taken, choose another"})

    usage = scan_usage(source)
    existing = {t["name"]: t for t in facts["tables"]}
    declared_by = {t["name"]: t for t in declared}
    tables = []
    for name in sorted(set(usage["tables"]) | set(declared_by)):
        exists = name in existing
        cols = existing[name]["columns"] if exists else (declared_by[name]["columns"] if name in declared_by else None)
        tables.append({"name": name, "exists": exists, "columns": cols, "lines": usage["tables"].get(name, [])})
        if not exists and cols is None:
            problems.append({"code": "missing_table", "line": None, "name": name, "message": "declare it in tables"})

    envs = facts["environments"]
    secrets = []
    for key in sorted(usage["secrets"]):
        known = facts["secrets"].get(key)
        present = known["present"] if known else []
        hosts = known["hosts"] if known else None
        secrets.append({"key": key, "present_in": present, "missing_in": [e for e in envs if e not in present],
                        "allowed_hosts": hosts, "bound": bool(hosts), "lines": usage["secrets"][key]})

    for line in usage["dynamic_tables"]:
        warnings.append({"code": "dynamic_table_name", "line": line,
                         "message": "The table name is not a string literal; it can't be checked or self-tested"})
    for line in usage["dynamic_secrets"]:
        warnings.append({"code": "dynamic_secret_name", "line": line,
                         "message": "The secret name is not a string literal; it can't be checked"})
    for d in decls:
        if d["method"] != "GET" and d.get("auth", "none") == "none":
            warnings.append({"code": "public_write_route", "line": d["line"],
                             "message": f"{d['method']} {d['path']} is public (auth=\"none\") and writes data"})
    if scrubbed:
        warnings.append(dict(SCRUB_WARNING))

    problems = [{"code": e["code"], "line": e.get("line"), "message": e["message"]} for e in errors] + problems
    return {"routes": routes, "errors": errors, "conflicts": conflicts, "tables": tables, "secrets": secrets,
            "warnings": warnings, "_problems": problems, "_decls": decls}


def problem_line(p: dict[str, Any]) -> str:
    code = p["code"]
    if code == "missing_table":
        return f"- missing_table {p['name']}: declare it in tables"
    if code == "no_code":
        return "- no_code: call emit_module with the full module"
    if code == "module_name":
        return f"- module_name: {p['message']}"
    where = f" line {p['line']}" if p.get("line") else ""
    return f"- {code}{where}: {p['message']}"


# ── generation ───────────────────────────────────────────────────────────────


def _scrub(messages: list[dict[str, Any]], variants: set[str]) -> tuple[list[dict[str, Any]], bool]:
    from .vars_substitution import redact_deep

    if not variants:
        return messages, False
    clean = redact_deep(messages, variants, expanded=True)
    return clean, json.dumps(clean, default=str) != json.dumps(messages, default=str)


def _fenced_python(text: str | None) -> str | None:
    m = re.search(r"```(?:python|py)?[ \t]*\n(.*?)```", text or "", re.S)
    return m.group(1) if m else None


class Generation:
    """One generation. ``events()`` yields event bodies (no ``seq``); the last is ``result`` or ``error``."""

    def __init__(self, project_id: str, req: GenerateRequest, is_disconnected: Callable[[], Awaitable[bool]] | None = None,
                 timeout_s: float | None = None) -> None:
        self.project_id = project_id
        self.req = req
        self._is_disconnected = is_disconnected
        self.timeout_s = settings.module_gen_timeout_s if timeout_s is None else timeout_s
        self.model = settings.module_gen_model
        self.scrubbed = False
        self.last_draft: dict[str, Any] | None = None
        self.last_analysis: dict[str, Any] | None = None
        self.last_problems: list[dict[str, Any]] = []

    async def _race(self, coro: Awaitable[Any], deadline: float) -> Any:
        """Await ``coro`` while polling for disconnect and the deadline; cancel it on either."""
        loop = asyncio.get_running_loop()
        task = asyncio.ensure_future(coro)
        task.add_done_callback(lambda t: t.cancelled() or t.exception())
        try:
            while True:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    raise _Timeout()
                done, _ = await asyncio.wait({task}, timeout=min(POLL_S, remaining))
                if task in done:
                    return task.result()
                if self._is_disconnected is not None and await self._is_disconnected():
                    raise _Disconnected()
        finally:
            if not task.done():
                task.cancel()   # sandbox backends kill their process group on cancellation

    async def events(self) -> AsyncIterator[dict[str, Any]]:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.timeout_s
        try:
            async for ev in self._run(deadline):
                yield ev
        except _Disconnected:
            return
        except _Timeout:
            yield {"type": "error", "code": "timeout",
                   "message": f"generation did not finish within {self.timeout_s:g} s",
                   **self._draft_fields()}
        except ProjectError as e:
            yield {"type": "error", "code": e.code if e.code in ("llm_failed",) else "internal", "message": e.message}
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - one terminal error, details only in the log
            logger.exception("module generation failed")
            yield {"type": "error", "code": "internal", "message": "generation failed unexpectedly"}

    def _draft_fields(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if self.last_draft:
            out["draft"] = {"module_name": self.last_draft["module_name"], "source": self.last_draft["source"]}
        if self.last_analysis:
            out["analysis"] = self._public(self.last_analysis)
        return out

    @staticmethod
    def _public(a: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in a.items() if not k.startswith("_")}

    async def _db(self, fn: Callable[[Any], Awaitable[Any]]) -> Any:
        async with async_session_factory() as s:
            return await fn(s)

    async def _run(self, deadline: float) -> AsyncIterator[dict[str, Any]]:
        from ..engine import llm_provider
        from ..sdk.generate import catalogue, reference
        from .vars_substitution import expand_secret_variants

        req = self.req
        yield {"type": "status", "phase": "context", "attempt": 0, "message": "Gathering project context"}

        async def load(s: Any) -> tuple[ProjectModel, dict[str, Any], set[str], CodeModuleModel | None]:
            project = await s.get(ProjectModel, self.project_id)
            if project is None:
                raise ProjectError(404, "project_not_found", "Project not found")
            saved = await s.get(CodeModuleModel, req.module_id) if req.module_id else None
            return project, await gather_facts(s, project), await secret_values(s, project), saved

        project, facts, secrets, saved = await self._db(load)
        variants = expand_secret_variants(secrets)
        pinned_name = req.module_name or (saved.name if saved else None)
        source_in = req.current_source if req.current_source is not None else (saved.source if saved else None)

        messages: list[dict[str, Any]] = [{"role": "system", "content": build_system_prompt(
            reference(), catalogue(), facts)}]
        if source_in is not None:
            messages.append({"role": "user", "content":
                             f"Current module {pinned_name or 'module'}.py:\n```python\n{source_in}\n```"})
            messages.append({"role": "assistant", "content": "OK."})
        messages += [{"role": t.role, "content": t.content} for t in req.conversation[-KEEP_TURNS:]]
        messages.append({"role": "user", "content": req.prompt})

        for attempt in range(1, MAX_ATTEMPTS + 1):
            yield {"type": "status", "phase": "generating", "attempt": attempt,
                   "message": "Generating the module" if attempt == 1 else "Generating a fixed module"}
            outgoing, changed = _scrub(messages, variants)
            self.scrubbed = self.scrubbed or changed
            try:
                resp = await self._race(llm_provider.call_llm(
                    self.model, outgoing, temperature=0.2, tools=[EMIT_MODULE_TOOL], max_tokens=8000), deadline)
            except (_Disconnected, _Timeout):
                raise
            except Exception as exc:  # noqa: BLE001
                from .vars_substitution import redact_deep
                logger.warning("module generation: LLM call failed: %s", type(exc).__name__)
                yield {"type": "error", "code": "llm_failed",
                       "message": str(redact_deep(f"{type(exc).__name__}: {exc}", variants, expanded=True))[:500],
                       **self._draft_fields()}
                return

            call = next((tc for tc in resp.tool_calls if tc.name == "emit_module"), None)
            args = call.args if call is not None and isinstance(call.args, dict) else {}
            source = args.get("source") if isinstance(args.get("source"), str) else None
            if source is None:
                source = _fenced_python(resp.text)
            assistant = resp.get_assistant_message()
            if assistant.get("content") is None and not assistant.get("tool_calls"):
                assistant["content"] = ""

            if source is None:
                self.last_problems = [{"code": "no_code", "line": None, "message": "no module source in the reply"}]
                analysis = {"routes": {"created": [], "updated": [], "removed": []},
                            "errors": [{"code": "no_code", "line": None, "column": None,
                                        "message": "the reply contained no module source"}],
                            "conflicts": [], "tables": [], "secrets": [],
                            "warnings": [dict(SCRUB_WARNING)] if self.scrubbed else [], "_problems": self.last_problems}
                self.last_analysis = analysis
                yield {"type": "status", "phase": "analysing", "attempt": attempt, "message": "Checking the module"}
                yield {"type": "analysis", "attempt": attempt, **self._public(analysis)}
            else:
                emitted = args.get("module_name") if isinstance(args.get("module_name"), str) else None
                name = pinned_name or emitted or "generated"
                declared, declared_errors = declared_tables(args.get("tables"))
                summary = (args.get("summary") if isinstance(args.get("summary"), str) else "")[:300]
                self.last_draft = {"module_name": name, "source": source, "summary": summary, "tables": declared}
                yield {"type": "draft", "attempt": attempt, "module_name": name, "source": source,
                       "summary": summary, "tables": declared}
                yield {"type": "status", "phase": "analysing", "attempt": attempt, "message": "Checking the module"}

                async def do_analyse(s: Any) -> dict[str, Any]:
                    p = await s.get(ProjectModel, self.project_id)
                    return await analyse(s, p, source=source, module_id=req.module_id, module_name=name,
                                         name_from_model=pinned_name is None, declared=declared,
                                         declared_errors=declared_errors, scrubbed=self.scrubbed, facts=facts)
                analysis = await self._race(self._db(do_analyse), deadline)
                self.last_analysis = analysis
                self.last_problems = analysis["_problems"]
                yield {"type": "analysis", "attempt": attempt, **self._public(analysis)}

            if not self.last_problems:
                self_test = None
                if req.self_test:
                    async for ev in self._self_test(project, analysis, source, name, deadline):
                        if ev["type"] == "self_test":
                            self_test = ev["results"]   # `result.self_test` is the results array (or null), UI-DESIGN §10.1
                        yield ev
                yield {"type": "result", "module_name": name, "source": source, "summary": summary,
                       "attempts": attempt, "model": self.model, "analysis": self._public(analysis),
                       "self_test": self_test}
                return

            if attempt == MAX_ATTEMPTS:
                n = len(self.last_problems)
                yield {"type": "error", "code": "generation_invalid",
                       "message": f"the module still has {n} problem{'s' if n != 1 else ''} after {MAX_ATTEMPTS} attempts",
                       **self._draft_fields()}
                return
            yield {"type": "status", "phase": "repairing", "attempt": attempt,
                   "message": f"Fixing {len(self.last_problems)} problem(s)"}
            messages.append(assistant)
            if resp.tool_calls:
                tc = call or resp.tool_calls[0]
                messages.append({"role": "tool", "tool_call_id": tc.id, "name": tc.name, "content": "ok"})
            messages.append({"role": "user", "content": "\n".join(
                [REPAIR_HEADER] + [problem_line(p) for p in self.last_problems])})

    async def _self_test(self, project: ProjectModel, analysis: dict[str, Any], source: str, name: str,
                         deadline: float) -> AsyncIterator[dict[str, Any]]:
        from ..sandbox.backend import availability
        from .draft_runner import run_draft

        ok, _reason = availability()
        if not ok:
            return
        virtual = [t["name"] for t in analysis["tables"] if not t["exists"]]
        results: list[dict[str, Any]] = []
        ran = 0
        for d in analysis["_decls"]:
            label = f"{d['method']} {d['path']}"
            if d["method"] != "GET":
                results.append({"route": label, "skipped": "writes"})
            elif "{" in d["path"]:
                results.append({"route": label, "skipped": "path_params"})
            elif ran >= SELF_TEST_MAX_ROUTES:
                results.append({"route": label, "skipped": "limit"})
            else:
                ran += 1
                yield {"type": "status", "phase": "testing", "attempt": 0, "message": f"Testing {label}", "route": label}
                results.append(await self._race(self._self_test_one(project, d, label, source, name, virtual,
                                                                    self.req.module_id, run_draft), deadline))
        yield {"type": "self_test", "results": results}

    async def _self_test_one(self, project: ProjectModel, d: dict[str, Any], label: str, source: str, name: str,
                             virtual: list[str], module_id: str | None, run_draft: Any) -> dict[str, Any]:
        async with async_session_factory() as s:
            p = await s.get(ProjectModel, project.id)
            try:
                r = await run_draft(s, p, source=source, module_name=name, module_id=module_id, method="GET",
                                    path=d["path"], virtual_tables=virtual, trigger_type="generate_self_test",
                                    origin="generate_self_test", read_only=True, max_calls=SELF_TEST_MAX_CALLS,
                                    timeout_s=SELF_TEST_ROUTE_TIMEOUT_S)
            except ProjectError as e:
                return {"route": label, "ok": False, "status": e.status, "duration_ms": 0.0, "error_code": e.code,
                        "body_preview": json.dumps({"error": e.message})[:BODY_PREVIEW_CHARS], "execution_id": None}
        err = r.get("error")
        return {"route": label, "ok": err is None and int(r["status"]) < 400, "status": r["status"],
                "duration_ms": r["duration_ms"], "error_code": (err or {}).get("code") if err else None,
                "body_preview": json.dumps(r.get("body"), default=str)[:BODY_PREVIEW_CHARS],
                "execution_id": r["execution_id"]}
