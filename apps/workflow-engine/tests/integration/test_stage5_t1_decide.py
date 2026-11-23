"""Stage 5 T1: decide semantics, races and timeouts over HTTP on real Postgres (DESIGN-STAGE5-UIS 5.4, 5.6, 7.1, 8.2, 10.2).

Written from the contract. The money rule under test: one decision, one resume, at most one refund.
"""

from __future__ import annotations

import json
import time
import uuid

import pytest

from .conftest import uniq
from .test_stage5_t1_support import (  # noqa: F401  (fixtures + helpers)
    C, GOOD_FIELDS, N, Stack, concurrently, decide, exec_row, fresh_stack, new_project, poll_until, publish_flow, refund_flow,
    run_view, stack, submit, submit_waiting, task_row, wait_run, wait_task,
)

pytestmark = pytest.mark.integration


def refunds_of(stack: Stack, *exec_ids: str) -> list[dict]:
    return [c for c in stack.refunds.refunds() if any((c["key"] or "").startswith(e + ":") for e in exec_ids)]


def wait_closed(flow, sub, timeout=30) -> dict:
    return wait_run(flow, sub, until=("success", "failed", "cancelled"), timeout=timeout)


# ── double click / two approvers ──────────────────────────────────────────────


def test_two_concurrent_decides_with_different_keys_one_decided_one_already_decided(stack):
    flow = refund_flow(stack)
    sub, task = submit_waiting(flow)
    res = concurrently(2, lambda i: decide(flow.api, task["id"], "approved", by=["Dana", "Eli"][i], key=f"k-{i}-{uuid.uuid4().hex}"))
    assert [r.status_code for r in res] == [200, 200], [r.text for r in res]
    outcomes = sorted(r.json()["outcome"] for r in res)
    assert outcomes == ["already_decided", "decided"]
    winner = next(r.json() for r in res if r.json()["outcome"] == "decided")
    loser = next(r.json() for r in res if r.json()["outcome"] == "already_decided")
    assert loser["task"]["decided_by"] == winner["task"]["decided_by"] and loser["task"]["status"] == "approved"

    done = wait_closed(flow, sub)
    assert done["status"] == "success" and done["result"]["status"] == "approved"
    calls = refunds_of(stack, sub["execution_id"])
    assert len(calls) == 1, calls                       # exactly one downstream run
    row = task_row(stack, task["id"])
    assert row["decided_by"] == winner["task"]["decided_by"] and row["resume_state"] == "done"


def test_ten_approvers_with_opposite_decisions_exactly_one_wins_and_the_run_follows_the_winner(stack):
    flow = refund_flow(stack)
    sub, task = submit_waiting(flow)
    res = concurrently(10, lambda i: decide(flow.api, task["id"], ["approved", "rejected"][i % 2], by=f"user{i}"))
    assert all(r.status_code == 200 for r in res), [r.text for r in res]
    winners = [r.json() for r in res if r.json()["outcome"] == "decided"]
    assert len(winners) == 1 and sum(r.json()["outcome"] == "already_decided" for r in res) == 9
    won = winners[0]["task"]
    assert {r.json()["task"]["decided_by"] for r in res} == {won["decided_by"]}      # every loser sees the same winner
    done = wait_closed(flow, sub)
    assert done["status"] == "success"
    paid = refunds_of(stack, sub["execution_id"])
    if won["status"] == "approved":
        assert done["result"]["status"] == "approved" and len(paid) == 1
    else:
        assert done["result"]["status"] == "rejected" and paid == []


def test_same_idempotency_key_concurrently_is_a_retry_of_one_click(stack):
    flow = refund_flow(stack)
    sub, task = submit_waiting(flow)
    key = "double-click-" + uuid.uuid4().hex
    res = concurrently(4, lambda i: decide(flow.api, task["id"], "approved", by="Dana", key=key))
    assert all(r.status_code == 200 and r.json()["outcome"] == "decided" for r in res), [r.text for r in res]
    assert wait_closed(flow, sub)["status"] == "success"
    assert len(refunds_of(stack, sub["execution_id"])) == 1
    # a late replay of the same click is still "decided", with the same task body
    again = decide(flow.api, task["id"], "approved", by="Dana", key=key)
    assert again.json()["outcome"] == "decided" and again.json()["task"]["status"] == "approved"
    # a different key after the fact is told who won
    other = decide(flow.api, task["id"], "rejected", by="Eli")
    assert other.json()["outcome"] == "already_decided" and other.json()["task"]["decided_by"] == "Dana"
    assert len(refunds_of(stack, sub["execution_id"])) == 1


