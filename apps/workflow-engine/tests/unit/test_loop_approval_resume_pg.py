"""A Loop whose body pauses at an Approval keeps its pending items across every pause/resume.

Real-world shape: rows -> Code (group into N items) -> Loop(batchSize 1) --loop--> Approval --approved/rejected--> ... ->
write --main--> back to Loop, Loop --done--> summary. Each pause stores a resume snapshot and the run is re-entered in a
fresh runner; the Loop's per-execution state (``node_internal_state``) must travel with that snapshot, or the second
iteration emits the loop-back item as though it were the next item."""

from __future__ import annotations

import asyncio

import pytest

from src.engine import task_resumer

from ._pg_world import CALLS, World, build, install_counter, make_world, nd, remove_counter

pytestmark = pytest.mark.anyio

GROUP_CODE = 'return [new_item({"group": "A", "n": 1}), new_item({"group": "B", "n": 2})]'


@pytest.fixture
async def world(pg):
    install_counter()
    try:
        yield await make_world(pg)
    finally:
        task_resumer.FAULT.clear()
        remove_counter()


def loop_with_approval(*, reminder: bool = False):
    """start -> rows -> group(Code, 2 items) -> loop --loop--> review(Approval) --approved/rejected--> after -> write -> loop;
    loop --done--> summary. With ``reminder``: review --timed_out--> remind -> review2(Approval) --approved--> after."""
    nodes = [
        nd("start", "Start"), nd("rows"), nd("group", "Code", code=GROUP_CODE), nd("loop", "Loop", batchSize=1),
        nd("review", "Approval", context={"group": "{{ $json.group }}", "n": "{{ $json.n }}"}, due_in="1h",
           on_timeout="timed_out"),
        nd("after"), nd("write"), nd("summary"),
    ]
    conns = [
        ("start", "rows"), ("rows", "group"), ("group", "loop"), ("loop", "review", "loop"),
        ("review", "after", "approved"), ("review", "after", "rejected"), ("after", "write"), ("write", "loop"),
        ("loop", "summary", "done"),
    ]
    if reminder:
        nodes += [nd("remind"), nd("review2", "Approval", context={"group": "{{ $json.group }}", "again": True})]
        conns += [("review", "remind", "timed_out"), ("remind", "review2"), ("review2", "after", "approved")]
    return build(nodes, conns, name="loop-approval")


async def open_tasks(world: World, exec_id: str, node: str) -> list[dict]:
    return [t for t in await world.tasks_of(exec_id) if t["node_name"] == node]


async def node_output(world: World, exec_id: str, node: str) -> list[dict]:
    from src.repositories.node_output_repository import NodeOutputRepository

    async with world.sf() as s:
        rows = [r for r in await NodeOutputRepository(s).get_outputs(exec_id) if r.node_name == node]
    rows.sort(key=lambda r: r.run_index)
    return [i["json"] for r in rows for i in (r.output or [])]


async def test_each_loop_item_gets_its_own_approval_and_done_fires_once_after_both(world):
    stored = await world.create(loop_with_approval())
    _, ctx = await world.run(stored.id)
    exec_id = ctx.execution_id
    assert (await world.execution(exec_id))["status"] == "waiting"

    [t1] = await open_tasks(world, exec_id, "review")
    assert t1["context"]["group"] == "A" and t1["context"]["n"] == 1
    await world.decide(t1["id"], "approved")
    await world.settle()

    assert (await world.execution(exec_id))["status"] == "waiting"  # paused again on the second item
    tasks = await open_tasks(world, exec_id, "review")
    assert len(tasks) == 2
    t2 = next(t for t in tasks if t["id"] != t1["id"])
    assert t2["context"]["group"] == "B" and t2["context"]["n"] == 2  # its own data, not the loop-back item
    assert "summary" not in CALLS

    await world.decide(t2["id"], "rejected")
    await world.settle()

    assert (await world.execution(exec_id))["status"] == "success"
    assert CALLS == ["rows", "after", "write", "after", "write", "summary"]  # done fired once, after both
    writes = await node_output(world, exec_id, "write")
    assert [(w["group"], w["approval"]["decision"]) for w in writes] == [("A", "approved"), ("B", "rejected")]
    [summary] = await node_output(world, exec_id, "summary")
    assert summary["_loop_completed"] is True and summary["total_items"] == 2
    assert [i["group"] for i in summary["items"]] == ["A", "B"]


async def test_a_timeout_then_a_second_approval_inside_one_iteration_keeps_the_loop_items(world):
    stored = await world.create(loop_with_approval(reminder=True))
    _, ctx = await world.run(stored.id)
    exec_id = ctx.execution_id

    [t1] = await open_tasks(world, exec_id, "review")
    assert t1["context"]["group"] == "A"
    # the first approval expires: the run takes timed_out -> remind -> a second Approval (pause #2 in iteration 1)
    await world.sql("UPDATE human_tasks SET due_at = now() - interval '1 second' WHERE id = :i", i=t1["id"])
    await asyncio.gather(*await task_resumer.tick(world.sf))
    await world.settle()
    assert (await world.execution(exec_id))["status"] == "waiting"
    [r2] = await open_tasks(world, exec_id, "review2")
    assert r2["context"]["group"] == "A" and r2["context"]["again"] is True

    await world.decide(r2["id"], "approved")
    await world.settle()

    # iteration 2 reaches the first Approval again with item B
    tasks = await open_tasks(world, exec_id, "review")
    assert len(tasks) == 2
    t2 = next(t for t in tasks if t["id"] != t1["id"])
    assert t2["context"]["group"] == "B" and t2["context"]["n"] == 2
    assert "summary" not in CALLS

    await world.decide(t2["id"], "approved")
    await world.settle()

    assert (await world.execution(exec_id))["status"] == "success"
    assert CALLS == ["rows", "remind", "after", "write", "after", "write", "summary"]
    writes = await node_output(world, exec_id, "write")
    assert [w["group"] for w in writes] == ["A", "B"]
    [summary] = await node_output(world, exec_id, "summary")
    assert summary["total_items"] == 2
