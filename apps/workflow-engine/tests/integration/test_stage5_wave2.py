"""Stage 5 wave 2 (engine wiring), DB-backed on the throwaway Postgres (no HTTP server):

1. a streamed run that pauses on an Approval ends with ``execution_waiting`` (open tasks in the payload), not ``execution:complete``
2. a studio run (``WorkflowService.run_workflow``) reports ``waiting`` / ``failed`` / ``success`` through ``run_status``
3. the task row holds the values its screen binds in ``context._bindings`` (captured at suspend, redacted)
4. FormTrigger works in test mode: test-trigger types, ``find_start_node``, the trigger recipe, a workflow-target test run
5. the startup sweep never fails a resume whose heartbeat is fresh, whoever owns it; a stale heartbeat is still RESUME_INTERRUPTED
"""

from __future__ import annotations

import json

import pytest

from src.engine import task_resumer
from src.engine.types import ExecutionEventType

# the Postgres fixtures live with the unit tests; reuse them so one harness serves both
from tests.unit.conftest import anyio_backend, pg, pg_dbname  # noqa: F401
from tests.unit._pg_world import CALLS, World, approval_flow, build, install_counter, make_world, nd, remove_counter

pytestmark = pytest.mark.anyio


@pytest.fixture
async def world(pg):  # noqa: F811
    install_counter()
    try:
        yield await make_world(pg)
    finally:
        task_resumer.FAULT.clear()
        remove_counter()


# ── 1. execution_waiting on the stream ────────────────────────────────────────

async def stream(world: World, stored, monkeypatch) -> list[dict]:
    from src.repositories.execution_repository import ExecutionRepository
    from src.repositories.workflow_repository import WorkflowRepository
    from src.routes import streaming
    from src.engine.types import NodeData

    monkeypatch.setattr(streaming, "_get_event_bus", lambda: None)  # never publish to the developer database
    out: list[dict] = []
    async with world.sf() as s:
        gen = streaming._run_workflow_with_events(
            stored.workflow, "start", [NodeData(json={"amount": 5})], "manual",
            ExecutionRepository(s), workflow_repo=WorkflowRepository(s),
        )
        async for raw in gen:
            out.append(json.loads(raw))
    return out


async def test_a_streamed_run_that_pauses_ends_with_execution_waiting_and_its_tasks(world, monkeypatch):
    stored = await world.create(approval_flow(title="Check {{ $json.amount }}", assignees=["dana"], due_in="2h"))
    events = await stream(world, stored, monkeypatch)
    types = [e["type"] for e in events]
    assert types[-1] == "execution_waiting" and "execution:complete" not in types and "execution:error" not in types
    ev = events[-1]
    exec_id = ev["executionId"]
    task = await world.task(exec_id)
    assert [t["id"] for t in ev["tasks"]] == [task["id"]]
    t = ev["tasks"][0]
    assert t["node_name"] == "review" and t["title"] == "Check 5" and t["assignees"] == ["dana"] and t["due_at"]
    assert (await world.execution(exec_id))["status"] == "waiting"
    assert sum(1 for x in types if x == "execution_waiting") == 1


async def test_a_streamed_run_that_finishes_still_ends_with_execution_complete(world, monkeypatch):
    stored = await world.create(build([nd("start", "Start"), nd("a")], [("start", "a")]))
    events = await stream(world, stored, monkeypatch)
    types = [e["type"] for e in events]
    assert types[-1] == "execution:complete" and "execution_waiting" not in types
    assert "tasks" not in events[-1]


def test_the_event_type_is_the_name_the_design_gives():
    assert ExecutionEventType.EXECUTION_WAITING.value == "execution_waiting"


# ── 2. run_status for a studio run ────────────────────────────────────────────

async def studio_run(world: World, wf) -> str:
    from src.repositories.execution_repository import ExecutionRepository
    from src.repositories.workflow_repository import WorkflowRepository
    from src.services.workflow_service import WorkflowService

    stored = await world.create(wf)
    async with world.sf() as s:
        svc = WorkflowService(WorkflowRepository(s), ExecutionRepository(s), node_service=None)
        res = await svc.run_workflow(stored.id, {"amount": 5})
    await world.settle()
    return res.status


