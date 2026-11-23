"""Approvals over HTTP on real Postgres (Builder A smoke): a run pauses at an Approval, the task shows up in the inbox,
a decision resumes the same execution, double clicks and late decisions are handled.

The wider contract tests (restart, crash mid-resume, timeouts through a forced tick, retention under load, 60-task paging)
are the testers' (DESIGN-STAGE5-UIS section 10.2); the engine-level invariants are in tests/unit/test_task_*_pg.py."""

from __future__ import annotations

import time

import pytest

from .conftest import uniq

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _tasks_router_registered(api):
    """routes/__init__.py is Builder B's: until it includes routes/tasks.py these tests have nothing to call."""
    if api.get("/tasks/counts").status_code == 404:
        pytest.skip("routes/tasks.py is not registered in routes/__init__.py yet")


def N(name, type_, params=None, x=0):
    return {"name": name, "type": type_, "parameters": params or {}, "position": {"x": x, "y": 0}}


def C(a, b, port="main"):
    return {"source_node": a, "target_node": b, "source_output": port, "target_input": "main"}


def approval_workflow(**params) -> dict:
    return {"name": uniq("approval"), "nodes": [
        N("Start", "Start"),
        N("Review", "Approval", {"title": "Refund {{ $json.body.order }}", "context": {"Order": "{{ $json.body.order }}"},
                                 "form": [{"name": "note", "label": "Note", "type": "text"}], **params}, 200),
        N("Paid", "Set", {"mode": "manual", "keepOnlySet": True, "fields": [{"name": "outcome", "value": "paid"}]}, 400),
        N("Refused", "Set", {"mode": "manual", "keepOnlySet": True, "fields": [{"name": "outcome", "value": "refused"}]}, 400),
    ], "connections": [C("Start", "Review"), C("Review", "Paid", "approved"), C("Review", "Refused", "rejected")], "settings": {}}


def make(api, **params):
    pid = api.post("/projects", {"name": uniq("appr")}).json()["id"]
    r = api.post(f"/projects/{pid}/workflows", approval_workflow(**params))
    assert r.status_code == 201, r.text
    return pid, r.json()["id"]


def run_and_find_task(api, pid, wid) -> tuple[str, dict]:
    r = api.post(f"/workflows/{wid}/run", {"input_data": {"order": "A-7"}})
    assert r.status_code == 200, r.text
    exec_id = r.json()["execution_id"]
    page = api.get("/tasks", params={"project_id": pid, "status": "open"}).json()
    task = next(t for t in page["items"] if t["execution_id"] == exec_id)
    return exec_id, task


def wait_for(api, exec_id, status, timeout=20):
    deadline = time.time() + timeout
    while True:
        got = api.get(f"/executions/{exec_id}").json()
        if got["status"] == status or time.time() > deadline:
            return got
        time.sleep(0.2)


