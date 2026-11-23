"""Stage 8 T1 (tester): the engine side of sub-workflow calls at its edges, with no database (DESIGN-STAGE8-REUSE 2.2, 2.3, 3.4-3.5).

A three-workflow cycle, the exact depth boundary, a timeout that must reach the awaiting node, hostile parameter values, and the
pairing metadata when the child returns a different number of items than it was given."""

from __future__ import annotations

import asyncio
import math

import pytest

from src.engine import subworkflow as sw
from src.engine.types import NodeData, NodeExecutionResult, StoredWorkflow
from src.engine.workflow_runner import WorkflowRunner
from src.nodes.flow.execute_workflow import ExecuteWorkflowNode, MAX_TIMEOUT_SECONDS, normalize_params
from src.nodes.triggers.execute_workflow_trigger import ExecuteWorkflowTriggerNode

from ._runner_fakes import FakeRegistry, items, node, wf
from .test_stage8_b1_subworkflow import NOW, Repo, named, run_parent

pytestmark = pytest.mark.anyio

CALL = {"version": "draft", "inputMode": "passThrough"}


class Napper:
    """An awaiting node that records whether it was cancelled while asleep."""

    input_count = 1

    def __init__(self, seen: dict):
        self.seen = seen

    async def execute(self, context, node_def, input_data):
        self.seen["started"] = True
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            self.seen["cancelled"] = True
            raise
        return NodeExecutionResult(outputs={"main": input_data})


class Fan:
    """Returns ``count`` items per call, whatever it was given."""

    input_count = 1

    async def execute(self, context, node_def, input_data):
        n = int(node_def.parameters.get("count", 1))
        return NodeExecutionResult(outputs={"main": [NodeData(json={"k": k, "via": node_def.name}) for k in range(n)]})


def runner_with(**extra):
    log: list = []
    r = WorkflowRunner()
    reg = FakeRegistry(log)
    reg._nodes.update({"ExecuteWorkflow": ExecuteWorkflowNode(), "ExecuteWorkflowTrigger": ExecuteWorkflowTriggerNode(), **extra})
    r._registry = reg
    return r, log


def link(wid: str, target: str | None, name: str | None = None):
    """trigger -> call(target)   (or trigger -> work when target is None)."""
    if target is None:
        return named(wf([node("trigger", "ExecuteWorkflowTrigger"), node("work")], [("trigger", "work")]), wid, name or wid)
    return named(wf([node("trigger", "ExecuteWorkflowTrigger"), node("go", "ExecuteWorkflow", parameters={**CALL, "workflowId": target})],
                    [("trigger", "go")]), wid, name or wid)


def error_chain(ctx) -> str:
    """The deepest message in a nested SUBWORKFLOW_FAILED chain."""
    e = ctx.errors[0]
    return (e.details or {}).get("child_error") or e.error


# ── cycles and depth ────────────────────────────────────────────────────────────────────────────────────────────────


async def test_a_cycle_through_three_workflows_names_the_whole_loop():
    runner, _ = runner_with()
    a, b, c = link("wf_a", "wf_b", "Alpha"), link("wf_b", "wf_c", "Beta"), link("wf_c", "wf_a", "Gamma")
    ctx = await run_parent(runner, a, Repo(a, b, c), start="trigger")
    assert ctx.errors and ctx.errors[0].error_code == sw.SUBWORKFLOW_FAILED
    assert "'Alpha' → 'Beta' → 'Gamma' → 'Alpha': a workflow can't call itself, directly or indirectly." in ctx.errors[0].error


async def test_a_cycle_that_does_not_include_the_root_is_still_caught_and_the_root_is_not_in_the_message():
    runner, _ = runner_with()
    root = link("wf_root", "wf_b", "Root")
    b, c = link("wf_b", "wf_c", "Beta"), link("wf_c", "wf_b", "Gamma")        # Root -> Beta -> Gamma -> Beta
    ctx = await run_parent(runner, root, Repo(root, b, c), start="trigger")
    assert ctx.errors
    msg = ctx.errors[0].error
    assert "'Beta' → 'Gamma' → 'Beta'" in msg and "'Root' →" not in msg.split("a workflow can't")[0].split("failed at 'go': ")[-1]


async def test_the_depth_limit_is_exactly_ten_calls_below_the_root():
    runner, log = runner_with()

    def chain(n: int):
        ws = [link(f"w{i}", f"w{i + 1}") for i in range(n)]
        ws.append(link(f"w{n}", None))
        return ws

    ok = chain(10)                                                            # root + ten children: the tenth runs 'work'
    ctx = await run_parent(runner, ok[0], Repo(*ok), start="trigger")
    assert ctx.errors == [] and "work" in log
    log.clear()
    too_deep = chain(11)
    ctx = await run_parent(runner, too_deep[0], Repo(*too_deep), start="trigger")
    assert ctx.errors and "work" not in log
    assert "nested more than 10 deep" in str(ctx.errors[0].details) + ctx.errors[0].error


async def test_a_timeout_reaches_the_node_that_is_awaiting_in_the_child():
    seen: dict = {}
    runner, _ = runner_with(Nap=Napper(seen))
    child = named(wf([node("trigger", "ExecuteWorkflowTrigger"), node("nap", "Nap")], [("trigger", "nap")]), "wf_child", "Sleepy")
    parent = named(wf([node("start"), node("call", "ExecuteWorkflow", parameters={**CALL, "workflowId": "wf_child", "timeoutSeconds": 0.05})],
                      [("start", "call")]), "wf_parent", "Parent")
    t0 = asyncio.get_running_loop().time()
    ctx = await run_parent(runner, parent, Repo(child))
    assert asyncio.get_running_loop().time() - t0 < 3
    assert ctx.errors[0].error_code == sw.SUBWORKFLOW_TIMEOUT
    assert seen == {"started": True, "cancelled": True}


