"""Stage 3A demo story over HTTP (HANDOFF-STAGE3 section 4-6), with no real outside calls and no fake API server:
Refund triage gets a Risk API from an OpenAPI spec, business cases, suggested boundaries and errors, a score, locked
tests, a broken If condition caught at the right node, a blocked publish, a fix, and a replay test."""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path

import pytest

from .conftest import uniq

pytestmark = pytest.mark.integration

FIX = Path(__file__).resolve().parents[1] / "fixtures"
FIXTURE = FIX / "workflows" / "refund_triage.json"
RISK_YAML = (FIX / "openapi" / "risk_api.yaml").read_text()
COLUMNS = [{"name": "order_id", "type": "string"}, {"name": "amount", "type": "number"},
           {"name": "reason", "type": "string"}, {"name": "status", "type": "string"}]
TABLE_MOCK = {"items": [{"id": 1, "order_id": "o-1", "status": "logged"}]}
BAR = re.compile(r"^(\d+)/(\d+) passing(?: · \d+ gaps?)? · (\d+)/(\d+) planted bugs caught$")


def tid() -> str:
    return "t_" + uuid.uuid4().hex[:8]


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
        {"node": "Log approved", "subject": "request", "path": "params.data.status", "op": "eq", "value": "auto_approved"},
        {"node": "Log approved", "subject": "request", "path": "params.data.order_id", "op": "eq", "value": "o-1"},
        {"node": "Log approved", "subject": "request", "path": "params.data.reason", "op": "eq", "value": "late"},
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
        {"node": "Risk API", "subject": "request", "path": "url", "op": "matches", "value": "/risk-check$"},
        {"node": "Risk API", "subject": "request", "path": "method", "op": "eq", "value": "POST"},
        {"node": "Risk API", "path": "statusCode", "op": "eq", "value": 200},
        {"node": "Risk API", "path": "body.decision", "op": "one_of", "value": ["approve", "review", "deny"]},
        {"node": "Log escalated", "op": "status", "value": "success"},
        {"node": "Log escalated", "subject": "request", "path": "params.data.status", "op": "eq", "value": "needs_review"},
        {"node": "Log escalated", "subject": "request", "path": "params.data.order_id", "op": "eq", "value": "o-1"},
        {"node": "Log escalated", "subject": "request", "path": "params.data.reason", "op": "eq", "value": "late"},
        {"node": "Log escalated", "subject": "request", "path": "params.data.amount", "op": "eq", "value": amount},
        {"node": "Log approved", "op": "status", "value": "not_run"},
    ]


def node_of(defn: dict, name: str) -> dict:
    return next(n for n in defn["nodes"] if n["name"] == name)


def save(api, wid: str, defn: dict, **extra):
    body = {k: defn[k] for k in ("nodes", "connections", "tests", "test_defaults") if k in defn}
    return api.put(f"/workflows/{wid}", {**body, **extra})


