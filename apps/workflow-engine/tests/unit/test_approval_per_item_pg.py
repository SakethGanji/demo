"""Approval ``per_item``: one task per item, all open at once; the run resumes once, when the last one is settled.

Real-world shape: managers (one item each) -> Approval(per_item) -> approved / rejected / timed_out, and the escalation
chain timed_out -> reminder -> a second per_item Approval that asks only the people who did not answer."""

from __future__ import annotations

import asyncio

import pytest

from src.engine import task_resumer
from src.services.task_service import TaskService

from ._pg_world import CALLS, World, build, install_counter, make_world, nd, remove_counter

pytestmark = pytest.mark.anyio

MANAGERS_CODE = (
    'return [new_item({"team": "Ops", "email": "ana@bank.example"}), '
    'new_item({"team": "Risk", "email": "ben@bank.example"}), '
    'new_item({"team": "Sales", "email": "cy@bank.example"})]'
)


@pytest.fixture
async def world(pg):
    install_counter()
    try:
        yield await make_world(pg)
    finally:
        task_resumer.FAULT.clear()
        remove_counter()


@pytest.fixture
def notified(monkeypatch):
    """What the run asked the approver email to send: ``[(task_id, title, to)]`` (no SMTP)."""
    sent: list[tuple[str, str, list[str]]] = []

    async def fake(session, created, *, workflow_name, project_id):
        sent.extend((c["task"]["id"], c["task"]["title"], list(c["notify"]["to"])) for c in created)

    monkeypatch.setattr("src.services.approval_notify.notify_created_tasks", fake)
    return sent


def review(name: str = "review", **extra):
    params = dict(per_item=True, title="Review {{ $json.team }}", context={"team": "{{ $json.team }}"},
                  notify_email="{{ $json.email }}", assignees=["{{ $json.email }}"], due_in="1h", on_timeout="timed_out")
    params.update(extra)
    return nd(name, "Approval", **params)


def per_item_flow(code: str = MANAGERS_CODE, *, escalate: bool = False, **review_params):
    """start -> up -> managers(Code) -> review(Approval per_item) -> yes | no | late.
    With ``escalate``: review --timed_out--> remind -> review2(Approval per_item) -> yes2 | no2 | late2 (instead of late)."""
    nodes = [nd("start", "Start"), nd("up"), nd("managers", "Code", code=code), review(**review_params),
             nd("yes"), nd("no")]
    conns = [("start", "up"), ("up", "managers"), ("managers", "review"), ("review", "yes", "approved"),
             ("review", "no", "rejected")]
    if escalate:
        nodes += [nd("remind"), review("review2", title="Reminder: {{ $json.team }}"), nd("yes2"), nd("no2"), nd("late2")]
        conns += [("review", "remind", "timed_out"), ("remind", "review2"), ("review2", "yes2", "approved"),
                  ("review2", "no2", "rejected"), ("review2", "late2", "timed_out")]
    else:
        nodes.append(nd("late"))
        conns.append(("review", "late", "timed_out"))
    return build(nodes, conns, name="per-item")


async def tasks_at(world: World, exec_id: str, node: str = "review") -> list[dict]:
    rows = [t for t in await world.tasks_of(exec_id) if t["node_name"] == node]
    return sorted(rows, key=lambda t: t["item_index"])


async def node_output(world: World, exec_id: str, node: str) -> list[dict]:
    from src.repositories.node_output_repository import NodeOutputRepository

    async with world.sf() as s:
        rows = [r for r in await NodeOutputRepository(s).get_outputs(exec_id) if r.node_name == node]
    rows.sort(key=lambda r: r.run_index)
    return [i["json"] for r in rows for i in (r.output or [])]


async def expire(world: World, *task_ids: str) -> None:
    """The sweeper path: past the deadline, then one scheduler tick (which also kicks the resumes)."""
    for tid in task_ids:
        await world.sql("UPDATE human_tasks SET due_at = now() - interval '1 second' WHERE id = :i", i=tid)
    await asyncio.gather(*await task_resumer.tick(world.sf))
    await world.settle()


async def decide_without_kick(world: World, task_id: str, decision: str = "approved") -> None:
    async with world.sf() as s:
        await TaskService(s, kick_resume=False).decide(task_id, decision, None, None, "dana", "k-" + task_id)


async def start(world: World, flow) -> str:
    stored = await world.create(flow)
    _, ctx = await world.run(stored.id)
    return ctx.execution_id


# ── N items -> N tasks at once ────────────────────────────────────────────────