# ── hostile parameter values ────────────────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("raw", [float("inf"), "inf", -1, 0, "0", "", None, True, False, "abc", [], {}, 1e308, "1e999"])
def test_the_timeout_is_always_a_finite_positive_number_within_the_cap(raw):
    t = normalize_params({"workflowId": "w", "version": "latest", "timeoutSeconds": raw})["timeoutSeconds"]
    assert isinstance(t, float) and math.isfinite(t) and 0 < t <= MAX_TIMEOUT_SECONDS, (raw, t)


@pytest.mark.parametrize("raw", [float("nan"), "nan", "NaN"])
def test_a_nan_timeout_is_not_kept(raw):
    t = normalize_params({"workflowId": "w", "version": "latest", "timeoutSeconds": raw})["timeoutSeconds"]
    assert math.isfinite(t) and 0 < t <= MAX_TIMEOUT_SECONDS


@pytest.mark.xfail(strict=True, raises=AssertionError, reason="S8-6 (fixed for the value): NaN is now replaced by the 300s default, so this child (asleep 30s) is "
                   "not cut off within the test's 2s wait. The test can only pass if NaN meant a sub-2s timeout, which no sane default is; "
                   "test_a_nan_timeout_is_not_kept covers the defect itself")
async def test_a_nan_timeout_still_cuts_off_a_child_that_never_finishes():
    seen: dict = {}
    runner, _ = runner_with(Nap=Napper(seen))
    child = named(wf([node("trigger", "ExecuteWorkflowTrigger"), node("nap", "Nap")], [("trigger", "nap")]), "wf_child", "Sleepy")
    parent = named(wf([node("start"), node("call", "ExecuteWorkflow", parameters={**CALL, "workflowId": "wf_child", "timeoutSeconds": "nan"})],
                      [("start", "call")]), "wf_parent", "Parent")
    try:
        ctx = await asyncio.wait_for(run_parent(runner, parent, Repo(child)), 2)
    except asyncio.TimeoutError:
        raise AssertionError("the child was never cut off") from None
    assert ctx.errors and ctx.errors[0].error_code == sw.SUBWORKFLOW_TIMEOUT


@pytest.mark.parametrize("raw,want", [("latest", "latest"), (" LATEST ", "latest"), ("Draft", "draft"), (3, 3), (3.0, 3), ("3", 3), (" 12 ", 12)])
def test_versions_that_are_accepted(raw, want):
    assert normalize_params({"version": raw})["version"] == want


@pytest.mark.parametrize("raw", [0, -1, 1.5, "0", "-2", "1.5", "v2", "latest!", True, False, [], {}, "٣٣٣x", float("nan")])
def test_versions_that_are_refused_with_a_message_not_a_crash(raw):
    with pytest.raises(ValueError) as e:
        normalize_params({"version": raw})
    assert "version" in str(e.value).lower()


@pytest.mark.parametrize("inputs", ['{"a": ', "not json", "[1, 2]", "null", "5"])
async def test_garbage_in_the_inputs_parameter_is_a_node_error_or_ignored_never_an_unhandled_crash(inputs):
    runner, _ = runner_with()
    child = link("wf_child", None)
    parent = named(wf([node("start"), node("call", "ExecuteWorkflow", parameters={
        "workflowId": "wf_child", "version": "draft", "inputMode": "map", "inputs": inputs})], [("start", "call")]), "wf_parent", "Parent")
    ctx = await run_parent(runner, parent, Repo(child))
    assert ctx.status.value in ("success", "failed") if hasattr(ctx, "status") else True
    for e in ctx.errors:
        assert e.node_name == "call" and e.error


# ── pairing when the child returns another number of items ──────────────────────────────────────────────────────────


async def test_once_mode_pairs_by_position_only_when_the_counts_match():
    runner, _ = runner_with(Fan=Fan())
    for produced, expect in ((3, [0, 1, 2]), (2, [None, None]), (5, [None] * 5)):
        child = named(wf([node("trigger", "ExecuteWorkflowTrigger"), node("fan", "Fan", parameters={"count": produced})], [("trigger", "fan")]),
                      "wf_child", "Fan")
        parent = named(wf([node("start"), node("call", "ExecuteWorkflow", parameters={**CALL, "workflowId": "wf_child"})], [("start", "call")]),
                       "wf_parent", "Parent")
        ctx = await run_parent(runner, parent, Repo(child), data=items({"i": 0}, {"i": 1}, {"i": 2}))
        assert ctx.errors == []
        assert [o.meta.get("pairedItem") for o in ctx.node_states["call"]] == expect, produced


async def test_each_mode_pairs_every_output_with_the_input_item_that_made_it_even_when_counts_differ():
    runner, _ = runner_with(Fan=Fan())
    child = named(wf([node("trigger", "ExecuteWorkflowTrigger"), node("fan", "Fan", parameters={"count": 2})], [("trigger", "fan")]),
                  "wf_child", "Fan")
    parent = named(wf([node("start"), node("call", "ExecuteWorkflow", parameters={**CALL, "workflowId": "wf_child", "mode": "each"})],
                      [("start", "call")]), "wf_parent", "Parent")
    ctx = await run_parent(runner, parent, Repo(child), data=items({"i": 0}, {"i": 1}, {"i": 2}))
    assert [o.meta["pairedItem"] for o in ctx.node_states["call"]] == [0, 0, 1, 1, 2, 2]
    assert len({o.meta["child_execution_id"] for o in ctx.node_states["call"]} - {None}) <= 3
