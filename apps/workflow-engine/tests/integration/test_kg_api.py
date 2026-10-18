"""Capability graph WP-A over HTTP: startup seed, rebuild, graph focus, node detail and 404s, the capability matrix, memory paging,
proposed concepts and LLM tagging on the fake script. Real engine, real Postgres, fake LLM (never a live model)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ._stage8_b4_support import C, N, child_body, make, publish
from .conftest import FAKE_LLM_TOKEN, FAKE_SCRIPT, uniq

pytestmark = pytest.mark.integration

TOK = {"X-Test-Token": FAKE_LLM_TOKEN}
FIX = Path(__file__).resolve().parents[1] / "fixtures"
NOTIFY = (FIX / "ai_builder" / "apis" / "notify.yaml").read_text()
LEDGER = """openapi: 3.0.3
info: {title: ledger, version: '1.0'}
servers: [{url: 'http://ledger.bank.test'}]
paths:
  /frob:
    post:
      operationId: frobnicate
      summary: Does a thing
      responses: {'200': {description: ok}}
"""


@pytest.fixture
def api(fake_api):
    yield fake_api
    fake_api.raw("PUT", "/api/_test/fake-llm/script", headers=TOK, json=json.loads(FAKE_SCRIPT.read_text()))


def mkproject(api) -> str:
    r = api.post("/projects", {"name": uniq("kg")})
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def graph(api, pid: str, **q) -> dict:
    qs = "&".join(f"{k}={v}" for k, v in {"project_id": pid, **q}.items())
    r = api.get(f"/knowledge/graph?{qs}")
    assert r.status_code == 200, r.text
    return r.json()


def by_key(g: dict) -> dict:
    return {n["key"]: n for n in g["nodes"]}


def rebuild(api, pid: str, **body) -> dict:
    r = api.post("/knowledge/rebuild", {"project_id": pid, **body})
    assert r.status_code == 200, r.text
    return r.json()


def escalation_body() -> dict:
    return {"name": uniq("kg-esc"), "settings": {}, "nodes": [
        N("Log", "FormTrigger", {"title": "Log"}),
        N("Resolve", "Approval", {"title": "Resolve", "assignees": ["role:ops_analyst"], "due_in": "48h"}, 200),
        N("Escalated", "Approval", {"title": "Escalated", "assignees": ["role:ops_head"]}, 400),
        N("Done", "Set", {"mode": "manual", "fields": [{"name": "x", "value": "1"}]}, 600)],
        "connections": [C("Log", "Resolve"), C("Resolve", "Done", "approved"), C("Resolve", "Escalated", "timed_out"), C("Escalated", "Done", "approved")]}


# ── startup seed ──────────────────────────────────────────────────────────────────────────────────────────────────────


def test_the_engine_seeds_the_ontology_at_startup(api):
    pid = mkproject(api)
    g = graph(api, pid)
    k = by_key(g)
    assert len([x for x in k if x.startswith("concept:")]) == 22 and len([x for x in k if x.startswith("role:")]) == 8
    assert {"node:Approval", "tpl:refund-triage", "ui:component:table"} <= set(k)
    assert g["truncated"] is False
    for n in g["nodes"]:                                                     # the listing never carries the heavy pattern props
        assert "reference_bundle" not in n["props"] and "skeleton" not in n["props"]
    ids = {n["id"] for n in g["nodes"]}
    assert g["edges"] and all(e["src_id"] in ids and e["dst_id"] in ids for e in g["edges"])
    assert {"id", "src_id", "rel", "dst_id", "props", "source"} == set(g["edges"][0])
    assert api.get("/knowledge/graph?project_id=proj_nope").status_code == 404
    assert api.get("/knowledge/graph").status_code == 422


# ── rebuild, focus and detail ─────────────────────────────────────────────────────────────────────────────────────────


def test_rebuild_extracts_a_published_subworkflow_and_the_apis(api):
    pid = mkproject(api)
    assert api.post("/api-specs", {"project_id": pid, "name": "notify", "spec_text": NOTIFY}).status_code in (200, 201)
    child = make(api, child_body(project_id=pid))
    publish(api, child)
    first = rebuild(api, pid)
    assert set(first) == {"added", "updated", "staled", "tagged", "proposed", "warnings"} and first["added"] >= 2
    k = by_key(graph(api, pid))
    sub = k[f"wf:{child}"]
    assert sub["subkind"] == "subworkflow" and sub["props"]["callable"] is True and sub["props"]["published_version"] == 1
    assert sub["scope"] == "project" and sub["project_id"] == pid
    assert "api:notify.sendMessage" in k
    again = rebuild(api, pid)
    assert (again["added"], again["updated"], again["staled"]) == (0, 0, 0)
    assert api.post("/knowledge/rebuild", {"project_id": "proj_nope"}).status_code == 404


def test_graph_focus_depth_kinds_and_learned_only(api):
    pid = mkproject(api)
    body = escalation_body()
    body["project_id"] = pid
    wid = make(api, body)
    rebuild(api, pid)
    full = graph(api, pid)
    concept = by_key(full)["concept:escalation"]
    d1 = graph(api, pid, focus=concept["id"], depth=1)
    d2 = graph(api, pid, focus=concept["id"], depth=2)
    k1, k2 = by_key(d1), by_key(d2)
    assert "concept:escalation" in k1 and f"wf:{wid}" in k1 and "concept:sla" in k1          # requires, and the workflow that implements it
    assert len(d1["nodes"]) < len(d2["nodes"]) < len(full["nodes"])
    assert set(k1) <= set(k2) and "role:ops_head" in k2 and "role:ops_head" not in k1       # two hops reach the role through the workflow
    ids = {n["id"] for n in d2["nodes"]}
    assert all(e["src_id"] in ids and e["dst_id"] in ids for e in d2["edges"])
    only = graph(api, pid, kinds="role")
    assert {n["kind"] for n in only["nodes"]} == {"role"} and len(only["nodes"]) == 8
    assert graph(api, pid, learned_only="true")["nodes"] == []                              # nothing has been learned yet
    assert api.get(f"/knowledge/graph?project_id={pid}&focus=kn_missing").status_code == 404
    assert api.get(f"/knowledge/graph?project_id={pid}&depth=3").status_code == 422


def test_node_detail_shows_neighbours_and_hides_other_projects(api):
    a, b = mkproject(api), mkproject(api)
    body = escalation_body()
    body["project_id"] = a
    wid = make(api, body)
    rebuild(api, a)
    rebuild(api, b)
    node = by_key(graph(api, a))[f"wf:{wid}"]
    r = api.get(f"/knowledge/nodes/{node['id']}?project_id={a}")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["node"]["key"] == f"wf:{wid}" and set(d) == {"node", "neighbours", "evidence", "learn_events"}
    impl = {n["node"]["key"] for n in d["neighbours"]["implements"]}
    assert {"concept:escalation", "concept:approval", "concept:sla"} <= impl and all(n["direction"] == "out" for n in d["neighbours"]["implements"])
    assert {n["node"]["key"] for n in d["neighbours"]["requires_permission"]} == {"role:ops_analyst", "role:ops_head"}
    # the concept sees the same edge from the other side
    esc = by_key(graph(api, a))["concept:escalation"]
    dc = api.get(f"/knowledge/nodes/{esc['id']}?project_id={a}").json()
    assert any(n["direction"] == "in" and n["node"]["key"] == f"wf:{wid}" for n in dc["neighbours"]["implements"])
    assert "slots" in dc["node"]["props"]
    # another project: 404, the same answer as a node that does not exist
    other = api.get(f"/knowledge/nodes/{node['id']}?project_id={b}")
    missing = api.get(f"/knowledge/nodes/kn_000000000000?project_id={b}")
    assert other.status_code == missing.status_code == 404 and other.json() == missing.json()
    assert api.get(f"/knowledge/graph?project_id={b}&focus={node['id']}").status_code == 404
    assert node["key"] not in by_key(graph(api, b))


# ── capabilities ──────────────────────────────────────────────────────────────────────────────────────────────────────


def test_the_matrix_flags_escalation_as_a_gap_until_a_workflow_implements_it(api):
    a, b = mkproject(api), mkproject(api)
    rebuild(api, a)
    m = api.get(f"/knowledge/capabilities?project_id={a}")
    assert m.status_code == 200
    body = m.json()
    rows = {r["concept"]["key"]: r for r in body["rows"]}
    assert len(rows) == 22 and rows["concept:escalation"]["gap"] is True and rows["concept:escalation"]["implemented_total"] == 0
    assert rows["concept:approval"]["gap"] is False and rows["concept:approval"]["implemented_by"]["node_type"] >= 1
    assert rows["concept:approval"]["patterns"] >= 1 and rows["concept:approval"]["validated"] >= 1      # the template patterns
    t = body["totals"]
    assert t["concepts"] == 22 and t["gaps"] == sum(1 for r in rows.values() if r["gap"]) and t["implemented"] + t["gaps"] == 22
    body_wf = escalation_body()
    body_wf["project_id"] = a
    make(api, body_wf)
    rebuild(api, a)
    after = {r["concept"]["key"]: r for r in api.get(f"/knowledge/capabilities?project_id={a}").json()["rows"]}
    assert after["concept:escalation"]["gap"] is False and after["concept:escalation"]["implemented_by"] == {"workflow": 1}
    assert {r["key"] for r in after["concept:escalation"]["roles"]} >= {"role:ops_head"}
    # project B has no such workflow: it is still a gap there
    rebuild(api, b)
    assert {r["concept"]["key"]: r for r in api.get(f"/knowledge/capabilities?project_id={b}").json()["rows"]}["concept:escalation"]["gap"] is True


# ── memory ────────────────────────────────────────────────────────────────────────────────────────────────────────────


def test_memory_pages_newest_first_with_a_cursor(api):
    pid = mkproject(api)
    for i in range(3):
        body = escalation_body()
        body["project_id"] = pid
        make(api, body)
        rebuild(api, pid)
    p1 = api.get(f"/knowledge/memory?project_id={pid}&limit=2").json()
    assert len(p1["items"]) == 2 and p1["next_cursor"]
    assert p1["items"][0]["id"] > p1["items"][1]["id"] and p1["items"][0]["trigger"] == "rebuild" and p1["items"][0]["summary"]
    p2 = api.get(f"/knowledge/memory?project_id={pid}&limit=2&cursor={p1['next_cursor']}").json()
    assert len(p2["items"]) == 1 and p2["next_cursor"] is None and p2["items"][0]["id"] < p1["items"][1]["id"]
    assert set(p1["items"][0]) == {"id", "at", "project_id", "team_id", "actor", "trigger", "proposal_id", "summary", "payload"}
    assert api.get(f"/knowledge/memory?project_id={pid}&cursor=abc").status_code == 422


# ── LLM tagging and proposed concepts ─────────────────────────────────────────────────────────────────────────────────


def use_script(api, rules: list[dict]) -> None:
    assert api.raw("PUT", "/api/_test/fake-llm/script", headers=TOK, json={"rules": rules}).status_code == 200
    api.raw("DELETE", "/api/_test/fake-llm/calls", headers=TOK)


def tagging_project(api) -> str:
    pid = mkproject(api)
    assert api.post("/api-specs", {"project_id": pid, "name": "ledger", "spec_text": LEDGER}).status_code in (200, 201)
    return pid


def test_rebuild_with_llm_tagging_on_the_fake_script_then_accept_the_proposed_concept(api):
    use_script(api, json.loads((FIX / "fake_llm" / "capability_graph" / "concept_tag.json").read_text())["rules"])
    pid = tagging_project(api)
    r = rebuild(api, pid, llm_tagging=True)
    assert r["tagged"] == 1 and r["proposed"] == 1 and r["warnings"] == []
    called = api.raw("GET", "/api/_test/fake-llm/calls", headers=TOK).json()
    assert [c["messages"][0]["content"].split("\n")[0] for c in called] == ["KG_AI:CONCEPT_TAG"]
    g = graph(api, pid)
    k = by_key(g)
    assert k["concept:case_triage"]["status"] == "proposed" and k["concept:case_triage"]["source"] == "llm_suggested"
    op = k["api:ledger.frobnicate"]
    detail = api.get(f"/knowledge/nodes/{op['id']}?project_id={pid}").json()
    assert {n["node"]["key"] for n in detail["neighbours"]["implements"]} == {"concept:reconciliation", "concept:case_triage"}   # made_up was dropped; the node sheet shows the proposal
    # the capability matrix does not list a proposed concept
    assert "concept:case_triage" not in {x["concept"]["key"] for x in api.get(f"/knowledge/capabilities?project_id={pid}").json()["rows"]}
    nid = k["concept:case_triage"]["id"]
    ok = api.post(f"/knowledge/nodes/{nid}/accept", {"actor": "Asha"})
    assert ok.status_code == 200 and ok.json()["status"] == "active" and ok.json()["id"] == nid
    assert api.post(f"/knowledge/nodes/{nid}/accept", {"actor": "Asha"}).status_code == 409              # not proposed any more
    assert api.post(f"/knowledge/nodes/{nid}/reject", {"actor": "Asha"}).status_code == 409
    assert "concept:case_triage" in {x["concept"]["key"] for x in api.get(f"/knowledge/capabilities?project_id={pid}").json()["rows"]}
    # the accepted concept now carries the edge the model proposed
    assert any(e["rel"] == "implements" and e["dst_id"] == nid for e in graph(api, pid)["edges"])


def test_reject_is_one_status_flip_and_only_concepts_can_be_decided(api):
    reply = {"tags": [{"key": "api:ledger.frobnicate", "concepts": [], "new_concept": "Vendor onboarding"}]}
    use_script(api, [{"id": "t", "when": {"system_matches": "^KG_AI:CONCEPT_TAG\\n"}, "respond": {"text": json.dumps(reply)}}])
    pid = tagging_project(api)
    rebuild(api, pid, llm_tagging=True)
    k = by_key(graph(api, pid))
    nid = k["concept:vendor_onboarding"]["id"]
    rej = api.post(f"/knowledge/nodes/{nid}/reject", {"actor": "Asha"})
    assert rej.status_code == 200 and rej.json()["status"] == "rejected"
    assert "concept:vendor_onboarding" not in by_key(graph(api, pid))
    rebuild(api, pid, llm_tagging=True)                                      # a rejection survives the next rebuild
    assert "concept:vendor_onboarding" not in by_key(graph(api, pid))
    approval = k["concept:approval"]
    assert api.post(f"/knowledge/nodes/{approval['id']}/accept", {"actor": "Asha"}).status_code == 409
    assert api.post("/knowledge/nodes/kn_000000000000/accept", {"actor": "Asha"}).status_code == 404
    op = k["api:ledger.frobnicate"]
    assert api.post(f"/knowledge/nodes/{op['id']}/accept?project_id=proj_nope", {}).status_code == 404


def test_llm_tagging_when_the_model_is_unavailable_is_a_warning(api):
    api.raw("PUT", "/api/_test/fake-llm/state", headers=TOK, json={"unavailable": True})
    try:
        pid = tagging_project(api)
        r = rebuild(api, pid, llm_tagging=True)
        assert r["tagged"] == 0 and r["warnings"] and r["added"] > 0
    finally:
        api.raw("PUT", "/api/_test/fake-llm/state", headers=TOK, json={"unavailable": False})


# ── understand ────────────────────────────────────────────────────────────────────────────────────────────────────────


def test_understand_is_lexical_only_and_writes_nothing(api):
    pid = mkproject(api)
    rebuild(api, pid)
    before = api.get(f"/knowledge/memory?project_id={pid}").json()
    api.raw("DELETE", "/api/_test/fake-llm/calls", headers=TOK)
    r = api.post("/knowledge/understand", {"project_id": pid, "request": "escalate unresolved exceptions to the head of operations after 48 hours"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert isinstance(body.get("concepts"), list) and body.get("v") == 1
    assert api.raw("GET", "/api/_test/fake-llm/calls", headers=TOK).json() == []                 # never a model call
    assert api.get(f"/knowledge/memory?project_id={pid}").json() == before                       # no row
    assert api.post("/knowledge/understand", {"project_id": "proj_nope", "request": "x"}).status_code == 404


def test_startup_seed_can_be_switched_off(tmp_path_factory):
    from .conftest import EngineServer, StudioClient, TestDatabase

    for env, expect_seeded in (({"WORKFLOW_KG_SEED": "0"}, False), ({"WORKFLOW_KG_SEED": "1"}, True)):
        db = TestDatabase()
        db.create()
        srv = None
        try:
            db.migrate("apply")
            srv = EngineServer(db, extra_env=env, cwd=str(tmp_path_factory.mktemp("kg-engine")), label="-kgseed")
            srv.start()                                                      # healthy either way
            n = db.query("SELECT count(*) FROM kg_nodes")[0][0]
            assert (n > 0) is expect_seeded
        finally:
            if srv:
                srv.stop()
            db.drop()
