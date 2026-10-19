"""The Stage 6 builder loop (DESIGN-STAGE6-AI-BUILDER sections 3, 4, 6): request -> spec -> approve -> build -> review -> Keep.

Everything the model says goes through ``engine/wf_ai.ask`` and comes back as plain data. The model never writes workflow JSON:
it sends toolkit ops, the server applies them one by one (a refused op is feedback, never a crash), runs the deterministic
repairs, fills default mocks, lays the nodes out, attaches the interface, runs the checker, the AI-only expression checks and the
approved tests, and feeds what is still wrong back for up to ``WF_AI_MAX_REPAIR_ROUNDS`` repair rounds. Nothing touches a saved
workflow before Keep (``proposal_sandbox.keep``).

Stages of one build (``_build_loop``):

1. skeleton   the base draft (or an empty workflow) plus the approved spec tests; approved tests never change afterwards.
2. ask        ``WF_AI:BUILD`` (round 0) or ``WF_AI:REPAIR`` (later rounds) -> a batch of ops.
3. apply      parse ``args_json``, refuse what the allowlist forbids (6.4), apply each op alone (``workflow_toolkit.apply_ops``).
4. repair     deterministic FIX01-FIX12 (``ai_repairs``), default mocks (``ai_default_mocks``), layout (``auto_layout``), interface
              (Stage 5 ``generate_interface``).
5. check      mistake checker, AIX01-AIX04 (``ai_grounding``), spec coverage (``ai_spec``).
6. test       the definition's tests on the unsaved proposal (``proposal_sandbox.run_proposal_tests``, nothing persisted).
7. decide     stop when clean / no progress / out of rounds, calls or time; otherwise feed the evidence back.

Every step writes an audit event (2.2) in its own committed transaction, so the studio can poll ``/events`` during the build.
"""

from __future__ import annotations

import copy
import difflib
import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from ..engine import ai_policy, llm_provider, wf_ai
from ..engine.wf_ai import AIError, Meter
from ..repositories.ai_proposal_repository import AIProposalRepository, new_proposal_id
from . import ai_default_mocks, ai_grounding, ai_repairs, ai_spec, auto_layout, workflow_diff, workflow_toolkit
from . import ai_builder_prompts as prompts
from . import proposal_sandbox as sb
from .proposal_sandbox import BadState, ProposalError, ProposalNotFound

logger = logging.getLogger(__name__)

MAX_REQUEST_CHARS = 8000
MAX_ACTOR_CHARS = 80
SECRET_MARK = "[REDACTED]"

# ── errors (section 4 envelope) ─────────────────────────────────────────────────────────────


class ActorRequired(ProposalError):
    http_status, code = 422, "actor_required"


class RequestInvalid(ProposalError):
    http_status, code = 422, "request_invalid"


class AINotConfigured(ProposalError):
    http_status, code = 503, "ai_not_configured"


class ModelNotAllowed(ProposalError):
    http_status, code = 403, "model_not_allowed"


class AIFailed(ProposalError):
    http_status, code = 502, "ai_failed"


class BuilderBusy(ProposalError):
    http_status, code = 409, "builder_busy"


class AnswerInvalid(ProposalError):
    """422 ``answer_invalid`` (capability graph): an answer does not fit its question. ``detail`` carries ``qid`` and the message."""

    http_status, code = 422, "answer_invalid"

    def __init__(self, qid: str, message: str) -> None:
        super().__init__(message, qid=qid)


class SpecInvalid(ProposalError):
    http_status, code = 422, "spec_invalid"

    def __init__(self, issues: list[dict[str, Any]], message: str | None = None) -> None:
        super().__init__(message or "The spec still has errors; fix them before approving.", issues=issues)


def clean_actor(actor: str | None) -> str:
    a = (actor or "").strip()
    if not a:
        raise ActorRequired("Say who is asking: send `actor` (or the X-Actor-Name header).")
    if len(a) > MAX_ACTOR_CHARS:
        raise ActorRequired(f"`actor` must be at most {MAX_ACTOR_CHARS} characters.")
    return a


def ai_error_to_http(e: AIError, **extra: Any) -> ProposalError:
    """``AIError`` -> the section 4 error: not_configured 503, model_not_allowed 403, everything else 502 ``ai_failed``."""
    if e.code == "not_configured":
        return AINotConfigured("AI isn't set up on this engine.", **extra)
    if e.code == "model_not_allowed":
        return ModelNotAllowed(e.message, **extra)
    return AIFailed(e.message, ai_code=e.code, **extra)


# ── small helpers ───────────────────────────────────────────────────────────────────────────


def _sf():
    """The session factory, looked up at call time (tests swap ``src.db.session.async_session_factory``)."""
    from ..db import session as dbs

    return dbs.async_session_factory


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _jsonable(v: Any) -> Any:
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_jsonable(x) for x in v]
    return v


