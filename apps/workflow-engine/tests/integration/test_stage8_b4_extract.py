"""Stage 8 B4: convert selection to sub-workflow over HTTP on real Postgres (DESIGN-STAGE8-REUSE 7.1-7.3).

A refund-style bank workflow is split in two. The run result must be the same before and after, the child must exist
and be callable, the parent must be rewritten in one save, locked tests must survive, and tests that checked a moved
step must be marked stale (and block publish until they are reviewed)."""

from __future__ import annotations

import pytest

from ._stage8_b4_support import C, N, make, publish, setf
from .conftest import uniq

pytestmark = pytest.mark.integration

TEST_A = "t_aaaa0001"   # asserts on Enrich (an inner step): affected
TEST_B = "t_bbbb0002"   # asserts on Score (the exit): follows the call node


def refund_body(path: str | None = None) -> dict:
    return {
        "name": uniq("s8b4-refund"),
        "nodes": [
            N("Webhook", "Webhook", {"method": "POST", "path": path or uniq("refund")}),
            setf("Enrich", 200, amount="{{ $json.body.amount }}", customer="{{ $json.body.customer }}"),
            setf("Score", 400, doubled="{{ $json.amount * 2 }}", band="gold"),
            setf("Decide", 600, verdict="{{ $json.band }}-{{ $json.doubled }}"),
        ],
        "connections": [C("Webhook", "Enrich"), C("Enrich", "Score"), C("Score", "Decide")],
        "settings": {},
    }


def refund_tests(parent_nodes_exit: str = "Score") -> list[dict]:
    base = {"target": {"kind": "workflow"}, "input": {"items": [{"body": {"amount": 120, "customer": "c-1"}}]}, "mocks": {},
            "category": "normal", "status": "draft", "source": "human"}
    return [
        {**base, "id": TEST_A, "name": "Enrich keeps the amount",
         "expect": [{"node": "Enrich", "port": "main", "path": "amount", "op": "eq", "value": 120}]},
        {**base, "id": TEST_B, "name": "Score doubles it",
         "expect": [{"node": parent_nodes_exit, "port": "main", "path": "doubled", "op": "eq", "value": 240}]},
    ]


def get(api, wid):
    r = api.get(f"/workflows/{wid}")
    assert r.status_code == 200, r.text
    return r.json()


def run_decide(api, wid):
    r = api.post(f"/workflows/{wid}/run", {"input_data": {"amount": 120, "customer": "c-1"}})
    assert r.status_code == 200, r.text
    run = r.json()
    assert run["status"] == "success", run
    return run, business(run["data"]["Decide"][0]["json"])


def business(item: dict) -> dict:
    """The item without clock stamps and the trigger's ``_`` metadata (a sub-workflow's trigger adds ``_triggeredAt``,
    ``_triggerType`` and ``_executionDepth`` to what the child returns, DESIGN-STAGE8-REUSE 2.1)."""
    return {k: v for k, v in item.items() if not k.startswith("_") and k != "triggeredAt"}


def extract(api, wid, nodes, name="Refund scoring", dry_run=None, expect=200, **extra):
    body = {"nodes": nodes, "name": name, **extra}
    if dry_run is not None:
        body["dry_run"] = dry_run
    r = api.post(f"/workflows/{wid}/extract-subworkflow", body)
    assert r.status_code == expect, r.text
    return r.json() if expect == 200 else r.json()["detail"]


