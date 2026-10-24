"""The one-shot workflow builder behind the editor chat (prototype and results: prototypes/workflow-oneshot/README.md).

A new workflow is ONE model call: a rules prompt (``oneshot_prompt.md``) filled with the project (node catalog, APIs, data tables,
variables with their non-secret values, database connections, callable sub-workflows) and the request; the reply is the whole
``workflow.json`` with its own tests. A change is one call too: the model gets the whole current workflow and replies with a
``changes.json`` patch that the platform applies (``apply_patch``; a rename follows every reference).

The platform owns everything that can be done without a model: parse, canvas layout, the form screen, the checker, the database
rules (``db_lint``) and the workflow's own tests run on the unsaved definition. Whatever still fails goes back to the model, at most
``MAX_FIXES`` times. The result is an ordinary ``ai_proposals`` row (``ready`` or ``failed``), so the studio's review, Keep, Discard
and Undo work unchanged and nothing is saved before Keep.

The model is ``WF_ONESHOT_MODEL``: ``cli:<model>`` runs the ``claude`` CLI (``claude -p``), anything else goes through
``engine/llm_provider``. Default ``cli:sonnet``.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..engine import ai_policy
from ..repositories.ai_proposal_repository import AIProposalRepository, new_proposal_id
from . import ai_builder as B
from . import auto_layout, workflow_diff
from . import proposal_sandbox as sb
from .proposal_sandbox import ProposalNotFound

logger = logging.getLogger(__name__)

PROMPT_FILE = Path(__file__).with_name("oneshot_prompt.md")
MAX_FIXES = 2
CALL_TIMEOUT_S = float(os.environ.get("WF_ONESHOT_TIMEOUT_S", "600"))
SKIP_TYPES = {"Sample", "Profile", "Aggregate", "Neo4j", "MongoDB", "ChatInput", "ErrorTrigger", "Html"}  # rarely needed in bank flows

EDIT_HEAD = (
    "You build automations (workflows) for a bank's internal teams. Below is a workflow that is already built, tested and running, "
    "and a change the user wants. Make exactly that change. Keep everything the change doesn't touch as it is: node names, parameters, "
    "connections and tests. Update the tests the change makes wrong and add tests that prove the change. Don't ask questions or plan "
    "out loud. Reply with:\n\nChanged: <one sentence: what you changed>\n"
    "Needs: <what someone must set up before it runs live, e.g. \"$vars.FINANCE_EMAIL\"; or \"nothing\">\n\n"
    '```json file="changes.json"\n{"nodes": [...], "rename": {...}, "remove_nodes": [...], "connect": [...], '
    '"disconnect": [...], "tests": [...], "remove_tests": [...]}\n```\n\n'
    "Reply with only the changes; the platform applies them to the current workflow:\n"
    "- `nodes`: every node you add or change, written out whole (name, type, all parameters). A node with an existing name replaces that node.\n"
    '- `rename`: `{"Old name": "New name"}`. The platform renames the node everywhere: connections, `$node["Old name"]` references, '
    "test mocks and assertions. After a rename use only the new name.\n"
    "- `remove_nodes`: names of nodes to delete; their connections go with them.\n"
    "- `connect` / `disconnect`: connections to add or remove, in the workflow's connection shape.\n"
    "- `tests`: tests you add or change, written out whole. A test with an existing name replaces it. `remove_tests`: names of tests to delete.\n"
    "- Leave out keys you don't need. Everything you don't mention stays exactly as it is.\n\n"
)


class OneShotFailed(B.AIFailed):
    pass


# ── the project, as the prompt and the checks see it ─────────────────────────────────────────


@dataclass
class Project:
    ctx: B.Ctx
    apis: dict[str, dict[str, Any]] = field(default_factory=dict)        # prompt name -> {"id", "operations"}
    variables: dict[str, dict[str, Any]] = field(default_factory=dict)   # name -> {"secret", "value", "description"}
    databases: dict[str, dict[str, Any]] = field(default_factory=dict)   # key -> {"writable", "description", "tables": {t: [(col, type)]}}
    tables: dict[str, list[dict[str, Any]]] = field(default_factory=dict)

    def api_ids(self) -> dict[str, str]:
        return {name: a["id"] for name, a in self.apis.items()}


async def load_project(project_ref: str, request: str, *, workflow_id: str | None = None, base: dict[str, Any] | None = None) -> Project:
    from sqlalchemy import select

    from ..db.connection_models import ProjectConnectionModel
    from ..db.models import DataTableModel
    from ..repositories.api_spec_repository import ApiSpecRepository
    from ..repositories.variable_repository import VariableRepository
    from .project_service import effective_environment, resolve_project
    from .variable_loader import load_run_variables

    ctx = await B.load_ctx(project_ref)
    await B.attach_subworkflows(ctx, request, workflow_id=workflow_id, base=base)
    async with B._sf()() as s:
        project = await resolve_project(s, ctx.project_id)
        env = effective_environment(project, "default")
        specs = await ApiSpecRepository(s).list(ctx.project_id)
        rows = await VariableRepository(s).load_runtime_rows(team_id=ctx.team_id, environment=env, project_id=ctx.project_id)
        conns = (await s.execute(select(ProjectConnectionModel).where(ProjectConnectionModel.project_id == ctx.project_id))).scalars().all()
        tables = {t.name: list(t.columns or []) for t in
                  (await s.execute(select(DataTableModel).where(DataTableModel.project_id == ctx.project_id))).scalars()}
    values = await load_run_variables(environment=env, team_id=ctx.team_id, project_id=ctx.project_id)
    p = Project(ctx=ctx, tables=tables)
    taken: set[str] = set()
    for r in specs:
        name = re.sub(r"[^A-Za-z0-9_-]+", "_", r.name or r.id).strip("_") or r.id
        name = name if name not in taken else r.id
        taken.add(name)
        p.apis[name] = {"id": r.id, "operations": (r.spec or {}).get("operations") or []}
    for k, v in rows.items():
        secret = v.type == "secret"
        p.variables[k] = {"secret": secret, "value": None if secret else values.get(k, v.value), "description": v.description}
    for c in conns:
        tbls = {}
        for t in (c.schema_snapshot or {}).get("tables") or []:
            tname = t["name"] if t.get("schema") in (None, "", "public") else f'{t["schema"]}.{t["name"]}'
            tbls[tname] = [(x["name"], x["type"]) for x in t.get("columns") or []]
        p.databases[c.key] = {"writable": not c.readonly, "description": c.name if c.name != c.key else "", "tables": tbls}
    return p


# ── prompt ───────────────────────────────────────────────────────────────────────────────────


def _prop(p: dict[str, Any], indent: str = "  ") -> list[str]:
    bits = [p.get("type", "")]
    if p.get("required"):
        bits.append("required")
    d = p.get("default")
    if d not in (None, "", [], {}):
        bits.append("default " + json.dumps(d))
    opts = [o.get("value") for o in p.get("options") or [] if isinstance(o, dict) and "value" in o]
    if opts and p.get("type") in ("options", "multiOptions"):
        bits.append("one of " + "|".join(str(o) for o in opts))
    show = (p.get("displayOptions") or {}).get("show")
    if show:
        bits.append("only when " + ", ".join(f"{k} in {v}" for k, v in show.items()))
    desc = (p.get("description") or "").strip()
    out = [f"{indent}- {p['name']} ({', '.join(b for b in bits if b)})" + (f": {desc}" if desc else "")]
    if p.get("type") == "collection" and p.get("properties"):
        out[0] += " -- a list of objects, each with:" if (p.get("typeOptions") or {}).get("multipleValues") or p.get("default") == [] else " -- an object with:"
        for s in p["properties"]:
            out += _prop(s, indent + "    ")
    return out


def render_catalog() -> str:
    from ..engine.node_registry import node_registry

    lines = []
    for info in node_registry.get_node_info_full():
        if info.type in SKIP_TYPES:
            continue
        outs = [o.get("name") for o in info.outputs or []] or ["main"]
        lines.append(f"### {info.type}: {info.description}")
        lines.append(f"  outputs: {', '.join(outs)}" + (" (dynamic)" if info.output_strategy else ""))
        for p in info.properties:
            if p.get("type") != "notice":
                lines += _prop(p)
    return "\n".join(lines)


def _shape(s: Any) -> str:
    if not isinstance(s, dict):
        return "any"
    t = s.get("type")
    if t == "object" and s.get("properties"):
        req = set(s.get("required") or [])
        return "{" + ", ".join(f"{k}{'' if k in req else '?'}: {_shape(v)}" for k, v in s["properties"].items()) + "}"
    if t == "array":
        return "[" + _shape(s.get("items")) + "]"
    if s.get("enum"):
        return "|".join(json.dumps(e) for e in s["enum"])
    return t or "any"


def render_apis(apis: dict[str, dict[str, Any]]) -> str:
    lines = []
    for sid, a in apis.items():
        for o in a["operations"]:
            lines.append(f'- {sid}.{o["operation_id"]}: {o.get("method")} {o.get("path")}: {o.get("summary") or ""}'.rstrip(": "))
            lines.append(f'    api_ref: {{"spec_id": "{sid}", "operation_id": "{o["operation_id"]}"}}')
            if o.get("request_shape"):
                lines.append(f'    request body: {_shape(o["request_shape"])}')
            for code, r in (o.get("responses") or {}).items():
                lines.append(f'    reply {code}: {_shape((r or {}).get("shape"))}')
    return "\n".join(lines) or "(none)"


def render_tables(tables: dict[str, list[dict[str, Any]]]) -> str:
    return "\n".join(f"- {t}: " + ", ".join(f'{c.get("name")} ({c.get("type")})' for c in cols) + " (plus id, created_at, updated_at)"
                     for t, cols in tables.items()) or "(none in this project)"


def render_vars(variables: dict[str, dict[str, Any]]) -> str:
    """Names, plus the value of every NON-secret variable (the model needs "ATM_DAILY_LIMIT = 1000" to write boundary tests);
    a secret is a name only and its value never reaches the model."""
    return "\n".join(f"- {k}" + (" (secret: value hidden)" if v.get("secret") else f' = {json.dumps(str(v.get("value") or ""))}')
                     + (f': {v["description"]}' if v.get("description") else "")
                     for k, v in sorted(variables.items())) or "(none defined yet)"


def render_dbs(dbs: dict[str, dict[str, Any]]) -> str:
    out = []
    for key, c in dbs.items():
        out.append(f'- {key} ({"writable" if c.get("writable") else "read-only"}): {c.get("description", "")}'.rstrip(": "))
        for t, cols in c["tables"].items():
            out.append(f"    {t}(" + ", ".join(f"{n} {ty}" for n, ty in cols) + ")")
    return "\n".join(out) or "(none)"


def render_subs(subs: list[dict[str, Any]]) -> str:
    out = []
    for w in subs:
        ins = ", ".join(f'{i.get("name")} ({i.get("type")}{", required" if i.get("required") else ""})' for i in w.get("inputs") or [])
        desc = w.get("untrusted_description") or w.get("description") or ""
        out.append(f'- {w["id"]}: "{w["name"]}": {desc}\n    inputs: {ins}\n    output fields: {", ".join(w.get("output_fields") or [])}')
    return "\n".join(out) or "(none)"


def _rules(p: Project) -> str:
    return (PROMPT_FILE.read_text()
            .replace("{{CATALOG}}", render_catalog())
            .replace("{{APIS}}", render_apis(p.apis))
            .replace("{{TABLES}}", render_tables(p.tables))
            .replace("{{VARIABLES}}", render_vars(p.variables))
            .replace("{{DATABASES}}", render_dbs(p.databases))
            .replace("{{SUBWORKFLOWS}}", render_subs(p.ctx.callable_subworkflows)))


def build_prompt(p: Project, request: str) -> str:
    return _rules(p).replace("{{REQUEST}}", request)


def edit_prompt(p: Project, current: dict[str, Any], request: str) -> str:
    rules = _rules(p)
    rules = rules[rules.index("# The workflow format"):rules.index("# The request")]
    return (EDIT_HEAD + rules + "# The current workflow\n\n```json\n" + json.dumps(shown(current, p), indent=1) + "\n```\n\n"
            + "# The change\n\n" + request + "\n")


# ── platform: parse, patch, finish ───────────────────────────────────────────────────────────


def _blocks(reply: str) -> list[str]:
    return re.findall(r"```json[^\n]*\n(.*?)\n```", reply, re.S)


def _loads(raw: str) -> Any:
    return json.loads(re.sub(r",\s*([}\]])", r"\1", raw))


def _normalize(d: dict[str, Any], api_ids: dict[str, str]) -> dict[str, Any]:
    """Default ports, no positions, ``api_ref`` as ``{spec_id, operation_id}`` with the prompt's API name mapped to the stored spec id."""
    for c in d.get("connections") or []:
        c.setdefault("source_output", "main")
        c.setdefault("target_input", "main")
    for n in d.get("nodes") or []:
        n.setdefault("parameters", {})
        n.pop("position", None)
        ref = n["parameters"].get("api_ref")
        if isinstance(ref, str) and "." in ref:
            ref = dict(zip(("spec_id", "operation_id"), ref.split(".", 1)))
        if isinstance(ref, dict):
            sid = str(ref.get("spec_id") or "")
            if "." in sid and not ref.get("operation_id"):
                sid, ref["operation_id"] = sid.split(".", 1)
            sid = sid.removesuffix(".yaml").removesuffix(".json")
            ref["spec_id"] = api_ids.get(sid, sid)
            n["parameters"]["api_ref"] = ref
            n["parameters"].setdefault("url", "")
    return d


