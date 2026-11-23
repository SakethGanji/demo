"""Capability graph WP-B over HTTP on the fake-LLM engine with real Postgres: Understand in the spec/build loop (CONTRACTS-CAPGRAPH.md 4.1-4.3).

Script: tests/fixtures/fake_llm/capability_graph/single_approval_escalation.json (UNDERSTAND, SPEC, BUILD, REPAIR). Nothing here calls a live model.
The Keep -> learn assertions need WP-A's ``kg_learn`` (``IMPLEMENTED``); until it lands they are skipped.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.services import kg_learn

from .conftest import FAKE_LLM_TOKEN, FAKE_SCRIPT, uniq
from .test_ai_builder_api import approve, build, calls, err, events, lines, use, user_payload

pytestmark = pytest.mark.integration

GRAPH_SCRIPT = json.loads((Path(__file__).resolve().parents[1] / "fixtures" / "fake_llm" / "capability_graph" / "single_approval_escalation.json").read_text())["rules"]
TOK = {"X-Test-Token": FAKE_LLM_TOKEN}


def request_text() -> str:
    # no approver is named, so Approval's required slot stays empty and Understand has to ask
    return f"Expense claims need sign off, and if nobody decides within 48 hours escalate it to the head of operations. ({uniq('r')})"


@pytest.fixture
def clean(fake_api):
    yield
    fake_api.raw("PUT", "/api/_test/fake-llm/script", headers=TOK, json=json.loads(FAKE_SCRIPT.read_text()))


MINE: list[str] = []          # the projects these tests made: what a Keep taught the graph is removed again


def forget(db) -> None:
    db.query("DELETE FROM kg_nodes WHERE kind IN ('pattern', 'app') OR project_id = ANY(%s)", (list(MINE),))
    db.query("DELETE FROM kg_learn_events WHERE trigger IN ('kept_workflow', 'kept_app') OR project_id = ANY(%s)", (list(MINE),))


@pytest.fixture(autouse=True)
def no_learned_patterns(fake_stack):
    """A Keep teaches the org (learned patterns and their events outlive the test), which would leak into later suites (test_kg_api counts
    the graph). Each test starts and ends with a graph without learned patterns."""
    forget(fake_stack[1])
    yield
    forget(fake_stack[1])


@pytest.fixture
def pid(fake_api, clean) -> str:
    r = fake_api.post("/projects", {"name": uniq("cg")})
    assert r.status_code in (200, 201), r.text
    MINE.append(r.json()["id"])
    return r.json()["id"]


def post_spec(api, pid: str, **extra) -> dict:
    r = api.post("/ai/builder/spec", {"project_id": pid, "request": request_text(), "actor": "Asha", **extra})
    assert r.status_code == 201, r.text
    return r.json()["proposal"]


def clarified(api, pid: str) -> dict:
    use(api, GRAPH_SCRIPT)
    return post_spec(api, pid, use_graph=True, clarify=True)


def answered(api, pid: str) -> dict:
    p = clarified(api, pid)
    r = api.post(f"/ai/proposals/{p['id']}/answers", {"answers": {"q_approval.approver_role": "ops manager"}, "actor": "Asha"})
    assert r.status_code == 200, r.text
    return r.json()


# ── default flags: exactly as before ──

def test_default_flags_keep_the_old_behaviour(fake_api, pid):
    use(fake_api, GRAPH_SCRIPT)
    p = post_spec(fake_api, pid)
    assert p["status"] == "spec_draft" and p["understanding"] is None and p["app_build_id"] is None and p["part_key"] is None
    assert lines(fake_api) == ["WF_AI:SPEC"]
    assert "capability_context" not in user_payload(calls(fake_api)[0])
    assert not [e for e in events(fake_api, p["id"]) if e["event"] in ("understood", "answered")]
    # clarify on its own is ignored: it needs use_graph
    use(fake_api, GRAPH_SCRIPT)
    p2 = post_spec(fake_api, pid, clarify=True)
    assert p2["status"] == "spec_draft" and lines(fake_api) == ["WF_AI:SPEC"]


# ── use_graph without clarify: grounded, never blocked ──

def test_use_graph_grounds_the_spec_without_a_model_call_or_a_block(fake_api, pid):
    use(fake_api, GRAPH_SCRIPT)
    p = post_spec(fake_api, pid, use_graph=True)
    assert p["status"] == "spec_draft"
    u = p["understanding"]
    assert u["v"] == 1 and {"concept:approval", "concept:escalation"} <= {c["key"] for c in u["concepts"] if c["confirmed"]}
    assert lines(fake_api) == ["WF_AI:SPEC"]                      # no UNDERSTAND call unless clarify
    cap = user_payload(calls(fake_api)[0])["capability_context"]
    assert cap["slots"]["sla.due_in"] == "48h" and cap["slots"]["escalation.target_role"] == "role:ops_head"
    assert {c["key"] for c in cap["concepts"]} >= {"concept:approval", "concept:escalation"}
    assert all(c["definition"] and c["prompt_hint"] and c["test_obligations"] for c in cap["concepts"])
    ev = [e for e in events(fake_api, p["id"]) if e["event"] == "understood"]
    assert ev and ev[0]["payload"]["questions"] == len(u["questions"]) and "concept:approval" in ev[0]["payload"]["concepts"]
    assert any(q["id"] == "q_approval.approver_role" and q["answer"] is None for q in u["questions"])   # asked, but not blocking


# ── clarify -> answers ──

def test_clarify_stops_with_questions_and_no_spec_then_answers_continue(fake_api, pid):
    p = clarified(fake_api, pid)
    assert p["status"] == "clarify" and p["spec"] is None
    assert lines(fake_api) == ["WF_AI:UNDERSTAND"]                # the one optional model call, and nothing else
    qs = [q for q in p["understanding"]["questions"] if q["required"] and q["answer"] is None]
    assert [q["id"] for q in qs] == ["q_approval.approver_role"]
    q = qs[0]
    assert q["kind"] == "role" and q["text"] and "role:ops_manager" in q["options"] and q["option_labels"]["role:ops_manager"]
    assert p["understanding"]["summary"]["questions"] == 1 and "Approval" in p["understanding"]["summary"]["understood"]
    assert fake_api.get(f"/ai/proposals/{p['id']}").json()["status"] == "clarify"
    assert {"spec_requested", "understood", "llm_call"} <= {e["event"] for e in events(fake_api, p["id"])}
    # a clarify proposal can be neither approved nor built
    err(fake_api.post(f"/ai/proposals/{p['id']}/spec/approve", {"actor": "Asha"}), 409, "bad_state")
    err(fake_api.post(f"/ai/proposals/{p['id']}/build", {"actor": "Asha"}), 409, "bad_state")

    # bad answers
    d = err(fake_api.post(f"/ai/proposals/{p['id']}/answers", {"answers": {"q_approval.approver_role": "the janitor"}, "actor": "Asha"}), 422, "answer_invalid")
    assert d["qid"] == "q_approval.approver_role"
    d = err(fake_api.post(f"/ai/proposals/{p['id']}/answers", {"answers": {"q_nope": "x"}, "actor": "Asha"}), 422, "answer_invalid")
    assert d["qid"] == "q_nope"
    d = err(fake_api.post(f"/ai/proposals/{p['id']}/answers", {"answers": {}, "actor": "Asha"}), 422, "answer_invalid")
    assert d["qid"] == "q_approval.approver_role"
    err(fake_api.post(f"/ai/proposals/{p['id']}/answers", {"answers": {"q_approval.approver_role": "ops manager"}}), 422, "actor_required")
    assert fake_api.get(f"/ai/proposals/{p['id']}").json()["status"] == "clarify"       # refused answers change nothing

    r = fake_api.post(f"/ai/proposals/{p['id']}/answers", {"answers": {"q_approval.approver_role": "ops manager"}, "actor": "Asha"})
    assert r.status_code == 200, r.text
    a = r.json()
    assert a["status"] == "spec_draft" and a["spec"]["steps"] and a["id"] == p["id"]
    u = a["understanding"]
    assert u["slots"]["approval.approver_role"] == {"value": "role:ops_manager", "from": "answer"}
    assert [q["answer"] for q in u["questions"] if q["id"] == "q_approval.approver_role"] == ["role:ops_manager"]
    assert lines(fake_api) == ["WF_AI:UNDERSTAND", "WF_AI:SPEC"]  # the answers re-run Understand without a second model call
    cap = user_payload(calls(fake_api)[1])["capability_context"]
    assert cap["slots"]["approval.approver_role"] == "role:ops_manager" and cap["slots"]["sla.due_in"] == "48h"
    ev = {e["event"]: e for e in events(fake_api, p["id"])}
    assert ev["answered"]["payload"] == {"actor": "Asha", "qids": ["q_approval.approver_role"]} and "spec_drafted" in ev
    err(fake_api.post(f"/ai/proposals/{p['id']}/answers", {"answers": {"q_approval.approver_role": "ops head"}, "actor": "Asha"}), 409, "bad_state")


def test_a_clarify_proposal_can_be_discarded(fake_api, pid):
    p = clarified(fake_api, pid)
    d = fake_api.post(f"/ai/proposals/{p['id']}/discard", {"actor": "Asha"})
    assert d.status_code == 200 and d.json()["status"] == "discarded"
    err(fake_api.post(f"/ai/proposals/{p['id']}/answers", {"answers": {"q_approval.approver_role": "ops manager"}, "actor": "Asha"}), 409, "bad_state")


def test_clarify_with_nothing_left_to_ask_goes_straight_to_the_spec(fake_api, pid):
    use(fake_api, GRAPH_SCRIPT)
    p = post_spec(fake_api, pid, use_graph=True, clarify=True, answers={"q_approval.approver_role": "ops manager"})
    assert p["status"] == "spec_draft" and p["spec"]["steps"]
    assert lines(fake_api) == ["WF_AI:UNDERSTAND", "WF_AI:SPEC"]


# ── build: a CGX finding reaches the repair prompt ──

def test_a_concept_finding_reaches_the_repair_prompt_and_is_fixed(fake_api, pid):
    a = answered(fake_api, pid)
    approve(fake_api, a)
    b = build(fake_api, a)
    assert b["status"] == "ready" and b["report"]["passed"] is True, json.dumps({k: b["report"][k] for k in ("rounds", "checker", "spec_coverage", "stopped_because", "concept_findings")} | {"fails": [r for r in b["report"]["tests"]["results"] if r["status"] != "passed"]}, default=str)[:6000]
    ls = lines(fake_api)
    assert ls == ["WF_AI:UNDERSTAND", "WF_AI:SPEC", "WF_AI:BUILD", "WF_AI:REPAIR"]
    build_payload, repair_payload = user_payload(calls(fake_api)[2]), user_payload(calls(fake_api)[3])
    assert build_payload["capability_context"]["slots"]["approval.approver_role"] == "role:ops_manager" and "reference_workflow" not in build_payload
    cgx = [f for f in repair_payload["findings"] if str(f["code"]).startswith("CGX")]
    assert {f["code"] for f in cgx} == {"CGX01", "CGX02"} and all(f["severity"] == "warning" for f in cgx)
    assert any("timed_out" in f["message"] or "due_in" in f["message"] for f in cgx)
    ev = [e for e in events(fake_api, a["id"]) if e["event"] == "concept_checked"]
    assert [e["payload"]["round"] for e in ev] == [0, 1] and ev[1]["payload"]["findings"] == []
    assert {"CGX01", "CGX02"} <= set(ev[0]["payload"]["findings"])
    assert b["report"]["concept_findings"] == [] and len(b["report"]["rounds"]) == 2
    # the final workflow carries what the person confirmed
    params = {n["name"]: n["parameters"] for n in b["proposed_definition"]["nodes"]}
    assert params["Manager sign-off"]["assignees"] == ["role:ops_manager"] and params["Manager sign-off"]["due_in"] == "48h"


def test_keep_learns_once_the_graph_core_is_in(fake_api, pid):
    a = answered(fake_api, pid)
    approve(fake_api, a)
    b = build(fake_api, a)
    k = fake_api.post(f"/ai/proposals/{b['id']}/keep", {"actor": "Asha"})
    assert k.status_code == 200, k.text
    assert k.json()["proposal"]["status"] == "kept"
    names = [e["event"] for e in events(fake_api, b["id"])]
    assert "kept" in names
    if not kg_learn.IMPLEMENTED:
        pytest.skip("WP-A kg_learn is not in yet: the learned-event assertions are skipped")
    learned = [e for e in events(fake_api, b["id"]) if e["event"] == "learned"]
    assert learned and learned[0]["payload"]["validated_count"] >= 1 and learned[0]["payload"]["pattern_key"]
    key = learned[0]["payload"]["pattern_key"]
    # the same request again, kept again: the identical skeleton merges and the count grows
    a2 = answered(fake_api, pid)
    approve(fake_api, a2)
    b2 = build(fake_api, a2)
    assert fake_api.post(f"/ai/proposals/{b2['id']}/keep", {"actor": "Asha", "name": uniq("again")}).status_code == 200
    again = [e for e in events(fake_api, b2["id"]) if e["event"] == "learned"]
    assert again and again[0]["payload"]["pattern_key"] == key and again[0]["payload"]["validated_count"] == learned[0]["payload"]["validated_count"] + 1


def test_a_default_flag_proposal_never_triggers_learning(fake_api, pid):
    use(fake_api, "first_try")
    p = fake_api.post("/ai/builder/spec", {"project_id": pid, "request": f"Amounts over 500 are marked for review. ({uniq('r')})", "actor": "Asha"}).json()["proposal"]
    approve(fake_api, p)
    b = build(fake_api, p)
    assert fake_api.post(f"/ai/proposals/{b['id']}/keep", {"actor": "Asha"}).status_code == 200
    assert not [e for e in events(fake_api, b["id"]) if e["event"] == "learned"]
