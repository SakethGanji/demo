"""Stage 5 T2: the API journey end to end over HTTP (DESIGN-STAGE5-UIS 9.2, 10.2, 10.3) on a real engine subprocess and Postgres.

create the workflow from the fixture -> publish -> submit the public form -> the task appears in /api/tasks and
/api/tasks/counts -> read the task -> approve | reject | time out -> the run completes with the right result, and the fake
bank saw exactly the right refunds. The Classify step runs on the scripted fake LLM (nothing is pinned).
"""

from __future__ import annotations

import base64
import json
import shutil
import subprocess
import time
import uuid
from pathlib import Path

import pytest

from .conftest import uniq
from .test_stage5_t1_support import (  # noqa: F401  (fixtures + helpers)
    C, GOOD_FIELDS, N, Flow, Stack, decide, exec_row, new_project, poll_until, publish_flow, refund_flow, refund_workflow, run_view, stack,
    submit, task_row, wait_run, wait_task,
)

pytestmark = pytest.mark.integration

SUMMARY = "Parcel shows as delivered but the customer reports damage."
SUMMARY_KEYS = {"id", "workflow_id", "workflow_name", "execution_id", "node_name", "title", "status", "assignees", "due_at", "created_at", "decided_by", "decided_at"}
DETAIL_KEYS = SUMMARY_KEYS | {"context", "form", "decision", "screen", "input", "allow_comment", "require_comment_on_reject", "on_timeout", "run_status"}


def script_classifier(stack: Stack, summary: str = SUMMARY, suggest: str = "approve") -> None:
    script = {"rules": [{"id": "classify", "when": {"system_matches": "REFUND_CLASSIFY"},
                         "respond": {"text": json.dumps({"category": "damaged", "summary": summary, "suggest": suggest})}}],
              "default": {"raise": "unmatched"}}
    r = stack.hook("PUT", "/fake-llm/script", script)
    assert r.status_code == 200, r.text


def refunds_for(stack: Stack, exec_id: str) -> list[dict]:
    return [c for c in stack.refunds.refunds() if (c["key"] or "").startswith(exec_id + ":")]


def start_journey(stack: Stack, **kw):
    """Steps 1-3: workflow from the fixture (Classify on the fake LLM), interface wired, published, form submitted, run waiting."""
    script_classifier(stack)
    wf = refund_workflow(stack.refunds.url, uniq("refunds"), pin=False, **kw)
    flow = publish_flow(stack, wf)
    got = stack.api.get(f"/workflows/{flow.wid}/interface").json()
    assert got["live"]["slug"] == flow.slug and got["warnings"] == []
    assert got["wired"] == {"input": ["order_id", "amount", "reason", "email"], "tasks": ["Manager review"],
                            "context": ["Classify.structured.summary"], "result": "Show result"}
    schema = stack.api.get(f"{flow.base}/schema").json()
    assert [f["name"] for f in schema["fields"]] == ["order_id", "amount", "reason", "email"]
    sub = submit(flow, GOOD_FIELDS, key="journey-" + uuid.uuid4().hex)
    task = wait_task(flow, sub)
    view = wait_run(flow, sub, until=("waiting", "failed", "success"))
    assert view["status"] == "waiting", view
    return flow, sub, task, view


