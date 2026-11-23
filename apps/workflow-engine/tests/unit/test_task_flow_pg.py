"""Durable approvals end to end on real Postgres (Stage 5, DESIGN-STAGE5-UIS 5.2-5.6): a run pauses at an Approval,
becomes a waiting execution with a task, and resumes in the same execution after a decision."""

from __future__ import annotations

import pytest

from ._pg_world import CALLS, World, approval_flow, install_counter, make_world, remove_counter

pytestmark = pytest.mark.anyio


@pytest.fixture
async def world(pg):
    install_counter()
    try:
        yield await make_world(pg)
    finally:
        remove_counter()


async def paused(world: World, **approval_params):
    """Create + run the approval flow; returns (stored, execution id, task row)."""
    stored = await world.create(approval_flow(**approval_params))
    _, ctx = await world.run(stored.id)
    return stored, ctx.execution_id, await world.task(ctx.execution_id)


# ── pause ─────────────────────────────────────────────────────────────────────

async def test_a_run_that_pauses_is_a_waiting_execution_with_one_task(world):
    stored, exec_id, task = await paused(world, title="Refund {{ $json.amount }}", due_in="3h",
                                         context={"Amount": "{{ $json.amount }}"})
    ex = await world.execution(exec_id)
    assert ex["status"] == "waiting" and ex["end_time"] is None
    assert ex["metadata"]["suspension"]["tasks"] == [task["id"]] and "since" in ex["metadata"]["suspension"]
    assert task["status"] == "open" and task["resume_state"] == "none" and task["kind"] == "approval"
    assert task["title"] == "Refund 5" and task["context"]["Amount"] == 5
    assert task["due_at"] is not None and task["on_timeout"] == "timed_out"
    assert task["workflow_id"] == stored.id and task["project_id"] == ex["project_id"]
    assert task["run_index"] == 0 and task["workflow_version_id"] is None
    assert CALLS == ["up"]  # nothing downstream of the Approval ran
    outs = await world.outputs(exec_id)
    assert outs[("review", 0)] == "waiting" and ("yes", 0) not in outs


async def test_the_waiting_run_does_not_run_drift_or_cleanup_hooks_but_calls_on_complete_hooks(world):
    from src.repositories import execution_repository as er

    seen = []

    async def hook(context, record):
        seen.append((record.status, bool(context.suspensions)))

    er.on_complete_hooks.append(hook)
    try:
        await paused(world)
    finally:
        er.on_complete_hooks.remove(hook)
    assert seen == [("waiting", True)]


async def test_creating_tasks_is_idempotent(world):
    """The unique (execution, node, run_index) index makes a repeated complete() a no-op."""
    from src.repositories.execution_repository import ExecutionRepository

    stored = await world.create(approval_flow())
    _, ctx = await world.run(stored.id)
    async with world.sf() as s:
        await ExecutionRepository(s).complete(ctx, stored.id, stored.name)
    assert len(await world.tasks_of(ctx.execution_id)) == 1


async def test_a_failed_run_creates_no_task(world):
    from ._pg_world import build, nd

    w = build([nd("start", "Start"), nd("review", "Approval"), nd("bad", "Code", code="raise Exception('x')")],
              [("start", "review"), ("start", "bad")])
    stored = await world.create(w)
    _, ctx = await world.run(stored.id)
    ex = await world.execution(ctx.execution_id)
    assert ex["status"] == "failed" and await world.tasks_of(ctx.execution_id) == []