async def test_a_studio_run_that_hits_an_approval_reports_waiting(world):
    assert await studio_run(world, approval_flow()) == "waiting"


async def test_a_studio_run_without_an_approval_reports_success(world):
    assert await studio_run(world, build([nd("start", "Start"), nd("a")], [("start", "a")])) == "success"


async def test_a_studio_run_with_an_error_reports_failed_even_with_an_approval_branch(world):
    wf = build([nd("start", "Start"), nd("review", "Approval"), nd("boom", "StopAndError", message="no")],
               [("start", "review"), ("start", "boom")], name="mixed")
    assert await studio_run(world, wf) == "failed"


# ── 3. task_screen_bindings captured at suspend ───────────────────────────────

async def test_the_task_row_carries_the_screen_bindings_captured_at_suspend(world):
    wf = approval_flow(title="T")
    wf.interface = {"kind": "custom", "slug": "s", "trigger_node": "start",
                    "screens": {"task:review": {"v": 1, "components": [
                        {"type": "field", "bind": "node.up.via"}, {"type": "field", "bind": "node.up.amount"},
                        {"type": "actions", "bind": "task"}]}}}
    stored = await world.create(wf)
    _, ctx = await world.run(stored.id, item={"amount": 742})
    rows = await world.sql("SELECT context FROM human_tasks WHERE execution_id = :e", e=ctx.execution_id)
    assert len(rows) == 1
    bindings = rows[0][0]["_bindings"]
    assert bindings == {"up.via": "up", "up.amount": 742}
    # the task page never has to read node outputs: the values are in the row even after the outputs are gone
    await world.sql("DELETE FROM node_outputs WHERE execution_id = :e AND node_name = 'up'", e=ctx.execution_id)
    again = await world.sql("SELECT context FROM human_tasks WHERE execution_id = :e", e=ctx.execution_id)
    assert again[0][0]["_bindings"] == bindings


async def test_a_task_without_a_screen_has_empty_bindings(world):
    stored = await world.create(approval_flow())
    _, ctx = await world.run(stored.id)
    assert (await world.task(ctx.execution_id))["context"]["_bindings"] == {}


# ── 4. FormTrigger in test mode ───────────────────────────────────────────────

def form_node(name="Refund form"):
    return nd(name, "FormTrigger", input_schema={
        "type": "object", "properties": {"amount": {"type": "number"}, "reason": {"type": "string"}}, "required": ["amount"]})


def test_form_trigger_is_a_test_trigger_and_a_start_node():
    from src.engine.graph import TRIGGER_NODE_TYPES
    from src.engine.workflow_runner import WorkflowRunner, _TEST_TRIGGER_TYPES

    assert "FormTrigger" in _TEST_TRIGGER_TYPES and "FormTrigger" in TRIGGER_NODE_TYPES
    wf = build([nd("s", "Start"), form_node(), nd("a")], [("Refund form", "a"), ("s", "a")])
    assert WorkflowRunner().find_start_node(wf).name == "Refund form"
    assert WorkflowRunner().find_start_node(build([form_node()], [])).type == "FormTrigger"


def test_form_trigger_has_the_trigger_recipe_and_effect_none():
    from src.engine.effects import effect_of
    from src.engine.test_recipes import recipe_for

    r = recipe_for("FormTrigger")
    assert effect_of("FormTrigger") == "none"
    assert r.checks == ["the payload is the test input"] and r.categories == ["normal"]


def test_a_form_workflow_gets_suggested_tests_fed_by_the_form_fields():
    from src.engine.test_recipes import SuggestContext, suggest_for_workflow

    definition = {
        "nodes": [
            {"name": "Refund form", "type": "FormTrigger", "parameters": {"input_schema": {
                "type": "object", "properties": {"amount": {"type": "number"}}, "required": ["amount"]}}},
            {"name": "Big", "type": "If", "parameters": {"conditions": {"conditions": [
                {"left": "{{ $json.amount }}", "operator": "gt", "right": "100"}]}}},
        ],
        "connections": [{"source_node": "Refund form", "target_node": "Big"}],
    }
    drafts = suggest_for_workflow(definition, {}, SuggestContext(definition=definition), node="Refund form")
    assert drafts, "a FormTrigger must have a trigger recipe that proposes tests"
    assert all(d["target"]["kind"] == "workflow" for d in drafts)


