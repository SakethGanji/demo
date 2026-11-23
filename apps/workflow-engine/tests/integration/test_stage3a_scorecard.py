"""Stage 3A scorecard, publish/release block, toolkit and Wave 1 fixes over HTTP on real Postgres (Refund triage)."""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

import pytest

from .conftest import uniq

pytestmark = pytest.mark.integration

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "workflows" / "refund_triage.json"
COLUMNS = [{"name": "order_id", "type": "string"}, {"name": "amount", "type": "number"},
           {"name": "reason", "type": "string"}, {"name": "status", "type": "string"}]
RISK_URL = "http://risk.example.test/v1/score"
RISK_MOCK = {"http": {"status": 200, "body": {"decision": "review", "score": 0.7}}}
TABLE_MOCK = {"items": [{"id": 1, "order_id": "o-1", "status": "logged"}]}
DEFAULTS = {"mocks": {"Risk API": RISK_MOCK, "Log escalated": TABLE_MOCK, "Log approved": TABLE_MOCK}}


def tid() -> str:
    return "t_" + uuid.uuid4().hex[:8]


def refund(tests: list | None = None, defaults: dict | None = None, project_id: str | None = None) -> dict:
    wf = json.loads(FIXTURE.read_text().replace("__REFUND_API_URL__", RISK_URL))
    wf["name"] = uniq("s3a-score")
    wf["settings"] = {}
    for n in wf["nodes"]:
        if n["type"] == "Webhook":
            n["parameters"]["path"] = uniq("refund")
    wf["tests"] = tests or []
    wf["test_defaults"] = DEFAULTS if defaults is None else defaults
    if project_id:
        wf["project_id"] = project_id
    return wf


def make(api, body: dict) -> str:
    r = api.post("/workflows", body)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def case(name: str, amount: int, expect: list, category: str = "normal", **kw) -> dict:
    return {
        "id": kw.pop("id", None) or tid(), "name": name, "target": {"kind": "workflow"},
        "input": {"items": [{"body": {"order_id": "o-1", "amount": amount, "reason": "late"}}]},
        "mocks": kw.pop("mocks", {}), "expect": expect, "category": category, **kw,
    }


def small_expect(amount: int) -> list:
    return [
        {"node": "Normalize", "path": "order_id", "op": "eq", "value": "o-1"},
        {"node": "Normalize", "path": "amount", "op": "eq", "value": amount},
        {"node": "Normalize", "path": "reason", "op": "eq", "value": "late"},
        {"node": "Amount over 500", "port": "true", "op": "count", "value": 0},
        {"node": "Amount over 500", "port": "false", "op": "count", "value": 1},
        {"node": "Risk API", "op": "status", "value": "not_run"},
        {"node": "Log escalated", "op": "status", "value": "not_run"},
        {"node": "Log approved", "op": "status", "value": "success"},
        {"node": "Log approved", "subject": "request", "path": "params.data.order_id", "op": "eq", "value": "o-1"},
        {"node": "Log approved", "subject": "request", "path": "params.data.amount", "op": "eq", "value": amount},
    ]


def big_expect(amount: int) -> list:
    return [
        {"node": "Normalize", "path": "order_id", "op": "eq", "value": "o-1"},
        {"node": "Normalize", "path": "amount", "op": "eq", "value": amount},
        {"node": "Normalize", "path": "reason", "op": "eq", "value": "late"},
        {"node": "Amount over 500", "port": "true", "op": "count", "value": 1},
        {"node": "Amount over 500", "port": "false", "op": "count", "value": 0},
        {"node": "Risk API", "subject": "request", "path": "body.order_id", "op": "eq", "value": "o-1"},
        {"node": "Risk API", "subject": "request", "path": "body.amount", "op": "eq", "value": amount},
        {"node": "Risk API", "path": "body.decision", "op": "eq", "value": "review"},
        {"node": "Log escalated", "op": "status", "value": "success"},
        {"node": "Log escalated", "subject": "request", "path": "params.data.order_id", "op": "eq", "value": "o-1"},
        {"node": "Log escalated", "subject": "request", "path": "params.data.amount", "op": "eq", "value": amount},
        {"node": "Log approved", "op": "status", "value": "not_run"},
    ]