async def test_three_items_open_three_tasks_at_once_each_resolved_against_its_own_item(world, notified):
    exec_id = await start(world, per_item_flow())
    assert (await world.execution(exec_id))["status"] == "waiting"
    tasks = await tasks_at(world, exec_id)
    assert len(tasks) == 3 and {t["status"] for t in tasks} == {"open"}
    assert [t["item_index"] for t in tasks] == [0, 1, 2]
    assert len({t["run_index"] for t in tasks}) == 1  # one node-run, one group
    assert [t["title"] for t in tasks] == ["Review Ops", "Review Risk", "Review Sales"]
    assert [t["context"]["team"] for t in tasks] == ["Ops", "Risk", "Sales"]
    assert [t["assignees"] for t in tasks] == [["ana@bank.example"], ["ben@bank.example"], ["cy@bank.example"]]
    assert all(t["due_at"] is not None for t in tasks)
    by_id = {tid: (title, to) for tid, title, to in notified}
    assert [by_id[t["id"]] for t in tasks] == [
        ("Review Ops", ["ana@bank.example"]), ("Review Risk", ["ben@bank.example"]), ("Review Sales", ["cy@bank.example"]),
    ]
    assert CALLS == ["up"]


async def test_deciding_in_any_order_resumes_once_with_each_items_own_approval(world, notified):
    exec_id = await start(world, per_item_flow())
    t0, t1, t2 = await tasks_at(world, exec_id)

    await world.decide(t2["id"], "approved", by="cy", comment="fine")
    await world.settle()
    assert (await world.execution(exec_id))["status"] == "waiting" and CALLS == ["up"]
    await world.decide(t0["id"], "rejected", by="ana", comment="too much")
    await world.settle()
    assert (await world.execution(exec_id))["status"] == "waiting" and CALLS == ["up"]
    await world.decide(t1["id"], "approved", by="ben")
    await world.settle()

    assert (await world.execution(exec_id))["status"] == "success"
    assert CALLS == ["up", "yes", "no"]  # downstream ran once per port, on the combined outputs
    yes = await node_output(world, exec_id, "yes")
    assert [(i["team"], i["approval"]["decided_by"], i["approval"]["task_id"]) for i in yes] == [
        ("Risk", "ben", t1["id"]), ("Sales", "cy", t2["id"]),
    ]  # input order, each with its own task
    assert yes[1]["approval"]["comment"] == "fine" and yes[1]["approval"]["decision"] == "approved"
    [no] = await node_output(world, exec_id, "no")
    assert no["team"] == "Ops" and no["approval"] == {
        "decision": "rejected", "comment": "too much", "form_data": {}, "decided_by": "ana",
        "decided_at": no["approval"]["decided_at"], "task_id": t0["id"],
    }
    assert {t["resume_state"] for t in await tasks_at(world, exec_id)} == {"done"}
    assert await task_resumer.sweep_pending(world.sf, min_age_s=0) == []  # nothing left to resume


async def test_a_mix_of_approved_rejected_and_timed_out_via_the_sweeper(world, notified):
    exec_id = await start(world, per_item_flow())
    t0, t1, t2 = await tasks_at(world, exec_id)
    await world.decide(t0["id"], "approved")
    await world.decide(t1["id"], "rejected")
    await world.settle()
    assert (await world.execution(exec_id))["status"] == "waiting"

    await expire(world, t2["id"])

    assert (await world.execution(exec_id))["status"] == "success"
    assert CALLS == ["up", "yes", "no", "late"]
    assert [i["team"] for i in await node_output(world, exec_id, "yes")] == ["Ops"]
    assert [i["team"] for i in await node_output(world, exec_id, "no")] == ["Risk"]
    [late] = await node_output(world, exec_id, "late")
    assert late["team"] == "Sales" and late["approval"]["decision"] == "timed_out"
    assert late["approval"]["decided_by"] == "system:timeout" and late["approval"]["task_id"] == t2["id"]


async def test_on_timeout_reject_sends_an_expired_item_to_rejected(world, notified):
    exec_id = await start(world, per_item_flow(on_timeout="reject"))
    t0, t1, t2 = await tasks_at(world, exec_id)
    await world.decide(t1["id"], "approved")
    await world.settle()
    await expire(world, t0["id"], t2["id"])  # two expire in one sweep: still one resume
    assert (await world.execution(exec_id))["status"] == "success"
    assert CALLS == ["up", "yes", "no"]
    assert [i["team"] for i in await node_output(world, exec_id, "no")] == ["Ops", "Sales"]


# ── escalation: timed_out -> reminder -> a second per_item Approval ────────────

async def test_escalation_asks_only_the_timed_out_people_again(world, notified):
    exec_id = await start(world, per_item_flow(escalate=True))
    t0, t1, t2 = await tasks_at(world, exec_id)
    await world.decide(t0["id"], "approved")
    await world.settle()
    await expire(world, t1["id"], t2["id"])

    assert (await world.execution(exec_id))["status"] == "waiting"  # paused again, at the second Approval
    assert CALLS == ["up", "yes", "remind"]
    r1, r2 = await tasks_at(world, exec_id, "review2")
    assert [r["title"] for r in (r1, r2)] == ["Reminder: Risk", "Reminder: Sales"]
    assert [r["context"]["team"] for r in (r1, r2)] == ["Risk", "Sales"]
    assert {r["status"] for r in (r1, r2)} == {"open"}
    reminded = {tid: to for tid, _, to in notified}
    assert reminded[r1["id"]] == ["ben@bank.example"] and reminded[r2["id"]] == ["cy@bank.example"]

    await world.decide(r2["id"], "rejected")
    await world.decide(r1["id"], "approved")
    await world.settle()

    assert (await world.execution(exec_id))["status"] == "success"
    assert CALLS == ["up", "yes", "remind", "yes2", "no2"]
    assert [i["team"] for i in await node_output(world, exec_id, "yes")] == ["Ops"]
    [y2] = await node_output(world, exec_id, "yes2")
    assert y2["team"] == "Risk" and y2["approval"]["task_id"] == r1["id"]
    [n2] = await node_output(world, exec_id, "no2")
    assert n2["team"] == "Sales" and n2["approval"]["decision"] == "rejected"