def parse(reply: str, api_ids: dict[str, str]) -> dict[str, Any]:
    blocks = _blocks(reply)
    if not blocks:
        raise ValueError('no ```json file="workflow.json" block in the reply')
    return _normalize(_loads(blocks[0]), api_ids)


def shown(d: dict[str, Any], p: Project | None = None) -> dict[str, Any]:
    """The workflow as the model sees it: no canvas positions, no form screen, API refs by their prompt name."""
    names = {v: k for k, v in (p.api_ids() if p else {}).items()}
    d = copy.deepcopy(d)
    for k in [k for k in d if k.startswith("_") or k in ("interface", "id", "pinned_data")]:
        d.pop(k)
    for n in d.get("nodes") or []:
        n.pop("position", None)
        ref = (n.get("parameters") or {}).get("api_ref")
        if isinstance(ref, dict) and ref.get("spec_id") in names:
            ref["spec_id"] = names[ref["spec_id"]]
    return d


def _rename_refs(obj: Any, old: str, new: str) -> Any:
    s = json.dumps(obj)
    for q in ('"', "'"):
        s = s.replace(json.dumps(f"$node[{q}{old}{q}]")[1:-1], json.dumps(f"$node[{q}{new}{q}]")[1:-1])
    s = s.replace(json.dumps(f'node_data["{old}"]')[1:-1], json.dumps(f'node_data["{new}"]')[1:-1])
    return json.loads(s)