def good_tests() -> list:
    return [
        case("small refund", 100, small_expect(100), id="t_aaaa0001"),
        case("exactly 500 is not escalated", 500, small_expect(500), "boundary", id="t_aaaa0002"),
        case("501 is escalated", 501, big_expect(501), "boundary", id="t_aaaa0003"),
        case("big refund", 900, big_expect(900), id="t_aaaa0004"),
    ]


def weak_tests() -> list:
    return [case("it runs", 900, [{"node": "Risk API", "op": "status", "value": "success"}], id="t_bbbb0001")]


def score(api, wid: str, **body):
    r = api.post(f"/workflows/{wid}/score", body)
    assert r.status_code == 200, r.text
    return r.json()


def approve(api, wid: str, test_id: str):
    r = api.post(f"/workflows/{wid}/tests/{test_id}/approve", {"approved_by": "sam"})
    assert r.status_code == 200, r.text


def broken_if(api, wid: str, condition: str) -> None:
    """Set the If condition on the draft, leaving the tests alone."""
    d = api.get(f"/workflows/{wid}").json()["definition"]
    for n in d["nodes"]:
        if n["name"] == "Amount over 500":
            n["parameters"]["condition"] = condition
    r = api.put(f"/workflows/{wid}", {"nodes": d["nodes"], "connections": d["connections"]})
    assert r.status_code == 200, r.text


@pytest.fixture(scope="module", autouse=True)
def refunds_table(api):
    api.post("/projects/proj_default/tables", {"name": "refunds", "columns": COLUMNS})


# ── scorecard ────────────────────────────────────────────────────────────────


def test_good_test_set_catches_planted_bugs_and_scores_fast(api):
    wid = make(api, refund(good_tests()))
    t0 = time.perf_counter()
    card = score(api, wid, mutations=True, repeat=0)
    elapsed = time.perf_counter() - t0
    print(f"\nscore with mutations: {elapsed:.2f}s, {card['bar']}")
    assert card["tests"]["total"] == 4 and card["tests"]["passed"] == 4, card["tests"]
    assert card["checker"]["errors"] == 0
    m = card["mutation"]
    assert m["total"] >= 8 and m["caught"] / m["total"] >= 0.8, m["survived"]
    assert not any(s["operator"] == "flip_compare" and s["node"] == "Amount over 500" for s in m["survived"])
    assert not any(s["operator"] == "swap_branches" for s in m["survived"])
    assert card["bar"].startswith("4/4 passing · ") and card["bar"].endswith(f"{m['caught']}/{m['total']} planted bugs caught")
    assert card["repeatability"] is None
    assert elapsed < 20
    # the scorecard never writes: no test executions were saved
    saved = api.get(f"/executions?workflow_id={wid}&include_test=true")
    assert saved.status_code == 200 and not (saved.json() if isinstance(saved.json(), list) else saved.json().get("items", saved.json().get("executions", [])))


def test_a_weak_test_set_catches_fewer_planted_bugs(api):
    good = score(api, make(api, refund(good_tests())), mutations=True)
    weak = score(api, make(api, refund(weak_tests())), mutations=True)
    assert weak["mutation"]["total"] == good["mutation"]["total"]
    assert weak["mutation"]["caught"] < good["mutation"]["caught"]
    assert any(s["operator"] == "flip_compare" and s["node"] == "Amount over 500" for s in weak["mutation"]["survived"])
    assert {"id", "operator", "node", "description"} == set(weak["mutation"]["survived"][0])