def test_decide_replay_after_the_run_finished_never_pays_again(stack):
    flow = refund_flow(stack)
    sub, task = submit_waiting(flow)
    assert decide(flow.api, task["id"], "approved", key="once").json()["outcome"] == "decided"
    assert wait_closed(flow, sub)["status"] == "success"
    for i in range(3):
        assert decide(flow.api, task["id"], "approved", key="once").json()["outcome"] == "decided"
        assert decide(flow.api, task["id"], "approved", key=f"again-{i}").json()["outcome"] == "already_decided"
    time.sleep(1.0)
    assert len(refunds_of(stack, sub["execution_id"])) == 1


def test_a_double_submitted_form_creates_one_run_and_one_task(stack):
    flow = refund_flow(stack)
    key = "form-" + uuid.uuid4().hex
    res = concurrently(6, lambda i: flow.api.post(f"{flow.base}/submit", {"fields": dict(GOOD_FIELDS)}, headers={"Idempotency-Key": key}))
    assert [r.status_code for r in res] == [202] * 6, [r.text for r in res]
    assert len({r.json()["execution_id"] for r in res}) == 1 and len({r.json()["submission_id"] for r in res}) == 1
    exec_id = res[0].json()["execution_id"]
    poll_until(lambda: flow.api.get("/tasks", params={"project_id": flow.pid, "status": "all"}).json()["items"], lambda i: len(i) >= 1, what="a task")
    time.sleep(0.5)
    items = flow.api.get("/tasks", params={"project_id": flow.pid, "status": "all"}).json()["items"]
    assert [t["execution_id"] for t in items] == [exec_id]
    runs = flow.api.get(f"/executions?workflow_id={flow.wid}").json()
    assert len(runs if isinstance(runs, list) else runs.get("items", runs)) == 1
    assert stack.q("SELECT count(*) FROM interface_submissions WHERE workflow_id = %s", (flow.wid,))[0][0] == 1
    # the same key with a different payload is still the first submission (the key wins, the payload is not re-run)
    again = flow.api.post(f"{flow.base}/submit", {"fields": {**GOOD_FIELDS, "amount": 9999}}, headers={"Idempotency-Key": key})
    assert again.status_code == 202 and again.json()["execution_id"] == exec_id


def test_a_new_key_is_a_new_submission(stack):
    flow = refund_flow(stack)
    a, b = submit(flow), submit(flow)
    assert a["execution_id"] != b["execution_id"] and a["submission_id"] != b["submission_id"]
    wait_task(flow, a), wait_task(flow, b)


# ── decide validation ─────────────────────────────────────────────────────────

REQUIRED_FORM = [
    {"name": "risk", "label": "Risk", "type": "select", "required": True, "options": ["low", "high"]},
    {"name": "paid", "label": "Paid", "type": "checkbox"},
    {"name": "limit", "label": "Limit", "type": "number"},
    {"name": "why", "label": "Why", "type": "text", "required": True},
]