def check_inbox_and_task(stack: Stack, flow: Flow, sub: dict, task: dict) -> dict:
    """Steps 4-5: the task is in the inbox, counted, and readable with its context."""
    listed = stack.api.get("/tasks", params={"project_id": flow.pid, "status": "open"}).json()
    assert [t["id"] for t in listed["items"]] == [task["id"]] and listed["next_cursor"] is None
    assert set(listed["items"][0]) == SUMMARY_KEYS
    assert stack.api.get("/tasks/counts", params={"project_id": flow.pid}).json() == {"open": 1, "by_workflow": {flow.wid: 1}}
    assert stack.api.get("/tasks", params={"project_id": flow.pid, "workflow_id": "wf_other"}).json()["items"] == []
    d = stack.api.get(f"/tasks/{task['id']}").json()
    assert set(d) == DETAIL_KEYS and "snapshot" not in d
    assert d["status"] == "open" and d["title"] == "Refund A-10042 · $742" and d["node_name"] == "Manager review"
    assert d["execution_id"] == sub["execution_id"] and d["workflow_id"] == flow.wid and d["run_status"] == "waiting"
    assert d["context"]["Summary"] == SUMMARY and d["context"]["Suggest"] == "approve"          # the AI summary reached the approver
    assert d["form"][0]["name"] == "note" and d["allow_comment"] is True and d["on_timeout"] in ("timed_out", "approve", "reject")
    assert d["input"]["order_id"] == "A-10042" and d["input"]["amount"] == 742 and d["input"]["email"] == "dana@example.com"
    assert d["screen"] and d["screen"]["components"] and any(c["type"] == "actions" for c in d["screen"]["components"])
    assert d["decision"] is None and d["decided_by"] is None and d["decided_at"] is None and d["due_at"]
    ex = stack.api.get(f"/executions/{sub['execution_id']}").json()
    assert ex["status"] == "waiting"
    return d


def test_journey_approve_pays_exactly_once_and_the_submitter_sees_the_result(stack):
    flow, sub, task, view = start_journey(stack)
    assert [w["title"] for w in view["waiting_for"]] == ["Refund A-10042 · $742"] and view["result"] is None
    assert refunds_for(stack, sub["execution_id"]) == []                       # nothing is paid while waiting
    check_inbox_and_task(stack, flow, sub, task)

    res = decide(stack.api, task["id"], "approved", by="Dana", comment="Looks right", form_data={"note": "Refund approved, 3-5 days"})
    assert res.status_code == 200 and res.json()["outcome"] == "decided"
    won = res.json()["task"]
    assert won["status"] == "approved" and won["decided_by"] == "Dana" and won["decided_at"] and set(won) == DETAIL_KEYS
    assert won["decision"]["comment"] == "Looks right" and won["decision"]["form_data"] == {"note": "Refund approved, 3-5 days"}

    done = wait_run(flow, sub)
    assert done["status"] == "success" and done["waiting_for"] == [] and done["error"] is None
    assert done["result"] == {"status": "approved", "refund_id": done["result"]["refund_id"], "message": "Your refund has been issued."}
    assert done["result"]["refund_id"].startswith("rf_")
    paid = refunds_for(stack, sub["execution_id"])
    assert len(paid) == 1 and paid[0]["key"] == f"{sub['execution_id']}:Manager review:0"
    assert json.loads(paid[0]["body"]) == {"order_id": "A-10042", "amount": 742}

    assert stack.api.get("/tasks/counts", params={"project_id": flow.pid}).json() == {"open": 0, "by_workflow": {}}
    assert stack.api.get("/tasks", params={"project_id": flow.pid, "status": "open"}).json()["items"] == []
    closed = stack.api.get("/tasks", params={"project_id": flow.pid, "status": "closed"}).json()["items"]
    assert [(t["id"], t["status"], t["decided_by"]) for t in closed] == [(task["id"], "approved", "Dana")]
    final = stack.api.get(f"/tasks/{task['id']}").json()
    assert final["run_status"] == "success" and final["status"] == "approved"
    ex = stack.api.get(f"/executions/{sub['execution_id']}").json()
    assert ex["status"] == "success"
    ap = ex["node_data"]["Manager review"][0]["json"]["approval"]
    assert ap["decision"] == "approved" and ap["comment"] == "Looks right" and ap["decided_by"] == "Dana" and ap["task_id"] == task["id"]
    assert ap["form_data"] == {"note": "Refund approved, 3-5 days"}
    assert ex["node_data"]["Manager review"][0]["json"]["structured"]["summary"] == SUMMARY      # the input item (Classify's) passes through
    assert "Auto refund" not in ex["node_data"] and "Notify requester" not in ex["node_data"]
    assert stack.q("SELECT count(*) FROM executions WHERE workflow_id = %s", (flow.wid,))[0][0] == 1       # one run, resumed in place


