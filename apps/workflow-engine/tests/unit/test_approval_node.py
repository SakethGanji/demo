"""Approval node (Stage 5, DESIGN-STAGE5-UIS 4.2): suspend payload, redaction, ports on resume, test mode, pinned data."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from src.engine.test_mode import TestRunOptions
from src.engine.types import NodeData
from src.engine.workflow_runner import WorkflowRunner
from src.nodes.flow.approval import (
    ApprovalConfigError,
    ApprovalNode,
    cap_context,
    normalize_form,
    parse_due_in,
)

from ._runner_fakes import FakeRegistry, PassNode, items, node, wf

pytestmark = pytest.mark.anyio

NOW = datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc)


def approval_runner():
    """The fake runner plus the real Approval node."""
    log: list[str] = []
    runner = WorkflowRunner()
    reg = FakeRegistry(log)
    reg._nodes["Approval"] = ApprovalNode()
    runner._registry = reg
    return runner, log


def flow(**params):
    """in -> Approval(review) with an approved/rejected/timed_out branch each."""
    return wf(
        [node("in"), node("review", "Approval", parameters=params), node("yes"), node("no"), node("late")],
        [("in", "review"), ("review", "yes", "approved"), ("review", "no", "rejected"), ("review", "late", "timed_out")],
    )


def opts(mocks=None):
    return TestRunOptions(mocks=mocks or {}, now=NOW, seed=1, persist=False)


# ── suspend payload ───────────────────────────────────────────────────────────

async def test_suspends_with_a_rendered_payload():
    runner, log = approval_runner()
    w = flow(
        title="Refund {{ $json.order }} for ${{ $json.amount }}",
        context={"Order": "{{ $json.order }}", "Plain": "static"},
        assignees=["dana@bank.example"],
        form=[{"name": "note", "label": "Note", "type": "textarea"}],
        due_in="3h", on_timeout="reject",
    )
    ctx = await runner.run(w, "in", items({"order": "A-1", "amount": 742}))
    assert len(ctx.suspensions) == 1
    s = ctx.suspensions[0]
    assert s["node"] == "review" and s["run_index"] == 0
    p = s["suspension"].payload
    assert p["title"] == "Refund A-1 for $742"
    assert p["context"]["Order"] == "A-1" and p["context"]["Plain"] == "static"
    assert p["assignees"] == ["dana@bank.example"]
    assert p["form"] == [{"name": "note", "label": "Note", "type": "textarea", "required": False}]
    assert p["on_timeout"] == "reject"
    due = datetime.fromisoformat(p["due_at"])
    assert timedelta(hours=2, minutes=59) < due - datetime.now(timezone.utc) <= timedelta(hours=3)
    assert s["input_items"][0].json["order"] == "A-1" and s["input_items"][0].json["amount"] == 742
    assert ctx.errors == []


async def test_title_defaults_to_the_node_name_and_no_deadline_by_default():
    runner, _ = approval_runner()
    ctx = await runner.run(flow(), "in", items({"a": 1}))
    p = ctx.suspensions[0]["suspension"].payload
    assert p["title"] == "review" and p["due_at"] is None and p["on_timeout"] == "timed_out"
    assert p["allow_comment"] is True and p["require_comment_on_reject"] is False and p["four_eyes"] is False


async def test_secrets_are_redacted_from_title_and_context():
    runner, _ = approval_runner()
    w = flow(title="Token {{ $json.tok }}", context={"Key": "{{ $json.tok }}", "Nested": "x {{ $json.tok }} y"})
    ctx = await runner.run(w, "in", items({"tok": "s3cr3t-value"}), secret_values={"s3cr3t-value"})
    p = ctx.suspensions[0]["suspension"].payload
    assert "s3cr3t-value" not in json.dumps(p)
    assert p["title"] == "Token ***" and p["context"]["Key"] == "***"


def test_due_in_parsing_and_bounds():
    assert parse_due_in("30m") == timedelta(minutes=30)
    assert parse_due_in("2d") == timedelta(days=2)
    assert parse_due_in("") is None and parse_due_in(None) is None
    for bad in ("0m", "91d", "3", "abc", "1h30m", "-1h", "3 h"):
        with pytest.raises(ApprovalConfigError):
            parse_due_in(bad)
    assert parse_due_in("90d") == timedelta(days=90) and parse_due_in("1m") == timedelta(minutes=1)


def test_form_normalisation_and_errors():
    assert normalize_form(None) == [] and normalize_form("") == []
    out = normalize_form([{"name": "pick", "type": "select", "options": ["a", {"value": "b"}], "required": True}])
    assert out == [{"name": "pick", "label": "pick", "type": "select", "required": True, "options": ["a", "b"]}]
    for bad in ([{"label": "x"}], [{"name": "x", "type": "date"}], [{"name": "x", "type": "select"}],
                [{"name": "x"}, {"name": "x"}], {"name": "x"}):
        with pytest.raises(ApprovalConfigError):
            normalize_form(bad)


def test_context_is_capped_and_flagged():
    big = {"huge": "x" * 100_000, "small": "ok"}
    out = cap_context(big)
    assert out["small"] == "ok" and len(out["huge"]) == 8 * 1024 and out["_truncated"] is True
    many = {f"k{i}": "y" * 7000 for i in range(30)}
    capped = cap_context(many)
    assert len(json.dumps(capped)) <= 64 * 1024 and capped["_truncated"] is True
    assert "_truncated" not in cap_context({"a": 1})


async def test_a_bad_due_in_fails_the_node_with_a_clear_error():
    runner, _ = approval_runner()
    ctx = await runner.run(flow(due_in="soon"), "in", items({"a": 1}))
    assert ctx.suspensions == [] and ctx.errors and "due_in" in ctx.errors[0].error


async def test_on_timeout_must_be_a_known_value():
    runner, _ = approval_runner()
    ctx = await runner.run(flow(on_timeout="explode"), "in", items({"a": 1}))
    assert ctx.suspensions == [] and ctx.errors and "on_timeout" in ctx.errors[0].error


# ── ports on resume ───────────────────────────────────────────────────────────

def resume_extras(decision: str, run_index: int = 0, **extra):
    return {"resume": {
        "task_id": "tsk_1", "node": "review", "run_index": run_index,
        "decision": {"decision": decision, "comment": "ok", "form_data": {"note": "n"}, "decided_by": "dana",
                     "decided_at": "2026-01-01T10:00:00+00:00"},
        "node_run_counts": {}, **extra,
    }}


@pytest.mark.parametrize("decision,port,other", [("approved", "yes", {"no", "late"}), ("rejected", "no", {"yes", "late"}),
                                                 ("timed_out", "late", {"yes", "no"})])
async def test_resume_emits_input_plus_approval_on_the_decision_port_only(decision, port, other):
    runner, log = approval_runner()
    ctx = await runner.run(flow(), "review", items({"amount": 5}), extras=resume_extras(decision))
    assert log == [port]  # PassNode logs the nodes it runs: only the decided branch, nothing upstream
    assert not (set(log) & other)
    out = ctx.node_states[port][0].json
    assert out["amount"] == 5 and out["via"] == port
    a = out["approval"] if "approval" in out else None
    # PassNode copies the item, so the approval object came from the Approval node's output
    assert a == {"decision": decision, "comment": "ok", "form_data": {"note": "n"}, "decided_by": "dana",
                 "decided_at": "2026-01-01T10:00:00+00:00", "task_id": "tsk_1"}
    assert ctx.suspensions == []


async def test_resume_only_runs_downstream_of_the_approval():
    runner, log = approval_runner()
    await runner.run(flow(), "review", items({"x": 1}), extras=resume_extras("approved"))
    assert "in" not in log


async def test_a_second_arrival_at_the_approval_suspends_again():
    """The resume is consumed by the first arrival; a loop that reaches the node again pauses again."""
    runner, _ = approval_runner()
    w = wf([node("review", "Approval"), node("back")], [("review", "back", "approved"), ("back", "review")])
    ctx = await runner.run(w, "review", items({"n": 1}), extras=resume_extras("approved"))
    assert len(ctx.suspensions) == 1 and ctx.suspensions[0]["node"] == "review"
    assert ctx.suspensions[0]["run_index"] == 0  # "back" -> "review" is not a loop port, so the index stays


async def test_resume_carries_the_run_index_and_loop_counters():
    runner, _ = approval_runner()
    ctx = await runner.run(flow(), "review", items({"x": 1}), extras=resume_extras("approved", run_index=3, node_run_counts={"in": 2}))
    assert ctx.node_run_counts["in"] == 2 and ctx.node_run_counts["review"] == 1


async def test_resume_key_reaches_expressions():
    from src.engine.expression_engine import ExpressionEngine, expression_engine

    runner, _ = approval_runner()
    seen = []

    class Peek(PassNode):
        async def execute(self, context, node_def, input_data):
            ectx = ExpressionEngine.create_context(input_data, context.node_states, context.execution_id, 0)
            seen.append(expression_engine.resolve("{{ $execution.resumeKey }}", ectx))
            return await super().execute(context, node_def, input_data)

    runner._registry._nodes["Peek"] = Peek([])
    w = wf([node("review", "Approval"), node("p", "Peek")], [("review", "p", "approved")])
    await runner.run(w, "review", items({"x": 1}), execution_id="exec_9", extras=resume_extras("approved", run_index=2))
    assert seen == ["exec_9:review:2"]
    # outside a resumed run it falls back to the execution id
    seen.clear()
    w2 = wf([node("p", "Peek")], [])
    await runner.run(w2, "p", items({"x": 1}), execution_id="exec_10")
    assert seen == ["exec_10"]


# ── test mode ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("port", ["approved", "rejected", "timed_out"])
async def test_a_mocked_approval_never_suspends(port):
    runner, log = approval_runner()
    mock = {"items": [{"approval": {"decision": port}}], "port": port}
    ctx = await runner.run(flow(), "in", items({"a": 1}), test_options=opts({"review": mock}))
    assert ctx.suspensions == [] and ctx.errors == []
    target = {"approved": "yes", "rejected": "no", "timed_out": "late"}[port]
    assert log == ["in", target]  # the mock answers for the Approval; only the mocked port's branch runs
    assert ctx.test_outputs["review"][port][0].json == {"approval": {"decision": port}}


async def test_an_unmocked_approval_in_test_mode_fails_with_test_unmocked_effect():
    runner, log = approval_runner()
    ctx = await runner.run(flow(), "in", items({"a": 1}), test_options=opts())
    assert ctx.suspensions == []
    assert [e.error_code for e in ctx.errors] == ["TEST_UNMOCKED_EFFECT"]
    assert ctx.errors[0].details["effect"] == "human"
    assert not ({"yes", "no", "late"} & set(log))


async def test_test_mode_never_creates_a_suspension_even_if_a_node_returns_one():
    """Defence in depth: a node that returns a suspension inside a test run fails like an unmocked effect."""
    from src.engine.types import NodeExecutionResult, Suspension

    class Pauser:
        input_count = 1

        async def execute(self, context, node_def, input_data):
            return NodeExecutionResult(outputs={}, suspend=Suspension("approval", {"title": "t"}))

    runner, _ = approval_runner()
    runner._registry._nodes["Pauser"] = Pauser()
    from src.engine import effects

    effects.NODE_EFFECTS["Pauser"] = "none"
    try:
        w = wf([node("p", "Pauser")], [])
        ctx = await runner.run(w, "p", items({"a": 1}), test_options=opts())
    finally:
        effects.NODE_EFFECTS.pop("Pauser")
    assert ctx.suspensions == [] and ctx.errors[0].error_code == "TEST_UNMOCKED_EFFECT"


# ── pinned data and disabled ──────────────────────────────────────────────────

async def test_pinned_data_on_an_approval_leaves_on_approved():
    runner, log = approval_runner()
    w = flow()
    w.nodes[1].pinned_data = [NodeData(json={"pinned": True})]
    ctx = await runner.run(w, "in", items({"a": 1}))
    assert ctx.suspensions == [] and "yes" in log and "no" not in log and "late" not in log


async def test_pinned_data_satisfies_the_test_mode_effect_check():
    runner, log = approval_runner()
    w = flow()
    w.nodes[1].pinned_data = [NodeData(json={"pinned": True})]
    ctx = await runner.run(w, "in", items({"a": 1}), test_options=opts())
    assert ctx.errors == [] and "yes" in log


async def test_a_disabled_approval_passes_the_item_on_approved():
    runner, log = approval_runner()
    w = flow()
    w.nodes[1].disabled = True
    ctx = await runner.run(w, "in", items({"a": 1}))
    assert ctx.suspensions == [] and "yes" in log and "no" not in log


# ── per_item: one task per item ───────────────────────────────────────────────

async def test_per_item_renders_one_task_per_item_against_that_item():
    runner, log = approval_runner()
    w = flow(per_item=True, title="Review {{ $json.team }}", context={"Team": "{{ $json.team }}"},
             assignees=["{{ $json.email }}"], notify_email="{{ $json.email }}", due_in="2h")
    ctx = await runner.run(w, "in", items({"team": "Ops", "email": "ana@bank.example"},
                                          {"team": "Risk", "email": "ben@bank.example"}))
    assert len(ctx.suspensions) == 1 and log == ["in"]
    p = ctx.suspensions[0]["suspension"].payload
    assert p["per_item"] is True and len(p["items"]) == 2
    assert [t["title"] for t in p["items"]] == ["Review Ops", "Review Risk"]
    assert [t["context"]["Team"] for t in p["items"]] == ["Ops", "Risk"]
    assert [t["assignees"] for t in p["items"]] == [["ana@bank.example"], ["ben@bank.example"]]
    assert [t["notify"]["to"] for t in p["items"]] == [["ana@bank.example"], ["ben@bank.example"]]
    assert all(t["due_at"] for t in p["items"])
    assert p["title"] == "Review Ops"  # the top level reads like the first item's task


async def test_per_item_false_keeps_the_single_task_payload():
    runner, _ = approval_runner()
    ctx = await runner.run(flow(title="Review {{ $json.team }}"), "in", items({"team": "Ops"}, {"team": "Risk"}))
    p = ctx.suspensions[0]["suspension"].payload
    assert "items" not in p and "per_item" not in p and p["title"] == "Review Ops"


async def test_per_item_resume_routes_each_item_by_its_own_decision_in_input_order():
    runner, log = approval_runner()
    decided = lambda d, by: {"decision": d, "comment": None, "form_data": {}, "decided_by": by, "decided_at": "t"}  # noqa: E731
    extras = resume_extras("approved")
    extras["resume"]["items"] = [
        {"item_index": 2, "task_id": "tsk_c", "decision": decided("timed_out", "system:timeout")},
        {"item_index": 0, "task_id": "tsk_a", "decision": decided("approved", "ana")},
        {"item_index": 1, "task_id": "tsk_b", "decision": decided("rejected", "ben")},
        {"item_index": 3, "task_id": "tsk_d", "decision": decided("approved", "dee")},
    ]
    ctx = await runner.run(flow(per_item=True), "review", items({"n": 0}, {"n": 1}, {"n": 2}, {"n": 3}), extras=extras)
    assert sorted(log) == ["late", "no", "yes"]  # each port's downstream runs once
    assert [(i.json["n"], i.json["approval"]["task_id"]) for i in ctx.node_states["yes"]] == [(0, "tsk_a"), (3, "tsk_d")]
    assert [i.json["approval"]["decided_by"] for i in ctx.node_states["no"]] == ["ben"]
    assert [i.json["approval"]["decision"] for i in ctx.node_states["late"]] == ["timed_out"]
    assert ctx.suspensions == []
