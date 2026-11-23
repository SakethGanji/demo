"""Prompt → validated module draft (UI-DESIGN §10.1, ``POST /projects/{p}/modules/generate``).

Never writes modules, routes, tables or secrets. The only rows it creates are the self-test executions, throw-away scratch
copies of the module's tables (deleted at the end of the self-test) and project-knowledge rows (spec, decisions, answers,
routes, table schemas) after a successful build.

One generation = a loop inside a wall-clock budget (``settings.module_gen_timeout_s``):
LLM (a lean prompt plus lookup tools; ``emit_module`` for a new module, ``patch_module`` for every edit) → static analysis (the
same ``CodeModuleService._plan`` the dry run uses, plus AST checks) → repair turn when there are problems (3 attempts) → a
self-test of the routes (scenario + probes) against scratch tables, whose failures go back to the model for up to 3 fix
rounds. A reply cut off by the output limit is retried as a smaller step, and a large build is written in parts
(``remaining`` → ``patch_module`` add_function). Every outgoing message list is scrubbed of the project's and team's
secret values (all environments, all encodings) before it reaches ``call_llm``; self-test output, traces and table rows
are never put into LLM messages (only the failing assertions and a 200-char response preview are).
"""

from __future__ import annotations

import ast
import asyncio
import contextlib
import hashlib
import json
import logging
import re
from contextvars import ContextVar
from typing import Any, AsyncIterator, Awaitable, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import select

from ..core.config import settings
from ..db.models import CodeModuleModel, ProjectModel, VariableModel
from ..db.session import async_session_factory
from .backend_templates import checks, coverage, gaps, patching, prompts, quality, selftest
from .backend_templates import index as templates_index
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
MAX_LOOKUPS = 10             # lookup-tool rounds per model turn before the model must act
MAX_CONTINUATIONS = 5        # `remaining` parts written after the first emit
MAX_TRUNCATIONS = 3          # replies cut off by the output limit that are retried as a smaller step
MAX_FIX_ROUNDS = selftest.MAX_FIX_ROUNDS
MAX_ITERATIONS = 18
INLINE_SOURCE_CHARS = 6000   # below this the current module is pasted whole; above, the model gets an outline + read_function
MEMORY_TOKENS = 1200
# A complete backend (20+ routes with validation, joins, aggregates, a state machine) is 600-900 lines, roughly
# 10-16k output tokens; 8000 cut such modules off mid-file (the tool call then carries no usable source).
MAX_OUTPUT_TOKENS = 20000  # < 21333: above it the Anthropic SDK refuses a non-streaming call
REPAIR_HEADER = "Fix these problems in the module you emitted:"
SCRUB_WARNING = {"code": "secret_scrubbed",
                 "message": "A secret value in your code or prompt was removed before sending it to the AI"}

# One in-flight generation per (project, actor). The actor is client-supplied (X-User-Id or "local"), so this
# is best-effort per client. In-process on purpose: the engine is a single process; a second worker would
# need a DB lock.
_ACTIVE: set[tuple[str, str]] = set()
# The routes a self-test is limited to ("METHOD /path"): set around a module check after a patch so only what the patch
# touched is probed again. None = every route.
SELF_TEST_ONLY: ContextVar[frozenset[str] | None] = ContextVar("self_test_only", default=None)


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
    # Spec-first flow (see SPEC_RULES): ``clarify`` asks for a spec before any code. When the request leaves a gap the
    # generation ends with ``result.status == "clarify"`` (spec + questions, no source); send the same prompt back
    # with that ``spec`` and ``answers`` {question id: answer} for every question and the builder builds to the spec.
    clarify: bool = False
    spec: dict[str, Any] | None = None
    answers: dict[str, str] | None = None
    # Approved project plan: when the project has one and it names this module, the build follows the plan and asks nothing.
    # ``plan_module`` names the plan module outright; otherwise the module name or a ``[plan:<key>]`` token in the prompt does.
    plan_module: str | None = None
    use_plan: bool = True
    # Set by the build coordinator: a planned module that is already saved is only patched (patch_module) and a full re-emit
    # is refused. Left false, the generation may re-emit the module (the module editor, a deliberate rewrite).
    patch_only: bool = False

    @field_validator("current_source")
    @classmethod
    def _size(cls, v: str | None) -> str | None:
        if v is not None and len(v.encode("utf-8")) > MAX_SOURCE_BYTES:
            raise ValueError(f"current_source is larger than {MAX_SOURCE_BYTES} bytes")
        return v

    @field_validator("spec")
    @classmethod
    def _spec_size(cls, v: dict[str, Any] | None) -> dict[str, Any] | None:
        if v is not None and len(json.dumps(v, default=str)) > MAX_SPEC_CHARS:
            raise ValueError(f"spec is larger than {MAX_SPEC_CHARS} characters")
        return v

    @field_validator("answers")
    @classmethod
    def _answers_size(cls, v: dict[str, str] | None) -> dict[str, str] | None:
        if v is not None and (len(v) > MAX_QUESTIONS or any(len(a) > MAX_ANSWER_CHARS for a in v.values())):
            raise ValueError(f"at most {MAX_QUESTIONS} answers of at most {MAX_ANSWER_CHARS} characters each")
        return v

    @model_validator(mode="after")
    def _answers_cover_questions(self) -> GenerateRequest:
        if self.answers is None:
            return self
        if self.spec is None:
            raise ValueError("answers need the spec they answer (send back result.spec from the clarify step)")
        asked = [q.get("id") for q in self.spec.get("open_questions") or [] if isinstance(q, dict)]
        missing = [q for q in asked if not str(self.answers.get(q) or "").strip()]
        if missing:
            raise ValueError(f"unanswered questions: {', '.join(map(str, missing))}")
        return self


MAX_SPEC_CHARS = 60_000
MAX_QUESTIONS = 14
MAX_ANSWER_CHARS = 2000
SPEC_TOPICS = ("entities", "workflows", "roles", "external_systems", "acceptance_criteria", "other", "safety", "approvals",
               "state_machines", "retention", "ids", "import", "environments", "failure", "concurrency", "notifications",
               "credentials", "identity")

_FIELD_SCHEMA = {"type": "object", "required": ["name", "type"], "properties": {
    "name": {"type": "string"},
    "type": {"enum": ["string", "number", "integer", "boolean", "datetime", "json"]},
    "required": {"type": "boolean"}, "unique": {"type": "boolean"},
    "ref": {"type": "string", "description": "entity.field this column points at, e.g. runs.id"},
    "enum": {"type": "array", "items": {"type": "string"}}}}

EMIT_SPEC_TOOL: dict[str, Any] = {
    "name": "emit_spec",
    "description": "Emit the backend spec you would build from the request, and the questions whose answers it needs.",
    "parameters": {
        "type": "object", "required": ["summary", "entities", "workflows", "roles", "external_systems",
                                       "acceptance_criteria", "questions"],
        "properties": {
            "summary": {"type": "string", "maxLength": 400},
            "entities": {"type": "array", "items": {"type": "object", "required": ["name", "fields"], "properties": {
                "name": {"type": "string", "description": "snake_case table name"},
                "fields": {"type": "array", "items": _FIELD_SCHEMA}}}},
            "workflows": {"type": "array", "items": {"type": "object", "required": ["entity", "states", "transitions"],
                          "properties": {
                "entity": {"type": "string"}, "field": {"type": "string"},
                "states": {"type": "array", "items": {"type": "string"}},
                "transitions": {"type": "array", "items": {"type": "object", "required": ["from", "to"], "properties": {
                    "from": {"type": "string"}, "to": {"type": "string"}, "who": {"type": "string"},
                    "effect": {"type": "string"}}}}}}},
            "roles": {"type": "array", "items": {"type": "object", "required": ["name"], "properties": {
                "name": {"type": "string"}, "can": {"type": "array", "items": {"type": "string"}}}}},
            "external_systems": {"type": "array", "items": {"type": "object", "required": ["name", "use"],
                                 "properties": {"name": {"type": "string"}, "use": {"type": "string"}}}},
            "acceptance_criteria": {"type": "array", "items": {"type": "string"}},
            "limitations": {"type": "array", "items": {"type": "string"},
                            "description": "what this backend cannot guarantee (e.g. no verified caller identity); shown to the team"},
            "module_plan": {"type": "array", "description": "when the backend is large: the modules to build, one per area",
                            "items": {"type": "object", "required": ["name", "areas"], "properties": {
                                "name": {"type": "string"}, "areas": {"type": "array", "items": {"type": "string"}},
                                "routes": {"type": "integer"}}}},
            "questions": {"type": "array", "items": {"type": "object", "required": ["id", "topic", "question"],
                          "properties": {
                "id": {"type": "string"}, "topic": {"enum": list(SPEC_TOPICS)}, "question": {"type": "string"},
                "why": {"type": "string"}, "options": {"type": "array", "items": {"type": "string"}},
                "default": {"type": "string"}}}},
        },
    },
}