def test_journey_reject_takes_the_rejected_branch_and_pays_nothing(stack):
    flow, sub, task, _ = start_journey(stack)
    check_inbox_and_task(stack, flow, sub, task)
    res = decide(stack.api, task["id"], "rejected", by="Eli", comment="Photos do not match")
    assert res.status_code == 200 and res.json()["outcome"] == "decided" and res.json()["task"]["status"] == "rejected"
    done = wait_run(flow, sub)
    assert done["status"] == "success"
    assert done["result"] == {"status": "rejected", "refund_id": "", "message": "Your refund was not approved."}
    time.sleep(0.5)
    assert refunds_for(stack, sub["execution_id"]) == []
    ex = stack.api.get(f"/executions/{sub['execution_id']}").json()
    assert ex["node_data"]["Manager review"][0]["json"]["approval"]["decision"] == "rejected"
    assert "Issue refund" not in ex["node_data"] and "Notify requester" in ex["node_data"]
    assert stack.api.get("/tasks/counts", params={"project_id": flow.pid}).json()["open"] == 0
    assert [t["status"] for t in stack.api.get("/tasks", params={"project_id": flow.pid, "status": "closed"}).json()["items"]] == ["rejected"]


def test_journey_timeout_takes_the_timed_out_branch_and_a_late_approval_is_refused(stack):
    flow, sub, task, _ = start_journey(stack)
    check_inbox_and_task(stack, flow, sub, task)
    stack.tick(task_id=task["id"], make_due=True)
    done = wait_run(flow, sub)
    assert done["status"] == "success" and done["result"] == {"status": "timed_out", "refund_id": "", "message": "Review timed out"}
    t = stack.api.get(f"/tasks/{task['id']}").json()
    assert t["status"] == "timed_out" and t["decided_by"] == "system:timeout" and t["decided_at"]
    late = decide(stack.api, task["id"], "approved", by="Dana")
    assert late.status_code == 200 and late.json()["outcome"] == "already_closed" and late.json()["task"]["status"] == "timed_out"
    time.sleep(0.5)
    assert refunds_for(stack, sub["execution_id"]) == []
    assert stack.api.get("/tasks/counts", params={"project_id": flow.pid}).json()["open"] == 0
    assert [x["status"] for x in stack.api.get("/tasks", params={"project_id": flow.pid, "status": "closed"}).json()["items"]] == ["timed_out"]


def test_journey_under_the_limit_never_asks_a_person(stack):
    script_classifier(stack)
    flow = publish_flow(stack, refund_workflow(stack.refunds.url, uniq("auto"), pin=False))
    sub = submit(flow, {**GOOD_FIELDS, "amount": 300})
    done = wait_run(flow, sub)
    assert done["status"] == "success" and done["result"]["status"] == "approved" and done["result"]["message"] == "Your refund has been issued."
    assert stack.api.get("/tasks", params={"project_id": flow.pid, "status": "all"}).json()["items"] == []
    assert stack.api.get("/tasks/counts", params={"project_id": flow.pid}).json()["open"] == 0
    calls = [c for c in stack.refunds.refunds() if json.loads(c["body"] or "{}").get("amount") == 300]
    assert len(calls) >= 1
    assert exec_row(stack, sub["execution_id"])["status"] == "success"


# ── the studio surfaces report waiting too ────────────────────────────────────


def approval_workflow(name: str | None = None, webhook: bool = False) -> dict:
    first = N("Webhook", "Webhook", {"method": "POST", "responseMode": "lastNode"}) if webhook else N("Start", "Start")
    return {"name": name or uniq("appr"), "settings": {}, "nodes": [
        first, N("Review", "Approval", {"title": "Check {{ $json.body.order }}"}, 200),
        N("Paid", "Set", {"mode": "manual", "keepOnlySet": True, "fields": [{"name": "outcome", "value": "paid"}]}, 400),
    ], "connections": [C(first["name"], "Review"), C("Review", "Paid", "approved")]}


