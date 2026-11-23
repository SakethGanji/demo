"""Stage 5 T1: size limits, secrets at rest, orphaned tasks and preview runs (DESIGN-STAGE5-UIS 3.4, 5.2, 5.3, 7.1, 8.8)."""

from __future__ import annotations

import json
import time
import uuid

import pytest

from .conftest import uniq
from .test_stage5_t1_support import (  # noqa: F401  (fixtures + helpers)
    C, GOOD_FIELDS, N, decide, exec_row, new_project, poll_until, publish_flow, refund_flow, refund_workflow, run_view, stack,
    submit_waiting, task_row, wait_run,
)

pytestmark = pytest.mark.integration


def approval_wf(**params) -> dict:
    return {"name": uniq("lim"), "settings": {}, "nodes": [
        N("Start", "Start"), N("Review", "Approval", {"title": "Review", **params}, 200),
        N("Paid", "Set", {"mode": "manual", "keepOnlySet": True, "fields": [{"name": "outcome", "value": "paid"}]}, 400),
    ], "connections": [C("Start", "Review"), C("Review", "Paid", "approved")]}


def run(stack, pid, wf, body):
    wid = stack.api.post(f"/projects/{pid}/workflows", wf).json()["id"]
    r = stack.api.post(f"/workflows/{wid}/run", {"input_data": body})
    assert r.status_code == 200, r.text
    return wid, r.json()


def test_a_task_context_over_64kb_is_truncated_and_flagged(stack):
    pid, _ = new_project(stack.api)
    wf = approval_wf(context={"Huge": "{{ $json.body.big }}", "Small": "{{ $json.body.small }}"})
    _, res = run(stack, pid, wf, {"big": "x" * 150_000, "small": "ok"})
    assert res["status"] == "waiting"
    task = stack.api.get("/tasks", params={"project_id": pid}).json()["items"][0]
    d = stack.api.get(f"/tasks/{task['id']}").json()
    assert d["context"].get("_truncated") is True
    assert len(json.dumps(d["context"])) <= 70_000
    assert d["context"]["Small"] == "ok"
    assert decide(stack.api, task["id"], "approved").json()["outcome"] == "decided"     # a truncated view does not stop the decision
    poll_until(lambda: exec_row(stack, task["execution_id"])["status"], lambda s: s == "success", what="resume")


def test_a_snapshot_over_1mb_fails_the_node_loudly_and_creates_no_task(stack):
    pid, _ = new_project(stack.api)
    wid, res = run(stack, pid, approval_wf(), {"big": "x" * 1_300_000})
    assert res["status"] == "failed", res
    assert "APPROVAL_SNAPSHOT_TOO_LARGE" in json.dumps(res) or exec_row(stack, res["execution_id"])["metadata"].get("error_code") == "APPROVAL_SNAPSHOT_TOO_LARGE" \
        or "APPROVAL_SNAPSHOT_TOO_LARGE" in json.dumps(stack.api.get(f"/executions/{res['execution_id']}").json())
    assert stack.q("SELECT count(*) FROM human_tasks WHERE execution_id = %s", (res["execution_id"],))[0][0] == 0
    assert exec_row(stack, res["execution_id"])["status"] == "failed"
    assert stack.api.get("/tasks/counts", params={"project_id": pid}).json()["open"] == 0


def test_a_public_result_over_256kb_is_replaced_by_a_truncated_marker(stack):
    schema = {"type": "object", "required": ["note"], "properties": {"note": {"type": "string", "maxLength": 60000}}}
    slug = uniq("big")
    wf = {"name": uniq("bigres"), "settings": {}, "nodes": [
        N("Form", "FormTrigger", {"input_schema": schema}),
        N("Show result", "Respond", {"statusCode": 200, "body": {k: "{{ $json.note }}" for k in "abcdef"}}, 200)],
        "connections": [C("Form", "Show result")],
        "interface": {"kind": "form", "slug": slug, "title": "Big", "trigger_node": "Form", "result_node": "Show result"}}
    flow = publish_flow(stack, wf)
    r = stack.api.post(f"{flow.base}/submit", {"fields": {"note": "y" * 59000}}, headers={"Idempotency-Key": uuid.uuid4().hex})
    assert r.status_code == 202, r.text
    done = wait_run(flow, r.json())
    assert done["status"] == "success" and done["result"] == {"truncated": True}
    small = stack.api.post(f"{flow.base}/submit", {"fields": {"note": "z" * 100}}, headers={"Idempotency-Key": uuid.uuid4().hex}).json()
    assert wait_run(flow, small)["result"] == {k: "z" * 100 for k in "abcdef"}