def test_form_data_is_validated_and_a_bad_decision_does_not_consume_the_task(stack):
    flow = refund_flow(stack, form=REQUIRED_FORM, require_comment_on_reject=True)
    sub, task = submit_waiting(flow)
    t = task["id"]
    ok = {"risk": "low", "why": "fine"}

    def fields(r):
        assert r.status_code == 422, r.text
        return r.json()["detail"]["fields"]

    assert set(fields(decide(flow.api, t, "approved", form_data={}))) >= {"risk", "why"}                     # required
    assert "risk" in fields(decide(flow.api, t, "approved", form_data={**ok, "risk": "medium"}))             # not in options
    assert "limit" in fields(decide(flow.api, t, "approved", form_data={**ok, "limit": "ten"}))              # type
    assert "limit" in fields(decide(flow.api, t, "approved", form_data={**ok, "limit": True}))               # bool is not a number
    assert "paid" in fields(decide(flow.api, t, "approved", form_data={**ok, "paid": "yes"}))                # checkbox is boolean
    assert "bogus" in fields(decide(flow.api, t, "approved", form_data={**ok, "bogus": 1}))                  # unknown field
    assert "why" in fields(decide(flow.api, t, "approved", form_data={**ok, "why": "x" * 4001}))             # too long
    assert "comment" in fields(decide(flow.api, t, "rejected", form_data=ok))                                # comment required to reject
    assert "comment" in fields(decide(flow.api, t, "rejected", form_data=ok, comment="   "))                 # whitespace is no comment
    blank = decide(flow.api, t, "approved", by="   ", form_data=ok)
    assert blank.status_code == 422 and "decided_by" in blank.json()["detail"]["fields"]
    assert task_row(stack, t)["status"] == "open"                    # none of that decided it
    assert flow.api.get("/tasks/counts", params={"project_id": flow.pid}).json()["open"] == 1

    good = decide(flow.api, t, "approved", by="  Dana  ", form_data={**ok, "paid": False, "limit": 5.5}, comment="ok")
    assert good.status_code == 200 and good.json()["outcome"] == "decided"
    assert good.json()["task"]["decided_by"] == "Dana"                # trimmed
    done = wait_closed(flow, sub)
    assert done["status"] == "success"
    node = flow.api.get(f"/executions/{sub['execution_id']}").json()["node_data"]["Manager review"][0]["json"]["approval"]
    assert node["decision"] == "approved" and node["form_data"]["risk"] == "low" and node["comment"] == "ok" and node["decided_by"] == "Dana"
    assert node["task_id"] == t


def test_decided_by_over_200_chars_is_refused_not_silently_truncated(stack):
    """5.4: decided_by is 1-200 chars. 201 must be a 422, and must not decide the task with a cut-off name."""
    flow = refund_flow(stack)
    sub, task = submit_waiting(flow)
    r = decide(flow.api, task["id"], "approved", by="x" * 201)
    assert r.status_code == 422 and "decided_by" in r.json()["detail"]["fields"], r.text
    assert task_row(stack, task["id"])["status"] == "open"
    ok = decide(flow.api, task["id"], "approved", by="y" * 200)
    assert ok.status_code == 200 and ok.json()["task"]["decided_by"] == "y" * 200
    wait_closed(flow, sub)


def test_decide_envelope_errors(stack):
    flow = refund_flow(stack)
    sub, task = submit_waiting(flow)
    t = task["id"]
    h = {"Idempotency-Key": "e1"}
    body = {"decision": "approved", "decided_by": "Dana"}
    assert flow.api.post(f"/tasks/{t}/decide", body).status_code == 422                                # no key anywhere
    assert decide(flow.api, t, "approved", key="k" * 129).status_code == 422                          # key over 128
    assert flow.api.post(f"/tasks/{t}/decide", {**body, "decision": "approve"}, headers=h).status_code == 422
    assert flow.api.post(f"/tasks/{t}/decide", {**body, "extra": 1}, headers=h).status_code == 422     # strict body
    big = flow.api.post(f"/tasks/{t}/decide", {**body, "comment": "x" * 40000}, headers=h)
    assert big.status_code == 413 and big.json()["detail"]["code"] == "PAYLOAD_TOO_LARGE"           # 32 KB cap
    assert flow.api.post("/tasks/tsk_nope/decide", body, headers=h).status_code == 404
    assert flow.api.get("/tasks/tsk_nope").status_code == 404
    # R12: a proxy stripped the header, so the key may travel in the body
    viabody = flow.api.post(f"/tasks/{t}/decide", {**body, "idempotency_key": "from-body"})
    assert viabody.status_code == 200 and viabody.json()["outcome"] == "decided"
    assert task_row(stack, t)["decide_key"] == "from-body"
    wait_closed(flow, sub)