# ── resume ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("decision,branch", [("approved", "yes"), ("rejected", "no")])
async def test_resume_reuses_the_execution_and_runs_only_downstream(world, decision, branch):
    stored, exec_id, task = await paused(world)
    res = await world.decide(task["id"], decision, comment="looks fine")
    assert res["outcome"] == "decided" and res["task"]["status"] == decision
    await world.settle()
    ex = await world.execution(exec_id)
    assert ex["status"] == "success" and ex["end_time"] is not None
    assert "resumed_at" in ex["metadata"] and "suspension" not in ex["metadata"]
    assert CALLS == ["up", branch]  # upstream ran once; only the decided branch ran after the resume
    outs = await world.outputs(exec_id)
    assert outs[("review", 0)] == "success" and outs[(branch, 0)] == "success"
    other = "no" if branch == "yes" else "yes"
    assert (other, 0) not in outs and ("late", 0) not in outs
    t = await world.task(exec_id)
    assert t["resume_state"] == "done" and t["status"] == decision
    assert t["decision"]["comment"] == "looks fine" and t["decided_by"] == "dana"
    # the Approval emitted its input plus the decision, and the branch node saw it
    async with world.sf() as s:
        from src.repositories.node_output_repository import NodeOutputRepository

        rows = {r.node_name: r for r in await NodeOutputRepository(s).get_outputs(exec_id)}
    seen = rows[branch].output[0]["json"]
    assert seen["amount"] == 5 and seen["approval"]["decision"] == decision and seen["approval"]["task_id"] == task["id"]
    # one execution row for the whole story
    assert len(await world.sql("SELECT id FROM executions WHERE workflow_id = :w", w=stored.id)) == 1


async def test_resume_after_the_draft_changed_uses_the_draft_snapshot(world):
    from ._pg_world import approval_flow

    stored, exec_id, task = await paused(world)
    changed = approval_flow()
    changed.nodes = [n for n in changed.nodes if n.name != "yes"]  # the author deletes the 'yes' branch meanwhile
    changed.connections = [c for c in changed.connections if c.target_node != "yes"]
    await world.edit_draft(stored, changed)
    await world.decide(task["id"], "approved")
    await world.settle()
    assert CALLS == ["up", "yes"]  # the run finishes the definition it started with (R4)


async def test_a_published_run_is_pinned_to_its_version(world):
    from ._pg_world import approval_flow

    stored = await world.create(approval_flow())
    vid = await world.publish(stored)
    _, ctx = await world.run(stored.id, version_id=vid)
    task = await world.task(ctx.execution_id)
    assert task["workflow_version_id"] == vid and task["snapshot"]["definition_source"] == {"version_id": vid}
    changed = approval_flow()
    changed.nodes = [n for n in changed.nodes if n.name != "yes"]
    changed.connections = [c for c in changed.connections if c.target_node != "yes"]
    await world.edit_draft(stored, changed)  # republishing/editing never changes an in-flight run
    await world.decide(task["id"], "approved")
    await world.settle()
    assert CALLS == ["up", "yes"]
    assert (await world.execution(ctx.execution_id))["status"] == "success"


async def test_the_resume_key_reaches_downstream_expressions(world):
    from ._pg_world import build, nd

    w = build([nd("start", "Start"), nd("review", "Approval"),
               nd("yes", "Set", mode="manual", fields=[{"name": "key", "value": "{{ $execution.resumeKey }}", "type": "string"}])],
              [("start", "review"), ("review", "yes", "approved")])
    stored = await world.create(w)
    _, ctx = await world.run(stored.id)
    task = await world.task(ctx.execution_id)
    await world.decide(task["id"])
    await world.settle()
    async with world.sf() as s:
        from src.repositories.node_output_repository import NodeOutputRepository

        rows = {r.node_name: r for r in await NodeOutputRepository(s).get_outputs(ctx.execution_id)}
    assert rows["yes"].output[0]["json"]["key"] == f"{ctx.execution_id}:review:0"


async def test_the_final_result_is_stored_for_interface_runs_only(world):
    from ._pg_world import build, nd

    w = build([nd("start", "Start"), nd("review", "Approval"),
               nd("answer", "Respond", body='{"status": "{{ $json.approval.decision }}"}')],
              [("start", "review"), ("review", "answer", "approved")])
    stored = await world.create(w)
    _, ctx = await world.run(stored.id, metadata={"interface": {"slug": "s", "mode": "live"}})
    task = await world.task(ctx.execution_id)
    assert "result" not in (await world.execution(ctx.execution_id))["metadata"]  # not finished yet
    await world.decide(task["id"])
    await world.settle()
    meta = (await world.execution(ctx.execution_id))["metadata"]
    assert meta["result"] == {"status": "approved"} and meta["interface"]["slug"] == "s"
    # a run that is not an interface run carries no result
    _, plain = await world.run(stored.id)
    t2 = await world.task(plain.execution_id)
    await world.decide(t2["id"])
    await world.settle()
    assert "result" not in (await world.execution(plain.execution_id))["metadata"]