def test_mutations_off_omits_the_planted_bug_part(api):
    card = score(api, make(api, refund(good_tests())), mutations=False)
    assert card["mutation"] is None and "planted" not in card["bar"]
    gaps = len(card["gaps"])
    assert card["bar"] == f"4/4 passing · {gaps} {'gap' if gaps == 1 else 'gaps'}"
    assert card["coverage"]["grid_gaps"] == gaps and all({"node", "category", "reason"} <= set(g) for g in card["gaps"])
    assert any(g["node"] == "Risk API" and g["category"] == "ext_timeout" for g in card["gaps"])


def test_repeat_20_is_identical(api):
    card = score(api, make(api, refund(good_tests())), mutations=False, repeat=20)
    assert card["repeatability"] == {"runs": 20, "identical": True, "differing_tests": []}
    r = api.post(f"/workflows/{make(api, refund(good_tests()))}/score", {"repeat": 21})
    assert r.status_code == 422


def test_repeat_flags_a_test_that_varies(api):
    nodes = [
        {"name": "Start", "type": "Start", "parameters": {}},
        {"name": "Roll", "type": "Code", "parameters": {"code": "import random\nreturn [{'json': {'n': random.random()}}]"}},
    ]
    t = {"id": "t_cccc0001", "name": "roll", "target": {"kind": "workflow"}, "input": {"items": []}, "mocks": {},
         "expect": [{"node": "Roll", "path": "n", "op": "type", "value": "number"}], "category": "normal"}
    r = api.post("/workflows", {"name": uniq("s3a-roll"), "settings": {}, "nodes": nodes,
                                "connections": [{"source_node": "Start", "target_node": "Roll", "source_output": "main", "target_input": "main"}],
                                "tests": [t]})
    assert r.status_code == 201, r.text
    card = score(api, r.json()["id"], mutations=False, repeat=5)
    assert card["tests"]["passed"] == 1, card
    assert card["repeatability"]["identical"] is False and card["repeatability"]["differing_tests"] == ["t_cccc0001"]


def test_branch_coverage_untaken_when_only_small_refunds_are_tested(api):
    small = [case("small", 100, small_expect(100))]
    card = score(api, make(api, refund(small)), mutations=False)
    cov = card["coverage"]
    assert cov["branches_total"] == 2 and cov["branches_taken"] == 1
    assert cov["untaken"] == [{"node": "Amount over 500", "port": "true"}]
    full = score(api, make(api, refund(good_tests())), mutations=False)["coverage"]
    assert full["branches_taken"] == 2 and full["untaken"] == []


def test_score_runs_an_unsaved_definition_and_never_saves_it(api):
    wid = make(api, refund(good_tests()))
    d = api.get(f"/workflows/{wid}").json()["definition"]
    for n in d["nodes"]:
        if n["name"] == "Amount over 500":
            n["parameters"]["condition"] = "{{ $json.amount > 100 }}"
    card = score(api, wid, definition=d, mutations=False)
    assert card["tests"]["failed"] >= 1
    assert score(api, wid, mutations=False)["tests"]["failed"] == 0


# ── publish and release block ────────────────────────────────────────────────


def test_publish_blocked_by_a_failing_approved_test_then_passes_when_fixed(api):
    tests = good_tests()
    wid = make(api, refund(tests))
    for t in tests:
        approve(api, wid, t["id"])
    broken_if(api, wid, "{{ $json.amount > 5000 }}")
    r = api.post(f"/workflows/{wid}/publish", {})
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert detail["message"] == "Approved tests fail"
    failed = {f["test_id"]: f for f in detail["failed_tests"]}
    assert set(failed) == {"t_aaaa0003", "t_aaaa0004"} and failed["t_aaaa0004"]["name"] == "big refund"
    assert failed["t_aaaa0004"]["failures"] and failed["t_aaaa0004"]["failures"][0]["kind"] in ("assertion", "node_error")
    broken_if(api, wid, "{{ $json.amount > 500 }}")
    ok = api.post(f"/workflows/{wid}/publish", {})
    assert ok.status_code == 200, ok.text


def test_draft_tests_never_block_publish(api):
    wid = make(api, refund(good_tests()))     # all drafts
    broken_if(api, wid, "{{ $json.amount > 5000 }}")
    r = api.post(f"/workflows/{wid}/publish", {})
    assert r.status_code == 200, r.text