def apply_patch(current: dict[str, Any], patch: dict[str, Any], api_ids: dict[str, str] | None = None) -> dict[str, Any]:
    """``current`` with a ``changes.json`` applied. Raises ``ValueError`` (fed back to the model) on a name that does not exist."""
    api_ids = api_ids or {}
    d = copy.deepcopy(current)
    d.setdefault("nodes", [])
    d.setdefault("connections", [])
    for old, new in (patch.get("rename") or {}).items():
        n = next((x for x in d["nodes"] if x["name"] == old), None)
        if n is None:
            raise ValueError(f"rename: there is no node named {old!r}")
        n["name"] = new
        for c in d["connections"]:
            for k in ("source_node", "target_node"):
                if c.get(k) == old:
                    c[k] = new
        for t in d.get("tests") or []:
            t["mocks"] = {(new if k == old else k): v for k, v in (t.get("mocks") or {}).items()}
            for a in t.get("expect") or []:
                if a.get("node") == old:
                    a["node"] = new
        d["nodes"] = _rename_refs(d["nodes"], old, new)
    gone = set(patch.get("remove_nodes") or [])
    if missing := gone - {n["name"] for n in d["nodes"]}:
        raise ValueError(f"remove_nodes: no such node(s) {sorted(missing)}")
    d["nodes"] = [n for n in d["nodes"] if n["name"] not in gone]
    d["connections"] = [c for c in d["connections"] if c["source_node"] not in gone and c["target_node"] not in gone]
    added = _normalize({"nodes": patch.get("nodes") or [], "connections": patch.get("connect") or []}, api_ids)
    dropped = _normalize({"connections": patch.get("disconnect") or []}, api_ids)
    by_name = {n["name"]: i for i, n in enumerate(d["nodes"])}
    for n in added["nodes"]:
        if n["name"] in by_name:
            n.setdefault("position", d["nodes"][by_name[n["name"]]].get("position"))
            d["nodes"][by_name[n["name"]]] = n
        else:
            d["nodes"].append(n)

    def key(c: dict[str, Any]) -> tuple[Any, ...]:
        return c["source_node"], c.get("source_output", "main"), c["target_node"], c.get("target_input", "main")

    drop = {key(c) for c in dropped["connections"]}
    d["connections"] = [c for c in d["connections"] if key(c) not in drop]
    have = {key(c) for c in d["connections"]}
    d["connections"] += [c for c in added["connections"] if key(c) not in have]
    removed_tests = set(patch.get("remove_tests") or [])
    tests = [t for t in d.get("tests") or [] if t.get("name") not in removed_tests]
    by_t = {t.get("name"): i for i, t in enumerate(tests)}
    for t in patch.get("tests") or []:
        if t.get("name") in by_t:
            old_t = tests[by_t[t["name"]]]
            for k in ("id", *_APPROVAL_KEYS):   # the same test, changed: it keeps its id and its approval
                if k in old_t:
                    t[k] = old_t[k]
            tests[by_t[t["name"]]] = t
        else:
            tests.append(t)
    d["tests"] = tests
    return d