def test_dry_run_plans_and_writes_nothing(api, test_db):
    wid = make(api, refund_body())
    before = get(api, wid)
    count = test_db.query("SELECT count(*) FROM workflows")[0][0]
    out = extract(api, wid, ["Enrich", "Score"])                           # dry_run defaults to true
    assert set(out) >= {"plan", "child_definition", "parent_definition", "warnings", "affected_tests"}
    assert out["plan"]["call_node"] == "Refund scoring" and out["plan"]["trigger_node"] == "Inputs"
    assert [n["name"] for n in out["child_definition"]["nodes"]] == ["Inputs", "Enrich", "Score"]
    assert [n["name"] for n in out["parent_definition"]["nodes"]] == ["Webhook", "Decide", "Refund scoring"] or \
        {n["name"] for n in out["parent_definition"]["nodes"]} == {"Webhook", "Decide", "Refund scoring"}
    assert "child_workflow_id" not in out or out["child_workflow_id"] is None
    assert test_db.query("SELECT count(*) FROM workflows")[0][0] == count
    assert get(api, wid)["definition"] == before["definition"]
    assert extract(api, wid, ["Enrich", "Score"], dry_run=True)["plan"]["call_node"] == "Refund scoring"


def test_convert_end_to_end_keeps_the_run_result_and_links_the_runs(api, test_db):
    wid = make(api, refund_body())
    run_before, decided = run_decide(api, wid)
    assert decided["verdict"] == "gold-240"

    out = extract(api, wid, ["Enrich", "Score"], dry_run=False)
    child = out["child_workflow_id"]
    assert child and out["child_version_number"] == 1 and out["call_version"] == "latest" and out["child_publish_errors"] == []

    parent = get(api, wid)["definition"]
    assert {n["name"] for n in parent["nodes"]} == {"Webhook", "Refund scoring", "Decide"}
    call = next(n for n in parent["nodes"] if n["name"] == "Refund scoring")
    assert call["type"] == "ExecuteWorkflow" and call["parameters"]["workflowId"] == child and call["parameters"]["version"] == "latest"
    assert call["parameters"]["inputMode"] == "map" and call["parameters"]["includeItem"] is True
    assert {(c["source_node"], c["target_node"]) for c in parent["connections"]} == {("Webhook", "Refund scoring"), ("Refund scoring", "Decide")}

    kid = get(api, child)
    assert kid["name"] == "Refund scoring" and kid["active"] is True and kid["project_id"] == get(api, wid)["project_id"]
    assert [n["name"] for n in kid["definition"]["nodes"]][0] == "Inputs"
    assert kid["definition"]["nodes"][0]["type"] == "ExecuteWorkflowTrigger"
    assert any(t["status"] == "draft" and t.get("source") == "suggested" for t in kid["definition"].get("tests") or [])
    assert len(api.get(f"/workflows/{child}/versions").json()) == 1

    run_after, decided_after = run_decide(api, wid)
    assert decided_after == decided                                         # the same answer as before the split
    assert set(run_after["data"]) == {"Webhook", "Refund scoring", "Decide"}

    detail = api.get(f"/executions/{run_after['execution_id']}").json()
    [link] = detail["children"]
    assert (link["node"], link["workflow_id"], link["version_number"], link["status"]) == ("Refund scoring", child, 1, "success")
    refs = test_db.query("SELECT node_name, child_workflow_id, version_spec FROM workflow_refs WHERE parent_workflow_id = %s AND parent_version_id IS NULL", (wid,))
    assert refs == [("Refund scoring", child, "latest")]                    # the index follows the rewrite
    assert [(u["parent_workflow_id"], u["where"]) for u in api.get(f"/workflows/{child}/used-by").json()] == [(wid, "draft")]


def test_refusals_come_back_as_422_with_a_code_and_write_nothing(api, test_db):
    wid = make(api, refund_body())
    before = get(api, wid)["definition"]
    for nodes, code in (([], "CS01"), (["Webhook", "Enrich"], "CS02"), (["Enrich", "Decide"], "CS05")):
        d = extract(api, wid, nodes, dry_run=False, expect=422)
        assert d["code"] == code and d["message"] and isinstance(d["nodes"], list), d
    assert get(api, wid)["definition"] == before
    assert api.post("/workflows/wf_nope_zz/extract-subworkflow", {"nodes": ["a"], "name": "x"}).status_code == 404


