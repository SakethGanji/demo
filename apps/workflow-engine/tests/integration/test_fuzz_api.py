"""Stage 3B fuzzing over HTTP on a real engine + Postgres: a planted bug is found with its node and field, the
finding's draft test fails on the buggy workflow and passes once the bug is fixed; the generators are deterministic
per seed and the count cap holds. Nothing is saved by the route."""

from __future__ import annotations

import copy
import json

import pytest

from .conftest import uniq

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module", autouse=True)
def _drop_test_mode_runs(test_db):
    """Test-mode runs (POST /tests/run) are kept by age, not by the run-retention count, and the engine/DB are
    shared for the whole session: delete the ones this module made so the global executions count stays honest
    for test_review_g4_runs (``count(*) from executions <= 100``)."""
    yield
    test_db.query("delete from executions where mode = 'test'")

BUGGY = (
    "item = items[0]['json']['body']\n"
    "email = item['customer']['email']\n"
    "return [{'json': {'domain': email.split('@')[1], 'total': item['amount'] + 1}}]\n"
)
FIXED = (
    "item = items[0]['json'].get('body')\n"
    "item = item if isinstance(item, dict) else {}\n"
    "customer = item.get('customer')\n"
    "email = customer.get('email') if isinstance(customer, dict) else None\n"
    "email = email if isinstance(email, str) else ''\n"
    "amount = item.get('amount')\n"
    "amount = amount if isinstance(amount, (int, float)) and not isinstance(amount, bool) else 0\n"
    "return [{'json': {'domain': email.split('@')[-1], 'total': amount + 1}}]\n"
)
BASE_TEST = {
    "id": "t_aaaa0001", "name": "happy", "target": {"kind": "workflow"}, "mocks": {}, "category": "normal",
    "input": {"items": [{"body": {"customer": {"email": "ann@example.com", "name": "Ann"}, "amount": 10}}]},
    "expect": [{"node": "Enrich", "op": "status", "value": "success"}],
}


def definition(code: str) -> dict:
    return {
        "name": uniq("fuzz"), "settings": {},
        "nodes": [
            {"name": "Hook", "type": "Webhook", "parameters": {"method": "POST", "path": uniq("fz"), "responseMode": "onReceived"}},
            {"name": "Enrich", "type": "Code", "parameters": {"code": code}},
        ],
        "connections": [{"source_node": "Hook", "target_node": "Enrich", "source_output": "main", "target_input": "main"}],
        "tests": [BASE_TEST],
    }


def make(api, body: dict) -> str:
    r = api.post("/workflows", body)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def fuzz(api, wid: str, **body) -> dict:
    r = api.post(f"/workflows/{wid}/tests/fuzz", body)
    assert r.status_code == 200, r.text
    return r.json()


def run_test_on(api, wid: str, code: str, test: dict) -> dict:
    wf = definition(code)
    wf["tests"] = [test]
    r = api.post(f"/workflows/{wid}/tests/run", {"definition": wf})
    assert r.status_code == 200, r.text
    return r.json()["results"][0]


def test_planted_bug_is_found_with_node_and_field_and_the_draft_test_tracks_the_fix(api):
    wid = make(api, definition(BUGGY))
    out = fuzz(api, wid, count=80)
    errors = [f for f in out["findings"] if f["kind"] == "node_error" and f["node"] == "Enrich"]
    assert errors, out
    fields = {f["field"] for f in errors}
    assert fields & {"body.customer.email", "body.amount", "body.customer"}, fields
    assert any(f["field"] == "body.customer.email" for f in errors) or any(f["field"] == "body.amount" for f in errors)
    f = next(f for f in errors if f["field"] in ("body.customer.email", "body.amount"))
    assert f["count"] >= 1 and f["error"]["message"]
    assert f["input"]["items"] and isinstance(f["input"]["items"][0]["body"], dict)
    t = f["test"]
    assert t["status"] == "draft" and t["locked"] is False and t["source"] == "fuzz"
    assert t["expect"] == [{"node": "Enrich", "op": "status", "value": "success"}]
    assert t["category"] in ("missing", "wrong_type", "boundary", "unusual") and t["field"] == f["field"]
    assert t["id"] != BASE_TEST["id"]

    assert run_test_on(api, wid, BUGGY, t)["status"] != "passed"
    assert run_test_on(api, wid, FIXED, t)["status"] == "passed"

    after = fuzz(api, make(api, definition(FIXED)), count=80)
    assert [x for x in after["findings"] if x["kind"] == "node_error"] == []
    # nothing was saved by the route
    stored = api.get(f"/workflows/{wid}").json()["definition"]
    assert [x["id"] for x in stored["tests"]] == [BASE_TEST["id"]]


def test_same_seed_same_findings_different_seed_may_differ_and_count_is_capped(api):
    wid = make(api, definition(BUGGY))
    a, b = fuzz(api, wid, count=30, seed=7), fuzz(api, wid, count=30, seed=7)
    assert a == b
    assert a["runs"] <= 30 and a["inputs_tried"] <= 30 and a["seed"] == 7 and a["ai_inputs"] == 0
    c = fuzz(api, wid, count=30, seed=8)
    assert c["runs"] <= 30
    assert fuzz(api, wid, count=1)["runs"] == 1
    assert fuzz(api, wid, count=5)["runs"] <= 5


def test_fuzz_validates_the_request(api):
    wid = make(api, definition(BUGGY))
    assert api.post(f"/workflows/{wid}/tests/fuzz", {"count": 0}).status_code == 422
    assert api.post(f"/workflows/{wid}/tests/fuzz", {"count": 201}).status_code == 422
    assert api.post("/workflows/wf_missing/tests/fuzz", {}).status_code == 404
    assert api.post(f"/workflows/{wid}/tests/fuzz", {"target": {"kind": "node", "node": "Nope"}}).status_code == 422
    assert api.post(f"/workflows/{wid}/tests/fuzz", {"target": {"kind": "range"}}).status_code == 422
    assert api.post(f"/workflows/{wid}/tests/fuzz", {"ai": True}).status_code in (200, 502, 503)


def test_node_target_fuzz_uses_the_node_and_its_findings_are_node_tests(api):
    wid = make(api, definition(BUGGY))
    t = {**BASE_TEST, "id": "t_aaaa0002", "target": {"kind": "node", "node": "Enrich"},
         "input": {"items": [{"body": {"customer": {"email": "a@b.co"}, "amount": 3}}]}}
    wf = definition(BUGGY)
    wf["tests"] = [t]
    wid = make(api, wf)
    out = fuzz(api, wid, count=60, target={"kind": "node", "node": "Enrich"})
    errs = [f for f in out["findings"] if f["kind"] == "node_error"]
    assert errs and all(f["test"]["target"] == {"kind": "node", "node": "Enrich"} for f in errs)
    assert json.dumps(out)  # serialisable, no markers leaked
