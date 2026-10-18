"""Operational memory: what a Keep teaches the graph (PLAN-CAPABILITY-GRAPH section 2.3; CONTRACTS-CAPGRAPH.md section 8).

Decision D2: a result is *validated* when it is kept with ``report.passed == true`` and ``accepted_failing == false``. Only validated
results create or strengthen patterns; a Keep with ``accept_failing`` still upserts the workflow capability but writes no pattern and
records its learn event as unvalidated. Decision D1: patterns are written ``scope='global', visibility='org'`` as scrubbed skeletons.

A pattern either matches (same skeleton hash) or it does not: ``validated_count`` is a plain count of validated Keeps, and the
evidence list says who. Identical skeletons merge, whatever the entity, table, role or duration names were.

Both entry points are best-effort: they open their own session, never raise, log a failure and record it as a learn event.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..repositories import kg_repository as kr
from ..repositories.kg_repository import KgRepository
from . import kg_signatures

logger = logging.getLogger(__name__)

IMPLEMENTED = True

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
_URL = re.compile(r"""https?://[^\s"'<>)\]]+""")
_ROLE_LIT = re.compile(r"role:[a-z][a-z0-9_]*")
_ROLE_FULL = re.compile(r"^role:[a-z][a-z0-9_]*$")


@dataclass
class LearnResult:
    validated: bool
    pattern_key: str | None = None
    validated_count: int = 0
    nodes_added: int = 0
    edges_added: int = 0
    event_id: int | None = None
    summary: str = ""
    errors: list[str] = field(default_factory=list)

    def payload(self) -> dict[str, Any]:
        """The ``learned`` event payload (CONTRACTS-CAPGRAPH.md section 7.4)."""
        return {"pattern_key": self.pattern_key, "validated_count": self.validated_count,
                "nodes_added": self.nodes_added, "edges_added": self.edges_added}


# ── scrubbing ─────────────────────────────────────────────────────────────────────────────────────────────────────────


def _hosts_of(obj: Any) -> set[str]:
    from urllib.parse import urlparse

    hosts: set[str] = set()

    def walk(x: Any, key: str = "") -> None:
        if isinstance(x, Mapping):
            for k, v in x.items():
                walk(v, str(k))
        elif isinstance(x, list):
            for v in x:
                walk(v, key)
        elif isinstance(x, str):
            for u in _URL.findall(x):
                if (h := urlparse(u).hostname):
                    hosts.add(h)
            if key in ("egress_hosts", "host", "hosts", "base_url"):
                h = urlparse(x).hostname or x.strip().split("/")[0]
                if h and "." in h:
                    hosts.add(h)

    walk(obj)
    return hosts


def scrub_bundle(bundle: Mapping[str, Any], *, slots: Mapping[str, str] | None = None) -> dict[str, Any]:
    """A reference bundle that is safe to show another team (decision D1): no secret, host, URL, email or literal person.

    * secrets and card/IBAN-like values are removed (``ai_policy.scrub_secrets`` / ``scrub_pii`` plus the bundle secret lints);
    * every host (``requires.egress_hosts``, API ``base_url``, hosts inside URLs) becomes ``@cap:host`` / ``@cap:http``;
    * ``role:x`` becomes ``@slot:<slot id>`` (``slots`` maps a role to its slot id; otherwise ``@slot:role``);
    * Approval assignees that are neither a role nor an expression become ``@slot:person``; emails become ``@slot:email``.
    ``@tpl:`` tokens are kept.
    """
    from ..engine import ai_policy
    from .template_bundle import secret_findings

    slots = dict(slots or {})
    b, _n = ai_policy.scrub_secrets(copy.deepcopy(dict(bundle)))
    hosts = sorted(_hosts_of(b), key=len, reverse=True)

    def clean(s: str) -> str:
        if s.startswith("@tpl:"):
            return s
        s = _URL.sub("@cap:http", s)
        for h in hosts:
            s = s.replace(h, "@cap:host")
        s = _EMAIL.sub("@slot:email", s)
        s = _ROLE_LIT.sub(lambda m: "@slot:" + slots.get(m.group(0), "role"), s)
        s, _c = ai_policy.scrub_pii(s)
        if secret_findings(s):
            return "@slot:redacted"
        return s

    def walk(x: Any, key: str = "") -> Any:
        if isinstance(x, dict):
            out: dict[str, Any] = {}
            for k, v in x.items():
                if k in ("approved_by", "approved_at", "created_by", "updated_by", "installed_by"):
                    continue
                if k == "egress_hosts":
                    out[k] = []
                elif k == "base_url" and isinstance(v, str):
                    out[k] = "@cap:api"
                elif k == "assignees" and isinstance(v, list):
                    out[k] = [walk(a) if isinstance(a, str) and (_ROLE_FULL.match(a) or "{{" in a)
                              else "@slot:person" for a in v if isinstance(a, (str, int))]
                else:
                    out[k] = walk(v, str(k))
            return out
        if isinstance(x, list):
            return [walk(v, key) for v in x]
        if isinstance(x, str):
            return clean(x)
        return x

    return walk(b)


