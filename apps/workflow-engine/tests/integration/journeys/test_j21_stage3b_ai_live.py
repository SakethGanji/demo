"""Stage 3B API journey on Refund triage with the fake LLM (CONTRACTS-STAGE3B section 7), written from the contract:

AI drafts tests for grid gaps (scripted, matched on TEST_AI:DRAFT_TESTS) -> approve one via the 3A route -> fuzz finds the
planted bug (Enrich) -> save the finding's test -> real runs against the fake risk API: v1 is clean, v2 opens drift and
turns the tests stale -> smoke run -> release blocked by the smoke check -> resolve the drift -> fake API back to v1 ->
release passes.
"""

from __future__ import annotations

import json

import pytest

from ..conftest import FAKE_LLM_TOKEN, FAKE_SCRIPT
from ..drift_smoke_support import FakeApi, big_case, make_refund_workflow, real_run

pytestmark = pytest.mark.integration

TOK = {"X-Test-Token": FAKE_LLM_TOKEN}
BUGGY = (  # passes the webhook body through, but trusts every field
    "b = items[0]['json']['body']\n"
    "return [{'json': {'body': {'order_id': b['order_id'], 'amount': b['amount'] + 0, 'reason': b['reason'].strip()}}}]\n"
)
SEED_ID = "t_c0000001"
V1_ONLY = {"node": "Risk API", "path": "body.decision", "op": "exists"}


@pytest.fixture(scope="module")
def fake():
    f = FakeApi()
    try:
        yield f
    finally:
        f.stop()


@pytest.fixture
def clean(fake_api):
    yield
    fake_api.raw("PUT", "/api/_test/fake-llm/script", headers=TOK, json=json.loads(FAKE_SCRIPT.read_text()))


def put_script(fake_api, rules: list) -> None:
    pinned = json.loads(FAKE_SCRIPT.read_text())
    r = fake_api.raw("PUT", "/api/_test/fake-llm/script", headers=TOK, json={**pinned, "rules": rules + pinned["rules"]})
    assert r.status_code == 200, r.text
    fake_api.raw("DELETE", "/api/_test/fake-llm/calls", headers=TOK)


def stored(api, wid: str) -> dict:
    return api.get(f"/workflows/{wid}").json()["definition"]


def add_tests(api, wid: str, *new: dict) -> None:
    d = stored(api, wid)
    r = api.put(f"/workflows/{wid}", {"nodes": d["nodes"], "connections": d["connections"], "tests": d["tests"] + list(new)})
    assert r.status_code == 200, r.text


def checks(api, pid: str) -> dict[str, list]:
    r = api.post(f"/projects/{pid}/releases?dry_run=true", {"environment": "dev"})
    assert r.status_code == 200, r.text
    out: dict[str, list] = {}
    for c in r.json()["checks"]:
        out.setdefault(c["id"], []).append(c)
    return out