def test_a_streamed_studio_run_ends_with_execution_waiting_and_its_task(stack):
    pid, _ = new_project(stack.api)
    wid = stack.api.post(f"/projects/{pid}/workflows", approval_workflow()).json()["id"]
    events = stack.api.stream_events(f"/execution-stream/{wid}", {"input_data": {"order": "A-7"}, "environment": "default"})
    types = [e["type"] for e in events]
    assert types[-1] == "execution_waiting" and "execution:complete" not in types and "execution:error" not in types, types
    last = events[-1]
    tasks = stack.api.get("/tasks", params={"project_id": pid, "status": "open"}).json()["items"]
    assert len(tasks) == 1 and [t["id"] for t in last["tasks"]] == [tasks[0]["id"]]
    assert last["tasks"][0]["title"] == "Check A-7" and last["tasks"][0]["node_name"] == "Review"
    assert last["executionId"] == tasks[0]["execution_id"]
    assert exec_row(stack, last["executionId"])["status"] == "waiting"
    assert sum(t == "execution_waiting" for t in types) == 1
    # the streamed run still resumes through the same decide endpoint
    decide(stack.api, tasks[0]["id"], "approved")
    done = poll_until(lambda: stack.api.get(f"/executions/{last['executionId']}").json(), lambda e: e["status"] in ("success", "failed"), what="resume")
    assert done["status"] == "success" and done["node_data"]["Paid"][0]["json"]["outcome"] == "paid"


def test_a_run_that_does_not_pause_still_streams_execution_complete(stack):
    pid, _ = new_project(stack.api)
    wf = {"name": uniq("plain"), "settings": {}, "nodes": [N("Start", "Start"), N("Set", "Set", {"mode": "manual", "fields": [{"name": "a", "value": "1"}]}, 200)],
          "connections": [C("Start", "Set")]}
    wid = stack.api.post(f"/projects/{pid}/workflows", wf).json()["id"]
    types = [e["type"] for e in stack.api.stream_events(f"/execution-stream/{wid}", {"input_data": None, "environment": "default"})]
    assert types[-1] == "execution:complete" and "execution_waiting" not in types


def test_run_endpoint_and_webhook_report_waiting_not_success(stack):
    pid, _ = new_project(stack.api)
    wid = stack.api.post(f"/projects/{pid}/workflows", approval_workflow()).json()["id"]
    r = stack.api.post(f"/workflows/{wid}/run", {"input_data": {"order": "A-8"}})
    assert r.status_code == 200 and r.json()["status"] == "waiting", r.text
    exec_id = r.json()["execution_id"]
    assert stack.api.get(f"/executions/{exec_id}").json()["status"] == "waiting"
    # listing executions shows it as waiting too, not running or success
    listed = stack.api.get(f"/executions?workflow_id={wid}").json()
    rows = listed if isinstance(listed, list) else listed.get("items", listed)
    assert [e["status"] for e in rows if e["id"] == exec_id] == ["waiting"]

    hook = stack.api.post("/workflows", {**approval_workflow(webhook=True), "name": uniq("hook")}).json()["id"]
    assert stack.api.post(f"/workflows/{hook}/publish", {}).status_code == 200
    w = stack.api.raw("POST", f"/webhook/{hook}", json={"order": "A-9"})
    assert w.status_code == 200 and w.json()["status"] == "waiting" and w.json()["executionId"].startswith("exec_"), w.text
    assert exec_row(stack, w.json()["executionId"])["status"] == "waiting"
    assert stack.api.get("/tasks/counts").json()["open"] >= 2