# ── skeleton ──────────────────────────────────────────────────────────────────────────────────────────────────────────


def _tok(s: str, entity: str | None) -> str:
    if entity:
        return re.sub(rf"(?<![a-z0-9]){re.escape(entity)}s?(?![a-z0-9])", "{entity}", s, flags=re.I)
    return s


def skeleton_hash(skeleton: Mapping[str, Any], *, salt: str | None = None) -> str:
    """sha256 of the canonical skeleton (sorted keys, no whitespace)."""
    body = json.dumps(skeleton, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(((salt + "\n" + body) if salt else body).encode()).hexdigest()


def skeleton_from_parts(
    parts: list[Mapping[str, Any]], *, concepts: list[dict[str, Any]] | None = None, entity: str | None = None,
    extra_concepts: Iterable[str] = (), slot_values: Mapping[str, Any] | None = None, tables: list[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """``parts`` are ``{key, role, definition}``. Returns ``{skeleton, concepts, slots, role_slots}``.

    The skeleton holds structure only: step types, the concept a step carries, and slot *tokens* (``{sla.due_in}``) where the
    definition had a value, so the same app with a renamed entity, other roles and another duration hashes the same."""
    detections_by_part: list[list[kg_signatures.Detection]] = []
    slots: dict[str, Any] = {}
    all_concepts: set[str] = set(extra_concepts)
    entity_tables: set[str] = set()
    for p in parts:
        dets = kg_signatures.detect(p["definition"], concepts=concepts)
        detections_by_part.append(dets)
        for d in dets:
            all_concepts.add(d["concept"])
            for k, v in d["slots"].items():
                slots.setdefault(k, v)
            if d["concept"] == "concept:exception_case":
                for n in p["definition"].get("nodes") or []:
                    if n.get("name") in d["evidence"]["nodes"] and isinstance((n.get("parameters") or {}).get("table"), str):
                        entity_tables.add(n["parameters"]["table"])
    for k, v in (slot_values or {}).items():
        slots[k] = v
    entity = entity or slots.get("exception_case.entity") or None
    if isinstance(entity, str) and entity:
        slots["exception_case.entity"] = entity
    else:
        entity = None
    # Several slots can hold the same role (the escalation target is also the report recipient). Keep them all and pick one per role
    # deterministically, so the skeleton depends on the slot structure and not on which role values happen to be equal: an
    # Approval's assignee is never a ``report.*`` slot when another slot holds that role, then the first slot by name.
    slots_by_role: dict[str, list[str]] = {}
    for k, v in sorted(slots.items()):
        if isinstance(v, str) and _ROLE_FULL.match(v):
            slots_by_role.setdefault(v, []).append(k)
    role_slots = {r: next((k for k in ks if not k.startswith("report.")), ks[0]) for r, ks in slots_by_role.items()}
    table_ids: dict[str, str] = {}

    def table_token(name: str) -> str:
        if name in entity_tables:
            return "{entity}"
        return table_ids.setdefault(name, "{table_%d}" % (len(table_ids) + 1))

    out_parts = []
    for p, dets in zip(parts, detections_by_part):
        defn = p["definition"]
        carried: dict[str, str] = {}
        for d in dets:
            for n in d["evidence"]["nodes"]:
                carried.setdefault(n, d["concept"])
        steps = []
        trigger = None
        for n in kg_signatures.ordered_nodes(defn):
            t = n.get("type")
            params = n.get("parameters") or {}
            step: dict[str, Any] = {"node_type": t}
            if n["name"] in carried:
                step["concept"] = carried[n["name"]]
            tp: dict[str, Any] = {}
            if t == "Approval":
                ass = params.get("assignees") or []
                if ass:
                    tp["assignees"] = ["{" + role_slots[a] + "}" if a in role_slots else ("{role}" if _ROLE_FULL.match(str(a)) else "{assignee}")
                                       for a in ass if isinstance(a, (str, int))]
                if params.get("due_in"):
                    tp["due_in"] = "{sla.due_in}"
            elif t == "Cron" and params.get("cronExpression"):
                tp["cronExpression"] = "{report.schedule}" if "concept:report" in all_concepts else "{schedule.cron}"
            elif t == "Switch" and (params.get("field") or params.get("rules")):
                tp["field"] = "{assignment.by_field}"
            elif t == "DataTable" and isinstance(params.get("table"), str) and params["table"]:
                tp["table"] = table_token(params["table"]) if "{{" not in params["table"] else "{expression}"
                tp["operation"] = params.get("operation") or "list"
            elif t == "SendEmail" and params.get("toEmail"):
                tp["toEmail"] = "{report.recipients_role}" if _ROLE_FULL.match(str(params["toEmail"])) else "{recipient}"
            elif t == "ExecuteWorkflow":
                tp["workflowId"] = "{subworkflow}"
            elif t == "HttpRequest" and params.get("api_ref"):
                tp["api_ref"] = "{api}"
            if tp:
                step["params"] = tp
            if trigger is None and t in ("FormTrigger", "Webhook", "Cron", "Start", "ChatInput", "ExecuteWorkflowTrigger"):
                trigger = {"FormTrigger": "form", "Webhook": "webhook", "Cron": "cron", "Start": "manual", "ChatInput": "chat",
                           "ExecuteWorkflowTrigger": "execute"}[t]
            steps.append(step)
        part_concepts = sorted({d["concept"] for d in dets} | set(p.get("concepts") or []))
        out_parts.append({"key": _tok(str(p.get("key") or "main"), entity), "role": p.get("role") or "entry", "trigger": trigger,
                          "steps": steps, "concepts": part_concepts,
                          "rules": concept_obligations(concepts, part_concepts) if concepts is not None else []})
    skeleton: dict[str, Any] = {"parts": out_parts}
    if tables is not None:
        skeleton["tables"] = [{"key": _tok(str(t.get("key") or t.get("name") or "t"), entity),
                               "columns": [(c.get("name") if isinstance(c, Mapping) else str(c)) for c in t.get("columns") or []]}
                              for t in tables]
    else:
        skeleton["tables"] = [{"key": v} for v in ([("{entity}" if entity_tables else None)] if entity_tables else []) + sorted(table_ids.values())]
    return {"skeleton": skeleton, "concepts": sorted(all_concepts), "slots": slots, "role_slots": role_slots}


def concept_obligations(concepts: list[dict[str, Any]], keys: Iterable[str]) -> list[str]:
    by = {c["key"]: c for c in concepts}
    out: list[str] = []
    for k in sorted(keys):
        c = by.get(k, {})
        for o in (c.get("props") or c).get("test_obligations") or []:
            if o not in out:
                out.append(o)
    return out


# ── shared learning steps ─────────────────────────────────────────────────────────────────────────────────────────────


async def _api_index(session: AsyncSession, project_id: str) -> dict[tuple[str, str], str]:
    rows = (await session.execute(text(
        "SELECT key, props FROM kg_nodes WHERE project_id = :p AND subkind = 'api_operation' AND status = 'active'"), {"p": project_id})).all()
    out: dict[tuple[str, str], str] = {}
    for key, props in rows:
        props = json.loads(props) if isinstance(props, (str, bytes)) else (props or {})
        p = props.get("api_ref_param") or {}
        if p.get("spec_id") and p.get("operation_id"):
            out[(p["spec_id"], p["operation_id"])] = key.split(":", 1)[1]
    return out


def _test_counts(row: Mapping[str, Any]) -> dict[str, int]:
    """Approved and passed test counts of one proposal row. A proposal's ``report.tests`` holds the counts either flat or under
    ``summary`` (the test runner's shape, which is what composed parts store)."""
    t = (row.get("report") or {}).get("tests") or {}
    if not isinstance(t, Mapping):
        return {"approved": 0, "passed": 0}
    if isinstance(t.get("summary"), Mapping):
        t = {**t, **t["summary"]}
    return {"approved": int(t.get("approved_total") or 0), "passed": int(t.get("passed") or 0)}


def _is_validated(row: Mapping[str, Any]) -> bool:
    return row.get("status") == "kept" and (row.get("report") or {}).get("passed") is True and not row.get("accepted_failing")


def _confirmed(understanding: Mapping[str, Any] | None) -> list[str]:
    return [c["key"] for c in (understanding or {}).get("concepts") or [] if isinstance(c, Mapping) and c.get("confirmed") and c.get("key")]


async def _escalations(w: Any, defn: Mapping[str, Any]) -> int:
    """Approval with a ``timed_out`` port wired to a second Approval: role_a escalates_to role_b after due_in."""
    by_name = {n["name"]: n for n in defn.get("nodes") or [] if n.get("name")}
    n_edges = 0
    for n in by_name.values():
        if n.get("type") != "Approval":
            continue
        a = kg_signatures._role_of(n)
        due = (n.get("parameters") or {}).get("due_in")
        for c in defn.get("connections") or []:
            if c.get("source_node") == n["name"] and c.get("source_output") == "timed_out" and c.get("target_node") in by_name:
                tgt = by_name[c["target_node"]]
                b = kg_signatures._role_of(tgt) if tgt.get("type") == "Approval" else None
                if a and b and a != b and a in w.ids and b in w.ids:
                    before = w.edges_added
                    await w.edge(w.ids[a], "escalates_to", w.ids[b], props={"after": str(due or "")}, source="learned")
                    n_edges += w.edges_added - before
    return n_edges


async def _upsert_pattern(
    session: AsyncSession, w: Any, *, shape: str, label: str, sk: Mapping[str, Any], bundle: Mapping[str, Any], project: Any,
    evidence: dict[str, Any], source_ref: dict[str, Any],
) -> tuple[dict[str, Any], int, bool]:
    """Create the pattern (validated 1) or strengthen the one with the same skeleton hash. Returns ``(node, count, was_new)``.

    The same evidence key (proposal or app build) never counts twice."""
    from ..engine import ai_policy

    key = kr.pattern_key(skeleton_hash(sk["skeleton"]))
    existing = await w.repo.get_node_by_key(key, scope="global")
    if existing is not None:
        ev = list((existing["props"] or {}).get("evidence") or [])
        if any(e.get("ref") == evidence["ref"] for e in ev):
            return existing, existing["validated_count"], False
        ev.append(evidence)
        node = await w.repo.bump_validated(existing["id"], props_patch={"evidence": ev})
        return node, node["validated_count"], False
    props = {"shape": shape, "concepts": sk["concepts"], "slots": sk["slots"], "skeleton": sk["skeleton"], "reference_bundle": bundle,
             "source": source_ref, "evidence": [evidence]}
    props, _ = ai_policy.scrub_secrets(props)
    res = await w.repo.upsert_node(key=key, kind="pattern", subkind=shape, scope="global", visibility="org", source="learned",
                                   label=label[:120], summary=f"Validated {shape} pattern", props=props)
    w.report.added += 1
    w.nodes_added += 1
    node = await w.repo.bump_validated(res.node["id"])
    return node, node["validated_count"], True


def _label(s: str, fallback: str) -> str:
    from ..engine import ai_policy

    clean, _ = ai_policy.scrub_pii(str(s or fallback))
    return clean.strip()[:120] or fallback


async def _fail(proposal_id: str, trigger: str, project_id: str | None, exc: Exception) -> None:
    try:
        from ..db import session as dbs

        async with dbs.async_session_factory() as s:
            await KgRepository(s).add_learn_event(trigger=trigger, project_id=project_id, proposal_id=proposal_id,
                                                  summary=f"Learning failed: {type(exc).__name__}", payload={"error": str(exc)[:300]})
            await s.commit()
    except Exception:  # noqa: BLE001
        logger.warning("could not record the learning failure", exc_info=True)


# ── entry points ──────────────────────────────────────────────────────────────────────────────────────────────────────


async def on_proposal_kept(proposal_id: str) -> LearnResult | None:
    """Run after the Keep transaction commits, in its own session (``src.db.session.async_session_factory``, looked up at call
    time). Best-effort: a failure is logged and recorded as a learn event and never fails the Keep. Returns None when nothing was
    learned."""
    project_id = None
    try:
        from ..db import session as dbs

        async with dbs.async_session_factory() as s:
            row = await _proposal_row(s, proposal_id)
            project_id = row["project_id"] if row else None
            res = await _learn_proposal(s, row) if row else None
            await s.commit()
            return res
    except Exception as e:  # noqa: BLE001
        logger.warning("learning from proposal %s failed", proposal_id, exc_info=True)
        await _fail(proposal_id, "kept_workflow", project_id, e)
        return None


async def _proposal_row(session: AsyncSession, proposal_id: str) -> dict[str, Any] | None:
    from ..repositories.ai_proposal_repository import AIProposalRepository

    row = await AIProposalRepository(session).get(proposal_id)
    return row if row and row.get("status") == "kept" and row.get("workflow_id") else None


async def _learn_proposal(session: AsyncSession, row: dict[str, Any]) -> LearnResult | None:
    from ..db.models import ProjectModel, WorkflowModel
    from . import kg_extract

    wf = await session.get(WorkflowModel, row["workflow_id"])
    project = await session.get(ProjectModel, row["project_id"])
    if wf is None or project is None:
        return None
    w = await kg_extract.ensure_seeded(session)
    concepts = await kg_extract.concept_rows(session)
    defn = wf.draft_definition or row.get("proposed_definition") or {}
    io = None
    try:
        from .subworkflow_api import SubworkflowApi

        async with session.begin_nested():
            io_full = await SubworkflowApi(session).workflow_io(wf.id, None)
        io = {"inputs": io_full.get("input_fields"), "output_fields": io_full.get("output_fields"), "callable": io_full.get("callable")}
    except Exception:  # noqa: BLE001 - the capability is still recorded, without its contract
        logger.info("no io contract for %s", wf.id, exc_info=True)
    await kg_extract.workflow_capability(session, w, project, wf, io=io, concepts=concepts, api_index=await _api_index(session, project.id))
    wf_node = await w.repo.get_node_by_key(kr.workflow_key(wf.id), scope="project", project_id=project.id)
    understanding = row.get("understanding")
    detected = kg_signatures.detect(defn, concepts=concepts)
    keys = sorted({*(d["concept"] for d in detected), *_confirmed(understanding)})
    for k in keys:
        if k in w.ids and wf_node:
            await w.edge(wf_node["id"], "implements", w.ids[k], source="learned")
    await _escalations(w, defn)
    validated = _is_validated(row)
    result = LearnResult(validated=validated)
    tests = _test_counts(row)
    if validated and wf_node is not None:
        slot_values = {k: v.get("value") for k, v in ((understanding or {}).get("slots") or {}).items()
                       if isinstance(v, Mapping) and v.get("value") not in (None, "")}
        sk = skeleton_from_parts([{"key": "main", "role": "entry", "definition": defn}], concepts=concepts,
                                 extra_concepts=_confirmed(understanding), slot_values=slot_values)
        try:
            from .template_installer import export_template

            async with session.begin_nested():
                raw_bundle, _warn = await export_template(session, wf.id)
        except Exception:  # noqa: BLE001 - a rough draft still teaches the skeleton
            logger.info("could not export %s as a bundle", wf.id, exc_info=True)
            raw_bundle = {}
        bundle = scrub_bundle(raw_bundle, slots=sk["role_slots"])
        now = datetime.now(timezone.utc).isoformat()
        ev = {"ref": row["id"], "proposal_id": row["id"], "project_label": _label(project.name, "a project"), "team_id": project.team_id,
              "tests": tests, "at": now}
        pnode, count, _new = await _upsert_pattern(
            session, w, shape="workflow", label=_label(wf.name, "Workflow pattern"), sk=sk, bundle=bundle, project=project, evidence=ev,
            source_ref={"project_label": ev["project_label"], "team_id": project.team_id, "proposal_id": row["id"], "kept_at": now})
        for k in sk["concepts"]:
            if k in w.ids:
                await w.edge(pnode["id"], "implements", w.ids[k], source="learned")
        await w.edge(pnode["id"], "validated_by", wf_node["id"], props={"tests": ev["tests"]}, source="learned")
        adapt = ((understanding or {}).get("adapt_from") or {}).get("pattern")
        src = await w.repo.get_node_by_key(adapt, scope="global") if adapt else None
        if src and src["id"] != pnode["id"]:
            await w.edge(wf_node["id"], "derived_from", src["id"], source="learned")
        result.pattern_key, result.validated_count = pnode["key"], count
        result.summary = f"Learned '{pnode['label']}' from {ev['project_label']}: validated x{count}, {len(sk['concepts'])} concepts, {ev['tests']['approved']} tests green"
    else:
        result.summary = f"Kept '{wf.name}' without a green approved run: not learned as a pattern (unvalidated)"
    result.nodes_added, result.edges_added = w.nodes_added, w.edges_added
    ev_row = await w.repo.add_learn_event(trigger="kept_workflow", project_id=project.id, team_id=project.team_id, actor=row.get("kept_by"),
                                          proposal_id=row["id"], summary=result.summary,
                                          payload={**result.payload(), "validated": validated, "concepts": keys})
    result.event_id = ev_row["id"]
    return result


async def on_app_kept(app_build_id: str) -> LearnResult | None:
    """The same for a kept application build (reads ``app_builds`` and the part proposals)."""
    project_id = None
    try:
        from ..db import session as dbs

        async with dbs.async_session_factory() as s:
            res = await _learn_app(s, app_build_id)
            await s.commit()
            return res
    except Exception as e:  # noqa: BLE001
        logger.warning("learning from app build %s failed", app_build_id, exc_info=True)
        await _fail(app_build_id, "kept_app", project_id, e)
        return None


async def _learn_app(session: AsyncSession, app_build_id: str) -> LearnResult | None:
    from ..db.models import ProjectModel, WorkflowModel
    from ..repositories.ai_proposal_repository import AIProposalRepository
    from ..repositories.app_build_repository import AppBuildRepository
    from . import kg_extract

    app = await AppBuildRepository(session).get(app_build_id)
    if app is None or app.get("status") != "kept":
        return None
    project = await session.get(ProjectModel, app["project_id"])
    if project is None:
        return None
    ids = [r[0] for r in (await session.execute(text("SELECT id FROM ai_proposals WHERE app_build_id = :a ORDER BY created_at"),
                                                  {"a": app_build_id})).all()]
    repo = AIProposalRepository(session)
    proposals = [p for p in [await repo.get(i) for i in ids] if p]
    plan = app.get("plan") or {}
    understanding = app.get("understanding") or {}
    w = await kg_extract.ensure_seeded(session)
    concepts = await kg_extract.concept_rows(session)
    api_index = await _api_index(session, project.id)
    plan_parts = {p["key"]: p for p in plan.get("parts") or [] if isinstance(p, Mapping) and p.get("key")}
    parts, wf_nodes, defs = [], [], []
    for p in proposals:
        wf = await session.get(WorkflowModel, p["workflow_id"]) if p.get("workflow_id") else None
        if wf is None:
            continue
        await kg_extract.workflow_capability(session, w, project, wf, concepts=concepts, api_index=api_index)
        node = await w.repo.get_node_by_key(kr.workflow_key(wf.id), scope="project", project_id=project.id)
        if node:
            wf_nodes.append((p.get("part_key"), node))
        d = wf.draft_definition or {}
        defs.append(d)
        await _escalations(w, d)
        pp = plan_parts.get(p.get("part_key")) or {}
        parts.append({"key": p.get("part_key") or wf.name, "role": pp.get("role") or "entry", "definition": d,
                      "concepts": pp.get("concepts") or []})
    validated = bool(proposals) and all(_is_validated(p) for p in proposals)
    plan_concepts = sorted({c for pp in plan_parts.values() for c in pp.get("concepts") or []} | set(_confirmed(understanding)))
    slot_values = {k: v.get("value") for k, v in (understanding.get("slots") or {}).items()
                   if isinstance(v, Mapping) and v.get("value") not in (None, "")}
    entity = plan.get("entity") or slot_values.get("exception_case.entity")
    sk = skeleton_from_parts(parts, concepts=concepts, entity=entity, extra_concepts=plan_concepts, slot_values=slot_values,
                             tables=plan.get("tables") or [])
    title = _label(plan.get("title") or "", "Application")
    app_node = await w.node(key=kr.app_key(app_build_id), kind="app", scope="project", project_id=project.id, team_id=project.team_id,
                            label=title, summary=f"Application built from {len(parts)} parts", source="learned",
                            props={"parts": {k: n["key"] for k, n in wf_nodes}, "tables": [t.get("name") for t in plan.get("tables") or []],
                                   "install_id": app.get("install_id"), "concepts": sk["concepts"]})
    if app_node:
        for _k, n in wf_nodes:
            await w.edge(app_node["id"], "composes", n["id"], source="learned")
        for k in sk["concepts"]:
            if k in w.ids:
                await w.edge(app_node["id"], "implements", w.ids[k], source="learned")
        for rk in sorted({v for v in sk["slots"].values() if isinstance(v, str) and _ROLE_FULL.match(v)}):
            if rk in w.ids:
                await w.edge(app_node["id"], "requires_permission", w.ids[rk], source="learned")
    result = LearnResult(validated=validated)
    counts = [_test_counts(p) for p in proposals]
    tests = {"approved": sum(c["approved"] for c in counts), "passed": sum(c["passed"] for c in counts)}
    if validated and app_node:
        bundle = scrub_bundle(app.get("bundle") or {}, slots=sk["role_slots"])
        now = datetime.now(timezone.utc).isoformat()
        ev = {"ref": app_build_id, "app_build_id": app_build_id, "project_label": _label(project.name, "a project"),
              "team_id": project.team_id, "tests": tests, "at": now}
        pnode, count, _new = await _upsert_pattern(
            session, w, shape="app", label=title, sk=sk, bundle=bundle, project=project, evidence=ev,
            source_ref={"project_label": ev["project_label"], "team_id": project.team_id, "app_build_id": app_build_id, "kept_at": now})
        for k in sk["concepts"]:
            if k in w.ids:
                await w.edge(pnode["id"], "implements", w.ids[k], source="learned")
        for _k, n in wf_nodes:
            await w.edge(pnode["id"], "validated_by", n["id"], props={"tests": tests}, source="learned")
        planned = plan.get("adapted_from")
        adapt = (understanding.get("adapt_from") or {}).get("pattern") or (planned.get("pattern") if isinstance(planned, Mapping) else planned)
        src = await w.repo.get_node_by_key(adapt, scope="global") if adapt else None
        # every kept app is lineage-linked to the pattern it instantiates; an adapted app also names its source pattern when that differs
        await w.edge(app_node["id"], "derived_from", pnode["id"], source="learned")
        if src and src["id"] != pnode["id"]:
            await w.edge(app_node["id"], "derived_from", src["id"], source="learned")
        result.pattern_key, result.validated_count = pnode["key"], count
        result.summary = (f"Learned '{pnode['label']}' from {ev['project_label']}: validated x{count}, {len(parts)} workflows, "
                          f"{len(sk['concepts'])} concepts, {tests['passed']} tests green")
    else:
        result.summary = f"Kept app '{title}' without a green approved run: not learned as a pattern (unvalidated)"
    result.nodes_added, result.edges_added = w.nodes_added, w.edges_added
    ev_row = await w.repo.add_learn_event(trigger="kept_app", project_id=project.id, team_id=project.team_id, actor=app.get("kept_by"),
                                          proposal_id=app_build_id, summary=result.summary,
                                          payload={**result.payload(), "validated": validated, "concepts": sk["concepts"]})
    result.event_id = ev_row["id"]
    return result
