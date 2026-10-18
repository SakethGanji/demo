"""Capability graph WP-A: operational memory. Skeletons, slot tokens, scrubbing, and what a Keep teaches the graph."""

from __future__ import annotations

import copy
import json
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text

from src.db.models import WorkflowModel
from src.repositories.ai_proposal_repository import AIProposalRepository, new_proposal_id
from src.repositories.app_build_repository import AppBuildRepository, new_app_build_id
from src.repositories.kg_repository import KgRepository
from src.services import kg_extract, kg_learn

from tests.unit.conftest import anyio_backend, pg, pg_dbname  # noqa: F401

pytestmark = pytest.mark.anyio

TEMPLATES = Path(__file__).resolve().parents[2] / "templates"


def node(name, type_, **params):
    return {"name": name, "type": type_, "parameters": params}


def conn(a, b, port="main"):
    return {"source_node": a, "target_node": b, "source_output": port, "target_input": "main"}


def case_app(entity="exception", table="exception_cases", first="role:ops_analyst", second="role:ops_head", due="48h", names=("Log", "Resolve", "Escalated", "Store")) -> dict:
    log, resolve, esc, store = names
    return {
        "nodes": [node(log, "FormTrigger"),
                  node(store, "DataTable", table=table, operation="insert", data={"id": "{{ $json.id }}", "status": "open"}),
                  node(resolve, "Approval", assignees=[first], due_in=due, title=f"Resolve {entity}"),
                  node(esc, "Approval", assignees=[second], title=f"Escalated {entity}")],
        "connections": [conn(log, store), conn(store, resolve), conn(resolve, esc, "timed_out")],
    }


def sk(defn, **kw):
    return kg_learn.skeleton_from_parts([{"key": "log_" + kw.pop("entity", "exception"), "role": "entry", "definition": defn}], **kw)


# ── skeleton ──────────────────────────────────────────────────────────────────────────────────────────────────────────


def test_the_same_app_with_a_renamed_entity_and_other_roles_has_the_same_hash():
    a = sk(case_app())
    b = sk(case_app(table="fraud_alerts", first="role:fraud_analyst", second="role:fraud_lead", due="4h",
                    names=("Raise", "Handle", "Pass up", "Save")), entity="fraud_alert")
    assert a["skeleton"] == b["skeleton"]
    assert kg_learn.skeleton_hash(a["skeleton"]) == kg_learn.skeleton_hash(b["skeleton"])
    assert a["slots"]["sla.due_in"] == "48h" and b["slots"]["sla.due_in"] == "4h"      # the values differ, the skeleton does not
    assert a["slots"]["exception_case.entity"] == "exception" and b["slots"]["exception_case.entity"] == "fraud_alert"
    # a different structure is a different pattern
    c = case_app()
    c["connections"] = [x for x in c["connections"] if x["source_output"] != "timed_out"]
    assert kg_learn.skeleton_hash(sk(c)["skeleton"]) != kg_learn.skeleton_hash(a["skeleton"])


def test_two_slots_sharing_a_role_hash_like_two_slots_with_different_roles():
    distinct = sk(case_app(second="role:ops_head"), slot_values={"report.recipients_role": "role:ops_manager"})
    shared = sk(case_app(second="role:fraud_lead"), slot_values={"report.recipients_role": "role:fraud_lead"})
    assert distinct["skeleton"] == shared["skeleton"]
    assert kg_learn.skeleton_hash(distinct["skeleton"]) == kg_learn.skeleton_hash(shared["skeleton"])
    assert shared["slots"]["report.recipients_role"] == shared["slots"]["escalation.target_role"] == "role:fraud_lead"
    assert shared["role_slots"]["role:fraud_lead"] == "escalation.target_role"      # deterministic: never the report slot when another holds the role


def test_values_become_slot_tokens():
    s = sk(case_app())
    dumped = json.dumps(s["skeleton"])
    for literal in ("48h", "ops_head", "ops_analyst", "exception_cases", "Resolve"):
        assert literal not in dumped
    steps = s["skeleton"]["parts"][0]["steps"]
    by_type = {(x["node_type"], x.get("concept")): x for x in steps}
    appr = steps[2]["params"]
    assert appr == {"assignees": ["{approval.approver_role}"], "due_in": "{sla.due_in}"} or appr["due_in"] == "{sla.due_in}"
    assert steps[1]["params"] == {"table": "{entity}", "operation": "insert"}
    assert s["skeleton"]["parts"][0]["key"] == "log_{entity}"
    assert "concept:escalation" in s["concepts"] and "concept:exception_case" in s["concepts"]
    assert s["role_slots"]["role:ops_head"] == "escalation.target_role"
    assert s["skeleton"]["parts"][0]["rules"] == []         # concepts=None: no obligations to quote


