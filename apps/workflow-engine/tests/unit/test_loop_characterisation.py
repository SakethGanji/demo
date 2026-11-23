"""Characterisation of how a Loop and a Poll (repeat until) run today, so the per-item history and the new loop
settings cannot change it: the outputs on ``loop`` / ``done``, the item order, what ``$node`` sees, the run counts,
and that a failed step inside the body stops the run. Real Loop / If / Poll nodes, fake body steps, no Postgres."""

from __future__ import annotations

import asyncio

import pytest

from src.engine.node_registry import node_registry, register_all_nodes
from src.engine.types import NodeData, NodeExecutionResult
from src.engine.workflow_runner import WorkflowRunner
from tests.unit._runner_fakes import items, node, wf

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


class Check:
    """A body step: fails for items with ``fail``; waits ``delay`` seconds first (to scramble finishing order)."""

    input_count = 1

    def __init__(self, log):
        self.log = log

    async def execute(self, context, node_def, input_data):
        j = input_data[0].json if input_data else {}
        await asyncio.sleep(float(j.get("delay") or 0))
        self.log.append((node_def.name, j.get("id")))
        if j.get("fail"):
            raise RuntimeError(f"check failed for {j.get('id')}")
        return NodeExecutionResult(outputs={"main": [NodeData(json={**i.json, "checked": True}) for i in input_data]})


class Mark:
    input_count = 1

    def __init__(self, log):
        self.log = log

    async def execute(self, context, node_def, input_data):
        self.log.append((node_def.name, input_data[0].json.get("id") if input_data else None))
        return NodeExecutionResult(outputs={"main": [NodeData(json={**i.json, "mark": node_def.name}) for i in input_data]})


class Registry:
    def __init__(self, log):
        if not node_registry.has("Loop"):
            register_all_nodes()
        self._fake = {"Check": Check(log), "Mark": Mark(log)}

    def get(self, t):
        return self._fake.get(t) or node_registry.get(t)


def runner():
    log: list = []
    r = WorkflowRunner()
    r._registry = Registry(log)
    return r, log


def loop_wf(loop_params=None, check_kw=None):
    return wf(
        [
            node("Start", "Mark"),
            node("Each", "Loop", parameters={"batchSize": 1, **(loop_params or {})}),
            node("Check", "Check", **(check_kw or {})),
            node("Big", "If", parameters={"condition": "{{ $json.amount > 100 }}"}),
            node("Flag", "Mark"),
            node("Release", "Mark"),
            node("Summary", "Mark"),
        ],
        [
            ("Start", "Each"),
            ("Each", "Check", "loop"),
            ("Check", "Big"),
            ("Big", "Flag", "true"),
            ("Big", "Release", "false"),
            ("Flag", "Each"),
            ("Release", "Each"),
            ("Each", "Summary", "done"),
        ],
    )


ROWS = [{"id": "w1", "amount": 50}, {"id": "w2", "amount": 500}, {"id": "w3", "amount": 20}]


def plain(ctx, name):
    return [d.json for d in ctx.node_states.get(name, [])]


async def test_loop_done_carries_every_item_once_in_order():
    r, log = runner()
    ctx = await r.run(loop_wf(), "Start", items(*ROWS))
    assert not ctx.errors
    done = plain(ctx, "Summary")
    assert len(done) == 1
    assert done[0]["_loop_completed"] is True and done[0]["total_items"] == 3
    assert [i["id"] for i in done[0]["items"]] == ["w1", "w2", "w3"]
    assert done[0]["mark"] == "Summary"
    # the body ran once per item, in item order, and each branch step saw its own item
    assert [x for x in log if x[0] == "Check"] == [("Check", "w1"), ("Check", "w2"), ("Check", "w3")]
    assert [x for x in log if x[0] in ("Flag", "Release")] == [("Release", "w1"), ("Flag", "w2"), ("Release", "w3")]


async def test_loop_node_states_keep_the_last_pass_only():
    r, _ = runner()
    ctx = await r.run(loop_wf(), "Start", items(*ROWS))
    assert plain(ctx, "Check") == [{"id": "w3", "amount": 20, "mark": "Start", "checked": True}]
    assert plain(ctx, "Flag") == [{"id": "w2", "amount": 500, "checked": True, "mark": "Flag"}]
    assert plain(ctx, "Release")[0]["id"] == "w3"
    assert plain(ctx, "Each")[0]["_loop_completed"] is True
    assert ctx.node_run_counts["Each"] == 4 and ctx.node_run_counts["Check"] == 3
    assert ctx.node_run_counts["Flag"] == 1 and ctx.node_run_counts["Release"] == 2
    assert "Each" not in ctx.node_internal_state