_APPROVAL_KEYS = ("status", "locked", "approved_by", "approved_at")


def read_patch(reply: str) -> dict[str, Any]:
    blocks = _blocks(reply)
    if not blocks:
        raise ValueError('no ```json file="changes.json" block in the reply')
    p = _loads(blocks[-1])
    if not isinstance(p, dict):
        raise ValueError("changes.json must be an object")
    return p


def finish(d: dict[str, Any], keep_positions: bool = False) -> dict[str, Any]:
    """What the platform owns: tests normalised (ids), layout and the form screen."""
    from ..schemas.interface import generate_interface, slugify
    from ..schemas.workflow import normalize_tests

    for t in d.get("tests") or []:
        if isinstance(t, dict):
            # the builder's tests are drafts: they run and gate the review, but no person approved (= locked) them, so a later
            # chat edit may still change them; a test a person approved stays locked (``locked_test_failures``)
            t.setdefault("status", "draft")
    d["tests"] = normalize_tests(d.get("tests"))
    keep = [n["name"] for n in d["nodes"] if keep_positions and n.get("position")]
    d = auto_layout.auto(d, keep=keep)
    if any(n.get("type") == "FormTrigger" for n in d["nodes"]):
        try:
            d["interface"] = generate_interface(d, d.get("name") or "Workflow", "form", slugify(d.get("name") or "workflow"))
        except Exception:  # noqa: BLE001 - a form screen that can't be generated leaves the workflow without one
            logger.info("form screen generation failed", exc_info=True)
            d.pop("interface", None)
    return d


