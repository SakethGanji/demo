"""Stage 5 T1: durability. The engine is a real subprocess that is killed with SIGKILL (DESIGN-STAGE5-UIS 5.5, 8.3, 9.2, 10.2).

The rules under test: a waiting run survives a crash and still resumes; a resume that was running when the process died
is failed loudly (RESUME_INTERRUPTED) and is NEVER re-run, so a refund is never paid twice; a decision nobody claimed
is resumed by the sweep; waiting runs are never pruned; a run keeps the definition it started on.
"""

from __future__ import annotations

import time

import pytest

from .conftest import uniq
from .test_stage5_t1_support import (  # noqa: F401  (fixtures + helpers)
    C, N, GOOD_FIELDS, Stack, decide, exec_row, fresh_stack, new_project, poll_until, refund_flow, run_view, stack, submit,
    submit_waiting, task_row, wait_run, wait_task,
)

pytestmark = pytest.mark.integration

STALE = "UPDATE human_tasks SET resume_heartbeat = now() - interval '10 minutes' WHERE resume_state = 'running'"


def refunds_of(stack: Stack, exec_id: str) -> list[dict]:
    return [c for c in stack.refunds.refunds() if (c["key"] or "").startswith(exec_id + ":")]


# ── a waiting run survives a crash ────────────────────────────────────────────


def test_a_waiting_run_survives_kill_9_and_still_resumes_once(fresh_stack):
    s = fresh_stack
    flow = refund_flow(s)
    sub, task = submit_waiting(flow)
    before = s.api.get(f"/tasks/{task['id']}").json()
    s.crash()
    s.restart()
    flow.stack = s  # the flow talks to the new process

    after = s.api.get(f"/tasks/{task['id']}")
    assert after.status_code == 200 and after.json()["status"] == "open" and after.json()["title"] == before["title"]
    assert after.json()["context"] == before["context"] and after.json()["due_at"] == before["due_at"]
    assert s.api.get("/tasks/counts", params={"project_id": flow.pid}).json() == {"open": 1, "by_workflow": {flow.wid: 1}}
    assert exec_row(s, sub["execution_id"])["status"] == "waiting"
    view = run_view(flow, sub)                                  # the old run token still works after a restart
    assert view["status"] == "waiting" and [w["task_id"] for w in view["waiting_for"]] == [task["id"]]
    s.tick()                                                    # a sweep does not touch an open task
    assert task_row(s, task["id"])["status"] == "open" and run_view(flow, sub)["status"] == "waiting"

    won = decide(s.api, task["id"], "approved", by="Dana", comment="after the restart")
    assert won.status_code == 200 and won.json()["outcome"] == "decided"
    done = wait_run(flow, sub)
    assert done["status"] == "success" and done["result"] == {"status": "approved", "refund_id": done["result"]["refund_id"], "message": "Your refund has been issued."}
    calls = refunds_of(s, sub["execution_id"])
    assert len(calls) == 1 and calls[0]["key"] == f"{sub['execution_id']}:Manager review:0"        # the resume key, 5.5
    assert task_row(s, task["id"])["resume_state"] == "done"


def test_a_waiting_run_survives_two_restarts_and_a_timeout_still_fires_after_them(fresh_stack):
    s = fresh_stack
    flow = refund_flow(s, on_timeout="timed_out")
    sub, task = submit_waiting(flow)
    for _ in range(2):
        s.crash()
        s.restart()
    assert s.api.get(f"/tasks/{task['id']}").json()["status"] == "open"
    s.tick(task_id=task["id"], make_due=True)
    done = wait_run(flow, sub)
    assert done["status"] == "success" and done["result"]["status"] == "timed_out"
    assert refunds_of(s, sub["execution_id"]) == []


