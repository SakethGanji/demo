"""Deterministic extraction of capabilities into the graph (PLAN-CAPABILITY-GRAPH section 2.2 and 2.4; CONTRACTS-CAPGRAPH.md).

* ``seed_global``    ontology + node catalog + screen components + shipped templates (and a pattern per template). Idempotent,
                     quick, run at startup (``WORKFLOW_KG_SEED=0`` skips it). It re-runs the ontology seed after extraction so the
                     ``renders_with -> ui:component:*`` edges that were deferred in the first pass are written.
* ``rebuild``        the same global pass, then one project's API operations, workflows, sub-workflows and interfaces. Upserts by
                     natural key with ``source='extracted'``; an extracted node that no longer exists becomes ``stale``. Seed, learned
                     and human nodes are never touched. Optional LLM tagging (``llm_tagging=True``) is a separate, explicit step.
* ``tag_with_llm``   ``KG_AI:CONCEPT_TAG``: one batched call over items no signature covers. The model may name only concept keys it
                     was given; anything else is dropped. A ``new_concept`` becomes a ``proposed`` concept (it takes one status flip
                     to accept it; it is never used for retrieval before).

Everything but ``tag_with_llm`` is free of LLM calls. Nothing here commits; the caller owns the transaction.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Iterable, Mapping

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..repositories import kg_repository as kr
from ..repositories.kg_repository import KgRepository
from . import kg_signatures

logger = logging.getLogger(__name__)

IMPLEMENTED = True

TAG_SYSTEM_LINE = "KG_AI:CONCEPT_TAG"
SCREEN_COMPONENTS = {
    "table": "A table of rows.", "form": "An input form.", "approval_task": "The task screen of an Approval.",
    "result": "The result of a run.", "status": "A status badge.",
}
#: interface component type -> graph screen component
_COMPONENT_MAP = {"table": "table", "form": "form", "input_form": "form", "actions": "approval_task", "result": "result",
                  "status": "status"}
_TRIGGER_KIND = {"FormTrigger": "form", "Webhook": "webhook", "Cron": "cron", "Start": "manual", "ChatInput": "chat",
                 "ExecuteWorkflowTrigger": "execute", "ErrorTrigger": "error"}
_EFFECT_ORDER = ("human", "network", "llm", "database", "subworkflow", "none")

TAG_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"tags": {"type": "array", "items": {"type": "object", "properties": {
        "key": {"type": "string"}, "concepts": {"type": "array", "items": {"type": "string"}},
        "new_concept": {"type": ["string", "null"]}}, "required": ["key", "concepts"]}}},
    "required": ["tags"],
}


# ── report and writer ─────────────────────────────────────────────────────────────────────────────────────────────────


@dataclass
class RebuildReport:
    added: int = 0
    updated: int = 0
    staled: int = 0
    tagged: int = 0
    proposed: int = 0
    warnings: list[str] = field(default_factory=list)

    def merge(self, other: "RebuildReport") -> None:
        self.added += other.added
        self.updated += other.updated
        self.staled += other.staled
        self.tagged += other.tagged
        self.proposed += other.proposed
        self.warnings += other.warnings

    @property
    def changed(self) -> bool:
        return bool(self.added or self.updated or self.staled)

    def dict(self) -> dict[str, Any]:
        return {"added": self.added, "updated": self.updated, "staled": self.staled, "tagged": self.tagged,
                "proposed": self.proposed, "warnings": list(self.warnings)}


class Writer:
    """Upserts nodes and edges for one source and counts what happened."""

    def __init__(self, session: AsyncSession, report: RebuildReport, source: str = "extracted") -> None:
        self.repo = KgRepository(session)
        self.s = session
        self.report = report
        self.source = source
        self.ids: dict[str, str] = {}      # global node key -> id (concepts, roles, node types, components, templates)
        self.nodes_added = 0
        self.edges_added = 0

    async def load_globals(self) -> None:
        rows = (await self.s.execute(text("SELECT id, key FROM kg_nodes WHERE scope = 'global' AND status <> 'rejected'"))).all()
        self.ids = {k: i for i, k in rows}

    async def node(self, **kw: Any) -> dict[str, Any] | None:
        kw.setdefault("source", self.source)
        if kw["source"] == "extracted" and kw.get("status") is None:
            kw["status"] = "active"      # an extraction revives a node that went stale
        res = await self.repo.upsert_node(**kw)
        if res.action == "created":
            self.report.added += 1
            self.nodes_added += 1
        elif res.action == "updated":
            self.report.updated += 1
        if kw.get("scope", "global") == "global":
            self.ids[kw["key"]] = res.node["id"]
        return None if res.action == "skipped" else res.node

    async def edge(self, src_id: str, rel: str, dst_id: str, *, props: dict[str, Any] | None = None, source: str | None = None) -> None:
        try:
            res = await self.repo.add_edge(src_id, rel, dst_id, source=source or self.source, props=props)
        except kr.EdgeIntegrityError as e:
            self.report.warnings.append(str(e))
            return
        if res.action == "created":
            self.report.added += 1
            self.edges_added += 1
        elif res.action == "updated":
            self.report.updated += 1

    async def sync_edges(self, src_id: str, desired: Mapping[tuple[str, str], dict[str, Any]], *, source: str | None = None) -> None:
        """Make the outgoing edges of ``src_id`` written by ``source`` exactly ``desired`` (``{(rel, dst_id): props}``)."""
        source = source or self.source
        for (rel, dst), props in desired.items():
            await self.edge(src_id, rel, dst, props=props, source=source)
        for e in await self.repo.neighbours(src_id):
            if e["outgoing"] and e["source"] == source and (e["rel"], e["dst_id"]) not in desired:
                await self.s.execute(text("DELETE FROM kg_edges WHERE id = :i"), {"i": e["id"]})
                self.report.updated += 1


async def concept_rows(session: AsyncSession, *, statuses: Iterable[str] = ("active", "stale")) -> list[dict[str, Any]]:
    res = await session.execute(text("SELECT * FROM kg_nodes WHERE kind = 'concept' AND scope = 'global' AND status = ANY(:st) ORDER BY key"),
                                {"st": list(statuses)})
    return [kr._node(m) for m in res.mappings().all()]


# ── lexical helpers ───────────────────────────────────────────────────────────────────────────────────────────────────


def normalize_words(s: str) -> str:
    """lower-case words: camelCase split, punctuation to spaces (``notify.sendMessage`` -> ``notify send message``)."""
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", str(s or ""))
    return " " + " ".join(re.findall(r"[a-z0-9]+", s.lower())) + " "


def lexical_concepts(text_: str, concepts: Iterable[Mapping[str, Any]], kinds: tuple[str, ...] = ("process",)) -> list[str]:
    """Concept keys whose alias occurs as a whole word or phrase in ``text_`` (only the given concept kinds)."""
    hay = normalize_words(text_)
    out: list[str] = []
    for c in concepts:
        if c.get("subkind") not in kinds:
            continue
        for a in c.get("aliases") or []:
            if f" {' '.join(re.findall(r'[a-z0-9]+', a.lower()))} " in hay:
                out.append(c["key"])
                break
    return out


def effect_of_types(types: Iterable[str], effects: Mapping[str, str]) -> str:
    present = {effects.get(t, "none") for t in types}
    return next((e for e in _EFFECT_ORDER if e in present), "none")


def node_effects() -> dict[str, str]:
    from .ai_grounding import full_catalog

    return {e["type"]: e.get("effect") or "none" for e in full_catalog()}


# ── global extraction ─────────────────────────────────────────────────────────────────────────────────────────────────


def _only_node_type(sig: Mapping[str, Any]) -> bool:
    return not (sig.get("params") or sig.get("ports") or sig.get("follows") or sig.get("writes_table_with"))


async def extract_global(session: AsyncSession, report: RebuildReport) -> Writer:
    """Node types, screen components, templates (with a pattern each). Needs the ontology seeded."""
    from .ai_grounding import full_catalog

    w = Writer(session, report)
    await w.load_globals()
    concepts = await concept_rows(session)
    alive: list[str] = []
    for e in full_catalog():
        key = kr.node_type_key(e["type"])
        alive.append(key)
        await w.node(key=key, kind="capability", subkind="node_type", label=e.get("display_name") or e["type"],
                     summary=f"The {e.get('display_name') or e['type']} node.",
                     props={"effect": e.get("effect"), "contract_kind": e.get("contract_kind"), "display_name": e.get("display_name"),
                            "node_type": e["type"]})
    for name, summary in SCREEN_COMPONENTS.items():
        key = kr.screen_component_key(name)
        alive.append(key)
        await w.node(key=key, kind="capability", subkind="screen_component", label=name.replace("_", " ").title(), summary=summary,
                     props={"component": name})
    # implements: concepts whose signature names only a node type
    for c in concepts:
        for sig in (c["props"].get("signatures") or []):
            t = sig.get("node_type")
            if t in kg_signatures.WEAK_NODE_TYPES or not _only_node_type(sig):
                continue
            nid = w.ids.get(kr.node_type_key(t))
            if nid:
                await w.edge(nid, "implements", w.ids[c["key"]])
    await extract_templates(session, w, concepts, alive)
    report.staled += await w.repo.mark_stale(source="extracted", alive_keys=alive, kind="capability",
                                              subkinds=["node_type", "screen_component", "template"])
    return w


async def extract_templates(session: AsyncSession, w: Writer, concepts: list[dict[str, Any]], alive: list[str]) -> None:
    from . import kg_learn
    from .template_bundle import get_catalog

    try:
        catalog = get_catalog()
    except Exception as e:   # a bad bundle in dev must not stop the seed
        w.report.warnings.append(f"template catalog: {e}")
        return
    by_key = {c["key"]: c for c in concepts}
    for slug, lt in catalog.items():
        b = lt.bundle
        tkey = kr.template_key(slug)
        alive.append(tkey)
        defs = [{"key": wf.key, "role": wf.role, "definition": wf.definition if isinstance(wf.definition, dict) else
                 wf.definition.model_dump(mode="json")} for wf in b.workflows]
        types = sorted({n.get("type") for d in defs for n in (d["definition"].get("nodes") or []) if n.get("type")})
        tags = [str(t).lower() for t in (b.tags or [])]
        node = await w.node(key=tkey, kind="capability", subkind="template", label=b.name, summary=b.summary or "",
                            aliases=sorted(set(tags)), props={"version": b.version, "slug": slug, "category": b.category, "tags": tags,
                                                                 "node_types": types, "workflows": len(defs)})
        if node is None:
            continue
        detections = [d for dd in defs for d in kg_signatures.detect(dd["definition"], concepts=concepts)]
        implemented = {d["concept"] for d in detections}
        for t in tags:
            for k in lexical_concepts(t, concepts):
                implemented.add(k)
        desired: dict[tuple[str, str], dict[str, Any]] = {}
        for t in types:
            if (nid := w.ids.get(kr.node_type_key(t))):
                desired[("composes", nid)] = {}
        for k in sorted(implemented):
            if k in by_key:
                desired[("implements", w.ids[k])] = {}
        for rk in sorted(_roles_in(dd["definition"] for dd in defs)):
            if rk in w.ids:
                desired[("requires_permission", w.ids[rk])] = {}
        await w.sync_edges(node["id"], desired)
        # one pattern per template: validated by the CI gate, so its count starts at 1
        sk = kg_learn.skeleton_from_parts(defs, concepts=concepts)
        pkey = kr.pattern_key(kg_learn.skeleton_hash(sk["skeleton"], salt=f"tpl:{slug}"))
        bundle = kg_learn.scrub_bundle(lt.raw, slots=sk["role_slots"])
        pnode = await w.repo.upsert_node(
            key=pkey, kind="pattern", subkind="workflow", scope="global", visibility="org", source="seed", label=b.name,
            summary=b.summary or "", props={
                "shape": "workflow", "concepts": sk["concepts"], "slots": sk["slots"], "skeleton": sk["skeleton"],
                "reference_bundle": bundle, "source": {"template": slug, "version": b.version},
                "evidence": [{"template": slug, "version": b.version, "tests": "ci"}]})
        if pnode.action == "created":
            w.report.added += 1
            await w.repo.bump_validated(pnode.node["id"])
        elif pnode.action == "updated":
            w.report.updated += 1
        if pnode.action != "skipped":
            pid = pnode.node["id"]
            pdesired = {("implements", w.ids[k]): {} for k in sk["concepts"] if k in w.ids}
            pdesired[("derived_from", node["id"])] = {}
            await w.sync_edges(pid, pdesired, source="seed")


def _roles_in(defs: Iterable[Mapping[str, Any]]) -> set[str]:
    out: set[str] = set()
    for d in defs:
        for n in d.get("nodes") or []:
            if n.get("type") == "Approval":
                for a in (n.get("parameters") or {}).get("assignees") or []:
                    if isinstance(a, str) and re.fullmatch(r"role:[a-z][a-z0-9_]*", a):
                        out.add(a)
    return out


async def ensure_seeded(session: AsyncSession) -> Writer:
    """A writer whose global ids are loaded; runs the (idempotent, uncommitted) global pass first when the graph is empty."""
    w = Writer(session, RebuildReport())
    await w.load_globals()
    if kr.concept_key("approval") not in w.ids:
        from . import kg_ontology

        await kg_ontology.seed(session)
        await extract_global(session, w.report)
        await kg_ontology.seed(session)
        await w.load_globals()
    return w


async def seed_global(session: AsyncSession | None = None) -> RebuildReport:
    """Ontology + global extraction, committed. ``session`` None opens its own (startup). Safe to call repeatedly."""
    if session is None:
        from ..db import session as dbs

        async with dbs.async_session_factory() as s:
            return await seed_global(s)
    from . import kg_ontology

    report = RebuildReport()
    seed1 = await kg_ontology.seed(session)
    report.added += seed1.nodes["created"] + seed1.edges["created"]
    report.updated += seed1.nodes["updated"] + seed1.edges["updated"]
    await extract_global(session, report)
    if seed1.deferred_edges:
        seed2 = await kg_ontology.seed(session)   # the deferred renders_with edges now have a target
        report.added += seed2.edges["created"]
    await session.flush()
    if report.changed:
        await KgRepository(session).add_learn_event(
            trigger="seed", summary=f"Seeded the ontology and global capabilities (+{report.added} new, {report.updated} changed)",
            payload=report.dict())
    await session.commit()
    return report


# ── project extraction ────────────────────────────────────────────────────────────────────────────────────────────────


async def workflow_capability(
    session: AsyncSession, w: Writer, project: Any, wf: Any, *, io: Mapping[str, Any] | None = None, concepts: list[dict[str, Any]],
    api_index: Mapping[tuple[str, str], str], source: str | None = None, node_fx: Mapping[str, str] | None = None,
) -> dict[str, Any] | None:
    """Upsert ``wf:<id>`` (plus its interface node) and sync its edges. Used by the rebuild and by learning (``source``)."""
    source = source or w.source
    defn = wf.draft_definition or {}
    nodes = [n for n in defn.get("nodes") or [] if isinstance(n, Mapping)]
    types = sorted({n["type"] for n in nodes if n.get("type")})
    fx = node_fx if node_fx is not None else node_effects()
    api_refs = sorted({api_index[(n["parameters"]["api_ref"].get("spec_id"), n["parameters"]["api_ref"].get("operation_id"))]
                       for n in nodes if n.get("type") == "HttpRequest" and isinstance((n.get("parameters") or {}).get("api_ref"), Mapping)
                       and (n["parameters"]["api_ref"].get("spec_id"), n["parameters"]["api_ref"].get("operation_id")) in api_index})
    trig = next((n["type"] for n in kg_signatures.ordered_nodes(defn) if n.get("type") in _TRIGGER_KIND), None)
    iface = defn.get("interface") if isinstance(defn.get("interface"), Mapping) else None
    approved = sum(1 for t in defn.get("tests") or [] if isinstance(t, Mapping) and t.get("status") == "approved")
    io = io or {}
    is_sub = bool(io.get("callable"))
    props = {"effect": effect_of_types(types, fx), "inputs": io.get("inputs") or [], "output_fields": io.get("output_fields") or [],
             "trigger_kind": _TRIGGER_KIND.get(trig), "published_version": io.get("published_version"), "callable": is_sub,
             "node_types": types, "api_refs": api_refs, "interface_kind": iface.get("kind") if iface else None,
             "tests": {"approved": approved, "last_passed": None}}
    node = await w.node(key=kr.workflow_key(wf.id), kind="capability", subkind="subworkflow" if is_sub else "workflow", scope="project",
                        project_id=project.id, team_id=project.team_id, label=wf.name, summary=(wf.description or "")[:500],
                        props=props, source=source)
    if node is None:
        return None
    desired: dict[tuple[str, str], dict[str, Any]] = {}
    for t in types:
        if (nid := w.ids.get(kr.node_type_key(t))):
            desired[("composes", nid)] = {}
    for ref in api_refs:
        n = await w.repo.get_node_by_key(kr.api_key(ref), scope="project", project_id=project.id)
        if n:
            desired[("composes", n["id"])] = {}
    from .workflow_refs import refs_of

    for _node, child, _spec in refs_of(defn):
        c = await w.repo.get_node_by_key(kr.workflow_key(child), scope="project", project_id=project.id)
        if c and c["id"] != node["id"]:
            desired[("composes", c["id"])] = {"node": _node}
    for d in kg_signatures.detect(defn, concepts=concepts):
        if d["concept"] in w.ids:
            desired[("implements", w.ids[d["concept"]])] = {"evidence": d["evidence"]["nodes"][:5]}
    for rk in sorted(_roles_in([defn])):
        if rk in w.ids:
            desired[("requires_permission", w.ids[rk])] = {}
    if iface:
        kind = str(iface.get("kind"))
        inode = await w.node(key=kr.interface_key(wf.id, kind), kind="capability", subkind="interface", scope="project",
                             project_id=project.id, team_id=project.team_id, label=f"{wf.name} ({kind})",
                             props={"kind": kind, "slug": iface.get("slug"), "workflow_id": wf.id}, source=source)
        if inode:
            desired[("renders_with", inode["id"])] = {}
            comps = {"form"} if kind == "form" else set()
            for spec in (iface.get("screens") or {}).values():
                for c in (spec.get("components") or []) if isinstance(spec, Mapping) else []:
                    if isinstance(c, Mapping) and c.get("type") in _COMPONENT_MAP:
                        comps.add(_COMPONENT_MAP[c["type"]])
            await w.sync_edges(inode["id"], {("composes", w.ids[kr.screen_component_key(c)]): {} for c in sorted(comps)
                                             if kr.screen_component_key(c) in w.ids}, source=source)
    await w.sync_edges(node["id"], desired, source=source)
    return node


async def extract_project(session: AsyncSession, project_id: str, w: Writer, concepts: list[dict[str, Any]]) -> dict[tuple[str, str], str]:
    from ..db.models import ProjectModel, WorkflowModel
    from ..repositories.api_spec_repository import ApiSpecRepository
    from . import ai_grounding
    from .api_catalog import project_operations
    from .subworkflow_api import SubworkflowApi
    from sqlalchemy import select

    project = await session.get(ProjectModel, project_id)
    if project is None:
        raise LookupError(f"project not found: {project_id}")
    alive: list[str] = []
    # API operations
    ops = await project_operations(session, project.id)
    specs = [{"id": r.id, "name": r.name} for r in await ApiSpecRepository(session).list(project.id)]
    api_index: dict[tuple[str, str], str] = {}
    api_nodes: list[tuple[dict[str, Any], str]] = []
    for entry in ai_grounding.api_operations(ops, specs):
        p = entry["api_ref_param"]
        api_index[(p["spec_id"], p["operation_id"])] = entry["api_ref"]
        key = kr.api_key(entry["api_ref"])
        alive.append(key)
        node = await w.node(key=key, kind="capability", subkind="api_operation", scope="project", project_id=project.id,
                            team_id=project.team_id, label=entry["api_ref"], summary=str(entry.get("untrusted_summary") or "")[:300],
                            props={"api_ref": entry["api_ref"], "api_ref_param": p, "method": entry["method"], "path": entry["path"],
                                   "response_status": entry.get("response_status")})
        if node:
            api_nodes.append((node, f"{entry['api_ref']} {entry['path']} {entry.get('untrusted_summary') or ''}"))
    for node, hay in api_nodes:
        found = lexical_concepts(hay, concepts)
        await w.sync_edges(node["id"], {("implements", w.ids[k]): {"how": "lexical"} for k in found if k in w.ids})
    # workflows
    try:
        io_rows = {r["id"]: r for r in await SubworkflowApi(session).list_for_project(project)}
    except Exception:
        logger.warning("sub-workflow io unavailable for %s", project_id, exc_info=True)
        await session.rollback()
        io_rows = {}
    wfs = list((await session.execute(select(WorkflowModel).where(WorkflowModel.project_id == project.id))).scalars().all())
    fx = node_effects()
    for wf in wfs:
        node = await workflow_capability(session, w, project, wf, io=io_rows.get(wf.id), concepts=concepts, api_index=api_index, node_fx=fx)
        if node:
            alive.append(node["key"])
            if (wf.draft_definition or {}).get("interface"):
                alive.append(kr.interface_key(wf.id, str(wf.draft_definition["interface"].get("kind"))))
    w.report.staled += await w.repo.mark_stale(source="extracted", alive_keys=alive, project_id=project.id, kind="capability",
                                               subkinds=["api_operation", "workflow", "subworkflow", "interface"])
    return api_index


async def rebuild(session: AsyncSession, project_id: str, *, llm_tagging: bool = False,
                  ask_model: Callable[[str, str, dict], Awaitable[dict]] | None = None) -> RebuildReport:
    """Global pass + one project. Committed. Raises ``LookupError`` for an unknown project."""
    from . import kg_ontology

    report = RebuildReport()
    team = (await session.execute(text("SELECT team_id FROM projects WHERE id = :p"), {"p": project_id})).scalar()
    if team is None and (await session.execute(text("SELECT 1 FROM projects WHERE id = :p"), {"p": project_id})).first() is None:
        raise LookupError(f"project not found: {project_id}")
    seed1 = await kg_ontology.seed(session)
    report.added += seed1.nodes["created"] + seed1.edges["created"]
    report.updated += seed1.nodes["updated"] + seed1.edges["updated"]
    w = await extract_global(session, report)
    if seed1.deferred_edges:
        report.added += (await kg_ontology.seed(session)).edges["created"]
    concepts = await concept_rows(session)
    await _project_pass(session, project_id, team, w, concepts, report, llm_tagging=llm_tagging, ask_model=ask_model)
    return report


async def _project_pass(session: AsyncSession, project_id: str, team: str | None, w: Writer, concepts: list[dict[str, Any]],
                        report: RebuildReport, *, llm_tagging: bool,
                        ask_model: Callable[[str, str, dict], Awaitable[dict]] | None) -> None:
    """One project's extraction (+ optional tagging) and its ``rebuild`` learn event, committed. ``w.report`` is ``report``."""
    await extract_project(session, project_id, w, concepts)
    if llm_tagging:
        await tag_project(session, project_id, w, concepts, report, ask_model=ask_model)
    if report.changed or report.tagged:
        await w.repo.add_learn_event(trigger="rebuild", project_id=project_id, team_id=team,
                                     summary=f"Rebuilt knowledge: {report.added} added, {report.updated} updated, {report.staled} stale",
                                     payload=report.dict())
    await session.commit()


