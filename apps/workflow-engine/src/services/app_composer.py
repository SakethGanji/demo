"""The app composer: build an APPLICATION (several workflows, a table, a form and a report page) with the Stage 6 loop, one part at a time
(PLAN-CAPABILITY-GRAPH section 5; CONTRACTS-CAPGRAPH.md 5.1).

An ``app_builds`` row owns the request, the Understanding, the plan and, once kept, the generated bundle. Every part is a NORMAL
``ai_proposals`` row (``app_build_id``, ``part_key``) that runs the unchanged spec -> approve -> build loop, so the studio plays a part
on the canvas with the existing stage player and reviews it with the existing proposal endpoints. Nothing is saved to the project
until Keep:

    clarify -> planned -> specs -> building -> ready -> kept          (discarded from anywhere before kept)
    create ->  answers ->  approve-plan -> build-next x N -> keep

**Seams.** While part P is drafted or built its siblings (``@part/<key>``) and the planned tables are *virtual*: ``app_bundle.virtuals``
gives the checker real contracts. ``draft_spec`` takes them through its ``app_part`` argument. ``_build_loop`` loads its own context, so the
``_build_loop`` asks ``part_seam`` for the part's virtuals when its proposal has an ``app_build_id`` and applies them itself.

**Keep = one generated bundle.** ``app_bundle.compile_bundle`` -> ``lint_bundle(profile="generated")`` -> ``TemplateInstaller`` (tables,
sub-workflows published first with their tests run, entry workflows left as drafts, tokens resolved, egress hosts added) -> every part
proposal ``kept`` with its new workflow id -> ``kg_learn.on_app_kept``. There is no app-level undo.
"""

from __future__ import annotations

import asyncio
import copy
import dataclasses
import json
import logging
from typing import Any

from sqlalchemy import text

from ..engine import ai_policy, wf_ai
from ..engine.wf_ai import Meter
from ..repositories.ai_proposal_repository import AIProposalRepository
from ..repositories.app_build_repository import AppBuildRepository, new_app_build_id
from . import ai_builder, app_bundle, app_plan, kg_slots, kg_understand
from . import proposal_sandbox as sb
from .proposal_sandbox import BadState, ProposalError, ProposalNotFound

logger = logging.getLogger(__name__)

FINAL_PART = ("kept", "discarded")


# ── errors (the house envelope; the routes raise them as ``detail``) ──────────────────────────────────────────────────


class AppNotFound(ProposalNotFound):
    pass


class PlanInvalid(ProposalError):
    http_status, code = 422, "plan_invalid"

    def __init__(self, issues: list[dict[str, Any]], message: str | None = None) -> None:
        super().__init__(message or "The plan has errors; fix them first.", issues=issues)


class PartsNotReady(ProposalError):
    http_status, code = 409, "parts_not_ready"

    def __init__(self, parts: list[dict[str, Any]], message: str | None = None) -> None:
        super().__init__(message or "Every part must be built and ready before the app can be kept.", parts=parts)


class SpecNotApproved(ProposalError):
    http_status, code = 409, "spec_not_approved"

    def __init__(self, part_key: str) -> None:
        super().__init__(f"Approve the spec of '{part_key}' first (parts are built in order).", part_key=part_key)


class BundleInvalid(ProposalError):
    http_status, code = 422, "bundle_invalid"

    def __init__(self, issues: list[dict[str, Any]]) -> None:
        super().__init__("The parts could not be turned into an installable app.", issues=issues)


class InstallFailed(ProposalError):
    http_status, code = 422, "install_failed"


# ── the build seam ────────────────────────────────────────────────────────────────────────────────────────────────────