def summary_line(reply: str, word: str) -> str:
    return next((ln.split(":", 1)[1].strip() for ln in reply.splitlines() if ln.startswith(f"{word}:")), "")


# ── checks ───────────────────────────────────────────────────────────────────────────────────


def db_lint(d: dict[str, Any], dbs: dict[str, dict[str, Any]]) -> list[str]:
    """The connection exists, a read-only connection only reads, and values reach SQL as parameters, never pasted into its text."""
    fails = []
    for n in d.get("nodes") or []:
        if n.get("type") != "Postgres":
            continue
        p = n.get("parameters") or {}
        key = p.get("connection")
        if key not in dbs:
            fails.append(f'"{n["name"]}": connection {key!r} is not one of the project connections ({", ".join(dbs) or "none"})')
            continue
        op = p.get("operation", "query")
        st = p.get("statements")
        if op == "query":
            sqls = [str(p.get("query") or "")]
        elif isinstance(st, list):
            sqls = [str((s or {}).get("query", "")) for s in st]
        else:
            sqls = [str(st or "")]
        if any("{{" in q for q in sqls):
            fails.append(f'"{n["name"]}": an expression is pasted into the SQL text; put values in queryParameters/params and use %s')
        if not dbs[key].get("writable") and (op != "query" or not sqls[0].lstrip().lower().startswith(("select", "with"))):
            fails.append(f'"{n["name"]}": connection {key!r} is read-only; it only runs one SELECT/WITH query')
    return fails


def _expr_errors(r: dict[str, Any]) -> list[str]:
    return [node for node, ports in (r.get("outputs") or {}).items() if "[Expression Error" in json.dumps(ports, default=str)]