def test_cancelling_a_waiting_run_over_http_closes_the_task_and_blocks_a_late_decision(stack):
    pid, _ = new_project(stack.api)
    wid = stack.api.post(f"/projects/{pid}/workflows", approval_workflow()).json()["id"]
    exec_id = stack.api.post(f"/workflows/{wid}/run", {"input_data": {"order": "A-1"}}).json()["execution_id"]
    task = poll_until(lambda: stack.api.get("/tasks", params={"project_id": pid}).json()["items"], lambda i: len(i) == 1, what="task")[0]
    assert stack.api.post(f"/executions/{exec_id}/cancel").status_code == 200
    assert exec_row(stack, exec_id)["status"] == "cancelled" and task_row(stack, task["id"])["status"] == "cancelled"
    assert stack.api.get("/tasks/counts", params={"project_id": pid}).json()["open"] == 0
    assert decide(stack.api, task["id"], "approved").json()["outcome"] == "already_closed"
    stack.tick()
    time.sleep(0.5)
    assert exec_row(stack, exec_id)["status"] == "cancelled"                        # nothing resurrects it


# ── inbox: keyset paging, filters, counts ─────────────────────────────────────


def test_inbox_keyset_paging_over_60_tasks_has_no_duplicates_or_gaps_and_survives_new_arrivals(stack):
    pid, _ = new_project(stack.api)
    wf_a = stack.api.post(f"/projects/{pid}/workflows", approval_workflow()).json()["id"]
    wf_b_def = approval_workflow()
    wf_b_def["nodes"][1]["parameters"]["assignees"] = ["dana@bank.example"]
    wf_b = stack.api.post(f"/projects/{pid}/workflows", wf_b_def).json()["id"]
    ids: dict[str, str] = {}
    for i in range(60):
        wid = wf_b if i % 3 == 0 else wf_a
        ex = stack.api.post(f"/workflows/{wid}/run", {"input_data": {"order": f"O-{i}"}}).json()["execution_id"]
        ids[ex] = wid
    expected = [r[0] for r in stack.q("SELECT id FROM human_tasks WHERE project_id = %s ORDER BY created_at DESC, id DESC", (pid,))]
    assert len(expected) == 60

    def page_through(limit: int, **params) -> list[str]:
        out, cursor, pages = [], None, 0
        while True:
            q = {"project_id": pid, "status": "all", "limit": limit, **params, **({"cursor": cursor} if cursor else {})}
            r = stack.api.get("/tasks", params=q)
            assert r.status_code == 200, r.text
            body = r.json()
            assert set(body) == {"items", "next_cursor"} and len(body["items"]) <= limit
            out += [t["id"] for t in body["items"]]
            pages += 1
            cursor = body["next_cursor"]
            if not cursor:
                return out
            assert body["items"], "an empty page must end the listing"
            assert pages < 100

    for limit in (25, 7, 1, 100):
        got = page_through(limit)
        assert got == expected, f"limit={limit}: {len(got)} ids, {len(set(got))} unique"
    default_page = stack.api.get("/tasks", params={"project_id": pid, "status": "all"}).json()
    assert len(default_page["items"]) == 25 and default_page["next_cursor"]            # default limit 25

    # a task arriving between page 1 and page 2 neither duplicates nor drops an older one
    first = stack.api.get("/tasks", params={"project_id": pid, "status": "all", "limit": 30}).json()
    stack.api.post(f"/workflows/{wf_a}/run", {"input_data": {"order": "NEW"}})
    rest = stack.api.get("/tasks", params={"project_id": pid, "status": "all", "limit": 100, "cursor": first["next_cursor"]}).json()
    assert [t["id"] for t in first["items"]] + [t["id"] for t in rest["items"]] == expected

    # filters
    by_wf = page_through(10, workflow_id=wf_b)
    assert len(by_wf) == 20 and set(by_wf) <= set(expected)
    assert len(page_through(10, workflow_id=wf_a)) == 41                                 # 40 + the late arrival
    assert len(page_through(10, assignee="dana@bank.example")) == 20
    assert page_through(10, assignee="nobody@bank.example") == []
    assert len(page_through(25, status="open")) == 61 and page_through(25, status="closed") == []
    counts = stack.api.get("/tasks/counts", params={"project_id": pid}).json()
    assert counts == {"open": 61, "by_workflow": {wf_a: 41, wf_b: 20}}
    other_pid, _ = new_project(stack.api)
    assert stack.api.get("/tasks/counts", params={"project_id": other_pid}).json() == {"open": 0, "by_workflow": {}}
    assert stack.api.get("/tasks", params={"project_id": other_pid, "status": "all"}).json()["items"] == []

    # closing some moves them from open to closed without disturbing the order
    some = expected[:5]
    for t in some:
        assert decide(stack.api, t, "rejected", by="Eli").json()["outcome"] == "decided"
    assert sorted(page_through(10, status="closed")) == sorted(some)
    assert len(page_through(10, status="open")) == 56
    assert stack.api.get("/tasks/counts", params={"project_id": pid}).json()["open"] == 56

    # bad paging parameters are refused, not guessed at
    for bad in ({"limit": 0}, {"limit": 101}, {"limit": -1}, {"limit": "x"}, {"status": "bogus"}, {"cursor": "not-a-cursor"},
                {"cursor": base64.urlsafe_b64encode(b"garbage").decode()}):
        r = stack.api.get("/tasks", params={"project_id": pid, **bad})
        assert r.status_code == 422, (bad, r.status_code, r.text)