async def test_a_workflow_target_test_starts_at_the_form_trigger_in_test_mode(world):
    from src.repositories.workflow_repository import WorkflowRepository, workflow_to_definition
    from src.services.test_runner import run_tests

    wf = build([form_node(), nd("Mark", "Set", mode="manual", fields=[{"name": "seen", "value": True, "type": "boolean"}])],
               [("Refund form", "Mark")], name="form-wf")
    stored = await world.create(wf)
    defn = workflow_to_definition(stored.workflow)
    defn["tests"] = [{
        "id": "t_0f0e1d2c", "name": "form submission flows through", "target": {"kind": "workflow"},
        "input": {"items": [{"amount": 7, "reason": "late"}]}, "mocks": {},
        "expect": [{"node": "Mark", "op": "status", "value": "success"},
                   {"node": "Mark", "op": "count", "value": 1}],
        "category": "normal", "status": "draft", "locked": False, "source": "human"}]
    async with world.sf() as s:
        report = await run_tests(WorkflowRepository(s), stored, defn, persist=False)
    res = report["results"][0]
    assert res["status"] == "passed", res


# ── 5. startup sweep: heartbeat staleness only ────────────────────────────────

async def crashed_resume(world: World):
    stored = await world.create(approval_flow())
    _, ctx = await world.run(stored.id)
    task = await world.task(ctx.execution_id)
    task_resumer.FAULT["after_claim"] = True
    await world.decide(task["id"])
    await world.settle()
    return ctx.execution_id, task


async def test_a_second_instance_never_fails_a_live_peers_resume(world, monkeypatch):
    exec_id, task = await crashed_resume(world)  # claimed by this process, heartbeat just written
    monkeypatch.setattr(task_resumer, "BOOT_ID", "the-second-instance")  # we are the peer: a different boot id
    await world.sql("UPDATE human_tasks SET resume_heartbeat = now() - interval '20 seconds' WHERE id = :i", i=task["id"])
    assert await task_resumer.startup_sweep(world.sf) == []
    assert await task_resumer.fail_interrupted(world.sf, startup=False) == []
    assert await task_resumer.fail_interrupted(world.sf, startup=True) == []
    assert (await world.task(exec_id))["resume_state"] == "running"
    assert (await world.execution(exec_id))["status"] == "running"


async def test_a_stale_heartbeat_is_still_resume_interrupted_at_startup_and_never_rerun(world, monkeypatch):
    exec_id, task = await crashed_resume(world)
    monkeypatch.setattr(task_resumer, "BOOT_ID", "restarted-process")
    await world.sql("UPDATE human_tasks SET resume_heartbeat = now() - interval '10 minutes' WHERE id = :i", i=task["id"])
    assert await task_resumer.startup_sweep(world.sf) == [task["id"]]
    ex = await world.execution(exec_id)
    assert ex["status"] == "failed" and ex["metadata"]["error_code"] == "RESUME_INTERRUPTED"
    assert (await world.task(exec_id))["resume_state"] == "failed"
    assert await task_resumer.startup_sweep(world.sf) == []
    await task_resumer.tick(world.sf)
    await world.settle()
    assert CALLS == ["up"]  # at-most-once: the decided branch never ran


async def test_a_resume_with_no_heartbeat_at_all_counts_as_stale(world, monkeypatch):
    exec_id, task = await crashed_resume(world)
    monkeypatch.setattr(task_resumer, "BOOT_ID", "other")
    await world.sql("UPDATE human_tasks SET resume_heartbeat = NULL WHERE id = :i", i=task["id"])
    assert await task_resumer.startup_sweep(world.sf) == [task["id"]]