SPEC_RULES = """Before writing any code, act like a careful developer: write down the spec you would build and ask about
every gap. Reply by calling emit_spec. Do NOT write code now.

The spec covers:
- entities: every table, each field with type, required, unique, and `ref` for relations (e.g. run_results.run_id -> runs.id).
  id / created_at / updated_at are managed by the table; never list them.
- workflows: every status field as a state machine: states and transitions (from, to, who may do it, side effect).
- roles: who uses the backend and what each may do.
- external_systems: what it calls (the Jobs service, Jira, email, a target app, ...) and for what.
- acceptance_criteria: concrete, checkable statements ("POST /runs returns a job_id within 2 s").

Questions: ask instead of guessing. Ask one question for each thing the request leaves missing or ambiguous that would
change the schema, the routes, a state machine, permissions, or an integration. Do not ask about what the request
already states, and do not ask about style. Each question has a short id (q1, q2, ...), a topic, the question, why it
matters, and when sensible `options` and the `default` you would pick. If the request is complete, return no questions.
Fill the spec with your best reading so the team can correct it.

Think about the DOMAIN, not only the schema: dangerous or irreversible actions and what guards them, who approves what, the
state machine of every status (final states, reopening, illegal moves), data retention, id schemes, import from existing
tools, environments, failure semantics (retry, blocked, flaky), concurrency and repeated calls. The builder adds a standard set
of these itself, so focus on what is specific to THIS request. Options must be real choices for this domain: never offer
mock/demo credentials, browser-supplied secrets or roles taken from request headers. Put what the platform cannot guarantee
(for example: no verified caller identity) in `limitations`. If the backend is large (over ~12 routes or several areas), propose
`module_plan`: one module per area.
""" + gaps.SECURITY_RULES


def gap_questions(spec: dict[str, Any], known_entities: Any = None) -> list[dict[str, Any]]:
    """Questions any spec needs answered regardless of what the model asked: the checks a careful developer runs.
    ``known_entities`` (tables the project already has, tables other modules designed) are valid relation targets."""
    out: list[dict[str, Any]] = []

    def ask(qid: str, topic: str, question: str, why: str) -> None:
        out.append({"id": qid, "topic": topic, "question": question, "why": why, "source": "check"})

    entities = [e for e in spec.get("entities") or [] if isinstance(e, dict)]
    if not entities:
        ask("chk_entities", "entities", "Which records does this backend store, and which fields does each have?",
            "No entities could be read from the request.")
    for e in entities:
        fields = [f for f in e.get("fields") or [] if isinstance(f, dict)]
        name = str(e.get("name") or "?")
        if not fields:
            ask(f"chk_fields_{name}", "entities", f"Which fields does {name} have (name, type, required, unique)?",
                f"{name} has no fields.")
        elif not any(f.get("required") for f in fields):
            ask(f"chk_required_{name}", "entities", f"Which fields of {name} are required?",
                f"No field of {name} is marked required, so any empty record would be accepted.")
    names = {str(e.get("name")) for e in entities} | {str(x) for x in known_entities or ()}
    for e in entities:
        for f in e.get("fields") or []:
            ref = f.get("ref") if isinstance(f, dict) else None
            if isinstance(ref, str) and ref.split(".")[0] not in names:
                ask(f"chk_ref_{e.get('name')}_{f.get('name')}", "entities",
                    f"{e.get('name')}.{f.get('name')} points at {ref}, which is not in the spec. Which entity is it?",
                    "A relation to an unknown entity.")
    for w in spec.get("workflows") or []:
        if isinstance(w, dict) and w.get("states") and not w.get("transitions"):
            ask(f"chk_transitions_{w.get('entity')}", "workflows",
                f"Which status changes are allowed for {w.get('entity')}, and who may make each?",
                "States are listed without transitions.")
    if not spec.get("roles"):
        ask("chk_roles", "roles", "Who uses this backend, and what may each role do?", "No roles in the request.")
    if not spec.get("acceptance_criteria"):
        ask("chk_acceptance", "acceptance_criteria", "How will you check it works? List the outcomes it must meet.",
            "No acceptance criteria in the request.")
    return out


def merge_questions(model_qs: Any, checks: list[dict[str, Any]], context: str = "", answered: Any = None) -> list[dict[str, Any]]:
    """``answered``: domain topics an earlier module's dialogue already settled; the model does not ask them again either."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    topics_asked: set[str] = set()
    settled = set(answered or ()) & set(gaps.DOMAIN_TOPICS)
    for q in (model_qs if isinstance(model_qs, list) else [])[:4]:     # the standard domain questions need room too
        if not isinstance(q, dict) or not str(q.get("question") or "").strip():
            continue
        if q.get("topic") in settled:
            continue
        qid = re.sub(r"[^a-z0-9_]", "_", str(q.get("id") or f"q{len(out) + 1}").lower())[:40] or f"q{len(out) + 1}"
        while qid in seen:
            qid += "_"
        seen.add(qid)
        topic = q.get("topic") if q.get("topic") in SPEC_TOPICS else "other"
        topics_asked.add(topic)
        opts = gaps.clean_options(q.get("options"), context + " " + str(q["question"]))
        out.append({"id": qid, "topic": topic, "question": str(q["question"])[:500],
                    "why": str(q.get("why") or "")[:300], "options": opts,
                    "default": gaps.clean_default(q.get("default"), opts, context), "source": "model"})
    structural = [c for c in checks if c.get("source") == "check"]
    domain = [c for c in checks if c.get("source") == "domain"]
    for c in structural:   # a check the model already asked about (same topic, not entity-specific) is not asked twice
        if c["id"] in ("chk_roles", "chk_acceptance", "chk_entities") and c["topic"] in topics_asked:
            continue
        if c["id"] not in seen:
            seen.add(c["id"])
            out.append({**c, "options": [], "default": None})
    for c in domain:       # one question per domain topic: the model's own wins
        if c["topic"] in topics_asked or c["id"] in seen:
            continue
        seen.add(c["id"])
        opts = gaps.clean_options(c.get("options"), context)
        out.append({**c, "options": opts, "default": gaps.clean_default(c.get("default"), opts, context)})
    return out[:MAX_QUESTIONS]


def spec_message(spec: dict[str, Any], answers: dict[str, str] | None) -> str:
    body = {k: v for k, v in spec.items() if k not in ("open_questions", "answered", "round")}
    qa = [{"question": q.get("question"), "answer": q.get("answer")}
          for q in spec.get("answered") or [] if isinstance(q, dict)]
    qa += [{"question": q.get("question"), "answer": (answers or {}).get(q.get("id"))}
           for q in spec.get("open_questions") or [] if isinstance(q, dict)]
    return ("The team agreed this spec. Build exactly this: every entity as a declared table with these fields, every "
            "workflow as a TRANSITIONS map enforced by its routes, role checks where roles differ, and routes that "
            "meet every acceptance criterion. Where an answer below changes the spec, the answer wins.\n"
            f"Spec:\n{json.dumps(body, indent=1, default=str)}\n"
            + (f"Answers:\n{json.dumps(qa, indent=1)}" if qa else ""))


EMIT_MODULE_TOOL: dict[str, Any] = {
    "name": "emit_module",
    "description": ("Emit a complete NEW Python module (its routes) with the data tables it needs. For a large backend emit the "
                    "first area and list the rest in `remaining`. Changes to an existing module use patch_module."),
    "parameters": {
        "type": "object", "required": ["module_name", "source", "summary"],
        "properties": {
            "module_name": {"type": "string", "description": "snake_case module name, e.g. refunds"},
            "source": {"type": "string", "description": "The complete module source."},
            "summary": {"type": "string", "maxLength": 300},
            "self_test": selftest.STEP_SCHEMA,
            "decisions": {"type": "array", "items": {"type": "string"},
                          "description": "design decisions and known limitations worth remembering (one sentence each)"},
            "remaining": {"type": "array", "items": {"type": "string"},
                          "description": "areas of a large build NOT written yet; empty when the module is complete"},
            "planned_routes": {"type": "array", "items": {"type": "string"},
                               "description": ("EVERY route this module will have when finished, as 'METHOD /path' (e.g. "
                                               "'POST /runs'), including the ones you write in later parts. The builder "
                                               "checks the saved module against this list and asks for what is missing.")},
            "next_modules": {"type": "array", "items": {"type": "string"},
                             "description": "other modules this backend should be split into (one per area), to build next"},
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


def _truncated(resp: Any) -> bool:
    """The reply hit the output limit. Only some providers set ``output_truncated``; the others show it in the stop reason or
    in an output token count at the cap, and a cut-off reply is never a finished one."""
    if getattr(resp, "output_truncated", False):
        return True
    for attr in ("stop_reason", "finish_reason"):
        if str(getattr(resp, attr, "") or "").lower() in ("max_tokens", "length", "max_output_tokens"):
            return True
    out = getattr(getattr(resp, "usage", None), "output_tokens", 0)
    return isinstance(out, int) and out >= int(MAX_OUTPUT_TOKENS * 0.98)


class _Disconnected(Exception):
    pass


class _Timeout(Exception):
    pass


# ── system prompt ────────────────────────────────────────────────────────────

RULES = prompts.RULES   # the core rules; patterns are templates, exact signatures are SDK lookups (see backend_templates/)


def _fmt_columns(cols: list[dict[str, Any]] | None) -> str:
    return ", ".join(f"{c['name']} {c['type']}{'' if not c.get('required') else '*'}" for c in cols or []) or "(no columns)"


def build_system_prompt(reference: dict[str, Any], catalogue: list[dict[str, Any]], facts: dict[str, Any]) -> str:
    """The lean prompt: core rules + an index of what exists. Table columns, route fields, SDK signatures, node parameters and
    patterns are fetched by the model through lookup tools, so the prompt does not grow with the project."""
    return prompts.build_lean_prompt(catalogue, facts, templates_index.index_lines())


async def gather_facts(session: Any, project: ProjectModel) -> dict[str, Any]:
    from .api_route_service import ApiRouteService
    from .data_table_service import DataTableService
    from .secret_service import SecretService

    tables = [t for t in await DataTableService(session).list_tables(project) if not selftest.SCRATCH_RE.search(t["name"])]
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
    from .connection_service import facts_for

    connections = await facts_for(session, project.id)
    return {"slug": project.slug, "environments": envs, "default_environment": project.default_environment,
            "connections": connections,
            "tables": tables, "routes": routes, "modules": [m.name for m in mods], "secrets": secrets,
            "egress": list((project.settings or {}).get("egress_allowlist") or []), "workflows": workflows,
            "jobs": _jobs_configured()}


def _jobs_configured() -> bool:
    from ..broker.jobs import config

    return bool(config().token)


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


SYSTEM_COLUMNS = ("id", "created_at", "updated_at")
_WRITE_ARG = {"insert": 0, "update": 1, "upsert": 1}  # positional index of the row data


def scan_writes(source: str) -> list[dict[str, Any]]:
    """Literal keys written to a literal table: ``[{table, column, line}]``.

    Catches ``studio.db.table("t").insert({...})`` and ``x = studio.db.table("t")`` then ``x.update(id, {...})``
    (also ``data=``, and a list of dicts on insert). Anything not written as a literal is skipped, never guessed."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    def table_of(node: ast.AST, bound: dict[str, str]) -> str | None:
        if isinstance(node, ast.Call) and _attr_chain(node.func) in ("studio.db.table", "db.table") and node.args \
                and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
            return node.args[0].value
        if isinstance(node, ast.Name):
            return bound.get(node.id)
        return None

    # Names bound to a table, per scope (module, each function). A complex module reuses short names (``t = ...``) for
    # different tables in different routes; one module-wide map would check a write against the wrong table. A name bound
    # to two different tables in one scope is ambiguous and skipped (never guessed).
    _AMBIGUOUS = "\0"

    def bindings(scope: ast.AST) -> dict[str, str]:
        out: dict[str, str] = {}
        for n in _walk_scope(scope):
            if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name):
                t = table_of(n.value, {})
                if t:
                    name = n.targets[0].id
                    out[name] = t if out.get(name, t) == t else _AMBIGUOUS
        return out

    module_bound = bindings(tree)
    scopes: list[tuple[ast.AST, dict[str, str]]] = [(tree, module_bound)]
    for f in ast.walk(tree):
        if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            scopes.append((f, {**module_bound, **bindings(f)}))
    out: list[dict[str, Any]] = []
    for scope, bound in scopes:
        out += _writes_in(scope, {k: v for k, v in bound.items() if v != _AMBIGUOUS}, table_of)
    return sorted(out, key=lambda w: (w["line"], w["table"], w["column"]))