def test_a_task_decided_while_the_engine_was_down_is_resumed_by_the_sweep_after_restart(fresh_stack):
    """The engine died between the decision and the claim (resume_state 'pending'): nothing downstream ran, so it is safe to resume."""
    s = fresh_stack
    flow = refund_flow(s)
    sub, task = submit_waiting(flow)
    s.crash()
    s.q("""UPDATE human_tasks SET status = 'approved', decided_by = 'Dana', decided_at = now() - interval '1 minute',
                  decision = '{"decision": "approved", "comment": "x", "form_data": {}, "decided_by": "Dana"}'::jsonb,
                  decide_key = 'k-down', resume_state = 'pending', updated_at = now() - interval '1 minute' WHERE id = %s""", (task["id"],))
    assert exec_row(s, sub["execution_id"])["status"] == "waiting"
    s.restart()
    flow.stack = s
    s.tick()
    done = wait_run(flow, sub)
    assert done["status"] == "success" and done["result"]["status"] == "approved"
    assert len(refunds_of(s, sub["execution_id"])) == 1
    s.tick()
    time.sleep(0.5)
    assert len(refunds_of(s, sub["execution_id"])) == 1
    assert decide(s.api, task["id"], "rejected", by="Eli").json()["outcome"] == "already_decided"


def test_the_real_scheduler_resumes_a_pending_decision_after_restart_with_no_hook(fresh_stack):
    s = fresh_stack
    flow = refund_flow(s)
    sub, task = submit_waiting(flow)
    s.crash()
    s.q("""UPDATE human_tasks SET status = 'rejected', decided_by = 'Eli', decided_at = now(),
                  decision = '{"decision": "rejected", "comment": null, "form_data": {}, "decided_by": "Eli"}'::jsonb,
                  decide_key = 'k-down', resume_state = 'pending', updated_at = now() - interval '1 minute' WHERE id = %s""", (task["id"],))
    s.restart()
    flow.stack = s
    done = wait_run(flow, sub, timeout=80)               # the 30 s CronScheduler tick must pick it up
    assert done["status"] == "success" and done["result"]["status"] == "rejected"
    assert refunds_of(s, sub["execution_id"]) == []


# ── crash mid-resume: never re-run ────────────────────────────────────────────


def start_slow_resume(s: Stack):
    """Approve a run whose refund call blocks in the fake bank; returns when the call is in flight (resume 'running')."""
    flow = refund_flow(s, issue_path="/slow-refunds")
    sub, task = submit_waiting(flow)
    assert decide(s.api, task["id"], "approved", by="Dana").json()["outcome"] == "decided"
    poll_until(lambda: len(refunds_of(s, sub["execution_id"])), lambda n: n >= 1, what="the refund call to arrive")
    row = task_row(s, task["id"])
    assert row["resume_state"] == "running" and row["resume_heartbeat"] is not None and row["resume_owner"]
    assert exec_row(s, sub["execution_id"])["status"] == "running"
    return flow, sub, task


def assert_interrupted(s: Stack, flow, sub, task) -> None:
    ex = exec_row(s, sub["execution_id"])
    assert ex["status"] == "failed" and ex["metadata"].get("error_code") == "RESUME_INTERRUPTED", ex
    assert "partly run" in ex["metadata"]["error"] or "check before retrying" in ex["metadata"]["error"]
    assert task_row(s, task["id"])["resume_state"] == "failed"
    view = run_view(flow, sub)
    assert view["status"] == "failed" and view["result"] is None
    assert view["error"]["code"] and sub["execution_id"] in view["error"]["message"]
    blob = str(view)
    assert "Issue refund" not in blob and "slow-refunds" not in blob and "Traceback" not in blob   # public: no internals


def test_crash_mid_resume_fails_resume_interrupted_at_startup_and_never_pays_twice(fresh_stack):
    """The process dies while the refund call is in flight; by the time it restarts the heartbeat is stale."""
    s = fresh_stack
    flow, sub, task = start_slow_resume(s)
    s.crash()
    s.q(STALE)                                   # time passes while the engine is down
    s.restart()                                  # startup sweep
    flow.stack = s
    s.refunds.release()
    poll_until(lambda: exec_row(s, sub["execution_id"])["status"], lambda st: st == "failed", timeout=20, what="the startup sweep")
    assert_interrupted(s, flow, sub, task)
    for _ in range(3):
        s.tick()
    time.sleep(1.0)
    assert len(refunds_of(s, sub["execution_id"])) == 1, s.refunds.calls    # at-most-once: the refund was NOT repeated
    assert decide(s.api, task["id"], "approved", by="Eli").json()["outcome"] == "already_decided"
    time.sleep(0.5)
    assert len(refunds_of(s, sub["execution_id"])) == 1
    assert exec_row(s, sub["execution_id"])["status"] == "failed"
    assert s.api.get("/tasks/counts", params={"project_id": flow.pid}).json()["open"] == 0


