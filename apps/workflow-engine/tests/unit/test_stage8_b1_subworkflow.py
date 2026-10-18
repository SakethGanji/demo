"""Stage 8 W1 / B1: ExecuteWorkflow v2, the in-memory invoker, guards, timeout, on_error, isolation (DESIGN-STAGE8-REUSE
2.2, 2.3, 3.2, 3.4-3.6, 3.8). No database: a fake workflow repository and the fake node registry."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from src.engine import subworkflow as sw
from src.engine.test_mode import TestRunOptions
from src.engine.types import (
    ExecutionEvent,
    ExecutionEventType,
    NodeData,
    NodeExecutionResult,
    StoredWorkflow,
)
from src.engine.workflow_runner import WorkflowRunner
from src.nodes.flow.approval import ApprovalNode
from src.nodes.flow.execute_workflow import ExecuteWorkflowNode, normalize_params
from src.nodes.flow.respond import RespondNode
from src.nodes.triggers.execute_workflow_trigger import ExecuteWorkflowTriggerNode

from ._runner_fakes import FakeRegistry, items, node, wf

pytestmark = pytest.mark.anyio

NOW = datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc)


class SlowNode:
    input_count = 1

    async def execute(self, context, node_def, input_data):
        await asyncio.sleep(float(node_def.parameters.get("seconds", 5)))
        return NodeExecutionResult(outputs={"main": input_data})


class FailIfBad:
    """Fails for an item with ``bad`` set; logs every call."""

    input_count = 1

    def __init__(self, log):
        self.log = log

    async def execute(self, context, node_def, input_data):
        self.log.append(("child_ran", [i.json.get("id") for i in input_data]))
        if any(i.json.get("bad") for i in input_data):
            raise RuntimeError("bad item")
        return NodeExecutionResult(outputs={"main": [NodeData(json={**i.json, "ok": True}) for i in input_data]})


class Repo:
    def __init__(self, *workflows):
        self.by_id = {}
        for w in workflows:
            self.by_id[w.id] = StoredWorkflow(
                id=w.id, name=w.name, workflow=w, active=True, created_at=NOW, updated_at=NOW
            )

    async def get(self, wid):
        return self.by_id.get(wid)


def named(w, wid, name=None):
    w.id = wid
    w.name = name or wid
    return w


def make_runner():
    log: list = []
    runner = WorkflowRunner()
    reg = FakeRegistry(log)
    reg._nodes.update(
        {
            "ExecuteWorkflow": ExecuteWorkflowNode(),
            "ExecuteWorkflowTrigger": ExecuteWorkflowTriggerNode(),
            "Respond": RespondNode(),
            "Approval": ApprovalNode(),
            "Slow": SlowNode(),
            "FailIfBad": FailIfBad(log),
        }
    )
    runner._registry = reg
    return runner, log


def child_flow(wid="wf_child", *middle, name=None, trigger_params=None):
    """trigger -> middle nodes in a chain."""
    nodes = [node("trigger", "ExecuteWorkflowTrigger", parameters=trigger_params or {})] + list(middle)
    conns = [(a.name, b.name) for a, b in zip(nodes, nodes[1:])]
    return named(wf(nodes, conns), wid, name)


def parent_flow(call_params, wid="wf_parent", name="Parent", **call_kw):
    return named(
        wf([node("start"), node("call", "ExecuteWorkflow", parameters=call_params, **call_kw), node("after")],
           [("start", "call"), ("call", "after")]),
        wid, name,
    )


async def run_parent(runner, parent, repo, data=None, start="start", **kw):
    kw.setdefault("subworkflow_invoker", sw.InMemoryInvoker(runner))
    return await runner.run(parent, start, data or items({"id": 1}), workflow_repository=repo, **kw)


V2 = {"workflowId": "wf_child", "version": "draft", "inputMode": "passThrough"}


# ── output semantics ─────────────────────────────────────────────────────────────────────────────────────────────


async def test_items_mode_emits_the_return_nodes_items_with_lineage_metadata():
    runner, log = make_runner()
    child = child_flow("wf_child", node("work"))
    ctx = await run_parent(runner, parent_flow(V2), Repo(child), items({"id": 1}, {"id": 2}))
    out = ctx.node_states["call"]
    assert ctx.errors == [] and [i.json["id"] for i in out] == [1, 2]
    assert all(i.json["via"] == "work" for i in out)
    assert [i.meta["pairedItem"] for i in out] == [0, 1]
    assert out[0].meta["child_workflow_id"] == "wf_child" and out[0].meta["child_version_number"] is None
    assert out[0].meta["child_execution_id"]
    assert "meta" not in out[0].json  # lineage never leaks into the json
    assert [i.json["id"] for i in ctx.node_states["after"]] == [1, 2]


async def test_each_mode_runs_the_child_once_per_item_in_order_and_concatenates():
    runner, log = make_runner()
    child = child_flow("wf_child", node("work", "FailIfBad"))
    params = {**V2, "mode": "each"}
    ctx = await run_parent(runner, parent_flow(params), Repo(child), items({"id": 1}, {"id": 2}, {"id": 3}))
    assert [c for c in log if c[0] == "child_ran"] == [("child_ran", [1]), ("child_ran", [2]), ("child_ran", [3])]
    out = ctx.node_states["call"]
    assert [i.json["id"] for i in out] == [1, 2, 3] and [i.meta["pairedItem"] for i in out] == [0, 1, 2]
    assert len({i.meta["child_execution_id"] for i in out}) == 3  # three child runs


async def test_the_first_respond_that_ran_is_the_return_node_even_when_nodes_run_after_it():
    runner, _ = make_runner()
    child = named(
        wf(
            [node("trigger", "ExecuteWorkflowTrigger"), node("early", "Respond"), node("late")],
            [("trigger", "early"), ("early", "late")],
        ),
        "wf_child",
    )
    ctx = await run_parent(runner, parent_flow(V2), Repo(child))
    # Respond is passthrough: its input items, not the node that ran after it
    assert ctx.node_states["call"][0].json["via"] == "start"  # not "late": Respond passes its input through


async def test_without_respond_the_return_node_is_the_last_node_that_finished():
    runner, _ = make_runner()
    child = child_flow("wf_child", node("one"), node("two"))
    ctx = await run_parent(runner, parent_flow(V2), Repo(child))
    assert ctx.node_states["call"][0].json["via"] == "two"


async def test_a_child_with_no_output_gives_an_empty_branch():
    runner, log = make_runner()

    class Nothing:
        input_count = 1

        async def execute(self, context, node_def, input_data):
            return NodeExecutionResult(outputs={"main": None})

    # the child's trigger emits nothing, so no node finished with output and the call returns []
    runner._registry._nodes["ExecuteWorkflowTrigger"] = Nothing()
    child = child_flow("wf_child", node("never"))
    ctx = await run_parent(runner, parent_flow(V2), Repo(child))
    assert ctx.errors == [] and "after" not in ctx.node_states and "after" not in log and "never" not in log


async def test_map_mode_builds_the_child_input_per_item_and_include_item_overlays():
    runner, _ = make_runner()
    child = child_flow("wf_child", node("work"))
    params = {
        "workflowId": "wf_child", "version": "draft", "inputMode": "map", "includeItem": True,
        "inputs": {"amount": "{{ $json.price }}", "label": "fixed"},
    }
    ctx = await run_parent(runner, parent_flow(params), Repo(child), items({"id": 1, "price": 5}, {"id": 2, "price": 7}))
    out = [i.json for i in ctx.node_states["call"]]
    assert [(o["id"], o["amount"], o["label"]) for o in out] == [(1, 5, "fixed"), (2, 7, "fixed")]
    # without includeItem only the mapped fields reach the child
    params["includeItem"] = False
    ctx = await run_parent(runner, parent_flow(params), Repo(child), items({"id": 1, "price": 5}))
    assert "id" not in ctx.node_states["call"][0].json and ctx.node_states["call"][0].json["amount"] == 5


async def test_custom_mode_resolves_expressions_for_new_nodes():
    runner, _ = make_runner()
    child = child_flow("wf_child", node("work"))
    params = {"workflowId": "wf_child", "version": "draft", "inputMode": "custom", "customInput": {"who": "{{ $json.id }}"}}
    ctx = await run_parent(runner, parent_flow(params), Repo(child), items({"id": 9}))
    assert ctx.node_states["call"][0].json["who"] == 9


# ── legacy compatibility ─────────────────────────────────────────────────────────────────────────────────────────


def test_normalize_params_legacy_node_reads_as_draft_passthrough_merged():
    p = normalize_params({"workflowId": "w"})
    assert (p["legacy"], p["version"], p["inputMode"], p["outputMode"], p["mode"]) == (True, "draft", "passThrough", "merged", "once")
    assert normalize_params({"workflowId": "w", "inputMode": "custom"})["inputMode"] == "custom"


def test_normalize_params_new_node_defaults_and_version_forms():
    p = normalize_params({"workflowId": "w", "version": "latest"})
    assert (p["legacy"], p["version"], p["inputMode"], p["outputMode"], p["timeoutSeconds"]) == (False, "latest", "map", "items", 300.0)
    assert normalize_params({"version": 3})["version"] == 3
    assert normalize_params({"version": "7"})["version"] == 7
    assert normalize_params({"version": "draft", "timeoutSeconds": 99999})["timeoutSeconds"] == 3600.0
    with pytest.raises(ValueError):
        normalize_params({"version": "banana"})
    with pytest.raises(ValueError):
        normalize_params({"version": 0})


async def test_legacy_node_keeps_the_merged_single_item_output_byte_for_byte():
    runner, _ = make_runner()
    child = child_flow("wf_child", node("work"), name="Child WF")
    legacy = {"workflowId": "wf_child"}  # no version, no outputMode: how every stored node looks today
    ctx = await run_parent(runner, parent_flow(legacy), Repo(child), items({"id": 1}, {"id": 2}))
    out = ctx.node_states["call"]
    assert len(out) == 1
    meta = out[0].json.pop("_subworkflow")
    assert meta["id"] == "wf_child" and meta["name"] == "Child WF" and meta["execution_id"]
    # the last item of the terminal node, merged into one json: exactly the old shape
    assert out[0].json == {"id": 2, "via": "work", "_triggeredAt": out[0].json["_triggeredAt"],
                           "_triggerType": "subworkflow", "_executionDepth": 1}


async def test_legacy_node_with_a_child_that_produced_nothing_marks_it_empty():
    runner, _ = make_runner()
    child = named(wf([node("trigger", "ExecuteWorkflowTrigger", pinned_data=[])], []), "wf_child")
    ctx = await run_parent(runner, parent_flow({"workflowId": "wf_child"}), Repo(child))
    assert ctx.errors == []


async def test_legacy_custom_input_is_one_item_unresolved_json():
    runner, _ = make_runner()
    child = child_flow("wf_child", node("work"))
    legacy = {"workflowId": "wf_child", "inputMode": "custom", "customInput": '{"a": 1}'}
    ctx = await run_parent(runner, parent_flow(legacy), Repo(child), items({"x": 1}, {"x": 2}))
    assert ctx.node_states["call"][0].json["a"] == 1 and "x" not in ctx.node_states["call"][0].json


# ── guards ───────────────────────────────────────────────────────────────────────────────────────────────────────


def codes(ctx):
    return [e.error_code for e in ctx.errors]


async def test_guard_child_not_found():
    runner, _ = make_runner()
    ctx = await run_parent(runner, parent_flow({**V2, "workflowId": "wf_nope"}), Repo())
    assert codes(ctx) == [sw.SUBWORKFLOW_NOT_FOUND]
    assert "Sub-workflow 'wf_nope' isn't in this project." in ctx.errors[0].error


async def test_guard_child_without_an_execute_workflow_trigger():
    runner, log = make_runner()
    child = named(wf([node("start"), node("work")], [("start", "work")]), "wf_child", "Plain")
    ctx = await run_parent(runner, parent_flow(V2), Repo(child))
    assert codes(ctx) == [sw.SUBWORKFLOW_NOT_CALLABLE]
    assert "'Plain' can't be called: it has no Execute Workflow Trigger." in ctx.errors[0].error
    assert "work" not in log


async def test_guard_child_with_a_webhook_and_a_trigger_starts_at_the_trigger():
    runner, _ = make_runner()
    child = named(
        wf([node("hook", "Webhook"), node("trigger", "ExecuteWorkflowTrigger"), node("work")], [("trigger", "work")]),
        "wf_child",
    )
    ctx = await run_parent(runner, parent_flow(V2), Repo(child))
    assert ctx.errors == [] and ctx.node_states["call"][0].json["via"] == "work"


async def test_guard_depth_limit():
    runner, log = make_runner()
    child = child_flow("wf_child", node("work"))
    parent = parent_flow(V2)
    ctx = runner._create_context(parent, "manual")
    ctx.execution_depth = ctx.max_execution_depth  # already 10 deep
    ctx.workflow_repository = Repo(child)
    res = await sw.InMemoryInvoker(runner).invoke(sw.SubworkflowCall(ctx, "call", "wf_child", "draft", items({}), 5))
    assert res.status == "failed" and res.error.error_code == sw.RECURSION_LIMIT
    assert res.error.error == "Sub-workflows are nested more than 10 deep."
    assert "work" not in log


async def test_guard_direct_self_call_is_a_cycle():
    runner, log = make_runner()
    a = named(wf([node("trigger", "ExecuteWorkflowTrigger"), node("again", "ExecuteWorkflow", parameters={**V2, "workflowId": "wf_a"})],
                 [("trigger", "again")]), "wf_a", "Alpha")
    ctx = await run_parent(runner, a, Repo(a), start="trigger")
    assert codes(ctx) == [sw.SUBWORKFLOW_CYCLE]
    assert "'Alpha' → 'Alpha': a workflow can't call itself, directly or indirectly." in ctx.errors[0].error


async def test_guard_indirect_cycle_a_b_a_names_the_chain():
    runner, _ = make_runner()
    a = named(wf([node("trigger", "ExecuteWorkflowTrigger"), node("to_b", "ExecuteWorkflow", parameters={**V2, "workflowId": "wf_b"})],
                 [("trigger", "to_b")]), "wf_a", "Alpha")
    b = named(wf([node("trigger", "ExecuteWorkflowTrigger"), node("to_a", "ExecuteWorkflow", parameters={**V2, "workflowId": "wf_a"})],
                 [("trigger", "to_a")]), "wf_b", "Beta")
    ctx = await run_parent(runner, a, Repo(a, b), start="trigger")
    # the failure surfaces at the root as Alpha's call to Beta failing, with Beta's cycle error inside
    assert codes(ctx) == [sw.SUBWORKFLOW_FAILED]
    err = ctx.errors[0]
    assert err.details["child_error_code"] == sw.SUBWORKFLOW_CYCLE
    assert "'Alpha' → 'Beta' → 'Alpha': a workflow can't call itself, directly or indirectly." in err.details["child_error"]


async def test_guard_child_with_an_approval_is_refused_before_it_runs():
    runner, log = make_runner()
    child = child_flow("wf_child", node("before"), node("ap", "Approval"), name="Reviewer")
    ctx = await run_parent(runner, parent_flow(V2), Repo(child))
    assert codes(ctx) == [sw.APPROVAL_IN_SUBWORKFLOW]
    assert "'Reviewer' has an Approval step; approvals must be in the top-level workflow." in ctx.errors[0].error
    assert "before" not in log  # raised at call time, so the child never does half its work


async def test_guards_apply_in_order_not_callable_before_approval():
    runner, _ = make_runner()
    child = named(wf([node("ap", "Approval")], []), "wf_child", "NoTrigger")
    ctx = await run_parent(runner, parent_flow(V2), Repo(child))
    assert codes(ctx) == [sw.SUBWORKFLOW_NOT_CALLABLE]


async def test_without_a_repository_the_call_is_not_found_not_a_crash():
    runner, _ = make_runner()
    ctx = await runner.run(parent_flow(V2), "start", items({}), subworkflow_invoker=sw.InMemoryInvoker(runner))
    assert codes(ctx) == [sw.SUBWORKFLOW_NOT_FOUND]


# ── timeout ──────────────────────────────────────────────────────────────────────────────────────────────────────


async def test_timeout_cancels_the_child_and_fails_the_node():
    runner, _ = make_runner()
    child = child_flow("wf_child", node("slow", "Slow", parameters={"seconds": 5}), name="Slowpoke")
    params = {**V2, "timeoutSeconds": 0.05}
    started = asyncio.get_running_loop().time()
    ctx = await run_parent(runner, parent_flow(params), Repo(child))
    assert asyncio.get_running_loop().time() - started < 2
    assert codes(ctx) == [sw.SUBWORKFLOW_TIMEOUT]
    assert "Sub-workflow 'Slowpoke' timed out after 0.05s." in ctx.errors[0].error
    assert "after" not in ctx.node_states


async def test_the_parents_deadline_caps_the_timeout():
    runner, _ = make_runner()
    child = child_flow("wf_child", node("slow", "Slow", parameters={"seconds": 5}))
    parent = parent_flow({**V2, "timeoutSeconds": 3000})
    parent.settings = {"max_execution_time": 0.05}  # seconds; becomes the root deadline
    ctx = await run_parent(runner, parent, Repo(child))
    assert codes(ctx) == [sw.SUBWORKFLOW_TIMEOUT]


def test_effective_timeout_is_min_of_param_and_remaining_deadline():
    import time

    runner, _ = make_runner()
    ctx = runner._create_context(parent_flow(V2), "manual")
    call = sw.SubworkflowCall(ctx, "n", "w", "draft", [], 30)
    assert sw.effective_timeout(call) == 30
    ctx.deadline = time.monotonic() + 2
    assert 0 < sw.effective_timeout(call) <= 2
    ctx.deadline = time.monotonic() - 1
    assert sw.effective_timeout(call) == 0
    assert sw.effective_timeout(sw.SubworkflowCall(ctx, "n", "w", "draft", [], 99999)) == 0  # deadline still wins
    ctx.deadline = None
    assert sw.effective_timeout(sw.SubworkflowCall(ctx, "n", "w", "draft", [], 99999)) == 3600


# ── error propagation and on_error ───────────────────────────────────────────────────────────────────────────────


def failing_child():
    return child_flow("wf_child", node("work", "FailIfBad", label="The Worker"), name="Risky")


async def test_a_child_failure_is_one_node_error_with_details():
    runner, _ = make_runner()
    ctx = await run_parent(runner, parent_flow(V2), Repo(failing_child()), items({"id": 1, "bad": True}))
    assert codes(ctx) == [sw.SUBWORKFLOW_FAILED] and ctx.errors[0].node_name == "call"
    err = ctx.errors[0]
    assert "Sub-workflow 'Risky' failed at 'The Worker': bad item" in err.error
    d = err.details
    assert d["child_node"] == "work" and d["child_workflow_id"] == "wf_child" and d["child_version_number"] is None
    assert d["child_execution_id"] and d["child_error"] == "bad item"
    assert "after" not in ctx.node_states  # on_error stop


async def test_on_error_continue_emits_an_error_item_and_the_run_goes_on():
    runner, _ = make_runner()
    ctx = await run_parent(runner, parent_flow(V2, on_error="continue"), Repo(failing_child()), items({"id": 1, "bad": True}))
    assert ctx.errors and ctx.node_states["after"][0].json["_errorNode"] == "call"
    assert "failed at 'The Worker'" in ctx.node_states["after"][0].json["error"]


async def test_on_error_error_output_once_mode_emits_one_error_item():
    runner, _ = make_runner()
    parent = named(
        wf([node("start"), node("call", "ExecuteWorkflow", parameters=V2, on_error="error_output"), node("ok"), node("oops")],
           [("start", "call"), ("call", "ok", "main"), ("call", "oops", "error")]), "wf_parent")
    ctx = await run_parent(runner, parent, Repo(failing_child()), items({"id": 1, "bad": True}))
    assert ctx.errors == [] and "ok" not in ctx.node_states
    e = ctx.node_states["oops"]
    assert len(e) == 1
    assert e[0].json["code"] == sw.SUBWORKFLOW_FAILED and e[0].json["failed_node"] == "work"
    assert e[0].json["child_execution_id"] and e[0].json["error"]["node"] == "call"
    assert "bad item" in e[0].json["error"]["message"] and e[0].json["id"] == 1


async def test_error_output_with_each_routes_per_item_successes_main_failures_error():
    runner, _ = make_runner()
    parent = named(
        wf([node("start"), node("call", "ExecuteWorkflow", parameters={**V2, "mode": "each"}, on_error="error_output"),
            node("ok"), node("oops")],
           [("start", "call"), ("call", "ok", "main"), ("call", "oops", "error")]), "wf_parent")
    data = items({"id": 1}, {"id": 2, "bad": True}, {"id": 3}, {"id": 4, "bad": True})
    ctx = await run_parent(runner, parent, Repo(failing_child()), data)
    assert ctx.errors == []
    assert [i.json["id"] for i in ctx.node_states["ok"]] == [1, 3]
    assert [i.json["id"] for i in ctx.node_states["oops"]] == [2, 4]  # the failing parent items, with the error added
    assert all(i.json["code"] == sw.SUBWORKFLOW_FAILED for i in ctx.node_states["oops"])
    assert [i.meta["pairedItem"] for i in ctx.node_states["call"]] == [0, 2]


async def test_error_output_retries_a_failed_item_without_repeating_the_good_ones():
    runner, log = make_runner()
    parent = named(
        wf([node("start"), node("call", "ExecuteWorkflow", parameters={**V2, "mode": "each"}, on_error="error_output", retry_on_fail=2),
            node("oops")], [("start", "call"), ("call", "oops", "error")]), "wf_parent")
    ctx = await run_parent(runner, parent, Repo(failing_child()), items({"id": 1}, {"id": 2, "bad": True}))
    ran = [c[1] for c in log if c[0] == "child_ran"]
    assert ran.count([1]) == 1 and ran.count([2]) == 3  # item 2: first try plus 2 retries; item 1 once
    assert [i.json["id"] for i in ctx.node_states["oops"]] == [2]


async def test_runner_retry_on_each_does_not_rerun_children_that_already_succeeded():
    runner, log = make_runner()
    parent = parent_flow({**V2, "mode": "each"}, retry_on_fail=1, retry_delay=1)
    ctx = await run_parent(runner, parent, Repo(failing_child()), items({"id": 1}, {"id": 2, "bad": True}))
    ran = [c[1] for c in log if c[0] == "child_ran"]
    assert ran.count([1]) == 1 and ran.count([2]) == 2  # the retry only redid the failed item
    assert codes(ctx) == [sw.SUBWORKFLOW_FAILED] and "(after 2 attempts)" in ctx.errors[0].error


async def test_retry_reinvokes_the_child_as_a_new_execution_each_attempt():
    runner, log = make_runner()
    ctx = await run_parent(runner, parent_flow(V2, retry_on_fail=2, retry_delay=1), Repo(failing_child()), items({"id": 1, "bad": True}))
    assert [c[1] for c in log if c[0] == "child_ran"] == [[1], [1], [1]] and codes(ctx) == [sw.SUBWORKFLOW_FAILED]


async def test_childs_own_continue_counts_as_success_for_the_parent():
    runner, _ = make_runner()
    child = child_flow("wf_child", node("work", "FailIfBad", on_error="continue"))
    ctx = await run_parent(runner, parent_flow(V2), Repo(child), items({"id": 1, "bad": True}))
    assert ctx.errors == [] and "error" in ctx.node_states["call"][0].json


async def test_a_timeout_goes_through_on_error_too():
    runner, _ = make_runner()
    child = child_flow("wf_child", node("slow", "Slow", parameters={"seconds": 5}))
    parent = parent_flow({**V2, "timeoutSeconds": 0.05}, on_error="continue")
    ctx = await run_parent(runner, parent, Repo(child))
    assert codes(ctx) == [sw.SUBWORKFLOW_TIMEOUT] and "after" in ctx.node_states


# ── isolation: call stack, extras, shared test state ────────────────────────────────────────────────────────────


class Spy:
    """A node that records the context it ran in."""

    input_count = 1

    def __init__(self):
        self.seen = []

    async def execute(self, context, node_def, input_data):
        self.seen.append(context)
        context.extras["child_only"] = True
        context.test_requests.setdefault("shared", []).append({"from": context.workflow.id})
        return NodeExecutionResult(outputs={"main": input_data})


async def test_call_stack_root_id_deadline_invoker_and_extras_are_isolated():
    runner, _ = make_runner()
    spy = Spy()
    runner._registry._nodes["Spy"] = spy
    child = child_flow("wf_child", node("spy", "Spy"))
    parent = parent_flow(V2)
    invoker = sw.InMemoryInvoker(runner)
    ctx = await runner.run(
        parent, "start", items({"id": 1}), workflow_repository=Repo(child), subworkflow_invoker=invoker,
        extras={"api_specs": {"parent": 1}, "release_id": "rel_1", "release_pins": {"x": 1}, "broker_token:a": "tok",
                "resume": {"node": "z"}},
    )
    (child_ctx,) = spy.seen
    assert ctx.call_stack == ("wf_parent",) and child_ctx.call_stack == ("wf_parent", "wf_child")
    assert child_ctx.root_execution_id == ctx.root_execution_id == ctx.execution_id
    assert child_ctx.parent_execution_id == ctx.execution_id and child_ctx.execution_depth == 1
    assert child_ctx.subworkflow_invoker is invoker
    assert child_ctx.extras is not ctx.extras
    assert "child_only" not in ctx.extras and child_ctx.extras["child_only"] is True
    assert "api_specs" not in child_ctx.extras  # G6: the parent's specs describe the parent's nodes
    assert "resume" not in child_ctx.extras
    assert child_ctx.extras["release_id"] == "rel_1" and child_ctx.extras["release_pins"] == {"x": 1}
    assert child_ctx.extras["broker_token:a"] == "tok"
    assert ctx.extras["api_specs"] == {"parent": 1}  # the parent keeps its own
    assert child_ctx.extras["secret_values"] is ctx.extras["secret_values"]


async def test_run_subworkflow_takes_the_childs_own_api_specs_and_execution_id():
    runner, _ = make_runner()
    parent = parent_flow(V2)
    pctx = runner._create_context(parent, "manual")
    pctx.extras["api_specs"] = {"parent": 1}
    child = child_flow("wf_child", node("work"))
    cctx = await runner.run_subworkflow(
        child, "trigger", items({}), pctx, workflow_id="wf_child", execution_id="exec_fixed", extras={"api_specs": {"child": 2}}
    )
    assert cctx.execution_id == "exec_fixed" and cctx.extras["api_specs"] == {"child": 2}
    assert pctx.extras["api_specs"] == {"parent": 1}


async def test_broker_tokens_minted_in_the_child_are_handed_up_for_the_root_to_revoke():
    runner, _ = make_runner()

    class Mint:
        input_count = 1

        async def execute(self, context, node_def, input_data):
            context.extras["broker_token:child"] = "tok-c"
            return NodeExecutionResult(outputs={"main": input_data})

    runner._registry._nodes["Mint"] = Mint()
    child = child_flow("wf_child", node("m", "Mint"))
    pctx = runner._create_context(parent_flow(V2), "manual")
    await runner.run_subworkflow(child, "trigger", items({}), pctx, workflow_id="wf_child")
    assert pctx.extras["broker_token:child"] == "tok-c"


async def test_test_requests_frozen_clock_and_rng_are_shared_with_the_child():
    runner, _ = make_runner()
    spy = Spy()
    runner._registry._nodes["Spy"] = spy
    child = child_flow("wf_child", node("spy", "Spy"))
    parent = named(
        wf([node("start"), node("call", "ExecuteWorkflow", parameters=V2)], [("start", "call")]), "wf_parent")
    opts = TestRunOptions(mocks={"start": {"items": [{"id": 1}]}}, now=NOW, seed=7, persist=False)
    # a test run mocks outside steps; the sub-workflow call is mocked by default, so drive run_subworkflow directly
    pctx = runner._create_context(parent, "manual")
    pctx.frozen_now, pctx.test_options = NOW, opts
    import random
    pctx.rng = random.Random(7)
    cctx = await runner.run_subworkflow(child, "trigger", items({}), pctx, workflow_id="wf_child")
    assert cctx.frozen_now == NOW and cctx.rng is pctx.rng
    assert cctx.test_requests is pctx.test_requests and pctx.test_requests["shared"] == [{"from": "wf_child"}]


async def test_the_childs_events_are_tagged_with_the_parent_node_and_child_id():
    runner, _ = make_runner()
    child = child_flow("wf_child", node("work"))
    events: list[ExecutionEvent] = []
    ctx = await run_parent(runner, parent_flow(V2), Repo(child), on_event=events.append)
    tagged = [e for e in events if e.subworkflow_parent_node == "call"]
    assert tagged and all(e.subworkflow_id == "wf_child" and e.type.value.startswith("node:") for e in tagged)
    assert {e.node_name for e in tagged} == {"trigger", "work"}
    assert not any(e.subworkflow_parent_node for e in events if e.node_name in ("start", "call", "after"))


# ── frozen time (3.8) ────────────────────────────────────────────────────────────────────────────────────────────


async def test_trigger_reads_the_frozen_clock_so_an_inline_child_is_deterministic():
    runner, _ = make_runner()
    child = child_flow("wf_child", node("work"))
    pctx = runner._create_context(parent_flow(V2), "manual")
    pctx.frozen_now = NOW
    cctx = await runner.run_subworkflow(child, "trigger", items({"a": 1}), pctx, workflow_id="wf_child")
    assert cctx.node_states["trigger"][0].json["_triggeredAt"] == NOW.isoformat()
    # default input path too
    cctx = await runner.run_subworkflow(child, "trigger", [NodeData(json={})], pctx, workflow_id="wf_child")
    assert cctx.node_states["trigger"][0].json["_triggeredAt"] == NOW.isoformat()


async def test_without_a_frozen_clock_the_trigger_uses_the_wall_clock():
    runner, _ = make_runner()
    child = child_flow("wf_child", node("work"))
    pctx = runner._create_context(parent_flow(V2), "manual")
    cctx = await runner.run_subworkflow(child, "trigger", items({"a": 1}), pctx, workflow_id="wf_child")
    assert cctx.node_states["trigger"][0].json["_triggeredAt"].startswith(str(datetime.now().year))


# ── the invoker seam ─────────────────────────────────────────────────────────────────────────────────────────────


async def test_the_node_uses_the_contexts_invoker_and_passes_version_items_and_timeout():
    runner, _ = make_runner()
    seen: list[sw.SubworkflowCall] = []

    class Fake:
        async def invoke(self, call):
            seen.append(call)
            return sw.SubworkflowResult(
                items=[NodeData(json={"r": 1})], child_execution_id="exec_c", child_workflow_id=call.workflow_id,
                child_version_number=4, status="success", child_name="Fake",
            )

    params = {"workflowId": "wf_child", "version": 4, "inputMode": "map", "inputs": {"k": "{{ $json.id }}"}, "timeoutSeconds": 12}
    ctx = await runner.run(parent_flow(params), "start", items({"id": 5}), subworkflow_invoker=Fake())
    (call,) = seen
    assert (call.workflow_id, call.version, call.parent_node, call.timeout_seconds) == ("wf_child", 4, "call", 12)
    assert [i.json for i in call.items] == [{"k": 5}] and call.parent_context.execution_id == ctx.execution_id
    out = ctx.node_states["call"][0]
    assert out.json == {"r": 1} and out.meta["child_version_number"] == 4 and out.meta["child_execution_id"] == "exec_c"


async def test_in_memory_invoker_writes_nothing_and_reports_a_draft():
    runner, _ = make_runner()
    child = child_flow("wf_child", node("work"), name="Kid")
    pctx = runner._create_context(parent_flow(V2), "manual")
    pctx.workflow_repository = Repo(child)
    res = await sw.InMemoryInvoker(runner).invoke(sw.SubworkflowCall(pctx, "call", "wf_child", "latest", items({"a": 1}), 5))
    assert res.status == "success" and res.child_version_number is None and res.child_name == "Kid"
    assert res.child_execution_id and res.items[0].json["via"] == "work"


async def test_protocol_is_runtime_checkable():
    assert isinstance(sw.InMemoryInvoker(), sw.SubworkflowInvoker)
    assert not isinstance(object(), sw.SubworkflowInvoker)


# ── G10: the credential rebuild keeps the label ─────────────────────────────────────────────────────────────────


async def test_credential_rebuild_of_the_node_definition_keeps_the_label():
    runner, _ = make_runner()
    seen = []

    class Rec:
        input_count = 1

        async def execute(self, context, node_def, input_data):
            seen.append(node_def)
            return NodeExecutionResult(outputs={"main": input_data})

    async def cred(credential_id, team_id="default"):
        return {"token": "t"}

    async def nop(**kw):
        return None

    runner._registry._nodes["Rec"] = Rec()
    runner._db_session = object()  # truthy: the credential branch runs
    runner._resolve_credential = cred
    runner._persist_node_output = nop
    w = wf([node("a", "Rec", parameters={"credentialId": "c1"}, label="Pretty label")], [])
    await runner.run(w, "a", items({}))
    assert seen[0].label == "Pretty label" and seen[0].parameters["_credentials"] == {"token": "t"}