def test_secrets_never_land_in_a_task_row_or_its_resume_snapshot(stack):
    pid, _ = new_project(stack.api)
    key = "S5_" + uuid.uuid4().hex[:8].upper()
    value = "s3cr3t-Value-" + uuid.uuid4().hex
    for env in ("dev", "prod"):
        assert stack.api.post(f"/projects/{pid}/secrets", {"key": key, "value": value, "environment": env, "type": "secret"}).status_code == 201
    wf = approval_wf(title="Pay with {{ $vars." + key + " }}", context={"Key": "{{ $vars." + key + " }}", "Order": "{{ $json.body.order }}"})
    _, res = run(stack, pid, wf, {"order": value})                    # the secret also travels in the data
    assert res["status"] == "waiting"
    rows = stack.q("SELECT title, context::text, snapshot::text, form::text, assignees::text FROM human_tasks WHERE execution_id = %s", (res["execution_id"],))
    assert len(rows) == 1
    for col in rows[0]:
        assert value not in col, "a secret value is stored in the task row"
    task = stack.api.get("/tasks", params={"project_id": pid}).json()["items"][0]
    assert value not in json.dumps(task) and value not in stack.api.get(f"/tasks/{task['id']}").text
    meta = stack.q("SELECT metadata::text FROM executions WHERE id = %s", (res["execution_id"],))[0][0]
    assert value not in meta
    # secrets are reloaded at resume, so the run still completes
    assert decide(stack.api, task["id"], "approved").json()["outcome"] == "decided"
    poll_until(lambda: exec_row(stack, res["execution_id"])["status"], lambda s: s == "success", what="resume")


def test_deleting_a_workflow_with_a_waiting_run_leaves_no_zombie_approval(stack):
    pid, _ = new_project(stack.api)
    wid, res = run(stack, pid, approval_wf(), {"order": "A-1"})
    task = stack.api.get("/tasks", params={"project_id": pid}).json()["items"][0]
    assert stack.api.delete(f"/workflows/{wid}").status_code == 200
    assert stack.api.get("/tasks/counts", params={"project_id": pid}).json()["open"] == 0, "an approval for a deleted workflow is still open in the inbox"
    r = decide(stack.api, task["id"], "approved")
    assert r.status_code == 404 or r.json()["outcome"] == "already_closed"
    stack.tick()
    time.sleep(0.5)
    assert stack.q("SELECT count(*) FROM human_tasks WHERE id = %s AND status = 'open'", (task["id"],))[0][0] == 0


def test_a_preview_run_of_the_draft_waits_resumes_and_is_not_a_live_run(stack):
    """mode=interface_preview: the draft runs in the default environment, its task is real, and resuming it works from the snapshot."""
    flow = publish_flow(stack, refund_workflow(stack.refunds.url, uniq("pv")), publish=False)
    r = stack.api.post(f"/workflows/{flow.wid}/interface/preview/submit", {"fields": GOOD_FIELDS}, headers={"Idempotency-Key": "pv-" + uuid.uuid4().hex})
    assert r.status_code == 202, r.text
    exec_id = r.json()["execution_id"]
    data = poll_until(lambda: stack.api.get(f"/workflows/{flow.wid}/interface/preview/data?execution_id={exec_id}").json(),
                      lambda d: d["status"] in ("waiting", "failed", "success"), what="the preview to wait")
    assert data["status"] == "waiting" and len(data["tasks"]) == 1 and data["input"]["_submission"]["mode"] == "preview"
    task_id = data["tasks"][0]["id"]
    # edit the draft while the preview run waits: the run keeps the definition it suspended with (R4)
    nodes = stack.api.get(f"/workflows/{flow.wid}").json()["definition"]["nodes"]
    for n in nodes:
        if n["name"] == "Refund issued":
            for f in n["parameters"]["fields"]:
                if f["name"] == "message":
                    f["value"] = "EDITED"
    assert stack.api.put(f"/workflows/{flow.wid}", {"nodes": nodes}).status_code == 200
    assert decide(stack.api, task_id, "approved").json()["outcome"] == "decided"
    done = poll_until(lambda: stack.api.get(f"/workflows/{flow.wid}/interface/preview/data?execution_id={exec_id}").json(),
                      lambda d: d["status"] in ("success", "failed"), what="the preview to finish")
    assert done["status"] == "success" and done["result"]["status"] == "approved" and done["result"]["message"] == "Your refund has been issued."
    assert stack.api.get(f"{flow.base}/schema").status_code == 404                  # nothing was ever made public by previewing
