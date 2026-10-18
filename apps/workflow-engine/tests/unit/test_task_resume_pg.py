"""Resume safety (Stage 5, DESIGN-STAGE5-UIS 5.5-5.7, 8): crash mid-resume is never re-run, decided-never-claimed tasks are
resumed by the sweep, timeouts use SKIP LOCKED, retention keeps waiting runs, the stale reaper ages by resumed_at, secrets
never reach a task row, cancel closes tasks, parallel approvals keep the run waiting until the last one."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta

import pytest

from src.engine import task_resumer
from src.engine.stale_reaper import StaleReaper
from src.services.task_service import TaskService

from ._pg_world import CALLS, World, approval_flow, build, install_counter, make_world, nd, remove_counter

pytestmark = pytest.mark.anyio


@pytest.fixture
async def world(pg):
    install_counter()
    try:
        yield await make_world(pg)
    finally:
        task_resumer.FAULT.clear()
        remove_counter()


async def paused(world: World, **approval_params):
    stored = await world.create(approval_flow(**approval_params))
    _, ctx = await world.run(stored.id)
    return stored, ctx.execution_id, await world.task(ctx.execution_id)


async def decide_without_kick(world: World, task_id: str, decision: str = "approved") -> None:
    """A decision whose in-process resume never started (the process died right after the UPDATE)."""
    async with world.sf() as s:
        await TaskService(s, kick_resume=False).decide(task_id, decision, None, None, "dana", "k-" + task_id)


# ── decided but never claimed: the sweep resumes it ───────────────────────────

async def test_a_decided_never_claimed_task_is_resumed_by_the_sweep(world):
    _, exec_id, task = await paused(world)
    await decide_without_kick(world, task["id"])
    assert (await world.task(exec_id))["resume_state"] == "pending" and CALLS == ["up"]
    started = await task_resumer.sweep_pending(world.sf, min_age_s=0)
    assert len(started) == 1
    await asyncio.gather(*started)
    assert CALLS == ["up", "yes"]
    assert (await world.execution(exec_id))["status"] == "success"
    assert (await world.task(exec_id))["resume_state"] == "done"
    assert await task_resumer.sweep_pending(world.sf, min_age_s=0) == []  # and it is not resumed twice


async def test_the_sweep_leaves_a_fresh_decision_to_the_in_process_kick(world):
    _, exec_id, task = await paused(world)
    await decide_without_kick(world, task["id"])
    assert await task_resumer.sweep_pending(world.sf, min_age_s=60) == []  # decided seconds ago: the kick has priority


async def test_two_claims_of_the_same_task_resume_once(world):
    _, exec_id, task = await paused(world)
    await decide_without_kick(world, task["id"])
    outcomes = await asyncio.gather(*[task_resumer.claim_and_resume(task["id"], world.sf) for _ in range(3)])
    assert sorted(outcomes).count("resumed") == 1 and set(outcomes) <= {"resumed", "not_pending", "busy"}
    assert CALLS == ["up", "yes"]


# ── crash mid-resume: never re-run ────────────────────────────────────────────

async def test_a_crash_mid_resume_fails_the_run_loudly_and_never_reruns(world, monkeypatch):
    _, exec_id, task = await paused(world)
    task_resumer.FAULT["after_claim"] = True  # the process "dies" right after the claim
    await world.decide(task["id"])
    await world.settle()
    t = await world.task(exec_id)
    assert t["resume_state"] == "running" and t["resume_owner"] == task_resumer.BOOT_ID
    assert (await world.execution(exec_id))["status"] == "running" and CALLS == ["up"]

    monkeypatch.setattr(task_resumer, "BOOT_ID", "a-new-process")  # restart: a different boot id
    # the dead process stopped beating; only a stale heartbeat (not a different boot id) fails a resume
    await world.sql("UPDATE human_tasks SET resume_heartbeat = now() - interval '5 minutes' WHERE id = :i", i=task["id"])
    failed = await task_resumer.startup_sweep(world.sf)
    assert failed == [task["id"]]
    ex = await world.execution(exec_id)
    assert ex["status"] == "failed" and ex["metadata"]["error_code"] == "RESUME_INTERRUPTED"
    assert "check before retrying" in ex["metadata"]["error"] and ex["error_count"] >= 1
    t = await world.task(exec_id)
    assert t["resume_state"] == "failed"
    outs = await world.outputs(exec_id)
    assert outs[("review", 0)] == "error"  # shown on the Approval node in the run view
    # nothing re-runs it, not the sweep, not a tick, not a second startup sweep
    await task_resumer.tick(world.sf)
    await world.settle()
    assert await task_resumer.sweep_pending(world.sf, min_age_s=0) == []
    assert await task_resumer.startup_sweep(world.sf) == []
    assert CALLS == ["up"]
    assert await task_resumer.claim_and_resume(task["id"], world.sf) == "not_pending"


async def test_a_running_resume_of_this_process_is_not_failed_by_the_sweep(world):
    _, exec_id, task = await paused(world)
    task_resumer.FAULT["after_claim"] = True
    await world.decide(task["id"])
    await world.settle()
    assert await task_resumer.startup_sweep(world.sf) == []  # same boot id: it is ours, still "running"
    assert (await world.execution(exec_id))["status"] == "running"


async def test_a_stale_heartbeat_fails_a_resume_of_another_process_but_a_fresh_one_waits(world, monkeypatch):
    _, exec_id, task = await paused(world)
    task_resumer.FAULT["after_claim"] = True
    await world.decide(task["id"])
    await world.settle()
    monkeypatch.setattr(task_resumer, "BOOT_ID", "other-process")
    assert await task_resumer.fail_interrupted(world.sf, startup=False) == []  # heartbeat is fresh: it may still be alive
    await world.sql("UPDATE human_tasks SET resume_heartbeat = now() - interval '5 minutes' WHERE id = :i", i=task["id"])
    assert await task_resumer.fail_interrupted(world.sf, startup=False) == [task["id"]]
    assert (await world.execution(exec_id))["metadata"]["error_code"] == "RESUME_INTERRUPTED"


async def test_a_resume_that_throws_fails_the_run_with_resume_failed(world):
    stored = await world.create(approval_flow())
    vid = await world.publish(stored)
    _, ctx = await world.run(stored.id, version_id=vid)
    task = await world.task(ctx.execution_id)
    await world.sql("UPDATE workflows SET published_version_id = NULL WHERE id = :w", w=stored.id)
    await world.sql("DELETE FROM workflow_versions WHERE id = :v", v=vid)  # the pinned version is gone
    await world.decide(task["id"])
    await world.settle()
    ex = await world.execution(ctx.execution_id)
    assert ex["status"] == "failed" and ex["metadata"]["error_code"] == "RESUME_FAILED"
    assert (await world.task(ctx.execution_id))["resume_state"] == "failed"
    assert CALLS == ["up"]


# ── timeouts ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("on_timeout,status,branch", [("timed_out", "timed_out", "late"), ("reject", "rejected", "no"),
                                                      ("approve", "approved", "yes")])
async def test_a_due_task_times_out_by_on_timeout_and_the_run_takes_that_port(world, on_timeout, status, branch):
    _, exec_id, task = await paused(world, due_in="1h", on_timeout=on_timeout)
    assert (await task_resumer.expire_due(world.sf)) == []  # not due yet
    await world.sql("UPDATE human_tasks SET due_at = now() - interval '1 second' WHERE id = :i", i=task["id"])
    started = await task_resumer.tick(world.sf)
    await asyncio.gather(*started)
    t = await world.task(exec_id)
    assert t["status"] == status and t["decided_by"] == "system:timeout" and t["decision"]["decision"] == status
    assert CALLS == ["up", branch]
    assert (await world.execution(exec_id))["status"] == "success"
    # the approval seen downstream says who decided
    async with world.sf() as s:
        from src.repositories.node_output_repository import NodeOutputRepository

        rows = {r.node_name: r for r in await NodeOutputRepository(s).get_outputs(exec_id)}
    assert rows[branch].output[0]["json"]["approval"]["decided_by"] == "system:timeout"


async def test_a_decision_after_the_timeout_loses(world):
    _, exec_id, task = await paused(world, due_in="1h")
    await world.sql("UPDATE human_tasks SET due_at = now() - interval '1 second' WHERE id = :i", i=task["id"])
    await asyncio.gather(*await task_resumer.tick(world.sf))
    late = await world.decide(task["id"], "approved")
    assert late["outcome"] == "already_closed" and late["task"]["status"] == "timed_out"
    assert CALLS == ["up", "late"]


async def test_timeouts_skip_rows_another_instance_has_locked(world):
    """FOR UPDATE SKIP LOCKED: two engine instances never take the same task."""
    tasks = []
    for _ in range(3):
        _, exec_id, task = await paused(world, due_in="1h")
        tasks.append(task["id"])
    await world.sql("UPDATE human_tasks SET due_at = now() - interval '1 second' WHERE id = ANY(:ids)", ids=tasks)
    async with world.sf() as other:  # "instance B" holds a row lock on the first task, mid-timeout
        from sqlalchemy import text

        await other.execute(text("SELECT id FROM human_tasks WHERE id = :i FOR UPDATE"), {"i": tasks[0]})
        expired = await task_resumer.expire_due(world.sf)
        got = {t["id"] for t in expired}
        assert tasks[0] not in got and got >= set(tasks[1:])
        await other.rollback()
    again = await task_resumer.expire_due(world.sf)
    assert {t["id"] for t in again} == {tasks[0]}  # picked up once the lock is gone, and only that one
    assert await task_resumer.expire_due(world.sf) == []  # nothing is timed out twice
    await world.settle()


async def test_concurrent_expiry_passes_split_the_tasks(world):
    ids = []
    for _ in range(4):
        _, _, task = await paused(world, due_in="1h")
        ids.append(task["id"])
    await world.sql("UPDATE human_tasks SET due_at = now() - interval '1 second' WHERE id = ANY(:ids)", ids=ids)
    a, b = await asyncio.gather(task_resumer.expire_due(world.sf), task_resumer.expire_due(world.sf))
    got = [t["id"] for t in a] + [t["id"] for t in b]
    assert sorted(got) == sorted(ids)  # every task exactly once


# ── retention and the stale reaper ────────────────────────────────────────────

async def test_retention_never_prunes_waiting_runs_or_pending_resumes(world):
    from src.repositories.execution_repository import ExecutionRepository

    _, waiting_id, _ = await paused(world)
    _, pending_exec, pending_task = await paused(world)
    await decide_without_kick(world, pending_task["id"])  # decided, resume not started: execution still "waiting"
    plain = build([nd("start", "Start"), nd("x")], [("start", "x")])
    stored = await world.create(plain)
    old = [(await world.run(stored.id))[1].execution_id for _ in range(3)]
    await world.sql("UPDATE executions SET start_time = now() - interval '30 days' WHERE id = ANY(:ids)", ids=[waiting_id, pending_exec, old[0]])
    async with world.sf() as s:
        await ExecutionRepository(s, max_records=1)._cleanup()
    left = {r[0] for r in await world.sql("SELECT id FROM executions WHERE id = ANY(:ids)", ids=[waiting_id, pending_exec, *old])}
    assert waiting_id in left and pending_exec in left  # protected
    assert old[0] not in left  # an ordinary old run is pruned
    assert (await world.tasks_of(waiting_id))  # the tasks of a protected run are still there


async def test_the_hourly_retention_cleaner_keeps_waiting_runs_too(world):
    from src.engine.retention_cleaner import RetentionCleaner

    _, waiting_id, _ = await paused(world)
    stored = await world.create(build([nd("start", "Start")], []))
    plain = (await world.run(stored.id))[1].execution_id
    await world.sql("UPDATE executions SET start_time = now() - interval '90 days' WHERE id = ANY(:ids)", ids=[waiting_id, plain])
    await RetentionCleaner(world.sf)._tick()
    left = {r[0] for r in await world.sql("SELECT id FROM executions WHERE id = ANY(:ids)", ids=[waiting_id, plain])}
    assert left == {waiting_id}


async def insert_running(world: World, exec_id: str, started_hours_ago: float, resumed_hours_ago: float | None) -> None:
    """A running execution. Times are the app's own naive local clock (what the reaper compares against), not the DB's now()."""
    meta = {} if resumed_hours_ago is None else {"resumed_at": (datetime.now() - timedelta(hours=resumed_hours_ago)).isoformat()}
    await world.sql(
        """INSERT INTO executions (id, workflow_id, workflow_name, status, mode, start_time, metadata)
           VALUES (:e, 'wf_r', 'r', 'running', 'manual', :t, CAST(:m AS jsonb))""",
        e=exec_id, t=datetime.now() - timedelta(hours=started_hours_ago), m=json.dumps(meta))


async def test_the_stale_reaper_ages_a_resumed_run_from_resumed_at(world):
    tag = world.prefix
    await insert_running(world, f"exec_{tag}_resumed_now", started_hours_ago=72, resumed_hours_ago=0.1)  # started 3 days ago, resumed just now
    await insert_running(world, f"exec_{tag}_resumed_old", started_hours_ago=72, resumed_hours_ago=5)  # resumed 5 h ago: stuck
    await insert_running(world, f"exec_{tag}_stuck", started_hours_ago=5, resumed_hours_ago=None)  # the old rule still applies
    await insert_running(world, f"exec_{tag}_fresh", started_hours_ago=1, resumed_hours_ago=None)
    await StaleReaper(world.sf)._tick()
    status = {r[0].split("_", 2)[2]: r[1] for r in await world.sql(
        "SELECT id, status FROM executions WHERE id LIKE :p", p=f"exec_{tag}_%")}
    assert status == {"resumed_now": "running", "resumed_old": "failed", "stuck": "failed", "fresh": "running"}


async def test_a_resumed_run_has_resumed_at_so_the_reaper_does_not_kill_it(world):
    _, exec_id, task = await paused(world)
    task_resumer.FAULT["after_claim"] = True
    await world.decide(task["id"])
    await world.settle()
    await world.sql("UPDATE executions SET start_time = :t WHERE id = :e", e=exec_id, t=datetime.now() - timedelta(days=3))
    await StaleReaper(world.sf)._tick()
    assert (await world.execution(exec_id))["status"] == "running"


# ── secrets ───────────────────────────────────────────────────────────────────

async def test_secrets_never_land_in_a_task_row(world):
    from src.engine.workflow_runner import WorkflowRunner
    from src.engine.types import NodeData
    from src.repositories.execution_repository import ExecutionRepository

    secret = "sk-live-TOP-SECRET-123"
    w = approval_flow(title="Token {{ $json.token }}", context={"Auth": "Bearer {{ $json.token }}", "ok": "visible"},
                      assignees=["dana@bank.example"])
    w.id = f"wf_secret_{world.prefix}"
    runner = WorkflowRunner(db_session_factory=world.sf)
    ctx = await runner.run(w, "start", [NodeData(json={"token": secret, "amount": 1})], secret_values={secret},
                           environment="default", project_id="proj_default", execution_id=f"exec_secret_{world.prefix}")
    async with world.sf() as s:
        await ExecutionRepository(s).complete(ctx, w.id, w.name)
    rows = await world.tasks_of(ctx.execution_id)
    assert len(rows) == 1
    blob = json.dumps({k: v for k, v in rows[0].items()}, default=str)
    assert secret not in blob
    assert "***" in blob and "visible" in blob and rows[0]["title"] == "Token ***"
    ex = await world.execution(ctx.execution_id)
    assert secret not in json.dumps(ex["metadata"], default=str)


# ── cancel ────────────────────────────────────────────────────────────────────

async def test_cancelling_a_waiting_run_cancels_its_tasks_and_a_later_decide_is_closed(world):
    from src.repositories.execution_repository import ExecutionRepository
    from src.services.execution_service import ExecutionService
    from src.repositories.workflow_repository import WorkflowRepository

    stored, exec_id, task = await paused(world)
    async with world.sf() as s:
        svc = ExecutionService(ExecutionRepository(s), WorkflowRepository(s))
        assert await svc.cancel_execution(exec_id) is True
    assert (await world.execution(exec_id))["status"] == "cancelled"
    assert (await world.task(exec_id))["status"] == "cancelled"
    res = await world.decide(task["id"])
    assert res["outcome"] == "already_closed" and res["task"]["status"] == "cancelled"
    await world.settle()
    assert CALLS == ["up"]


async def test_a_decision_on_a_run_cancelled_in_between_does_not_resume(world):
    from src.repositories.execution_repository import ExecutionRepository

    _, exec_id, task = await paused(world)
    await decide_without_kick(world, task["id"])
    async with world.sf() as s:
        await ExecutionRepository(s).cancel(exec_id)  # cancelled after the decision, before the resume was claimed
    assert await task_resumer.claim_and_resume(task["id"], world.sf) == "closed"
    assert (await world.task(exec_id))["resume_state"] == "done"
    assert CALLS == ["up"] and (await world.execution(exec_id))["status"] == "cancelled"


async def test_cancelling_a_finished_run_is_still_refused(world):
    from src.repositories.execution_repository import ExecutionRepository
    from src.repositories.workflow_repository import WorkflowRepository
    from src.services.execution_service import ExecutionService

    _, exec_id, task = await paused(world)
    await world.decide(task["id"])
    await world.settle()
    async with world.sf() as s:
        assert await ExecutionService(ExecutionRepository(s), WorkflowRepository(s)).cancel_execution(exec_id) is False


# ── parallel approvals ────────────────────────────────────────────────────────

def two_approvals():
    return build(
        [nd("start", "Start"), nd("up"), nd("ap1", "Approval"), nd("ap2", "Approval"), nd("y1"), nd("y2")],
        [("start", "up"), ("up", "ap1"), ("up", "ap2"), ("ap1", "y1", "approved"), ("ap2", "y2", "approved")],
        name="two-approvals",
    )


async def test_parallel_approvals_keep_the_run_waiting_until_the_last_is_decided(world):
    stored = await world.create(two_approvals())
    _, ctx = await world.run(stored.id)
    exec_id = ctx.execution_id
    t1, t2 = await world.task(exec_id, "ap1"), await world.task(exec_id, "ap2")
    assert (await world.execution(exec_id))["status"] == "waiting"
    await world.decide(t1["id"])
    await world.settle()
    ex = await world.execution(exec_id)
    assert ex["status"] == "waiting" and ex["end_time"] is None  # the second approval is still open
    assert ex["metadata"]["suspension"]["tasks"] == [t2["id"]]
    assert CALLS == ["up", "y1"]
    await world.decide(t2["id"])
    await world.settle()
    assert (await world.execution(exec_id))["status"] == "success" and CALLS == ["up", "y1", "y2"]


async def test_two_approvals_decided_at_once_both_run_one_after_the_other(world):
    stored = await world.create(two_approvals())
    _, ctx = await world.run(stored.id)
    exec_id = ctx.execution_id
    t1, t2 = await world.task(exec_id, "ap1"), await world.task(exec_id, "ap2")
    await asyncio.gather(world.decide(t1["id"]), world.decide(t2["id"]))
    for _ in range(5):  # the one that found the run busy is picked up by the sweep
        await world.settle()
        if (await world.execution(exec_id))["status"] == "success":
            break
        await asyncio.gather(*await task_resumer.sweep_pending(world.sf, min_age_s=0))
    assert (await world.execution(exec_id))["status"] == "success"
    assert sorted(CALLS) == ["up", "y1", "y2"]  # each branch ran exactly once, upstream once
    assert {t["resume_state"] for t in await world.tasks_of(exec_id)} == {"done"}


async def test_a_failure_after_one_approval_cancels_the_other_open_task(world):
    w = build([nd("start", "Start"), nd("ap1", "Approval"), nd("ap2", "Approval"), nd("boom", "Code", code="raise Exception('x')")],
              [("start", "ap1"), ("start", "ap2"), ("ap1", "boom", "approved")])
    stored = await world.create(w)
    _, ctx = await world.run(stored.id)
    t1 = await world.task(ctx.execution_id, "ap1")
    await world.decide(t1["id"])
    await world.settle()
    assert (await world.execution(ctx.execution_id))["status"] == "failed"
    assert (await world.task(ctx.execution_id, "ap2"))["status"] == "cancelled"
