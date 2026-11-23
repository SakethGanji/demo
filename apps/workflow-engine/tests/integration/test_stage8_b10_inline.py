"""Stage 8 B10 over HTTP on real Postgres: inline children in tests (6.3), typed generated mocks (6.2), and "used by" after every
kind of draft write (5.1)."""

from __future__ import annotations

import uuid

import pytest

from ._stage8_b4_support import C, N, make, parent_body, publish, save, setf, trigger
from .conftest import uniq

pytestmark = pytest.mark.integration


def tid() -> str:
    return "t_" + uuid.uuid4().hex[:8]


def child_with_mail(project_id: str | None = None, defaults: dict | None = None) -> dict:
    """Inputs -> Notify (SendEmail, an outside step) -> Mark (Set)."""
    body = {"name": uniq("s8b10-child"),
            "nodes": [trigger(("amount", "number", False)),
                      N("Notify", "SendEmail", {"toEmail": "ops@bank.test", "subject": "s", "body": "b"}, 200),
                      setf("Mark", 400, tag="child")],
            "connections": [C("Inputs", "Notify"), C("Notify", "Mark")], "settings": {}}
    if defaults is not None:
        body["test_defaults"] = defaults
    return body


def parent_with_inline_test(child_id: str, **test_kw) -> tuple[dict, dict]:
    body = parent_body(child_id, "draft")
    t = {"id": tid(), "name": "inline", "target": {"kind": "workflow"}, "input": {"items": [{"amount": 5}]},
         "subworkflows": {"call": "inline"}, "mocks": {}, "expect": [], "category": "normal", **test_kw}
    body["tests"] = [t]
    return body, t


def put_tests(api, wid: str, body: dict) -> None:
    r = api.put(f"/workflows/{wid}", {k: body[k] for k in ("name", "nodes", "connections", "settings", "tests") if k in body})
    assert r.status_code == 200, r.text


def run(api, wid: str, **body) -> dict:
    r = api.post(f"/workflows/{wid}/tests/run", body)
    assert r.status_code == 200, r.text
    return r.json()


def only(report: dict) -> dict:
    assert len(report["results"]) == 1
    return report["results"][0]


MAIL = {"items": [{"success": True, "to": "ops@bank.test", "subject": "s", "src": "child-default"}]}


# ── 6.3 inline children ───────────────────────────────────────────────────────────────────────────────────────────


def test_an_inline_child_runs_with_its_own_default_mock_and_can_be_asserted_on(api):
    child = make(api, child_with_mail(defaults={"mocks": {"Notify": MAIL}}))
    body, t = parent_with_inline_test(child, expect=[
        {"node": "call/Notify", "op": "status", "value": "success"},
        {"node": "call/Notify", "path": "src", "op": "eq", "value": "child-default"},
        {"node": "call/Mark", "path": "tag", "op": "eq", "value": "child"},
        {"node": "call", "path": "tag", "op": "eq", "value": "child"},
    ])
    wid = make(api, body)
    res = only(run(api, wid))
    assert res["status"] == "passed", [(f.get("node"), f.get("path"), f["message"]) for f in res["failures"]]
    assert all(a["passed"] for a in res["assertions"]) and len(res["assertions"]) == 4
    assert res["node_status"]["call/Mark"] == "success" and res["node_status"]["call"] == "success"
    assert res["outputs"]["call/Notify"]["main"][0]["src"] == "child-default"


def test_the_parent_tests_path_mock_beats_the_childs_default(api):
    child = make(api, child_with_mail(defaults={"mocks": {"Notify": MAIL}}))
    body, t = parent_with_inline_test(
        child, mocks={"call/Notify": {"items": [{"success": True, "to": "ops@bank.test", "subject": "s", "src": "parent-test"}]}},
        expect=[{"node": "call/Notify", "path": "src", "op": "eq", "value": "parent-test"}])
    res = only(run(api, make(api, body)))
    assert res["status"] == "passed", res["failures"]


