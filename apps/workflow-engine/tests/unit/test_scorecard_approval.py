"""Scorecard and test runs with an Approval (Stage 5, DESIGN-STAGE5-UIS 5.7): ports count as branches, a mocked approval
never waits, the planted-bug score catches tests that ignore the decision."""

from __future__ import annotations

import hashlib

import pytest

from src.services.scorecard import branches_of

from ._pg_world import build, make_world, nd

pytestmark = pytest.mark.anyio


def N(name, type_, params=None, **kw):
    return {"name": name, "type": type_, "parameters": params or {}, **kw}


def test_branches_of_lists_the_approval_decision_ports():
    d = {"nodes": [N("A", "Approval", {"due_in": "1h", "on_timeout": "approve"}), N("B", "Approval"), N("C", "Approval", {"on_timeout": "reject"}),
                   N("Gate", "If"), N("Off", "Approval", disabled=True)]}
    got = branches_of(d)
    assert [p for n, p in got if n == "A"] == ["approved", "rejected", "timed_out"]  # a deadline: it can time out
    assert [p for n, p in got if n == "B"] == ["approved", "rejected", "timed_out"]  # default on_timeout is the timed_out port
    assert [p for n, p in got if n == "C"] == ["approved", "rejected"]  # reject on timeout: no timed_out port
    assert [p for n, p in got if n == "Gate"] == ["true", "false"]
    assert not [1 for n, _ in got if n == "Off"]


def test_error_output_adds_an_error_branch_to_an_approval():
    got = branches_of({"nodes": [N("A", "Approval", on_error="error_output")]})
    assert [p for _, p in got] == ["approved", "rejected", "timed_out", "error"]


@pytest.fixture
async def world(pg):
    yield await make_world(pg)


def flow():
    return build(
        [nd("Start", "Start"), nd("Review", "Approval", due_in="1h"),
         nd("Pay", "Set", mode="manual", fields=[{"name": "paid", "value": True, "type": "boolean"}]),
         nd("Refuse", "Set", mode="manual", fields=[{"name": "paid", "value": False, "type": "boolean"}]),
         nd("Late", "Set", mode="manual", fields=[{"name": "paid", "value": False, "type": "boolean"}])],
        [("Start", "Review"), ("Review", "Pay", "approved"), ("Review", "Refuse", "rejected"), ("Review", "Late", "timed_out")],
        name="scored",
    )


def decision_test(port, node, *, check_node=True):
    approval = {"decision": port, "comment": "", "form_data": {}, "decided_by": "t", "decided_at": "2026-01-01T09:00:00+00:00", "task_id": "tsk_t"}
    expect = [{"node": "Review", "port": port, "op": "count", "value": 1}]
    if check_node:
        expect.append({"node": node, "op": "status", "value": "success"})
    return {"id": "t_" + hashlib.md5(f"{port}{check_node}".encode()).hexdigest()[:8], "name": f"{port} path", "target": {"kind": "workflow"},
            "input": {"items": [{"amount": 5}]}, "mocks": {"Review": {"items": [{"amount": 5, "approval": approval}], "port": port}},
            "expect": expect, "category": "normal", "status": "draft", "locked": False, "source": "human"}


async def stored_with(world, tests):
    from src.repositories.workflow_repository import workflow_to_definition

    stored = await world.create(flow())
    defn = workflow_to_definition(stored.workflow)
    defn["tests"] = tests
    return stored, defn


async def test_tests_with_a_mocked_approval_pass_and_create_nothing_to_wait_for(world):
    from src.repositories.workflow_repository import WorkflowRepository
    from src.services.test_runner import run_tests

    stored, defn = await stored_with(world, [decision_test("approved", "Pay"), decision_test("rejected", "Refuse"),
                                             decision_test("timed_out", "Late")])
    async with world.sf() as s:
        report = await run_tests(WorkflowRepository(s), stored, defn, persist=True)
    assert report["summary"]["passed"] == 3 and report["summary"]["total"] == 3
    assert await world.sql("SELECT id FROM human_tasks") == []
    assert await world.sql("SELECT id FROM executions WHERE workflow_id = :w AND status = 'waiting'", w=stored.id) == []
    assert {r[0] for r in await world.sql("SELECT status FROM executions WHERE workflow_id = :w", w=stored.id)} <= {"success"}


async def test_an_unmocked_approval_in_a_test_fails_with_test_unmocked_effect(world):
    from src.repositories.workflow_repository import WorkflowRepository
    from src.services.test_runner import run_tests

    t = decision_test("approved", "Pay")
    t["mocks"] = {}
    stored, defn = await stored_with(world, [t])
    async with world.sf() as s:
        report = await run_tests(WorkflowRepository(s), stored, defn, persist=False)
    res = report["results"][0]
    assert res["status"] != "passed" and any("TEST_UNMOCKED_EFFECT" in str(f) or "human" in str(f) for f in res["failures"])
    assert await world.sql("SELECT id FROM human_tasks") == []


async def test_the_scorecard_counts_approval_ports_and_catches_a_swapped_decision(world):
    from src.repositories.workflow_repository import WorkflowRepository
    from src.services.scorecard import score

    stored, defn = await stored_with(world, [decision_test("approved", "Pay"), decision_test("rejected", "Refuse"),
                                             decision_test("timed_out", "Late")])
    async with world.sf() as s:
        card = await score(WorkflowRepository(s), stored, defn, lookups={}, mutations=True)
    cov = card["coverage"]
    assert cov["branches_total"] == 3 and cov["branches_taken"] == 3 and cov["untaken"] == []
    swap = [m for m in card["mutation"]["survived"] if m["operator"] == "swap_branches"]
    assert swap == []  # the tests check which branch ran, so the swapped decision is caught
    assert card["mutation"]["caught"] >= 1


async def test_a_test_that_ignores_the_decision_lets_the_swap_survive(world):
    from src.repositories.workflow_repository import WorkflowRepository
    from src.services.scorecard import score

    stored, defn = await stored_with(world, [decision_test("approved", "Pay", check_node=False)])
    async with world.sf() as s:
        card = await score(WorkflowRepository(s), stored, defn, lookups={}, mutations=True)
    survived = [m for m in card["mutation"]["survived"] if m["operator"] == "swap_branches"]
    assert [m["node"] for m in survived] == ["Review"]
    cov = card["coverage"]
    assert cov["branches_taken"] == 1 and {u["port"] for u in cov["untaken"]} == {"rejected", "timed_out"}