def test_a_fresh_heartbeat_is_left_alone_by_the_sweep_and_goes_interrupted_once_stale(fresh_stack):
    """5.5: only heartbeat staleness counts (never the boot id): a restart within the window must not touch the resume."""
    s = fresh_stack
    flow, sub, task = start_slow_resume(s)
    owner_before = task_row(s, task["id"])["resume_owner"]
    s.crash()
    s.restart()                                  # new boot id, heartbeat only seconds old
    flow.stack = s
    s.refunds.release()
    s.tick()
    s.tick()
    row = task_row(s, task["id"])
    assert row["resume_state"] == "running" and row["resume_owner"] == owner_before        # not claimed by anyone else, not failed
    assert exec_row(s, sub["execution_id"])["status"] == "running"
    s.q(STALE)
    s.tick()
    assert_interrupted(s, flow, sub, task)
    s.tick()
    time.sleep(1.0)
    assert len(refunds_of(s, sub["execution_id"])) == 1


def test_the_real_scheduler_also_fails_a_stale_resume_without_any_hook(fresh_stack):
    s = fresh_stack
    flow, sub, task = start_slow_resume(s)
    s.crash()
    s.restart()
    flow.stack = s
    s.refunds.release()
    s.q(STALE)
    poll_until(lambda: exec_row(s, sub["execution_id"])["status"], lambda st: st == "failed", timeout=80, every=1.0, what="the scheduler tick")
    assert_interrupted(s, flow, sub, task)
    assert len(refunds_of(s, sub["execution_id"])) == 1


def test_kill_right_after_the_decision_never_pays_twice_and_never_hangs(fresh_stack):
    """Crash at an arbitrary instant after the decide returns: unclaimed (sweep resumes it), claimed (fails loudly) or done.
    Whatever the instant, at most one refund and the run reaches a terminal state."""
    s = fresh_stack
    seen = set()
    for i in range(4):
        flow = refund_flow(s)
        sub, task = submit_waiting(flow)
        assert decide(s.api, task["id"], "approved", by="Dana").status_code == 200
        if i % 2:
            time.sleep(0.02 * i)
        s.crash()
        s.restart()
        flow.stack = s
        s.q("UPDATE human_tasks SET updated_at = now() - interval '1 minute' WHERE resume_state = 'pending'")
        s.q(STALE)

        def settle():
            s.tick()
            return exec_row(s, sub["execution_id"])["status"]

        end = poll_until(settle, lambda st: st in ("success", "failed"), timeout=40, every=1.0, what="a terminal state")
        n = len(refunds_of(s, sub["execution_id"]))
        assert n <= 1, f"iteration {i}: {n} refunds"
        row = task_row(s, task["id"])
        if end == "failed":
            assert exec_row(s, sub["execution_id"])["metadata"].get("error_code") == "RESUME_INTERRUPTED"
            assert row["resume_state"] == "failed"
        else:
            assert n == 1 and row["resume_state"] == "done"
        seen.add(end)
        s.tick()
        time.sleep(0.3)
        assert len(refunds_of(s, sub["execution_id"])) == n          # settled: nothing runs again later
    assert seen  # the loop ran


# ── retention, pinning, drafts ────────────────────────────────────────────────