# ── scrubbing ─────────────────────────────────────────────────────────────────────────────────────────────────────────


def dirty_bundle() -> dict:
    return {
        "format": "wf-template/1", "slug": "x", "requires": {"egress_hosts": ["api.acme-bank.com"], "secrets": [{"name": "K"}]},
        "apis": [{"key": "pay", "name": "pay", "spec": {"base_url": "https://api.acme-bank.com/v1", "operations": []}}],
        "workflows": [{"key": "main", "definition": {"nodes": [
            {"name": "Call", "type": "HttpRequest", "parameters": {"url": "https://api.acme-bank.com/v1/refunds?x=1", "headers":
                {"Authorization": "Bearer abcdefghijklmnopqrstuvwxyz0123456789"}, "api_key": "sk-abcdefghijklmnopqrstuvwxyz"}},
            {"name": "A", "type": "Approval", "parameters": {"assignees": ["role:ops_head", "jane.doe", "jane@acme-bank.com", "{{ $json.owner }}"],
                                                              "title": "Mail jane@acme-bank.com about card 4111 1111 1111 1111"}},
            {"name": "Sub", "type": "ExecuteWorkflow", "parameters": {"workflowId": "@tpl:workflow/child"}}],
            "tests": [{"id": "t1", "status": "approved", "approved_by": "jane", "name": "contact +1 415 555 0132 at jane@acme-bank.com"}]}}],
    }


def test_scrub_bundle_leaves_no_host_secret_email_or_person():
    out = kg_learn.scrub_bundle(dirty_bundle(), slots={"role:ops_head": "escalation.target_role"})
    blob = json.dumps(out)
    for leaked in ("acme-bank", "http://", "https://", "jane", "Bearer abc", "sk-abc", "4111", "415 555", "approved_by"):
        assert leaked not in blob, leaked
    a = out["workflows"][0]["definition"]["nodes"][1]["parameters"]["assignees"]
    assert a == ["@slot:escalation.target_role", "@slot:person", "@slot:person", "{{ $json.owner }}"]
    assert out["requires"]["egress_hosts"] == [] and out["apis"][0]["spec"]["base_url"] == "@cap:api"
    assert out["workflows"][0]["definition"]["nodes"][2]["parameters"]["workflowId"] == "@tpl:workflow/child"   # tokens are kept
    assert "acme-bank" in json.dumps(dirty_bundle())                                                       # the input was not mutated


def test_shipped_templates_scrub_clean():
    for d in sorted(p for p in TEMPLATES.iterdir() if (p / "template.json").is_file()):
        raw = json.loads((d / "template.json").read_text())
        hosts = raw.get("requires", {}).get("egress_hosts", [])
        blob = json.dumps(kg_learn.scrub_bundle(raw))
        assert "http://" not in blob and "https://" not in blob, d.name
        assert not any(h in blob for h in hosts), d.name


# ── what a Keep teaches the graph (real Postgres) ─────────────────────────────────────────────────────────────────────


def uid(p: str) -> str:
    return f"{p}{uuid.uuid4().hex[:8]}"


async def project(sf, team: str | None = None, name: str = "Payments Ops") -> tuple[str, str]:
    team = team or uid("team_")
    pid = uid("proj_")
    async with sf() as s:
        await s.execute(text("INSERT INTO teams (id, name, slug) VALUES (:t, :t, :t) ON CONFLICT (id) DO NOTHING"), {"t": team})
        await s.execute(text("INSERT INTO projects (id, team_id, name, slug) VALUES (:p, :t, :n, :p)"), {"p": pid, "t": team, "n": name})
        await s.commit()
    return team, pid


