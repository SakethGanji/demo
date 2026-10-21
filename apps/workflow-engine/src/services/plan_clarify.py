"""One requirements conversation for the whole project: prompt -> draft ProjectPlan + questions -> answers -> reviewed plan.

Round 1 asks the model for the plan in two calls (``emit_plan_core``: requirements, data, modules, integrations,
environments, secrets; then ``emit_plan_surface``: routes, apps and screens, NFR, given the core) so neither reply hits the
output cap. Questions come from one merged list: the model's, the structural checks of ``module_generator.gap_questions``
on a spec projection of the plan (every plan table counts as known, so a ref to a sibling module's table is not asked
about), ``gaps.domain_questions`` and :func:`plan_gap_questions` (validation issues only the team can settle). One question
per topic; at most :data:`MAX_PLAN_QUESTIONS` over both rounds.

Round 2 records the answers in ``requirements.answers``, applies the ones that settle a fact deterministically (hosts,
secret names, URLs, the live environment, defaults, retention numbers), runs one model revision and validates again.
Follow-ups come only from issues that still need input and whose topic was not asked yet. After the last round the
remaining gaps take their question defaults, written to ``assumptions``. The row stays ``draft``; its phase becomes
``review``. Every outgoing message is scrubbed of the project's secret values.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any, AsyncIterator, Awaitable, Callable

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..core.config import settings
from . import plan_checklist as pcl
from . import plan_compact
from . import project_plan as pp
from .backend_templates import gaps
from .module_generator import (EMIT_SPEC_TOOL, MAX_OUTPUT_TOKENS, SPEC_TOPICS, _FIELD_SCHEMA, _scrub, gap_questions,
                               merge_questions)
from .project_service import ProjectError

logger = logging.getLogger(__name__)

MAX_PLAN_QUESTIONS = 20
ROUND1_MAX = 16          # leaves room for the follow-ups the answers open
MAX_ROUNDS = 2
MAX_PROMPT_CHARS = 12000
MAX_ANSWER_CHARS = 2000
GAP_TOPICS = ("secret_names", "secret_hosts", "env_urls", "live_env", "column_defaults", "retention_numbers")
PLAN_TOPICS = tuple(SPEC_TOPICS) + GAP_TOPICS
_UPPER_SNAKE = re.compile(r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b")

_running: set[str] = set()


def try_acquire(project_id: str) -> str | None:
    """One plan conversation per project at a time (two would race for the next version number)."""
    if project_id in _running:
        return None
    _running.add(project_id)
    return project_id


def release(key: str | None) -> None:
    if key is not None:
        _running.discard(key)


# ── tools ────────────────────────────────────────────────────────────────────

_COLUMN = {**_FIELD_SCHEMA, "properties": {**_FIELD_SCHEMA["properties"], "index": {"type": "boolean"},
                                           "default": {}, "description": {"type": "string"}}}
_QUESTIONS = EMIT_SPEC_TOOL["parameters"]["properties"]["questions"]
_STR_LIST = {"type": "array", "items": {"type": "string"}}
_ROUTE_FIELDS = {"type": "object", "properties": {"fields": {"type": "array", "items": {
    "type": "object", "required": ["name"], "properties": {"name": {"type": "string"}, "type": {"type": "string"},
                                                           "required": {"type": "boolean"}}}}}}

CORE_PROPS: dict[str, Any] = {
    "title": {"type": "string"}, "summary": {"type": "string", "maxLength": 600},
    "requirements": {"type": "object", "properties": {
        "assumptions": _STR_LIST, "limitations": _STR_LIST,
        "acceptance": {"type": "array", "items": {"type": "object", "required": ["id", "text"], "properties": {
            "id": {"type": "string"}, "text": {"type": "string"},
            "covers": {"type": "array", "items": {"type": "string"},
                       "description": "'route:METHOD /path', 'screen:<name>', 'module:<key>' or 'table:<name>'"}}}}}},
    "roles_note": {"type": "string", "description": "who does what (login/identity is out of scope)"},
    "environments": {"type": "array", "items": {"type": "object", "required": ["name"], "properties": {
        "name": {"type": "string"}, "base_url": {"type": "string"}, "live": {"type": "boolean"}, "refused_actions": _STR_LIST,
        "placeholder": {"type": "boolean", "description": "true when the environment is only named (to show its guards) and "
                        "is never called: it needs no secrets"}}}},
    "secrets": {"type": "array", "items": {"type": "object", "required": ["name"], "properties": {
        "name": {"type": "string", "description": "UPPER_SNAKE"}, "purpose": {"type": "string"}, "environments": _STR_LIST,
        "allowed_hosts": {"type": "array", "items": {"type": "string"}, "description": "hostnames only: no scheme, no port"}}}},
    "integrations": {"type": "array", "items": {"type": "object", "required": ["key", "kind"], "properties": {
        "key": {"type": "string"}, "kind": {"enum": ["jobs", "http", "email", "other"]}, "purpose": {"type": "string"},
        "job_types": _STR_LIST, "contract": {"type": "object"}, "secrets": _STR_LIST, "hosts": _STR_LIST,
        "smoke": {"type": "object", "description": "how the smoke run proves the integration works: it calls THIS project's "
                  "own routes (on scratch tables), never the Jobs service by job type", "properties": {
                      "start": {"type": "string", "description": "METHOD /path of a planned route that starts one job, "
                                "e.g. 'POST /runs'"},
                      "poll": {"type": "string", "description": "METHOD /path of the planned route that reads it back, "
                               "e.g. 'GET /runs/{id}' ({id} comes from the start response's id / run_id / job_id)"},
                      "until": {"type": "string", "description": "when polling stops: 'status in [passed,failed]' or "
                                "'status == done'"},
                      "environment": {"type": "string"}, "body": {"type": "object", "description": "the start request body"},
                      "expect": {"type": "object"}, "fixtures": {"type": "object"}, "timeout_s": {"type": "integer"}}}}}},
    "data": {"type": "object", "properties": {
        "conventions": {"type": "object"},
        "tables": {"type": "array", "items": {"type": "object", "required": ["name", "owner", "columns"], "properties": {
            "name": {"type": "string"}, "owner": {"type": "string", "description": "the one module that owns it"},
            "writers": _STR_LIST, "description": {"type": "string"}, "columns": {"type": "array", "items": _COLUMN},
            "indexes": {"type": "array", "items": _STR_LIST}, "business_key": _STR_LIST, "audit": {"type": "boolean"},
            "soft_delete": {"type": "boolean"},
            "retention": {"type": "object", "properties": {"policy": {"enum": list(pp.RETENTION_POLICIES)},
                                                           "value": {"type": ["integer", "null"]}}},
            "state_machine": {"type": "object", "properties": {
                "field": {"type": "string"}, "initial": {"type": "string"}, "final": _STR_LIST,
                "transitions": {"type": "array", "items": {"type": "object", "required": ["from", "to"], "properties": {
                    "from": {"type": "string"}, "to": {"type": "string"}, "route": {"type": "string"},
                    "who": {"type": "string"}, "effect": {"type": "string"}}}}}},
            "growth_note": {"type": "string"}}}}}},
    "modules": {"type": "array", "items": {"type": "object", "required": ["key", "kind"], "properties": {
        "key": {"type": "string"}, "kind": {"enum": ["shared", "routes"]}, "purpose": {"type": "string"},
        "depends_on": _STR_LIST, "owns": _STR_LIST, "reads": _STR_LIST, "integrations": _STR_LIST,
        "helpers": {"type": "array", "items": {"type": "object", "required": ["name", "signature"], "properties": {
            "name": {"type": "string"}, "signature": {"type": "string"}, "purpose": {"type": "string"}}}}}}},
    "build_order": {**_STR_LIST, "description": "module keys in build order (dependencies first), then 'app:<key>'"},
}
SURFACE_PROPS: dict[str, Any] = {
    "routes": {"type": "array", "items": {"type": "object", "required": ["key", "module"], "properties": {
        "key": {"type": "string", "description": "'METHOD /path'"}, "module": {"type": "string"}, "purpose": {"type": "string"},
        "auth": {"type": "string"}, "request": _ROUTE_FIELDS, "response": _ROUTE_FIELDS,
        "errors": {"type": "array", "items": {"type": "object", "properties": {"status": {"type": "integer"},
                                                                             "code": {"type": "string"}}}},
        "idempotency": {"type": "string", "description": "none | natural_key:<cols> | repeat_is_noop"},
        "pagination": {"type": "boolean"}, "integrations": _STR_LIST,
        "tables": {"type": "object", "properties": {"reads": _STR_LIST, "writes": _STR_LIST}}}}},
    "apps": {"type": "array", "items": {"type": "object", "required": ["key", "name", "screens"], "properties": {
        "key": {"type": "string"}, "name": {"type": "string"}, "users": {"type": "string"},
        "screens": {"type": "array", "items": {"type": "object", "required": ["name", "routes"], "properties": {
            "name": {"type": "string"}, "purpose": {"type": "string"}, "routes": _STR_LIST,
            "states": {"type": "object", "description": "empty, loading, error, live"}, "journeys": _STR_LIST}}}}}},
    "nfr": {"type": "object"},
}


_CHECKLIST = {"type": "array", "description": "what a real deployment of THIS app still lacks that the standard checks "
              "(roles, sample data, credentials, notifications, approvals, limits, retention, reports) would not find",
              "items": {"type": "object", "required": ["topic", "question", "options"], "properties": {
                  "topic": {"type": "string"}, "question": {"type": "string"}, "why": {"type": "string"}, "multi": {"type": "boolean"},
                  "options": _STR_LIST, "suggested": {**_STR_LIST, "description": "the options to preselect"}}}}
_S = {"type": "string"}
CORE_COMPACT_PROPS: dict[str, Any] = {
    "title": _S, "summary": {"type": "string", "maxLength": 400}, "roles_note": _S,
    "assumptions": _STR_LIST, "limitations": _STR_LIST,
    "acceptance": {**_STR_LIST, "description": "one per line: 'text :: route:GET /x, screen:Name, table:t, module:m'"},
    "environments": CORE_PROPS["environments"], "secrets": CORE_PROPS["secrets"], "integrations": CORE_PROPS["integrations"],
    "tables": {"type": "array", "description": "ONE entry per table", "items": {
        "type": "object", "required": ["name", "owner", "cols"], "properties": {
            "name": _S, "owner": {"type": "string", "description": "the one module that owns it"},
            "cols": {**_STR_LIST, "description": "one string per column: 'name:type [req] [uniq] [idx] [=default] [enum:a|b|c] "
                     "[ref:table.id]'. Types: string number integer boolean datetime json. Never list id/created_at/updated_at"},
            "sm": {"type": "string", "description": "state machine: 'status initial=new final=done,rejected; new>review POST "
                   "/things/{id}/submit @member; review>done POST /things/{id}/approve @admin'. States become the enum"},
            "keep": {"type": "string", "description": "retention: 'days:90' or 'last_n:500' (default: keep)"},
            "writers": _STR_LIST}}},
    "modules": {"type": "array", "items": {"type": "object", "required": ["key", "kind"], "properties": {
        "key": _S, "kind": {"enum": ["shared", "routes"]}, "purpose": {"type": "string", "maxLength": 160},
        "helpers": {**_STR_LIST, "description": "shared module only: 'name(signature) purpose'"},
        "integrations": _STR_LIST}}},
    "checklist": _CHECKLIST,
}
SURFACE_COMPACT_PROPS: dict[str, Any] = {
    "route_groups": {"type": "array", "description": "routes grouped by module", "items": {
        "type": "object", "required": ["module", "routes"], "properties": {"module": _S, "routes": {
            **_STR_LIST, "description": "one string per route: 'METHOD /path | purpose | in: a!,b:integer | out: id,name | "
            "err: 404 not_found | w: table | r: table | idem: repeat_is_noop | int: integration'. Everything after the path is "
            "optional: table, request/response fields, pagination, 404/422 errors and idempotency are derived from the path "
            "and the table"}}}},
    "apps": {"type": "array", "items": {"type": "object", "required": ["key", "name", "screens"], "properties": {
        "key": _S, "name": _S, "users": _S,
        "screens": {**_STR_LIST, "description": "one string per screen: 'Name | purpose | GET /a, POST /b'"}}}},
    "nfr": {"type": "object"},
}
CORE_KEYS = tuple(CORE_PROPS) + tuple(k for k in CORE_COMPACT_PROPS if k not in CORE_PROPS)
SURFACE_KEYS = tuple(SURFACE_PROPS) + tuple(k for k in SURFACE_COMPACT_PROPS if k not in SURFACE_PROPS)


def _tool(name: str, description: str, props: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"name": name, "description": description, "parameters": {"type": "object", "required": required, "properties": props}}


EMIT_PLAN_CORE_TOOL = _tool(
    "emit_plan_core", "Emit the core of the project plan (requirements, data model, modules, integrations, environments, "
    "secrets, build order) and the questions whose answers it needs.",
    {**CORE_PROPS, "questions": _QUESTIONS}, ["title", "summary", "data", "modules", "build_order", "questions"])
EMIT_PLAN_SURFACE_TOOL = _tool(
    "emit_plan_surface", "Emit the surface of the project plan for the given core: every route contract, the apps and their "
    "screens in compact form (one string per route and screen), and the non-functional rules.",
    {**SURFACE_COMPACT_PROPS, "questions": _QUESTIONS}, ["route_groups", "apps"])
EMIT_PLAN_REVISION_TOOL = _tool(
    "emit_plan_revision", "Emit ONLY the plan sections that change (each section you emit replaces that section whole).",
    {**CORE_PROPS, **SURFACE_PROPS, **{k: v for k, v in CORE_COMPACT_PROPS.items() if k != "checklist"},
     **SURFACE_COMPACT_PROPS, "notes": _STR_LIST}, [])

PLAN_RULES = """You are planning a whole backend + app project with a business team, BEFORE anything is built. Every builder
will build exactly this plan afterwards and will not ask again, so the plan must be complete and concrete.