def test_a_waiting_run_survives_retention_pruning_and_still_resumes(stack):
    flow = refund_flow(stack)
    sub, task = submit_waiting(flow)                    # the oldest run in the database
    pid, _ = new_project(stack.api)
    wf = {"name": uniq("noise"), "settings": {}, "nodes": [N("Start", "Start"), N("Set", "Set", {"mode": "manual", "keepOnlySet": True, "fields": [{"name": "a", "value": "1"}]}, 200)],
          "connections": [C("Start", "Set")]}
    noise = stack.api.post(f"/projects/{pid}/workflows", wf).json()["id"]
    for _ in range(110):                                # retention keeps the newest 100: this prunes everything older
        assert stack.api.post(f"/workflows/{noise}/run", {"input_data": {}}).status_code == 200
    total = stack.q("SELECT count(*) FROM executions WHERE mode <> 'test'")[0][0]
    assert total <= 105, f"retention did not run ({total} executions)"      # proves pruning happened in this test
    assert stack.q("SELECT count(*) FROM executions WHERE id = %s", (sub["execution_id"],))[0][0] == 1
    assert exec_row(stack, sub["execution_id"])["status"] == "waiting"
    assert stack.q("SELECT count(*) FROM node_outputs WHERE execution_id = %s", (sub["execution_id"],))[0][0] >= 1   # R3
    assert task_row(stack, task["id"])["status"] == "open"
    won = decide(stack.api, task["id"], "approved", by="Dana")
    assert won.json()["outcome"] == "decided"
    done = wait_run(flow, sub)
    assert done["status"] == "success" and done["result"]["status"] == "approved"
    assert len(refunds_of(stack, sub["execution_id"])) == 1


def test_a_published_run_keeps_the_version_it_started_on(stack):
    flow = refund_flow(stack)
    sub, task = submit_waiting(flow)
    wf = stack.api.get(f"/workflows/{flow.wid}").json()["definition"]
    nodes = wf["nodes"]
    for n in nodes:
        if n["name"] == "Refund issued":
            for f in n["parameters"]["fields"]:
                if f["name"] == "message":
                    f["value"] = "CHANGED AFTER PUBLISH"
        if n["name"] == "Issue refund":
            n["parameters"]["url"] = stack.refunds.url + "/changed-path"
    assert stack.api.put(f"/workflows/{flow.wid}", {"nodes": nodes}).status_code == 200
    assert stack.api.post(f"/workflows/{flow.wid}/publish", {}).status_code == 200      # a newer published version exists now
    assert decide(stack.api, task["id"], "approved").json()["outcome"] == "decided"
    done = wait_run(flow, sub)
    assert done["status"] == "success" and done["result"]["message"] == "Your refund has been issued."
    assert [c["path"] for c in stack.refunds.refunds() if (c["key"] or "").startswith(sub["execution_id"])] == ["/refunds"]


def test_a_draft_run_keeps_the_definition_it_suspended_with(stack):
    """R4: editing the draft while a draft run waits must not change what that run does when it resumes."""
    pid, _ = new_project(stack.api)
    wf = {"name": uniq("draft"), "settings": {}, "nodes": [
        N("Start", "Start"), N("Review", "Approval", {"title": "t"}, 200),
        N("Yes", "Set", {"mode": "manual", "keepOnlySet": True, "fields": [{"name": "outcome", "value": "original"}]}, 400),
    ], "connections": [C("Start", "Review"), C("Review", "Yes", "approved")]}
    wid = stack.api.post(f"/projects/{pid}/workflows", wf).json()["id"]
    exec_id = stack.api.post(f"/workflows/{wid}/run", {"input_data": {}}).json()["execution_id"]
    task = poll_until(lambda: [t for t in stack.api.get("/tasks", params={"project_id": pid}).json()["items"] if t["execution_id"] == exec_id],
                      lambda ts: len(ts) == 1, what="the draft task")[0]
    nodes = stack.api.get(f"/workflows/{wid}").json()["definition"]["nodes"]
    for n in nodes:
        if n["name"] == "Yes":
            n["parameters"]["fields"][0]["value"] = "EDITED"
    assert stack.api.put(f"/workflows/{wid}", {"nodes": nodes}).status_code == 200
    assert decide(stack.api, task["id"], "approved").json()["outcome"] == "decided"
    final = poll_until(lambda: stack.api.get(f"/executions/{exec_id}").json(), lambda e: e["status"] in ("success", "failed"), what="draft run to end")
    assert final["status"] == "success" and final["node_data"]["Yes"][0]["json"]["outcome"] == "original"
