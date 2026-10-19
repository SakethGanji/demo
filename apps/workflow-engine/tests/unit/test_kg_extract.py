"""Capability graph WP-A: deterministic extraction (seed, node catalog, templates, API operations, workflows) and LLM tag handling."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text

from src.db.models import WorkflowModel
from src.repositories.api_spec_repository import ApiSpecRepository
from src.repositories.kg_repository import KgRepository
from src.services import kg_extract
from src.services.openapi_import import import_openapi, parse_spec_text

from tests.unit.conftest import anyio_backend, pg, pg_dbname  # noqa: F401

pytestmark = pytest.mark.anyio

FIX = Path(__file__).resolve().parents[1] / "fixtures"


def uid(p: str) -> str:
    return f"{p}{uuid.uuid4().hex[:8]}"


async def project(sf, name="Payments Ops") -> tuple[str, str]:
    team, pid = uid("team_"), uid("proj_")
    async with sf() as s:
        await s.execute(text("INSERT INTO teams (id, name, slug) VALUES (:t, :t, :t)"), {"t": team})
        await s.execute(text("INSERT INTO projects (id, team_id, name, slug) VALUES (:p, :t, :n, :p)"), {"p": pid, "t": team, "n": name})
        await s.commit()
    return team, pid


async def add_notify(sf, pid) -> None:
    doc = import_openapi(parse_spec_text((FIX / "ai_builder" / "apis" / "notify.yaml").read_text()))
    assign = {"operation_id": "assign", "method": "post", "path": "/queues/{id}/assign", "summary": "Assign an item to an owner", "responses": {}}
    other = {"operation_id": "frobnicate", "method": "post", "path": "/frob", "summary": "Does a thing", "responses": {}}
    async with sf() as s:
        await ApiSpecRepository(s).create(project_id=pid, name="notify", base_url=doc["base_url"], spec={"operations": doc["operations"]}, source="paste")
        await ApiSpecRepository(s).create(project_id=pid, name="queues", base_url="http://q.test", spec={"operations": [assign, other]}, source="paste")


SUB = {"nodes": [{"name": "Inputs", "type": "ExecuteWorkflowTrigger", "parameters": {"inputSource": "fields", "fields": [
           {"name": "category", "type": "string", "required": True}]}},
          {"name": "By category", "type": "Switch", "parameters": {"rules": [{"field": "{{ $json.category }}", "operation": "equals", "value": "a", "output": 0}]}},
          {"name": "Set owner", "type": "Set", "parameters": {"mode": "json", "keepOnlySet": True, "jsonData": {"owner_role": "role:ops_analyst"}}}],
       "connections": [{"source_node": "Inputs", "target_node": "By category", "source_output": "main", "target_input": "main"},
                       {"source_node": "By category", "target_node": "Set owner", "source_output": "output0", "target_input": "main"}]}


def parent(child_id: str) -> dict:
    return {"nodes": [{"name": "Log", "type": "FormTrigger", "parameters": {"title": "Log"}},
                      {"name": "Assign", "type": "ExecuteWorkflow", "parameters": {"workflowId": child_id, "version": "draft", "inputMode": "fields", "inputs": {}}},
                      {"name": "Ask", "type": "Approval", "parameters": {"assignees": ["role:ops_manager"], "title": "t"}},
                      {"name": "Mail", "type": "SendEmail", "parameters": {"toEmail": "role:ops_manager"}}],
            "connections": [{"source_node": "Log", "target_node": "Assign", "source_output": "main", "target_input": "main"},
                            {"source_node": "Assign", "target_node": "Ask", "source_output": "main", "target_input": "main"}],
            "interface": {"kind": "form", "slug": "log-it"}}


async def add_workflows(sf, team, pid) -> tuple[str, str]:
    child, par = uid("wf_"), uid("wf_")
    async with sf() as s:
        s.add(WorkflowModel(id=child, team_id=team, project_id=pid, name="Assign owner", draft_definition=SUB))
        await s.flush()
        s.add(WorkflowModel(id=par, team_id=team, project_id=pid, name="Log an item", draft_definition=parent(child)))
        await s.commit()
    return child, par


async def keys(sf, pid=None) -> dict[str, dict]:
    async with sf() as s:
        sl = await KgRepository(s).load_slice(pid or "none", statuses=("active", "stale", "proposed"))
    return {n["key"]: n for n in sl.nodes.values()}


def edge_set(sl, key: str, rel: str) -> set[str]:
    n = sl.by_key(key)
    return {sl.node(e["dst_id"])["key"] for e in sl.out_edges(n["id"], rel)}


# ── global ────────────────────────────────────────────────────────────────────────────────────────────────────────────


async def test_seed_global_writes_the_ontology_catalog_templates_and_patterns_and_is_idempotent(pg):
    async with pg() as s:
        first = await kg_extract.seed_global(s)
    assert first.added > 0
    async with pg() as s:
        second = await kg_extract.seed_global(s)
    assert (second.added, second.updated, second.staled) == (0, 0, 0)         # nothing new, nothing rewritten
    _, pid = await project(pg)
    async with pg() as s:
        sl = await KgRepository(s).load_slice(pid)
    k = {n["key"] for n in sl.nodes.values()}
    assert len([x for x in k if x.startswith("concept:")]) == 24 and len([x for x in k if x.startswith("role:")]) == 8
    assert {"node:Approval", "node:Cron", "node:SendEmail", "ui:component:table", "tpl:refund-triage", "tpl:customer-notify"} <= k
    assert len([x for x in k if x.startswith("tpl:")]) == 9  # the nine shipped templates
    pats = [n for n in sl.of_kind("pattern") if n["source"] == "seed"]
    assert len(pats) == 9 and all(p["validated_count"] == 1 and p["visibility"] == "org" for p in pats)
    # the node types that alone realise a concept
    assert edge_set(sl, "node:Approval", "implements") >= {"concept:approval"}
    assert "concept:notification" in edge_set(sl, "node:SendEmail", "implements")
    assert "concept:schedule" in edge_set(sl, "node:Cron", "implements")
    assert "concept:intake" in edge_set(sl, "node:FormTrigger", "implements")
    assert "concept:data_profiling" in edge_set(sl, "node:Profile", "implements")   # dataset needs datasetId set: a workflow, not the type
    # the edge Wave 0 deferred (Report renders with the table component) is written by the second seed pass
    assert "ui:component:table" in edge_set(sl, "concept:report", "renders_with")
    # templates: composes node types, implements concepts, requires roles; a pattern derived from the template
    assert "node:Approval" in edge_set(sl, "tpl:refund-triage", "composes")
    assert "concept:approval" in edge_set(sl, "tpl:refund-triage", "implements")
    p = next(p for p in pats if p["props"]["source"]["template"] == "refund-triage")
    assert edge_set(sl, p["key"], "derived_from") == {"tpl:refund-triage"}
    assert "://" not in json.dumps(p["props"]["reference_bundle"])
    # Escalation is a gap in the shipped material: nothing wires timed_out
    assert not [x for x in sl.in_edges(sl.by_key("concept:escalation")["id"], "implements")]


# ── project ───────────────────────────────────────────────────────────────────────────────────────────────────────────


async def test_rebuild_extracts_apis_workflows_interfaces_and_is_idempotent(pg):
    team, pid = await project(pg)
    await add_notify(pg, pid)
    child, par = await add_workflows(pg, team, pid)
    async with pg() as s:
        r1 = await kg_extract.rebuild(s, pid)
    assert r1.added > 0 and r1.staled == 0
    async with pg() as s:
        sl = await KgRepository(s).load_slice(pid)
    assert {"api:notify.sendMessage", "api:queues.assign", "api:queues.frobnicate", f"wf:{child}", f"wf:{par}", f"ui:{par}:form"} <= \
        {n["key"] for n in sl.nodes.values()}
    sub = sl.by_key(f"wf:{child}")
    assert sub["subkind"] == "subworkflow" and sub["props"]["callable"] and sub["props"]["output_fields"] is not None
    assert [i["name"] for i in sub["props"]["inputs"]] == ["category"] and sub["props"]["trigger_kind"] == "execute"
    assert sub["scope"] == "project" and sub["project_id"] == pid
    assert sl.by_key(f"wf:{par}")["props"]["interface_kind"] == "form" and "Approval" in sl.by_key(f"wf:{par}")["props"]["node_types"]
    # edges
    assert {f"wf:{child}", "node:Approval", "node:ExecuteWorkflow"} <= edge_set(sl, f"wf:{par}", "composes")
    assert "concept:assignment" in edge_set(sl, f"wf:{child}", "implements")
    assert {"concept:approval", "concept:intake"} <= edge_set(sl, f"wf:{par}", "implements")
    assert edge_set(sl, f"wf:{par}", "requires_permission") == {"role:ops_manager"}
    assert edge_set(sl, f"wf:{par}", "renders_with") == {f"ui:{par}:form"}
    assert "ui:component:form" in edge_set(sl, f"ui:{par}:form", "composes")
    # lexical tags on API operations (aliases against operation id, path and summary)
    assert edge_set(sl, "api:notify.sendMessage", "implements") == {"concept:notification"}
    assert edge_set(sl, "api:queues.assign", "implements") == {"concept:assignment"}
    assert edge_set(sl, "api:queues.frobnicate", "implements") == set()
    # a second run changes nothing
    async with pg() as s:
        r2 = await kg_extract.rebuild(s, pid)
    assert (r2.added, r2.updated, r2.staled) == (0, 0, 0)


async def test_a_removed_workflow_becomes_stale_and_a_changed_one_updates(pg):
    team, pid = await project(pg)
    child, par = await add_workflows(pg, team, pid)
    async with pg() as s:
        await kg_extract.rebuild(s, pid)
    async with pg() as s:
        await s.execute(text("DELETE FROM workflows WHERE id = :w"), {"w": par})
        await s.execute(text("UPDATE workflows SET name = 'Assign the owner' WHERE id = :w"), {"w": child})
        await s.commit()
    async with pg() as s:
        r = await kg_extract.rebuild(s, pid)
    assert r.staled >= 2 and r.updated >= 1                                    # the workflow and its interface
    k = await keys(pg, pid)
    assert k[f"wf:{par}"]["status"] == "stale" and k[f"ui:{par}:form"]["status"] == "stale"
    assert k[f"wf:{child}"]["status"] == "active" and k[f"wf:{child}"]["label"] == "Assign the owner"


async def test_a_rebuild_never_touches_seed_learned_or_other_projects(pg):
    team, pid = await project(pg)
    team2, pid2 = await project(pg, "Cards Ops")
    _, par2 = await add_workflows(pg, team2, pid2)
    async with pg() as s:
        repo = KgRepository(s)
        res = await repo.upsert_node(key="pattern:aaaaaaaaaaaaaaaa", kind="pattern", subkind="workflow", scope="global", visibility="org",
                                     source="learned", label="Learned one")
        await s.commit()
    async with pg() as s:
        await kg_extract.rebuild(s, pid2)
    async with pg() as s:
        r = await kg_extract.rebuild(s, pid)            # project 1 has no workflows: nothing of project 2 may go stale
        assert r.staled == 0
    k = await keys(pg, pid2)
    assert k[f"wf:{par2}"]["status"] == "active"
    async with pg() as s:
        assert (await KgRepository(s).get_node(res.node["id"]))["status"] == "active"
    with pytest.raises(LookupError):
        async with pg() as s:
            await kg_extract.rebuild(s, "proj_nope")


# ── LLM tagging ───────────────────────────────────────────────────────────────────────────────────────────────────────


async def test_llm_tags_outside_the_vocabulary_are_dropped_and_a_new_concept_is_only_proposed(pg):
    team, pid = await project(pg)
    await add_notify(pg, pid)
    seen = {}

    async def fake(system: str, user: str, schema: dict) -> dict:
        seen["system"], seen["user"] = system, json.loads(user)
        return {"tags": [
            {"key": "api:queues.frobnicate", "concepts": ["concept:reconciliation", "concept:made_up", "role:ops_head"], "new_concept": "Case triage"},
            {"key": "api:not.in.list", "concepts": ["concept:approval"], "new_concept": None},
            "junk"]}

    async with pg() as s:
        r = await kg_extract.rebuild(s, pid, llm_tagging=True, ask_model=fake)
    assert seen["system"].startswith("KG_AI:CONCEPT_TAG\n")
    assert [i["key"] for i in seen["user"]["untrusted_items"]] == ["api:queues.frobnicate"]       # only operations no alias covered
    assert len(seen["user"]["concept_vocabulary"]) == 24
    assert (r.tagged, r.proposed) == (1, 1)
    async with pg() as s:
        sl = await KgRepository(s).load_slice(pid)
    assert edge_set(sl, "api:queues.frobnicate", "implements") == {"concept:reconciliation"}      # made_up and role:* were dropped
    assert "concept:case_triage" not in {n["key"] for n in sl.nodes.values()}                       # proposed: not in a retrieval slice
    k = await keys(pg, pid)
    c = k["concept:case_triage"] if "concept:case_triage" in k else None
    async with pg() as s:
        row = await KgRepository(s).get_node_by_key("concept:case_triage", scope="global")
    assert row["status"] == "proposed" and row["source"] == "llm_suggested"
    # re-running does not duplicate the proposal
    async with pg() as s:
        r2 = await kg_extract.rebuild(s, pid, llm_tagging=True, ask_model=fake)
    assert r2.proposed == 0


async def test_llm_tagging_that_cannot_run_is_a_warning_not_an_error(pg):
    team, pid = await project(pg)
    await add_notify(pg, pid)

    async def down(system, user, schema):
        raise kg_extract.TaggingUnavailable("no credentials")

    async with pg() as s:
        r = await kg_extract.rebuild(s, pid, llm_tagging=True, ask_model=down)
    assert r.tagged == 0 and r.warnings and "no credentials" in r.warnings[0]
    assert r.added > 0                                                          # the deterministic part still ran


def test_lexical_concepts_matches_whole_words_in_camel_case_and_paths():
    c = [{"key": "concept:notification", "subkind": "process", "aliases": ["notify", "send an email"]},
         {"key": "concept:assignment", "subkind": "process", "aliases": ["assign"]},
         {"key": "concept:exception_case", "subkind": "entity", "aliases": ["case"]}]
    assert kg_extract.lexical_concepts("notify.sendMessage /messages", c) == ["concept:notification"]
    assert kg_extract.lexical_concepts("queues.assignOwner", c) == ["concept:assignment"]   # camelCase is split into words
    assert kg_extract.normalize_words("queues.assignOwner").split() == ["queues", "assign", "owner"]
    assert kg_extract.lexical_concepts("reassign case", c) == []             # whole words only; entities are never tagged from an API


async def test_the_graph_view_is_capped_and_says_so(pg, monkeypatch):
    from src.services import kg_service

    team, pid = await project(pg)
    async with pg() as s:
        await kg_extract.rebuild(s, pid)
    monkeypatch.setattr(kg_service, "MAX_NODES", 10)
    async with pg() as s:
        g = await kg_service.graph(s, pid)
    assert g["truncated"] is True and len(g["nodes"]) == 10
    assert {n["kind"] for n in g["nodes"]} <= {"pattern", "concept"}                  # patterns and concepts first
    ids = {n["id"] for n in g["nodes"]}
    assert all(e["src_id"] in ids and e["dst_id"] in ids for e in g["edges"])
