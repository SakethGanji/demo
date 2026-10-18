"""Capability graph WP-D over HTTP on the fake-LLM engine with real Postgres: the app composer (``/api/ai/apps``), team A's
"operational exceptions" app end to end (CONTRACTS-CAPGRAPH.md 5.1-5.3).

Script: tests/fixtures/fake_llm/capability_graph/app_exceptions_team_a.json (UNDERSTAND, then SPEC / BUILD per part, one scripted REPAIR round for
the intake part). Nothing here calls a live model.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from .conftest import FAKE_LLM_TOKEN, FAKE_SCRIPT, uniq
from .test_ai_builder_api import calls, err, events, lines, use, user_payload

pytestmark = pytest.mark.integration

SCRIPT = json.loads((Path(__file__).resolve().parents[1] / "fixtures" / "fake_llm" / "capability_graph" / "app_exceptions_team_a.json").read_text())["rules"]
TOK = {"X-Test-Token": FAKE_LLM_TOKEN}
PARTS = {"assign_owner", "build_report", "log_exception", "weekly_report", "report_view"}
ORDER = ["build_report", "assign_owner", "log_exception", "weekly_report", "report_view"]


def request_text() -> str:
    return ("We need an application to manage operational exceptions: anyone in payments ops can log an exception, each one gets an owner based on its "
            f"category, if the owner hasn't resolved it within 48 hours escalate it to the head of operations, and every Monday send the ops managers a report "
            f"of open and closed exceptions. ({uniq('r')})")


@pytest.fixture
def clean(fake_api):
    yield
    fake_api.raw("PUT", "/api/_test/fake-llm/script", headers=TOK, json=json.loads(FAKE_SCRIPT.read_text()))


MINE: list[str] = []          # the projects these tests made: what they taught the graph is removed again


def new_project(api, prefix: str) -> str:
    r = api.post("/projects", {"name": uniq(prefix)})
    assert r.status_code in (200, 201), r.text
    MINE.append(r.json()["id"])
    return r.json()["id"]


def forget(db) -> None:
    db.query("DELETE FROM kg_nodes WHERE kind IN ('pattern', 'app') OR project_id = ANY(%s)", (list(MINE),))
    db.query("DELETE FROM kg_learn_events WHERE trigger = 'kept_app' OR project_id = ANY(%s)", (list(MINE),))


@pytest.fixture(autouse=True)
def no_learned_patterns(fake_stack):
    """A Keep teaches the org (a pattern is visible to every project), so the next test's request would be adapted from it, and the learned
    workflows would count in other suites' graph. Each test starts and ends with a graph without learned apps; the compounding itself is
    tested in ``test_a_second_project_is_adapted_from_the_pattern_the_first_one_taught``."""
    forget(fake_stack[1])
    yield
    forget(fake_stack[1])


@pytest.fixture
def pid(fake_api, clean) -> str:
    return new_project(fake_api, "app")


def create(api, pid, actor="Asha", **extra) -> dict:
    r = api.post("/ai/apps", {"project_id": pid, "request": request_text(), "actor": actor, **extra})
    assert r.status_code == 201, r.text
    return r.json()


def post(api, app, action, **body) -> dict:
    r = api.post(f"/ai/apps/{app['id']}/{action}", {"actor": "Asha", **body})
    assert r.status_code == 200, r.text
    return r.json()


def answered(api, pid) -> dict:
    use(api, SCRIPT)
    app = create(api, pid)
    return post(api, app, "answers", answers={"q_assignment.owners": {"payments": "ops analyst", "other": "ops manager"}, "q_exception_case.fields": ["category", "details"]})


def planned_to_specs(api, pid) -> dict:
    app = answered(api, pid)
    return post(api, app, "approve-plan")


def part(app, key) -> dict:
    return next(p for p in app["parts"] if p["key"] == key)


def approve_all(api, app) -> None:
    for p in app["parts"]:
        r = api.post(f"/ai/proposals/{p['proposal_id']}/spec/approve", {"actor": "Asha"})
        assert r.status_code == 200, r.text


def built(api, pid) -> dict:
    app = planned_to_specs(api, pid)
    approve_all(api, app)
    for _ in ORDER:
        app = post(api, app, "build-next")
    return app


# ── clarify -> plan ──

def test_create_asks_the_required_questions_then_answers_produce_the_plan(fake_api, pid):
    use(fake_api, SCRIPT)
    app = create(fake_api, pid)
    assert app["status"] == "clarify" and app["plan"] is None and app["parts"] == []
    assert lines(fake_api) == ["WF_AI:UNDERSTAND"]                                          # the one optional model call, nothing else
    assert [q["id"] for q in app["questions"]] == ["q_assignment.owners", "q_exception_case.fields"] and app["understanding"]["shape"] == "app"
    assert app["summary"]["understood"] and app["id"].startswith("ab_")
    # bad answers change nothing
    d = err(fake_api.post(f"/ai/apps/{app['id']}/answers", {"answers": {"q_assignment.owners": {"payments": "the janitor"}}, "actor": "Asha"}), 422, "answer_invalid")
    assert d["qid"] == "q_assignment.owners"
    err(fake_api.post(f"/ai/apps/{app['id']}/answers", {"answers": {}, "actor": "Asha"}), 422, "answer_invalid")
    err(fake_api.post(f"/ai/apps/{app['id']}/answers", {"answers": {"q_x": 1}}), 422, "actor_required")
    err(fake_api.post(f"/ai/apps/{app['id']}/approve-plan", {"actor": "Asha"}), 409, "bad_state")           # there is no plan yet
    assert fake_api.get(f"/ai/apps/{app['id']}").json()["status"] == "clarify"
    use(fake_api, SCRIPT)
    a = post(fake_api, app, "answers", answers={"q_assignment.owners": {"payments": "ops analyst", "other": "ops manager"}, "q_exception_case.fields": ["category", "details"]})
    assert a["status"] == "planned" and lines(fake_api) == [] and a["questions"] == []                      # answers re-run Understand without a model call
    plan = a["plan"]
    assert {p["key"] for p in plan["parts"]} == PARTS and [t["name"] for t in plan["tables"]] == ["exception_cases"] and a["plan_issues"] == []
    assert {c["name"] for c in plan["tables"][0]["columns"]} >= {"category", "status", "owner_role", "opened_at", "details"}
    assert [p["status"] for p in a["parts"]] == ["pending"] * 5 and a["next_part"] is None
    err(fake_api.post(f"/ai/apps/{app['id']}/answers", {"answers": {"q_assignment.owners": {"x": "ops manager"}}, "actor": "Asha"}), 409, "bad_state")
    assert any(a["id"] == x["id"] and x["status"] == "planned" and x["title"] == "Operational exceptions"
               for x in fake_api.get(f"/ai/apps?project_id={pid}").json()["apps"])


def test_put_plan_revalidates_without_a_model_call(fake_api, pid):
    app = answered(fake_api, pid)
    use(fake_api, SCRIPT)
    bad = json.loads(json.dumps(app["plan"]))
    next(p for p in bad["parts"] if p["key"] == "assign_owner")["depends_on"] = ["log_exception"]          # a cycle
    next(p for p in bad["parts"] if p["key"] == "log_exception")["concepts"].append("concept:approval")
    d = err(fake_api.put(f"/ai/apps/{app['id']}/plan", {"plan": bad, "actor": "Asha"}), 422, "plan_invalid")
    assert {"APP003", "APP006"} <= {i["code"] for i in d["issues"]}
    good = json.loads(json.dumps(app["plan"]))
    good["title"] = "Ops exceptions"
    r = fake_api.put(f"/ai/apps/{app['id']}/plan", {"plan": good, "actor": "Asha"})
    assert r.status_code == 200 and r.json()["plan"]["title"] == "Ops exceptions" and lines(fake_api) == []


# ── approve-plan: one proposal per part ──

def test_approve_plan_drafts_one_spec_per_part_children_first_with_virtual_siblings(fake_api, pid):
    app = answered(fake_api, pid)
    use(fake_api, SCRIPT)
    a = post(fake_api, app, "approve-plan")
    assert a["status"] == "specs" and {p["key"] for p in a["parts"]} == PARTS
    assert all(p["status"] == "spec_draft" and p["proposal_id"] for p in a["parts"])
    assert lines(fake_api) == ["WF_AI:SPEC"] * 5
    drafted = [user_payload(c)["capability_context"]["part"]["part_key"] for c in calls(fake_api) if sys_is(c, "WF_AI:SPEC")]
    assert drafted == ORDER and a["next_part"] == "build_report"                                            # children before the parts that call them
    # each part is a NORMAL proposal the canvas and the review panel already know
    for p in a["parts"]:
        pr = fake_api.get(f"/ai/proposals/{p['proposal_id']}").json()
        assert pr["status"] == "spec_draft" and pr["app_build_id"] == app["id"] and pr["part_key"] == p["key"] and pr["spec"]["steps"]
        assert pr["understanding"] and pr["spec"]["title"]
    log = next(c for c in calls(fake_api) if sys_is(c, "WF_AI:SPEC") and '"part_key":"log_exception"' in c["messages"][-1]["content"])
    cap = user_payload(log)["capability_context"]
    assert cap["part"]["calls"] == ["@part/assign_owner"] and cap["part"]["tables"][0]["name"] == "exception_cases" and cap["part"]["interface"]["slug"] == "log-exception"
    assert cap["siblings"]["@part/assign_owner"]["inputs"] == [{"name": "category", "required": True, "type": "string"}]
    assert {c["key"] for c in cap["concepts"]} == {"concept:intake", "concept:exception_case", "concept:sla", "concept:escalation"}        # the part's own concepts
    again = post(fake_api, app, "approve-plan")                                                             # idempotent: no second draft of any part
    assert again["status"] == "specs" and lines(fake_api) == ["WF_AI:SPEC"] * 5
    assert [p["proposal_id"] for p in again["parts"]] == [p["proposal_id"] for p in a["parts"]]
    err(fake_api.put(f"/ai/apps/{app['id']}/plan", {"plan": a["plan"], "actor": "Asha"}), 409, "bad_state")


def sys_is(call, line):
    return call["messages"][0]["content"].split("\n")[0] == line


# ── build-next, one part per call ──

def test_build_next_needs_approved_specs_and_builds_one_part_per_call_in_dependency_order(fake_api, fake_stack, pid):
    app = planned_to_specs(fake_api, pid)
    err(fake_api.post(f"/ai/apps/{app['id']}/build-next", {"actor": "Asha"}), 409, "spec_not_approved")
    err(fake_api.post(f"/ai/apps/{app['id']}/keep", {"actor": "Asha"}), 409, "parts_not_ready")
    approve_all(fake_api, app)
    seen: list[str] = []
    for i, key in enumerate(ORDER):
        app = post(fake_api, app, "build-next")
        assert app["built"]["part_key"] == key and app["built"]["status"] == "ready" and app["built"]["passed"] is True, app["built"]
        seen.append(key)
        assert {p["key"] for p in app["parts"] if p["status"] == "ready"} == set(seen) and part(app, key)["passed"] is True
        assert app["status"] == ("ready" if i == len(ORDER) - 1 else "building")
        if i < len(ORDER) - 1:
            d = err(fake_api.post(f"/ai/apps/{app['id']}/keep", {"actor": "Asha"}), 409, "parts_not_ready")
            assert {x["part_key"] for x in d["parts"]} == set(ORDER[i + 1:])
    assert app["can_keep"] and app["next_part"] is None and app["keep_blockers"] == []
    done = post(fake_api, app, "build-next")                                                                 # nothing left: the view, no build
    assert done["built"] is None and done["status"] == "ready"
    # the intake part took one scripted repair round: CGX01 (the escalation role is unused) and CGX02 (the timed_out port is not wired) were found in round 0 and fixed in round 1
    log = part(app, "log_exception")
    pr = fake_api.get(f"/ai/proposals/{log['proposal_id']}").json()
    assert log["rounds"] == 2 and pr["report"]["checker"]["errors"] == 0 and pr["report"]["passed"] is True
    checked = [e["payload"] for e in events(fake_api, log["proposal_id"]) if e["event"] == "concept_checked"]
    assert checked[0]["findings"] == ["CGX01", "CGX02"] and checked[-1]["findings"] == []
    assert all(part(app, k)["rounds"] == 1 for k in ORDER if k != "log_exception")
    # the parent was built against the virtual sibling: it calls @part/assign_owner and the checker saw no WF006
    nodes = {n["name"]: n for n in pr["proposed_definition"]["nodes"]}
    assert nodes["Assign owner"]["parameters"]["workflowId"] == "@part/assign_owner"
    assert not [f for f in pr["report"]["checker"]["findings"] if f["code"] in ("WF006", "WF021", "WF024")]
    assert nodes["Resolve exception"]["parameters"]["due_in"] == "48h"
    # nothing is saved before Keep
    assert fake_api.get(f"/projects/{pid}/template-installs").json() == []
    assert counts(fake_stack[1], pid)[:3] == (0, 0, 0)                                                         # no workflow, table or install


# ── keep: one generated bundle ──

def test_keep_installs_the_whole_app_atomically_and_teaches_the_graph(fake_api, fake_stack, pid):
    _srv, db = fake_stack
    app = built(fake_api, pid)
    k = post(fake_api, app, "keep")
    assert k["status"] == "kept" and k["kept_by"] == "Asha" and k["install_id"] and k["learned"]["pattern_key"].startswith("pattern:")
    inst = k["installed"]
    assert set(inst["workflows"]) == PARTS and set(inst["published"]) == {"assign_owner", "build_report"} and set(inst["drafts"]) == {"log_exception", "weekly_report", "report_view"}
    assert [t["name"] for t in inst["tables"].values()] == ["exception_cases"] and inst["needs"] == []
    assert inst["interfaces"]["log_exception"]["path"].endswith("/log-exception") and inst["interfaces"]["report_view"]["kind"] == "custom"
    assert all(p["status"] == "kept" and p["workflow_id"] == inst["workflows"][p["key"]] for p in k["parts"])
    assert k["tests"]["assign_owner"]["failed"] == 0 and k["tests"]["log_exception"]["passed"] == 3
    # the sub-workflows are published, the entries are drafts, the table exists
    for key in ("assign_owner", "build_report"):
        assert len(fake_api.get(f"/workflows/{inst['workflows'][key]}/versions").json()) == 1
    for key in ("log_exception", "weekly_report", "report_view"):
        assert fake_api.get(f"/workflows/{inst['workflows'][key]}/versions").json() == []
        assert fake_api.get(f"/workflows/{inst['workflows'][key]}").json()["active"] is False
    rows = db.query("SELECT name FROM data_tables WHERE project_id = %s", (pid,))
    assert rows == [("exception_cases",)]
    # tokens are resolved: the intake workflow calls the real sub-workflow and writes the real table
    log = fake_api.get(f"/workflows/{inst['workflows']['log_exception']}").json()["definition"]
    nodes = {n["name"]: n for n in log["nodes"]}
    assert nodes["Assign owner"]["parameters"]["workflowId"] == inst["workflows"]["assign_owner"] and nodes["Save exception"]["parameters"]["table"] == "exception_cases"
    assert "@part/" not in json.dumps(log) and "@tpl:" not in json.dumps(log)
    # the interfaces resolve in preview: a form for the intake, a custom page with a table for the report
    form = fake_api.get(f"/workflows/{inst['workflows']['log_exception']}/interface").json()
    assert form["interface"]["kind"] == "form" and form["interface"]["slug"] == "log-exception" and form["live"] is None
    page = fake_api.get(f"/workflows/{inst['workflows']['report_view']}/interface").json()
    assert page["interface"]["kind"] == "custom" and page["interface"]["slug"] == "exceptions-report"
    assert {"type": "table", "bind": "result.rows"} in page["interface"]["screens"]["result"]["components"]
    # the installed workflows run their approved tests green
    for key, wid in inst["workflows"].items():
        r = fake_api.post(f"/workflows/{wid}/tests/run", {})
        assert r.status_code == 200 and r.json()["summary"]["failed"] == 0 and r.json()["summary"]["total"] == 3, (key, r.text)
    # the part proposals are kept and idempotent Keep returns the same install
    assert fake_api.get(f"/ai/proposals/{part(k, 'log_exception')['proposal_id']}").json()["status"] == "kept"
    again = post(fake_api, app, "keep")
    assert again["status"] == "kept" and again["install_id"] == k["install_id"]
    err(fake_api.post(f"/ai/apps/{app['id']}/discard", {"actor": "Asha"}), 409, "bad_state")
    err(fake_api.post(f"/ai/apps/{app['id']}/build-next", {"actor": "Asha"}), 409, "bad_state")
    # learning: the app node composes the five workflows, and a validated app pattern exists once
    napp = db.query("SELECT id FROM kg_nodes WHERE key = %s", (f"app:{app['id']}",))
    assert len(napp) == 1
    composes = db.query("SELECT count(*) FROM kg_edges WHERE src_id = %s AND rel = 'composes'", (napp[0][0],))
    assert composes == [(5,)]
    pat = db.query("SELECT validated_count, subkind FROM kg_nodes WHERE key = %s", (k["learned"]["pattern_key"],))
    assert pat == [(1, "app")]
    ev = db.query("SELECT summary FROM kg_learn_events WHERE trigger = 'kept_app' AND proposal_id = %s", (app["id"],))
    assert len(ev) == 1 and "validated x1" in ev[0][0]


def test_keep_refuses_a_red_approved_test_unless_it_is_accepted_and_then_learns_nothing(fake_api, fake_stack, pid):
    _srv, db = fake_stack
    red = json.loads(json.dumps(SCRIPT))
    rule = next(r for r in red if r["id"] == "spec_report_view")
    spec = json.loads(rule["respond"]["text"])
    t = json.loads(spec["tests"][0]["test_json"])
    t["expect"] = [{"node": "Reply", "op": "status", "value": "error"}]            # the page replies, so this approved test is red and the loop cannot change it
    spec["tests"][0]["test_json"] = json.dumps(t)
    for r in red:
        if r["id"] in ("spec_report_view", "spec_repair_report_view"):
            r["respond"]["text"] = json.dumps(spec)
    use(fake_api, red)
    app = create(fake_api, pid)
    app = post(fake_api, app, "answers", answers={"q_assignment.owners": {"payments": "ops analyst"}, "q_exception_case.fields": ["category"]})
    app = post(fake_api, app, "approve-plan")
    approve_all(fake_api, app)
    for _ in ORDER:
        app = post(fake_api, app, "build-next")
    assert app["status"] == "ready" and part(app, "report_view")["passed"] is False and not app["keep_blockers"] == []
    assert [b["reason"] for b in app["keep_blockers"]] == ["tests_failing"] and app["can_keep"]
    err(fake_api.post(f"/ai/apps/{app['id']}/keep", {"actor": "Asha"}), 409, "tests_failing")
    k = post(fake_api, app, "keep", accept_failing=True)
    assert k["status"] == "kept" and part(k, "report_view")["accepted_failing"] is True and k["learned"]["pattern_key"] is None       # kept, but not a validated pattern
    assert db.query("SELECT count(*) FROM kg_nodes WHERE kind = 'pattern' AND label = %s", ("Operational exceptions",)) in ([(0,)], [(1,)])


# ── discard writes nothing ──

def counts(db, pid) -> tuple:
    q = lambda sql: db.query(sql, (pid,))[0][0]   # noqa: E731
    return (q("SELECT count(*) FROM workflows WHERE project_id = %s"), q("SELECT count(*) FROM data_tables WHERE project_id = %s"),
            q("SELECT count(*) FROM template_installs WHERE project_id = %s"), q("SELECT count(*) FROM kg_nodes WHERE project_id = %s"))


def test_discard_drops_every_part_and_writes_nothing(fake_api, fake_stack, pid):
    _srv, db = fake_stack
    before = counts(db, pid)
    app = planned_to_specs(fake_api, pid)
    approve_all(fake_api, app)
    for _ in range(2):
        app = post(fake_api, app, "build-next")                      # two parts built, three still specs
    d = post(fake_api, app, "discard")
    assert d["status"] == "discarded"
    props = db.query("SELECT status FROM ai_proposals WHERE app_build_id = %s ORDER BY part_key", (app["id"],))
    assert len(props) == 5 and {s for (s,) in props} == {"discarded"}
    assert counts(db, pid) == before
    assert post(fake_api, app, "discard")["status"] == "discarded"                                                 # idempotent
    err(fake_api.post(f"/ai/apps/{app['id']}/keep", {"actor": "Asha"}), 409, "bad_state")
    err(fake_api.post(f"/ai/apps/{app['id']}/approve-plan", {"actor": "Asha"}), 409, "bad_state")


def test_a_clarify_app_can_be_discarded_and_other_projects_cannot_see_it(fake_api, pid):
    use(fake_api, SCRIPT)
    app = create(fake_api, pid)
    other = new_project(fake_api, "other")
    err(fake_api.get(f"/ai/apps/{app['id']}?project_id={other}"), 404, "not_found")
    assert fake_api.get(f"/ai/apps/{app['id']}?project_id={pid}").status_code == 200
    err(fake_api.get("/ai/apps/ab_nope"), 404, "not_found")
    assert post(fake_api, app, "discard")["status"] == "discarded"
    assert fake_api.get(f"/ai/apps?project_id={other}").json()["apps"] == []
    err(fake_api.post("/ai/apps", {"project_id": pid, "request": "x"}), 422, "actor_required")
    err(fake_api.post("/ai/apps", {"project_id": pid, "request": "   ", "actor": "Asha"}), 422, "request_invalid")


def test_the_build_seam_does_not_change_an_ordinary_proposal(fake_api, pid):
    built(fake_api, pid)                                             # the composer has now wrapped load_ctx in this engine process
    from .test_ai_builder_api import ready

    b = ready(fake_api, pid)
    assert b["status"] == "ready" and b["app_build_id"] is None and b["report"]["passed"] is True


# ── the graph compounds ──

REQ_B = ("Build an app for card fraud alerts: log an alert, assign an analyst by region, escalate to the fraud lead if it isn't handled in 4 hours, "
         "and send fraud managers a daily summary.")


def test_a_second_project_is_adapted_from_the_pattern_the_first_one_taught(fake_api, fake_stack, pid):
    built_a = built(fake_api, pid)
    kept = post(fake_api, built_a, "keep")
    pattern = kept["learned"]["pattern_key"]
    other = new_project(fake_api, "cards")
    use(fake_api, SCRIPT)
    r = fake_api.post("/ai/apps", {"project_id": other, "request": f"{REQ_B} ({uniq('r')})", "actor": "Bea"})
    assert r.status_code == 201, r.text
    b = r.json()
    assert b["understanding"]["adapt_from"]["pattern"] == pattern and b["summary"]["reusing"]["patterns"] == 1
    assert [q["id"] for q in b["questions"]] == ["q_assignment.owners"]                      # the fields come from the pattern: one question, not two
    b = post(fake_api, b, "answers", answers={"q_assignment.owners": {"other": "fraud analyst"}})
    banner = b["adapted_from"]
    assert banner["pattern"] == pattern and banner["label"] == "Operational exceptions" and banner["validated_count"] == 1
    diff = {d["slot"]: (d["from"], d["to"]) for d in banner["diff"]}
    assert diff["sla.due_in"] == ("48h", "4h") and diff["escalation.target_role"] == ("role:ops_head", "role:fraud_lead")
    assert diff["report.schedule"] == ("0 8 * * 1", "0 8 * * *") and diff["exception_case.entity"] == ("exception", "alert")
    assert {p["key"] for p in b["parts"]} == {"assign_owner", "build_report", "log_alert", "weekly_report", "report_view"}
    assert part(b, "weekly_report")["cron"] == "0 8 * * *" and b["plan"]["tables"][0]["name"] == "alert_cases" and b["plan_issues"] == []
    # the pattern is org-visible but its reference bundle carries no host, address or literal person
    ref = fake_stack[1].query("SELECT props->'reference_bundle' FROM kg_nodes WHERE key = %s", (pattern,))[0][0]
    text = json.dumps(ref)
    assert "role:" not in text and "@example.com" not in text and "@part/" not in text and "@slot:" in text and "@tpl:" in text


# ── a part that already exists in the project is reused, not rebuilt ──

def test_a_reused_sub_workflow_is_called_by_its_real_id_and_not_built_or_installed(fake_api, fake_stack, pid):
    from ._stage8_b4_support import make, publish

    child = make(fake_api, {"name": uniq("Assign owner"), "project_id": pid, "settings": {}, "connections": [{"source_node": "Inputs", "target_node": "Pick owner"}],
                            "nodes": [{"name": "Inputs", "type": "ExecuteWorkflowTrigger", "position": {"x": 0, "y": 0},
                                       "parameters": {"inputSource": "fields", "fields": [{"name": "category", "type": "string", "required": True}]}},
                                      {"name": "Pick owner", "type": "Set", "position": {"x": 240, "y": 0},
                                       "parameters": {"mode": "manual", "keepOnlySet": True, "fields": [{"name": "owner_role", "value": "role:ops_analyst"}]}}]})
    publish(fake_api, child)
    assert fake_api.post("/knowledge/rebuild", {"project_id": pid}).status_code == 200
    app = answered(fake_api, pid)
    plan = json.loads(json.dumps(app["plan"]))
    next(p for p in plan["parts"] if p["key"] == "assign_owner")["reuse"] = {"capability": f"wf:{child}", "label": "Assign owner"}
    bad = json.loads(json.dumps(plan))
    next(p for p in bad["parts"] if p["key"] == "assign_owner")["reuse"] = {"capability": "wf:wf_not_in_this_project"}
    err(fake_api.put(f"/ai/apps/{app['id']}/plan", {"plan": bad, "actor": "Asha"}), 422, "plan_invalid")      # APP008
    assert fake_api.put(f"/ai/apps/{app['id']}/plan", {"plan": plan, "actor": "Asha"}).status_code == 200
    app = post(fake_api, app, "approve-plan")
    assert part(app, "assign_owner")["status"] == "reused" and part(app, "assign_owner")["proposal_id"] is None
    assert len([p for p in app["parts"] if p["proposal_id"]]) == 4                                          # four specs, none for the reused part
    approve_all(fake_api, {"parts": [p for p in app["parts"] if p["proposal_id"]]})
    keys = []
    for _ in range(4):
        app = post(fake_api, app, "build-next")
        keys.append(app["built"]["part_key"])
        assert app["built"]["passed"] is True, app["built"]
    assert keys == ["build_report", "log_exception", "weekly_report", "report_view"] and app["status"] == "ready"
    k = post(fake_api, app, "keep")
    assert set(k["installed"]["workflows"]) == PARTS - {"assign_owner"}
    log = fake_api.get(f"/workflows/{k['installed']['workflows']['log_exception']}").json()["definition"]
    assert {n["name"]: n for n in log["nodes"]}["Assign owner"]["parameters"]["workflowId"] == child       # the real workflow, no token, no copy