def _walk_scope(scope: ast.AST):
    """The nodes of one scope: ``ast.walk`` that does not descend into nested functions/classes."""
    todo = list(ast.iter_child_nodes(scope))
    while todo:
        n = todo.pop()
        yield n
        if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            todo.extend(ast.iter_child_nodes(n))


def _writes_in(scope: ast.AST, bound: dict[str, str], table_of: Callable[..., str | None]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for n in _walk_scope(scope):
        if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in _WRITE_ARG):
            continue
        table = table_of(n.func.value, bound)
        if table is None:
            continue
        i = _WRITE_ARG[n.func.attr]
        data = n.args[i] if len(n.args) > i else next((k.value for k in n.keywords if k.arg == "data"), None)
        dicts = data.elts if isinstance(data, ast.List) else [data]
        for d in dicts:
            if isinstance(d, ast.Dict):
                out += [{"table": table, "column": k.value, "line": n.lineno}
                        for k in d.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)]
    return out


SDK_NAMES = {"api": "from studio import api", "studio": "import studio",
             "Response": "from studio import Response", "schedule": "from studio import schedule", "HTTPError": "from studio import HTTPError"}


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


# ── approved plan ────────────────────────────────────────────────────────────

_PLAN_TOKEN = re.compile(r"\[plan:([A-Za-z0-9_.-]+)\]")


def resolve_plan_module(body: dict[str, Any], plan_module: str | None, module_name: str | None, prompt: str) -> str | None:
    """The plan module this build is for: the explicit request field, then the module name, then a ``[plan:<key>]`` token in
    the prompt. Only keys the plan actually has count."""
    keys = {str(m.get("key")) for m in body.get("modules") or [] if isinstance(m, dict)}
    m = _PLAN_TOKEN.search(prompt or "")
    for cand in (plan_module, module_name, m.group(1) if m else None):
        if cand and cand in keys:
            return cand
    return None


def plan_context(body: dict[str, Any], version: Any, key: str) -> dict[str, Any]:
    """What the build checks need from the plan, flattened: its tables (columns, owner, writers), this module's owned tables,
    the shared module and its helper names."""
    tables = {str(t["name"]): t for t in (body.get("data") or {}).get("tables") or [] if isinstance(t, dict) and t.get("name")}
    mods = [m for m in body.get("modules") or [] if isinstance(m, dict)]
    me = next((m for m in mods if m.get("key") == key), {})
    owned = [str(n) for n in me.get("owns") or []] or [n for n, t in tables.items() if t.get("owner") == key]
    shared = next((m for m in mods if m.get("kind") == "shared"), None)
    helpers = [str(h.get("name")) for h in (shared or {}).get("helpers") or [] if isinstance(h, dict) and h.get("name")]
    return {"version": version, "key": key, "tables": tables, "owned": [n for n in owned if n in tables],
            "shared": (shared or {}).get("key") if shared and shared.get("key") != key else None,
            "helpers": helpers if shared and shared.get("key") != key else []}


def plan_declared(ctx: dict[str, Any]) -> list[dict[str, Any]]:
    """The tables a module of this plan declares: its owned tables, exactly as planned."""
    raw = [{"name": n, "columns": [{k: c[k] for k in ("name", "type", "required", "default", "unique", "enum", "ref") if k in c}
                                   for c in ctx["tables"][n].get("columns") or [] if isinstance(c, dict)]}
           for n in ctx["owned"]]
    out, _errors = declared_tables(raw)
    return out