def _sha(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8", errors="surrogatepass")).hexdigest()


def _clip(v: Any, n: int = 300) -> Any:
    """A short, JSON-safe rendering of ``v`` for feedback payloads."""
    if isinstance(v, str):
        return v if len(v) <= n else v[:n] + "..."
    if isinstance(v, (int, float, bool)) or v is None:
        return v
    s = json.dumps(v, default=str, ensure_ascii=False)
    return v if len(s) <= n else s[:n] + "..."


# ── busy lock ((project, actor), in process; the module_generator pattern) ──────────────────

_BUSY: set[tuple[str, str]] = set()


class _Busy:
    def __init__(self, project_id: str, actor: str) -> None:
        self.key = (project_id, actor)

    def __enter__(self) -> "_Busy":
        if self.key in _BUSY:
            raise BuilderBusy("You already have a build running in this project. Wait for it to finish.")
        _BUSY.add(self.key)
        return self

    def __exit__(self, *exc: Any) -> None:
        _BUSY.discard(self.key)


# ── context: what the model may be told about the project ───────────────────────────────────


@dataclass
class Ctx:
    project_id: str
    team_id: str
    secrets: set[str]
    lookups: Any
    operations: dict[str, dict[str, dict[str, Any]]]
    api_ops: list[dict[str, Any]]
    catalog: list[dict[str, Any]]
    variable_names: list[str]
    existing_workflows: list[dict[str, Any]]
    egress: list[str]
    # Stage 8 (section 9): the project's callable sub-workflows, loaded by ``attach_subworkflows`` (only the drafting and building paths do)
    callable_subworkflows: list[dict[str, Any]] = field(default_factory=list)
    subworkflow_ids: set[str] | None = None            # None = not loaded: ExecuteWorkflow ids are not held to a list
    subworkflow_outputs: dict[str, Any] = field(default_factory=dict)   # child workflow id -> output Shape (for default mocks)
    # capability graph (CONTRACTS-CAPGRAPH.md 4.3). ``capability`` is the rendered ``capability_context`` (None = the graph is not in use).
    # The two virtual maps are the app-part seam: sibling parts that are not saved yet, visible to the checker as real children / tables.
    capability: dict[str, Any] | None = None
    virtual_children: dict[str, Any] = field(default_factory=dict)   # "@part/<key>" -> workflow_checker.ChildWorkflow
    virtual_tables: dict[str, list] = field(default_factory=dict)    # table name -> columns
    # the project's datasets as prompt entries (``ai_grounding.dataset_entries``); a node's ``datasetId`` holds an entry's ``id``
    datasets: list[dict[str, Any]] = field(default_factory=list)

    def host_ok(self, url: str) -> bool:
        """False for a literal http(s) URL whose host is outside the project egress allowlist (an empty allowlist allows all)."""
        from urllib.parse import urlsplit

        from ..broker.policy import host_matches

        if not self.egress or "{{" in url or not re.match(r"(?i)^https?://", url or ""):
            return True
        host = urlsplit(url).hostname or ""
        return host_matches(host, self.egress)


async def load_ctx(project_ref: str) -> Ctx:
    """Everything the prompts and the checks need, loaded once per request. 404 when the project does not exist."""
    from sqlalchemy import select

    from ..db.models import ProjectDatasetModel, WorkflowModel
    from ..repositories.api_spec_repository import ApiSpecRepository
    from .check_lookups import build_lookups
    from .project_service import effective_environment, resolve_project
    from .variable_loader import load_run_secrets

    async with _sf()() as s:
        project = await resolve_project(s, project_ref)
        if project is None:
            raise ProposalNotFound(f"Project not found: {project_ref}")
        team_id = project.team_id or "default"
        env = effective_environment(project, "default")
        lookups = await build_lookups(s, project.id, None, team_id=team_id)
        specs = [{"id": r.id, "name": r.name, "base_url": r.base_url} for r in await ApiSpecRepository(s).list(project.id)]
        rows = (await s.execute(select(WorkflowModel.id, WorkflowModel.name, WorkflowModel.draft_definition)
                                .where(WorkflowModel.project_id == project.id).order_by(WorkflowModel.updated_at.desc()).limit(60))).all()
        dataset_rows = (await s.execute(select(ProjectDatasetModel).where(ProjectDatasetModel.project_id == project.id)
                                        .order_by(ProjectDatasetModel.created_at.desc(), ProjectDatasetModel.id.desc()))).scalars().all()
        project_id = project.id
    # every real dataset may be named in a datasetId (link id, legacy analytics id or name, as the run resolves it); the prompt gets 25
    lookups.datasets = {str(v) for d in dataset_rows for v in (d.id, d.dataset_id, d.name) if v}
    secrets = set(await load_run_secrets(environment=env, team_id=team_id, project_id=project_id))
    existing = []
    for wid, name, d in rows:
        trig = next((n.get("type") for n in (d or {}).get("nodes") or [] if n.get("type") in _TRIGGERS), None)
        existing.append({"id": wid, "name": name, "trigger": trig})
    return Ctx(
        project_id=project_id, team_id=team_id, secrets=secrets, lookups=lookups, operations=lookups.api_specs,
        api_ops=ai_grounding.api_operations(lookups.api_specs, specs), catalog=ai_grounding.full_catalog(),
        variable_names=sorted(lookups.variables | lookups.secrets), existing_workflows=existing,
        egress=list(lookups.egress_allowlist or []), datasets=ai_grounding.dataset_entries(dataset_rows),
    )


def _base_calls(base: dict[str, Any] | None) -> list[str]:
    """Workflow ids that the nodes of ``base`` already call (an ExecuteWorkflow with a literal ``workflowId``)."""
    out: list[str] = []
    for n in (base or {}).get("nodes") or []:
        wid = (n.get("parameters") or {}).get("workflowId") if n.get("type") == "ExecuteWorkflow" else None
        if isinstance(wid, str) and wid and wid not in out:
            out.append(wid)
    return out


async def attach_subworkflows(ctx: Ctx, request: str, *, workflow_id: str | None = None, base: dict[str, Any] | None = None) -> None:
    """Ground the model in the project's callable sub-workflows (DESIGN-STAGE8-REUSE section 9.1): fills ``ctx.callable_subworkflows``
    (at most 20, ranked by similarity to ``request``, never the workflow being edited), ``ctx.subworkflow_ids`` (what the model may name
    in a ``workflowId``) and ``ctx.subworkflow_outputs`` (for typed default mocks). A failure leaves the context as it was: the build
    then simply has no sub-workflows to offer, and ids are not held to a list."""
    from ..db.models import ProjectModel
    from .subworkflow_api import SubworkflowApi

    try:
        async with _sf()() as s:
            project = await s.get(ProjectModel, ctx.project_id)
            if project is None:
                return
            api = SubworkflowApi(s)
            rows = await api.list_for_project(project, callable_only=True)
            already = _base_calls(base)
            entries = ai_grounding.subworkflow_entries(rows, request, exclude=[workflow_id] if workflow_id else [], keep=already)
            outputs: dict[str, Any] = {}
            for e in entries:
                try:
                    outputs[e["id"]] = (await api.workflow_io(e["id"], int(e["published_version"])))["output"]
                except Exception:  # noqa: BLE001 - one child's shape failing costs only its typed mock
                    logger.info("sub-workflow output shape unavailable for %s", e["id"], exc_info=True)
    except Exception:  # noqa: BLE001
        logger.warning("could not load the callable sub-workflows; the AI builder goes without them", exc_info=True)
        return
    ctx.callable_subworkflows = entries
    ctx.subworkflow_outputs = outputs
    # what may be named: the offered children, the ones already called, and any other workflow of the project (an unpublished
    # child is still a real workflow; the checker's pin rules judge it). What is refused is an id that is none of these.
    ctx.subworkflow_ids = {e["id"] for e in entries} | set(_base_calls(base)) | {w["id"] for w in ctx.existing_workflows if w.get("id")}


def apply_virtuals(ctx: Ctx) -> None:
    """Merge the app-part seam into what the checker and the id guard see (call after ``attach_subworkflows``, which resets the id set).
    Virtual children become ``lookups.children`` and known workflow ids, virtual tables become ``lookups.tables``. No-op without virtuals."""
    if not ctx.virtual_children and not ctx.virtual_tables:
        return
    lk = ctx.lookups
    if ctx.virtual_children:
        lk.children = {**getattr(lk, "children", {}), **ctx.virtual_children}
        lk.workflows = set(getattr(lk, "workflows", set())) | set(ctx.virtual_children)
        ctx.subworkflow_ids = (ctx.subworkflow_ids if ctx.subworkflow_ids is not None else set()) | set(ctx.virtual_children)
    if ctx.virtual_tables:
        lk.tables = {**getattr(lk, "tables", {}), **ctx.virtual_tables}


async def template_suggestion(ctx: Ctx, request: str) -> dict[str, Any] | None:
    """A catalog template that fits ``request`` (DESIGN-STAGE8-REUSE section 9.4), or None. Pure scoring in ``ai_grounding.match_template``;
    a template the project already has installed is not suggested again. Nothing is installed: the person accepts or declines."""
    from sqlalchemy import select

    from ..db.models import TemplateInstallModel
    from .template_bundle import get_catalog

    try:
        catalog = get_catalog()
        installed: set[str] = set()
        try:
            async with _sf()() as s:
                installed = set((await s.execute(select(TemplateInstallModel.template_slug)
                                                 .where(TemplateInstallModel.project_id == ctx.project_id))).scalars().all())
        except Exception:  # noqa: BLE001 - not knowing what is installed only means a suggestion may repeat
            logger.info("could not read the project's template installs", exc_info=True)
        return ai_grounding.match_template(request, {k: v for k, v in catalog.items() if k not in installed})
    except Exception:  # noqa: BLE001 - a suggestion is a courtesy; it never fails a draft
        logger.warning("template matching failed", exc_info=True)
        return None


# ── capability graph: Understand, wired into the loop (CONTRACTS-CAPGRAPH.md 4.1-4.3) ───────────────────────────────────────


def _open_questions(understanding: dict[str, Any] | None) -> list[dict[str, Any]]:
    from . import kg_understand

    return kg_understand.open_questions(understanding)


def _understood_payload(u: dict[str, Any]) -> dict[str, Any]:
    r = u.get("retrieval") or {}
    return {"concepts": [c["key"] for c in u.get("concepts", []) if c.get("confirmed")], "questions": len(u.get("questions") or []),
            "reuse": {"subworkflows": len(r.get("subworkflows", [])), "templates": len(r.get("templates", [])), "patterns": len(r.get("patterns", []))},
            "adapt_from": u.get("adapt_from")}


async def _installed_templates(project_id: str) -> set[str]:
    from sqlalchemy import select

    from ..db.models import TemplateInstallModel

    try:
        async with _sf()() as s:
            return set((await s.execute(select(TemplateInstallModel.template_slug).where(TemplateInstallModel.project_id == project_id))).scalars().all())
    except Exception:  # noqa: BLE001 - not knowing only means a template may be offered again
        return set()


async def _load_slice(ctx: Ctx):
    """The project's graph slice; an empty one when the graph cannot be read (Understand then runs on the reviewed seed file)."""
    from ..repositories.kg_repository import GraphSlice
    from . import kg_retrieve

    try:
        async with _sf()() as s:
            return await kg_retrieve.load_slice(s, ctx.project_id)
    except Exception:  # noqa: BLE001
        logger.warning("could not load the knowledge graph slice; Understand runs without it", exc_info=True)
        return GraphSlice(project_id=ctx.project_id, team_id=ctx.team_id, nodes={}, edges=[])


async def _ask_understand(payload: dict[str, Any], *, meter: Meter, model: str, ctx: Ctx) -> dict[str, Any]:
    """The one ``WF_AI:UNDERSTAND`` call. Not routed through ``wf_ai.ask``: its task table is frozen by a contract test, and a failed
    understand call is never fatal (the caller degrades to keyword matching), so there is no json_fix round either."""
    import asyncio

    from ..engine.test_ai import parse_json_object

    cfg = wf_ai.config()
    clean, _ = ai_policy.scrub_payload(payload, ctx.secrets)
    system, user = prompts.render_understand(clean)
    if meter.calls >= meter.budget():
        raise AIError("call_budget", f"The call budget ({meter.budget()}) is used up")
    if len(system) + len(user) > cfg.max_prompt_chars:
        raise AIError("prompt_too_large", "The understand prompt is too large")
    sha = wf_ai.prompt_sha256(model, system, user)
    event: dict[str, Any] = {"task": "understand", "system_line": prompts.UNDERSTAND_SYSTEM_LINE, "prompt_sha256": sha, "model": model,
                             "input_tokens": 0, "output_tokens": 0, "ms": 0, "cached": False, "outcome": "ok"}
    t0 = time.monotonic()
    meter.calls += 1
    try:
        reply = await asyncio.wait_for(wf_ai.make_adapter(model, cfg).ask_json(system, user, prompts.UNDERSTAND_SCHEMA, max_tokens=cfg.max_output_tokens,
                                                                               timeout_s=cfg.call_timeout_s), timeout=cfg.call_timeout_s)
        event["ms"] = int((time.monotonic() - t0) * 1000)
        meter.llm_ms += event["ms"]
        event["input_tokens"], event["output_tokens"] = meter.add_usage(reply.usage)
        obj = parse_json_object(reply.text)
        if not isinstance(obj.get("concepts"), list):
            raise ValueError("the reply has no concepts list")
        return obj
    except BaseException as e:
        event["outcome"] = "timeout" if isinstance(e, asyncio.TimeoutError) else "provider_failed"
        raise
    finally:
        meter.emit(event)


async def _understand(ctx: Ctx, request: str, *, answers: dict[str, Any] | None, use_llm: bool, meter: Meter, model: str,
                      prior: dict[str, Any] | None = None) -> dict[str, Any]:
    """``kg_understand.understand`` over this project's slice. Never raises: any failure is the empty understanding plus a warning."""
    import functools

    from . import kg_understand

    try:
        sl = await _load_slice(ctx)
        extras: dict[str, Any] = {"installed_templates": await _installed_templates(ctx.project_id)}
        if prior:
            extras["prior"] = prior
        ask = functools.partial(_ask_understand, meter=meter, model=model, ctx=ctx) if use_llm else None
        uctx = kg_understand.UnderstandContext(project_id=ctx.project_id, team_id=ctx.team_id, slice=sl, ask_model=ask, extras=extras)
        return await kg_understand.understand(uctx, request, answers=answers, use_llm=use_llm)
    except Exception:  # noqa: BLE001 - the graph only ever adds to a build; it never stops one
        logger.warning("understand failed; continuing without the knowledge graph", exc_info=True)
        u = kg_understand.empty_understanding()
        u["warnings"].append("The knowledge graph could not be used for this request.")
        return u


def _capability_section(understanding: dict[str, Any] | None, app_part: dict[str, Any] | None = None) -> dict[str, Any] | None:
    from . import kg_understand

    if not understanding:
        return None
    return kg_understand.capability_context(understanding, part=(app_part or {}).get("part"), siblings=(app_part or {}).get("siblings")) or None


async def _reference_workflow(project_id: str, understanding: dict[str, Any] | None, part_key: str | None = None) -> dict[str, Any] | None:
    """The adapt pattern's scrubbed reference definition (compacted), or None. Best effort. For an app part the workflow whose key matches
    ``part_key`` is preferred; otherwise (and for a single workflow) the first entry workflow."""
    adapt = (understanding or {}).get("adapt_from")
    if not adapt:
        return None
    from ..repositories.kg_repository import KgRepository

    try:
        async with _sf()() as s:
            sl = await KgRepository(s).load_slice(project_id)
        node = sl.by_key(adapt["pattern"])
        bundle = ((node or {}).get("props") or {}).get("reference_bundle") or {}
        wfs = [w for w in bundle.get("workflows") or [] if isinstance(w, dict) and isinstance(w.get("definition"), dict)]
        pick = next((w for w in wfs if part_key and w.get("key") == part_key), None) if part_key else None
        pick = pick or next((w for w in wfs if w.get("role") == "entry"), wfs[0] if wfs else None)
        return compact_definition(pick["definition"]) if pick else None
    except Exception:  # noqa: BLE001
        logger.info("could not load the reference workflow", exc_info=True)
        return None


def _triggers() -> frozenset[str]:
    from ..engine.graph import TRIGGER_NODE_TYPES

    return frozenset(TRIGGER_NODE_TYPES)


_TRIGGERS = _triggers()
_CODE_TYPES = ("Code", "PythonStep")

# ── compact forms for prompts ───────────────────────────────────────────────────────────────


def compact_definition(defn: dict[str, Any] | None) -> dict[str, Any] | None:
    """``CompactDefinition`` (5.3): nodes without positions, connections as ``[source, port, target, input]``, tests summarised.
    A Code body is untrusted text and travels under ``untrusted_code``."""
    if defn is None:
        return None
    nodes = []
    for n in defn.get("nodes") or []:
        params = copy.deepcopy(n.get("parameters") or {})
        item: dict[str, Any] = {"name": n.get("name"), "type": n.get("type")}
        if n.get("type") in _CODE_TYPES and isinstance(params.get("code"), str):
            item["untrusted_code"] = params.pop("code")
        item["parameters"] = params
        if n.get("label"):
            item["label"] = n["label"]
        if n.get("disabled"):
            item["disabled"] = True
        if n.get("on_error") not in (None, "stop"):
            item["on_error"] = n["on_error"]
        nodes.append(item)
    conns = [[c.get("source_node"), c.get("source_output", "main"), c.get("target_node"), c.get("target_input", "main")]
             for c in defn.get("connections") or []]
    tests = [{"id": t.get("id"), "name": t.get("name"), "status": t.get("status", "draft"), "locked": bool(t.get("locked"))}
             for t in defn.get("tests") or []]
    out = {"name": defn.get("name"), "nodes": nodes, "connections": conns, "tests": tests,
           "has_interface": bool(defn.get("interface")),
           "default_mocks_for": sorted(((defn.get("test_defaults") or {}).get("mocks") or {}).keys())}
    return out


def spec_for_model(spec: dict[str, Any]) -> dict[str, Any]:
    """The spec as the build prompts see it: no validation noise, tests without approval state (ids kept for change requests)."""
    out = {k: copy.deepcopy(v) for k, v in spec.items() if k not in ("issues", "warnings", "template_suggestion")}
    out["tests"] = [{k: v for k, v in t.items() if k not in ("status", "locked", "approved_by", "approved_at", "source")}
                    for t in spec.get("tests") or []]
    return out


def locked_tests_for_model(defn: dict[str, Any] | None, limit: int = 30) -> list[dict[str, Any]]:
    out = []
    for t in (defn or {}).get("tests") or []:
        if t.get("status") == "approved" or t.get("locked"):
            out.append({k: v for k, v in t.items() if k in ("id", "name", "description", "target", "input", "mocks", "expect", "covers")})
    return out[:limit]


# ── usage / events: every write commits, so a polling studio sees progress ─────────────────


async def add_events(proposal_id: str, events: list[tuple[str, dict[str, Any], str | None]], *, usage: dict[str, Any] | None = None,
                     fields: dict[str, Any] | None = None, only_in: list[str] | None = None) -> None:
    async with _sf()() as s:
        repo = AIProposalRepository(s)
        for ev, payload, actor in events:
            await repo.add_event(proposal_id, ev, payload, actor)
        if usage:
            await repo.add_usage(proposal_id, **usage)
        if fields:
            await repo.update_fields(proposal_id, fields, only_in=only_in)
        elif not usage:
            await repo.update_fields(proposal_id, {})  # touch updated_at: the stale reaper reads it as a heartbeat
        await s.commit()


def _price_cost(i: int, o: int) -> float | None:
    c = wf_ai.config()
    if c.price_in_per_mtok is None or c.price_out_per_mtok is None:
        return None
    return round(i / 1e6 * c.price_in_per_mtok + o / 1e6 * c.price_out_per_mtok, 5)


class Flusher:
    """Writes a ``Meter``'s new ``llm_call`` events and usage deltas to the proposal. Call after every model call."""

    def __init__(self, proposal_id: str, meter: Meter) -> None:
        self.pid, self.meter = proposal_id, meter
        self.events_done = 0
        self.calls_done, self.in_done, self.out_done = meter.calls, 0, 0

    async def flush(self) -> None:
        m = self.meter
        new = m.events[self.events_done:]
        dc, di, do = m.calls - self.calls_done, m.input_tokens - self.in_done, m.output_tokens - self.out_done
        if not new and not (dc or di or do):
            return
        self.events_done, self.calls_done, self.in_done, self.out_done = len(m.events), m.calls, m.input_tokens, m.output_tokens
        await add_events(self.pid, [("llm_call", ev, None) for ev in new],
                         usage={"llm_calls": dc, "input_tokens": di, "output_tokens": do, "cost_usd": _price_cost(di, do)})


async def ask_model(task: str, context: dict[str, Any], schema: dict[str, Any], *, meter: Meter, flusher: Flusher, ctx: Ctx,
                    model: str) -> dict[str, Any]:
    """Scrub the payload, render the prompt, ask, and flush the audit events. ``AIError`` propagates after the flush."""
    clean, _ = ai_policy.scrub_payload(context, ctx.secrets)
    system, user = prompts.render(task, clean)
    try:
        return await wf_ai.ask(task, system, user, schema, meter=meter, model=model)
    finally:
        await flusher.flush()


# ── views ───────────────────────────────────────────────────────────────────────────────────

_VIEW_KEYS = (
    "id", "project_id", "team_id", "workflow_id", "parent_id", "kind", "status", "actor", "request", "request_sha256", "spec", "base_definition",
    "spec_approved_by", "spec_approved_at", "spec_fingerprint", "base_draft_hash", "proposed_definition", "ops_log", "diff", "report",
    "test_change_requests", "answer", "error_code", "error_message", "kept_by", "kept_at", "kept_draft_hash", "accepted_failing",
    "understanding", "app_build_id", "part_key", "created_at", "updated_at",
)


def view(row: dict[str, Any]) -> dict[str, Any]:
    """``ProposalView`` (4.8): the public fields plus ``usage`` and ``policy``. Never prompts (only their hashes, in events)."""
    out = {k: row.get(k) for k in _VIEW_KEYS}
    out["usage"] = {"llm_calls": row.get("llm_calls", 0), "input_tokens": row.get("input_tokens", 0),
                    "output_tokens": row.get("output_tokens", 0), "cost_usd": row.get("cost_usd"), "duration_ms": row.get("duration_ms", 0)}
    out["policy"] = {"model": row.get("model"), "provider": row.get("provider"), "region": row.get("region")}
    return _jsonable(out)


def summary(row: dict[str, Any]) -> dict[str, Any]:
    keys = ("id", "kind", "status", "actor", "created_at", "model", "llm_calls", "cost_usd", "passed", "workflow_id")
    return _jsonable({k: row.get(k) for k in keys})


async def get_row(proposal_id: str, project_id: str | None = None) -> dict[str, Any]:
    async with _sf()() as s:
        row = await AIProposalRepository(s).get(proposal_id, project_id)
    if row is None:
        raise ProposalNotFound(f"Proposal not found: {proposal_id}")
    return row


async def get_view(proposal_id: str, project_id: str | None = None) -> dict[str, Any]:
    return view(await get_row(proposal_id, project_id))


async def list_proposals(project_id: str, *, workflow_id: str | None = None, status: str | None = None, limit: int = 50,
                         cursor: str | None = None) -> dict[str, Any]:
    from .project_service import resolve_project

    async with _sf()() as s:
        project = await resolve_project(s, project_id)
        if project is None:
            raise ProposalNotFound(f"Project not found: {project_id}")
        rows, nxt = await AIProposalRepository(s).list(project.id, workflow_id=workflow_id, status=status, limit=limit, cursor=cursor)
    return {"items": [summary(r) for r in rows], "next_cursor": nxt}


async def list_events(proposal_id: str, after: int = 0, project_id: str | None = None) -> dict[str, Any]:
    async with _sf()() as s:
        repo = AIProposalRepository(s)
        if await repo.get(proposal_id, project_id) is None:
            raise ProposalNotFound(f"Proposal not found: {proposal_id}")
        items, last = await repo.list_events(proposal_id, after)
    return {"items": [_jsonable({"seq": e["seq"], "at": e["at"], "event": e["event"], "payload": e["payload"], "actor": e["actor"]})
                      for e in items], "last_seq": last}


def config_view() -> dict[str, Any]:
    cfg = wf_ai.config()
    return {"model": cfg.model, "provider": ai_policy.provider_of(cfg.model),
            "configured": bool(llm_provider.is_configured(cfg.model)), "allowed_models": ai_policy.allowed_models(),
            "region": ai_policy.region(), "send_run_values": ai_policy.send_run_values(),
            "prices_configured": cfg.price_in_per_mtok is not None and cfg.price_out_per_mtok is not None, "budgets": cfg.budgets()}


async def api_refs(project_ref: str) -> dict[str, Any]:
    """``GET /api/ai/builder/api-refs``: the ``api_ref`` strings a spec step of this project may use, exactly as the grounding builds them
    (``ai_grounding.api_operations``), so the studio's picker never re-derives the naming rule. 404 for an unknown project."""
    ctx = await load_ctx(project_ref)
    items = [{"api_ref": o["api_ref"], "api": o.get("api"), "method": o.get("method"), "path": o.get("path"), "summary": o.get("untrusted_summary") or ""}
             for o in ctx.api_ops]
    return {"project_id": ctx.project_id, "api_refs": [i["api_ref"] for i in items], "items": items}


# ── policy ──────────────────────────────────────────────────────────────────────────────────


def check_model(model: str) -> None:
    ok, reason = ai_policy.allowed(model)
    if not ok:
        raise ModelNotAllowed(reason, model=model)
    if not llm_provider.is_configured(model):
        raise AINotConfigured("AI isn't set up on this engine.")


async def refuse(proposal_id: str, model: str, e: ProposalError) -> None:
    """Write ``policy_refused`` for a proposal whose model the policy no longer allows."""
    if isinstance(e, ModelNotAllowed):
        await add_events(proposal_id, [("policy_refused", {"model": model, "reason": e.message}, None)])


# ═══════════════════════════════════════════════════════════════════════════════════════════
# SPEC PHASE
# ═══════════════════════════════════════════════════════════════════════════════════════════


def _norm_spec_input(incoming: dict[str, Any], old: dict[str, Any] | None, base: dict[str, Any] | None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """A client-edited spec (3.1 shape, tests as full TestCases) -> the stored form. Returns ``(spec, parse_issues)``."""
    from pydantic import ValidationError as PydanticValidationError

    from ..schemas.workflow import normalize_tests

    def s(v: Any) -> str:
        return v.strip() if isinstance(v, str) else ""

    old_tests = {t["id"]: t for t in (old or {}).get("tests") or [] if isinstance(t, dict) and t.get("id")}
    taken = {t["id"] for t in (base or {}).get("tests") or [] if isinstance(t, dict) and t.get("id")}
    seen: set[str] = set()
    issues: list[dict[str, Any]] = []
    tests: list[dict[str, Any]] = []
    for i, raw in enumerate(incoming.get("tests") or []):
        if not isinstance(raw, dict):
            issues.append(ai_spec.issue(f"tests[{i}]", "SPEC005", "A test must be an object; it was dropped."))
            continue
        t = copy.deepcopy(raw)
        for k in ("approved_by", "approved_at"):
            t.pop(k, None)
        tid = t.get("id")
        if not isinstance(tid, str) or not re.match(r"^t_[0-9a-f]{8}$", tid) or tid in seen or tid in taken:
            t["id"] = ai_spec.new_test_id(seen | taken | set(old_tests))
        seen.add(t["id"])
        t["status"], t["locked"] = "draft", False
        known_source = (old_tests.get(t["id"]) or {}).get("source")
        t["source"] = known_source if known_source else (t["source"] if t.get("source") in ("ai", "human", "suggested", "replay", "fuzz") else "human")
        try:
            tests.append(normalize_tests([t])[0])
        except PydanticValidationError as e:
            err = e.errors()[0]
            issues.append(ai_spec.issue(f"tests[{i}]", "SPEC005", f"Invalid test: {err['msg']} ({'.'.join(str(p) for p in err['loc'])})."))
    ids = {t["id"] for t in tests}
    rule_tests: dict[str, list[str]] = {}
    for rid, tids in (incoming.get("rule_tests") or {}).items() if isinstance(incoming.get("rule_tests"), dict) else []:
        rule_tests[str(rid)] = [str(t) for t in (tids if isinstance(tids, list) else []) if str(t) in ids or str(t) in taken]
    # a test the client renumbered keeps its place: rule_tests that named a dropped / re-id'd test just lose it
    spec = {
        "spec_version": ai_spec.SPEC_VERSION,
        "title": s(incoming.get("title")) or s((old or {}).get("title")) or "Untitled workflow",
        "summary": s(incoming.get("summary")),
        "trigger": ai_spec._norm_trigger(incoming.get("trigger")),
        "rules": ai_spec._norm_rules(incoming.get("rules")),
        "steps": ai_spec._norm_steps(incoming.get("steps")),
        "interface": ai_spec._norm_interface(incoming.get("interface")),
        "tests": tests,
        "rule_tests": rule_tests,
        "assumptions": [str(a) for a in incoming.get("assumptions") or [] if isinstance(a, str) and a.strip()],
        "questions": [str(q) for q in incoming.get("questions") or [] if isinstance(q, str) and q.strip()],
    }
    return spec, issues


def _base_trigger_kind(base: dict[str, Any] | None) -> str | None:
    inv = {v: k for k, v in ai_grounding.TRIGGER_TYPE_BY_KIND.items()}
    for n in (base or {}).get("nodes") or []:
        if n.get("type") in inv and not n.get("disabled"):
            return inv[n["type"]]
    return None


def validate(spec: dict[str, Any], ctx: Ctx, base: dict[str, Any] | None, understanding: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """``ai_spec.validate_spec`` in the right mode: with a base workflow it is a change spec (``mode="modify"``), which lists only the new
    or changed steps and so need not name the trigger."""
    existing_ids = [t["id"] for t in (base or {}).get("tests") or [] if isinstance(t, dict) and t.get("id")]
    return ai_spec.validate_spec(spec, ctx.catalog, ctx.api_ops, base_definition=base, existing_test_ids=existing_ids,
                                 mode="modify" if base is not None else "new", understanding=understanding)


def _stamp(spec: dict[str, Any], issues: list[dict[str, Any]], warnings: list[str]) -> dict[str, Any]:
    out = dict(spec)
    out["issues"] = issues
    out["warnings"] = warnings
    return out


def _scrub_warnings(counts: dict[str, Any]) -> list[str]:
    labels = {"email": ("email address", "email addresses"), "phone": ("phone number", "phone numbers"),
              "ssn": ("social security number", "social security numbers"), "card": ("card number", "card numbers"),
              "iban": ("IBAN", "IBANs"), "account": ("account number", "account numbers")}
    out = []
    for kind, (one, many) in labels.items():
        n = (counts.get("pii") or {}).get(kind, 0)
        if n:
            out.append(f"{n} {one if n == 1 else many} {'was' if n == 1 else 'were'} removed from your request.")
    n = counts.get("secrets", 0)
    if n:
        out.append(f"{n} secret value{'' if n == 1 else 's'} {'was' if n == 1 else 'were'} removed from your request.")
    return out


async def draft_spec(project_ref: str, request: str, actor: str, *, workflow_id: str | None = None, model: str | None = None,
                     use_graph: bool = False, clarify: bool = False, answers: dict[str, Any] | None = None,
                     app_part: dict[str, Any] | None = None) -> dict[str, Any]:
    """``POST /api/ai/builder/spec``: one or two model calls (``WF_AI:SPEC``, then ``WF_AI:SPEC_REPAIR`` for remaining errors).

    Returns the new ``spec_draft`` proposal's view. A failed call leaves the proposal ``failed`` and raises 502/503/403.

    Capability graph (both flags default to False, and then nothing below differs from before): ``use_graph`` runs Understand (keyword
    matching and the graph, no model call) and grounds the spec prompt in what it found. ``clarify`` also lets Understand make its one
    ``WF_AI:UNDERSTAND`` call and, when a required slot is still empty, stops with a ``clarify`` proposal and its questions instead of
    drafting a spec; ``answer_questions`` continues it. ``answers`` pre-answer questions (by question id). ``app_part`` is the seam an
    app composer uses: ``{app_build_id, part_key, part, siblings, virtual_children, virtual_tables}``.
    """
    actor = clean_actor(actor)
    request = request or ""
    if not request.strip():
        raise RequestInvalid("Describe what the workflow should do.")
    if len(request) > MAX_REQUEST_CHARS:
        raise RequestInvalid(f"The request is longer than {MAX_REQUEST_CHARS} characters.")
    cfg = wf_ai.config()
    model = model or cfg.model
    check_model(model)
    ctx = await load_ctx(project_ref)
    if app_part:
        ctx.virtual_children = dict(app_part.get("virtual_children") or {})
        ctx.virtual_tables = dict(app_part.get("virtual_tables") or {})

    base, base_hash = None, None
    if workflow_id:
        from ..repositories.workflow_repository import WorkflowRepository

        async with _sf()() as s:
            stored = await WorkflowRepository(s).get(workflow_id)
            if stored is None or stored.project_id != ctx.project_id:
                raise ProposalNotFound(f"Workflow not found: {workflow_id}")
            base, base_hash = sb.draft_of(stored)

    clean_request, counts = ai_policy.scrub_request(request, ctx.secrets)
    await attach_subworkflows(ctx, clean_request, workflow_id=workflow_id, base=base)
    apply_virtuals(ctx)
    suggestion = None if workflow_id else await template_suggestion(ctx, clean_request)
    understanding, meter = None, None
    if use_graph:
        meter = Meter(max_calls=cfg.max_calls, case=ctx.project_id, secret_values=ctx.secrets)
        understanding = await _understand(ctx, clean_request, answers=answers, use_llm=clarify, meter=meter, model=model)
        if suggestion and any(t["key"] == f"tpl:{suggestion['slug']}" for t in understanding["retrieval"]["templates"]):
            suggestion = None   # the understanding card already lists it; no second card
        ctx.capability = _capability_section(understanding, app_part)
    waiting = bool(use_graph and clarify and _open_questions(understanding))
    pid = new_proposal_id()
    async with _sf()() as s:
        repo = AIProposalRepository(s)
        row = {
            "id": pid, "project_id": ctx.project_id, "team_id": ctx.team_id, "workflow_id": workflow_id,
            "kind": "modify" if workflow_id else "build", "status": "clarify" if waiting else "spec_draft", "actor": actor, "request": clean_request,
            "request_sha256": _sha(request), "base_definition": base, "base_draft_hash": base_hash,
            "model": model, "provider": ai_policy.provider_of(model), "region": ai_policy.region(),
        }
        if understanding is not None:
            row["understanding"] = understanding
        if app_part:
            row.update({k: app_part[k] for k in ("app_build_id", "part_key") if app_part.get(k)})
        await repo.insert(row)
        await repo.add_event(pid, "spec_requested", {"request_sha256": _sha(request), "model": model}, actor)
        if counts["secrets"] or counts["pii_total"]:
            await repo.add_event(pid, "scrubbed", {"secrets": counts["secrets"], "pii": counts["pii"]})
        if understanding is not None:
            await repo.add_event(pid, "understood", _understood_payload(understanding))
        await s.commit()

    t0 = time.monotonic()
    if meter is None:
        meter = Meter(max_calls=cfg.max_calls, case=ctx.project_id, secret_values=ctx.secrets)
    flusher = Flusher(pid, meter)
    if waiting:   # the understand call (if any) is on the meter already; record it and stop: nothing is drafted until the questions are answered
        await flusher.flush()
        async with _sf()() as s:
            await AIProposalRepository(s).add_usage(pid, duration_ms=int((time.monotonic() - t0) * 1000))
            await s.commit()
        return await get_view(pid)
    await flusher.flush()
    return await _spec_phase(pid, ctx, clean_request, base, workflow_id, model, counts, suggestion, meter, flusher, t0, use_graph=use_graph,
                             understanding=understanding)


async def _spec_phase(pid: str, ctx: Ctx, clean_request: str, base: dict[str, Any] | None, workflow_id: str | None, model: str, counts: dict[str, Any],
                      suggestion: dict[str, Any] | None, meter: Meter, flusher: Flusher, t0: float, *, use_graph: bool = False,
                      understanding: dict[str, Any] | None = None) -> dict[str, Any]:
    """From the first ``WF_AI:SPEC`` call to the stored ``spec_draft`` (the second half of ``draft_spec``; ``answer_questions`` joins here)."""
    try:
        reply = await ask_model("spec", {
            "untrusted_request": clean_request, "mode": "modify" if workflow_id else "new",
            "current_workflow": compact_definition(base), "locked_tests": locked_tests_for_model(base),
            "catalog": ai_grounding.catalog_subset(None, ctx.catalog, extra_types=_full_types(ctx, clean_request)), "api_operations": ctx.api_ops,
            "existing_workflows": [w for w in ctx.existing_workflows if w["id"] != workflow_id],
            "variable_names": ctx.variable_names, **_sub_section(ctx), **_cap_section(ctx), **_ds_section(ctx),
        }, prompts.SPEC_SCHEMA, meter=meter, flusher=flusher, ctx=ctx, model=model)
    except AIError as e:
        await _fail(pid, e, "spec_draft")
        raise ai_error_to_http(e, proposal_id=pid) from None

    taken = {t["id"] for t in (base or {}).get("tests") or [] if isinstance(t, dict) and t.get("id")}
    spec, issues = ai_spec.parse_spec(reply, taken_test_ids=taken, base_definition=base)
    _fix_modify_trigger(spec, base)
    issues = issues + validate(spec, ctx, base, understanding)
    if ai_spec.errors_of(issues) or any(i.get("code") == "SPEC009" for i in issues) or (use_graph and any(i.get("code") in ("SPEC010", "SPEC011") for i in issues)):
        try:
            reply2 = await ask_model("spec_repair", {
                "spec": prompts.spec_to_model_output(spec), "issues": issues,
                "catalog": ai_grounding.catalog_subset(spec, ctx.catalog, extra_types=_full_types(ctx, clean_request)),
                "api_operations": ctx.api_ops, **_sub_section(ctx), **_cap_section(ctx), **_ds_section(ctx),
            }, prompts.SPEC_SCHEMA, meter=meter, flusher=flusher, ctx=ctx, model=model)
        except AIError as e:
            logger.info("spec repair call failed (%s); keeping the first spec with its issues", e.code)
        else:
            spec2, issues2 = ai_spec.parse_spec(reply2, taken_test_ids=taken, base_definition=base)
            _fix_modify_trigger(spec2, base)
            spec, issues = spec2, issues2 + validate(spec2, ctx, base, understanding)
    spec = _stamp(spec, issues, _scrub_warnings(counts))
    if suggestion:   # beside the spec, never in what the model sees (``spec_for_model`` drops it)
        spec["template_suggestion"] = suggestion
    async with _sf()() as s:
        repo = AIProposalRepository(s)
        await repo.update_fields(pid, {"spec": spec}, only_in=["spec_draft"])
        await repo.add_event(pid, "spec_drafted", {"rules": len(spec["rules"]), "steps": len(spec["steps"]), "tests": len(spec["tests"])})
        if suggestion:
            await repo.add_event(pid, "template_suggested", {"slug": suggestion["slug"], "score": suggestion["score"]})
        await repo.add_usage(pid, duration_ms=int((time.monotonic() - t0) * 1000))
        await s.commit()
    return await get_view(pid)


def _sub_types(ctx: Ctx) -> tuple[str, ...]:
    """``ExecuteWorkflow`` is sent in full whenever there is a sub-workflow to call (section 9.1)."""
    return ("ExecuteWorkflow",) if ctx.callable_subworkflows else ()


def _full_types(ctx: Ctx, request: str | None) -> set[str]:
    """``_sub_types`` plus the dataset readers (the project has datasets) and the report chain (the request asks for a report)."""
    return set(_sub_types(ctx)) | ai_grounding.request_types(request, bool(ctx.datasets))


def _ds_section(ctx: Ctx) -> dict[str, Any]:
    """The optional ``datasets`` prompt section; nothing at all when the project has none."""
    return {"datasets": ctx.datasets} if ctx.datasets else {}


def _sub_section(ctx: Ctx) -> dict[str, Any]:
    """The optional ``callable_subworkflows`` prompt section; nothing at all (not an empty list) when there are none."""
    return {"callable_subworkflows": ctx.callable_subworkflows} if ctx.callable_subworkflows else {}


def _cap_section(ctx: Ctx) -> dict[str, Any]:
    """The optional ``capability_context`` prompt section; nothing at all when the graph is not in use or understood nothing."""
    return {"capability_context": ctx.capability} if ctx.capability else {}


def _fix_modify_trigger(spec: dict[str, Any], base: dict[str, Any] | None) -> None:
    if base is None:
        return
    kind = _base_trigger_kind(base)
    if kind and (spec.get("trigger") or {}).get("kind") not in ai_grounding.TRIGGER_TYPE_BY_KIND:
        spec["trigger"] = {"kind": kind, "fields": (spec.get("trigger") or {}).get("fields") or []}


async def _fail(pid: str, e: AIError, from_status: str) -> None:
    async with _sf()() as s:
        repo = AIProposalRepository(s)
        await repo.transition(pid, [from_status], "failed", {"error_code": e.code, "error_message": e.message})
        await s.commit()


async def answer_questions(proposal_id: str, answers: dict[str, Any], actor: str, project_id: str | None = None) -> dict[str, Any]:
    """``POST /api/ai/proposals/{id}/answers``: answer the questions of a ``clarify`` proposal and continue to the spec phase.

    ``answers`` is ``{question id: value}`` (an id with or without its ``q_`` prefix). Every required question needs an answer; each answer
    is validated against its slot type (422 ``answer_invalid`` with ``qid``). Understand re-runs with the answers and no second model call
    (what the first run's model call found is carried over), the proposal moves to ``spec_draft`` and the spec is drafted with the
    confirmed slots in ``capability_context``."""
    from . import kg_slots, kg_understand

    actor = clean_actor(actor)
    row = await get_row(proposal_id, project_id)
    if row["status"] != "clarify":
        raise BadState(row["status"], "This proposal is not waiting for answers.")
    model = row["model"]
    try:
        check_model(model)
    except ProposalError as e:
        await refuse(proposal_id, model, e)
        raise
    ctx = await load_ctx(row["project_id"])
    prev = row.get("understanding") or {}
    try:
        clean = kg_understand.check_answers(prev, answers or {}, await _load_slice(ctx))
    except kg_slots.AnswerInvalid as e:
        raise AnswerInvalid(getattr(e, "qid", ""), str(e)) from None
    merged = {q["id"]: q["answer"] for q in prev.get("questions") or [] if q.get("answer") is not None} | clean
    request, base, workflow_id = row["request"], row.get("base_definition"), row.get("workflow_id")
    await attach_subworkflows(ctx, request, workflow_id=workflow_id, base=base)
    cfg = wf_ai.config()
    meter = Meter(max_calls=cfg.max_calls, case=ctx.project_id, secret_values=ctx.secrets)
    meter.calls = int(row.get("llm_calls") or 0)
    understanding = await _understand(ctx, request, answers=merged, use_llm=False, meter=meter, model=model, prior=prev)
    ctx.capability = _capability_section(understanding)
    async with _sf()() as s:
        repo = AIProposalRepository(s)
        won = await repo.transition(proposal_id, ["clarify"], "spec_draft", {"understanding": understanding})
        if won is None:
            await s.rollback()
            raise BadState((await repo.get(proposal_id))["status"], "This proposal is not waiting for answers.")
        await repo.add_event(proposal_id, "answered", {"actor": actor, "qids": sorted(clean)}, actor)
        await repo.add_event(proposal_id, "understood", _understood_payload(understanding))
        await s.commit()
    suggestion = None if workflow_id else await template_suggestion(ctx, request)
    if suggestion and any(t["key"] == f"tpl:{suggestion['slug']}" for t in understanding["retrieval"]["templates"]):
        suggestion = None
    counts = {"secrets": 0, "pii": {}, "pii_total": 0}   # the request was scrubbed when the proposal was created
    return await _spec_phase(proposal_id, ctx, request, base, workflow_id, model, counts, suggestion, meter, Flusher(proposal_id, meter),
                             time.monotonic(), use_graph=True, understanding=understanding)


async def put_spec(proposal_id: str, spec_in: dict[str, Any], actor: str, project_id: str | None = None) -> dict[str, Any]:
    """``PUT /api/ai/proposals/{id}/spec``: store the user's edits, revalidate, no model call. Only while ``spec_draft``."""
    actor = clean_actor(actor)
    row = await get_row(proposal_id, project_id)
    if row["status"] != "spec_draft":
        raise BadState(row["status"], "The spec can only be edited before it is approved.")
    ctx = await load_ctx(row["project_id"])
    base = row.get("base_definition")
    old = row.get("spec") or {}
    spec, parse_issues = _norm_spec_input(spec_in, old, base)
    spec, counts = _scrub_spec_edit(spec, ctx.secrets)
    _fix_modify_trigger(spec, base)
    issues = parse_issues + validate(spec, ctx, base)
    changed = [k for k in ("rules", "steps", "tests", "interface", "trigger") if _canon(old.get(k)) != _canon(spec.get(k))]
    warnings = list(old.get("warnings") or [])
    warnings += [w.replace("your request", "your spec edit") for w in _scrub_warnings(counts) if w.replace("your request", "your spec edit") not in warnings]
    new = _stamp(spec, issues, warnings)
    if old.get("template_suggestion"):
        new["template_suggestion"] = old["template_suggestion"]
    async with _sf()() as s:
        repo = AIProposalRepository(s)
        won = await repo.update_fields(proposal_id, {"spec": new}, only_in=["spec_draft"])
        if won is None:
            await s.rollback()
            raise BadState((await repo.get(proposal_id))["status"], "The spec can only be edited before it is approved.")
        await repo.add_event(proposal_id, "spec_edited", {"actor": actor, "changed": changed}, actor)
        if counts["secrets"] or counts["pii_total"]:
            await repo.add_event(proposal_id, "scrubbed", {"secrets": counts["secrets"], "pii": counts["pii"], "source": "spec_edit"})
        await s.commit()
    return await get_view(proposal_id)


def _scrub_spec_edit(spec: dict[str, Any], secrets: set[str]) -> tuple[dict[str, Any], dict[str, Any]]:
    """The person's spec edit gets the same scrub as the request (DESIGN 10.3): secrets everywhere, PII in the prose a person types
    (title, summary, rule text, step purpose, assumptions, questions, test names). Test data (inputs, mocks, assertion values) keeps its
    PII-shaped values untouched, because rewriting them would silently change what an approved test proves."""
    clean, n_secrets = ai_policy.scrub_secrets(copy.deepcopy(spec), secrets)
    pii: dict[str, int] = {k: 0 for k in ai_policy.PII_KINDS}

    def prose(text: Any) -> Any:
        if not isinstance(text, str):
            return text
        out, c = ai_policy.scrub_pii(text)
        for k, v in c.items():
            pii[k] = pii.get(k, 0) + v
        return out

    for key in ("title", "summary"):
        clean[key] = prose(clean.get(key))
    for key in ("assumptions", "questions"):
        clean[key] = [prose(x) for x in clean.get(key) or []]
    for r in clean.get("rules") or []:
        if isinstance(r, dict):
            r["text"] = prose(r.get("text"))
    for st in clean.get("steps") or []:
        if isinstance(st, dict):
            st["purpose"] = prose(st.get("purpose"))
    for t in clean.get("tests") or []:
        if isinstance(t, dict):
            for key in ("name", "description"):
                if key in t:
                    t[key] = prose(t.get(key))
    return clean, {"secrets": n_secrets, "pii": pii, "pii_total": sum(pii.values())}


def _canon(v: Any) -> str:
    return sb.canonical_json(v)


async def approve_spec(proposal_id: str, actor: str, project_id: str | None = None) -> dict[str, Any]:
    """``POST /api/ai/proposals/{id}/spec/approve``: refused (422 ``spec_invalid``) while errors remain. Tests become approved + locked."""
    actor = clean_actor(actor)
    row = await get_row(proposal_id, project_id)
    if row["status"] == "spec_approved":
        return view(row)
    if row["status"] != "spec_draft" or not row.get("spec"):
        raise BadState(row["status"], "Only a spec that is still a draft can be approved.")
    ctx = await load_ctx(row["project_id"])
    spec = {k: v for k, v in row["spec"].items() if k not in ("issues", "warnings", "template_suggestion")}
    base = row.get("base_definition")
    issues = validate(spec, ctx, base)
    errors = ai_spec.errors_of(issues)
    if errors:
        raise SpecInvalid(issues)
    now = _now()
    approved = ai_spec.approve_spec_tests(spec, actor, now.isoformat())
    fp = sb.fingerprint(approved["tests"])
    stored = _stamp(approved, issues, (row["spec"].get("warnings") or []))
    async with _sf()() as s:
        repo = AIProposalRepository(s)
        won = await repo.transition(proposal_id, ["spec_draft"], "spec_approved", {
            "spec": stored, "spec_approved_by": actor, "spec_approved_at": now, "spec_fingerprint": fp})
        if won is None:
            await s.rollback()
            raise BadState((await repo.get(proposal_id))["status"])
        await repo.add_event(proposal_id, "spec_approved", {"actor": actor, "fingerprint": fp, "test_ids": [t["id"] for t in approved["tests"]]}, actor)
        await s.commit()
    return await get_view(proposal_id)


# ═══════════════════════════════════════════════════════════════════════════════════════════
# OPS: parse, allowlist (6.4), apply
# ═══════════════════════════════════════════════════════════════════════════════════════════

_NODE_KEYS = ("name", "type", "parameters", "label", "position", "disabled", "on_error", "retry_on_fail", "retry_delay", "continue_on_fail")
_ALIASES = {
    "connect": {"source": ("source_node", "from", "source_name", "from_node", "src"), "target": ("target_node", "to", "target_name", "to_node", "dst"),
                "source_output": ("port", "output", "source_port", "outputPort", "sourceOutput", "from_port"),
                "target_input": ("input", "target_port", "targetInput", "to_port")},
    "set_parameter": {"node": ("node_name", "name"), "path": ("parameter", "key", "param", "field", "param_path"), "value": ("val",)},
    "set_parameters": {"node": ("node_name", "name")},
    "set_node_field": {"node": ("node_name", "name")},
    "set_test_default_mock": {"node": ("node_name", "name")},
    "rename_node": {"old": ("old_name", "from", "name"), "new": ("new_name", "to")},
    "remove_node": {"name": ("node", "node_name")},
}
_ALIASES["disconnect"] = _ALIASES["connect"]


def _json_arg(v: Any) -> Any:
    """Models sometimes send an object as a JSON string inside the JSON string."""
    if isinstance(v, str) and v.strip()[:1] in ("{", "["):
        try:
            return json.loads(v)
        except ValueError:
            return v
    return v


def normalise_args(op: str, args: dict[str, Any]) -> dict[str, Any]:
    args = dict(args)
    for wrapper in ("args", "arguments"):
        if set(args) == {wrapper} and isinstance(args[wrapper], dict):
            args = dict(args[wrapper])
    if op == "add_node":
        if isinstance(args.get("node"), dict):
            args = dict(args["node"])
        for src, dst in (("node_type", "type"), ("nodeType", "type"), ("node_name", "name"), ("params", "parameters"), ("config", "parameters")):
            if dst not in args and src in args:
                args[dst] = args.pop(src)
        if "parameters" in args:
            args["parameters"] = _json_arg(args["parameters"])
        if isinstance(args.get("parameters"), dict) is False and "parameters" in args:
            args.pop("parameters")
        return {k: v for k, v in args.items() if k in _NODE_KEYS}
    for canon, alts in _ALIASES.get(op, {}).items():
        if canon not in args:
            for a in alts:
                if a in args:
                    args[canon] = args.pop(a)
                    break
    if op in ("set_parameters", "set_test_default_mock", "set_interface", "add_test"):
        for k in ("parameters", "mock", "interface", "test"):
            if k in args:
                args[k] = _json_arg(args[k])
    if op == "set_parameter" and "value" in args:
        args["value"] = _json_arg(args["value"])
    return args


@dataclass
class ParsedOp:
    index: int
    op: str
    args: dict[str, Any]
    why: str = ""


def parse_ops(reply: dict[str, Any], max_ops: int) -> tuple[list[ParsedOp], list[dict[str, Any]]]:
    """``(ops, op_errors)``: a bad ``args_json`` fails that one op with ``bad_args_json``; ops past ``max_ops`` are refused."""
    from ..engine.test_ai import TestAIError, parse_json_object

    ops: list[ParsedOp] = []
    errors: list[dict[str, Any]] = []
    raw_ops = reply.get("ops") if isinstance(reply.get("ops"), list) else []
    for i, o in enumerate(raw_ops):
        name = o.get("op") if isinstance(o, dict) else None
        if i >= max_ops:
            errors.append({"op_index": i, "op": name, "message": f"Too many ops in one round (limit {max_ops}); this one was skipped."})
            continue
        if not isinstance(o, dict) or not isinstance(name, str):
            errors.append({"op_index": i, "op": name, "message": "An op must be an object {op, args_json}."})
            continue
        raw = o.get("args_json", o.get("args"))
        if isinstance(raw, dict):
            args = raw
        elif raw is None or (isinstance(raw, str) and not raw.strip()):
            args = {}
        else:
            try:
                args = parse_json_object(raw if isinstance(raw, str) else json.dumps(raw))
            except TestAIError:
                errors.append({"op_index": i, "op": name, "message": "bad_args_json: args_json must be a JSON object serialised as a string."})
                continue
        ops.append(ParsedOp(i, name, normalise_args(name, args), str(o.get("why") or "")[:300]))
    return ops, errors


def _close_type(t: Any, spec: dict[str, Any] | None, catalog_types: list[str]) -> str | None:
    if not isinstance(t, str):
        return None
    pref = [s["node_type"] for s in (spec or {}).get("steps") or [] if isinstance(s, dict) and s.get("node_type")]
    return ai_repairs._close(t, pref) or ai_repairs._close(t, catalog_types)


@dataclass
class LoopState:
    """What the allowlist needs to know while a round's ops are applied."""

    ctx: Ctx
    spec: dict[str, Any] | None
    max_nodes: int
    renames: dict[str, str] = field(default_factory=dict)
    auto_mocks: dict[str, Any] = field(default_factory=dict)
    protected_extra: set[str] = field(default_factory=set)
    readded: dict[str, int] = field(default_factory=dict)   # name -> index of the LAST add_node of it this round (a protected node may be swapped)


def _url_params(op: str, args: dict[str, Any], defn: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    if op == "add_node" and args.get("type") == "HttpRequest":
        u = (args.get("parameters") or {}).get("url")
        if isinstance(u, str):
            urls.append(u)
    elif op == "set_parameter" and args.get("path") == "url" and isinstance(args.get("value"), str):
        node = next((n for n in defn.get("nodes") or [] if n.get("name") == args.get("node")), None)
        if node and node.get("type") == "HttpRequest":
            urls.append(args["value"])
    elif op == "set_parameters" and isinstance((args.get("parameters") or {}).get("url"), str):
        node = next((n for n in defn.get("nodes") or [] if n.get("name") == args.get("node")), None)
        if node and node.get("type") == "HttpRequest":
            urls.append(args["parameters"]["url"])
    return urls


def enforce_allowlist(op: ParsedOp, defn: dict[str, Any], st: LoopState) -> str | None:
    """Section 6.4: a message when the op must be refused (it becomes feedback), else None. May fix ``add_node`` types (FIX01)."""
    if op.op not in prompts.ALLOWED_OPS:
        if op.op in ("update_test", "remove_test"):
            return "Approved tests are fixed: no op can change or remove a test. Use test_change_requests to ask a person."
        return f"Op '{op.op}' is not allowed. Allowed ops: {', '.join(prompts.ALLOWED_OPS)}."
    args = op.args
    protected = ai_repairs.approved_node_names(defn) | st.protected_extra
    if op.op == "add_test":
        t = args.get("test")
        if not isinstance(t, dict):
            return "add_test needs {test: <TestCase>}."
        if t.get("status") == "approved" or t.get("locked") is True:
            return "A new test joins as a draft; only a person approves tests."
    if op.op in ("remove_node", "rename_node"):
        target = args.get("name") if op.op == "remove_node" else args.get("old")
        if target in protected and not (op.op == "remove_node" and st.readded.get(target, -1) > op.index):
            return (f"'{target}' is named by an approved test, so it can't be {'removed' if op.op == 'remove_node' else 'renamed'}. "
                    "Approved tests are fixed; if the test is wrong, file a test_change_request.")
    if op.op == "set_test_default_mock" and args.get("node") in {k for t in defn.get("tests") or [] if t.get("status") == "approved" or t.get("locked")
                                                                  for k in (t.get("mocks") or {})}:
        return f"An approved test already mocks '{args.get('node')}' itself; its mock wins, so the default mock is not changed."
    if op.op == "add_node":
        if len(defn.get("nodes") or []) >= st.max_nodes:
            return f"The workflow already has {st.max_nodes} nodes, the limit."
        types = [e["type"] for e in st.ctx.catalog if e.get("type")]
        t = args.get("type")
        if t not in types:
            fixed = _close_type(t, st.spec, types)
            if fixed is None:
                return f"Unknown node type '{t}'. Use a type from the catalog."
            args["type"] = fixed
            op.why = (op.why + f" [type '{t}' read as '{fixed}']").strip()
    if st.ctx.subworkflow_ids is not None:
        refused = workflow_toolkit.unknown_subworkflow(op.op, args, defn, st.ctx.subworkflow_ids)
        if refused:
            return refused
    if ai_policy.find_secret(args, st.ctx.secrets):
        return "The op contains something that looks like a credential, token or key. Refer to credentials and secrets by name (for example {{ $vars.NAME }})."
    for url in _url_params(op.op, args, defn):
        if not st.ctx.host_ok(url):
            return ("The URL host is not on the project's egress allowlist. Use an operation from api_operations (api_ref) "
                    "instead of typing a URL.")
    return None


def _scrub_args(args: Any, secrets: set[str]) -> Any:
    clean, _ = ai_policy.scrub_secrets(copy.deepcopy(args), secrets)
    return clean


_NAME_ARGS = {"set_parameter": ("node",), "set_parameters": ("node",), "set_node_field": ("node",), "set_test_default_mock": ("node",),
              "connect": ("source", "target"), "disconnect": ("source", "target"), "remove_node": ("name",), "rename_node": ("old",)}


def resolve_op_names(op: ParsedOp, defn: dict[str, Any]) -> None:
    """A node named in an op that differs from a real node only by case, spacing or a typo is read as that node (when exactly one fits)."""
    names = [n["name"] for n in defn.get("nodes") or [] if n.get("name")]
    for key in _NAME_ARGS.get(op.op, ()):
        v = op.args.get(key)
        if not isinstance(v, str) or v in names:
            continue
        hits = [n for n in names if re.sub(r"\s+", " ", n.strip().lower()) == re.sub(r"\s+", " ", v.strip().lower())]
        if not hits:
            hits = difflib.get_close_matches(v, names, n=2, cutoff=0.85)
            if len(hits) == 2 and difflib.SequenceMatcher(None, v, hits[0]).ratio() - difflib.SequenceMatcher(None, v, hits[1]).ratio() < 0.05:
                hits = []   # two names fit about equally well: do not guess
        if len(hits) == 1:
            op.args[key] = hits[0]


def apply_round_ops(defn: dict[str, Any], ops: list[ParsedOp], st: LoopState, rnd: int) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """Apply ``ops`` one by one. Returns ``(definition, op_errors, ops_log_entries)``."""
    errors: list[dict[str, Any]] = []
    log: list[dict[str, Any]] = []
    st.readded = {o.args["name"]: o.index for o in ops if o.op == "add_node" and isinstance(o.args.get("name"), str)}
    if not any(o.op in ("remove_node", "rename_node") for o in ops):
        # models list connects before the nodes they connect; a batch with no removal or rename means the same in either order
        ops = [o for o in ops if o.op == "add_node"] + [o for o in ops if o.op != "add_node"]
    for op in ops:
        resolve_op_names(op, defn)
        refused = enforce_allowlist(op, defn, st)
        entry = {"round": rnd, "op_index": op.index, "op": op.op, "args": _scrub_args(op.args, st.ctx.secrets)}
        if refused:
            errors.append({"op_index": op.index, "op": op.op, "message": refused})
            log.append({**entry, "result": "error", "message": refused})
            continue
        try:
            defn = workflow_toolkit.apply_ops(defn, [{"op": op.op, "args": op.args}])
        except workflow_toolkit.OpError as e:
            errors.append({"op_index": op.index, "op": op.op, "message": e.message})
            log.append({**entry, "result": "error", "message": e.message})
            continue
        except Exception as e:  # noqa: BLE001 - a malformed arg must cost one op, never the round
            msg = f"The op could not be applied ({type(e).__name__})."
            errors.append({"op_index": op.index, "op": op.op, "message": msg})
            log.append({**entry, "result": "error", "message": msg})
            continue
        if op.op == "rename_node":
            st.renames[op.args["old"]] = op.args["new"]
        log.append({**entry, "result": "ok", "message": op.why})
    return defn, errors, log


# ═══════════════════════════════════════════════════════════════════════════════════════════
# THE PIPELINE: finalize a definition, check it, test it
# ═══════════════════════════════════════════════════════════════════════════════════════════


def attach_interface(defn: dict[str, Any], spec: dict[str, Any] | None) -> tuple[dict[str, Any], str | None]:
    """The server builds the screen (Stage 5 ``generate_interface``); the model only chose kind, slug and title. ``(defn, warning)``."""
    want = (spec or {}).get("interface")
    if not want:
        return defn, None
    from ..schemas.interface import generate_interface, slugify

    kind = want.get("kind")
    try:
        iface = generate_interface(defn, defn.get("name") or spec.get("title") or "Workflow", kind, slugify(want.get("slug") or want.get("title") or spec.get("title") or ""))
        if want.get("title"):
            iface["title"] = want["title"]
        return workflow_toolkit.set_interface(defn, iface), None
    except Exception as e:  # noqa: BLE001
        msg = getattr(e, "message", None) or str(e)
        return defn, f"The {kind} screen could not be generated yet: {msg}"


_BARE_CONDITION = re.compile(r"\$json|\$node|\$vars|\$input|[<>]=?|==|!=|\band\b|\bor\b|\bnot\b|\blength\(")


def align_step_names(defn: dict[str, Any], spec: dict[str, Any] | None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """FIX14: the spec's step names are the contract (approved tests name nodes by them). A node the spec has no step for, whose type is the
    type of a step that has no node, is that step under another name: rename it (references, connections and default mocks follow).
    Only when the pairing is certain: one candidate per missing step, or equally many in the same order."""
    steps = [s for s in (spec or {}).get("steps") or [] if isinstance(s, dict) and s.get("name") and s.get("node_type")]
    step_names = {s["name"] for s in steps}
    have = {n["name"] for n in defn.get("nodes") or []}
    fixes: list[dict[str, Any]] = []
    for ntype in dict.fromkeys(s["node_type"] for s in steps):
        missing = [s["name"] for s in steps if s["node_type"] == ntype and s["name"] not in have]
        extra = [n["name"] for n in defn.get("nodes") or [] if n.get("type") == ntype and n["name"] not in step_names]
        if not missing or len(missing) != len(extra):
            continue
        for old, new in zip(extra, missing):          # same count: pair in order (spec order = flow order = the order the model built them)
            if new in {n["name"] for n in defn["nodes"]}:
                continue
            try:
                defn = workflow_toolkit.rename_node(defn, old, new)
            except workflow_toolkit.ToolkitError:
                continue
            fixes.append({"node": new, "code": "FIX14", "changed": True,
                          "message": f"The {ntype} node '{old}' is the spec step '{new}'; renamed it, because approved tests refer to steps by name."})
    return defn, fixes


def local_repairs(defn: dict[str, Any], spec: dict[str, Any] | None = None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Builder-only certain fixes beyond FIX01-FIX12. FIX13: an ``If`` condition written without ``{{ }}`` is a literal string (always true).
    FIX14: a node that is a spec step under another name takes the step's name."""
    defn, fixes = align_step_names(defn, spec)
    for n in defn.get("nodes") or []:
        if n.get("type") != "If" or not isinstance(n.get("parameters"), dict):
            continue
        cond = n["parameters"].get("condition")
        if isinstance(cond, str) and cond.strip() and "{{" not in cond and _BARE_CONDITION.search(cond):
            n["parameters"]["condition"] = "{{ " + cond.strip() + " }}"
            fixes.append({"node": n.get("name"), "code": "FIX13", "changed": True,
                          "message": "The If condition was not inside {{ }}, so it would be read as text and always be true; wrapped it as an expression."})
    return defn, fixes


def finalize(defn: dict[str, Any], st: LoopState, base_names: set[str] | None) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """Deterministic repairs, default mocks, layout, interface. Returns ``(defn, auto_fixes, mock_warnings, warnings)``."""
    ctx = st.ctx
    defn, fixes = ai_repairs.apply_repairs(defn, spec=st.spec, catalog=ctx.catalog, api_ops=ctx.api_ops, operations=ctx.operations,
                                           renames=st.renames)
    st.renames = {}
    defn, more = local_repairs(defn, st.spec)
    fixes = list(fixes) + more
    # a default mock the server made earlier is regenerated, so it follows the node when its api_ref or type changed
    defaults = dict(defn.get("test_defaults") or {})
    mocks = dict(defaults.get("mocks") or {})
    for name, m in list(st.auto_mocks.items()):
        if mocks.get(name) == m:
            del mocks[name]
    defaults["mocks"] = mocks
    defn["test_defaults"] = defaults
    before = set(mocks)
    defn, mock_warnings = ai_default_mocks.fill(defn, ctx.operations, ctx.subworkflow_outputs or None)
    after = (defn.get("test_defaults") or {}).get("mocks") or {}
    st.auto_mocks = {n: after[n] for n in after if n not in before}
    defn = auto_layout.auto(defn, keep=base_names if base_names is not None else [])
    defn, warn = attach_interface(defn, st.spec)
    return defn, fixes, mock_warnings, [warn] if warn else []


def structure_findings(defn: dict[str, Any]) -> list[dict[str, Any]]:
    """The save path's own structural checks, as checker-style findings (a definition Keep would refuse is not usable)."""
    from ..repositories.workflow_repository import definition_to_workflow

    try:
        sb._check_structure(definition_to_workflow(defn, str(defn.get("name") or "workflow")[:255] or "workflow"))
    except Exception as e:  # noqa: BLE001
        return [{"code": "STRUCT001", "severity": "error", "message": getattr(e, "message", None) or str(e), "node": None, "param": None, "path": None}]
    return []


def check_all(defn: dict[str, Any], ctx: Ctx) -> dict[str, Any]:
    from .workflow_checker import check_workflow

    try:
        res = check_workflow(defn, lookups=ctx.lookups)
    except Exception as e:  # noqa: BLE001 - an odd definition must reach the model as feedback, not crash the build
        logger.info("checker failed: %s", type(e).__name__)
        res = {"findings": [{"code": "CHK001", "severity": "error", "message": f"The checker could not read this workflow ({type(e).__name__}).",
                             "node": None, "param": None, "path": None}], "shapes_by_node": {}}
    findings = list(res.get("findings") or []) + structure_findings(defn)
    shapes = res.get("shapes_by_node") or {}
    return {"findings": findings, "shapes": shapes, "errors": sum(1 for f in findings if f.get("severity") == "error"),
            "warnings": sum(1 for f in findings if f.get("severity") == "warning"),
            "grounding": ai_grounding.check_expressions(defn, shapes)}


def _tamper(defn: dict[str, Any], expected: list[dict[str, Any]]) -> list[str]:
    return sb.tampered_tests(expected, defn)


def distill_tests(report: dict[str, Any] | None, limit: int = 8) -> list[dict[str, Any]]:
    """The failing tests as repair evidence: failed assertions, the path the run took, what the failing nodes actually emitted."""
    out: list[dict[str, Any]] = []
    for r in (report or {}).get("results") or []:
        if r.get("status") == "passed":
            continue
        failed = []
        for a in r.get("assertions") or []:
            if not a.get("passed"):
                failed.append({k: _clip(a.get(k)) for k in ("node", "subject", "port", "path", "op", "expected", "actual", "message") if a.get(k) is not None})
        run_errors = []
        for f in r.get("failures") or []:
            if f.get("kind") != "assertion":
                # a step that raised (a Code node's Python error, a bad expression, an unmocked call) says WHY in its message: keep it whole
                # enough to read and put it first, so the other failures of the same test can never push it out of the list
                run_errors.append({k: _clip(f.get(k), 600 if k == "message" else 300) for k in ("kind", "node", "message") if f.get(k) is not None})
        keep_errors = run_errors[:3]  # ... and a flood of assertion / contract failures can never push them out of the capped list
        failed = failed[: 8 - len(keep_errors)] + keep_errors
        trace = [f"{n}:{p}" for n, ports in (r.get("ports_taken") or {}).items() for p in ports]
        not_run = sorted(n for n, s in (r.get("node_status") or {}).items() if s in ("not_run", "no_output"))
        nodes = []
        for f in failed:
            if f.get("node") and f["node"] not in nodes:
                nodes.append(f["node"])
        seen_out = {}
        for n in nodes[:4]:
            ports = (r.get("outputs") or {}).get(n)
            if ports:
                first = next(iter(ports.values()))
                seen_out[n] = _clip(first[0] if isinstance(first, list) and first else first, 500)
            req = (r.get("requests") or {}).get(n)
            if req:
                seen_out[f"{n} (request sent)"] = _clip(req[-1] if isinstance(req, list) else req, 500)
        out.append({"test_id": r.get("test_id"), "name": r.get("name"), "status": r.get("status"), "failed": failed, "trace": trace,
                    "not_run": not_run, "actual_outputs": seen_out})
        if len(out) >= limit:
            break
    return out


def test_counts(report: dict[str, Any] | None, approved_ids: set[str]) -> dict[str, int]:
    res = (report or {}).get("results") or []
    appr = [r for r in res if r.get("test_id") in approved_ids]
    return {"approved_total": len(appr), "approved_failed": sum(1 for r in appr if r.get("status") != "passed"),
            "passed": sum(1 for r in res if r.get("status") == "passed"), "failed": sum(1 for r in res if r.get("status") == "failed"),
            "errored": sum(1 for r in res if r.get("status") == "error"), "total": len(res)}


def approved_test_ids(defn: dict[str, Any]) -> set[str]:
    return {t["id"] for t in defn.get("tests") or [] if t.get("status") == "approved" or t.get("locked")}


@dataclass
class RoundResult:
    n: int
    defn: dict[str, Any]
    ops: int = 0
    rejected: int = 0
    auto_fixed: int = 0
    op_errors: list[dict[str, Any]] = field(default_factory=list)
    auto_fixes: list[dict[str, Any]] = field(default_factory=list)
    check: dict[str, Any] = field(default_factory=dict)
    coverage: dict[str, Any] = field(default_factory=dict)
    tests: dict[str, Any] | None = None
    counts: dict[str, int] = field(default_factory=dict)
    tampered: list[str] = field(default_factory=list)
    mock_warnings: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    notes: str = ""

    @property
    def errors(self) -> int:
        return int(self.check.get("errors", 0))

    @property
    def missing(self) -> int:
        return len(self.coverage.get("steps_missing", [])) + len(self.coverage.get("steps_wrong_type", []))

    @property
    def grounding(self) -> list[dict[str, Any]]:
        return self.check.get("grounding", [])

    @property
    def failed_tests(self) -> int:
        return 999 if self.tests is None else self.counts.get("approved_failed", 0)

    @property
    def green(self) -> bool:
        """The Keep-relevant verdict: checker clean and every approved test passes."""
        return bool(self.tests is not None and self.errors == 0 and self.counts.get("approved_total", 0) > 0 and self.counts.get("approved_failed", 0) == 0
                    and not self.tampered)

    @property
    def concept_findings(self) -> list[dict[str, Any]]:
        """CGX01-CGX05 warnings (empty unless the proposal was built with the capability graph)."""
        return self.check.get("concept", [])

    @property
    def clean(self) -> bool:
        return self.green and self.missing == 0 and not self.concept_findings

    @property
    def key(self) -> tuple[int, ...]:
        base = (self.errors, self.failed_tests, self.missing, len(self.grounding))
        return base + (len(self.concept_findings),) if self.concept_findings else base

    def signature(self) -> str:
        """What a repair round would see. Two consecutive rounds with the same signature mean the model is spinning (no_progress):
        the same workflow, the same failures (with their actual values), the same checker errors and the same refused ops."""
        failing = [(r.get("test_id"), r.get("status"), [(f.get("node"), f.get("path"), _clip(f.get("expected"), 80), _clip(f.get("actual"), 80), f.get("message"))
                                                    for f in r.get("failures") or []])
                   for r in (self.tests or {}).get("results") or [] if r.get("status") != "passed"]
        errs = sorted((f.get("code"), f.get("node"), f.get("message")) for f in self.check.get("findings") or [] if f.get("severity") == "error")
        ops = sorted((e.get("op"), e.get("message")) for e in self.op_errors)
        parts = [sb.canonical_hash(self.defn), failing, errs, ops, [g["message"] for g in self.grounding], self.coverage]
        if self.concept_findings:
            parts.append(sorted((f["code"], f.get("node"), f["message"]) for f in self.concept_findings))
        return sb.canonical_hash(parts)

    def report_row(self) -> dict[str, Any]:
        return {"round": self.n, "ops": self.ops, "rejected": self.rejected, "auto_fixed": self.auto_fixed, "errors": self.errors,
                "warnings": int(self.check.get("warnings", 0)),
                "tests": {"passed": self.counts.get("passed", 0), "failed": self.counts.get("failed", 0) + self.counts.get("errored", 0)},
                **({"notes": self.notes} if self.notes else {})}


async def evaluate(defn: dict[str, Any], ctx: Ctx, proposal: dict[str, Any], spec: dict[str, Any] | None, expected_tests: list[dict[str, Any]],
                   *, run_tests: bool = True, understanding: dict[str, Any] | None = None, siblings: dict[str, Any] | None = None) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any] | None, dict[str, int], list[str]]:
    """Check + coverage + tests of a finalized definition. Returns ``(check, coverage, test_report, counts, tampered_ids)``."""
    check = check_all(defn, ctx)
    if understanding:   # capability graph: CGX01-CGX05, warnings under check["concept"] (absent without an understanding)
        from . import kg_checks

        try:
            check["concept"] = kg_checks.check(defn, understanding, siblings=siblings)
        except Exception:  # noqa: BLE001 - a bug in a concept check must not cost the build
            logger.warning("concept checks failed", exc_info=True)
            check["concept"] = []
    coverage = ai_spec.spec_coverage(defn, spec) if spec else {"steps_missing": [], "steps_wrong_type": [], "rules_untested": []}
    tampered = _tamper(defn, expected_tests)
    report = None
    if run_tests and not tampered:
        try:
            async with _sf()() as s:
                report = await sb.run_proposal_tests(s, proposal, defn)
        except Exception as e:  # noqa: BLE001 - a definition the runner cannot load is reported as feedback, not a crash
            logger.info("proposal test run failed: %s", type(e).__name__)
            check["findings"].append({"code": "RUN001", "severity": "error", "message": f"The tests could not run: {getattr(e, 'message', None) or e}",
                                      "node": None, "param": None, "path": None})
            check["errors"] += 1
    return check, coverage, report, test_counts(report, approved_test_ids(defn)), tampered


# ═══════════════════════════════════════════════════════════════════════════════════════════
# BUILD
# ═══════════════════════════════════════════════════════════════════════════════════════════


def skeleton(row: dict[str, Any]) -> dict[str, Any]:
    """The round-0 starting point: the proposal's best definition so far (revise), else the base draft, else an empty workflow;
    plus the spec's approved tests (each exactly as approved)."""
    spec = row.get("spec") or {}
    start = row.get("proposed_definition") or row.get("base_definition")
    if start is None:
        start = {"name": spec.get("title") or "AI workflow", "nodes": [], "connections": [], "settings": {}, "tests": [], "test_defaults": {}}
    defn = copy.deepcopy(start)
    defn.setdefault("nodes", [])
    defn.setdefault("connections", [])
    defn.setdefault("test_defaults", {})
    have = {t.get("id") for t in defn.get("tests") or []}
    defn["tests"] = list(defn.get("tests") or []) + [copy.deepcopy(t) for t in spec.get("tests") or [] if t.get("id") not in have]
    # an approved spec test is always exactly the approved one (a revise child starts from the parent's output)
    by_id = {t["id"]: t for t in spec.get("tests") or []}
    defn["tests"] = [copy.deepcopy(by_id[t["id"]]) if t.get("id") in by_id else t for t in defn["tests"]]
    return defn


def _proposal_for_run(row: dict[str, Any]) -> dict[str, Any]:
    return {k: row.get(k) for k in ("id", "project_id", "team_id", "workflow_id", "spec")}


def _build_context(row: dict[str, Any], ctx: Ctx, defn: dict[str, Any], spec: dict[str, Any], graph: dict[str, Any] | None = None) -> dict[str, Any]:
    """``graph`` holds the optional ``capability_context`` / ``reference_workflow`` sections (empty without the capability graph)."""
    types = {n.get("type") for n in defn.get("nodes") or []}
    return {
        "spec": spec_for_model(spec),
        "catalog": ai_grounding.catalog_subset(spec, ctx.catalog, extra_types=set(types) | _full_types(ctx, row.get("request"))),
        "api_operations": ctx.api_ops, "variable_names": ctx.variable_names, **_sub_section(ctx), **_ds_section(ctx), **(graph or {}),
    }


async def build(proposal_id: str, actor: str, project_id: str | None = None, *, instruction: str | None = None) -> dict[str, Any]:
    """``POST /api/ai/proposals/{id}/build``: run the loop. Returns the view (``ready`` or ``failed``); 502 when round 0 never got an answer."""
    actor = clean_actor(actor)
    row = await get_row(proposal_id, project_id)
    if row["status"] != "spec_approved":
        raise BadState(row["status"], "Approve the spec before building.")
    model = row["model"]
    try:
        check_model(model)
    except ProposalError as e:
        await refuse(proposal_id, model, e)
        raise
    with _Busy(row["project_id"], actor):
        return await _run_build(row, actor, instruction)


async def _run_build(row: dict[str, Any], actor: str, instruction: str | None) -> dict[str, Any]:
    pid = row["id"]
    async with _sf()() as s:
        repo = AIProposalRepository(s)
        won = await repo.transition(pid, ["spec_approved"], "building", {"error_code": None, "error_message": None})
        if won is None:
            await s.rollback()
            raise BadState((await repo.get(pid))["status"])
        await s.commit()
    row = won
    t0 = time.monotonic()
    try:
        return await _build_loop(row, actor, instruction, t0)
    except ProposalError:
        raise
    except Exception as e:  # noqa: BLE001 - a bug must not leave the row 'building'
        logger.exception("build crashed")
        await _finish(pid, "failed", {"error_code": "internal_error", "error_message": f"The build crashed ({type(e).__name__})."},
                      {"status": "failed", "passed": False, "rounds": 0}, t0)
        raise AIFailed("The build crashed. Nothing was saved.", ai_code="internal_error", proposal_id=pid) from None


async def _finish(pid: str, status: str, fields: dict[str, Any], finished: dict[str, Any], t0: float) -> bool:
    async with _sf()() as s:
        repo = AIProposalRepository(s)
        won = await repo.transition(pid, ["building"], status, fields)
        if won is None:  # the stale reaper or a discard got there first: leave their answer
            await s.rollback()
            return False
        await repo.add_event(pid, "build_finished", finished)
        await repo.add_usage(pid, duration_ms=int((time.monotonic() - t0) * 1000))
        await s.commit()
    return True


# ── live-build events for the studio canvas (additive; carry names, types, positions and wiring, never parameter values) ──

_RAN = ("success", "error", "no_output")


def _xy(node: dict[str, Any]) -> dict[str, float]:
    pos = node.get("position") or {}
    return {"x": pos.get("x", 0), "y": pos.get("y", 0)}


def _conn_of(c: dict[str, Any]) -> list[str]:
    return [c.get("source_node"), c.get("source_output") or "main", c.get("target_node"), c.get("target_input") or "main"]


def _shape_of(rnd: int, defn: dict[str, Any], prev: dict[str, Any] | None) -> dict[str, Any]:
    """The ``round_shape`` payload: the round's nodes with type and position, its connections, which nodes changed (the top-level
    parameter keys whose value differs from ``prev``, the previous round's or the base definition) and which were removed.
    No parameter values travel, so a secret in a parameter cannot reach the event stream."""
    before = {n.get("name"): n for n in (prev or {}).get("nodes") or []}
    nodes = [n for n in defn.get("nodes") or [] if n.get("name")]
    now = {n["name"] for n in nodes}
    changed = []
    for n in nodes:
        old = before.get(n["name"])
        if old is None:
            continue
        a, b = old.get("parameters") or {}, n.get("parameters") or {}
        fields = sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))
        if old.get("type") != n.get("type"):
            fields.insert(0, "type")
        if fields:
            changed.append({"name": n["name"], "fields": fields})
    return {"round": rnd,
            "nodes": [{"name": n["name"], "type": n.get("type"), "position": _xy(n)} for n in nodes],
            "connections": [_conn_of(c) for c in defn.get("connections") or []],
            "changed": changed,
            "removed": sorted(name for name in before if name not in now)}


def _run_order(defn: dict[str, Any], ran: set[str]) -> list[str]:
    """The nodes that ran, trigger first, each after its upstream (Kahn's order, ties in definition order).
    ``node_status`` lists nodes in definition order, not run order, so the order is rebuilt from the wiring."""
    names = [n["name"] for n in defn.get("nodes") or [] if n.get("name") in ran]
    edges = [(c.get("source_node"), c.get("target_node")) for c in defn.get("connections") or []
             if c.get("source_node") in ran and c.get("target_node") in ran and c.get("source_node") != c.get("target_node")]
    indeg = {n: 0 for n in names}
    for _, t in edges:
        indeg[t] += 1
    out: list[str] = []
    ready = [n for n in names if indeg[n] == 0]
    while ready:
        n = ready.pop(0)
        out.append(n)
        for s_, t in edges:
            if s_ == n:
                indeg[t] -= 1
                if indeg[t] == 0:
                    ready.append(t)
        ready.sort(key=names.index)
    return out + [n for n in names if n not in out]  # a cycle: keep the rest in definition order


def _test_results(report: dict[str, Any] | None, defn: dict[str, Any]) -> list[dict[str, Any]]:
    """``tested.results``: per test its status, the nodes that ran in run order, and the nodes it failed on."""
    out = []
    for r in (report or {}).get("results") or []:
        ns = r.get("node_status") or {}
        failed = []
        for f in r.get("failures") or []:
            if f.get("node") and f["node"] not in failed:
                failed.append(f["node"])
        failed += [n for n, st in ns.items() if st == "error" and n not in failed]
        out.append({"test_id": r.get("test_id"), "name": r.get("name"), "status": r.get("status"),
                    "path": _run_order(defn, {n for n, st in ns.items() if st in _RAN}), "failed_nodes": failed})
    return out


async def _build_loop(row: dict[str, Any], actor: str, instruction: str | None, t0: float) -> dict[str, Any]:
    pid, model, spec = row["id"], row["model"], row["spec"]
    cfg = wf_ai.config()
    ctx = await load_ctx(row["project_id"])
    base = row.get("base_definition")
    await attach_subworkflows(ctx, row.get("request") or "", workflow_id=row.get("workflow_id"), base=base)
    siblings = None
    if row.get("app_build_id"):   # an app part: its sibling parts and planned tables are virtual (see app_composer.part_seam)
        from . import app_composer

        seam = await app_composer.part_seam(row)
        if seam:
            ctx.virtual_children, ctx.virtual_tables, siblings = dict(seam["children"]), dict(seam["tables"]), seam["siblings"]
            apply_virtuals(ctx)
    understanding = row.get("understanding") or None
    graph: dict[str, Any] = {}
    if understanding:   # capability graph: what the person confirmed and what already exists, plus an adapt-from reference (best effort)
        cap = _capability_section(understanding)
        ref = await _reference_workflow(ctx.project_id, understanding, row.get("part_key"))
        graph = {**({"capability_context": cap} if cap else {}), **({"reference_workflow": ref} if ref else {})}
    meter = Meter(max_calls=cfg.max_calls, deadline_s=cfg.build_deadline_s, case=ctx.project_id, secret_values=ctx.secrets)
    meter.calls = int(row.get("llm_calls") or 0)  # the call budget is per proposal: the spec phase already used some
    flusher = Flusher(pid, meter)
    st = LoopState(ctx=ctx, spec=spec, max_nodes=cfg.max_nodes)
    expected = [t for t in spec.get("tests") or []] + [t for t in (base or {}).get("tests") or [] if t.get("status") == "approved" or t.get("locked")]
    defn = skeleton(row)
    base_names = {n["name"] for n in (base or {}).get("nodes") or [] if n.get("name")} if base is not None else set()
    proposal = _proposal_for_run(row)
    rounds: list[RoundResult] = []
    ops_log: list[dict[str, Any]] = []
    change_requests: list[dict[str, Any]] = list(row.get("test_change_requests") or [])
    approved_ids = {t["id"] for t in expected}
    stopped = "rounds_exhausted"
    ai_error: AIError | None = None
    best: RoundResult | None = None
    feedback: RoundResult | None = None
    prev: RoundResult | None = None
    shape_prev: dict[str, Any] = defn  # what the next round_shape is compared with: the base, then the previous round
    # an existing definition (revise child, or modify) is changed with ops; an empty one is built from the spec
    current_for_prompt = compact_definition(defn) if (defn.get("nodes")) else None

    for n in range(cfg.max_repair_rounds + 1):
        await add_events(pid, [("round_started", {"round": n}, None)])
        task = "build" if n == 0 else "repair"
        if n == 0:
            payload = _build_context(row, ctx, defn, spec, graph)
            payload["current_workflow"] = current_for_prompt
            payload["untrusted_revision_request"] = instruction or ""
        else:
            assert feedback is not None
            payload = {**_build_context(row, ctx, defn, spec, graph), "current_workflow": compact_definition(defn),
                       "shapes": ai_grounding.compact_shapes(feedback.check.get("shapes")),
                       "op_errors": feedback.op_errors, "auto_fixes": [{"node": f.get("node"), "message": f.get("message")} for f in feedback.auto_fixes],
                       "findings": _findings_for_model(feedback.check.get("findings") or []),
                       "grounding": [{"code": g["code"], "node": g["node"], "message": g["message"]} for g in feedback.grounding[:20]],
                       "test_failures": distill_tests(feedback.tests), "spec_coverage": feedback.coverage,
                       "round": n, "rounds_left": cfg.max_repair_rounds - n + 1,
                       "untrusted_revision_request": instruction or ""}
            if feedback.concept_findings:   # CGX* findings ride in the ordinary `findings` section; no new prompt section
                payload["findings"] = payload["findings"] + _findings_for_model(feedback.concept_findings)
            if feedback.tampered:
                payload["op_errors"] = payload["op_errors"] + [{"op_index": -1, "op": "-", "message": "The approved tests changed in the last round; that round was discarded."}]
            if feedback.tests is None and not feedback.errors:
                payload["findings"] = payload["findings"] + [{"code": "RUN001", "node": None, "message": "The tests could not run on the last round's workflow."}]
        try:
            reply = await ask_model(task, payload, prompts.BUILD_SCHEMA, meter=meter, flusher=flusher, ctx=ctx, model=model)
        except AIError as e:
            ai_error = e
            stopped = {"call_budget": "call_budget", "deadline": "deadline"}.get(e.code, "ai_failed")
            break
        ops, op_errors = parse_ops(reply, cfg.max_ops)
        before = defn
        defn, more_errors, log = apply_round_ops(defn, ops, st, n)
        op_errors += more_errors
        ops_log += log
        defn, fixes, mock_warnings, warns = finalize(defn, st, base_names if base is not None else set())
        for f in fixes:
            ops_log.append({"round": n, "op_index": -1, "op": f["code"], "args": {"node": f.get("node")},
                            "result": "auto_fix" if f.get("changed") else "info", "message": f["message"]})
        await add_events(pid, [("round_shape", _shape_of(n, defn, shape_prev), None)])
        shape_prev = defn
        await add_events(pid, [("ops_applied", {"round": n, "ok": sum(1 for l in log if l["result"] == "ok"), "rejected": len(op_errors),
                                                 "auto_fixed": sum(1 for f in fixes if f.get("changed"))}, None)])
        # test change requests: filed for a person, never applied
        for tcr in reply.get("test_change_requests") or []:
            if isinstance(tcr, dict):
                tcr = {**tcr, "test_id": resolve_test_ref(tcr.get("test_id"), spec, approved_ids)}
            if isinstance(tcr, dict) and tcr.get("test_id") in approved_ids and not any(
                    c.get("test_id") == tcr["test_id"] and c.get("reason") == tcr.get("reason") for c in change_requests):
                proposed = tcr.get("proposed_json")
                if isinstance(proposed, str):
                    try:
                        proposed = json.loads(proposed)
                    except ValueError:
                        pass
                change_requests.append({"test_id": tcr["test_id"], "reason": str(tcr.get("reason") or "")[:1000], "proposed": proposed, "round": n})
                await add_events(pid, [("test_change_requested", {"test_id": tcr["test_id"]}, None)])
        check, coverage, report, counts, tampered = await evaluate(defn, ctx, proposal, spec, expected, understanding=understanding, siblings=siblings)
        r = RoundResult(n=n, defn=defn, ops=len(reply.get("ops") or []), rejected=len(op_errors), auto_fixed=sum(1 for f in fixes if f.get("changed")),
                        op_errors=op_errors, auto_fixes=fixes, check=check, coverage=coverage, tests=report, counts=counts, tampered=tampered,
                        mock_warnings=mock_warnings, warnings=warns, notes=str(reply.get("notes") or "")[:300])
        await add_events(pid, [("checked", {"round": n, "errors": r.errors, "warnings": int(check["warnings"])}, None)] + (
            [("concept_checked", {"round": n, "findings": [f["code"] for f in r.concept_findings]}, None)] if understanding else []) + (
            [("tested", {"round": n, "passed": counts["passed"], "failed": counts["failed"], "errored": counts["errored"],
                         "results": _test_results(report, defn)}, None)] if report is not None else []),
            fields=None)
        if tampered:  # layer 1 of the immutability gate: this round is discarded, not merely ranked
            defn = before
            r.defn = before
        rounds.append(r)
        if not tampered and (best is None or r.key <= best.key):
            best = r
        if r.clean:
            stopped = "passed"
            break
        if prev is not None and prev.signature() == r.signature():
            stopped = "no_progress"
            break
        if n == cfg.max_repair_rounds:
            stopped = "rounds_exhausted"
            break
        prev, feedback = r, r
        rem = meter.remaining_s()
        if rem is not None and rem <= 0:
            stopped = "deadline"
            break

    async with _sf()() as s:  # ops log + change requests, once
        repo = AIProposalRepository(s)
        await repo.append_ops_log(pid, ops_log)
        await repo.update_fields(pid, {"test_change_requests": change_requests})
        await s.commit()

    if best is None:  # no round produced a definition: the model never answered, or every round changed the approved tests
        if any(r.tampered for r in rounds):
            err = AIError("tests_changed", "Every round changed the approved tests, so the build was discarded. Nothing was saved.")
        else:
            err = ai_error or AIError("provider_failed", "The model did not produce a workflow.")
        await _finish(pid, "failed", {"error_code": err.code, "error_message": err.message},
                      {"status": "failed", "passed": False, "rounds": 0, "error_code": err.code}, t0)
        raise ai_error_to_http(err, proposal_id=pid)

    final = _canonical_definition(best.defn)
    diff = workflow_diff.diff_definitions(base, final)
    score = await _score(ctx, proposal, final)
    report_doc = {
        "passed": best.green, "rounds": [r.report_row() for r in rounds], "best_round": best.n,
        "checker": {"errors": best.errors, "warnings": int(best.check.get("warnings", 0)),
                    "findings": _findings_for_model(best.check.get("findings") or [], 100)},
        "tests": best.tests, "spec_coverage": best.coverage,
        "grounding": [{"code": g["code"], "node": g["node"], "message": g["message"]} for g in best.grounding],
        "score": score, "stopped_because": stopped,
        "warnings": best.warnings, "mock_warnings": best.mock_warnings,
        **({"ai_error": {"code": ai_error.code, "message": ai_error.message}} if ai_error is not None else {}),
        **({"concept_findings": [{"code": f["code"], "node": f.get("node"), "message": f["message"]} for f in best.concept_findings]} if understanding else {}),
    }
    usable = best.errors == 0 and bool(final.get("nodes"))
    status = "ready" if usable else "failed"
    fields: dict[str, Any] = {"proposed_definition": final, "diff": diff, "report": report_doc}
    if not usable:
        fields.update(error_code="checker_errors" if final.get("nodes") else "no_workflow",
                      error_message=("The workflow still has checker errors after the repair rounds." if final.get("nodes")
                                     else "The model did not add any nodes."))
    await flusher.flush()
    await _finish(pid, status, fields, {"status": status, "passed": best.green, "rounds": len(rounds)}, t0)
    return await get_view(pid)


def resolve_test_ref(ref: Any, spec: dict[str, Any], approved_ids: set[str]) -> Any:
    """A model may cite a test by id, by the ``T1``-style ref it saw in the spec, or by name; all mean the same approved test."""
    if ref in approved_ids:
        return ref
    tests = [t for t in spec.get("tests") or [] if isinstance(t, dict)]
    m = re.fullmatch(r"\s*T(\d+)\s*", str(ref or ""))
    if m and 1 <= int(m.group(1)) <= len(tests):
        return tests[int(m.group(1)) - 1].get("id")
    for t in tests:
        if t.get("name") == ref:
            return t.get("id")
    return ref


def _findings_for_model(findings: list[dict[str, Any]], limit: int = 30) -> list[dict[str, Any]]:
    order = {"error": 0, "warning": 1}
    fs = sorted((f for f in findings if f.get("severity") in order), key=lambda f: order[f["severity"]])
    return [{"code": f.get("code"), "severity": f.get("severity"), "node": f.get("node"), "message": f.get("message")} for f in fs[:limit]]


def _canonical_definition(defn: dict[str, Any]) -> dict[str, Any]:
    """The definition in the exact form a saved draft has (through the engine ``Workflow`` model)."""
    from ..repositories.workflow_repository import definition_to_workflow, workflow_to_definition

    wf = definition_to_workflow(defn, str(defn.get("name") or "AI workflow")[:255] or "AI workflow")
    out = workflow_to_definition(wf)
    if defn.get("name"):
        out["name"] = defn["name"]
    return out


async def _score(ctx: Ctx, proposal: dict[str, Any], defn: dict[str, Any]) -> dict[str, Any] | None:
    """The existing scorecard without mutations (they are on demand in review). Never fails a build."""
    from .scorecard import score

    try:
        async with _sf()() as s:
            from ..repositories.workflow_repository import WorkflowRepository

            return await score(WorkflowRepository(s), sb.proposal_stored(proposal, defn), defn, lookups=ctx.lookups, mutations=False)
    except Exception as e:  # noqa: BLE001
        logger.info("scorecard failed: %s", type(e).__name__)
        return None


# ═══════════════════════════════════════════════════════════════════════════════════════════
# REVISE, KEEP, UNDO, DISCARD
# ═══════════════════════════════════════════════════════════════════════════════════════════


async def revise(proposal_id: str, instruction: str, actor: str, project_id: str | None = None) -> dict[str, Any]:
    """``POST /api/ai/proposals/{id}/revise``: a child ``modify`` proposal that starts from this one's result, builds once, and
    moves the parent to ``discarded``. The parent's approved tests stay the gate."""
    actor = clean_actor(actor)
    instruction = (instruction or "").strip()
    if not instruction:
        raise RequestInvalid("Say what to change.")
    if len(instruction) > MAX_REQUEST_CHARS:
        raise RequestInvalid(f"The instruction is longer than {MAX_REQUEST_CHARS} characters.")
    parent = await get_row(proposal_id, project_id)
    if parent["status"] != "ready":
        raise BadState(parent["status"], "Only a proposal that is ready can be revised.")
    if not isinstance(parent.get("spec"), dict) or not parent["spec"]:
        # a node-level assist has no spec to build from; refuse BEFORE the parent is touched so the ready proposal stays as it was
        raise BadState(parent["status"], "This proposal came from a node assist and has no spec; ask the node assistant again instead of revising it.")
    model = parent["model"]
    try:
        check_model(model)
    except ProposalError as e:
        await refuse(proposal_id, model, e)
        raise
    ctx = await load_ctx(parent["project_id"])
    clean_instruction, counts = ai_policy.scrub_request(instruction, ctx.secrets)
    cid = new_proposal_id()
    with _Busy(parent["project_id"], actor):
        async with _sf()() as s:
            repo = AIProposalRepository(s)
            gone = await repo.transition(proposal_id, ["ready"], "discarded")
            if gone is None:
                await s.rollback()
                raise BadState((await repo.get(proposal_id))["status"])
            await repo.add_event(proposal_id, "discarded", {"actor": actor, "revised_by": cid}, actor)
            await repo.insert({
                "id": cid, "project_id": parent["project_id"], "team_id": parent["team_id"], "workflow_id": parent["workflow_id"],
                "parent_id": proposal_id, "kind": "modify", "status": "spec_approved", "actor": actor, "request": clean_instruction,
                "request_sha256": _sha(instruction), "spec": parent["spec"], "spec_approved_by": parent["spec_approved_by"],
                "spec_approved_at": parent["spec_approved_at"], "spec_fingerprint": parent["spec_fingerprint"],
                "base_definition": parent["base_definition"], "base_draft_hash": parent["base_draft_hash"],
                "proposed_definition": parent["proposed_definition"], "model": model, "provider": parent["provider"], "region": ai_policy.region(),
                **({"understanding": parent["understanding"]} if parent.get("understanding") else {}),
            })
            await repo.add_event(cid, "spec_requested", {"request_sha256": _sha(instruction), "model": model}, actor)
            await repo.add_event(cid, "spec_approved", {"actor": parent["spec_approved_by"], "fingerprint": parent["spec_fingerprint"],
                                                         "test_ids": [t["id"] for t in (parent["spec"] or {}).get("tests") or []]}, actor)
            if counts["secrets"] or counts["pii_total"]:
                await repo.add_event(cid, "scrubbed", {"secrets": counts["secrets"], "pii": counts["pii"]})
            await s.commit()
        child = await get_row(cid)
        await _run_build(child, actor, clean_instruction)
    return await get_view(cid)


async def chat_change(workflow_id: str, request: str, actor: str, *, project_id: str | None = None, node: str | None = None,
                      model: str | None = None) -> dict[str, Any]:
    """A change asked for in the editor's chat (DESIGN-STAGE6 section 8): a ``modify`` proposal on the stored draft whose spec is the
    workflow's own approved tests (nothing else is approved by this path), then ONE build loop with the user's words in the
    ``untrusted_revision_request`` slot. Returns the view (``ready`` or ``failed``). Nothing is saved before Keep."""
    from ..repositories.workflow_repository import WorkflowRepository

    actor = clean_actor(actor)
    request = (request or "").strip()
    if not request:
        raise RequestInvalid("Say what to change.")
    if len(request) > MAX_REQUEST_CHARS:
        raise RequestInvalid(f"The request is longer than {MAX_REQUEST_CHARS} characters.")
    cfg = wf_ai.config()
    model = model or cfg.model
    check_model(model)
    async with _sf()() as s:
        stored = await WorkflowRepository(s).get(workflow_id)
        if stored is None or (project_id and stored.project_id not in (project_id,) and not await _same_project(s, project_id, stored.project_id)):
            raise ProposalNotFound(f"Workflow not found: {workflow_id}")
        base, base_hash = sb.draft_of(stored)
        stored_project = stored.project_id
    ctx = await load_ctx(stored_project)
    clean_request, counts = ai_policy.scrub_request(request, ctx.secrets)
    instruction = f"About the node '{node}': {clean_request}" if node else clean_request
    tests = [copy.deepcopy(t) for t in base.get("tests") or [] if t.get("status") == "approved" or t.get("locked")]
    fp = sb.fingerprint(tests)
    now = _now()
    spec = {"spec_version": ai_spec.SPEC_VERSION, "title": base.get("name") or "Workflow", "summary": clean_request[:300],
            "trigger": {"kind": _base_trigger_kind(base) or "manual", "fields": []}, "rules": [], "steps": [], "interface": None,
            "tests": tests, "rule_tests": {}, "assumptions": [], "questions": [], "issues": [], "warnings": []}
    pid = new_proposal_id()
    with _Busy(stored_project, actor):
        async with _sf()() as s:
            repo = AIProposalRepository(s)
            await repo.insert({
                "id": pid, "project_id": stored_project, "team_id": ctx.team_id, "workflow_id": workflow_id, "kind": "modify",
                "status": "spec_approved", "actor": actor, "request": clean_request, "request_sha256": _sha(request), "spec": spec,
                "spec_approved_by": actor, "spec_approved_at": now, "spec_fingerprint": fp, "base_definition": base, "base_draft_hash": base_hash,
                "model": model, "provider": ai_policy.provider_of(model), "region": ai_policy.region()})
            await repo.add_event(pid, "spec_requested", {"request_sha256": _sha(request), "model": model, "via": "chat"}, actor)
            await repo.add_event(pid, "spec_approved", {"actor": actor, "fingerprint": fp, "test_ids": [t["id"] for t in tests], "via": "chat"}, actor)
            if counts["secrets"] or counts["pii_total"]:
                await repo.add_event(pid, "scrubbed", {"secrets": counts["secrets"], "pii": counts["pii"]})
            await s.commit()
        row = await get_row(pid)
        await _run_build(row, actor, instruction)
    return await get_view(pid)


async def _same_project(session: Any, ref: str, project_id: str) -> bool:
    """True when ``ref`` (an id or slug) names ``project_id``."""
    from .project_service import resolve_project

    p = await resolve_project(session, ref)
    return p is not None and p.id == project_id


async def keep(proposal_id: str, actor: str, *, accept_failing: bool = False, name: str | None = None, project_id: str | None = None) -> dict[str, Any]:
    actor = clean_actor(actor)
    async with _sf()() as s:
        res = await sb.keep(s, proposal_id, actor, accept_failing=accept_failing, name=name, project_id=project_id)
    if (res["proposal"].get("understanding") or None) and not res.get("already"):
        await _learn_from_keep(proposal_id)   # capability graph: only graph-built proposals teach it; best effort, after the Keep committed
    return {"workflow_id": res["workflow_id"], "draft_hash": res["draft_hash"], "proposal": view(res["proposal"])}


async def _learn_from_keep(proposal_id: str) -> None:
    """``kg_learn.on_proposal_kept`` and its ``learned`` event. A failure here is logged and never touches the Keep."""
    try:
        from . import kg_learn

        learned = await kg_learn.on_proposal_kept(proposal_id)
        if learned is not None:
            await add_events(proposal_id, [("learned", learned.payload(), None)])
    except Exception:  # noqa: BLE001
        logger.warning("learning from a kept proposal failed", exc_info=True)


async def undo(proposal_id: str, actor: str, project_id: str | None = None) -> dict[str, Any]:
    actor = clean_actor(actor)
    async with _sf()() as s:
        res = await sb.undo(s, proposal_id, actor, project_id=project_id)
    return view(res["proposal"])


async def discard(proposal_id: str, actor: str, project_id: str | None = None) -> dict[str, Any]:
    actor = clean_actor(actor)
    row = await get_row(proposal_id, project_id)
    if row["status"] == "clarify":   # waiting for answers: nothing was drafted, so there is nothing else to undo
        async with _sf()() as s:
            repo = AIProposalRepository(s)
            if await repo.transition(proposal_id, ["clarify"], "discarded") is not None:
                await repo.add_event(proposal_id, "discarded", {"actor": actor}, actor)
            await s.commit()
        return await get_view(proposal_id)
    async with _sf()() as s:
        res = await sb.discard(s, proposal_id, actor, project_id=project_id)
    return view(res["proposal"])


# ── stale reaper hook ───────────────────────────────────────────────────────────────────────


async def reap_stale_builds(session_factory: Any = None) -> list[str]:
    """``building`` rows untouched for the build deadline plus 60 s become ``failed(deadline)``. Called by the stale reaper."""
    older = wf_ai.config().build_deadline_s + 60
    async with (session_factory or _sf())() as s:
        ids = await AIProposalRepository(s).fail_stale_building(older)
        if ids:
            repo = AIProposalRepository(s)
            for pid in ids:
                await repo.add_event(pid, "build_finished", {"status": "failed", "passed": False, "rounds": 0, "error_code": "deadline"})
        await s.commit()
    return ids
