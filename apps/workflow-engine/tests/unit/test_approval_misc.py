"""Small Stage 5 pieces: actor seam, replay of a decided approval, hooks, scheduler wiring, detached runs."""

from __future__ import annotations

import asyncio

import pytest
from starlette.requests import Request

from src.core.actor import Actor, current_actor
from src.services.test_suggest import _recorded_mock

from ._pg_world import CALLS, approval_flow, install_counter, make_world, remove_counter

pytestmark = pytest.mark.anyio


def req(headers: dict[str, str] | None = None) -> Request:
    return Request({"type": "http", "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()], "method": "POST", "path": "/"})


def test_actor_is_the_typed_name_until_identity_exists():
    a = current_actor(req(), "  Dana  ")
    assert a == Actor(id=None, label="Dana", kind="typed")
    assert current_actor(req(), "x" * 500).label == "x" * 200
    assert current_actor(req({"X-User-Id": "u-7"})) == Actor(id="u-7", label="u-7", kind="user")
    assert current_actor(req({"X-User-Id": "u-7"}), "Dana").kind == "typed"  # what the person typed wins
    assert current_actor(req()).label == "" and current_actor(None, None).label == ""


def test_a_decided_approval_replays_on_the_port_it_took():
    out = [{"amount": 5, "approval": {"decision": "rejected"}}]
    assert _recorded_mock("Approval", out, None) == {"items": out, "port": "rejected"}
    assert _recorded_mock("Approval", [{"x": 1}], None) == {"items": [{"x": 1}]}
    assert _recorded_mock("Set", out, None) == {"items": out}
    assert _recorded_mock("Approval", out, "boom") == {"error": {"message": "boom"}}


@pytest.fixture
async def world(pg):
    install_counter()
    try:
        yield await make_world(pg)
    finally:
        remove_counter()


async def test_the_scheduler_tick_times_out_and_resumes(world):
    from src.engine.cron_scheduler import CronScheduler

    stored = await world.create(approval_flow(due_in="1h"))
    _, ctx = await world.run(stored.id)
    task = await world.task(ctx.execution_id)
    await world.sql("UPDATE human_tasks SET due_at = now() - interval '1 second' WHERE id = :i", i=task["id"])
    started = await CronScheduler(world.sf)._tick_tasks()
    await asyncio.gather(*started)
    assert (await world.execution(ctx.execution_id))["status"] == "success" and CALLS == ["up", "late"]


async def test_one_failing_part_of_the_tick_does_not_skip_the_others(world, monkeypatch):
    from src.engine.cron_scheduler import CronScheduler

    sched = CronScheduler(world.sf)
    ran = []

    async def boom():
        ran.append("triggers")
        raise RuntimeError("x")

    async def smoke():
        ran.append("smoke")
        return []

    async def tasks():
        ran.append("tasks")
        return []

    monkeypatch.setattr(sched, "_tick_triggers", boom)
    monkeypatch.setattr(sched, "_tick_smoke", smoke)
    monkeypatch.setattr(sched, "_tick_tasks", tasks)
    await sched._tick()
    assert ran == ["triggers", "smoke", "tasks"]


async def test_the_smoke_tick_and_its_skip_locked_claim_are_intact(world):
    """_tick_smoke still claims due smoke configs (FOR UPDATE SKIP LOCKED) and returns the tasks it started."""
    from src.engine.cron_scheduler import CronScheduler
    import inspect

    assert "skip_locked=True" in inspect.getsource(CronScheduler._tick_smoke)
    assert await CronScheduler(world.sf)._tick_smoke() == []


async def test_the_test_hook_can_make_a_task_due_and_tick(world):
    from src.routes.test_hooks import TaskFaultBody, TaskTickBody, put_task_fault, tick_tasks
    from src.engine import task_resumer

    stored = await world.create(approval_flow(due_in="1h"))
    _, ctx = await world.run(stored.id)
    task = await world.task(ctx.execution_id)
    assert await tick_tasks(TaskTickBody(task_id=task["id"], make_due=True)) == {"started": 1}
    assert (await world.task(ctx.execution_id))["status"] == "timed_out" and CALLS == ["up", "late"]
    assert await put_task_fault(TaskFaultBody(after_claim=True)) == {"after_claim": True}
    assert task_resumer.FAULT == {"after_claim": True}
    assert await put_task_fault(TaskFaultBody(after_claim=False)) == {"after_claim": False}


async def test_run_detached_returns_the_execution_id_and_the_run_outlives_the_caller(world):
    from src.engine import execution_registry
    from src.services.execution_service import run_detached

    stored = await world.create(approval_flow())
    exec_id = await run_detached(stored.id, initial_item={"amount": 9}, mode="api", substitute_inputs=False,
                                 metadata={"interface": {"slug": "s", "mode": "live"}})
    task = execution_registry.get_all().get(exec_id)
    if task is not None:
        await task
    ex = await world.execution(exec_id)
    assert ex["status"] == "waiting" and ex["metadata"]["interface"]["slug"] == "s" and ex["mode"] == "api"
    assert (await world.task(exec_id))["status"] == "open"


async def test_run_detached_raises_a_start_failure_to_the_caller(world):
    from src.core.exceptions import WorkflowNotFoundError
    from src.services.execution_service import run_detached

    with pytest.raises(WorkflowNotFoundError):
        await run_detached("wf_does_not_exist", initial_item={})