def plan_problems(ctx: dict[str, Any], *, source: str, usage: dict[str, Any], existing: dict[str, Any],
                  model_tables: set[str], shared_source: str | None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(repair problems, warnings) from comparing the module with the approved plan: tables and columns the plan does not have,
    writes to tables another module owns, helpers the shared module already provides, planned columns still to be added."""
    from .backend_templates import helpers_dup

    problems: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    planned = ctx["tables"]
    key = ctx["key"]
    for name in sorted((set(usage["tables"]) | set(model_tables)) - set(planned)):
        line = (usage["tables"].get(name) or [None])[0]
        problems.append({"code": "unplanned_table", "line": line, "name": name,
                         "message": f"table '{name}' is not in the approved plan (v{ctx['version']}); use a planned table "
                                    f"({', '.join(sorted(planned)[:12]) or 'none'}) or ask for a plan revision"})
    seen: set[tuple[str, str]] = set()
    foreign: set[str] = set()
    for w in scan_writes(source):
        t = planned.get(w["table"])
        if t is None:
            continue
        have = {c["name"] for c in t.get("columns") or [] if isinstance(c, dict)}
        have |= {c["name"] for c in (existing.get(w["table"]) or {}).get("columns") or []} | set(SYSTEM_COLUMNS)
        if w["column"] not in have and (w["table"], w["column"]) not in seen:
            seen.add((w["table"], w["column"]))
            problems.append({"code": "unplanned_column", "line": w["line"], "name": w["table"],
                             "message": f"column '{w['column']}' of '{w['table']}' is not in the approved plan "
                                        f"(planned: {', '.join(sorted(have - set(SYSTEM_COLUMNS))) or 'none'})"})
        owner = t.get("owner")
        if owner and owner != key and key not in (t.get("writers") or []) and w["table"] not in foreign:
            foreign.add(w["table"])
            problems.append({"code": "foreign_write", "line": w["line"], "name": w["table"],
                             "message": f"'{w['table']}' is owned by module '{owner}'; this module may not write it "
                                        f"(call a route or helper of '{owner}', or ask for a plan revision)"})
    if ctx.get("shared"):
        try:
            have_fp = helpers_dup.helper_fingerprints(shared_source or "")
            mine = helpers_dup.helper_fingerprints(source)
        except Exception:  # noqa: BLE001 - a check must never take the build down
            have_fp, mine = {}, {}
        by_hash = {h: n for n, h in have_fp.items()}
        for name, h in mine.items():
            hit = by_hash.get(h) or (name if name in set(ctx["helpers"]) | set(have_fp) else None)
            if hit:
                problems.append({"code": "duplicate_helper", "line": None, "name": name,
                                 "message": f"'{name}' duplicates the shared helper '{hit}' of module '{ctx['shared']}': "
                                            f"delete it and import it: `from {ctx['shared']} import {hit}`"})
    for name in ctx["owned"]:
        cur = existing.get(name)
        if cur is None:
            continue
        have = {c["name"] for c in cur.get("columns") or []}
        pend = [c["name"] for c in planned[name].get("columns") or [] if isinstance(c, dict) and c.get("name") not in have]
        if pend:
            warnings.append({"code": "schema_pending", "line": None,
                             "message": f"{len(pend)} planned column(s) will be added to '{name}' on save: {', '.join(pend[:10])}"})
    return problems, warnings


async def analyse(session: Any, project: ProjectModel, *, source: str, module_id: str | None, module_name: str,
                  name_from_model: bool, declared: list[dict[str, Any]], declared_errors: list[dict[str, Any]],
                  scrubbed: bool, facts: dict[str, Any], plan: dict[str, Any] | None = None) -> dict[str, Any]:
    """§10.1 step 2. ``plan`` (from ``plan_context``) adds the approved-plan checks. Returns the ``analysis`` event body plus ``problems`` (what a repair turn lists)."""
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
        rplan = await CodeModuleService(session)._plan(project, module_id, source,
                                                       old_source=saved.source if saved else None, collect=clashes)
        svc = CodeModuleService(session)
        summary = svc._summary(project, rplan)
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
        if plan and name in plan["tables"]:     # the plan's columns are what the table will have once this module is saved
            pc = [c for c in plan["tables"][name].get("columns") or [] if isinstance(c, dict)]
            cols = (cols or []) + [c for c in pc if c.get("name") not in {x["name"] for x in cols or []}]
        tables.append({"name": name, "exists": exists, "columns": cols, "lines": usage["tables"].get(name, [])})
        if not exists and cols is None and not (plan and name not in plan["tables"]):
            problems.append({"code": "missing_table", "line": None, "name": name, "message": "declare it in tables"})

    seen: set[tuple[str, str]] = set()
    for w in scan_writes(source):
        cols = next((t["columns"] for t in tables if t["name"] == w["table"]), None)
        key = (w["table"], w["column"])
        if cols is None or key in seen:
            continue
        seen.add(key)
        if w["column"] in SYSTEM_COLUMNS:
            problems.append({"code": "system_column", "line": w["line"], "name": w["table"],
                             "message": f"'{w['column']}' is managed by table '{w['table']}'; don't write it"})
        elif w["column"] not in {c["name"] for c in cols}:
            if plan and w["table"] in plan["tables"]:
                continue        # plan_problems reports it as unplanned_column
            problems.append({"code": "unknown_column", "line": w["line"], "name": w["table"],
                             "message": f"table '{w['table']}' has no column '{w['column']}' "
                                        f"(columns: {', '.join(c['name'] for c in cols) or 'none'})"})

    if plan:
        shared_src = None
        if plan.get("shared"):
            shared_src = (await session.execute(select(CodeModuleModel.source).where(
                CodeModuleModel.project_id == project.id, CodeModuleModel.name == plan["shared"]))).scalar_one_or_none()
        pp_problems, pp_warnings = plan_problems(plan, source=source, usage=usage, existing=existing,
                                                 model_tables=set(plan.get("model_tables") or ()), shared_source=shared_src)
        problems += pp_problems
        warnings += pp_warnings

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


FINAL_NAMES = ("emit_module", "patch_module")

_GET_NODE_TOOL = {"name": "get_node", "description": "Parameters of one studio.nodes function (name, type, required, default, options).",
                  "parameters": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}}
_READ_FUNCTION_TOOL = {"name": "read_function",
                       "description": "Source of top-level functions/constants of the module being edited (decorators included).",
                       "parameters": {"type": "object", "required": ["names"],
                                      "properties": {"names": {"type": "array", "items": {"type": "string"}},
                                                     "offset": {"type": "integer", "description": "continue a truncated result from this character"}}}}
_SEARCH_TEMPLATES_TOOL = {"name": "search_backend_templates", "description": "Find a proven backend pattern for a need.",
                          "parameters": {"type": "object", "required": ["query"], "properties": {"query": {"type": "string"}}}}
_GET_TEMPLATE_TOOL = {"name": "get_backend_template",
                      "description": ("Runnable pattern to adapt: crud-filters, job-run-poll-sync, approval-state-machine, "
                                      "dashboard-stats."),
                      "parameters": {"type": "object", "required": ["name"], "properties": {"name": {"type": "string"}}}}
_PLATFORM_LOOKUPS = ("search_sdk", "get_sdk_symbol", "get_table_schema", "get_route_signature", "search_project_knowledge")
LOOKUP_NAMES = frozenset(_PLATFORM_LOOKUPS) | {"get_node", "read_function", "search_backend_templates", "get_backend_template"}
MAX_LOOKUP_CHARS = 7000


def cut_lookup(text: str, offset: Any = 0) -> str:
    """One window of a lookup result. A cut is never silent: it says how much is left and how to get it."""
    try:
        start = max(0, int(offset or 0))
    except (TypeError, ValueError):
        start = 0
    chunk = text[start:start + MAX_LOOKUP_CHARS]
    left = len(text) - start - len(chunk)
    if left > 0:
        return chunk + f"\n[truncated: {left} more chars; call read_function with offset={start + len(chunk)} (fewer names per call also helps)]"
    return chunk


def lookup_tools(with_read: bool) -> list[dict[str, Any]]:
    """Lookup tools in the provider's ``parameters`` format."""
    from .platform_index import TOOLS

    out = [{"name": t["name"], "description": t["description"], "parameters": t["input_schema"]}
           for t in TOOLS if t["name"] in _PLATFORM_LOOKUPS]
    out += [_GET_NODE_TOOL, _SEARCH_TEMPLATES_TOOL, _GET_TEMPLATE_TOOL] + ([_READ_FUNCTION_TOOL] if with_read else [])
    return out


TRUNC_NEW = ("Your reply was cut off by the output limit, so nothing was saved. Emit a SMALLER first step: emit_module with the "
             "tables, shared helpers and at most ~6 routes of the most important area, and list every area still missing in "
             "`remaining`. Keep docstrings short. You will be asked to add the rest with patch_module.")
TRUNC_PATCH = ("Your reply was cut off by the output limit, so that patch was not applied. Send a smaller patch: at most 4 "
               "functions per call, short docstrings, and `remaining` for what is still to do.")
CONTINUE = ("Applied. The module now has the routes listed in the outline below. Continue with patch_module (add_function ops, at "
            "most ~8 routes) for the remaining areas: {remaining}. Set `remaining` to [] in the call that finishes the module. "
            "Module outline:\n{outline}")
OWED = (" You still owe these routes (planned or requested, not in the module yet): {owed}. Write them now with "
        "patch_module (add_function); do not finish the build while any is missing.")
SELF_FIX = ("The module was applied and its self-test ran against scratch copies of its tables. These checks FAILED (each shows "
            "the real response). For each, decide: if the module is wrong, fix it with patch_module; if the expectation is wrong, "
            "send a corrected step with the same id in `self_test`. Never weaken a check that is right.")


def schedule_hint(analysis: dict[str, Any], project_slug: str | None = None) -> dict[str, Any] | None:
    """How to run a ``sync_pending_runs``-style route without a browser polling: a ``@schedule`` function in the module."""
    routes = (analysis.get("routes") or {})
    for r in (routes.get("created") or []) + (routes.get("updated") or []):
        if "sync_pending" in str(r.get("function") or "") or "sync-pending" in str(r.get("path") or "") or \
                "sync_pending" in str(r.get("path") or ""):
            return {"route": f"{r['method']} {r['path']}", "every_seconds": 30,
                    "how": ("Add `@schedule(every_seconds=30)` (from studio import schedule) to a function that runs the same sync "
                            "logic as this route. The engine runs it in the background, so results are persisted without a "
                            "browser polling; status at GET /api/projects/<project>/schedules.")}
    return None


def _drain(notes: list[str]) -> str:
    if not notes:
        return ""
    text = "\nAlso: " + "; ".join(notes[:6]) + " (use steps like {\"call\": \"POST /cases\", \"body\": {...}, \"expect\": {\"status\": 201}, \"save\": {\"cid\": \"id\"}})"
    notes.clear()
    return text


def _parses(source: str) -> bool:
    try:
        ast.parse(source)
    except SyntaxError:
        return False
    return True


PATCH_HELP = ("patch_module needs ops: a non-empty list like [{\"op\": \"replace_function\", \"name\": \"metrics_summary\", "
              "\"source\": \"@api.get(...)\\ndef metrics_summary(req): ...\"}, {\"op\": \"add_function\", \"source\": \"...\"}]")


def _extra_problems(source: str) -> list[dict[str, Any]]:
    """Problems beyond syntax and the platform's own plan: names nothing defines, Jobs fields the service ignores, credentials
    from the browser, roles from a header."""
    out: list[dict[str, Any]] = []
    sdk = set(SDK_NAMES)
    for name, line in patching.undefined_names(source):
        if name not in sdk:
            out.append({"code": "undefined_name", "line": line, "message": f"'{name}' is used but defined nowhere in the module"})
    return out + checks.all_problems(source) + checks.job_contract_problems(source) + quality.placeholder_problems(source)


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
        self.memory = ""                     # retrieved project knowledge (a string for the prompt)
        self.stored_plan: list[dict[str, Any]] = []
        self.prompt_chars = 0
        self.stats: dict[str, Any] = {"lookups": 0, "truncations": 0, "continuations": 0, "fix_rounds": 0, "patches": 0,
                                      "model_calls": 0}
        self.max_attempts = MAX_ATTEMPTS          # repair rounds are attempts - 1; a patch of a saved scaffold gets one
        self.max_fix_rounds = MAX_FIX_ROUNDS

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
        async for ev in self._events():
            if ev.get("type") == "error" and "stats" not in ev:       # a failed run still says what it cost
                ev = {**ev, "stats": dict(self.stats)}
            yield ev

    async def _events(self) -> AsyncIterator[dict[str, Any]]:
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
        except Exception:  # one terminal error, details only in the log
            logger.exception("module generation failed")
            yield {"type": "error", "code": "internal", "message": "generation failed unexpectedly"}

    def _draft_fields(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if self.last_draft:
            out["draft"] = {"module_name": self.last_draft["module_name"], "source": self.last_draft["source"],
                            "tables": self.last_draft.get("tables") or []}
        if self.last_analysis:
            out["analysis"] = self._public(self.last_analysis)
        return out

    @staticmethod
    def _public(a: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in a.items() if not k.startswith("_")}

    async def _db(self, fn: Callable[[Any], Awaitable[Any]]) -> Any:
        async with async_session_factory() as s:
            return await fn(s)

    # ── lookups ──────────────────────────────────────────────────────────────

    async def _lookup(self, tc: Any, source_cur: str | None) -> str:
        """Answer one lookup tool call. Never raises: an error is text the model reads."""
        from ..sdk.generate import catalogue
        from .platform_index import dispatch_tool

        args = tc.args if isinstance(tc.args, dict) else {}
        try:
            if tc.name == "read_function":
                names = args.get("names") if isinstance(args.get("names"), list) else [args.get("name")]
                out: Any = patching.function_sources(source_cur or "", [str(n) for n in names if n])
            elif tc.name == "get_node":
                want = str(args.get("name") or "").strip()
                node = next((f for f in catalogue() if f["function"] == want), None)
                out = ({"function": node["function"], "description": node.get("description"), "python": node.get("python"),
                        "egress": node.get("egress")} if node else {"error": f"no studio.nodes function '{want}'"})
            elif tc.name == "search_backend_templates":
                out = templates_index.search_backend_templates(str(args.get("query") or ""))
            elif tc.name == "get_backend_template":
                out = templates_index.get_backend_template(str(args.get("name") or "")) or {
                    "error": f"no template '{args.get('name')}'; one of {', '.join(templates_index.names())}"}
            else:
                out = await self._db(lambda s: dispatch_tool(s, self.project_id, tc.name, args))
        except Exception as e:  # noqa: BLE001
            out = {"error": f"{type(e).__name__}: {str(e)[:200]}"}
        text = prompts.tool_json(out)
        return cut_lookup(text, args.get("offset") if tc.name == "read_function" else 0)

    async def _turn(self, messages: list[dict[str, Any]], final_tools: list[dict[str, Any]], variants: set[str],
                    deadline: float, source_cur: str | None) -> tuple[Any, Any]:
        """One model turn. Lookup tool calls are answered and the model is asked again (up to MAX_LOOKUPS rounds, then lookups are
        withdrawn). Returns ``(response, final call or None)``; ``messages`` keeps the lookup exchanges."""
        from ..engine import llm_provider

        rounds = 0
        while True:
            if rounds == MAX_LOOKUPS:       # budget used: say so, then withdraw the lookups so the next call must be an edit
                messages.append({"role": "user", "content": "Lookup budget used. Act now: call patch_module (or emit_module) with "
                                                            "what you have learned. An empty ops list is not an answer."})
                rounds += 1
            tools = final_tools + (lookup_tools(source_cur is not None) if rounds < MAX_LOOKUPS else [])
            outgoing, changed = _scrub(messages, variants)
            self.scrubbed = self.scrubbed or changed
            self.stats["model_calls"] += 1
            resp = await self._race(llm_provider.call_llm(
                self.model, outgoing, temperature=0.2, tools=tools, tool_choice="required", max_tokens=MAX_OUTPUT_TOKENS), deadline)
            calls = getattr(resp, "tool_calls", None) or []
            finals = [tc for tc in calls if tc.name in FINAL_NAMES]
            lookups = [tc for tc in calls if tc.name in LOOKUP_NAMES]
            if finals or not lookups or _truncated(resp):
                return resp, (finals[0] if finals else None)
            assistant = resp.get_assistant_message()
            if assistant.get("content") is None and not assistant.get("tool_calls"):
                assistant["content"] = ""
            messages.append(assistant)
            for tc in calls:
                body = await self._lookup(tc, source_cur) if tc.name in LOOKUP_NAMES else "unknown tool"
                messages.append({"role": "tool", "tool_call_id": tc.id, "name": tc.name, "content": body})
            rounds += 1
            self.stats["lookups"] += len(lookups)

    @staticmethod
    def _answer_calls(messages: list[dict[str, Any]], resp: Any, call: Any, note: str) -> None:
        """Record the model's reply and answer EVERY tool call in it (providers refuse a dangling tool_use)."""
        assistant = resp.get_assistant_message()
        if assistant.get("content") is None and not assistant.get("tool_calls"):
            assistant["content"] = ""
        messages.append(assistant)
        for tc in getattr(resp, "tool_calls", None) or []:
            messages.append({"role": "tool", "tool_call_id": tc.id, "name": tc.name,
                             "content": note if tc is call else "not run: act on the main call first"})

    # ── the run ──────────────────────────────────────────────────────────────

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
            saved = await s.get(CodeModuleModel, req.module_id) if req.module_id else await self._saved_by_plan_name(s, project)
            await self._load_memory(s, project, saved)
            return project, await gather_facts(s, project), await secret_values(s, project), saved

        project, facts, secrets, saved = await self._db(load)
        variants = expand_secret_variants(secrets)
        pinned_name = req.module_name or (saved.name if saved else None)
        plan_ctx, plan_rec = None, None
        if req.use_plan:
            plan_rec = await self._approved_plan()
            key = resolve_plan_module(plan_rec["body"], req.plan_module, pinned_name, req.prompt) if plan_rec else None
            if key:
                plan_ctx = plan_context(plan_rec["body"], plan_rec.get("version"), key)
        if saved is not None and req.module_id is None:
            # (re)building a planned module that is already saved is an update of it, not a second module with its name
            if plan_ctx is not None and saved.name == plan_ctx["key"]:
                req = self.req = req.model_copy(update={"module_id": saved.id})
                pinned_name = saved.name
            else:
                saved = None
        source_in = req.current_source if req.current_source is not None else (saved.source if saved else None)
        plan_raw: list[dict[str, Any]] = list(self.stored_plan)

        system = build_system_prompt(reference(), catalogue(), facts)
        patch_only = plan_ctx is not None and source_in is not None and req.patch_only
        if patch_only:                     # patching a saved scaffold: one repair round, then the leftovers are reported as gaps
            self.max_attempts, self.max_fix_rounds = min(self.max_attempts, 2), min(self.max_fix_rounds, 1)
        if plan_ctx is not None:           # a planned build gets the exact SDK inline, so lookups are rarely needed
            system += "\n\n" + prompts.SDK_REFERENCE
        self.prompt_chars = len(system)
        messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
        if self.memory:
            messages += [{"role": "user", "content": "Project memory (retrieved for this request; look up more with the tools):\n"
                                                     + self.memory}, {"role": "assistant", "content": "OK."}]
        if source_in is not None:
            label = f"{pinned_name or 'module'}.py"
            if len(source_in) <= INLINE_SOURCE_CHARS:
                body = f"Current module {label}:\n```python\n{source_in}\n```"
            else:
                body = (f"Current module {label} is {len(source_in):,} characters, so it is not pasted. Its outline:\n"
                        f"{patching.outline_text(source_in)}\nRead what you touch with read_function and change it with "
                        "patch_module; do not re-emit the module. Do the lookups in one or two rounds: read_function for every function you "
                        "will change or copy from (use the names above, or the route like 'GET /metrics/summary'), get_table_schema for "
                        "the tables you touch, then call patch_module.")
            messages += [{"role": "user", "content": body}, {"role": "assistant", "content": "OK."}]
        messages += [{"role": t.role, "content": t.content} for t in req.conversation[-KEEP_TURNS:]]
        messages.append({"role": "user", "content": req.prompt})

        agreed: dict[str, Any] | None = req.spec
        envs = facts["environments"]
        prior: dict[str, Any] = {"topics": set(), "answers": [], "entities": set()}
        if plan_ctx is not None:
            # the plan is the agreed spec: nothing is asked, even with clarify=true
            from . import project_plan as pp

            body = plan_rec["body"]
            agreed = {**(pp.module_spec(body, plan_ctx["key"]) or {}), "plan_version": plan_ctx["version"]}
            agreed.setdefault("nfr", body.get("nfr") or {})
            agreed.setdefault("shared_module", plan_ctx.get("shared"))
            yield {"type": "status", "phase": "planning", "attempt": 0,
                   "message": f"Building '{plan_ctx['key']}' from approved plan v{plan_ctx['version']}"}
        elif req.clarify and req.answers is None and req.spec is None:
            prior = await self._prior_knowledge(project.id)
            yield {"type": "status", "phase": "specifying", "attempt": 0, "message": "Writing the spec"}
            outgoing, changed = _scrub(messages + [{"role": "user", "content": SPEC_RULES}], variants)
            self.scrubbed = self.scrubbed or changed
            self.stats["model_calls"] += 1
            try:
                resp = await self._race(llm_provider.call_llm(
                    self.model, outgoing, temperature=0.2, tools=[EMIT_SPEC_TOOL], tool_choice="required",
                    max_tokens=MAX_OUTPUT_TOKENS), deadline)
            except (_Disconnected, _Timeout):
                raise
            except Exception as exc:  # noqa: BLE001
                from .vars_substitution import redact_deep
                logger.warning("module generation: spec call failed: %s", type(exc).__name__)
                yield {"type": "error", "code": "llm_failed",
                       "message": str(redact_deep(f"{type(exc).__name__}: {exc}", variants, expanded=True))[:500]}
                return
            call = next((tc for tc in resp.tool_calls if tc.name == "emit_spec"), None)
            raw = call.args if call is not None and isinstance(call.args, dict) else {}
            spec = {k: raw.get(k) or [] for k in ("entities", "workflows", "roles", "external_systems",
                                                   "acceptance_criteria", "limitations", "module_plan")}
            spec["summary"] = str(raw.get("summary") or "")[:400]
            spec["round"] = 1
            known = {t["name"] for t in facts["tables"]} | prior["entities"]
            questions = merge_questions(raw.get("questions"),
                                        gap_questions(spec, known_entities=known)
                                        + gaps.domain_questions(req.prompt, spec, envs, answered=prior["topics"]),
                                        context=req.prompt, answered=prior["topics"])
            if prior["answers"]:      # what the team already settled for other modules travels with this spec
                spec["answered"] = prior["answers"]
            spec["open_questions"] = questions
            if questions:
                yield {"type": "result", "status": "clarify", "needs_answers": True, "spec": spec,
                       "questions": questions, "round": 1, "module_name": pinned_name, "source": None,
                       "summary": spec["summary"], "attempts": 0, "model": self.model, "analysis": None, "self_test": None}
                return
            agreed = spec
        elif req.clarify and req.spec is not None and req.answers is not None:
            spec0 = req.spec
            rnd = int(spec0.get("round") or 1)
            more = gaps.followup_questions(spec0, req.answers, rnd, known_secrets=set(facts["secrets"]))
            if more:      # the answers opened new gaps: a second round, only the new questions
                answered = list(spec0.get("answered") or []) + [
                    {"id": q.get("id"), "topic": q.get("topic"), "question": q.get("question"),
                     "answer": req.answers.get(q.get("id"))}
                    for q in spec0.get("open_questions") or [] if isinstance(q, dict)]
                spec2 = {**{k: v for k, v in spec0.items() if k != "open_questions"}, "round": rnd + 1, "answered": answered,
                         "open_questions": more}
                yield {"type": "result", "status": "clarify", "needs_answers": True, "spec": spec2, "questions": more,
                       "round": rnd + 1, "module_name": pinned_name, "source": None, "summary": spec2.get("summary", ""),
                       "attempts": 0, "model": self.model, "analysis": None, "self_test": None}
                return
        if agreed is not None:
            messages.append({"role": "user", "content": spec_message(agreed, req.answers)})
            if plan_ctx is not None and (block := prompts.plan_block(agreed)):
                messages.append({"role": "user", "content": block})

        # ── build loop ───────────────────────────────────────────────────────
        source_cur = source_in
        done_source: str | None = None    # the last source that passed its checks: a reply with nothing more to add keeps it
        baseline = {(x["code"], x["message"]) for x in _extra_problems(source_in)} if source_in else set()
        declared_all: dict[str, dict[str, Any]] = {}
        decisions: list[str] = []
        next_modules: list[str] = []
        summary = ""
        attempt = trunc = cont = fix_rounds = patch_fail = iterations = 0
        notes: list[str] = []          # things to tell the model with its next message (e.g. self-test steps that were dropped)
        patched_fns: list[str] = []
        first = True
        ledger: dict[str, str] = (coverage.plan_routes(agreed) if plan_ctx is not None
                                  else coverage.spec_routes(agreed, req.prompt, req.conversation))
        # a stub the app builder declared is a route that exists but is not built: with a plan only this source counts
        existing_routes = (set() if plan_ctx is not None
                           else {coverage.norm(r["method"], r["path"]) for r in facts["routes"]})
        model_extra: set[str] = set()      # tables the model declared this turn that the plan does not have
        overridden: set[str] = set()
        if plan_ctx is not None:
            declared_all.update({t["name"]: t for t in plan_declared(plan_ctx)})
            built = set(facts["modules"]) | {plan_ctx["key"]}
            next_modules = [k for k in pp.build_order(plan_rec["body"]) if not str(k).startswith("app:") and k not in built]

        def absorb(raw_tables: Any) -> list[dict[str, Any]]:
            """Record the tables a reply declares. With a plan, the plan's definition of its own tables wins."""
            d, e = declared_tables(raw_tables)
            if plan_ctx is None:
                declared_all.update({t["name"]: t for t in d})
                return e
            for t in d:
                if t["name"] not in plan_ctx["tables"]:
                    model_extra.add(t["name"])
                elif t["name"] in declared_all and t["name"] not in overridden and \
                        {c["name"] for c in t["columns"]} != {c["name"] for c in declared_all[t["name"]]["columns"]}:
                    overridden.add(t["name"])
                    decisions.append(f"Table '{t['name']}': the approved plan's columns were used instead of the emitted ones")
            return e
        while True:
            iterations += 1
            if iterations > MAX_ITERATIONS:
                yield {"type": "error", "code": "generation_invalid", "message": "the generation did not converge",
                       **self._draft_fields()}
                return
            yield {"type": "status", "phase": "generating", "attempt": attempt + 1,
                   "message": "Generating the module" if first else ("Generating a fixed module" if attempt else "Continuing")}
            first = False
            final_tools = ([] if patch_only else [EMIT_MODULE_TOOL]) + ([patching.PATCH_MODULE_TOOL] if source_cur is not None else [])
            try:
                resp, call = await self._turn(messages, final_tools, variants, deadline, source_cur)
            except (_Disconnected, _Timeout):
                raise
            except Exception as exc:  # noqa: BLE001
                from .vars_substitution import redact_deep
                logger.warning("module generation: LLM call failed: %s", type(exc).__name__)
                yield {"type": "error", "code": "llm_failed",
                       "message": str(redact_deep(f"{type(exc).__name__}: {exc}", variants, expanded=True))[:500],
                       **self._draft_fields()}
                return

            if _truncated(resp):
                trunc += 1
                self.stats["truncations"] = trunc
                if trunc > MAX_TRUNCATIONS:
                    yield {"type": "error", "code": "output_truncated",
                           "message": f"the reply hit the output limit {trunc} times even as smaller steps",
                           **self._draft_fields()}
                    return
                yield {"type": "status", "phase": "repairing", "attempt": attempt,
                       "message": "The reply was cut off; asking for a smaller step"}
                messages.append({"role": "user", "content": TRUNC_PATCH if source_cur is not None else TRUNC_NEW})
                continue

            args = call.args if call is not None and isinstance(call.args, dict) else {}
            model_extra.clear()       # judged on what this reply declares, not on an earlier one
            source: str | None = None
            patch_errors: list[str] = []
            name = pinned_name or (self.last_draft or {}).get("module_name") or "generated"
            if call is not None and call.name == "patch_module":
                self.stats["patches"] += 1
                if source_cur is None:
                    patch_errors = ["there is no module to patch yet; call emit_module"]
                elif not args.get("ops") and isinstance(args.get("self_test"), list) and args["self_test"]:
                    source = source_cur            # a plan-only fix: the expectation was wrong, the module stays as it is
                else:
                    new_source, patch_errors = patching.apply_ops(source_cur, args.get("ops"))
                    if not patch_errors:
                        source = new_source
                        patched_fns = sorted(set(patched_fns) | set(patching.changed_functions(source_cur, new_source)))
                if args.get("tables"):
                    absorb(args.get("tables"))
            elif call is not None and patch_only:
                patch_errors = ["this module is already saved: change it with patch_module ops (replace_function / add_function); "
                                "a full rewrite with emit_module is refused"]
            elif call is not None:
                source = args.get("source") if isinstance(args.get("source"), str) and args["source"].strip() else None
            if source is None and not patch_errors:
                source = _fenced_python(getattr(resp, "text", None))
            if source is None and done_source is not None and (
                    call is None or (call.name == "patch_module" and not args.get("ops") and not args.get("self_test"))):
                # no tool call (or an empty patch) after a step that passed: the model says it is finished, not "no code"
                source, patch_errors = done_source, []
            if call is not None and call.name == "emit_module" and source is not None:
                emitted = args.get("module_name") if isinstance(args.get("module_name"), str) else None
                name = pinned_name or emitted or name
                derr = absorb(args.get("tables"))
            else:
                derr = []
            if isinstance(args.get("summary"), str) and args["summary"].strip():
                summary = args["summary"][:300]
            decisions += [str(x)[:300] for x in args.get("decisions") or [] if isinstance(x, str)]
            if plan_ctx is None:       # with a plan the ledger and the module list are the plan's, not the model's
                ledger.update(coverage.planned(args.get("planned_routes")))
            if plan_ctx is None and isinstance(args.get("next_modules"), list):
                next_modules = [str(x)[:80] for x in args["next_modules"]][:10]
            if isinstance(args.get("self_test"), list) and args["self_test"]:
                new_steps, plan_problems = selftest.parse_plan(args["self_test"])
                good = selftest.canonical(new_steps)
                # a patch (or a fix round) adds to the stored plan; a first emit defines it. Malformed steps are dropped, never
                # run, never stored; the model hears about them in the next message instead of burning a repair attempt.
                plan_raw = selftest.merge_plan(plan_raw, good) if source_cur is not None else good
                notes += [f"self_test: {m}" for m in plan_problems]

            if source is None:
                attempt_problems = ([{"code": "patch", "line": None, "message": m + (f". {PATCH_HELP}" if "non-empty" in m else "")}
                                     for m in patch_errors]
                                    or [{"code": "no_code", "line": None, "message": "no module source in the reply (emit_module needs the full "
                                                                      "`source`; if you meant to edit, call patch_module)"}])
                if patch_errors:
                    patch_fail += 1
                self.last_problems = attempt_problems
                analysis = {"routes": {"created": [], "updated": [], "removed": []},
                            "errors": [{"code": p["code"], "line": None, "column": None, "message": p["message"]}
                                       for p in attempt_problems],
                            "conflicts": [], "tables": [], "secrets": [],
                            "warnings": [dict(SCRUB_WARNING)] if self.scrubbed else [], "_problems": attempt_problems}
                self.last_analysis = analysis
                yield {"type": "status", "phase": "analysing", "attempt": attempt + 1, "message": "Checking the module"}
                yield {"type": "analysis", "attempt": attempt + 1, **self._public(analysis)}
            else:
                self.last_draft = {"module_name": name, "source": source, "summary": summary, "tables": list(declared_all.values())}
                yield {"type": "draft", "attempt": attempt + 1, "module_name": name, "source": source, "summary": summary,
                       "tables": list(declared_all.values()),
                       **({"patch": {"functions": patched_fns}} if call is not None and call.name == "patch_module" else {})}
                yield {"type": "status", "phase": "analysing", "attempt": attempt + 1, "message": "Checking the module"}

                async def do_analyse(s: Any, source: str = source, name: str = name, derr: list = derr) -> dict[str, Any]:
                    p = await s.get(ProjectModel, self.project_id)
                    return await analyse(s, p, source=source, module_id=req.module_id, module_name=name,
                                         name_from_model=pinned_name is None, declared=list(declared_all.values()),
                                         declared_errors=derr, scrubbed=self.scrubbed, facts=facts,
                                         plan=({**plan_ctx, "model_tables": set(model_extra)} if plan_ctx else None))
                analysis = await self._race(self._db(do_analyse), deadline)
                extra = _extra_problems(source)
                # an edit is judged on what it adds: problems the module already had are reported as warnings, not repaired
                # (otherwise "add a column to stats" turns into a rewrite of the module's login handling)
                inherited = [x for x in extra if (x["code"], x["message"]) in baseline]
                extra = [x for x in extra if (x["code"], x["message"]) not in baseline]
                analysis["warnings"] = list(analysis.get("warnings") or []) + [
                    {"code": "existing_" + x["code"], "line": x.get("line"), "message": "already in the module: " + x["message"]}
                    for x in inherited]
                for x in extra:
                    analysis["errors"].append({"code": x["code"], "line": x.get("line"), "column": None, "message": x["message"]})
                analysis["_problems"] = analysis["_problems"] + extra
                self.last_analysis = analysis
                self.last_problems = analysis["_problems"]
                yield {"type": "analysis", "attempt": attempt + 1, **self._public(analysis)}

            if not self.last_problems and source is not None:
                remaining = [str(x)[:120] for x in args.get("remaining") or [] if isinstance(x, str) and x.strip()]
                owed = coverage.missing(ledger, analysis.get("_decls") or [], existing_routes)
                if (remaining or owed) and cont < MAX_CONTINUATIONS and deadline - asyncio.get_running_loop().time() > 45:
                    cont += 1
                    self.stats["continuations"] = cont
                    source_cur = done_source = source
                    self._answer_calls(messages, resp, call, "applied")
                    areas = "; ".join(remaining or owed)
                    messages.append({"role": "user", "content": CONTINUE.format(
                        remaining=areas, outline=patching.outline_text(source)) + (OWED.format(owed=", ".join(owed[:30])) if owed else "")
                        + _drain(notes)})
                    yield {"type": "status", "phase": "generating", "attempt": attempt + 1,
                           "message": f"Part {cont} written; continuing with {(remaining or owed)[0]}"}
                    continue
                not_built = owed + [r for r in remaining if r not in owed]
                self_test = None
                results: list[dict[str, Any]] = []
                schemas: dict[str, Any] = {}
                if req.self_test:
                    only = None
                    if patch_only and source_in is not None:      # the scaffold's own check covered the rest
                        got = patching.touched_routes(source_in, source, analysis.get("_decls") or [])
                        only = frozenset(got) if got is not None else None
                    async for ev in self._self_test(project, analysis, source, name, deadline, plan_raw,
                                                    **({"only": only} if only is not None else {})):
                        if ev["type"] == "self_test":
                            results = ev["results"]
                            schemas = ev.get("schemas") or {}
                            self_test = results   # `result.self_test` is the results array (or null), UI-DESIGN §10.1
                        yield ev
                bad = selftest.failures(results)
                if bad and fix_rounds < self.max_fix_rounds and deadline - asyncio.get_running_loop().time() > 45:
                    fix_rounds += 1
                    self.stats["fix_rounds"] = fix_rounds
                    source_cur = done_source = source
                    self._answer_calls(messages, resp, call, "applied; self-test failed")
                    messages.append({"role": "user", "content": "\n".join([SELF_FIX] + selftest.failure_lines(results)) + _drain(notes)})
                    yield {"type": "status", "phase": "repairing", "attempt": attempt + 1,
                           "message": f"Self-test found {len(bad)} problem(s); fixing (round {fix_rounds}/{self.max_fix_rounds})"}
                    continue
                warnings = list(analysis.get("warnings") or [])
                if not_built:
                    warnings.append({"code": "partial_build", "line": None,
                                     "message": f"{len(not_built)} item(s) were asked for or planned but are NOT in this module: "
                                                + "; ".join(not_built[:20])})
                if bad:
                    warnings.append({"code": "self_test_failed", "line": None,
                                     "message": f"{len(bad)} self-test check(s) still fail after {fix_rounds} fix round(s)"})
                public = {**self._public(analysis), "warnings": warnings}
                kw: dict[str, Any] = {}
                if agreed is not None:
                    kw = {"status": "built", "spec": agreed}
                if not_built:       # never plain success: say what is missing
                    kw = {**kw, "status": "partial", "missing": not_built[:50]}
                plan_version = plan_ctx["version"] if plan_ctx else None
                await self._remember(project, name, source, None if plan_ctx else agreed, req.answers, decisions, plan_raw,
                                     [] if plan_ctx else list(declared_all.values()),
                                     **({"plan_version": plan_version} if plan_version is not None else {}))
                if not_built:
                    summary = (summary + " " if summary else "") + "PARTIAL: not built yet: " + "; ".join(not_built[:8])
                yield {"type": "result", "module_name": name, "source": source, "summary": summary,
                       "attempts": attempt + 1, "model": self.model, "analysis": public, "self_test": self_test,
                       "self_test_passed": not bad if results else None, "self_test_plan": plan_raw, "response_schemas": schemas,
                       "mode": "patch" if source_in is not None else "new",
                       **({"module_id": req.module_id} if req.module_id else {}),   # save with PUT to this module
                       "tables": list(declared_all.values()),          # pass as `tables` when saving the module (creates them)
                       **({"patch": {"functions": patched_fns}} if patched_fns else {}),
                       **({"next_modules": next_modules} if next_modules else {}),
                       **({"plan_version": plan_version, "plan_module": plan_ctx["key"]} if plan_ctx else {}),
                       **({"schedule_hint": hint} if (hint := schedule_hint(public, getattr(project, "slug", None))) else {}),
                       "stats": {**self.stats, "prompt_chars": self.prompt_chars, "source_chars": len(source)}, **kw}
                return

            attempt += 1
            if attempt >= self.max_attempts:
                n = len(self.last_problems)
                yield {"type": "error", "code": "generation_invalid",
                       "message": f"the module still has {n} problem{'s' if n != 1 else ''} after {self.max_attempts} attempts",
                       **self._draft_fields()}
                return
            yield {"type": "status", "phase": "repairing", "attempt": attempt,
                   "message": f"Fixing {len(self.last_problems)} problem(s)"}
            self._answer_calls(messages, resp, call, "not applied" if source is None else "applied; has problems")
            if source is not None and _parses(source):
                source_cur = source
            done_source = None                # what is in hand now has problems: a reply without code fixes nothing
            hint = ""
            if patch_fail >= 2 and source_cur is not None and len(source_cur) < 40_000 and not patch_only:
                hint = "\nThe patch failed twice: send the whole module with emit_module instead."
            messages.append({"role": "user", "content": "\n".join(
                [REPAIR_HEADER] + [problem_line(p) for p in self.last_problems]) + hint + _drain(notes)})

    # ── knowledge ────────────────────────────────────────────────────────────

    async def _approved_plan(self) -> dict[str, Any] | None:
        """``{version, body}`` of the project's approved plan, or None (no plan, or no plan store on this engine)."""
        try:
            from . import project_plan as pp

            async def load_plan(s: Any) -> Any:
                return await pp.load_approved(s, self.project_id)

            got = await self._db(load_plan)
        except Exception as e:  # noqa: BLE001 - without a readable plan the old flow runs
            logger.warning("module generation: no approved plan (%s)", type(e).__name__)
            return None
        return got if isinstance(got, dict) and isinstance(got.get("body"), dict) else None

    async def _prior_knowledge(self, project_id: str) -> dict[str, Any]:
        """What the project's earlier clarify dialogues settled: answered topics, their answers, and the tables other modules
        designed. Read in full (not through the budgeted retrieval, which can drop an answer)."""
        out: dict[str, Any] = {"topics": set(), "answers": [], "entities": set()}
        try:
            from . import project_knowledge as pk

            async def load_prior(s: Any) -> Any:
                return {"answers": await pk.list_kind(s, project_id, "clarify_answer"),
                        "tables": await pk.list_kind(s, project_id, "table_schema")}

            got = await self._db(load_prior)
        except Exception as e:  # noqa: BLE001
            logger.warning("module generation: no earlier answers (%s)", type(e).__name__)
            return out
        if not isinstance(got, dict):
            return out
        for row in got.get("answers") or []:
            body = row.get("body") or {}
            topic = gaps.topic_of(str(row.get("key")), body.get("topic"))
            if topic and str(body.get("answer") or "").strip():
                out["topics"].add(topic)
                if len(out["answers"]) < 20:
                    out["answers"].append({"id": row["key"], "topic": topic, "question": body.get("question"),
                                           "answer": body["answer"], "inherited": True})
        out["entities"] = {str(r.get("key")) for r in got.get("tables") or [] if r.get("key")}
        return out

    async def _saved_by_plan_name(self, s: Any, project: ProjectModel) -> CodeModuleModel | None:
        """The saved module a request without ``module_id`` names as a plan module (``plan_module``, the module name or a
        ``[plan:<key>]`` token), so a rebuild from the plan edits it. Confirmed against the approved plan in ``_run``."""
        req = self.req
        if not req.use_plan or req.current_source is not None:
            return None
        tok = _PLAN_TOKEN.search(req.prompt or "")
        names = [n for n in (req.plan_module, req.module_name, tok.group(1) if tok else None) if n]
        if not names:
            return None
        rows = (await s.execute(select(CodeModuleModel).where(CodeModuleModel.project_id == project.id,
                                                              CodeModuleModel.name.in_(names)))).scalars().all()
        by_name = {r.name: r for r in rows}
        return next((by_name[n] for n in names if n in by_name), None)

    async def _load_memory(self, s: Any, project: ProjectModel, saved: CodeModuleModel | None) -> None:
        """Retrieve what the project already recorded for this request, and the stored self-test of the module being edited.
        A missing knowledge table (migration not applied) just means no memory."""
        try:
            from . import project_knowledge as pk

            query = " ".join([self.req.prompt] + [str(a) for a in (self.req.answers or {}).values()])[:600]
            got = await pk.retrieve(s, project.id, query, kinds=["decision", "clarify_answer", "requirement", "spec", "failure",
                                                                 "route", "table_schema"], token_budget=MEMORY_TOKENS)
            self.memory = got["text"] if got.get("items") else ""
            name = self.req.module_name or (saved.name if saved else None)
            current = self.req.current_source if self.req.current_source is not None else (saved.source if saved else None)
            if name and current is not None:
                row = await pk.get(s, project.id, "acceptance", f"selftest:{name}")
                body = (row or {}).get("body", {})
                # the stored steps describe the module as the last build left it; if the module is not that source any more
                # (a draft that was never saved, a hand edit) they would test behaviour that is not there
                if isinstance(body.get("steps"), list) and body.get("source_sha") == hashlib.sha256(current.encode()).hexdigest():
                    self.stored_plan = body["steps"]
        except Exception as e:  # noqa: BLE001
            logger.warning("module generation: no project memory (%s)", type(e).__name__)
            with contextlib.suppress(Exception):
                await s.rollback()

    async def _remember(self, project: ProjectModel, name: str, source: str, spec: dict[str, Any] | None,
                        answers: dict[str, str] | None, decisions: list[str], plan: list[dict[str, Any]],
                        declared: list[dict[str, Any]], plan_version: Any = None) -> None:
        """After a successful build: write spec, answers, decisions, self-test, routes and table schemas to the knowledge store."""
        async def write(s: Any) -> None:
            from . import project_knowledge as pk

            pid = project.id
            if spec:
                body = {k: v for k, v in spec.items() if k != "open_questions"}
                await pk.upsert(s, pid, "spec", name, title=f"spec {name}", body=body, status="approved", source="design",
                                source_ref=f"module:{name}", commit=False)
                qa = [a for a in spec.get("answered") or [] if isinstance(a, dict) and not a.get("inherited")] + [
                    {"id": q.get("id"), "topic": q.get("topic"), "question": q.get("question"),
                     "answer": (answers or {}).get(q.get("id"))}
                    for q in spec.get("open_questions") or [] if isinstance(q, dict)]
                for q in qa:
                    if q.get("id") and q.get("answer"):
                        await pk.upsert(s, pid, "clarify_answer", f"{name}:{q['id']}", title=str(q.get("question") or "")[:200],
                                        body={"question": q.get("question"), "answer": q["answer"], "topic": q.get("topic")},
                                        status="approved",
                                        source="human", source_ref=f"module:{name}", commit=False)
            all_decisions = list(decisions) + [f"Limitation: {lim}" for lim in (spec or {}).get("limitations") or []]
            for d in all_decisions:
                await pk.upsert(s, pid, "decision", f"{name}:{hashlib.sha1(d.encode()).hexdigest()[:8]}", title=d[:200],
                                body={"decision": d, "module": name}, status="approved", source="design",
                                source_ref=f"module:{name}", commit=False)
            if plan or plan_version is not None:
                await pk.upsert(s, pid, "acceptance", f"selftest:{name}", title=f"self-test steps for {name}",
                                body={"steps": plan, "source_sha": hashlib.sha256(source.encode()).hexdigest(),
                                      **({"plan_version": plan_version} if plan_version is not None else {})},
                                text=f"self-test {name} " + " ".join(str(x.get("call")) for x in plan),
                                status="approved", source="design", source_ref=f"module:{name}", commit=False)
            for t in declared:      # tables the UI has not created yet: record the design, index_tables replaces it once they exist
                cols = ", ".join(f"{c['name']} {c['type']}{' required' if c.get('required') else ''}" for c in t["columns"])
                await pk.upsert(s, pid, "table_schema", t["name"], title=f"table {t['name']}",
                                body={"name": t["name"], "description": None, "columns": t["columns"]},
                                text=f"table {t['name']} columns: {cols}", status="draft", source="design",
                                source_ref="tables", commit=False)
            await pk.index_module(s, pid, source, name, commit=False)
            await pk.index_tables(s, pid, commit=False)
            await s.commit()

        try:
            await self._db(write)
        except Exception as e:  # noqa: BLE001 - memory is a convenience; the build result stands
            logger.warning("module generation: could not record project knowledge (%s: %s)", type(e).__name__, str(e)[:200])

    # ── self-test ────────────────────────────────────────────────────────────

    async def _self_test(self, project: ProjectModel, analysis: dict[str, Any], source: str, name: str, deadline: float,
                         plan_raw: list[dict[str, Any]] | None = None,
                         only: frozenset[str] | None = None) -> AsyncIterator[dict[str, Any]]:
        """Scenario steps + probes against scratch copies of the module's tables (see ``backend_templates.selftest``)."""
        from ..sandbox.backend import availability
        from .data_table_service import DataTableService
        from .draft_runner import run_draft

        ok, _reason = availability()
        if not ok:
            return
        decls = analysis["_decls"]
        names, dynamic = selftest.literal_tables(source)
        cols_of = {t["name"]: (t.get("columns") or []) for t in analysis["tables"] if t.get("columns") is not None}
        used = [n for n in names if n in cols_of]
        required = {n: [c["name"] for c in cols_of[n] if c.get("required")] for n in used}
        flags = selftest.route_flags(source, decls)
        steps, _plan_problems = selftest.parse_plan(plan_raw)
        steps += selftest.baseline_steps(decls, flags, required)
        only = only if only is not None else SELF_TEST_ONLY.get()
        if only is not None:               # a save that touched some routes re-probes those, not the whole module
            steps = [x for x in steps if x.get("kind") != "probe" or x.get("label") in only]
        token = selftest.new_token()
        mapping = {n: selftest.scratch_name(n, token) for n in used}
        scratch_src = selftest.rewrite_for_scratch(source, mapping) if used or "studio.jobs" in source else source
        loop = asyncio.get_running_loop()
        yield {"type": "status", "phase": "testing", "attempt": 0, "message": f"Self-test: {len(steps)} check(s) on scratch tables"}

        created: list[str] = []
        try:
            async def make(s: Any) -> None:
                svc = DataTableService(s)
                p = await s.get(ProjectModel, project.id)
                await self._sweep_scratch(s, svc, p)
                for real, scratch in mapping.items():
                    await svc.create_table(p, {"name": scratch, "columns": cols_of[real], "description": "builder self-test (temporary)"})
                    created.append(scratch)
            await self._race(self._db(make), deadline)

            async def counts() -> dict[str, int]:
                async def count(s: Any) -> dict[str, int]:
                    svc = DataTableService(s)
                    out = {}
                    for real, scratch in mapping.items():
                        t = await svc.table_by_name(project.id, scratch)
                        out[real] = await svc.row_count(t.id) if t is not None else 0
                    return out
                return await self._db(count)

            async def call(method: str, path: str, query: dict[str, Any], body: Any, read_only: bool,
                           headers: dict[str, str] | None = None) -> dict[str, Any]:
                async def one(s: Any) -> dict[str, Any]:
                    p = await s.get(ProjectModel, project.id)
                    try:
                        r = await run_draft(s, p, source=scratch_src, module_name=name, module_id=self.req.module_id,
                                            method=method, path=path, query=query or None, body=body, headers=headers,
                                            virtual_tables=None,
                                            trigger_type="generate_self_test", origin="generate_self_test", read_only=read_only,
                                            max_calls=SELF_TEST_MAX_CALLS * 3, timeout_s=SELF_TEST_ROUTE_TIMEOUT_S, decls=decls)
                    except ProjectError as e:
                        return {"status": e.status, "body": {"error": e.message}, "duration_ms": 0.0, "error_code": e.code,
                                "execution_id": None}
                    err = r.get("error")
                    return {"status": r["status"], "body": r.get("body"), "duration_ms": r["duration_ms"],
                            "error_code": (err or {}).get("code") if err else None, "execution_id": r["execution_id"]}
                return await self._race(self._db(one), deadline)

            from .schema_inference import infer_json_schema

            schemas: dict[str, Any] = {}
            results = await selftest.run_steps(steps, call, counts, flags, decls, writes_allowed=not dynamic,
                                               time_left=lambda: deadline - loop.time(), schemas=schemas, infer=infer_json_schema)
            for r in results:
                if r.get("ok"):
                    r.pop("request", None)
            yield {"type": "self_test", "results": results, "schemas": schemas}
        finally:
            if created:
                try:
                    await asyncio.shield(self._db(lambda s: self._drop_scratch(s, project.id, created)))
                except Exception as e:  # noqa: BLE001 - swept at the next self-test
                    logger.warning("module generation: scratch cleanup deferred (%s)", type(e).__name__)

    @staticmethod
    async def _drop_scratch(s: Any, project_id: str, names: list[str]) -> None:
        from .data_table_service import DataTableService

        svc = DataTableService(s)
        p = await s.get(ProjectModel, project_id)
        for n in names:
            t = await svc.table_by_name(project_id, n)
            if t is not None:
                await svc.delete_table(p, t.id)

    @staticmethod
    async def _sweep_scratch(s: Any, svc: Any, project: ProjectModel) -> None:
        """Delete scratch tables an earlier, crashed self-test left behind (older than 20 minutes)."""
        from datetime import datetime, timedelta

        from ..db.models import DataTableModel

        rows = (await s.execute(select(DataTableModel).where(DataTableModel.project_id == project.id))).scalars().all()
        for t in rows:
            if selftest.SCRATCH_RE.search(t.name) and t.created_at and t.created_at < datetime.now() - timedelta(minutes=20):
                await svc.delete_table(project, t.id)