def test_the_header_key_wins_over_the_body_key(stack):
    flow = refund_flow(stack)
    sub, task = submit_waiting(flow)
    r = flow.api.post(f"/tasks/{task['id']}/decide", {"decision": "approved", "decided_by": "Dana", "idempotency_key": "body"},
                      headers={"Idempotency-Key": "header"})
    assert r.status_code == 200 and task_row(stack, task["id"])["decide_key"] == "header"
    wait_closed(flow, sub)


# ── timeouts ──────────────────────────────────────────────────────────────────


def test_timeout_takes_the_timed_out_port_and_a_late_decide_is_already_closed(stack):
    flow = refund_flow(stack, on_timeout="timed_out")
    sub, task = submit_waiting(flow)
    assert task["due_at"] is not None
    stack.tick()                                               # nothing is due: nothing happens
    assert task_row(stack, task["id"])["status"] == "open"
    stack.tick(task_id=task["id"], make_due=True)
    done = wait_closed(flow, sub)
    assert done["status"] == "success" and done["result"] == {"status": "timed_out", "refund_id": "", "message": "Review timed out"}
    row = task_row(stack, task["id"])
    assert row["status"] == "timed_out" and row["decided_by"] == "system:timeout" and row["resume_state"] == "done"
    late = decide(flow.api, task["id"], "approved", by="Dana")
    assert late.status_code == 200 and late.json()["outcome"] == "already_closed"
    assert late.json()["task"]["status"] == "timed_out"
    time.sleep(0.5)
    assert refunds_of(stack, sub["execution_id"]) == []
    node = flow.api.get(f"/executions/{sub['execution_id']}").json()["node_data"]["Manager review"][0]["json"]["approval"]
    assert node["decision"] == "timed_out" and node["decided_by"] == "system:timeout"


def test_on_timeout_approve_pays_once_and_a_late_reject_changes_nothing(stack):
    flow = refund_flow(stack, on_timeout="approve")
    sub, task = submit_waiting(flow)
    stack.tick(task_id=task["id"], make_due=True)
    done = wait_closed(flow, sub)
    assert done["status"] == "success" and done["result"]["status"] == "approved"
    assert len(refunds_of(stack, sub["execution_id"])) == 1
    row = task_row(stack, task["id"])
    assert row["status"] == "approved" and row["decided_by"] == "system:timeout"
    late = decide(flow.api, task["id"], "rejected", by="Eli", comment="too late")
    assert late.json()["outcome"] in ("already_decided", "already_closed") and late.json()["task"]["status"] == "approved"
    stack.tick()
    time.sleep(0.5)
    assert len(refunds_of(stack, sub["execution_id"])) == 1


def test_on_timeout_reject_takes_the_rejected_branch(stack):
    flow = refund_flow(stack, on_timeout="reject")
    sub, task = submit_waiting(flow)
    stack.tick(task_id=task["id"], make_due=True)
    done = wait_closed(flow, sub)
    assert done["status"] == "success" and done["result"]["status"] == "rejected"
    assert refunds_of(stack, sub["execution_id"]) == []
    late = decide(flow.api, task["id"], "approved", by="Dana")
    assert late.json()["outcome"] in ("already_decided", "already_closed") and late.json()["task"]["status"] == "rejected"
    time.sleep(0.5)
    assert refunds_of(stack, sub["execution_id"]) == []


def test_a_task_without_a_deadline_never_times_out(stack):
    flow = refund_flow(stack, due_in=None)
    sub, task = submit_waiting(flow)
    assert task["due_at"] is None
    stack.tick()
    assert task_row(stack, task["id"])["status"] == "open"
    assert run_view(flow, sub)["status"] == "waiting"
    decide(flow.api, task["id"], "rejected")
    wait_closed(flow, sub)


def test_the_deadline_matches_due_in(stack):
    flow = refund_flow(stack, due_in="3h")
    sub, task = submit_waiting(flow)
    delta = stack.q("SELECT extract(epoch FROM (due_at - created_at)) FROM human_tasks WHERE id = %s", (task["id"],))[0][0]
    assert abs(float(delta) - 3 * 3600) < 120, delta
    decide(flow.api, task["id"], "rejected")
    wait_closed(flow, sub)