async def rebuild_team(session: AsyncSession, team_id: str, *, llm_tagging: bool = False,
                       ask_model: Callable[[str, str, dict], Awaitable[dict]] | None = None,
                       ) -> tuple[RebuildReport, list[dict[str, Any]]]:
    """The global seed once, then every project of the team, each committed with its own ``rebuild`` learn event (exactly the
    project part of ``rebuild``). ``(summed report, [{project_id, added, updated, staled}])``."""
    total = await seed_global(session)
    w = Writer(session, total)
    await w.load_globals()
    concepts = await concept_rows(session)
    per: list[dict[str, Any]] = []
    for p in await KgRepository(session).team_projects(team_id):
        report = RebuildReport()
        w.report = report
        await _project_pass(session, p["id"], team_id, w, concepts, report, llm_tagging=llm_tagging, ask_model=ask_model)
        total.merge(report)
        per.append({"project_id": p["id"], "added": report.added, "updated": report.updated, "staled": report.staled})
    return total, per


# ── LLM tagging (closed vocabulary) ───────────────────────────────────────────────────────────────────────────────────


class TaggingUnavailable(Exception):
    pass


async def default_ask(system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
    """One structured call through the configured adapter (the fake LLM in tests). Raises ``TaggingUnavailable``."""
    from ..engine import ai_policy, llm_provider, wf_ai

    cfg = wf_ai.config()
    ok, reason = ai_policy.allowed(cfg.model)
    if not ok:
        raise TaggingUnavailable(reason)
    if not llm_provider.is_configured(cfg.model):
        raise TaggingUnavailable(f"No credentials for model '{cfg.model}'")
    try:
        reply = await wf_ai.make_adapter(cfg.model, cfg).ask_json(system, user, schema, max_tokens=cfg.max_output_tokens,
                                                                   timeout_s=cfg.call_timeout_s)
        data = json.loads(reply.text or "")
    except Exception as e:  # noqa: BLE001 - provider down, script unmatched, bad JSON
        raise TaggingUnavailable(f"the model call failed: {type(e).__name__}") from e
    if not isinstance(data, dict):
        raise TaggingUnavailable("the model did not return an object")
    return data


def tag_prompt(items: list[dict[str, Any]], concepts: list[Mapping[str, Any]]) -> tuple[str, str]:
    from ..engine import ai_policy

    payload = {
        "task": "For each item, name the business concepts it implements. Use ONLY keys from concept_vocabulary. "
                "If an item clearly implements a concept that is missing, give new_concept as a short label; otherwise null.",
        "untrusted_items": items,
        "concept_vocabulary": [{"key": c["key"], "label": c["label"], "definition": (c.get("summary") or "")[:200]} for c in concepts],
    }
    clean, _ = ai_policy.scrub_payload(payload)
    return TAG_SYSTEM_LINE + "\nYou tag capabilities with business concepts. Reply with JSON only.", json.dumps(clean, ensure_ascii=False)


async def tag_with_llm(
    w: Writer, items: list[dict[str, Any]], concepts: list[dict[str, Any]], report: RebuildReport,
    ask_model: Callable[[str, str, dict], Awaitable[dict]] | None = None, *, project: Any = None,
) -> None:
    """``items`` are ``{key, node_id, label, summary}``. Writes ``implements`` edges (source ``llm_suggested``)."""
    if not items:
        return
    system, user = tag_prompt([{"key": i["key"], "label": i["label"], "untrusted_summary": i.get("summary", "")} for i in items], concepts)
    try:
        data = await (ask_model or default_ask)(system, user, TAG_SCHEMA)
    except TaggingUnavailable as e:
        report.warnings.append(f"LLM tagging skipped: {e}")
        return
    vocab = {c["key"] for c in concepts}
    by_key = {i["key"]: i for i in items}
    for tag in data.get("tags") or []:
        if not isinstance(tag, Mapping) or tag.get("key") not in by_key:
            continue
        item = by_key[tag["key"]]
        keys = [k for k in dict.fromkeys(tag.get("concepts") or []) if isinstance(k, str) and k in vocab]
        label = tag.get("new_concept")
        if isinstance(label, str) and label.strip():
            slug = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")[:40]
            if slug and kr.concept_key(slug) not in vocab:
                ckey = kr.concept_key(slug)
                created = await w.repo.upsert_node(key=ckey, kind="concept", subkind="process", scope="global", source="llm_suggested",
                                                   label=label.strip()[:80], status="proposed",
                                                   props={"definition": "", "kind": "process", "slots": [], "signatures": [],
                                                          "test_obligations": [], "prompt_hint": "", "part_recipe": None})
                if created.action == "created":
                    report.proposed += 1
                if created.action != "skipped":
                    await w.edge(item["node_id"], "implements", created.node["id"], source="llm_suggested")
        wrote = 0
        for k in keys:
            if k in w.ids:
                await w.edge(item["node_id"], "implements", w.ids[k], props={"how": "llm"}, source="llm_suggested")
                wrote += 1
        report.tagged += 1 if wrote else 0


async def tag_project(session: AsyncSession, project_id: str, w: Writer, concepts: list[dict[str, Any]], report: RebuildReport, *,
                      ask_model: Callable[[str, str, dict], Awaitable[dict]] | None = None) -> None:
    """Tag the project's API operations that no lexical match covers."""
    rows = (await session.execute(text(
        """SELECT n.id, n.key, n.label, n.summary FROM kg_nodes n WHERE n.project_id = :p AND n.subkind = 'api_operation'
           AND n.status = 'active' AND NOT EXISTS (SELECT 1 FROM kg_edges e WHERE e.src_id = n.id AND e.rel = 'implements')
           ORDER BY n.key LIMIT 60"""), {"p": project_id})).mappings().all()
    items = [{"key": r["key"], "node_id": r["id"], "label": r["label"], "summary": r["summary"]} for r in rows]
    await tag_with_llm(w, items, concepts, report, ask_model)