def test_an_unmocked_child_step_fails_the_test_naming_the_path(api):
    child = make(api, child_with_mail())                                   # no default mock for Notify
    body, t = parent_with_inline_test(child, expect=[{"node": "call/Mark", "op": "status", "value": "success"}])
    res = only(run(api, make(api, body)))
    assert res["status"] == "error"
    (f,) = [f for f in res["failures"] if f["kind"] == "unmocked"]
    assert "call/Notify" in f["message"] and "network" in f["message"]


def test_a_persisted_inline_child_is_a_test_row_linked_to_the_parents_test_run(api, test_db):
    child = make(api, child_with_mail(defaults={"mocks": {"Notify": MAIL}}))
    body, t = parent_with_inline_test(child, expect=[{"node": "call/Mark", "op": "status", "value": "success"}])
    wid = make(api, body)
    res = only(run(api, wid))
    assert res["status"] == "passed" and res["execution_id"]
    rows = test_db.query("SELECT mode, parent_execution_id, parent_node_name FROM executions WHERE workflow_id = %s", (child,))
    assert rows == [("test", res["execution_id"], "call")]


def test_naming_a_node_that_is_not_an_execute_workflow_is_a_test_error_not_a_crash(api):
    child = make(api, child_with_mail(defaults={"mocks": {"Notify": MAIL}}))
    body, t = parent_with_inline_test(child, subworkflows={"Start": "inline"},
                                      expect=[{"node": "call", "op": "status", "value": "success"}])
    body["tests"][0]["mocks"] = {"call": {"items": [{}]}}
    res = only(run(api, make(api, body)))
    assert res["status"] == "error"
    assert res["failures"][0]["kind"] == "setup" and "Start" in res["failures"][0]["message"]


def test_without_inline_the_call_is_still_an_outside_step_that_needs_a_mock(api):
    child = make(api, child_with_mail(defaults={"mocks": {"Notify": MAIL}}))
    body, t = parent_with_inline_test(child, expect=[{"node": "call", "op": "status", "value": "success"}])
    body["tests"][0].pop("subworkflows")
    res = only(run(api, make(api, body)))
    assert res["status"] == "error"
    (f,) = [f for f in res["failures"] if f["kind"] == "unmocked"]
    assert f["node"] == "call" and "/" not in f["message"].split('"')[1]


def test_the_inline_field_survives_a_save_and_load(api):
    child = make(api, child_with_mail(defaults={"mocks": {"Notify": MAIL}}))
    body, t = parent_with_inline_test(child, expect=[{"node": "call/Mark", "op": "status", "value": "success"}])
    wid = make(api, body)
    got = api.get(f"/workflows/{wid}").json()["definition"]["tests"][0]
    assert got["subworkflows"] == {"call": "inline"}


# ── 6.2 typed generated mocks ─────────────────────────────────────────────────────────────────────────────────────


def test_suggested_mocks_for_a_sub_workflow_call_have_the_childs_output_shape(api):
    child_id = make(api, {"name": uniq("s8b10-typed"),
                          "nodes": [trigger(("amount", "number", False)), setf("Decide", 200, verdict="review", limit="500")],
                          "connections": [C("Inputs", "Decide")], "settings": {}})
    publish(api, child_id)
    wid = make(api, parent_body(child_id, "latest", mode="map", inputs={"amount": "{{ $json.amount }}"}))
    r = api.post(f"/workflows/{wid}/tests/suggest", {"node": "call"})
    assert r.status_code == 200, r.text
    success = next(d for d in r.json() if d["category"] == "ext_success")
    item = success["mocks"]["call"]["items"][0]
    assert {"verdict", "limit"} <= set(item), item                         # typed from the child, not {"id": 1}
    assert "id" not in item