def test_locked_tests_follow_the_move_and_the_affected_one_goes_stale(api, test_db):
    wid = make(api, refund_body())
    assert api.put(f"/workflows/{wid}", {"tests": refund_tests()}).status_code == 200
    for tid in (TEST_A, TEST_B):
        assert api.post(f"/workflows/{wid}/tests/{tid}/approve", {"approved_by": "qa"}).status_code == 200
    # locked tests cannot be changed by an ordinary save...
    changed = refund_tests()
    changed[0]["name"] = "renamed"
    changed[0].update(status="approved", locked=True, approved_by="qa")
    assert api.put(f"/workflows/{wid}", {"tests": changed}).status_code == 409

    out = extract(api, wid, ["Enrich", "Score"], dry_run=False)
    assert [a["id"] for a in out["affected_tests"]] == [TEST_A] and out["affected_tests"][0]["nodes"] == ["Enrich"]

    tests = {t["id"]: t for t in get(api, wid)["definition"]["tests"]}
    a, b = tests[TEST_A], tests[TEST_B]
    assert (a["status"], a["locked"], a["stale"]) == ("approved", True, True) and "Refund scoring" in a["stale_reason"]
    assert (b["status"], b["locked"]) == ("approved", True) and not b.get("stale")           # kept locked and approved
    assert b["expect"][0]["node"] == "Refund scoring"                                         # the exit's test follows the call node
    assert a["expect"][0]["node"] == "Enrich"                                                 # not deleted, not rewritten

    # an ordinary save that round-trips the tests unchanged is still fine (the bypass is only for the extract write)
    assert api.put(f"/workflows/{wid}", {"tests": list(tests.values())}).status_code == 200

    # an approved stale test blocks publish until it is reviewed
    blocked = api.post(f"/workflows/{wid}/publish", {})
    assert blocked.status_code == 422, blocked.text
    stale = [f for f in blocked.json()["detail"]["failed_tests"] if f["test_id"] == TEST_A]
    assert stale and stale[0]["failures"][0]["kind"] == "stale"

    assert api.post(f"/workflows/{wid}/tests/{TEST_A}/unlock", {"by": "qa"}).status_code == 200      # back to a draft...
    t = {t["id"]: t for t in get(api, wid)["definition"]["tests"]}[TEST_A]
    assert t["status"] == "draft" and t["stale"] is True                                      # ...still flagged for review
    again = api.post(f"/workflows/{wid}/publish", {})
    # the stale flag no longer objects; what does is the exit's test: the call is an outside step answered by a default mock
    # that has no `doubled` (the test needs a mock of the sub-workflow's reply, or the inline child of section 6.3)
    assert again.status_code == 422, again.text
    assert [(f["test_id"], f["failures"][0]["kind"]) for f in again.json()["detail"]["failed_tests"]] == [(TEST_B, "assertion")]
    # approving again is the review: it clears the flag
    assert api.post(f"/workflows/{wid}/tests/{TEST_A}/approve", {"approved_by": "qa"}).status_code == 200
    t = {t["id"]: t for t in get(api, wid)["definition"]["tests"]}[TEST_A]
    assert "stale" not in t and "stale_reason" not in t


def test_a_child_that_cannot_publish_leaves_the_call_on_draft(api, test_db):
    body = refund_body()
    body["nodes"][2] = N("Score", "ExecuteWorkflow", {"workflowId": "wf_gone_zz", "version": "draft", "inputMode": "passThrough"}, 400)
    wid = make(api, body)
    out = extract(api, wid, ["Enrich", "Score"], dry_run=False)
    child = out["child_workflow_id"]
    assert child and out["child_version_number"] is None and out["call_version"] == "draft"
    assert any(e["kind"] == "check" and e["code"] == "WF006" for e in out["child_publish_errors"]), out["child_publish_errors"]
    call = next(n for n in get(api, wid)["definition"]["nodes"] if n["type"] == "ExecuteWorkflow")
    assert call["parameters"]["version"] == "draft" and call["parameters"]["workflowId"] == child     # still runnable as a draft
    assert get(api, child)["active"] is False and api.get(f"/workflows/{child}/versions").json() == []