# ── resume exactly once ───────────────────────────────────────────────────────

async def test_concurrent_decides_of_the_last_two_tasks_resume_once(world, notified):
    exec_id = await start(world, per_item_flow())
    t0, t1, t2 = await tasks_at(world, exec_id)
    await world.decide(t0["id"], "approved")
    await world.settle()
    await asyncio.gather(world.decide(t1["id"], "approved"), world.decide(t2["id"], "rejected"))
    await world.settle()
    assert (await world.execution(exec_id))["status"] == "success"
    assert CALLS == ["up", "yes", "no"]
    assert [i["team"] for i in await node_output(world, exec_id, "yes")] == ["Ops", "Risk"]


async def test_competing_claims_of_a_settled_group_resume_once(world, notified):
    exec_id = await start(world, per_item_flow())
    tasks = await tasks_at(world, exec_id)
    for t in tasks:
        await decide_without_kick(world, t["id"])
    outcomes = await asyncio.gather(*[task_resumer.claim_and_resume(t["id"], world.sf) for t in tasks for _ in range(2)])
    assert outcomes.count("resumed") == 1
    assert set(outcomes) <= {"resumed", "not_pending", "busy", "waiting"}
    await world.settle()
    assert CALLS == ["up", "yes"]
    assert len(await node_output(world, exec_id, "yes")) == 3
    assert await task_resumer.sweep_pending(world.sf, min_age_s=0) == []


async def test_a_sweeper_timeout_and_a_person_at_the_same_moment_resume_once(world, notified):
    exec_id = await start(world, per_item_flow())
    t0, t1, t2 = await tasks_at(world, exec_id)
    await world.decide(t0["id"], "approved")
    await world.settle()
    await world.sql("UPDATE human_tasks SET due_at = now() - interval '1 second' WHERE id = :i", i=t2["id"])
    started, _ = await asyncio.gather(task_resumer.tick(world.sf), world.decide(t1["id"], "approved"))
    await asyncio.gather(*started)
    await world.settle()
    assert (await world.execution(exec_id))["status"] == "success"
    assert CALLS == ["up", "yes", "late"]
    assert [i["team"] for i in await node_output(world, exec_id, "yes")] == ["Ops", "Risk"]


async def test_a_partly_decided_group_is_resumed_by_the_sweep_once_the_last_is_decided(world, notified):
    exec_id = await start(world, per_item_flow())
    t0, t1, t2 = await tasks_at(world, exec_id)
    await decide_without_kick(world, t0["id"])
    await decide_without_kick(world, t1["id"])
    await asyncio.gather(*await task_resumer.sweep_pending(world.sf, min_age_s=0))
    assert (await world.execution(exec_id))["status"] == "waiting" and CALLS == ["up"]
    await decide_without_kick(world, t2["id"], "rejected")
    await asyncio.gather(*await task_resumer.sweep_pending(world.sf, min_age_s=0))
    await world.settle()
    assert (await world.execution(exec_id))["status"] == "success"
    assert CALLS == ["up", "yes", "no"]


# ── edges: per_item false, 0 items, 1 item ────────────────────────────────────

async def test_per_item_false_is_one_task_for_every_item(world, notified):
    exec_id = await start(world, per_item_flow(per_item=False, title="Review all"))
    [t] = await tasks_at(world, exec_id)
    assert t["title"] == "Review all" and t["item_index"] == 0
    await world.decide(t["id"], "approved")
    await world.settle()
    assert (await world.execution(exec_id))["status"] == "success"
    yes = await node_output(world, exec_id, "yes")
    assert [i["team"] for i in yes] == ["Ops", "Risk", "Sales"]
    assert {i["approval"]["task_id"] for i in yes} == {t["id"]}


async def test_zero_items_make_no_tasks_and_emit_nothing(world, notified):
    exec_id = await start(world, per_item_flow(code="return []"))
    assert await world.tasks_of(exec_id) == []
    assert (await world.execution(exec_id))["status"] == "success"
    assert CALLS == ["up"]


async def test_one_item_behaves_like_a_plain_approval(world, notified):
    exec_id = await start(world, per_item_flow(code='return [new_item({"team": "Ops", "email": "ana@bank.example"})]'))
    [t] = await tasks_at(world, exec_id)
    assert t["title"] == "Review Ops"
    await world.decide(t["id"], "rejected")
    await world.settle()
    assert (await world.execution(exec_id))["status"] == "success"
    [no] = await node_output(world, exec_id, "no")
    assert no["team"] == "Ops" and no["approval"]["task_id"] == t["id"]
    assert CALLS == ["up", "no"]