def test_a_mock_that_breaks_the_childs_output_contract_is_reported(api):
    child_id = make(api, {"name": uniq("s8b10-typed"),
                          "nodes": [trigger(("amount", "number", False)), setf("Decide", 200, verdict="review")],
                          "connections": [C("Inputs", "Decide")], "settings": {}})
    publish(api, child_id)
    body = parent_body(child_id, "latest", mode="map", inputs={"amount": "{{ $json.amount }}"})
    good = {"id": tid(), "name": "good", "target": {"kind": "workflow"}, "input": {"items": [{"amount": 1}]},
            "mocks": {"call": {"items": [{"verdict": "ok"}]}}, "expect": [{"node": "call", "op": "status", "value": "success"}],
            "category": "normal"}
    bad = {**good, "id": tid(), "name": "bad", "mocks": {"call": {"items": [{"verdict": 42}]}}}
    body["tests"] = [good, bad]
    wid = make(api, body)
    report = run(api, wid)
    by = {r["name"]: r for r in report["results"]}
    assert by["good"]["status"] == "passed", by["good"]["failures"]
    assert by["bad"]["status"] == "failed"
    assert any(f["kind"] == "contract" and f["node"] == "call" for f in by["bad"]["failures"])


# ── 5.1 used-by after every draft write ───────────────────────────────────────────────────────────────────────────


def used_by(api, child: str) -> list[tuple[str, str, str]]:
    return sorted((u["parent_workflow_id"], u["node"], u["where"]) for u in api.get(f"/workflows/{child}/used-by").json())


def test_used_by_follows_create_update_ops_restore_and_test_saves(api):
    a = make(api, child_with_mail())
    b = make(api, child_with_mail())
    # create
    parent = make(api, parent_body(a, "draft"))
    assert used_by(api, a) == [(parent, "call", "draft")] and used_by(api, b) == []
    # update (PUT)
    body = parent_body(b, "draft")
    save(api, parent, body)
    assert used_by(api, a) == [] and used_by(api, b) == [(parent, "call", "draft")]
    # node mutation (apply-ops)
    r = api.post(f"/workflows/{parent}/apply-ops", {"ops": [
        {"op": "set_parameter", "args": {"node": "call", "path": "workflowId", "value": a}}]})
    assert r.status_code == 200, r.text
    assert used_by(api, a) == [(parent, "call", "draft")] and used_by(api, b) == []
    # a save that only touches tests keeps the rows
    t = {"id": tid(), "name": "x", "target": {"kind": "workflow"}, "input": {"items": [{}]}, "mocks": {"call": {"items": [{"success": True, "to": "a@b.test", "subject": "s"}]}},
         "expect": [{"node": "call", "op": "status", "value": "success"}], "category": "normal"}
    r = api.put(f"/workflows/{parent}", {"name": body["name"], "nodes": api.get(f"/workflows/{parent}").json()["definition"]["nodes"],
                                          "connections": body["connections"], "tests": [t]})
    assert r.status_code == 200, r.text
    assert used_by(api, a) == [(parent, "call", "draft")]
    # approve a test (writes the draft through the repository too)
    assert api.post(f"/workflows/{parent}/tests/{t['id']}/approve", {"approved_by": "sam"}).status_code == 200
    assert used_by(api, a) == [(parent, "call", "draft")]
    # restore an older version that called b
    save(api, parent, parent_body(b, "latest"))
    publish(api, b)
    publish(api, parent)
    versions = api.get(f"/workflows/{parent}/versions").json()
    v_b = versions[0]["id"]
    api.post(f"/workflows/{parent}/apply-ops", {"ops": [{"op": "remove_node", "args": {"name": "call"}}]})
    assert used_by(api, b) == [(parent, "call", "published")]              # only the published version still calls b
    r = api.post(f"/workflows/{parent}/versions/{v_b}/restore", {})
    assert r.status_code == 200, r.text
    assert (parent, "call", "draft") in used_by(api, b)