async def keep_workflow(sf, team: str, pid: str, defn: dict, *, passed: bool = True, accept_failing: bool = False,
                        understanding: dict | None = None, name: str = "Case handling") -> str:
    wid = uid("wf_")
    async with sf() as s:
        s.add(WorkflowModel(id=wid, team_id=team, project_id=pid, name=name, draft_definition=defn))
        await s.flush()
        await AIProposalRepository(s).insert({
            "id": new_proposal_id(), "project_id": pid, "team_id": team, "workflow_id": wid, "kind": "build", "status": "kept",
            "actor": "ana", "request": "r", "request_sha256": "a" * 64, "model": "m", "provider": "p", "proposed_definition": defn,
            "report": {"passed": passed, "tests": {"approved_total": 5, "passed": 5 if passed else 3}}, "understanding": understanding})
        pr = (await s.execute(text("SELECT id FROM ai_proposals WHERE workflow_id = :w"), {"w": wid})).scalar()
        await s.execute(text("UPDATE ai_proposals SET accepted_failing = :a, kept_by = 'ana' WHERE id = :i"), {"a": accept_failing, "i": pr})
        await s.commit()
    return pr


async def pattern_rows(sf) -> list[dict]:
    async with sf() as s:
        res = await s.execute(text("SELECT * FROM kg_nodes WHERE kind = 'pattern' AND source = 'learned' ORDER BY created_at"))
        return [dict(m) for m in res.mappings().all()]


async def test_a_validated_keep_creates_a_pattern_and_a_second_team_strengthens_it(pg):
    t1, p1 = await project(pg, name="Payments Ops")
    t2, p2 = await project(pg, name="Cards Ops")
    pr1 = await keep_workflow(pg, t1, p1, case_app(), name="Case handling")
    res = await kg_learn.on_proposal_kept(pr1)
    assert res and res.validated and res.validated_count == 1 and res.pattern_key.startswith("pattern:")
    assert "Learned" in res.summary and res.nodes_added >= 2 and res.edges_added >= 3
    pats = await pattern_rows(pg)
    assert len(pats) == 1 and pats[0]["validated_count"] == 1 and pats[0]["visibility"] == "org" and pats[0]["scope"] == "global"
    props = json.loads(pats[0]["props"]) if isinstance(pats[0]["props"], str) else pats[0]["props"]
    assert {"concept:escalation", "concept:sla", "concept:approval", "concept:exception_case"} <= set(props["concepts"])
    assert props["evidence"][0]["project_label"] == "Payments Ops"
    blob = json.dumps(props)
    assert "ops_head" not in json.dumps(props["skeleton"]) and "://" not in json.dumps(props["reference_bundle"])

    # learning the same Keep again must not count twice
    again = await kg_learn.on_proposal_kept(pr1)
    assert again.validated_count == 1

    # team B keeps the same shape with other names: the same pattern, now validated x2 with two teams of evidence
    pr2 = await keep_workflow(pg, t2, p2, case_app(table="fraud_alerts", first="role:fraud_analyst", second="role:fraud_lead", due="4h",
                                                   names=("Raise", "Handle", "Pass up", "Save")), name="Fraud alerts")
    res2 = await kg_learn.on_proposal_kept(pr2)
    assert res2.validated_count == 2 and res2.pattern_key == res.pattern_key
    pats = await pattern_rows(pg)
    assert len(pats) == 1 and pats[0]["validated_count"] == 2
    props = json.loads(pats[0]["props"]) if isinstance(pats[0]["props"], str) else pats[0]["props"]
    assert [e["project_label"] for e in props["evidence"]] == ["Payments Ops", "Cards Ops"]

    # the graph now has: the workflow, implements edges, requires_permission, an escalation between the two roles, validated_by
    async with pg() as s:
        sl = await KgRepository(s).load_slice(p1)
        wf1 = next(n for n in sl.of_kind("capability") if n["subkind"] in ("workflow", "subworkflow"))
        implemented = {sl.node(e["dst_id"])["key"] for e in sl.out_edges(wf1["id"], "implements")}
        assert {"concept:escalation", "concept:sla", "concept:exception_case"} <= implemented
        roles = {sl.node(e["dst_id"])["key"] for e in sl.out_edges(wf1["id"], "requires_permission")}
        assert roles == {"role:ops_analyst", "role:ops_head"}
        esc = [e for e in sl.edges if e["rel"] == "escalates_to"]
        assert [(sl.node(e["src_id"])["key"], sl.node(e["dst_id"])["key"], e["props"]["after"]) for e in esc
                if sl.node(e["src_id"])["key"] == "role:ops_analyst"] == [("role:ops_analyst", "role:ops_head", "48h")]
        pat = sl.by_key(res.pattern_key)
        assert [e["rel"] for e in sl.out_edges(pat["id"], "validated_by")] == ["validated_by"]
        events, _ = await KgRepository(s).list_learn_events(p1)
        assert [e["trigger"] for e in events] == ["kept_workflow", "kept_workflow"]
        assert events[0]["payload"]["pattern_key"] == res.pattern_key


