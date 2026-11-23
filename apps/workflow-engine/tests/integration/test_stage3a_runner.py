"""Stage 3A testing core over HTTP on real Postgres: test runs of Refund triage with mocks, unmocked-step
failures, on_error/retry behaviour under mocks, frozen time, hidden test executions, lock rules, approve/unlock."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from .conftest import uniq

pytestmark = pytest.mark.integration

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "workflows" / "refund_triage.json"
COLUMNS = [{"name": "order_id", "type": "string"}, {"name": "amount", "type": "number"},
           {"name": "reason", "type": "string"}, {"name": "status", "type": "string"}]
RISK_URL = "http://risk.example.test/v1/score"


def tid() -> str:
    return "t_" + uuid.uuid4().hex[:8]


def refund(tests: list | None = None, defaults: dict | None = None) -> dict:
    wf = json.loads(FIXTURE.read_text().replace("__REFUND_API_URL__", RISK_URL))
    wf["name"] = uniq("s3a-refund")
    wf["settings"] = {}
    for n in wf["nodes"]:
        if n["type"] == "Webhook":
            n["parameters"]["path"] = uniq("refund")
    wf["tests"] = tests or []
    wf["test_defaults"] = defaults or {}
    return wf


def make(api, body: dict) -> str:
    r = api.post("/workflows", body)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def put(api, wid: str, body: dict, **extra):
    payload = {k: body[k] for k in ("name", "nodes", "connections", "settings", "tests", "test_defaults") if k in body}
    return api.put(f"/workflows/{wid}", {**payload, **extra})


def case(name: str, amount: int, expect: list, mocks: dict | None = None, **kw) -> dict:
    return {
        "id": kw.pop("id", None) or tid(), "name": name, "target": {"kind": "workflow"},
        "input": {"items": [{"body": {"order_id": "o-1", "amount": amount, "reason": "late"}}]},
        "mocks": mocks or {}, "expect": expect, "category": "normal", **kw,
    }


RISK_MOCK = {"http": {"status": 200, "body": {"decision": "review", "score": 0.7}}}
TABLE_MOCK = {"items": [{"id": 1, "order_id": "o-1", "status": "logged"}]}
DEFAULTS = {"mocks": {"Risk API": RISK_MOCK, "Log escalated": TABLE_MOCK, "Log approved": TABLE_MOCK}}


def run(api, wid: str, **body):
    r = api.post(f"/workflows/{wid}/tests/run", body)
    assert r.status_code == 200, r.text
    return r.json()


def result(report: dict, test_id: str) -> dict:
    return next(r for r in report["results"] if r["test_id"] == test_id)


# ── running tests ────────────────────────────────────────────────────────────


def test_tests_and_defaults_round_trip_through_save_and_load(api):
    t = case("big refund", 900, [{"node": "Amount over 500", "port": "true", "op": "count", "value": 1}])
    wid = make(api, refund([t], DEFAULTS))
    d = api.get(f"/workflows/{wid}").json()["definition"]
    assert d["tests"][0]["id"] == t["id"] and d["tests"][0]["status"] == "draft" and d["tests"][0]["locked"] is False
    assert d["test_defaults"]["mocks"]["Risk API"] == RISK_MOCK


def test_escalated_refund_runs_with_an_http_mock_and_a_mocked_table(api):
    expect = [
        {"node": "Amount over 500", "port": "true", "op": "count", "value": 1},
        {"node": "Amount over 500", "port": "false", "op": "count", "value": 0},
        {"node": "Risk API", "path": "statusCode", "op": "eq", "value": 200},
        {"node": "Risk API", "path": "body.decision", "op": "eq", "value": "review"},
        {"node": "Risk API", "subject": "request", "path": "body.amount", "op": "eq", "value": 900},
        {"node": "Risk API", "subject": "request", "path": "url", "op": "eq", "value": RISK_URL},
        {"node": "Log escalated", "op": "status", "value": "success"},
        {"node": "Log approved", "op": "status", "value": "not_run"},
    ]
    t = case("big refund", 900, expect)
    wid = make(api, refund([t], DEFAULTS))
    report = run(api, wid)
    r = result(report, t["id"])
    assert r["status"] == "passed", r["failures"]
    assert r["ports_taken"]["Amount over 500"] == ["true"]
    assert r["requests"]["Risk API"][0]["method"] == "POST"
    assert report["summary"] == {"total": 1, "passed": 1, "failed": 0, "errored": 0, "approved_total": 0, "approved_failed": 0}
    assert report["badges"]["Risk API"] == "pass"
    grid = {row["node"]: row["categories"] for row in report["grid"]["rows"]}
    assert "Webhook" not in grid and grid["Risk API"]["ext_timeout"]["status"] == "none"
    assert grid["Risk API"]["boundary"]["status"] == "na" and grid["Amount over 500"]["boundary"]["status"] == "none"


def test_a_404_http_mock_is_a_normal_reply_with_status_404(api):
    mocks = {"Risk API": {"http": {"status": 404, "body": {"error": "unknown order"}}}}
    t = case("not found", 900, [{"node": "Risk API", "path": "statusCode", "op": "eq", "value": 404},
                                {"node": "Risk API", "op": "status", "value": "success"}], mocks, category="ext_error")
    wid = make(api, refund([t], DEFAULTS))
    r = result(run(api, wid), t["id"])
    assert r["status"] == "passed", r["failures"]


def test_smart_mock_rules_and_expressions_echo_the_request(api):
    smart = {"rules": [{"when": {"path": "amount", "op": "gt", "value": 1000}, "then": {"http": {"status": 200, "body": {"decision": "deny"}}}}],
             "default": {"http": {"status": 200, "body": {"decision": "review", "echo": "{{ $json.order_id }}"}}}}
    t1 = case("low", 900, [{"node": "Risk API", "path": "body.echo", "op": "eq", "value": "o-1"}], {"Risk API": smart})
    t2 = case("high", 5000, [{"node": "Risk API", "path": "body.decision", "op": "eq", "value": "deny"}], {"Risk API": smart})
    wid = make(api, refund([t1, t2], DEFAULTS))
    report = run(api, wid)
    assert [r["status"] for r in report["results"]] == ["passed", "passed"], [r["failures"] for r in report["results"]]


def test_an_unmocked_outside_step_fails_with_fixes_and_stops_the_run(api):
    t = case("no mocks", 900, [])
    wid = make(api, refund([t], {}))  # no default mocks at all
    r = result(run(api, wid), t["id"])
    assert r["status"] == "error"
    f = r["failures"][0]
    assert f["kind"] == "unmocked" and f["node"] == "Risk API"
    assert f["fixes"] == ["generate_from_spec", "use_last_real_reply"]
    assert r["node_status"]["Risk API"] == "error" and r["node_status"]["Log escalated"] == "not_run"


def test_unmocked_step_error_has_a_code_and_details_on_the_execution(api):
    t = case("no mocks", 900, [])
    wid = make(api, refund([t], {}))
    r = result(run(api, wid), t["id"])
    ex = api.get(f"/executions/{r['execution_id']}").json()
    err = ex["errors"][0]
    assert err["node_name"] == "Risk API" and err["error_code"] == "TEST_UNMOCKED_EFFECT"
    assert err["details"]["effect"] == "network"


def test_error_mock_with_error_output_routes_to_the_error_port(api):
    wf = refund([], DEFAULTS)
    risk = next(n for n in wf["nodes"] if n["name"] == "Risk API")
    risk["on_error"] = "error_output"
    wf["nodes"].append({"name": "Alert", "type": "Set", "position": {"x": 0, "y": 0},
                        "parameters": {"mode": "manual", "fields": [{"name": "alerted", "value": "yes"}]}})
    wf["connections"].append({"source_node": "Risk API", "target_node": "Alert", "source_output": "error", "target_input": "main"})
    t = case("risk is down", 900, [
        {"node": "Risk API", "port": "error", "path": "error.message", "op": "contains", "value": "boom"},
        {"node": "Alert", "path": "alerted", "op": "eq", "value": "yes"},
        {"node": "Log escalated", "op": "status", "value": "not_run"},
    ], {"Risk API": {"error": {"message": "boom: upstream 500", "code": "E_UP"}}}, category="ext_error")
    wf["tests"] = [t]
    wid = make(api, wf)
    r = result(run(api, wid), t["id"])
    assert r["status"] == "passed", r["failures"]
    assert r["ports_taken"]["Risk API"] == ["error"]


def test_a_timeout_mock_with_on_error_continue_keeps_the_run_going(api):
    wf = refund([], DEFAULTS)
    next(n for n in wf["nodes"] if n["name"] == "Risk API")["on_error"] = "continue"
    t = case("slow risk", 900, [{"node": "Risk API", "path": "error", "op": "contains", "value": "TEST_MOCK_TIMEOUT"},
                                {"node": "Risk API", "op": "status", "value": "error"},  # production marks the run failed too
                                {"node": "Log escalated", "op": "status", "value": "success"}],
             {"Risk API": {"timeout": True}}, category="ext_timeout")
    wf["tests"] = [t]
    wid = make(api, wf)
    r = result(run(api, wid), t["id"])
    assert r["status"] == "passed", r["failures"]


def test_an_unexpected_node_error_is_a_node_error_failure_unless_the_test_expects_it(api):
    mocks = {"Risk API": {"error": {"message": "nope"}}}
    bad = case("unexpected", 900, [], mocks)
    ok = case("expected", 900, [{"node": "Risk API", "op": "status", "value": "error"}], mocks)
    wid = make(api, refund([bad, ok], DEFAULTS))
    report = run(api, wid)
    assert result(report, bad["id"])["status"] == "error"
    assert result(report, bad["id"])["failures"][0]["kind"] == "node_error"
    assert result(report, ok["id"])["status"] == "passed"
    assert report["badges"]["Risk API"] == "fail"


def test_retry_on_fail_retries_an_error_mock_before_giving_up(api):
    wf = refund([], DEFAULTS)
    risk = next(n for n in wf["nodes"] if n["name"] == "Risk API")
    risk["retry_on_fail"] = 2
    t = case("flaky", 900, [{"node": "Risk API", "op": "status", "value": "error"},
                            {"node": "Risk API", "subject": "request", "op": "count", "value": 3}],
             {"Risk API": {"error": {"message": "503 again"}}})
    wf["tests"] = [t]
    wid = make(api, wf)
    r = result(run(api, wid), t["id"])
    assert r["status"] == "passed", r["failures"]
    ex = api.get(f"/executions/{r['execution_id']}").json()
    assert ex["node_metrics"]["Risk API"]["retries"] == 2


def test_frozen_time_makes_runs_identical(api):
    wf = refund([], DEFAULTS)
    wf["nodes"].append({"name": "Stamp", "type": "Set", "position": {"x": 0, "y": 0},
                        "parameters": {"mode": "manual", "fields": [{"name": "at", "value": "{{ date_now() }}"},
                                                                   {"name": "ms", "value": "{{ now() }}"}]}})
    wf["connections"].append({"source_node": "Log approved", "target_node": "Stamp", "source_output": "main", "target_input": "main"})
    t = case("stamp", 100, [{"node": "Stamp", "path": "at", "op": "eq", "value": "2026-01-01T09:00:00+00:00"}], now="2026-01-01T09:00:00+00:00")
    wf["tests"] = [t]
    wid = make(api, wf)
    first = result(run(api, wid), t["id"])
    second = result(run(api, wid), t["id"])
    assert first["status"] == "passed", first["failures"]
    assert first["outputs"] == second["outputs"]
    assert first["outputs"]["Stamp"]["main"][0]["ms"] == 1767258000000


def test_a_node_target_feeds_its_own_input_and_stops_there(api):
    t = {"id": tid(), "name": "normalize alone", "target": {"kind": "node", "node": "Normalize"},
         "input": {"items": [{"body": {"order_id": "o-9", "amount": 5, "reason": "dup"}}]}, "mocks": {},
         "expect": [{"node": "Normalize", "path": "order_id", "op": "eq", "value": "o-9"},
                    {"node": "Amount over 500", "op": "status", "value": "not_run"}], "category": "normal"}
    wid = make(api, refund([t], {}))
    r = result(run(api, wid), t["id"])
    assert r["status"] == "passed", r["failures"]


def test_a_range_target_runs_from_one_node_to_another(api):
    t = {"id": tid(), "name": "range", "target": {"kind": "range", "from": "Normalize", "to": "Amount over 500"},
         "input": {"items": [{"body": {"order_id": "o-9", "amount": 900, "reason": "x"}}]}, "mocks": {},
         "expect": [{"node": "Amount over 500", "port": "true", "op": "count", "value": 1},
                    {"node": "Risk API", "op": "status", "value": "not_run"}], "category": "boundary"}
    wid = make(api, refund([t], {}))
    r = result(run(api, wid), t["id"])
    assert r["status"] == "passed", r["failures"]


def test_the_automatic_contract_check_fails_a_mock_that_breaks_a_declared_shape(api):
    wf = refund([], DEFAULTS)
    risk = next(n for n in wf["nodes"] if n["name"] == "Risk API")
    risk["contract"] = {"source": "declared", "approved_by": "sam", "outputs": {"main": {
        "type": "object", "properties": {"statusCode": {"type": "integer"},
                                         "body": {"type": "object", "properties": {"decision": {"type": "string", "enum": ["approve", "deny"]}},
                                                  "required": ["decision"]}}, "required": ["statusCode", "body"]}}}
    t = case("contract", 900, [])
    wf["tests"] = [t]
    wid = make(api, wf)
    r = result(run(api, wid), t["id"])
    assert r["status"] == "failed"
    f = [x for x in r["failures"] if x["kind"] == "contract"]
    assert f and f[0]["node"] == "Risk API" and f[0]["path"] == "body.decision"


def test_a_declared_input_shape_is_checked_against_the_arriving_items(api):
    wf = refund([], DEFAULTS)
    norm = next(n for n in wf["nodes"] if n["name"] == "Normalize")
    norm["contract"] = {"source": "declared", "approved_by": "sam", "outputs": {"main": {"type": "unknown"}},
                        "inputs": {"main": {"type": "object", "properties": {"body": {"type": "object", "properties": {"amount": {"type": "number", "minimum": 0}}}}}}}
    t = case("negative", -5, [])
    wf["tests"] = [t]
    wid = make(api, wf)
    r = result(run(api, wid), t["id"])
    f = [x for x in r["failures"] if x["kind"] == "contract" and x.get("direction") == "input"]
    assert f and f[0]["node"] == "Normalize" and f[0]["path"] == "body.amount"


def test_an_unsaved_definition_is_run_without_saving(api):
    wid = make(api, refund([], DEFAULTS))
    t = case("draft only", 900, [{"node": "Risk API", "path": "statusCode", "op": "eq", "value": 200}])
    stored = api.get(f"/workflows/{wid}").json()["definition"]
    draft = {"nodes": stored["nodes"], "connections": stored["connections"], "tests": [t], "test_defaults": DEFAULTS}
    report = run(api, wid, definition=draft)
    assert report["summary"]["passed"] == 1
    assert api.get(f"/workflows/{wid}").json()["definition"]["tests"] == []


def test_run_tests_with_ids_runs_only_those_and_unknown_ids_are_404(api):
    a, b = case("a", 900, []), case("b", 100, [])
    wid = make(api, refund([a, b], DEFAULTS))
    assert [r["test_id"] for r in run(api, wid, ids=[b["id"]])["results"]] == [b["id"]]
    assert api.post(f"/workflows/{wid}/tests/run", {"ids": ["t_00000000"]}).status_code == 404


# ── test executions stay out of the way ──────────────────────────────────────


def test_test_executions_are_saved_with_mode_test_and_hidden_from_listings(api, test_db):
    t = case("persisted", 900, [])
    wid = make(api, refund([t], DEFAULTS))
    r = result(run(api, wid), t["id"])
    eid = r["execution_id"]
    assert test_db.query("SELECT mode FROM executions WHERE id = %s", (eid,)) == [("test",)]
    assert eid not in [e["id"] for e in api.get(f"/executions?workflow_id={wid}").json()]
    assert eid in [e["id"] for e in api.get(f"/executions?workflow_id={wid}&include_test=true").json()]
    assert eid not in [e["id"] for e in api.get(f"/projects/proj_default/runs?workflow_id={wid}").json()]
    assert eid in [e["id"] for e in api.get(f"/projects/proj_default/runs?workflow_id={wid}&include_test=true").json()]


def test_old_test_executions_are_purged_on_the_next_test_run(api, test_db):
    t = case("purge", 900, [])
    wid = make(api, refund([t], DEFAULTS))
    first = result(run(api, wid), t["id"])["execution_id"]
    test_db.query("UPDATE executions SET start_time = now() - interval '8 days' WHERE id = %s", (first,))
    second = result(run(api, wid), t["id"])["execution_id"]
    ids = [r[0] for r in test_db.query("SELECT id FROM executions WHERE workflow_id = %s", (wid,))]
    assert first not in ids and second in ids


# ── lock rules ───────────────────────────────────────────────────────────────


def approve(api, wid: str, tid_: str, by: str = "sam"):
    return api.post(f"/workflows/{wid}/tests/{tid_}/approve", {"approved_by": by})


def test_approve_locks_a_test_and_unlock_frees_it(api):
    t = case("lockable", 900, [])
    wid = make(api, refund([t], DEFAULTS))
    r = approve(api, wid, t["id"])
    assert r.status_code == 200, r.text
    saved = r.json()["definition"]["tests"][0]
    assert saved["status"] == "approved" and saved["locked"] is True and saved["approved_by"] == "sam" and saved["approved_at"]
    r = api.post(f"/workflows/{wid}/tests/{t['id']}/unlock", {"by": "sam"})
    assert r.status_code == 200
    saved = r.json()["definition"]["tests"][0]
    assert saved["status"] == "draft" and saved["locked"] is False and "approved_by" not in saved and "approved_at" not in saved
    assert approve(api, wid, "t_ffffffff").status_code == 404
    assert api.post(f"/workflows/{wid}/tests/t_ffffffff/unlock", {"by": "x"}).status_code == 404


def test_saving_changes_to_a_locked_test_is_refused_with_409(api):
    t = case("locked one", 900, [])
    other = case("other", 100, [])
    wf = refund([t, other], DEFAULTS)
    wid = make(api, wf)
    approve(api, wid, t["id"])
    stored = api.get(f"/workflows/{wid}").json()["definition"]

    edited = json.loads(json.dumps(stored["tests"]))
    edited[0]["name"] = "renamed test"
    r = api.put(f"/workflows/{wid}", {"tests": edited})
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == {"message": "Locked tests can't be changed", "test_ids": [t["id"]]}

    # deleting it is refused too
    r = api.put(f"/workflows/{wid}", {"tests": [x for x in stored["tests"] if x["id"] != t["id"]]})
    assert r.status_code == 409 and r.json()["detail"]["test_ids"] == [t["id"]]

    # a draft can be edited, and a client may not approve by itself
    ok = json.loads(json.dumps(stored["tests"]))
    ok[1]["name"] = "other, edited"
    assert api.put(f"/workflows/{wid}", {"tests": ok}).status_code == 200
    sneaky = json.loads(json.dumps(ok))
    sneaky[1]["status"], sneaky[1]["locked"] = "approved", True
    r = api.put(f"/workflows/{wid}", {"tests": sneaky})
    assert r.status_code == 409 and r.json()["detail"]["test_ids"] == [other["id"]]

    # saving the unchanged locked test (and a plain workflow save) is fine
    assert api.put(f"/workflows/{wid}", {"tests": ok}).status_code == 200
    assert api.put(f"/workflows/{wid}", {"description": "still fine"}).status_code == 200


def test_a_rename_alone_never_trips_the_lock_and_updates_the_stored_tests(api):
    t = case("renames", 900, [{"node": "Risk API", "path": "statusCode", "op": "eq", "value": 200}],
             {"Risk API": RISK_MOCK}, covers=["Risk API"])
    wf = refund([t], {"mocks": {"Risk API": RISK_MOCK}})
    wid = make(api, wf)
    approve(api, wid, t["id"])
    stored = api.get(f"/workflows/{wid}").json()["definition"]
    # the studio renamed the node and rewrote its own copy of the tests; it sends node_renames
    nodes = json.loads(json.dumps(stored["nodes"]))
    conns = json.loads(json.dumps(stored["connections"]))
    next(n for n in nodes if n["name"] == "Risk API")["name"] = "Risk score"
    for c in conns:
        for k in ("source_node", "target_node"):
            if c[k] == "Risk API":
                c[k] = "Risk score"
    moved = json.loads(json.dumps(stored["tests"]))
    moved[0]["expect"][0]["node"] = "Risk score"
    moved[0]["mocks"] = {"Risk score": RISK_MOCK}
    moved[0]["covers"] = ["Risk score"]
    r = api.put(f"/workflows/{wid}", {"nodes": nodes, "connections": conns, "tests": moved,
                                     "test_defaults": {"mocks": {"Risk score": RISK_MOCK}},
                                     "node_renames": {"Risk API": "Risk score"}})
    assert r.status_code == 200, r.text
    d = r.json()["definition"]
    assert d["tests"][0]["locked"] is True and d["tests"][0]["expect"][0]["node"] == "Risk score"
    # without node_renames the same save looks like an edit of a locked test
    nodes2 = json.loads(json.dumps(nodes))
    next(n for n in nodes2 if n["name"] == "Risk score")["name"] = "Risk again"
    again = json.loads(json.dumps(d["tests"]))
    again[0]["expect"][0]["node"] = "Risk again"
    assert api.put(f"/workflows/{wid}", {"nodes": nodes2, "tests": again}).status_code == 409

    # a rename sent without tests also rewrites the stored (locked) tests
    nodes3 = json.loads(json.dumps(nodes))
    next(n for n in nodes3 if n["name"] == "Risk score")["name"] = "Scorer"
    conns3 = json.loads(json.dumps(conns))
    for c in conns3:
        for k in ("source_node", "target_node"):
            if c[k] == "Risk score":
                c[k] = "Scorer"
    r = api.put(f"/workflows/{wid}", {"nodes": nodes3, "connections": conns3, "node_renames": {"Risk score": "Scorer"}})
    assert r.status_code == 200, r.text
    d = r.json()["definition"]
    assert d["tests"][0]["expect"][0]["node"] == "Scorer" and d["test_defaults"]["mocks"] == {"Scorer": RISK_MOCK}


def test_tests_and_defaults_survive_publish_and_version_restore(api):
    api.post("/projects/proj_default/tables", {"name": "refunds", "columns": COLUMNS})
    t = case("survivor", 900, [{"node": "Risk API", "path": "statusCode", "op": "eq", "value": 200}])
    wf = refund([t], DEFAULTS)
    wid = make(api, wf)
    approve(api, wid, t["id"])
    pub = api.post(f"/workflows/{wid}/publish", {"message": "with tests"})
    assert pub.status_code == 200, pub.text
    versions = api.get(f"/workflows/{wid}/versions").json()
    snap = api.get(f"/workflows/{wid}/versions/{versions[0]['id']}").json()["definition"]
    assert snap["tests"][0]["id"] == t["id"] and snap["tests"][0]["locked"] is True
    assert snap["test_defaults"]["mocks"]["Risk API"] == RISK_MOCK

    # wipe the draft's tests (unlock first: a locked test can't be deleted), then restore the version
    api.post(f"/workflows/{wid}/tests/{t['id']}/unlock", {"by": "sam"})
    assert api.put(f"/workflows/{wid}", {"tests": [], "test_defaults": {}}).json()["definition"]["tests"] == []
    restored = api.post(f"/workflows/{wid}/versions/{versions[0]['id']}/restore", {}).json()["definition"]
    assert restored["tests"][0]["id"] == t["id"] and restored["tests"][0]["status"] == "approved"
    assert restored["test_defaults"]["mocks"]["Risk API"] == RISK_MOCK