def test_release_checks_fail_when_an_approved_test_fails(api):
    pr = api.post("/projects", {"name": uniq("s3a-rel")}).json()
    pid = pr["id"]
    api.post(f"/projects/{pid}/tables", {"name": "refunds", "columns": COLUMNS})
    tests = good_tests()
    body = refund(tests)
    r = api.post(f"/projects/{pid}/workflows", {k: body[k] for k in ("name", "nodes", "connections", "settings", "tests", "test_defaults")})
    assert r.status_code == 201, r.text
    wid = r.json()["id"]
    assert api.post(f"/projects/{pid}/routes", {"method": "GET", "path": "/refund", "handler": {"type": "workflow", "workflow_id": wid}}).status_code == 201
    for t in tests:
        approve(api, wid, t["id"])
    ok = api.post(f"/projects/{pid}/releases?dry_run=true", {"environment": "dev"})
    assert ok.status_code == 200, ok.text
    checks = [c for c in ok.json()["checks"] if c["id"] == "workflow_tests_pass"]
    assert len(checks) == 1 and checks[0]["status"] == "pass" and checks[0]["details"]["workflow_id"] == wid
    broken_if(api, wid, "{{ $json.amount > 5000 }}")
    bad = api.post(f"/projects/{pid}/releases?dry_run=true", {"environment": "dev"})
    assert bad.status_code == 200, bad.text
    chk = next(c for c in bad.json()["checks"] if c["id"] == "workflow_tests_pass")
    assert chk["status"] == "fail" and {f["test_id"] for f in chk["details"]["failed_tests"]} == {"t_aaaa0003", "t_aaaa0004"}
    real = api.post(f"/projects/{pid}/releases", {"environment": "dev"})
    assert real.status_code == 422 and real.json()["detail"]["code"] == "checks_failed"


# ── apply-ops ────────────────────────────────────────────────────────────────


def test_apply_ops_dry_run_does_not_save_then_real_run_does(api):
    wid = make(api, refund(good_tests()))
    ops = [
        {"op": "rename_node", "args": {"old": "Normalize", "new": "Clean"}},
        {"op": "set_parameter", "args": {"node": "Amount over 500", "path": "condition", "value": "{{ $json.amount > 400 }}"}},
        {"op": "add_node", "args": {"name": "Audit", "type": "Set", "parameters": {"mode": "manual", "fields": []}}},
        {"op": "connect", "args": {"source": "Log approved", "target": "Audit"}},
    ]
    dry = api.post(f"/workflows/{wid}/apply-ops", {"ops": ops, "dry_run": True})
    assert dry.status_code == 200, dry.text
    body = dry.json()
    assert {"definition", "check"} <= set(body) and "findings" in body["check"]
    assert "Clean" in [n["name"] for n in body["definition"]["nodes"]]
    assert body["definition"]["tests"][0]["expect"][0]["node"] == "Clean"
    stored = api.get(f"/workflows/{wid}").json()["definition"]
    assert "Normalize" in [n["name"] for n in stored["nodes"]] and "Audit" not in [n["name"] for n in stored["nodes"]]

    real = api.post(f"/workflows/{wid}/apply-ops", {"ops": ops})
    assert real.status_code == 200, real.text
    stored = api.get(f"/workflows/{wid}").json()["definition"]
    names = [n["name"] for n in stored["nodes"]]
    assert "Clean" in names and "Audit" in names and "Normalize" not in names
    assert stored["test_defaults"]["mocks"]["Risk API"] == RISK_MOCK
    cond = next(n for n in stored["nodes"] if n["name"] == "Amount over 500")["parameters"]["condition"]
    assert cond == "{{ $json.amount > 400 }}"
    log = next(n for n in stored["nodes"] if n["name"] == "Log escalated")["parameters"]["data"]["order_id"]
    assert log == '{{ $node["Clean"].json.order_id }}'
    assert all(t["expect"][0]["node"] == "Clean" for t in stored["tests"])