async def test_accept_failing_still_records_the_workflow_but_writes_no_pattern(pg):
    t, p = await project(pg)
    before = len(await pattern_rows(pg))
    pr = await keep_workflow(pg, t, p, case_app(), passed=False, accept_failing=True)
    res = await kg_learn.on_proposal_kept(pr)
    assert res and not res.validated and res.pattern_key is None and "unvalidated" in res.summary
    assert len(await pattern_rows(pg)) == before
    async with pg() as s:
        sl = await KgRepository(s).load_slice(p)
        assert [n for n in sl.of_kind("capability") if n["scope"] == "project" and n["subkind"] in ("workflow", "subworkflow")]
        events, _ = await KgRepository(s).list_learn_events(p)
        assert events[0]["payload"]["validated"] is False
    # a red report without the override is not validated either
    pr2 = await keep_workflow(pg, t, p, case_app(), passed=False, accept_failing=False)
    assert not (await kg_learn.on_proposal_kept(pr2)).validated


async def test_learning_never_raises_and_ignores_proposals_that_are_not_kept(pg):
    assert await kg_learn.on_proposal_kept("p_doesnotexist") is None
    t, p = await project(pg)
    async with pg() as s:
        await AIProposalRepository(s).insert({"id": "p_notkept00001", "project_id": p, "kind": "build", "status": "ready", "actor": "a",
                                              "request": "r", "request_sha256": "a" * 64, "model": "m", "provider": "p"})
        await s.commit()
    assert await kg_learn.on_proposal_kept("p_notkept00001") is None


async def test_a_kept_app_becomes_an_app_node_and_an_app_pattern(pg):
    t, p = await project(pg, name="Payments Ops")
    abid = new_app_build_id()
    plan = {"v": 1, "title": "Operational exceptions", "entity": "exception", "adapted_from": None,
            "tables": [{"key": "cases", "name": "exception_cases", "columns": [{"name": "id", "type": "string"}, {"name": "status", "type": "string"}]}],
            "parts": [{"key": "log_exception", "role": "entry", "concepts": ["concept:intake", "concept:escalation"]}]}
    async with pg() as s:
        await AppBuildRepository(s).insert({"id": abid, "project_id": p, "team_id": t, "actor": "ana", "request": "r", "request_sha256": "a" * 64,
                                            "status": "kept", "plan": plan, "understanding": {"slots": {"sla.due_in": {"value": "48h", "from": "request"}}},
                                            "bundle": dirty_bundle()})
        await s.commit()
    wid = uid("wf_")
    async with pg() as s:
        s.add(WorkflowModel(id=wid, team_id=t, project_id=p, name="Log an exception", draft_definition=case_app()))
        await s.flush()
        await AIProposalRepository(s).insert({"id": new_proposal_id(), "project_id": p, "workflow_id": wid, "kind": "build", "status": "kept",
                                              "actor": "ana", "request": "r", "request_sha256": "a" * 64, "model": "m", "provider": "p",
                                              "app_build_id": abid, "part_key": "log_exception",
                                              "report": {"passed": True, "tests": {"approved_total": 5, "passed": 5}}})
        await s.commit()
    res = await kg_learn.on_app_kept(abid)
    assert res and res.validated and res.validated_count == 1
    async with pg() as s:
        sl = await KgRepository(s).load_slice(p)
        app = sl.by_key(f"app:{abid}")
        assert app and app["kind"] == "app" and app["label"] == "Operational exceptions"
        assert [sl.node(e["dst_id"])["key"] for e in sl.out_edges(app["id"], "composes")] == [f"wf:{wid}"]
        pat = sl.by_key(res.pattern_key)
        assert pat["props"]["shape"] == "app" and pat["validated_count"] == 1
        assert pat["props"]["skeleton"]["tables"] == [{"key": "cases", "columns": ["id", "status"]}]
        assert "acme-bank" not in json.dumps(pat["props"])
        events, _ = await KgRepository(s).list_learn_events(p)
        assert events[0]["trigger"] == "kept_app"