def test_stage3b_ai_fuzz_drift_smoke_release_journey(fake_api, fake, clean):
    api = fake_api
    fake.shape("v1")

    # a. Refund triage in its own project, with a bug planted in a new Enrich step and a locked-able seed test
    seed = big_case("big refund reaches the risk API", id=SEED_ID, expect=[V1_ONLY, {"node": "Log escalated", "op": "status", "value": "success"}])
    pid, wid = make_refund_workflow(api, fake.url, tests=[seed])
    d = stored(api, wid)
    d["nodes"].append({"name": "Enrich", "type": "Code", "parameters": {"code": BUGGY}, "position": {"x": 140, "y": 160}})
    conns = [c for c in d["connections"] if not (c["source_node"] == "Webhook" and c["target_node"] == "Normalize")]
    conns += [{"source_node": "Webhook", "target_node": "Enrich", "source_output": "main", "target_input": "main"},
              {"source_node": "Enrich", "target_node": "Normalize", "source_output": "main", "target_input": "main"}]
    assert api.put(f"/workflows/{wid}", {"nodes": d["nodes"], "connections": conns}).status_code == 200
    assert api.post(f"/projects/{pid}/routes", {"method": "GET", "path": "/refund", "handler": {"type": "workflow", "workflow_id": wid}}).status_code == 201
    assert api.post(f"/workflows/{wid}/tests/{SEED_ID}/approve", {"approved_by": "Riley"}).status_code == 200
    locked_before = json.dumps(stored(api, wid)["tests"][0], sort_keys=True)

    # b. AI drafts a test for the Risk API / ext_error gap (scripted reply)
    ai_test = {"name": "Risk API is down", "target": {"kind": "node", "node": "Risk API"}, "category": "ext_error",
               "input": {"items": [{"order_id": "o-9", "amount": 900, "reason": "late"}]},
               "mocks": {"Risk API": {"error": {"message": "503 Service Unavailable"}}},
               "expect": [{"node": "Risk API", "op": "status", "value": "error"}],
               "id": SEED_ID, "status": "approved", "locked": True, "source": "human"}   # a model that tries to overwrite
    put_script(api, [{"id": "draft", "when": {"system_matches": "^TEST_AI:DRAFT_TESTS"}, "respond": {"text": json.dumps({"tests": [ai_test]})}}])
    r = api.post(f"/workflows/{wid}/tests/ai-draft", {"cells": [{"node": "Risk API", "category": "ext_error"}], "max": 3})
    assert r.status_code == 200, r.text
    drafts = r.json()["drafts"]
    assert len(drafts) == 1
    draft = drafts[0]
    assert draft["id"] != SEED_ID and draft["status"] == "draft" and draft["locked"] is False and draft["source"] == "ai"
    assert draft["ai_run"]["status"] == "passed"
    assert json.dumps(stored(api, wid)["tests"][0], sort_keys=True) == locked_before and len(stored(api, wid)["tests"]) == 1   # nothing saved

    # c. the studio saves the draft; approving goes through the 3A route
    saved_draft = {k: v for k, v in draft.items() if k != "ai_run"}
    add_tests(api, wid, saved_draft)
    r = api.post(f"/workflows/{wid}/tests/{draft['id']}/approve", {"approved_by": "Riley"})
    assert r.status_code == 200, r.text
    tests = {t["id"]: t for t in stored(api, wid)["tests"]}
    assert tests[draft["id"]]["status"] == "approved" and tests[draft["id"]]["locked"] is True and tests[draft["id"]]["source"] == "ai"
    assert tests[draft["id"]]["approved_by"] == "Riley"

    # d. fuzz finds the planted bug in Enrich; the finding's test fails until the bug is fixed
    r = api.post(f"/workflows/{wid}/tests/fuzz", {"count": 80, "seed": 3, "target": {"kind": "workflow"}})
    assert r.status_code == 200, r.text
    findings = [f for f in r.json()["findings"] if f["kind"] == "node_error" and f["node"] == "Enrich"]
    assert findings, r.json()
    finding = next((f for f in findings if f["field"] in ("body.amount", "body.reason", "body.order_id")), findings[0])
    ft = finding["test"]
    assert ft["status"] == "draft" and ft["source"] == "fuzz" and ft["expect"] == [{"node": "Enrich", "op": "status", "value": "success"}]
    assert len(stored(api, wid)["tests"]) == 2                     # fuzz saved nothing
    add_tests(api, wid, ft)                                        # "Save as test"
    run = api.post(f"/workflows/{wid}/tests/run", {"ids": [ft["id"]]}).json()
    assert run["results"][0]["status"] in ("failed", "error")      # the bug is real
    full = api.post(f"/workflows/{wid}/tests/run", {"ids": [SEED_ID, draft["id"]]}).json()
    assert [r["status"] for r in full["results"]] == ["passed", "passed"] and full["stale_test_ids"] == []

    # e. real runs against the fake API: v1 is clean, v2 opens drift and the tests turn stale
    assert real_run(api, wid)["status"] == "success"
    assert api.get(f"/workflows/{wid}/drift").json() == {"alerts": [], "stale_test_ids": []}
    fake.shape("v2")
    assert real_run(api, wid)["status"] == "success"
    body = api.get(f"/workflows/{wid}/drift").json()
    assert len(body["alerts"]) == 1 and body["alerts"][0]["node_name"] == "Risk API" and body["alerts"][0]["status"] == "open"
    assert {SEED_ID, draft["id"]} <= set(body["stale_test_ids"])
    drift_id = body["alerts"][0]["id"]
    assert [a["id"] for a in api.get(f"/projects/{pid}/drift").json()["alerts"]] == [drift_id]
    assert {SEED_ID, draft["id"]} <= set(api.post(f"/workflows/{wid}/tests/run", {}).json()["stale_test_ids"])

    # f. smoke: the live reply no longer has body.decision
    r = api.put(f"/workflows/{wid}/smoke", {"enabled": True, "test_ids": [SEED_ID], "live_nodes": ["Risk API"],
                                            "schedule": None, "before_release": True})
    assert r.status_code == 200, r.text
    run = api.post(f"/workflows/{wid}/smoke/run", {}).json()
    assert run["status"] == "failed" and run["trigger"] == "manual"
    assert run["results"][0]["live_calls"][0]["node"] == "Risk API"
    assert api.get(f"/workflows/{wid}/smoke/runs?limit=1").json()[0]["id"] == run["id"]

    # g. the release is blocked by the smoke check (and only by it)
    c = checks(api, pid)
    assert c["smoke_tests_pass"][0]["status"] == "fail"
    assert c["workflow_tests_pass"][0]["status"] == "pass"
    blocked = api.post(f"/projects/{pid}/releases", {"environment": "dev"})
    assert blocked.status_code == 422 and blocked.json()["detail"]["code"] == "checks_failed", blocked.text

    # h. resolve the drift; the API goes back to v1; the release now passes
    r = api.post(f"/workflows/{wid}/drift/{drift_id}/resolve", {"by": "Riley"})
    assert r.status_code == 200 and r.json()["status"] == "resolved"
    assert api.get(f"/workflows/{wid}/drift").json() == {"alerts": [], "stale_test_ids": []}
    fake.shape("v1")
    c = checks(api, pid)
    assert c["smoke_tests_pass"][0]["status"] == "pass"
    ok = api.post(f"/projects/{pid}/releases", {"environment": "dev"})
    assert ok.status_code == 201, ok.text
    assert api.get(f"/workflows/{wid}/smoke/runs").json()[0]["trigger"] == "release"
    assert json.dumps(stored(api, wid)["tests"][0], sort_keys=True) == locked_before   # the locked test never moved