def test_apply_ops_error_is_structured_400_and_saves_nothing(api):
    wid = make(api, refund(good_tests()))
    ops = [{"op": "set_parameter", "args": {"node": "Amount over 500", "path": "condition", "value": "x"}},
           {"op": "remove_node", "args": {"name": "Ghost"}}]
    r = api.post(f"/workflows/{wid}/apply-ops", {"ops": ops})
    assert r.status_code == 400
    assert r.json()["detail"] == {"op_index": 1, "op": "remove_node", "message": "No node named 'Ghost'"}
    cond = next(n for n in api.get(f"/workflows/{wid}").json()["definition"]["nodes"] if n["name"] == "Amount over 500")["parameters"]["condition"]
    assert cond == "{{ $json.amount > 500 }}"


def test_apply_ops_refuses_to_change_a_locked_test(api):
    tests = good_tests()
    wid = make(api, refund(tests))
    approve(api, wid, tests[0]["id"])
    r = api.post(f"/workflows/{wid}/apply-ops", {"ops": [{"op": "update_test", "args": {"test_id": tests[0]["id"], "changes": {"name": "x"}}}]})
    assert r.status_code == 400 and r.json()["detail"]["op_index"] == 0 and "locked" in r.json()["detail"]["message"]
    r = api.post(f"/workflows/{wid}/apply-ops", {"ops": [{"op": "update_test", "args": {"test_id": tests[1]["id"], "changes": {"name": "renamed"}}}]})
    assert r.status_code == 200 and r.json()["definition"]["tests"][1]["name"] == "renamed"


# ── Wave 1 fixes ─────────────────────────────────────────────────────────────


def test_contract_approve_keeps_declared_inputs(api):
    wid = make(api, refund([]))
    d = api.get(f"/workflows/{wid}").json()["definition"]
    inputs = {"main": {"type": "object", "properties": {"amount": {"type": "number"}}, "required": ["amount"]}}
    for n in d["nodes"]:
        if n["name"] == "Normalize":
            n["contract"] = {"inputs": inputs, "outputs": {"main": {"type": "unknown"}}, "source": "declared", "approved_by": "sam"}
    assert api.put(f"/workflows/{wid}", {"nodes": d["nodes"], "connections": d["connections"]}).status_code == 200
    out = {"main": {"type": "object", "properties": {"order_id": {"type": "string"}}}}
    r = api.post(f"/workflows/{wid}/nodes/Normalize/contract/approve", {"outputs": out, "approved_by": "sam"})
    assert r.status_code == 200, r.text
    node = next(n for n in r.json()["definition"]["nodes"] if n["name"] == "Normalize")
    assert node["contract"]["inputs"] == inputs and node["contract"]["source"] == "observed"
    assert node["contract"]["outputs"]["main"]["properties"]["order_id"]["type"] == "string"


def test_project_stats_exclude_test_runs(api):
    pid = api.post("/projects", {"name": uniq("s3a-stats")}).json()["id"]
    api.post(f"/projects/{pid}/tables", {"name": "refunds", "columns": COLUMNS})
    body = refund(good_tests())
    r = api.post(f"/projects/{pid}/workflows", {k: body[k] for k in ("name", "nodes", "connections", "settings", "tests", "test_defaults")})
    wid = r.json()["id"]
    run = api.post(f"/workflows/{wid}/tests/run", {})
    assert run.status_code == 200 and run.json()["summary"]["total"] == 4
    summary = api.get(f"/projects/{pid}/summary").json()
    assert summary["health"]["runs_24h"] == 0 and summary["last_run"] is None


def test_publish_returns_the_per_workflow_version_number(api):
    wid = make(api, refund())
    first = api.post(f"/workflows/{wid}/publish", {}).json()
    second = api.post(f"/workflows/{wid}/publish", {}).json()
    assert first["version_number"] == 1 and second["version_number"] == 2, (first, second)
    assert second["version_id"] > first["version_id"]      # version_id stays the global row id
