"""Runner pause (Stage 5, DESIGN-STAGE5-UIS 5.1): suspend never queues downstream, other branches finish, the outcome is
waiting, errors beat suspensions, parallel approvals each pause, sub-workflows cannot pause."""

from __future__ import annotations

import pytest

from src.nodes.flow.approval import ApprovalNode

from ._runner_fakes import items, node, wf
from .test_approval_node import approval_runner

pytestmark = pytest.mark.anyio


async def test_suspend_does_not_queue_downstream():
    runner, log = approval_runner()
    w = wf([node("a"), node("ap", "Approval"), node("after")], [("a", "ap"), ("ap", "after", "approved")])
    ctx = await runner.run(w, "a", items({"x": 1}))
    assert log == ["a"] and len(ctx.suspensions) == 1
    assert "after" not in ctx.node_states and "ap" not in ctx.node_states  # a paused node has no output yet


async def test_other_branches_finish_while_one_branch_waits():
    runner, log = approval_runner()
    w = wf([node("a"), node("ap", "Approval"), node("slow1"), node("slow2"), node("held")],
           [("a", "ap"), ("a", "slow1"), ("slow1", "slow2"), ("ap", "held", "approved")])
    ctx = await runner.run(w, "a", items({"x": 1}))
    assert {"a", "slow1", "slow2"} <= set(log) and "held" not in log
    assert [s["node"] for s in ctx.suspensions] == ["ap"] and ctx.errors == []


async def test_a_run_with_a_suspension_and_no_errors_is_waiting():
    runner, _ = approval_runner()
    w = wf([node("a"), node("ap", "Approval")], [("a", "ap")])
    ctx = await runner.run(w, "a", items({"x": 1}))
    # the outcome rule complete() applies: suspensions and no errors -> waiting
    assert ctx.suspensions and not ctx.errors


async def test_errors_and_a_suspension_means_failed():
    runner, _ = approval_runner()
    w = wf([node("a"), node("ap", "Approval"), node("bad", "Fail")], [("a", "ap"), ("a", "bad")])
    ctx = await runner.run(w, "a", items({"x": 1}))
    assert ctx.errors and ctx.suspensions  # complete() turns this into "failed" and creates no task


async def test_two_parallel_approvals_give_two_suspensions():
    runner, _ = approval_runner()
    w = wf([node("a"), node("ap1", "Approval"), node("ap2", "Approval")], [("a", "ap1"), ("a", "ap2")])
    ctx = await runner.run(w, "a", items({"x": 1}))
    assert sorted(s["node"] for s in ctx.suspensions) == ["ap1", "ap2"]
    assert all(s["run_index"] == 0 for s in ctx.suspensions)


async def test_the_waiting_node_reports_a_waiting_status_in_its_metrics():
    runner, _ = approval_runner()
    w = wf([node("ap", "Approval")], [])
    ctx = await runner.run(w, "ap", items({"x": 1}))
    assert ctx.node_metrics["ap"]["status"] == "waiting"


async def test_a_suspension_in_a_sub_workflow_is_an_error_not_a_silent_continue():
    runner, log = approval_runner()
    parent = wf([node("p")], [])
    parent_ctx = runner._create_context(parent, "manual")
    child = wf([node("ap", "Approval"), node("after")], [("ap", "after", "approved")])
    ctx = await runner.run_subworkflow(child, "ap", items({"x": 1}), parent_ctx)
    assert ctx.suspensions == []
    assert [e.error_code for e in ctx.errors] == ["APPROVAL_IN_SUBWORKFLOW"] and "after" not in log


async def test_a_suspension_does_not_trigger_retries_or_on_error():
    runner, _ = approval_runner()
    attempts: list[int] = []

    class Counting(ApprovalNode):
        async def execute(self, context, node_definition, input_data):
            attempts.append(1)
            return await super().execute(context, node_definition, input_data)

    runner._registry._nodes["Approval"] = Counting()
    w = wf([node("ap", "Approval", retry_on_fail=3, on_error="continue")], [])
    ctx = await runner.run(w, "ap", items({"x": 1}))
    assert attempts == [1] and len(ctx.suspensions) == 1 and ctx.errors == []


async def test_a_suspension_snapshot_that_is_too_large_fails_the_node():
    """The 1 MB resume snapshot is checked when the node pauses, so the run fails now rather than at task creation."""
    runner, _ = approval_runner()
    w = wf([node("ap", "Approval")], [])
    ctx = await runner.run(w, "ap", items({"blob": "x" * (1024 * 1024 + 10)}))
    assert ctx.suspensions == []
    assert [e.error_code for e in ctx.errors] == ["APPROVAL_SNAPSHOT_TOO_LARGE"]
