"""Journey: two teams, one pattern, compounding (PLAN-CAPABILITY-GRAPH section 9, Acts 1-4), entirely over HTTP on the fake LLM.

Script: tests/fixtures/fake_llm/capability_graph/demo_two_teams.json (team A's rules, then team B's, told apart by the request text).

  Act 1  Payments Ops: Escalation is a gap in "What we can do".
  Act 2  Team A builds the operational exceptions app: 2 questions, 12 model calls, one repair round, Keep teaches the org a pattern.
  Act 3  Cards Ops asks for card fraud alerts: adapted from that pattern, 1 question, 11 model calls, no repair round.
  Act 4  The numbers: questions 2 -> 1, pattern validated x1 -> x2, derived_from, memory events, the matrix.

A Keep teaches the org (patterns are visible to every project), so everything this test teaches the graph is removed again.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ..integration.conftest import FAKE_LLM_TOKEN, FAKE_SCRIPT, uniq
from ..integration.test_ai_apps_http import ORDER, approve_all, forget, new_project, post
from ..integration.test_ai_builder_api import lines, use

pytestmark = pytest.mark.integration

DEMO = json.loads((Path(__file__).resolve().parents[1] / "fixtures" / "fake_llm" / "capability_graph" / "demo_two_teams.json").read_text())["rules"]
TOK = {"X-Test-Token": FAKE_LLM_TOKEN}
REQ_A = ("We need an application to manage operational exceptions: anyone in payments ops can log an exception, each one gets an owner based on its "
         "category, if the owner hasn't resolved it within 48 hours escalate it to the head of operations, and every Monday send the ops managers a report "
         "of open and closed exceptions.")
# Team B's escalation target and report recipient are the same role (role:fraud_lead): the skeleton must still match team A's, which has two roles.
REQ_B = ("Build an app for card fraud alerts: log an alert, assign an analyst by region, escalate to the fraud lead if it isn't handled in 4 hours, "
         "and send fraud managers a daily summary.")


@pytest.fixture(autouse=True)
def isolated(fake_api, fake_stack):
    forget(fake_stack[1])
    yield
    forget(fake_stack[1])
    fake_api.raw("PUT", "/api/_test/fake-llm/script", headers=TOK, json=json.loads(FAKE_SCRIPT.read_text()))


def create(api, pid, request, actor):
    r = api.post("/ai/apps", {"project_id": pid, "request": f"{request} ({uniq('r')})", "actor": actor})
    assert r.status_code == 201, r.text
    return r.json()


def build_and_keep(api, app, actor):
    app = post(api, app, "approve-plan", actor=actor)
    approve_all(api, app)
    for _ in ORDER_B if "log_alert" in {p["key"] for p in app["parts"]} else ORDER:
        app = post(api, app, "build-next", actor=actor)
    assert app["status"] == "ready" and app["can_keep"]
    return app, post(api, app, "keep", actor=actor)


ORDER_B = ["build_report", "assign_owner", "log_alert", "weekly_report", "report_view"]


def matrix_row(api, pid, concept):
    rows = api.get(f"/knowledge/capabilities?project_id={pid}").json()
    return next(r for r in rows["rows"] if r["concept"]["key"] == concept), rows["totals"]


def memory_summaries(api, pid):
    return [e["summary"] for e in api.get(f"/knowledge/memory?project_id={pid}").json()["items"] if e["trigger"] == "kept_app"]


def edges(db, rel, src_kind=None):
    return db.query("SELECT s.key, d.key FROM kg_edges e JOIN kg_nodes s ON s.id = e.src_id JOIN kg_nodes d ON d.id = e.dst_id WHERE e.rel = %s "
                    "AND (%s::text IS NULL OR s.kind = %s)", (rel, src_kind, src_kind))


def test_two_teams_compound(fake_api, fake_stack):
    _srv, db = fake_stack
    team_a = new_project(fake_api, "payments-ops")
    team_b = new_project(fake_api, "cards-ops")
    for p in (team_a, team_b):
        assert fake_api.post("/knowledge/rebuild", {"project_id": p}).status_code == 200

    # ── Act 1: what the org knows today ──
    esc, totals0 = matrix_row(fake_api, team_a, "concept:escalation")
    assert esc["gap"] is True and totals0["gaps"] >= 1 and totals0["apps"] == 0          # the 6 seeded template patterns are there, no learned app

    # ── Act 2: team A ──
    use(fake_api, DEMO)
    a = create(fake_api, team_a, REQ_A, "Asha")
    a_questions = len(a["questions"])
    assert [q["id"] for q in a["questions"]] == ["q_assignment.owners", "q_exception_case.fields"] and a["understanding"].get("adapt_from") in (None, {})
    a_reusing = a["summary"]["reusing"]
    a = post(fake_api, a, "answers", actor="Asha", answers={"q_assignment.owners": {"payments": "ops analyst", "other": "ops manager"},
                                                            "q_exception_case.fields": ["category", "details"]})
    assert a["adapted_from"] is None and {p["key"] for p in a["parts"]} == set(ORDER)
    a, a_kept = build_and_keep(fake_api, a, "Asha")
    a_repairs = sum(p["rounds"] - 1 for p in a["parts"])
    a_calls = lines(fake_api)
    assert a_repairs == 1 and a_calls.count("WF_AI:REPAIR") == 1 and len(a_calls) == 12, a_calls
    pattern = a_kept["learned"]["pattern_key"]
    assert pattern and a_kept["learned"]["validated_count"] == 1
    esc, totals1 = matrix_row(fake_api, team_a, "concept:escalation")
    assert esc["gap"] is False and esc["apps"] == 1 and esc["validated"] >= 1 and totals1["gaps"] < totals0["gaps"]
    assert len(memory_summaries(fake_api, team_a)) == 1 and "validated x1" in memory_summaries(fake_api, team_a)[0]
    assert edges(db, "derived_from", "app") == [(f"app:{a['id']}", pattern)]         # the origin app is lineage-linked to the pattern it created
    assert db.query("SELECT validated_count FROM kg_nodes WHERE key = %s", (pattern,)) == [(1,)]

    # ── Act 3: team B ──
    use(fake_api, DEMO)
    b = create(fake_api, team_b, REQ_B, "Bea")
    b_questions = len(b["questions"])
    assert b["understanding"]["adapt_from"]["pattern"] == pattern and b["summary"]["reusing"]["patterns"] == a_reusing["patterns"] + 1
    assert [q["id"] for q in b["questions"]] == ["q_assignment.owners"]
    b = post(fake_api, b, "answers", actor="Bea", answers={"q_assignment.owners": {"other": "fraud analyst"}})
    banner = b["adapted_from"]
    assert banner["pattern"] == pattern and banner["label"] == "Operational exceptions" and banner["validated_count"] == 1
    diff = {d["slot"]: (d["from"], d["to"]) for d in banner["diff"]}
    assert diff["sla.due_in"] == ("48h", "4h") and diff["escalation.target_role"] == ("role:ops_head", "role:fraud_lead")
    assert diff["report.schedule"] == ("0 8 * * 1", "0 8 * * *") and diff["exception_case.entity"] == ("exception", "alert")
    assert {p["key"] for p in b["parts"]} == {"assign_owner", "build_report", "log_alert", "weekly_report", "report_view"}
    b, b_kept = build_and_keep(fake_api, b, "Bea")
    b_repairs = sum(p["rounds"] - 1 for p in b["parts"])
    b_calls = lines(fake_api)
    assert b_repairs == 0 and "WF_AI:REPAIR" not in b_calls and len(b_calls) == 11, b_calls          # understand + 5 specs + 5 builds: green first try
    assert all(p["passed"] for p in b["parts"])

    # ── Act 4: the numbers ──
    assert (a_questions, b_questions) == (2, 1)
    assert b_kept["learned"]["pattern_key"] == pattern and b_kept["learned"]["validated_count"] == 2
    assert db.query("SELECT validated_count FROM kg_nodes WHERE key = %s", (pattern,)) == [(2,)]
    assert db.query("SELECT count(*) FROM kg_nodes WHERE kind = 'pattern' AND subkind = 'app'") == [(1,)]                 # one pattern, not two
    # every kept app is lineage-linked to the one pattern it instantiates: two derived_from edges, app -> pattern
    assert sorted(edges(db, "derived_from", "app")) == sorted([(f"app:{a['id']}", pattern), (f"app:{b['id']}", pattern)])
    apps = db.query("SELECT key FROM kg_nodes WHERE kind = 'app' ORDER BY key")
    assert {k for (k,) in apps} == {f"app:{a['id']}", f"app:{b['id']}"}
    cited = db.query("SELECT DISTINCT n.project_id FROM kg_edges e JOIN kg_nodes p ON p.id = e.src_id JOIN kg_nodes n ON n.id = e.dst_id "
                     "WHERE e.rel = 'validated_by' AND p.key = %s", (pattern,))
    assert {r[0] for r in cited} == {team_a, team_b}
    assert db.query("SELECT count(*) FROM kg_edges e JOIN kg_nodes a ON a.id = e.src_id WHERE e.rel = 'composes' AND a.kind = 'app'") == [(10,)]
    # memory: the "N tests green" text is the sum of the passed tests over the app's parts, never 0
    import re
    for app_, text in ((a, memory_summaries(fake_api, team_a)[0]), (b, memory_summaries(fake_api, team_b)[0])):
        green = int(re.search(r"(\d+) tests green", text).group(1))
        assert green > 0 and green == sum(p["tests"]["passed"] for p in app_["parts"]), (text, app_["parts"])
    # memory: both Keeps are recorded; the second says x2
    evs = db.query("SELECT summary, project_id FROM kg_learn_events WHERE trigger = 'kept_app' ORDER BY id")
    assert len(evs) == 2 and "validated x1" in evs[0][0] and "validated x2" in evs[1][0] and {e[1] for e in evs} == {team_a, team_b}
    assert "validated x2" in memory_summaries(fake_api, team_b)[0]
    # the matrix: no gap in either project, and the concept now counts two teams
    for p in (team_a, team_b):
        esc, _t = matrix_row(fake_api, p, "concept:escalation")
        assert esc["gap"] is False and esc["validated"] == 2 and len(esc["teams"]) == 2, esc
    # the pattern carries evidence from both projects and no host, address or person
    props = db.query("SELECT props FROM kg_nodes WHERE key = %s", (pattern,))[0][0]
    assert len(props["evidence"]) == 2 and "@example.com" not in json.dumps(props["reference_bundle"])
    # the compounding table
    table = {"questions": (a_questions, b_questions), "reused": (a_reusing, b["summary"]["reusing"]), "pattern_validated": (1, 2), "repairs": (a_repairs, b_repairs)}
    print("ACT4", json.dumps(table))