async def check(d: dict[str, Any], p: Project, proposal: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    """``(failures as plain sentences, evidence)``: structure, checker errors, database rules, then the workflow's own tests."""
    from ..repositories.workflow_repository import definition_to_workflow

    ev: dict[str, Any] = {"check": {"findings": [], "errors": 0, "warnings": 0, "grounding": []}, "tests": None}
    try:
        sb._check_structure(definition_to_workflow(copy.deepcopy(d), d.get("name") or "wf"))
    except Exception as e:  # noqa: BLE001
        return [f"structure: {getattr(e, 'message', None) or getattr(e, 'detail', None) or e}"], ev
    ev["check"] = B.check_all(d, p.ctx)
    fails = [f'checker {f["code"]} at {f.get("node")}{"." + f["param"] if f.get("param") else ""}: {f["message"]}'
             for f in ev["check"]["findings"] if f.get("severity") == "error"]
    fails += db_lint(d, p.databases)
    if not d.get("tests"):
        return fails + ["no tests"], ev
    async with B._sf()() as s:
        report = await sb.run_proposal_tests(s, proposal, d)
    ev["tests"] = report
    for r in report.get("results") or []:
        if r["status"] != "passed":
            for f in [f for f in r.get("failures") or [] if f.get("kind") != "contract"][:4]:
                fails.append(f'test "{r.get("name")}": {f.get("kind")}: {f.get("message")}')
        for node in _expr_errors(r):
            fails.append(f'test "{r.get("name")}": an expression in "{node}" failed: {json.dumps(r["outputs"][node], default=str)[:300]}')
    return fails, ev


# ── the model ───────────────────────────────────────────────────────────────────────────────


def model_name() -> str:
    return (os.environ.get("WF_ONESHOT_MODEL") or "cli:sonnet").strip()


async def call_model(prompt: str) -> str:
    model = model_name()
    if model.startswith("cli:"):
        proc = await asyncio.create_subprocess_exec(
            os.environ.get("WF_ONESHOT_CLI", "claude"), "-p", "--model", model[4:] or "sonnet", "--tools", "",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            out, err = await asyncio.wait_for(proc.communicate(prompt.encode()), CALL_TIMEOUT_S)
        except asyncio.TimeoutError:
            proc.kill()
            raise OneShotFailed("The model did not answer in time.", ai_code="timeout") from None
        if proc.returncode != 0:
            logger.warning("claude CLI failed: %s", (err or out).decode(errors="replace")[:500])
            raise OneShotFailed("The model call failed.", ai_code="provider_error")
        return out.decode()
    from ..engine import llm_provider

    resp = await llm_provider.call_llm(model, [{"role": "user", "content": prompt}], max_tokens=32000)
    return resp.text or ""


# ── one build or edit ────────────────────────────────────────────────────────────────────────


async def _loop(first_prompt: str, make: Any, p: Project, proposal: dict[str, Any], guard: Any = None) -> dict[str, Any]:
    """One call, then at most ``MAX_FIXES`` fix calls. ``make(reply) -> definition`` (raises on an unreadable reply);
    ``guard(definition) -> [failure]`` adds what only this kind of proposal must respect (an edit: locked tests)."""
    rounds: list[dict[str, Any]] = []
    prompt, best = first_prompt, None
    for n in range(MAX_FIXES + 1):
        t = time.monotonic()
        reply = await call_model(prompt)
        try:
            d = make(reply)
            fails, ev = await check(d, p, proposal)
            fails = (guard(d) if guard else []) + fails
        except Exception as e:  # noqa: BLE001 - an unreadable reply is feedback for the next call
            d, fails, ev = None, [f"reply could not be read: {e}"], {"check": {"findings": [], "errors": 1, "warnings": 0, "grounding": []}, "tests": None}
        rounds.append({"round": n, "seconds": round(time.monotonic() - t, 1), "failures": fails[:20], "chars": len(reply)})
        if d is not None and (best is None or len(fails) <= len(best["fails"])):
            best = {"d": d, "fails": fails, "ev": ev, "reply": reply}
        if d is not None and not fails:
            break
        if n < MAX_FIXES:
            prompt = (first_prompt + "\n\nYour previous reply is below. When we ran it, these failed:\n" + "\n".join(f"- {f}" for f in fails)
                      + "\n\nFix them and reply again in the same format. If a test itself was wrong (it expects something the request "
                      "doesn't say), fix the test, never weaken a rule.\n\nPrevious reply:\n" + reply)
    return {"best": best, "rounds": rounds}


def _report(out: dict[str, Any]) -> dict[str, Any]:
    best = out["best"] or {"fails": ["the model never produced a readable workflow"], "ev": {"check": {}, "tests": None}}
    chk = best["ev"].get("check") or {}
    tests = best["ev"].get("tests")
    green = not best["fails"]
    return {
        "passed": green and bool(tests and tests["summary"]["total"]),
        "builder": "oneshot",
        "rounds": [{"round": r["round"], "errors": len(r["failures"]), "failures": r["failures"], "seconds": r["seconds"]} for r in out["rounds"]],
        "best_round": len(out["rounds"]) - 1,
        "checker": {"errors": int(chk.get("errors", 0)), "warnings": int(chk.get("warnings", 0)),
                    "findings": B._findings_for_model(chk.get("findings") or [], 100)},
        "tests": tests, "spec_coverage": {}, "grounding": [], "failures": best["fails"],
        "stopped_because": "passed" if green else "rounds_exhausted",
        "summary": summary_line(best.get("reply") or "", "Built") or summary_line(best.get("reply") or "", "Changed"),
        "needs": summary_line(best.get("reply") or "", "Needs"),
    }


async def _run(pid: str, row: dict[str, Any], first_prompt: str, make: Any, p: Project, base: dict[str, Any] | None, t0: float,
               guard: Any = None) -> dict[str, Any]:
    try:
        out = await _loop(first_prompt, make, p, row, guard)
    except B.ProposalError as e:
        await B._finish(pid, "failed", {"error_code": e.code, "error_message": e.message}, {"status": "failed", "passed": False}, t0)
        raise
    except Exception as e:  # noqa: BLE001 - a bug must not leave the row 'building'
        logger.exception("one-shot build crashed")
        await B._finish(pid, "failed", {"error_code": "internal_error", "error_message": f"The build crashed ({type(e).__name__})."},
                        {"status": "failed", "passed": False}, t0)
        raise OneShotFailed("The build crashed. Nothing was saved.", ai_code="internal_error", proposal_id=pid) from None
    report = _report(out)
    best = out["best"]
    if best is None:
        await B._finish(pid, "failed", {"error_code": "no_workflow", "error_message": "The model did not produce a readable workflow.", "report": report},
                        {"status": "failed", "passed": False, "rounds": len(out["rounds"])}, t0)
        return await B.get_view(pid)
    final = B._canonical_definition(best["d"])
    if best["d"].get("interface"):
        final["interface"] = best["d"]["interface"]
    usable = report["checker"]["errors"] == 0 and bool(final.get("nodes"))
    status = "ready" if usable else "failed"
    spec = {**row["spec"], "title": final.get("name") or row["spec"]["title"], "summary": report["summary"] or row["spec"]["summary"]}
    fields: dict[str, Any] = {"proposed_definition": final, "diff": workflow_diff.diff_definitions(base, final), "report": report, "spec": spec}
    if not usable:
        fields.update(error_code="checker_errors", error_message="The workflow still has checker errors after the fix calls.")
    await B._finish(pid, status, fields, {"status": status, "passed": report["passed"], "rounds": len(out["rounds"])}, t0)
    return await B.get_view(pid)


def _spec(title: str, summary: str, trigger: str = "manual") -> dict[str, Any]:
    from . import ai_spec

    return {"spec_version": ai_spec.SPEC_VERSION, "title": title, "summary": summary[:300], "trigger": {"kind": trigger, "fields": []},
            "rules": [], "steps": [], "interface": None, "tests": [], "rule_tests": {}, "assumptions": [], "questions": [], "issues": [],
            "warnings": []}


async def _insert(row: dict[str, Any], request: str, actor: str) -> None:
    async with B._sf()() as s:
        repo = AIProposalRepository(s)
        await repo.insert(row)
        await repo.add_event(row["id"], "spec_requested", {"request_sha256": B._sha(request), "model": row["model"], "via": "oneshot"}, actor)
        await s.commit()


async def build(project_ref: str, request: str, actor: str) -> dict[str, Any]:
    """A new workflow from one request: a ``build`` proposal, ``ready`` (tested) or ``failed``. Nothing is saved before Keep."""
    actor = B.clean_actor(actor)
    request = _clean(request)
    p = await load_project(project_ref, request)
    clean_request, _ = scrub_request(request, p.ctx.secrets)
    pid, model, now = new_proposal_id(), model_name(), B._now()
    row = {"id": pid, "project_id": p.ctx.project_id, "team_id": p.ctx.team_id, "workflow_id": None, "kind": "build", "status": "building",
           "actor": actor, "request": clean_request, "request_sha256": B._sha(request), "spec": _spec("New workflow", clean_request),
           "spec_approved_by": actor, "spec_approved_at": now, "spec_fingerprint": sb.fingerprint([]), "base_definition": None,
           "base_draft_hash": None, "model": model, "provider": "oneshot", "region": ai_policy.region()}
    await _insert(row, request, actor)
    api_ids = p.api_ids()
    with B._Busy(p.ctx.project_id, actor):
        return await _run(pid, row, build_prompt(p, clean_request), lambda reply: finish(parse(reply, api_ids)), p, None, time.monotonic())


async def edit(workflow_id: str, request: str, actor: str, *, project_id: str | None = None, node: str | None = None) -> dict[str, Any]:
    """A change to a saved workflow's draft: a ``modify`` proposal from one ``changes.json``. Nothing is saved before Keep."""
    from ..repositories.workflow_repository import WorkflowRepository

    actor = B.clean_actor(actor)
    request = _clean(request)
    async with B._sf()() as s:
        stored = await WorkflowRepository(s).get(workflow_id)
        if stored is None or (project_id and stored.project_id != project_id and not await B._same_project(s, project_id, stored.project_id)):
            raise ProposalNotFound(f"Workflow not found: {workflow_id}")
        base, base_hash = sb.draft_of(stored)
        name = stored.name
    p = await load_project(stored.project_id, request, workflow_id=workflow_id, base=base)
    clean_request, _ = scrub_request(request, p.ctx.secrets)
    instruction = f"About the node '{node}': {clean_request}" if node else clean_request
    pid, model, now = new_proposal_id(), model_name(), B._now()
    row = {"id": pid, "project_id": p.ctx.project_id, "team_id": p.ctx.team_id, "workflow_id": workflow_id, "kind": "modify", "status": "building",
           "actor": actor, "request": clean_request, "request_sha256": B._sha(request), "spec": _spec(name, clean_request),
           "spec_approved_by": actor, "spec_approved_at": now, "spec_fingerprint": sb.fingerprint([]), "base_definition": base,
           "base_draft_hash": base_hash, "model": model, "provider": "oneshot", "region": ai_policy.region()}
    await _insert(row, request, actor)
    api_ids = p.api_ids()
    current = {**base, "name": base.get("name") or name}

    def make(reply: str) -> dict[str, Any]:
        return finish(apply_patch(current, read_patch(reply), api_ids), keep_positions=True)

    def guard(d: dict[str, Any]) -> list[str]:
        return locked_test_failures(base.get("tests") or [], d.get("tests") or [])

    with B._Busy(p.ctx.project_id, actor):
        return await _run(pid, row, edit_prompt(p, current, instruction), make, p, base, time.monotonic(), guard)


async def revise(parent: dict[str, Any], instruction: str, actor: str) -> dict[str, Any]:
    """The review panel's "ask for a change" on a one-shot proposal: one ``changes.json`` on the proposal's result, as a child proposal
    with the parent's base (so Keep still creates or updates the same workflow); the parent moves to ``discarded``."""
    actor = B.clean_actor(actor)
    instruction = _clean(instruction)
    if parent["status"] != "ready" or not parent.get("proposed_definition"):
        raise B.BadState(parent["status"], "Only a proposal that is ready can be revised.")
    base = parent.get("base_definition")
    p = await load_project(parent["project_id"], instruction, workflow_id=parent.get("workflow_id"), base=base)
    clean_instruction, _ = scrub_request(instruction, p.ctx.secrets)
    cid, model = new_proposal_id(), model_name()
    row = {"id": cid, "project_id": parent["project_id"], "team_id": parent["team_id"], "workflow_id": parent.get("workflow_id"),
           "parent_id": parent["id"], "kind": parent["kind"], "status": "building", "actor": actor, "request": clean_instruction,
           "request_sha256": B._sha(instruction), "spec": parent["spec"], "spec_approved_by": parent["spec_approved_by"],
           "spec_approved_at": parent["spec_approved_at"], "spec_fingerprint": parent["spec_fingerprint"], "base_definition": base,
           "base_draft_hash": parent.get("base_draft_hash"), "model": model, "provider": "oneshot", "region": ai_policy.region()}
    async with B._sf()() as s:
        repo = AIProposalRepository(s)
        if await repo.transition(parent["id"], ["ready"], "discarded") is None:
            await s.rollback()
            raise B.BadState((await repo.get(parent["id"]))["status"])
        await repo.add_event(parent["id"], "discarded", {"actor": actor, "revised_by": cid}, actor)
        await repo.insert(row)
        await repo.add_event(cid, "spec_requested", {"request_sha256": B._sha(instruction), "model": model, "via": "oneshot"}, actor)
        await s.commit()
    api_ids = p.api_ids()
    current = parent["proposed_definition"]

    def make(reply: str) -> dict[str, Any]:
        return finish(apply_patch(current, read_patch(reply), api_ids), keep_positions=True)

    def guard(d: dict[str, Any]) -> list[str]:
        return locked_test_failures((base or {}).get("tests") or [], d.get("tests") or [])

    with B._Busy(p.ctx.project_id, actor):
        return await _run(cid, row, edit_prompt(p, current, clean_instruction), make, p, base, time.monotonic(), guard)


def locked_test_failures(stored: list[dict[str, Any]], incoming: list[dict[str, Any]]) -> list[str]:
    """What Keep would refuse, as feedback: a locked test that was changed or removed (a new test is fine; Keep approves it)."""
    from ..engine.test_mode import locked_test_ids
    from ..schemas.workflow import normalize_tests

    old = normalize_tests(stored)
    known = {t["id"] for t in old}
    new = [t if t["id"] in known else sb._as_draft(t) for t in normalize_tests(incoming)]
    names = {t["id"]: t.get("name") for t in old + new}
    return [f'test "{names.get(tid) or tid}" is locked: keep it exactly as it is (add a new test instead)' for tid in locked_test_ids(old, new)]


def scrub_request(request: str, secrets: set[str]) -> tuple[str, dict[str, Any]]:
    """Secrets, cards, IBANs, SSNs, phones and account numbers are removed; email addresses stay. In a build or change request they
    are where the workflow sends things ("email compliance@bank.test"), and without them the model has to guess."""
    emails: list[str] = []

    def keep(m: re.Match[str]) -> str:
        emails.append(m.group(0))
        return f"\x00E{len(emails) - 1}\x00"

    clean, counts = ai_policy.scrub_request(ai_policy._EMAIL_RE.sub(keep, request), secrets)
    return re.sub(r"\x00E(\d+)\x00", lambda m: emails[int(m.group(1))], clean), counts


def _clean(request: str) -> str:
    request = (request or "").strip()
    if not request:
        raise B.RequestInvalid("Say what you want built or changed.")
    if len(request) > B.MAX_REQUEST_CHARS:
        raise B.RequestInvalid(f"The request is longer than {B.MAX_REQUEST_CHARS} characters.")
    return request