def test_run_pauses_task_appears_decision_resumes_the_same_execution(api):
    pid, wid = make(api, due_in="3h")
    exec_id, task = run_and_find_task(api, pid, wid)
    assert api.get(f"/executions/{exec_id}").json()["status"] == "waiting"
    assert task["status"] == "open" and task["title"].startswith("Refund ") and task["node_name"] == "Review"
    assert task["workflow_id"] == wid and task["due_at"] is not None and task["decided_by"] is None

    counts = api.get("/tasks/counts", params={"project_id": pid}).json()
    assert counts["open"] == 1 and counts["by_workflow"] == {wid: 1}
    detail = api.get(f"/tasks/{task['id']}").json()
    assert detail["context"]["Order"] == "A-7" and detail["form"][0]["name"] == "note" and detail["run_status"] == "waiting"
    assert detail["allow_comment"] is True and "snapshot" not in detail

    body = {"decision": "approved", "decided_by": "Dana", "comment": "ok", "form_data": {"note": "go"}}
    assert api.post(f"/tasks/{task['id']}/decide", body).status_code == 422  # Idempotency-Key is required
    won = api.post(f"/tasks/{task['id']}/decide", body, headers={"Idempotency-Key": "k1"})
    assert won.status_code == 200 and won.json()["outcome"] == "decided" and won.json()["task"]["status"] == "approved"
    replay = api.post(f"/tasks/{task['id']}/decide", body, headers={"Idempotency-Key": "k1"})
    assert replay.json()["outcome"] == "decided"
    other = api.post(f"/tasks/{task['id']}/decide", {**body, "decision": "rejected", "decided_by": "Eli"}, headers={"Idempotency-Key": "k2"})
    assert other.json()["outcome"] == "already_decided" and other.json()["task"]["decided_by"] == "Dana"

    done = wait_for(api, exec_id, "success")
    assert done["status"] == "success"
    assert [d["json"]["outcome"] for d in done["node_data"]["Paid"]] == ["paid"] and "Refused" not in done["node_data"]
    approval = done["node_data"]["Review"][0]["json"]["approval"]
    assert approval["decision"] == "approved" and approval["comment"] == "ok" and approval["form_data"] == {"note": "go"}
    assert api.get("/tasks/counts", params={"project_id": pid}).json()["open"] == 0
    closed = api.get("/tasks", params={"project_id": pid, "status": "closed"}).json()["items"]
    assert [t["id"] for t in closed] == [task["id"]]


def test_reject_takes_the_rejected_branch(api):
    pid, wid = make(api)
    exec_id, task = run_and_find_task(api, pid, wid)
    r = api.post(f"/tasks/{task['id']}/decide", {"decision": "rejected", "decided_by": "Eli"}, headers={"Idempotency-Key": "r1"})
    assert r.status_code == 200
    done = wait_for(api, exec_id, "success")
    assert "Paid" not in done["node_data"] and done["node_data"]["Refused"][0]["json"]["outcome"] == "refused"


def test_cancelling_a_waiting_run_closes_its_task(api):
    pid, wid = make(api)
    exec_id, task = run_and_find_task(api, pid, wid)
    assert api.post(f"/executions/{exec_id}/cancel").status_code == 200
    assert api.get(f"/executions/{exec_id}").json()["status"] == "cancelled"
    assert api.get(f"/tasks/{task['id']}").json()["status"] == "cancelled"
    res = api.post(f"/tasks/{task['id']}/decide", {"decision": "approved", "decided_by": "Dana"}, headers={"Idempotency-Key": "c1"})
    assert res.json()["outcome"] == "already_closed"


def test_validation_and_unknown_task(api):
    pid, wid = make(api, require_comment_on_reject=True)
    _, task = run_and_find_task(api, pid, wid)
    h = {"Idempotency-Key": "v1"}
    assert api.post(f"/tasks/{task['id']}/decide", {"decision": "maybe", "decided_by": "x"}, headers=h).status_code == 422
    assert api.post(f"/tasks/{task['id']}/decide", {"decision": "approved"}, headers=h).status_code == 422  # who decided?
    rej = api.post(f"/tasks/{task['id']}/decide", {"decision": "rejected", "decided_by": "x"}, headers=h)
    assert rej.status_code == 422 and "comment" in rej.json()["detail"]["fields"]
    bad_form = api.post(f"/tasks/{task['id']}/decide", {"decision": "approved", "decided_by": "x", "form_data": {"nope": 1}}, headers=h)
    assert bad_form.status_code == 422 and "nope" in bad_form.json()["detail"]["fields"]
    assert api.get(f"/tasks/{task['id']}").json()["status"] == "open"  # none of those decided it
    assert api.get("/tasks/tsk_missing").status_code == 404
    assert api.post("/tasks/tsk_missing/decide", {"decision": "approved", "decided_by": "x"}, headers=h).status_code == 404
    assert api.get("/tasks", params={"status": "bogus"}).status_code == 422
    assert api.get("/tasks", params={"cursor": "###"}).status_code == 422