@pytest.mark.parametrize("round_", range(5))
def test_timeout_racing_a_decision_has_exactly_one_winner_and_one_downstream_run(stack, round_):
    flow = refund_flow(stack, on_timeout="timed_out")
    sub, task = submit_waiting(flow)
    stack.q("UPDATE human_tasks SET due_at = now() - interval '1 second' WHERE id = %s", (task["id"],))

    def go(i: int):
        if i == 0:
            return ("tick", stack.hook("POST", "/tasks/tick", {"wait": True}))
        return ("decide", decide(flow.api, task["id"], "approved", by="Dana"))

    res = dict(concurrently(2, go))
    assert res["tick"].status_code == 200 and res["decide"].status_code == 200, (res["tick"].text, res["decide"].text)
    outcome = res["decide"].json()["outcome"]
    done = wait_closed(flow, sub)
    assert done["status"] == "success"
    paid = refunds_of(stack, sub["execution_id"])
    row = task_row(stack, task["id"])
    if outcome == "decided":
        assert row["status"] == "approved" and done["result"]["status"] == "approved" and len(paid) == 1
    else:
        assert outcome == "already_closed" and row["status"] == "timed_out", (outcome, row["status"])
        assert done["result"]["status"] == "timed_out" and paid == []
    assert row["resume_state"] == "done"


def test_the_real_scheduler_times_tasks_out_without_any_hook(stack):
    """The 30 s CronScheduler tick must itself run the task pass (the hook calls task_resumer directly)."""
    flow = refund_flow(stack, on_timeout="timed_out")
    sub, task = submit_waiting(flow)
    stack.q("UPDATE human_tasks SET due_at = now() - interval '1 second' WHERE id = %s", (task["id"],))
    done = wait_run(flow, sub, timeout=80)
    assert done["status"] == "success" and done["result"]["status"] == "timed_out"


def test_two_engines_ticking_at_once_resume_every_due_task_exactly_once(stack):
    """SKIP LOCKED across instances: 10 due tasks, two engines run the pass together; each task resumes once, so 10 refunds."""
    second = stack.add_engine("peer")
    flow = refund_flow(stack, on_timeout="approve")
    subs = [submit(flow, {**GOOD_FIELDS, "order_id": f"A-{20000 + i}"}) for i in range(10)]
    tasks = [wait_task(flow, s) for s in subs]
    ids = [t["id"] for t in tasks]
    stack.q("UPDATE human_tasks SET due_at = now() - interval '1 second' WHERE id = ANY(%s)", (ids,))
    res = concurrently(2, lambda i: stack.hook("POST", "/tasks/tick", {"wait": True}, srv=[stack.engines[0], second][i]))
    assert [r.status_code for r in res] == [200, 200], [r.text for r in res]
    for s in subs:
        assert wait_closed(flow, s, timeout=60)["status"] == "success"
    paid = refunds_of(stack, *(s["execution_id"] for s in subs))
    assert len(paid) == 10, f"{len(paid)} refunds for 10 tasks"
    assert len({c["key"] for c in paid}) == 10                       # one per execution, none twice
    for t in ids:
        r = task_row(stack, t)
        assert r["status"] == "approved" and r["decided_by"] == "system:timeout" and r["resume_state"] == "done"
    assert stack.q("SELECT count(*) FROM executions WHERE id = ANY(%s) AND metadata->>'error_code' IS NOT NULL", ([s["execution_id"] for s in subs],))[0][0] == 0


def test_two_engines_deciding_the_same_task_at_once_one_wins(stack):
    second = stack.add_engine("peer2")
    flow = refund_flow(stack)
    sub, task = submit_waiting(flow)
    res = concurrently(2, lambda i: decide(stack.client([stack.engines[0], second][i]), task["id"], "approved", by=["Dana", "Eli"][i]))
    assert sorted(r.json()["outcome"] for r in res) == ["already_decided", "decided"], [r.text for r in res]
    assert wait_closed(flow, sub)["status"] == "success"
    stack.tick()
    stack.tick(srv=second)
    time.sleep(0.5)
    assert len(refunds_of(stack, sub["execution_id"])) == 1


