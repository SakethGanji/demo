"""Stage 3B drift alerts over HTTP: a real (non-test) run against the fake risk API in v1 is clean, in v2 it opens an
alert with a readable summary, tests that mock the node turn stale (the stored tests never change), resolve works,
and the next v2 run reopens and bumps the same alert."""

from __future__ import annotations

import json

import pytest

from .drift_smoke_support import FakeApi, big_case, make_refund_workflow, real_run

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module", autouse=True)
def _drop_test_mode_runs(test_db):
    """Test-mode runs (POST /tests/run) are kept by age, not by the run-retention count, and the engine/DB are
    shared for the whole session: delete the ones this module made so the global executions count stays honest
    for test_review_g4_runs (``count(*) from executions <= 100``)."""
    yield
    test_db.query("delete from executions where mode = 'test'")


@pytest.fixture(scope="module")
def fake():
    f = FakeApi()
    try:
        yield f
    finally:
        f.stop()


def test_drift_journey_v1_clean_v2_opens_stale_resolve_reopen(api, fake):
    fake.shape("v1")
    locked = big_case("big refund escalated (locked)", id="t_d0000001")
    own = big_case("risk API 500", id="t_d0000002", category="ext_error", mocks={"Risk API": {"http": {"status": 500, "body": {"error": "down"}}}},
                   expect=[{"node": "Risk API", "path": "statusCode", "op": "eq", "value": 500}])
    pid, wid = make_refund_workflow(api, fake.url, tests=[locked, own])
    assert api.post(f"/workflows/{wid}/tests/t_d0000001/approve", {"approved_by": "Riley"}).status_code == 200
    stored_before = json.dumps(api.get(f"/workflows/{wid}").json()["definition"]["tests"], sort_keys=True)

    # v1: the reply is what the default mock describes -> no drift
    assert real_run(api, wid)["status"] == "success"
    assert api.get(f"/workflows/{wid}/drift").json() == {"alerts": [], "stale_test_ids": []}
    assert api.get(f"/projects/{pid}/drift").json() == {"alerts": []}

    # v2: decision moved under result, score appeared
    fake.shape("v2")
    assert real_run(api, wid)["status"] == "success"
    body = api.get(f"/workflows/{wid}/drift").json()
    assert len(body["alerts"]) == 1
    a = body["alerts"][0]
    assert a["node_name"] == "Risk API" and a["basis"] == "mock" and a["status"] == "open" and a["count"] == 1
    assert a["title"] == "Risk API reply changed" and a["id"].startswith("drift_") and a["status_code"] == 200
    assert "body.decision missing" in a["summary"] and "body.received missing" in a["summary"]
    assert "unexpected body.result" in a["summary"] and "unexpected body.score" in a["summary"]
    kinds = {(d["path"], d["kind"]) for d in a["diffs"]}
    assert {("body.decision", "missing"), ("body.received", "missing"), ("body.result", "extra"), ("body.score", "extra")} <= kinds
    assert a["last_execution_id"]
    assert set(body["stale_test_ids"]) == {"t_d0000001", "t_d0000002"}
    assert [x["id"] for x in api.get(f"/projects/{pid}/drift").json()["alerts"]] == [a["id"]]

    # stale ids show in the test report and the scorecard; the locked test is byte-for-byte unchanged
    report = api.post(f"/workflows/{wid}/tests/run", {}).json()
    assert set(report["stale_test_ids"]) == {"t_d0000001", "t_d0000002"}
    card = api.post(f"/workflows/{wid}/score", {"mutations": False}).json()
    assert set(card["stale_test_ids"]) == {"t_d0000001", "t_d0000002"}
    assert json.dumps(api.get(f"/workflows/{wid}").json()["definition"]["tests"], sort_keys=True) == stored_before

    # resolve -> gone from the open list, still in ?status=all, tests no longer stale
    r = api.post(f"/workflows/{wid}/drift/{a['id']}/resolve", {"by": "Riley"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "resolved" and r.json()["resolved_by"] == "Riley" and r.json()["resolved_at"]
    assert api.get(f"/workflows/{wid}/drift").json() == {"alerts": [], "stale_test_ids": []}
    allrows = api.get(f"/workflows/{wid}/drift?status=all").json()["alerts"]
    assert [x["id"] for x in allrows] == [a["id"]]
    assert api.post(f"/workflows/{wid}/tests/run", {}).json()["stale_test_ids"] == []

    # another v2 run reopens the same alert and bumps it
    assert real_run(api, wid)["status"] == "success"
    again = api.get(f"/workflows/{wid}/drift").json()
    assert [x["id"] for x in again["alerts"]] == [a["id"]]
    assert again["alerts"][0]["count"] == 2 and again["alerts"][0]["status"] == "open"
    assert again["alerts"][0]["resolved_by"] is None
    assert again["alerts"][0]["last_seen"] >= a["last_seen"]
    assert set(again["stale_test_ids"]) == {"t_d0000001", "t_d0000002"}

    # back to v1: nothing new, the open alert stays until someone resolves it
    fake.shape("v1")
    assert real_run(api, wid)["status"] == "success"
    assert api.get(f"/workflows/{wid}/drift").json()["alerts"][0]["count"] == 2


def test_drift_errors_are_404(api, fake):
    _, wid = make_refund_workflow(api, fake.url)
    assert api.get("/workflows/wf_missing/drift").status_code == 404
    assert api.post(f"/workflows/{wid}/drift/drift_nope/resolve", {"by": "x"}).status_code == 404
    assert api.post(f"/workflows/{wid}/drift/drift_nope/resolve", {}).status_code == 422
    assert api.get("/projects/proj_missing_zz/drift").status_code == 404


def test_drift_uses_a_declared_contract_before_the_mock(api, fake):
    fake.shape("v1")
    pid, wid = make_refund_workflow(api, fake.url)
    d = api.get(f"/workflows/{wid}").json()["definition"]
    risk = next(n for n in d["nodes"] if n["name"] == "Risk API")
    risk["contract"] = {"source": "declared", "approved_by": "Riley", "outputs": {"main": {
        "type": "object", "required": ["statusCode", "body"], "properties": {
            "statusCode": {"type": "integer"},
            "body": {"type": "object", "required": ["ok", "score"], "properties": {"ok": {"type": "boolean"}, "score": {"type": "number"}}}}}}}
    assert api.put(f"/workflows/{wid}", {"nodes": d["nodes"], "connections": d["connections"]}).status_code == 200
    fake.shape("v2")  # score is "high" (a string): wrong type under the contract; no "extra" diffs outside the mock basis
    real_run(api, wid)
    a = api.get(f"/workflows/{wid}/drift").json()["alerts"]
    assert len(a) == 1 and a[0]["basis"] == "contract"
    assert [(x["path"], x["kind"]) for x in a[0]["diffs"]] == [("body.score", "type")]
    assert a[0]["summary"] == "body.score: expected number, got string"
    fake.shape("v1")


def test_a_failing_drift_check_never_changes_the_run(api, fake):
    """A workflow whose Risk API reply is a 500 still succeeds, and a status change against the mock is just an alert."""
    fake.shape("v1")
    _, wid = make_refund_workflow(api, fake.url.replace("/ok", "") + "/nowhere")  # url ends in /ok in the fixture; point it elsewhere
    d = api.get(f"/workflows/{wid}").json()["definition"]
    next(n for n in d["nodes"] if n["name"] == "Risk API")["parameters"]["url"] = f"{fake.url}/error"
    assert api.put(f"/workflows/{wid}", {"nodes": d["nodes"], "connections": d["connections"]}).status_code == 200
    run = real_run(api, wid)
    assert run["status"] == "success"
    alerts = api.get(f"/workflows/{wid}/drift").json()["alerts"]
    assert len(alerts) == 1 and alerts[0]["diffs"][0]["kind"] == "status" and alerts[0]["status_code"] == 500
    assert "status 500" in alerts[0]["summary"]