Data model: snake_case tables; every table has exactly ONE owner module (others that write it are listed in `writers`);
columns with type, required, unique, enum (for every status/kind field), ref ('table.id') for relations, index for fields
that are filtered on, defaults for required fields. id / created_at / updated_at are managed: never list them. Prefer
audit=true and soft_delete=true for business records; give each table a retention policy. Status fields get a
state_machine (initial, final states, transitions with the route that performs them and who may).
Modules: one per area, at most 12 routes each, plus ONE shared module (kind "shared") holding helpers every module imports
(audit, pagination, error responses, job case building) - list their signatures; no other module redefines them.
build_order: the shared module first, then dependencies before dependants (a module that reads a table comes after its
owner), then 'app:<key>' for each app.
Routes: list routes paginate; state-changing routes declare errors and idempotency; request/response field names are the
contract the app is built against.
Integrations: the Jobs service (kind "jobs") with its job types, request contract and a smoke run (start = 'METHOD /path' of
one of this plan's routes, poll = the route that reads the run back, until = 'status in [passed,failed]'); credentials
are project secrets referenced by UPPER_SNAKE name, with allowed_hosts as hostnames (no scheme, no port); environments with
base_url, and which one is live.
Ask instead of guessing about what would change the schema, routes, state machines or integrations; never about style.
Write the plan COMPACT: one terse string per column, route and screen (see the tool descriptions), no prose repeated across
sections, no purpose longer than a short phrase. Do not write what code derives: build_order, owns / reads / depends_on,
enum values of a state field, request / response fields of plain CRUD routes, pagination, 404 / 422 errors, idempotency.
Use `checklist` only for what this specific app needs beyond roles, sample data, credentials, notifications, approvals,
limits, retention and reports (the standard checks add those), each with options and the options you suggest.
""" + gaps.SECURITY_RULES


# ── questions ────────────────────────────────────────────────────────────────


def _q(qid: str, topic: str, question: str, why: str, default: str | None, options: list[str] | None = None) -> dict[str, Any]:
    return {"id": qid, "topic": topic, "question": question, "why": why, "options": options or [],
            "default": default, "source": "plan"}


def _host_default(p: dict[str, Any], secret: str) -> str:
    hosts = [h for i in p["integrations"] if secret in i["secrets"] for h in i["hosts"]]
    hosts += [pp.hostname(e["base_url"]) for e in p["environments"] if e["base_url"]]
    return ", ".join(dict.fromkeys(h for h in hosts if h)) or "localhost"


def _type_default(col: dict[str, Any]) -> str:
    if col.get("enum"):
        return str(col["enum"][0])
    return {"integer": "0", "number": "0", "boolean": "false", "json": "{}"}.get(col.get("type"), "")


def plan_gap_questions(issues: list[dict[str, Any]], plan: dict[str, Any]) -> list[dict[str, Any]]:
    """One question per gap topic for the validation issues that need the team's input (``needs_input``), each with a
    default the plan can fall back to. Answers are applied by :func:`apply_gap_answers`."""
    p = pp.normalize(plan)
    need = [i for i in issues if i.get("needs_input")]
    out: list[dict[str, Any]] = []
    names = sorted({i["secret"] for i in need if i["code"] == "PLAN006" and i.get("secret")})
    if names:
        out.append(_q("gap_secret_names", "secret_names",
                      f"The integrations use the secret(s) {', '.join(names)}, which the plan does not define. Add them as "
                      "project secrets, or give the exact UPPER_SNAKE names to use instead.",
                      "Secret names must match the project secrets exactly.", "Add them as named",
                      ["Add them as named"]))
    hosts = sorted({i["secret"] for i in need if i["code"] == "PLAN007" and i.get("secret")})
    if hosts:
        out.append(_q("gap_secret_hosts", "secret_hosts",
                      f"Which hostnames may {', '.join(hosts)} be sent to? Hostnames only (no http://, no :port). One list for "
                      "all, or NAME=host1, host2; per secret.",
                      "The broker only sends a secret to its allowed hosts.", _host_default(p, hosts[0])))
    envs = sorted({i["environment"] for i in need if i["code"] == "PLAN008" and i.get("environment")})
    if envs:
        out.append(_q("gap_env_urls", "env_urls",
                      f"What is the base URL of {', '.join(envs)}? Use name=url; one per environment.",
                      "Integrations and smoke runs call this URL.", None))
    if any(i["code"] == "PLAN008" and not i.get("environment") for i in need):
        names_e = [e["name"] for e in p["environments"]]
        live = next((n for n in names_e if n.lower() in ("prod", "production", "live")), names_e[-1] if names_e else None)
        out.append(_q("gap_live_env", "live_env", "Which environment is live (where the refused actions are refused)?",
                      "The guard on dangerous actions is generated from this.", live, names_e))
    cols = [i for i in need if i["code"] == "PLAN010" and i.get("column")]
    if cols:
        tables = {t["name"]: t for t in p["data"]["tables"]}
        dflt = "; ".join(f"{i['table']}.{i['column']}=" + _type_default(next(
            (c for c in tables.get(i["table"], {}).get("columns", []) if c["name"] == i["column"]), {})) for i in cols)
        out.append(_q("gap_column_defaults", "column_defaults",
                      "These tables already have rows, so new required columns need a value for the existing rows: "
                      + ", ".join(f"{i['table']}.{i['column']}" for i in cols) + ". Give table.column=value; ...",
                      "Existing rows are backfilled with it.", dflt))
    ret = [i for i in need if i["code"] == "PLAN010" and i.get("policy")]
    if ret:
        dflt = "; ".join(f"{i['table']}={90 if i['policy'] == 'days' else 100}" for i in ret)
        out.append(_q("gap_retention_numbers", "retention_numbers",
                      "Retention numbers: " + ", ".join(f"{i['table']} ({'days' if i['policy'] == 'days' else 'rows kept'})"
                                                        for i in ret) + ". Give table=number; ...",
                      "A purge needs exact bounds.", dflt))
    return out


def _pairs(answer: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in re.split(r"[;\n]", answer or ""):
        if "=" in part:
            k, v = part.split("=", 1)
            if k.strip():
                out[k.strip()] = v.strip()
    return out


def _value(raw: str, typ: str) -> Any:
    raw = raw.strip()
    try:
        if typ == "integer":
            return int(raw)
        if typ == "number":
            return float(raw)
        if typ == "boolean":
            return raw.lower() in ("true", "yes", "1")
        if typ == "json":
            return json.loads(raw)
    except (ValueError, json.JSONDecodeError):
        return None
    return raw


def secrets_from_answers(plan: dict[str, Any]) -> dict[str, Any]:
    """Secret names (UPPER_SNAKE) the team named in a credentials answer become plan secrets, so nobody asks for them again."""
    p = pp.normalize(plan)
    have = {s["name"] for s in p["secrets"]}
    for a in p["requirements"]["answers"]:
        if a["topic"] not in ("credentials", "secret_names", "external_systems"):
            continue
        for name in _UPPER_SNAKE.findall(a["answer"]):
            if name not in have:
                have.add(name)
                hosts = [h for i in p["integrations"] for h in i["hosts"]]
                p["secrets"].append({"name": name, "purpose": "named in the requirements", "environments": [],
                                     "allowed_hosts": list(dict.fromkeys(hosts))})
    return p


_DECLINED = re.compile(r"\b(?:no|without|needs?\s+no|does\s+not\s+need|doesn'?t\s+need)\s+(?:any\s+|real\s+)?"
                       r"(?:login|log-?in|sign[- ]?in|secrets?|credentials?|passwords?)\b", re.IGNORECASE)
_PLACEHOLDER = re.compile(r"placeholder|never (?:actually )?call|not (?:a )?real|dummy|fake", re.IGNORECASE)


def drop_declined_secrets(plan: dict[str, Any]) -> dict[str, Any]:
    """When the team answered a credentials question with "no login" / "no secrets", the plan keeps no secret they did not
    name: the model's guesses are removed (from ``secrets`` and the integrations), with the reason written down."""
    p = pp.normalize(plan)
    answers = [a for a in p["requirements"]["answers"] if a["topic"] in ("credentials", "secret_names")]
    if not any(_DECLINED.search(a["answer"]) for a in answers):
        return p
    named = {n for a in p["requirements"]["answers"] for n in _UPPER_SNAKE.findall(a["answer"])}
    dropped = [s["name"] for s in p["secrets"] if s["name"] not in named]
    if not dropped:
        return p
    p["secrets"] = [s for s in p["secrets"] if s["name"] in named]
    for i in p["integrations"]:
        i["secrets"] = [n for n in i["secrets"] if n not in dropped]
    line = f"No secrets: the team answered that no login is needed, so {', '.join(dropped)} {'was' if len(dropped) == 1 else 'were'} left out."
    if line not in p["requirements"]["assumptions"]:
        p["requirements"]["assumptions"].append(line)
    return p


def mark_placeholder_envs(plan: dict[str, Any]) -> dict[str, Any]:
    """An environment the team called a placeholder ("prod is a placeholder, never call it") is marked ``placeholder``:
    it keeps its guards, and nobody is asked for its secrets or URL."""
    p = pp.normalize(plan)
    texts = [a["answer"] for a in p["requirements"]["answers"] if a["topic"] in ("environments", "env_urls", "live_env")]
    clauses = [c for t in texts for c in re.split(r"[;\n]|\.\s", t) if _PLACEHOLDER.search(c)]
    for e in p["environments"]:
        if not e["placeholder"] and any(re.search(rf"(?<![\w.-]){re.escape(e['name'])}(?![\w-])", c, re.IGNORECASE)
                                        for c in clauses):
            e["placeholder"] = True
    return p


def apply_gap_answers(plan: dict[str, Any], questions: list[dict[str, Any]], answers: dict[str, str]) -> dict[str, Any]:
    """Apply the answers to plan gap questions to the plan itself (deterministic: no model involved)."""
    p = pp.normalize(plan)
    issues = pp.validate_plan(p)
    by_id = {q["id"]: q for q in questions}
    for qid, ans in answers.items():
        q = by_id.get(qid)
        if q is None or q.get("source") != "plan" or not str(ans).strip():
            continue
        topic = q["topic"]
        if topic == "secret_names":
            names = {i["secret"] for i in issues if i["code"] == "PLAN006"}
            given = _UPPER_SNAKE.findall(ans)
            have = {s["name"] for s in p["secrets"]}
            for n in (given or sorted(names)):
                if n not in have:
                    p["secrets"].append({"name": n, "purpose": "", "environments": [], "allowed_hosts": []})
        elif topic == "secret_hosts":
            pairs = _pairs(ans)
            for s in p["secrets"]:
                raw = pairs.get(s["name"]) if pairs else (ans if not s["allowed_hosts"] else None)
                if raw:
                    s["allowed_hosts"] = [h for h in (pp.hostname(x) for x in re.split(r"[,\s]+", raw)) if h]
        elif topic == "env_urls":
            pairs = _pairs(ans)
            for e in p["environments"]:
                if e["name"] in pairs:
                    e["base_url"] = pairs[e["name"]]
                elif not pairs and not e["base_url"] and re.match(r"https?://", ans.strip()):
                    e["base_url"] = ans.strip()
        elif topic == "live_env":
            for e in p["environments"]:
                if e["name"].lower() == ans.strip().lower():
                    e["live"] = True
        elif topic == "column_defaults":
            tables = {t["name"]: t for t in p["data"]["tables"]}
            for k, v in _pairs(ans).items():
                tname, _, cname = k.partition(".")
                col = next((c for c in tables.get(tname, {}).get("columns", []) if c["name"] == cname), None)
                if col is not None and (val := _value(v, col["type"])) is not None:
                    col["default"] = val
        elif topic == "retention_numbers":
            tables = {t["name"]: t for t in p["data"]["tables"]}
            for k, v in _pairs(ans).items():
                if k in tables and v.strip().isdigit():
                    tables[k]["retention"]["value"] = int(v.strip())
    return p


def _combine(questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One question per topic: several on the same topic become one question listing each part."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for q in questions:
        groups.setdefault(q["topic"], []).append(q)
    out = []
    for topic, qs in groups.items():
        if len(qs) == 1:
            out.append(qs[0])
            continue
        first = qs[0]
        out.append({**first, "question": first["question"] + "\n" + "\n".join(f"- {q['question']}" for q in qs[1:]),
                    "why": "; ".join(dict.fromkeys(q.get("why") or "" for q in qs if q.get("why"))),
                    "options": first.get("options") or [], "default": first.get("default"),
                    "parts": [q["id"] for q in qs]})
    return out


def spec_projection(plan: dict[str, Any]) -> dict[str, Any]:
    """The plan as a ``module_generator`` spec, so its structural and domain checks run on the whole project at once."""
    p = pp.normalize(plan)
    return {"summary": p["summary"],
            "entities": [{"name": t["name"], "fields": t["columns"]} for t in p["data"]["tables"]],
            "workflows": [{"entity": t["name"], "field": t["state_machine"]["field"],
                           "states": next((c.get("enum") or [] for c in t["columns"] if c["name"] == t["state_machine"]["field"]), []),
                           "transitions": t["state_machine"]["transitions"]}
                          for t in p["data"]["tables"] if t.get("state_machine")],
            "roles": [{"name": "team", "can": [p["roles_note"]]}] if p["roles_note"] else [],
            "external_systems": [{"name": i["key"], "use": i["purpose"] or i["kind"]} for i in p["integrations"]],
            "acceptance_criteria": [a["text"] for a in p["requirements"]["acceptance"]]}


def first_round_questions(prompt: str, plan: dict[str, Any], model_questions: Any, facts: dict[str, Any] | None,
                          issues: list[dict[str, Any]] | None = None, model_checklist: Any = None) -> list[dict[str, Any]]:
    """Round 1 as one checklist: the deployment checks (roles, sample data, credentials, notifications, approvals, limits,
    retention, reports), the model's own list, its questions, the structural and domain checks and the plan gaps - one item
    per topic, each with options, suggested picks and a free-text other."""
    facts = facts or {}
    p = pp.normalize(plan)
    known = {t["name"] for t in p["data"]["tables"]} | {
        str(t.get("name")) for t in facts.get("tables") or [] if isinstance(t, dict)}
    spec = spec_projection(p)
    envs = list(facts.get("environments") or []) or [e["name"] for e in p["environments"]]
    domain = pcl.prune_domain_questions(gaps.domain_questions(prompt, spec, envs), p, envs, prompt)
    merged = merge_questions(model_questions, gap_questions(spec, known) + domain, context=prompt)
    issues = pp.validate_plan(p, facts) if issues is None else issues
    plan_gaps = plan_gap_questions(issues, p)
    ctx = prompt + " " + p["summary"]
    asked = [q for q in merged if q.get("source") == "model"]       # a topic the model asked about stays its question
    rest = [q for q in merged if q.get("source") != "model"]
    items = pcl.merge_items(asked, pcl.deployment_checklist(p, prompt, facts), pcl.model_items(model_checklist, ctx), rest, plan_gaps)
    return [pcl.decorate(q) for q in _combine(items)[:ROUND1_MAX]]


def record_answers(plan: dict[str, Any], questions: list[dict[str, Any]], answers: dict[str, str], round_no: int
                   ) -> dict[str, Any]:
    p = pp.normalize(plan)
    have = {a["id"] for a in p["requirements"]["answers"]}
    for q in questions:
        a = str(answers.get(q["id"]) or "").strip()
        if not a or q["id"] in have:
            continue
        src = q.get("source") if q.get("source") in ("model", "check", "domain", "plan", "checklist") else "model"
        p["requirements"]["answers"].append({"id": q["id"], "topic": q["topic"], "question": q["question"], "answer": a,
                                             "round": round_no, "source": src})
    return p


def asked_topics(plan: dict[str, Any]) -> set[str]:
    return {a["topic"] for a in pp.normalize(plan)["requirements"]["answers"]}


def follow_up(plan: dict[str, Any], facts: dict[str, Any] | None, round_no: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """After an answer round: the next round's questions (gaps still open whose topic was not asked yet), or, when no round
    is left or nothing new is open, the plan with every remaining gap filled from its question's default and the
    assumption written down."""
    issues = pp.validate_plan(plan, facts)
    gap_qs = plan_gap_questions(issues, plan)
    asked = asked_topics(plan)
    answered_n = len(pp.normalize(plan)["requirements"]["answers"])
    room = max(0, MAX_PLAN_QUESTIONS - answered_n)
    fresh = [q for q in gap_qs if q["topic"] not in asked][:room]
    if fresh and round_no < MAX_ROUNDS:
        return plan, fresh
    p = pp.normalize(plan)
    defaulted = [q for q in gap_qs if q.get("default")]
    if defaulted:
        p = apply_gap_answers(p, defaulted, {q["id"]: q["default"] for q in defaulted})
        for q in defaulted:
            line = f"{q['question'].splitlines()[0]} Assumed: {q['default']}"
            if line not in p["requirements"]["assumptions"]:
                p["requirements"]["assumptions"].append(line)
    return p, []


# ── the conversation ─────────────────────────────────────────────────────────


class PlanSource(BaseModel):
    """An external database the plan should be built on: a project connection key and, optionally, the tables of interest."""
    model_config = ConfigDict(extra="forbid")

    connection: str = Field(max_length=60)
    tables: list[str] | None = Field(default=None, max_length=100)


class PlanDraftRequest(BaseModel):
    """``POST /plan/draft``: ``{prompt}`` starts a plan (round 1); ``{version, answers}`` answers the open questions of a
    draft (``{version, accept_suggestions: true}`` takes every suggested pick, ``answers`` overriding single ones); ``{body}`` saves a hand-written plan body as a new draft without any model."""
    model_config = ConfigDict(extra="forbid")

    prompt: str | None = Field(default=None, max_length=MAX_PROMPT_CHARS)
    version: int | None = None
    answers: dict[str, str] | None = None
    accept_suggestions: bool = False
    body: dict[str, Any] | None = None
    sources: list[PlanSource] | None = Field(default=None, max_length=10)

    @field_validator("answers")
    @classmethod
    def _answers_size(cls, v: dict[str, str] | None) -> dict[str, str] | None:
        if v is not None and (len(v) > MAX_PLAN_QUESTIONS or any(len(a) > MAX_ANSWER_CHARS for a in v.values())):
            raise ValueError(f"at most {MAX_PLAN_QUESTIONS} answers of at most {MAX_ANSWER_CHARS} characters each")
        return v

    @model_validator(mode="after")
    def _one_mode(self) -> PlanDraftRequest:
        modes = [self.body is not None, self.answers is not None or self.accept_suggestions, bool((self.prompt or "").strip())]
        if sum(modes) != 1:
            raise ValueError("send exactly one of: prompt, answers or accept_suggestions (with version), body")
        if (self.answers is not None or self.accept_suggestions) and self.version is None:
            raise ValueError("answers need the version they answer")
        return self


class _Timeout(Exception):
    pass


class PlanConversation:
    """One step of the plan conversation. ``events()`` yields ``status`` events, then one ``result`` (``{plan}``) or
    ``error``. ``revise_prompt`` makes it a revision of the approved plan instead (``POST /plan/revise``)."""

    def __init__(self, project_id: str, req: PlanDraftRequest | None, actor: str | None = None,
                 is_disconnected: Callable[[], Awaitable[bool]] | None = None, timeout_s: float | None = None,
                 revise_prompt: str | None = None, sources: list[PlanSource] | None = None) -> None:
        self.project_id = project_id
        self.sources = sources if sources is not None else (req.sources if req is not None else None)
        self.req = req
        self.actor = actor
        self.revise_prompt = revise_prompt
        self._is_disconnected = is_disconnected
        self.timeout_s = settings.module_gen_timeout_s * 2 if timeout_s is None else timeout_s
        self.model = settings.module_gen_model
        self.scrubbed = False
        self.calls = 0
        self._t0 = time.monotonic()
        self._model_calls: list[dict[str, Any]] = []

    def timing(self) -> dict[str, Any]:
        """Planner wall time and model calls so far: ``{wall_s, model_calls, model_s, calls: [{tool, s}]}``."""
        return {"wall_s": round(time.monotonic() - self._t0, 2), "model_calls": self.calls,
                "model_s": round(sum(c["s"] for c in self._model_calls), 2), "calls": list(self._model_calls)}

    @staticmethod
    def _session() -> Any:
        from ..db import session as dbs

        return dbs.async_session_factory()

    async def events(self) -> AsyncIterator[dict[str, Any]]:
        loop = asyncio.get_running_loop()
        self._t0 = time.monotonic()
        deadline = loop.time() + self.timeout_s
        try:
            async for ev in self._run(deadline):
                yield ev
        except _Timeout:
            yield {"type": "error", "code": "timeout", "message": f"planning did not finish within {self.timeout_s:g} s"}
        except ProjectError as e:
            yield {"type": "error", "code": e.code, "message": e.message, "status": e.status, **e.extra}
        except asyncio.CancelledError:
            raise
        except Exception:  # one terminal error, details only in the log
            logger.exception("plan conversation failed")
            yield {"type": "error", "code": "internal", "message": "planning failed unexpectedly"}

    async def _call(self, messages: list[dict[str, Any]], tool: dict[str, Any], variants: set[str], deadline: float
                    ) -> dict[str, Any]:
        from ..engine import llm_provider

        outgoing, changed = _scrub(messages, variants)
        self.scrubbed = self.scrubbed or changed
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise _Timeout()
        self.calls += 1
        started = time.monotonic()
        try:
            resp = await asyncio.wait_for(llm_provider.call_llm(
                self.model, outgoing, temperature=0.2, tools=[tool], tool_choice="required", max_tokens=MAX_OUTPUT_TOKENS),
                remaining)
        except asyncio.TimeoutError as e:
            raise _Timeout() from e
        except Exception as exc:  # noqa: BLE001
            from .vars_substitution import redact_deep

            logger.warning("plan conversation: model call failed: %s", type(exc).__name__)
            raise ProjectError(502, "llm_failed", str(redact_deep(f"{type(exc).__name__}: {exc}", variants,
                                                                  expanded=True))[:500]) from exc
        self._model_calls.append({"tool": tool["name"], "s": round(time.monotonic() - started, 2)})
        call = next((tc for tc in getattr(resp, "tool_calls", None) or [] if tc.name == tool["name"]), None)
        return call.args if call is not None and isinstance(call.args, dict) else {}

    def _log_timing(self, step: str, body: dict[str, Any]) -> dict[str, Any]:
        t = {**self.timing(), "step": step, "plan_bytes": len(json.dumps(body, default=str))}
        logger.info("plan %s: %.1fs wall, %d model calls, %d bytes", step, t["wall_s"], t["model_calls"], t["plan_bytes"])
        return t

    async def _context(self) -> tuple[dict[str, Any], set[str]]:
        from ..db.models import ProjectModel
        from .module_generator import gather_facts, secret_values
        from .vars_substitution import expand_secret_variants

        async with self._session() as s:
            project = await s.get(ProjectModel, self.project_id)
            if project is None:
                raise ProjectError(404, "project_not_found", "Project not found")
            facts = await gather_facts(s, project)
            facts["connections_context"] = await self._connections_context(s)
            return facts, expand_secret_variants(await secret_values(s, project))

    async def _connections_context(self, session: Any) -> str:
        from .connection_service import connections_context

        srcs = self.sources or []
        keys = sorted({x.connection for x in srcs}) or None
        tables = [t for x in srcs for t in x.tables or []] or None
        return await connections_context(self.project_id, tables, keys, session=session)

    def _system(self, facts: dict[str, Any]) -> str:
        have = {"environments": facts.get("environments"), "secrets": sorted((facts.get("secrets") or {}).keys()),
                "tables": [{"name": t.get("name"), "columns": [c.get("name") for c in t.get("columns") or []],
                            "rows": t.get("row_count")} for t in facts.get("tables") or []],
                "routes": [f"{r.get('method')} {r.get('path')}" for r in facts.get("routes") or []],
                "modules": facts.get("modules"), "jobs_configured": facts.get("jobs")}
        out = PLAN_RULES + "\nThe project already has (reuse, do not duplicate):\n" + json.dumps(have, default=str)[:6000]
        if facts.get("connections_context"):
            out += ("\n" + facts["connections_context"] + "\nPlan on these external tables: they are read-only and already "
                    "connected (routes read them with studio.connection(key); add no integration, secret or environment for them; "
                    "point refs at them as connection.table.column; never plan to create or write them).")
        return out

    async def _run(self, deadline: float) -> AsyncIterator[dict[str, Any]]:
        req = self.req
        yield {"type": "status", "phase": "context", "message": "Reading the project"}
        facts, variants = await self._context()
        if self.revise_prompt is not None:
            async for ev in self._revise(facts, variants, deadline):
                yield ev
            return
        assert req is not None
        if req.body is not None:
            async with self._session() as s:
                approved = await pp.load_approved(s, self.project_id)
                row = await pp.create_draft(s, self.project_id, req.body, round_no=0, actor=self.actor,
                                            base_version=approved["version"] if approved else None)
                yield {"type": "result", "plan": pp.plan_to_dict(row, pp.validate_plan(row.body, facts))}
            return
        if req.answers is not None or req.accept_suggestions:
            async for ev in self._answer(facts, variants, deadline):
                yield ev
            return
        prompt = (req.prompt or "").strip()
        system = self._system(facts)
        yield {"type": "status", "phase": "core", "message": "Drafting the data model and modules"}
        core = await self._call([{"role": "system", "content": system}, {"role": "user", "content": prompt}],
                                EMIT_PLAN_CORE_TOOL, variants, deadline)
        yield {"type": "status", "phase": "surface", "message": "Drafting routes and screens"}
        surface = await self._call([{"role": "system", "content": system}, {"role": "user", "content": prompt},
                                    {"role": "user", "content": "The core of the plan:\n" + json.dumps(
                                        {k: v for k, v in core.items() if k != "questions"}, default=str)
                                     + "\nNow call emit_plan_surface: every route of every module, the apps and screens, "
                                       "and the NFR."}],
                                   EMIT_PLAN_SURFACE_TOOL, variants, deadline)
        body = draft_body(core, surface)
        body.setdefault("requirements", {})["request"] = prompt   # so a reloaded chat can show what was asked
        model_qs = list(core.get("questions") or []) + list(surface.get("questions") or [])
        issues = pp.validate_plan(body, facts)
        questions = first_round_questions(prompt, body, model_qs, facts, issues, core.get("checklist"))
        async with self._session() as s:
            approved = await pp.load_approved(s, self.project_id)
            row = await pp.create_draft(s, self.project_id, body, questions=questions, round_no=1, actor=self.actor,
                                        base_version=approved["version"] if approved else None)
            yield {"type": "result", "plan": pp.plan_to_dict(row, pp.validate_plan(row.body, facts)),
                   "scrubbed": self.scrubbed, "timing": self._log_timing("draft", body)}

    async def _answer(self, facts: dict[str, Any], variants: set[str], deadline: float) -> AsyncIterator[dict[str, Any]]:
        req = self.req
        assert req is not None and req.version is not None
        async with self._session() as s:
            row = await pp.get_version(s, self.project_id, req.version)
            if row.status != "draft":
                raise ProjectError(409, "plan_not_draft", f"plan v{row.version} is {row.status}")
            questions, round_no, base_updated = list(row.questions or []), row.round or 1, row.updated_at.isoformat()
            body = row.body
        if not questions:
            raise ProjectError(409, "no_open_questions", "this plan has no open questions")
        answers = dict(req.answers or {})
        if req.accept_suggestions:
            answers = {**pcl.suggested_answers(questions), **{k: v for k, v in answers.items() if str(v).strip()}}
        missing = [q["id"] for q in questions if not str(answers.get(q["id"]) or "").strip()]
        if missing:
            raise ProjectError(422, "unanswered_questions", f"unanswered questions: {', '.join(missing)}", missing=missing)
        body = record_answers(body, questions, answers, round_no)
        body = apply_gap_answers(body, questions, answers)
        body, needs_model = pcl.apply_checklist_answers(body, questions, answers)
        body = mark_placeholder_envs(drop_declined_secrets(secrets_from_answers(body)))
        if needs_model:     # free text, or an answer to a question only the model can place: one revision call
            yield {"type": "status", "phase": "revising", "message": "Updating the plan with your answers"}
            qa = [{"question": q["question"], "answer": answers[q["id"]]} for q in questions]
            rev = await self._call([{"role": "system", "content": self._system(facts)},
                                    {"role": "user", "content": "The current plan:\n" + json.dumps(body, default=str)},
                                    {"role": "user", "content": "The team answered:\n" + json.dumps(qa) + "\nRevise the plan so it "
                                     "follows every answer: call emit_plan_revision with only the sections that change."}],
                                   EMIT_PLAN_REVISION_TOOL, variants, deadline)
            body = merge_revision(body, rev)
            body = apply_gap_answers(body, questions, answers)    # the model may have dropped a settled fact
            body, _ = pcl.apply_checklist_answers(body, questions, answers)
            body = mark_placeholder_envs(drop_declined_secrets(secrets_from_answers(body)))   # ...or re-added a declined one
        body, more = follow_up(body, facts, round_no)
        async with self._session() as s:
            row = await pp.update_draft(s, self.project_id, req.version, body=body, questions=more,
                                        round_no=round_no + 1 if more else round_no, base_updated_at=base_updated)
            yield {"type": "result", "plan": pp.plan_to_dict(row, pp.validate_plan(row.body, facts)), "scrubbed": self.scrubbed,
                   "timing": self._log_timing("answer", body)}

    async def _revise(self, facts: dict[str, Any], variants: set[str], deadline: float) -> AsyncIterator[dict[str, Any]]:
        async with self._session() as s:
            approved = await pp.load_approved(s, self.project_id)
        if approved is None:
            raise ProjectError(409, "plan_not_approved", "nothing is approved yet; draft and approve a plan first")
        body = approved["body"]
        if (self.revise_prompt or "").strip():
            yield {"type": "status", "phase": "revising", "message": "Revising the approved plan"}
            rev = await self._call([{"role": "system", "content": self._system(facts)},
                                    {"role": "user", "content": "The approved plan:\n" + json.dumps(body, default=str)},
                                    {"role": "user", "content": "Change requested:\n" + self.revise_prompt.strip()
                                     + "\nCall emit_plan_revision with only the sections that change. Prefer additive "
                                       "changes (new columns, enum values, routes); deprecate instead of removing."}],
                                   EMIT_PLAN_REVISION_TOOL, variants, deadline)
            body = merge_revision(body, rev)
        async with self._session() as s:
            out = await pp.revise(s, self.project_id, body, actor=self.actor)
        out["issues"] = pp.validate_plan(out["body"], facts)
        yield {"type": "result", "plan": out, "diff": out["diff"], "affected": out["affected"], "scrubbed": self.scrubbed,
               "timing": self._log_timing("revise", body)}


def draft_body(core: dict[str, Any], surface: dict[str, Any]) -> dict[str, Any]:
    """The full plan body from the planner's compact core and surface (full-form sections pass through unchanged)."""
    raw = {k: v for k, v in core.items() if k not in ("questions", "checklist")}
    raw.update({k: v for k, v in surface.items() if k in SURFACE_KEYS})
    reqs = raw.get("requirements") if isinstance(raw.get("requirements"), dict) else {}
    raw["requirements"] = {**reqs, "answers": []}
    return pp.normalize(plan_compact.expand(raw))


_REV_REQ = ("assumptions", "limitations", "acceptance")


def merge_revision(body: dict[str, Any], rev: dict[str, Any]) -> dict[str, Any]:
    """The plan with every section the revision emitted replaced. Recorded answers and migrations are never the model's.
    A revision may use the compact forms; they are expanded against the plan they revise."""
    out = dict(pp.normalize(body))
    rev = dict(rev)
    if any(k in rev for k in ("tables", "route_groups", "acceptance", "assumptions", "limitations")) or any(
            isinstance(m, dict) and isinstance(m.get("helpers"), list) and m["helpers"] and isinstance(m["helpers"][0], str)
            for m in rev.get("modules") or []) or any(
            isinstance(a, dict) and any(isinstance(x, str) for x in a.get("screens") or []) for a in rev.get("apps") or []):
        ctx = {"data": out["data"], "modules": out["modules"], "apps": out["apps"]}
        if "route_groups" not in rev and "routes" not in rev:
            ctx["routes"] = out["routes"]
        x = plan_compact.expand({**ctx, **{k: v for k, v in rev.items() if k not in ("assumptions", "limitations", "acceptance")},
                                 **({"acceptance": rev["acceptance"]} if "acceptance" in rev else {})})
        mapped: dict[str, Any] = {}
        for k, v in rev.items():
            if k == "tables":
                mapped["data"] = {"tables": x["data"]["tables"]}
            elif k == "route_groups":
                mapped["routes"] = x["routes"]
            elif k in _REV_REQ:
                mapped.setdefault("requirements", {})[k] = x["requirements"].get(k, v) if k == "acceptance" else v
            elif k in ("modules", "apps", "routes", "build_order"):
                mapped[k] = x.get(k, v)
            else:
                mapped[k] = v
        rev = mapped
    for k in CORE_KEYS + SURFACE_KEYS:
        if k not in rev or rev[k] in (None, "", [], {}):
            continue
        if k == "requirements" and isinstance(rev[k], dict):
            out["requirements"] = {**out["requirements"], **{kk: vv for kk, vv in rev[k].items() if kk != "answers"}}
        elif k == "data" and isinstance(rev[k], dict):
            out["data"] = {**out["data"], **rev[k]}
        elif k in ("tables", "route_groups", "acceptance", "assumptions", "limitations", "checklist"):
            continue
        else:
            out[k] = rev[k]
    out["requirements"]["answers"] = pp.normalize(body)["requirements"]["answers"]
    out["migrations"] = pp.normalize(body)["migrations"]
    return pp.normalize(out)