# ── parallel approvals in one run (5.5, R10) ──────────────────────────────────


def parallel_workflow(name: str | None = None) -> dict:
    def appr(n, title):
        return N(n, "Approval", {"title": title, "context": {"Who": n}}, 200)

    def mark(n, v):
        return N(n, "Set", {"mode": "manual", "keepOnlySet": True, "fields": [{"name": "branch", "value": v}]}, 400)

    return {"name": name or uniq("parallel"), "settings": {}, "nodes": [
        N("Start", "Start"), appr("Legal", "Legal review"), appr("Risk", "Risk review"),
        mark("Legal ok", "legal-ok"), mark("Legal no", "legal-no"), mark("Risk ok", "risk-ok"), mark("Risk no", "risk-no"),
    ], "connections": [C("Start", "Legal"), C("Start", "Risk"), C("Legal", "Legal ok", "approved"), C("Legal", "Legal no", "rejected"),
                       C("Risk", "Risk ok", "approved"), C("Risk", "Risk no", "rejected")]}


def run_parallel(stack):
    pid, _ = new_project(stack.api)
    wid = stack.api.post(f"/projects/{pid}/workflows", parallel_workflow()).json()["id"]
    exec_id = stack.api.post(f"/workflows/{wid}/run", {"input_data": {"x": 1}}).json()["execution_id"]
    tasks = poll_until(lambda: [t for t in stack.api.get("/tasks", params={"project_id": pid, "status": "open"}).json()["items"]
                                if t["execution_id"] == exec_id], lambda ts: len(ts) == 2, what="two tasks")
    return pid, exec_id, {t["node_name"]: t for t in tasks}


def exec_api(stack, exec_id):
    return stack.api.get(f"/executions/{exec_id}").json()


def test_parallel_approvals_the_run_stays_waiting_until_the_last_one_is_decided(stack):
    pid, exec_id, tasks = run_parallel(stack)
    assert exec_row(stack, exec_id)["status"] == "waiting"
    assert decide(stack.api, tasks["Legal"]["id"], "approved").json()["outcome"] == "decided"
    time.sleep(2.0)
    poll_until(lambda: task_row(stack, tasks["Legal"]["id"])["resume_state"], lambda s: s == "done", what="first resume done")
    assert exec_row(stack, exec_id)["status"] == "waiting"          # the other task is still open
    mid = exec_api(stack, exec_id)
    assert mid["status"] == "waiting" and "Legal ok" in mid["node_data"] and "Risk ok" not in mid["node_data"]
    assert stack.api.get("/tasks/counts", params={"project_id": pid}).json()["open"] == 1
    assert decide(stack.api, tasks["Risk"]["id"], "rejected").json()["outcome"] == "decided"
    final = poll_until(lambda: exec_api(stack, exec_id), lambda e: e["status"] in ("success", "failed"), what="final status")
    assert final["status"] == "success"
    assert final["node_data"]["Legal ok"][0]["json"]["branch"] == "legal-ok"       # outputs of the first resume survived the second
    assert final["node_data"]["Risk no"][0]["json"]["branch"] == "risk-no"
    assert "Legal no" not in final["node_data"] and "Risk ok" not in final["node_data"]


def test_parallel_approvals_decided_at_the_same_moment_both_branches_run_and_the_run_succeeds(stack):
    pid, exec_id, tasks = run_parallel(stack)
    res = concurrently(2, lambda i: decide(stack.api, [tasks["Legal"], tasks["Risk"]][i]["id"], "approved", by="Dana"))
    assert all(r.json()["outcome"] == "decided" for r in res), [r.text for r in res]
    # the second resume may have to wait for the first (execution busy) and be picked up by the 30 s sweep: allow it
    final = poll_until(lambda: exec_api(stack, exec_id), lambda e: e["status"] in ("success", "failed"), timeout=90, every=0.5, what="final status")
    assert final["status"] == "success", final
    assert "Legal ok" in final["node_data"] and "Risk ok" in final["node_data"]
    assert [r for r in stack.q("SELECT resume_state FROM human_tasks WHERE execution_id = %s", (exec_id,))] == [("done",), ("done",)]