async def part_seam(proposal: dict[str, Any]) -> dict[str, Any] | None:
    """The virtual siblings and tables a part's build sees: ``{children, tables, aliases, siblings}``, or None when the proposal is not an
    app part. ``ai_builder._build_loop`` calls this for a proposal with an ``app_build_id`` and hands the result to ``apply_virtuals``."""
    app_id, key = proposal.get("app_build_id"), proposal.get("part_key")
    if not app_id or not key:
        return None
    row = await _app_row(app_id)
    plan = row["plan"]
    full = await _proposal_rows(app_id, app_plan.closure(plan, key))
    defs = {k: p.get("proposed_definition") for k, p in full.items() if p and p["status"] in ("ready", "kept")}
    children, tables = app_bundle.virtuals(plan, key, defs, row["project_id"])
    aliases = {f"@part/{d}": str(app_plan.part_of(plan, d)["reuse"]["capability"]).split(":", 1)[-1]
               for d in app_plan.closure(plan, key) if app_plan.part_of(plan, d).get("reuse")}
    if aliases:   # a reused part: the real workflow answers to @part/<key> too (its contract, as published)
        from .check_lookups import load_call_closure

        seed = {"nodes": [{"name": f"call {i}", "type": "ExecuteWorkflow", "parameters": {"workflowId": real, "version": "latest"}}
                          for i, real in enumerate(aliases.values())]}
        async with _sf()() as s:
            kids = await load_call_closure(s, proposal["team_id"], proposal["project_id"], seed)
        for alias, real in aliases.items():
            if real in kids:
                children[alias] = dataclasses.replace(kids[real], id=alias)
    return {"children": children, "tables": tables, "siblings": app_plan.siblings_of(plan, key)}


# ── small helpers ─────────────────────────────────────────────────────────────────────────────────────────────────────

_LOCKS: dict[str, asyncio.Lock] = {}


def _lock(app_id: str) -> asyncio.Lock:
    return _LOCKS.setdefault(app_id, asyncio.Lock())


def _sf():
    return ai_builder._sf()


def _j(v: Any) -> Any:
    if isinstance(v, (str, bytes)):
        try:
            return json.loads(v)
        except ValueError:
            return v
    return v


async def _app_row(app_id: str, project_id: str | None = None) -> dict[str, Any]:
    async with _sf()() as s:
        row = await AppBuildRepository(s).get(app_id, project_id)
    if row is None:
        raise AppNotFound(f"Application build not found: {app_id}")
    return row


_PART_SQL = """
SELECT DISTINCT ON (part_key) id, part_key, status, workflow_id, accepted_failing, error_code, error_message, created_at, updated_at,
       llm_calls, (report->>'passed') AS passed_text, report->'tests'->'summary' AS tests,
       CASE WHEN jsonb_typeof(report->'rounds') = 'array' THEN jsonb_array_length(report->'rounds') END AS rounds
FROM ai_proposals WHERE app_build_id = :a AND part_key IS NOT NULL
ORDER BY part_key, created_at DESC, id DESC
"""


async def _part_rows(app_id: str) -> dict[str, dict[str, Any]]:
    """The newest proposal of every part (light columns), by part key."""
    async with _sf()() as s:
        res = await s.execute(text(_PART_SQL), {"a": app_id})
        rows = [dict(m) for m in res.mappings().all()]
    out = {}
    for r in rows:
        r["tests"] = _j(r.get("tests"))
        t = r.pop("passed_text", None)
        r["passed"] = None if t is None else t == "true"
        out[r["part_key"]] = r
    return out


async def _proposal_rows(app_id: str, keys: list[str]) -> dict[str, dict[str, Any]]:
    """The full newest proposal of each part in ``keys``."""
    light = await _part_rows(app_id)
    out = {}
    async with _sf()() as s:
        repo = AIProposalRepository(s)
        for k in keys:
            if k in light:
                out[k] = await repo.get(light[k]["id"])
    return out


def _status_of(rows: dict[str, dict[str, Any]], key: str) -> str | None:
    r = rows.get(key)
    return None if r is None else r["status"]


# ── views ─────────────────────────────────────────────────────────────────────────────────────────────────────────────