def test_demo_story_over_http_without_any_real_call(api):
    # a. create Refund triage in its own project (with the refunds table)
    pid = api.post("/projects", {"name": uniq("s3a-journey")}).json()["id"]
    assert api.post(f"/projects/{pid}/tables", {"name": "refunds", "columns": COLUMNS}).status_code == 201
    wf = json.loads(FIXTURE.read_text().replace("__REFUND_API_URL__", "http://unused.invalid"))
    wf["name"] = uniq("journey-refund")
    wf["settings"] = {}
    for n in wf["nodes"]:
        if n["type"] == "Webhook":
            n["parameters"]["path"] = uniq("refund")
    r = api.post(f"/projects/{pid}/workflows", wf)
    assert r.status_code == 201, r.text
    wid = r.json()["id"]

    # b. register the Risk API from its spec, point the node at it, write the default mocks
    r = api.post("/api-specs", {"project_id": pid, "spec_text": RISK_YAML})
    assert r.status_code == 200, r.text
    spec = r.json()
    assert any(o["operation_id"] == "riskCheck" for o in spec["operations"])
    opts = api.get(f"/api-specs/{spec['id']}/operations/riskCheck/mocks").json()
    ok_mock = next(o["mock"] for o in opts if o["category"] == "ext_success")
    assert {o["category"] for o in opts} >= {"ext_success", "ext_error", "ext_timeout"}

    defn = api.get(f"/workflows/{wid}").json()["definition"]
    risk = node_of(defn, "Risk API")
    risk["parameters"] = {"api_ref": {"spec_id": spec["id"], "operation_id": "riskCheck"}, "path_params": {},
                          "body": risk["parameters"]["body"], "responseType": "json"}
    defaults = {"mocks": {"Risk API": ok_mock, "Log escalated": TABLE_MOCK, "Log approved": TABLE_MOCK}}
    r = api.put(f"/workflows/{wid}", {"nodes": defn["nodes"], "connections": defn["connections"], "test_defaults": defaults})
    assert r.status_code == 200, r.text
    assert api.post(f"/workflows/{wid}/check", {}).json()["findings"] is not None
    errs = [f for f in api.post(f"/workflows/{wid}/check", {}).json()["findings"] if f["severity"] == "error"]
    assert errs == [], errs

    # c. business cases by hand
    risk_500 = {"http": {"status": 500, "body": {"error": "risk service down"}}}
    hand = [
        case("small refund auto-approved", 100, small_expect(100), id="t_a0000001"),
        case("exactly 500 stays auto-approved", 500, small_expect(500), "boundary", id="t_a0000002", field="amount"),
        case("big refund escalated", 900, big_expect(900), id="t_a0000003"),
        case("risk API 500 is handled", 900, [
            {"node": "Risk API", "path": "statusCode", "op": "eq", "value": 500},
            {"node": "Risk API", "op": "status", "value": "success"},
            {"node": "Risk API", "subject": "request", "op": "count", "value": 1},
        ], "ext_error", id="t_a0000004", mocks={"Risk API": risk_500}),
    ]
    r = api.put(f"/workflows/{wid}", {"tests": hand})
    assert r.status_code == 200, r.text
    first = api.post(f"/workflows/{wid}/tests/run", {}).json()
    assert [x["status"] for x in first["results"]] == ["passed"] * 4, [x["failures"] for x in first["results"]]

    # d. suggestions: accept the 500/501 boundaries and the Risk API error + timeout drafts
    drafts = api.post(f"/workflows/{wid}/tests/suggest", {}).json()
    assert drafts and all(d["source"] == "suggested" and d["status"] == "draft" for d in drafts)
    boundary = [d for d in drafts if d["category"] == "boundary" and d.get("field") == "amount"]
    amounts = {d["input"]["items"][0]["body"]["amount"] for d in boundary}
    assert {500, 501} <= amounts
    chosen = [d for d in boundary if d["input"]["items"][0]["body"]["amount"] in (500, 501)]
    chosen += [d for d in drafts if d["covers"] == ["Risk API"] and d["category"] in ("ext_error", "ext_timeout")]
    assert any(d["category"] == "ext_timeout" for d in chosen) and sum(d["category"] == "ext_error" for d in chosen) >= 3
    assert not {d["id"] for d in chosen} & {h["id"] for h in hand}
    tests = hand + chosen
    assert api.put(f"/workflows/{wid}", {"tests": tests}).status_code == 200
    saved = api.get(f"/workflows/{wid}").json()["definition"]
    assert [t["id"] for t in saved["tests"]] == [t["id"] for t in tests]

    # e. all green, the grid shows covered cells
    report = api.post(f"/workflows/{wid}/tests/run", {}).json()
    bad = [(x["name"], x["status"], x["failures"]) for x in report["results"] if x["status"] != "passed"]
    assert not bad, bad
    assert report["summary"]["total"] == len(tests) and report["summary"]["failed"] == report["summary"]["errored"] == 0
    grid = {row["node"]: row["categories"] for row in report["grid"]["rows"]}
    assert grid["Amount over 500"]["boundary"]["status"] == "pass"
    assert grid["Risk API"]["normal"]["status"] == "pass"
    assert grid["Risk API"]["ext_error"]["status"] == "pass" and grid["Risk API"]["ext_timeout"]["status"] == "pass"
    assert grid["Log approved"]["normal"]["status"] == "pass"
    assert grid["Risk API"]["boundary"]["status"] == "na"
    assert "Webhook" not in grid
    assert report["badges"]["Amount over 500"] == "pass" and report["badges"]["Risk API"] == "pass"

    # f. scorecard
    card = api.post(f"/workflows/{wid}/score", {"mutations": True}).json()
    m = BAR.match(card["bar"])
    assert m, card["bar"]
    assert m.group(1) == m.group(2) == str(len(tests))
    assert card["mutation"]["total"] >= 5
    assert card["mutation"]["caught"] / card["mutation"]["total"] >= 0.8, card["mutation"]["survived"]
    assert int(m.group(3)) == card["mutation"]["caught"] and int(m.group(4)) == card["mutation"]["total"]
    assert card["checker"]["errors"] == 0 and card["coverage"]["untaken"] == []

    # g. approve two tests; a PUT that edits a locked test is a 409
    for t in ("t_a0000002", "t_a0000003"):
        r = api.post(f"/workflows/{wid}/tests/{t}/approve", {"approved_by": "Riley"})
        assert r.status_code == 200, r.text
    d = api.get(f"/workflows/{wid}").json()["definition"]
    locked = {t["id"]: t for t in d["tests"] if t["locked"]}
    assert set(locked) == {"t_a0000002", "t_a0000003"} and all(t["status"] == "approved" and t["approved_by"] == "Riley" for t in locked.values())
    edited = json.loads(json.dumps(d["tests"]))
    next(t for t in edited if t["id"] == "t_a0000002")["name"] = "weakened"
    r = api.put(f"/workflows/{wid}", {"tests": edited})
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == {"message": "Locked tests can't be changed", "test_ids": ["t_a0000002"]}
    edited = [t for t in d["tests"] if t["id"] != "t_a0000003"]
    assert api.put(f"/workflows/{wid}", {"tests": edited}).status_code == 409   # deleting is also refused
    still = api.get(f"/workflows/{wid}").json()["definition"]["tests"]
    assert next(t for t in still if t["id"] == "t_a0000002")["name"] == "exactly 500 stays auto-approved"

    # h. break the If condition: > 500 becomes >= 500
    d = api.get(f"/workflows/{wid}").json()["definition"]
    node_of(d, "Amount over 500")["parameters"]["condition"] = "{{ $json.amount >= 500 }}"
    assert api.put(f"/workflows/{wid}", {"nodes": d["nodes"], "connections": d["connections"]}).status_code == 200
    report = api.post(f"/workflows/{wid}/tests/run", {}).json()
    failed = {x["test_id"]: x for x in report["results"] if x["status"] != "passed"}
    assert "t_a0000002" in failed, [x["name"] for x in report["results"]]
    f500 = failed["t_a0000002"]
    assert f500["status"] == "failed"
    assert any(f["node"] == "Amount over 500" and f["kind"] == "assertion" for f in f500["failures"]), f500["failures"]
    assert report["badges"]["Amount over 500"] == "fail"
    assert report["summary"]["approved_failed"] >= 1
    assert "t_a0000003" not in failed   # 900 is still over the line

    # i. publish is blocked and names the failing test; fix the condition; publish passes
    r = api.post(f"/workflows/{wid}/publish", {"message": "oops"})
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert detail["message"] == "Approved tests fail"
    assert [f["test_id"] for f in detail["failed_tests"]] == ["t_a0000002"]
    assert detail["failed_tests"][0]["name"] == "exactly 500 stays auto-approved" and detail["failed_tests"][0]["failures"]
    assert api.get(f"/workflows/{wid}").json()["active"] is False
    node_of(d, "Amount over 500")["parameters"]["condition"] = "{{ $json.amount > 500 }}"
    assert api.put(f"/workflows/{wid}", {"nodes": d["nodes"], "connections": d["connections"]}).status_code == 200
    ok = api.post(f"/workflows/{wid}/publish", {"message": "fixed"})
    assert ok.status_code == 200 and ok.json()["active"] is True, ok.text
    assert all(x["status"] == "passed" for x in api.post(f"/workflows/{wid}/tests/run", {}).json()["results"])

    # j. save-as-test from an execution made with the effect nodes pinned (no real calls)
    d = api.get(f"/workflows/{wid}").json()["definition"]
    pins = {"Risk API": {"statusCode": 200, "headers": {}, "body": {"decision": "review", "score": 70}},
            "Log escalated": {"id": 7, "order_id": "o-9", "status": "needs_review"}}
    for name, pinned in pins.items():
        node_of(d, name)["pinned_data"] = [{"json": pinned}]
    pinned_nodes = json.loads(json.dumps(d["nodes"]))
    assert api.put(f"/workflows/{wid}", {"nodes": d["nodes"], "connections": d["connections"]}).status_code == 200
    run = api.post(f"/workflows/{wid}/run", {"input_data": {"order_id": "o-9", "amount": 900, "reason": "late"}})
    assert run.status_code == 200 and run.json()["status"] == "success", run.text
    eid = run.json()["execution_id"]
    for n in pinned_nodes:
        n.pop("pinned_data", None)
    assert api.put(f"/workflows/{wid}", {"nodes": pinned_nodes, "connections": d["connections"]}).status_code == 200
    assert all("pinned_data" not in n or not n["pinned_data"] for n in api.get(f"/workflows/{wid}").json()["definition"]["nodes"])
    r = api.post(f"/workflows/{wid}/tests/from-execution", {"execution_id": eid, "name": "Replay of o-9"})
    assert r.status_code == 200, r.text
    replay = r.json()
    assert replay["source"] == "replay" and replay["status"] == "draft" and replay["input"]["items"][0]["body"]["amount"] == 900
    assert {"Risk API", "Log escalated"} <= set(replay["mocks"]) and "Log approved" not in replay["mocks"]
    tests2 = api.get(f"/workflows/{wid}").json()["definition"]["tests"]
    r = api.put(f"/workflows/{wid}", {"tests": tests2 + [replay]})
    assert r.status_code == 200, r.text
    report = api.post(f"/workflows/{wid}/tests/run", {"ids": [replay["id"]]}).json()
    assert [x["status"] for x in report["results"]] == ["passed"], report["results"][0]["failures"]

    # test runs left no trace in the normal runs lists
    runs = api.get(f"/executions?workflow_id={wid}").json()
    assert [e["id"] for e in runs] == [eid]
    with_test = api.get(f"/executions?workflow_id={wid}&include_test=true").json()
    assert len(with_test) > len(runs)