# ── the typed client compiles ─────────────────────────────────────────────────

TSC_CANDIDATES = [Path(__file__).resolve().parents[4] / "apps/workflow-studio/node_modules/.bin/tsc",
                  Path(__file__).resolve().parents[4] / "apps/analytics-service/web/node_modules/.bin/tsc"]


def _tsc() -> str | None:
    return next((str(p) for p in TSC_CANDIDATES if p.exists()), shutil.which("tsc"))


def run_tsc(tsc: str, directory: Path) -> subprocess.CompletedProcess:
    (directory / "tsconfig.json").write_text(json.dumps({"compilerOptions": {
        "target": "ES2020", "module": "ESNext", "moduleResolution": "bundler", "strict": True, "noEmit": True, "lib": ["ES2020", "DOM"],
        "skipLibCheck": True, "types": []}, "include": ["*.ts"]}))
    return subprocess.run([tsc, "-p", str(directory)], capture_output=True, text=True, timeout=180, cwd=str(directory))


def test_the_generated_typed_client_compiles_and_a_wrong_consumer_does_not(stack, tmp_path):
    tsc = _tsc()
    if tsc is None:
        pytest.skip("no tsc binary available")
    flow = refund_flow(stack)
    r = stack.api.get(f"/workflows/{flow.wid}/interface/client.ts")
    assert r.status_code == 200 and r.text == stack.api.get(f"/workflows/{flow.wid}/interface/client.ts").text
    for leak in ("Classify", "Issue refund", stack.refunds.url, "REFUND_CLASSIFY", "password"):
        assert leak not in r.text
    (tmp_path / "client.ts").write_text(r.text)
    (tmp_path / "good.ts").write_text(
        'import { createClient } from "./client";\n'
        "export async function go() {\n"
        '  const c = createClient("http://x");\n'
        '  const run = await c.startRun({ amount: 5, order_id: "A-12345", reason: "damaged", email: "a@b.co" });\n'
        "  const view = await c.getRun(run.execution_id, run.run_token);\n"
        "  const status: string = view.status;\n"
        "  return status;\n"
        "}\n")
    ok = run_tsc(tsc, tmp_path)
    assert ok.returncode == 0, ok.stdout + ok.stderr
    (tmp_path / "bad.ts").write_text(
        'import { createClient } from "./client";\n'
        'export const c = createClient("http://x");\n'
        'c.startRun({ amount: "five", order_id: 1, reason: "nope" });\n'
        "export const n: number = 'not a number' as string;\n")
    bad = run_tsc(tsc, tmp_path)
    assert bad.returncode != 0 and "bad.ts" in bad.stdout                              # the types are real: a wrong call fails to compile