def _part_views(plan: dict[str, Any] | None, rows: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for p in (plan or {}).get("parts") or []:
        r = rows.get(p["key"])
        status = "reused" if p.get("reuse") else (r["status"] if r else "pending")
        t = (r or {}).get("tests") or {}
        out.append({
            "key": p["key"], "title": p["title"], "role": p["role"], "trigger": p["trigger"], "concepts": p.get("concepts") or [],
            "depends_on": p.get("depends_on") or [], "interface": p.get("interface"), "cron": p.get("cron"), "reuse": p.get("reuse"),
            "status": status, "proposal_id": (r or {}).get("id"), "workflow_id": (r or {}).get("workflow_id"),
            "passed": (r or {}).get("passed"), "accepted_failing": bool((r or {}).get("accepted_failing")),
            "tests": ({"passed": t.get("passed"), "total": t.get("total")} if isinstance(t, dict) and t else None),
            "rounds": (r or {}).get("rounds"), "error": ((r or {}).get("error_message") if (r or {}).get("status") == "failed" else None),
        })
    return out


def _blockers(app: dict[str, Any], plan: dict[str, Any] | None, rows: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    if not plan:
        return [{"reason": "no_plan"}]
    out = []
    for k in app_plan.build_order(plan):
        st = _status_of(rows, k)
        if st != "ready":
            out.append({"part_key": k, "status": st or "pending", "reason": "not_ready"})
        elif rows[k].get("passed") is False:
            out.append({"part_key": k, "status": st, "reason": "tests_failing"})
    return out


async def _installed(app: dict[str, Any], plan: dict[str, Any] | None) -> dict[str, Any] | None:
    from ..db.models import ProjectModel, TemplateInstallModel

    if not app.get("install_id"):
        return None
    async with _sf()() as s:
        inst = await s.get(TemplateInstallModel, app["install_id"])
        project = await s.get(ProjectModel, app["project_id"])
    if inst is None:
        return None
    m = inst.mapping or {}
    pslug = getattr(project, "slug", None) or app["project_id"]
    by_key = {p["key"]: p for p in (plan or {}).get("parts") or []}
    interfaces = {}
    for k, slug in (m.get("slugs") or {}).items():
        interfaces[k] = {"slug": slug, "kind": ((by_key.get(k) or {}).get("interface") or {}).get("kind"), "path": f"/i/{pslug}/{slug}"}
    roles = m.get("roles") or {}
    return {"install_id": inst.id, "workflows": dict(m.get("workflows") or {}),
            "tables": {k: {"id": v, "name": (m.get("table_names") or {}).get(k)} for k, v in (m.get("tables") or {}).items()},
            "published": sorted(k for k, r in roles.items() if r == "subworkflow"), "drafts": sorted(k for k, r in roles.items() if r == "entry"),
            "interfaces": interfaces, "renamed": list(m.get("renamed") or []), "needs": list(inst.needs or [])}


async def view(app: dict[str, Any], *, rows: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """``AppBuildView``: the row, the open questions, the plan with its issues, and one status per part."""
    rows = await _part_rows(app["id"]) if rows is None else rows
    plan = app.get("plan")
    u = app.get("understanding") or None
    parts = _part_views(plan, rows)
    order = app_plan.build_order(plan) if plan else []
    blockers = _blockers(app, plan, rows) if app["status"] in ("specs", "building", "ready") else []
    nxt = next((k for k in order if _status_of(rows, k) not in ("ready", "kept", "failed", "discarded")), None)
    out = {
        "id": app["id"], "project_id": app["project_id"], "status": app["status"], "actor": app["actor"], "request": app["request"],
        "understanding": u, "summary": (u or {}).get("summary"), "questions": kg_understand.open_questions(u),
        "plan": plan, "plan_issues": app_plan.validate_app_plan(plan) if plan else [], "adapted_from": (plan or {}).get("adapted_from"),
        "parts": parts, "next_part": nxt if app["status"] in ("specs", "building") else None,
        "can_keep": app["status"] == "ready" and not [b for b in blockers if b["reason"] == "not_ready"],
        "keep_blockers": blockers,
        "install_id": app.get("install_id"), "installed": await _installed(app, plan) if app["status"] == "kept" else None,
        "kept_by": app.get("kept_by"), "kept_at": app.get("kept_at"), "created_at": app.get("created_at"), "updated_at": app.get("updated_at"),
    }
    return ai_builder._jsonable(out)


async def get_view(app_id: str, project_id: str | None = None) -> dict[str, Any]:
    return await view(await _app_row(app_id, project_id))


async def list_apps(project_id: str, *, status: str | None = None, limit: int = 50, cursor: str | None = None) -> dict[str, Any]:
    from .project_service import resolve_project

    async with _sf()() as s:
        project = await resolve_project(s, project_id)
        if project is None:
            raise ProposalNotFound(f"Project not found: {project_id}")
        rows, nxt = await AppBuildRepository(s).list(project.id, status=status, limit=limit, cursor=cursor)
        titles = {}
        if rows:
            res = await s.execute(text("SELECT id, plan->>'title' AS title FROM app_builds WHERE id = ANY(:ids)"), {"ids": [r["id"] for r in rows]})
            titles = {m["id"]: m["title"] for m in res.mappings().all()}
    items = [{**{k: r.get(k) for k in ("id", "status", "actor", "install_id", "kept_by", "kept_at", "created_at", "updated_at")}, "title": titles.get(r["id"])}
             for r in rows]
    return ai_builder._jsonable({"apps": items, "next_cursor": nxt})


# ── understand and plan ───────────────────────────────────────────────────────────────────────────────────────────────


async def _understand(ctx: Any, request: str, *, answers: dict[str, Any], use_llm: bool, prior: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = wf_ai.config()
    model = cfg.model
    if use_llm:
        try:
            ai_builder.check_model(model)
        except ProposalError:
            use_llm = False   # no model: keyword matching only, never a failure
    meter = Meter(max_calls=cfg.max_calls, case=ctx.project_id, secret_values=ctx.secrets)
    return await ai_builder._understand(ctx, request, answers=answers, use_llm=use_llm, meter=meter, model=model, prior=prior)


def _callable_keys(sl: Any, project_id: str) -> set[str]:
    out = set()
    for n in sl.nodes.values():
        p = n.get("props") or {}
        if n["kind"] == "capability" and n.get("subkind") in ("subworkflow", "workflow") and n.get("project_id") == project_id \
                and p.get("callable") and p.get("published_version"):
            out.add(n["key"])
    return out


async def _make_plan(ctx: Any, understanding: dict[str, Any], request: str) -> dict[str, Any]:
    sl = await ai_builder._load_slice(ctx)
    return app_plan.plan_app(understanding, sl, request=request)


# ── the flow ──────────────────────────────────────────────────────────────────────────────────────────────────────────


async def create(project_ref: str, request: str, actor: str, *, answers: dict[str, Any] | None = None, use_llm: bool = True) -> dict[str, Any]:
    """``POST /api/ai/apps``: Understand the request (forced to the app shape), then ``clarify`` while a required slot is open, else
    ``planned`` with the plan. No part is drafted yet."""
    actor = ai_builder.clean_actor(actor)
    if not (request or "").strip():
        raise ai_builder.RequestInvalid("Describe the application.")
    if len(request) > ai_builder.MAX_REQUEST_CHARS:
        raise ai_builder.RequestInvalid(f"The request is longer than {ai_builder.MAX_REQUEST_CHARS} characters.")
    ctx = await ai_builder.load_ctx(project_ref)
    clean, _counts = ai_policy.scrub_request(request, ctx.secrets)
    given = {"q_shape": "app", **(answers or {})}
    u = await _understand(ctx, clean, answers=given, use_llm=use_llm)
    waiting = bool(kg_understand.open_questions(u))
    plan = None if waiting else await _make_plan(ctx, u, clean)
    aid = new_app_build_id()
    async with _sf()() as s:
        await AppBuildRepository(s).insert({
            "id": aid, "project_id": ctx.project_id, "team_id": ctx.team_id, "actor": actor, "request": clean, "request_sha256": ai_builder._sha(request),
            "status": "clarify" if waiting else "planned", "understanding": u, "answers": given, "plan": plan})
        await s.commit()
    return await get_view(aid)


async def answer(app_id: str, answers: dict[str, Any], actor: str, project_id: str | None = None) -> dict[str, Any]:
    """``POST /api/ai/apps/{id}/answers``: validate the answers (422 ``answer_invalid`` with ``qid``), re-run Understand without a second
    model call, and plan. Only while ``clarify``."""
    ai_builder.clean_actor(actor)
    async with _lock(app_id):
        row = await _app_row(app_id, project_id)
        if row["status"] != "clarify":
            raise BadState(row["status"], "This application is not waiting for answers.")
        ctx = await ai_builder.load_ctx(row["project_id"])
        sl = await ai_builder._load_slice(ctx)
        prev = row.get("understanding") or {}
        try:
            clean = kg_understand.check_answers(prev, answers or {}, sl)
        except kg_slots.AnswerInvalid as e:
            raise ai_builder.AnswerInvalid(getattr(e, "qid", ""), str(e)) from None
        merged = {**(row.get("answers") or {}), **{q["id"]: q["answer"] for q in prev.get("questions") or [] if q.get("answer") is not None}, **clean}
        u = await _understand(ctx, row["request"], answers=merged, use_llm=False, prior=prev)
        waiting = bool(kg_understand.open_questions(u))
        plan = None if waiting else app_plan.plan_app(u, sl, request=row["request"])
        async with _sf()() as s:
            won = await AppBuildRepository(s).transition(app_id, ["clarify"], "clarify" if waiting else "planned",
                                                         {"understanding": u, "answers": merged, "plan": plan})
            if won is None:
                await s.rollback()
                raise BadState((await AppBuildRepository(s).get(app_id))["status"], "This application is not waiting for answers.")
            await s.commit()
    return await get_view(app_id)


async def put_plan(app_id: str, plan: dict[str, Any], actor: str, project_id: str | None = None) -> dict[str, Any]:
    """``PUT /api/ai/apps/{id}/plan``: store an edited plan after APP001-APP008, no model call. Only while ``planned``."""
    ai_builder.clean_actor(actor)
    async with _lock(app_id):
        row = await _app_row(app_id, project_id)
        if row["status"] != "planned":
            raise BadState(row["status"], "The plan can only be edited before the specs are drafted.")
        ctx = await ai_builder.load_ctx(row["project_id"])
        issues = app_plan.validate_app_plan(plan, callable_keys=_callable_keys(await ai_builder._load_slice(ctx), row["project_id"]))
        if app_plan.errors_of(issues):
            raise PlanInvalid(issues)
        plan = copy.deepcopy(plan)
        plan.setdefault("tables", [])
        plan.setdefault("adapted_from", (row.get("plan") or {}).get("adapted_from"))
        plan["screens"] = plan.get("screens") or app_plan._screens(plan)
        async with _sf()() as s:
            won = await AppBuildRepository(s).update_fields(app_id, {"plan": plan}, only_in=["planned"])
            if won is None:
                await s.rollback()
                raise BadState((await AppBuildRepository(s).get(app_id))["status"], "The plan can only be edited before the specs are drafted.")
            await s.commit()
    return await get_view(app_id)


async def approve_plan(app_id: str, actor: str, project_id: str | None = None) -> dict[str, Any]:
    """``POST /api/ai/apps/{id}/approve-plan``: one ``spec_draft`` proposal per part to build, drafted children first with the normal spec
    call. A part that fails leaves the app ``planned``; calling again continues with the parts that have no proposal yet."""
    actor = ai_builder.clean_actor(actor)
    async with _lock(app_id):
        row = await _app_row(app_id, project_id)
        if row["status"] == "specs":
            return await view(row)
        if row["status"] != "planned":
            raise BadState(row["status"], "Only a planned application can have its plan approved.")
        plan = row["plan"]
        ctx = await ai_builder.load_ctx(row["project_id"])
        issues = app_plan.validate_app_plan(plan, callable_keys=_callable_keys(await ai_builder._load_slice(ctx), row["project_id"]))
        if app_plan.errors_of(issues):
            raise PlanInvalid(issues)
        have = await _part_rows(app_id)
        confirmed = [c["key"] for c in (row.get("understanding") or {}).get("concepts") or [] if c.get("confirmed")]
        for key in app_plan.build_order(plan):
            if have.get(key) and have[key]["status"] not in ("failed", "discarded"):
                continue
            children, tables = app_bundle.virtuals(plan, key, {}, row["project_id"])
            app_part = {"app_build_id": app_id, "part_key": key, "part": app_plan.part_brief(plan, key), "siblings": app_plan.siblings_of(plan, key),
                        "virtual_children": children, "virtual_tables": tables}
            focus = app_plan.focus_concepts(plan, key, confirmed)   # the part is specified against its own concepts only
            part_answers = {**(row.get("answers") or {}), "concepts_off": [c for c in confirmed if c not in focus]}
            await ai_builder.draft_spec(row["project_id"], row["request"], actor, use_graph=True, clarify=False, answers=part_answers, app_part=app_part)
        async with _sf()() as s:
            won = await AppBuildRepository(s).transition(app_id, ["planned"], "specs")
            if won is None:
                await s.rollback()
            await s.commit()
    return await get_view(app_id)


async def build_next(app_id: str, actor: str, project_id: str | None = None) -> dict[str, Any]:
    """``POST /api/ai/apps/{id}/build-next``: build the next part (dependency order, one per call) with the unchanged build loop. The part's
    spec must be approved (409 ``spec_not_approved``). The app becomes ``ready`` when every part is. Returns the view plus ``built``."""
    actor = ai_builder.clean_actor(actor)
    async with _lock(app_id):
        row = await _app_row(app_id, project_id)
        if row["status"] == "ready":
            return {**await view(row), "built": None}
        if row["status"] not in ("specs", "building"):
            raise BadState(row["status"], "Approve the plan and the specs before building.")
        plan = row["plan"]
        order = app_plan.build_order(plan)
        rows = await _part_rows(app_id)
        target = None
        for k in order:
            st = _status_of(rows, k)
            if st in ("ready", "kept", "failed", "discarded"):
                continue
            if st == "spec_approved":
                target = k
                break
            if st == "spec_draft":
                raise SpecNotApproved(k)
            raise BadState(st or "pending", f"'{k}' is {st or 'not drafted'}; it cannot be built now.")
        built_info = None
        if target is not None:
            async with _sf()() as s:
                await AppBuildRepository(s).transition(app_id, ["specs", "building"], "building")
                await s.commit()
            pv = await ai_builder.build(rows[target]["id"], actor, row["project_id"])
            built_info = {"part_key": target, "proposal_id": pv["id"], "status": pv["status"], "passed": (pv.get("report") or {}).get("passed")}
        rows = await _part_rows(app_id)
        if all(_status_of(rows, k) in ("ready", "kept") for k in order):
            async with _sf()() as s:
                await AppBuildRepository(s).transition(app_id, ["specs", "building"], "ready")
                await s.commit()
    return {**await get_view(app_id), "built": built_info}


async def keep(app_id: str, actor: str, *, accept_failing: bool = False, project_id: str | None = None) -> dict[str, Any]:
    """``POST /api/ai/apps/{id}/keep``: install the whole app as one generated bundle, mark the parts kept, learn from it.

    409 ``parts_not_ready`` while a part is not ``ready``; 409 ``tests_failing`` when an approved test is red (unless ``accept_failing``);
    422 ``bundle_invalid`` / ``install_failed`` (nothing is left behind: the installer undoes a failed install)."""
    from ..db.models import ProjectModel
    from ..schemas.template import InstallRequest
    from .template_bundle import TemplateBundleError
    from .template_installer import TemplateError, TemplateInstaller

    actor = ai_builder.clean_actor(actor)
    async with _lock(app_id):
        row = await _app_row(app_id, project_id)
        if row["status"] == "kept":
            return await view(row)
        plan = row.get("plan")
        if row["status"] in ("specs", "building"):
            raise PartsNotReady(_blockers(row, plan, await _part_rows(app_id)))
        if row["status"] != "ready":
            raise BadState(row["status"], "Only a built application can be kept.")
        order = app_plan.build_order(plan)
        props = await _proposal_rows(app_id, order)
        bad = [{"part_key": k, "status": (props.get(k) or {}).get("status") or "pending"} for k in order if (props.get(k) or {}).get("status") != "ready"]
        if bad:
            raise PartsNotReady(bad)
        red = [k for k in order if (props[k].get("report") or {}).get("passed") is False]
        if red and not accept_failing:
            raise sb.TestsFailing("The tests are failing. Keep the app anyway only with accept_failing.", parts=red)
        for k in order:   # the approved tests are exactly what was approved, and still in the result (the Stage 6 gate, per part)
            p = props[k]
            tests = (p.get("spec") or {}).get("tests") or []
            if p.get("spec_fingerprint") and (sb.fingerprint(tests) != p["spec_fingerprint"] or sb.tampered_tests(tests, p.get("proposed_definition"))):
                raise sb.LockedTests([t["id"] for t in sb.approved_tests(tests)], f"The approved tests of '{k}' no longer match what was approved.")
        try:
            raw = app_bundle.compile_bundle(app_id, plan, row["request"], {k: props[k]["proposed_definition"] for k in order})
            lt = app_bundle.loaded_template(raw)
        except TemplateBundleError as e:
            raise BundleInvalid([i.as_dict() for i in e.issues]) from None
        async with _sf()() as s:
            project = await s.get(ProjectModel, row["project_id"])
            try:
                _status, body = await TemplateInstaller(s, catalog={lt.bundle.slug: lt}).install(
                    project, lt.bundle.slug, InstallRequest(clash="rename", run_tests=True), actor)
            except TemplateError as e:
                err = InstallFailed(e.message, install_code=e.code, **{k: v for k, v in e.extra.items() if k != "message"})
                err.http_status = 409 if e.status == 409 else 422
                raise err from None
        wf_ids = {w["key"]: w["id"] for w in body["workflows"]}
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        async with _sf()() as s:
            prepo = AIProposalRepository(s)
            for k in order:
                won = await prepo.transition(props[k]["id"], ["ready"], "kept", {"kept_by": actor, "kept_at": now, "accepted_failing": bool(accept_failing),
                                                                                "workflow_id": wf_ids[k]})
                if won is not None:
                    await prepo.add_event(props[k]["id"], "kept", {"actor": actor, "workflow_id": wf_ids[k], "accepted_failing": bool(accept_failing),
                                                                  "app_build_id": app_id}, actor)
            arepo = AppBuildRepository(s)
            won = await arepo.transition(app_id, ["ready"], "kept", {"install_id": body["install_id"], "bundle": raw, "kept_by": actor, "kept_at": now})
            if won is None:
                await s.rollback()
                raise BadState((await arepo.get(app_id))["status"])
            await s.commit()
    learned = None
    try:
        from . import kg_learn

        res = await kg_learn.on_app_kept(app_id)
        learned = res.payload() if res is not None else None
    except Exception:  # noqa: BLE001 - learning is best effort; the app is installed either way
        logger.warning("learning from app %s failed", app_id, exc_info=True)
    out = await get_view(app_id)
    out["learned"] = learned
    out["tests"] = body.get("tests")
    return out


async def discard(app_id: str, actor: str, project_id: str | None = None) -> dict[str, Any]:
    """``POST /api/ai/apps/{id}/discard``: every part proposal is discarded; nothing was saved to the project, so nothing is undone.
    A part that is being built right now (409) must finish first. A kept app cannot be discarded."""
    actor = ai_builder.clean_actor(actor)
    async with _lock(app_id):
        row = await _app_row(app_id, project_id)
        if row["status"] == "discarded":
            return await view(row)
        if row["status"] == "kept":
            raise BadState("kept", "A kept application is installed; it can no longer be discarded.")
        async with _sf()() as s:
            res = await s.execute(text("SELECT id, status FROM ai_proposals WHERE app_build_id = :a"), {"a": app_id})
            props = [dict(m) for m in res.mappings().all()]
        if any(p["status"] == "building" for p in props):
            raise BadState("building", "A part is being built right now; wait for it to finish.")
        for p in props:
            if p["status"] in ("kept", "discarded", "undone", "answered"):
                continue
            try:
                await ai_builder.discard(p["id"], actor, row["project_id"])
            except ProposalError:
                logger.info("part proposal %s could not be discarded (%s)", p["id"], p["status"])
        async with _sf()() as s:
            await AppBuildRepository(s).transition(app_id, ["clarify", "planned", "specs", "building", "ready", "failed"], "discarded")
            await s.commit()
    return await get_view(app_id)