async def test_loop_batches_with_a_split_body_come_round_once_per_branch():
    """Today's behaviour, quirks and all: a batch whose items split across both branches returns to the Loop once per
    branch, so the Loop advances twice and ``done`` fires twice (the second time with what is left)."""
    r, log = runner()
    ctx = await r.run(loop_wf({"batchSize": 2}), "Start", items(*ROWS))
    assert not ctx.errors
    assert log == [("Start", "w1"), ("Check", "w1"), ("Flag", "w2"), ("Release", "w1"), ("Summary", None),
                   ("Check", "w3"), ("Release", "w3"), ("Check", "w3"), ("Release", "w3"), ("Summary", None)]
    assert ctx.node_run_counts == {"Start": 1, "Each": 5, "Check": 3, "Big": 3, "Flag": 1, "Release": 3, "Summary": 2}
    assert plain(ctx, "Summary")[0]["total_items"] == 1


async def test_loop_batches_linear_body():
    r, log = runner()
    w = wf([node("Start", "Mark"), node("Each", "Loop", parameters={"batchSize": 2}), node("Check", "Check"), node("Summary", "Mark")],
           [("Start", "Each"), ("Each", "Check", "loop"), ("Check", "Each"), ("Each", "Summary", "done")])
    ctx = await r.run(w, "Start", items(*ROWS))
    assert not ctx.errors
    assert [x for x in log if x[0] == "Check"] == [("Check", "w1"), ("Check", "w3")]  # the batch's first item drives the fake
    assert plain(ctx, "Check") == [{"id": "w3", "amount": 20, "mark": "Start", "checked": True}]
    assert plain(ctx, "Summary")[0]["total_items"] == 3
    assert ctx.node_run_counts["Each"] == 3


async def test_loop_max_iterations():
    r, _ = runner()
    ctx = await r.run(loop_wf({"maxIterations": 2}), "Start", items({"id": "x", "amount": 1}))
    done = plain(ctx, "Summary")[0]
    assert done["total_items"] == 2 and [i["i"] for i in done["items"]] == [0, 1]


async def test_a_failed_step_inside_the_loop_stops_the_run():
    r, log = runner()
    rows = [{"id": "w1", "amount": 50}, {"id": "w2", "amount": 500, "fail": True}, {"id": "w3", "amount": 20}]
    ctx = await r.run(loop_wf(), "Start", items(*rows))
    assert [e.node_name for e in ctx.errors] == ["Check"]
    assert "check failed for w2" in ctx.errors[0].error
    assert ("Check", "w3") not in log and "Summary" not in ctx.node_states
    assert ctx.node_metrics["Check"]["status"] == "error"


async def test_on_error_continue_inside_the_loop_keeps_going():
    """Today: the failed step's error stays on the run, its item goes on as ``{error, _errorNode}``, the loop finishes."""
    r, log = runner()
    rows = [{"id": "w1", "amount": 50}, {"id": "w2", "amount": 500, "fail": True}, {"id": "w3", "amount": 20}]
    ctx = await r.run(loop_wf(check_kw={"on_error": "continue"}), "Start", items(*rows))
    assert [(e.node_name, e.error) for e in ctx.errors] == [("Check", "check failed for w2")]
    assert plain(ctx, "Summary")[0]["total_items"] == 3
    assert log == [("Start", "w1"), ("Check", "w1"), ("Release", "w1"), ("Check", "w2"), ("Flag", None), ("Check", "w3"),
                   ("Release", "w3"), ("Summary", None)]
    assert plain(ctx, "Flag") == [{"error": "check failed for w2", "_errorNode": "Check", "mark": "Flag"}]


async def test_retry_on_fail_inside_the_loop():
    r, log = runner()
    rows = [{"id": "w1", "amount": 50, "fail": True}]
    ctx = await r.run(loop_wf(check_kw={"retry_on_fail": 2, "retry_delay": 0}), "Start", items(*rows))
    assert [x for x in log if x[0] == "Check"] == [("Check", "w1")] * 3
    assert ctx.errors and "(after 3 attempts)" in ctx.errors[0].error


def poll_wf(condition, timeout=30):
    return wf(
        [
            node("Start", "Mark"),
            node("Until", "Poll", parameters={"condition": condition, "interval": 1, "timeout": timeout}),
            node("Bump", "Set", parameters={"mode": "manual", "fields": [
                {"name": "attempt", "value": "{{ ($json.attempt or 0) + 1 }}"}]}),
            node("Done", "Mark"),
            node("Late", "Mark"),
        ],
        [("Start", "Until"), ("Until", "Bump", "loop"), ("Bump", "Until"), ("Until", "Done", "done"), ("Until", "Late", "timeout")],
    )


async def test_poll_repeats_until_the_condition_holds(monkeypatch):
    async def no_sleep(_s):
        return None

    monkeypatch.setattr("src.nodes.flow.poll.asyncio.sleep", no_sleep)
    r, _ = runner()
    ctx = await r.run(poll_wf("{{ $json.attempt >= 3 }}"), "Start", items({"id": "p1"}))
    assert not ctx.errors
    assert plain(ctx, "Done")[0]["attempt"] == 3
    assert "Late" not in ctx.node_states
    assert ctx.node_run_counts["Until"] == 4


async def test_poll_condition_true_at_once():
    r, _ = runner()
    ctx = await r.run(poll_wf("{{ true }}"), "Start", items({"id": "p1"}))
    assert plain(ctx, "Done") == [{"id": "p1", "mark": "Done"}]
    assert "Bump" not in ctx.node_states
