"""Stage 8 B10: opt-in inline child in tests (DESIGN-STAGE8-REUSE 6.3) and typed generated mocks (6.2).

Pure parts first (schema, options, validation, mock generator); the invoker and the runner run on the throwaway Postgres.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
from pydantic import ValidationError

from src.engine.test_mode import TestRunOptions, parse_now
from src.engine.types import NodeData, NodeDefinition, Workflow
from src.engine.workflow_runner import WorkflowRunner
from src.schemas.workflow import TestCaseSchema, normalize_tests
from src.services.mock_gen import subworkflow_mock
from src.services.test_ai_service import validate_test
from tests.unit._stage8_b2_world import Env, build, install_nodes, nd, remove_nodes
from tests.unit.conftest import anyio_backend, pg, pg_dbname  # noqa: F401

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def nodes():
    install_nodes()
    yield
    remove_nodes()


# ── schema and options ────────────────────────────────────────────────────────────────────────────────────────────


def test_a_test_may_name_inline_sub_workflows_and_it_round_trips():
    t = normalize_tests([{"name": "t", "subworkflows": {"call": "inline"}}])[0]
    assert t["subworkflows"] == {"call": "inline"}
    assert normalize_tests([t])[0] == t
    assert "subworkflows" not in normalize_tests([{"name": "plain"}])[0]   # absent stays absent


def test_only_inline_is_a_valid_mode():
    with pytest.raises(ValidationError):
        TestCaseSchema.model_validate({"name": "t", "subworkflows": {"call": "live"}})


def test_options_default_to_no_inline_nodes():
    assert TestRunOptions().inline_subworkflows == frozenset()


# ── validate_test: "<node>/<child node>" keys ─────────────────────────────────────────────────────────────────────


def _defn() -> dict[str, Any]:
    return {"nodes": [{"name": "Start", "type": "Start"}, {"name": "call", "type": "ExecuteWorkflow"},
                      {"name": "Set", "type": "Set"}], "connections": []}


def _test(**kw: Any) -> dict[str, Any]:
    base = {"id": "t_00000001", "name": "t", "target": {"kind": "workflow"}, "input": {"items": [{}]}, "mocks": {},
            "expect": [{"node": "Set", "op": "status", "value": "success"}]}
    return {**base, **kw}


def test_path_keys_are_accepted_for_an_inline_node_only():
    mock = {"items": [{"sent": True}]}
    expect = [{"node": "call/Notify", "op": "status", "value": "success"}]
    inline = _test(subworkflows={"call": "inline"}, mocks={"call/Notify": mock}, expect=expect)
    assert validate_test(inline, _defn()) is None
    not_inline = _test(mocks={"call/Notify": mock}, expect=expect)
    assert "call/Notify" in (validate_test(not_inline, _defn()) or "")
    only_assert = _test(expect=expect)
    assert "call/Notify" in (validate_test(only_assert, _defn()) or "")


def test_an_inline_path_key_still_needs_a_child_node_part_and_a_valid_mock():
    t = _test(subworkflows={"call": "inline"}, mocks={"call/": {"items": []}})
    assert validate_test(t, _defn())
    t = _test(subworkflows={"call": "inline"}, mocks={"call/Notify": {"nonsense": 1}})
    assert validate_test(t, _defn())


def test_http_mocks_are_allowed_for_an_inline_child_node():
    t = _test(subworkflows={"call": "inline"}, mocks={"call/Fetch": {"http": {"status": 200, "body": {}}}})
    assert validate_test(t, _defn()) is None


def test_subworkflows_must_name_execute_workflow_nodes():
    assert "not an Execute Workflow" in (validate_test(_test(subworkflows={"Set": "inline"}), _defn()) or "")
    assert "doesn't exist" in (validate_test(_test(subworkflows={"ghost": "inline"}), _defn()) or "")


# ── typed generated mocks (6.2) ───────────────────────────────────────────────────────────────────────────────────


def test_the_subworkflow_mock_is_shaped_like_the_childs_declared_output():
    shape = {"type": "object", "x-source": "derived", "required": ["verdict", "limit"], "properties": {
        "verdict": {"type": "string", "enum": ["ok", "review"]}, "limit": {"type": "number"},
        "flags": {"type": "array", "items": {"type": "string"}}}}
    mock, warning = subworkflow_mock(shape, "call")
    assert warning is None
    assert mock == {"items": [{"verdict": "ok", "limit": 1, "flags": ["flags_example"]}]}


@pytest.mark.parametrize("shape", [None, {"type": "unknown"}, {"type": "object", "properties": {}}, {"type": "string"}])
def test_an_untyped_child_output_keeps_the_empty_item_and_warns(shape):
    mock, warning = subworkflow_mock(shape, "call")
    assert mock == {"items": [{}]} and "call" in warning and "sub-workflow" in warning


def test_the_generic_recipe_does_not_invent_an_id_for_an_untyped_sub_workflow():
    from src.engine.test_recipes import _generic_options

    opts = _generic_options({"name": "call", "type": "ExecuteWorkflow"}, {})
    assert opts[0]["mock"] == {"items": [{}]}
    assert _generic_options({"name": "q", "type": "Postgres"}, {})[0]["mock"] == {"items": [{"id": 1}]}   # others keep theirs


def test_the_generic_recipe_types_the_mock_from_the_checker_shape():
    from src.engine.test_recipes import _generic_options

    shapes = {"call": {"outputs": {"main": {"type": "object", "x-source": "derived", "properties": {
        "verdict": {"type": "string"}}}}}}
    assert _generic_options({"name": "call", "type": "ExecuteWorkflow"}, shapes)[0]["mock"] == {"items": [{"verdict": "verdict_example"}]}


# ── the invoker and the runner on Postgres ────────────────────────────────────────────────────────────────────────


def child_flow(name: str, *, notify_default: dict | None = None) -> Workflow:
    wf = build([nd("Inputs", "ExecuteWorkflowTrigger"), nd("Notify", "SendEmail", toEmail="a@b.test", subject="s", body="b"),
                nd("Mark", "TestMark", tag="child")],
               [("Inputs", "Notify"), ("Notify", "Mark")], name)
    if notify_default is not None:
        wf.test_defaults = {"mocks": {"Notify": notify_default}}
    return wf


def opts(mocks: dict | None = None, **kw: Any) -> TestRunOptions:
    return TestRunOptions(mocks=mocks or {}, inline_subworkflows=frozenset({"call"}), persist=kw.pop("persist", False), **kw)


async def inline_call(w: Env, child_id: str, o: TestRunOptions, parent, *, runner_factory=None, row=False, mode="test"):
    ctx = await w.parent_ctx(parent, mode=mode, row=row, test_options=o)
    base = w.call(ctx, child_id, "draft")
    from dataclasses import replace

    call = replace(base, test_inline=True)
    inv = w.invoker()
    if runner_factory is not None:
        inv._runner_factory = runner_factory
    return ctx, await inv.invoke(call)


async def make_pair(w: Env, notify_default: dict | None):
    from tests.unit._stage8_b2_world import parent_wf

    child = await w.create(child_flow(f"c-{w.prefix}", notify_default=notify_default))
    parent = await w.create(parent_wf(f"p-{w.prefix}", child.id, "draft"))
    return parent, child


async def test_the_childs_own_default_mock_answers_its_outside_step(pg):
    w = Env(pg)
    parent, child = await make_pair(w, {"items": [{"sent": True, "src": "child-default"}]})
    ctx, res = await inline_call(w, child.id, opts(), parent)
    assert res.status == "success", res.error
    assert res.items[0].json["src"] == "child-default" and res.items[0].json["tag"] == "child"
    # the child's outputs and node status are visible to the parent under "<node>/<child node>"
    assert [i.json["src"] for i in ctx.test_outputs["call/Notify"]["main"]] == ["child-default"]
    assert ctx.test_outputs["call/Mark"]["main"][0].json["tag"] == "child"
    status = ctx.extras["test_inline_status"]
    assert status["call/Notify"] == "success" and status["call/Mark"] == "success" and status["call/Inputs"] == "success"
    assert "Notify" not in ctx.test_outputs                                 # not under the bare child name (no collision)


async def test_a_parent_test_mock_for_the_path_beats_the_childs_default(pg):
    w = Env(pg)
    parent, child = await make_pair(w, {"items": [{"sent": True, "src": "child-default"}]})
    o = opts({"call/Notify": {"items": [{"sent": False, "src": "parent-test"}]}, "Other": {"items": [{"x": 1}]}})
    ctx, res = await inline_call(w, child.id, o, parent)
    assert res.status == "success" and res.items[0].json["src"] == "parent-test"
    assert o.mocks["call/Notify"]["items"][0]["src"] == "parent-test"       # the parent's options were not changed


async def test_an_unmocked_child_step_fails_naming_the_path(pg):
    w = Env(pg)
    parent, child = await make_pair(w, None)
    ctx, res = await inline_call(w, child.id, opts(), parent)
    assert res.status == "failed"
    assert res.error.error_code == "TEST_UNMOCKED_EFFECT" and res.error.node_name == "call/Notify"
    assert "call/Notify" in res.error.error and "network" in res.error.error
    assert ctx.extras["test_inline_status"]["call/Notify"] == "error"
    assert ctx.extras["test_inline_status"]["call/Mark"] == "not_run"


async def test_the_child_gets_the_parents_clock_seed_and_persistence(pg):
    w = Env(pg)
    parent, child = await make_pair(w, {"items": [{}]})
    seen: list[TestRunOptions] = []

    class Spy(WorkflowRunner):
        async def run_subworkflow(self, *a, **kw):
            seen.append(kw["test_options"])
            return await super().run_subworkflow(*a, **kw)

    now = parse_now("2027-03-04T05:06:07Z")
    o = opts({"call/Notify": {"items": [{"n": 1}]}}, now=now, seed=42)
    await inline_call(w, child.id, o, parent, runner_factory=lambda sf: Spy(db_session_factory=sf))
    (child_opts,) = seen
    assert (child_opts.now, child_opts.seed, child_opts.persist) == (now, 42, False)
    assert child_opts.mocks == {"Notify": {"items": [{"n": 1}]}}            # the prefix is stripped for the child
    assert child_opts.live_nodes == frozenset() and child_opts.inline_subworkflows == frozenset()
    assert child_opts is not o


async def test_a_persisted_inline_child_is_a_test_row_linked_to_the_parent(pg):
    w = Env(pg)
    parent, child = await make_pair(w, {"items": [{"ok": 1}]})
    ctx, res = await inline_call(w, child.id, opts(persist=True), parent, row=True)
    assert res.status == "success"
    (row,) = await w.rows_of(child.id)
    assert row["mode"] == "test" and row["parent_execution_id"] == ctx.execution_id
    assert row["parent_node_name"] == "call" and res.child_execution_id == row["id"]


async def test_an_unsaved_inline_child_writes_no_row(pg):
    w = Env(pg)
    parent, child = await make_pair(w, {"items": [{"ok": 1}]})
    ctx, res = await inline_call(w, child.id, opts(persist=False), parent)
    assert res.status == "success" and res.child_execution_id is None
    assert await w.rows_of(child.id) == []


async def test_a_child_request_is_recorded_under_the_path_not_the_bare_name(pg):
    w = Env(pg)
    parent, child = await make_pair(w, {"items": [{"ok": 1}]})
    ctx = await w.parent_ctx(parent, mode="test", row=False, test_options=opts())
    ctx.test_requests["Notify"] = [{"params": {"mine": True}}]              # a parent node that happens to share the child's name
    from dataclasses import replace

    res = await w.invoker().invoke(replace(w.call(ctx, child.id, "draft"), test_inline=True))
    assert res.status == "success"
    assert ctx.test_requests["Notify"] == [{"params": {"mine": True}}]      # untouched
    assert ctx.test_requests["call/Notify"][0]["params"]["toEmail"] == "a@b.test"


# the runner, with the real node and invoker, from a parent that has the ExecuteWorkflow node


async def run_parent(w: Env, parent, o: TestRunOptions):
    from src.db.session import async_session_factory
    from src.services.subworkflow_wiring import subworkflow_invoker

    stored_wf = parent.workflow
    runner = WorkflowRunner()
    return await runner.run(
        stored_wf, "Start", [NodeData(json={"a": 1})], "test", project_id=parent.project_id, environment="default",
        test_options=o, subworkflow_invoker=subworkflow_invoker(w.sf), workflow_repository=None,
    )


async def test_an_inline_node_runs_through_the_runner_and_is_not_mocked(pg):
    w = Env(pg)
    parent, child = await make_pair(w, {"items": [{"sent": True}]})
    # a mock on the node itself is ignored for an inline node: the child really runs
    o = opts({"call": {"items": [{"fake": True}]}})
    ctx = await run_parent(w, parent, o)
    assert ctx.errors == []
    out = ctx.test_outputs["call"]["main"][0].json
    assert out.get("tag") == "child" and "fake" not in out
    assert "call/Mark" in ctx.test_outputs


async def test_without_inline_the_same_node_still_needs_a_mock(pg):
    w = Env(pg)
    parent, child = await make_pair(w, {"items": [{"sent": True}]})
    ctx = await run_parent(w, parent, TestRunOptions(persist=False))
    assert [e.error_code for e in ctx.errors] == ["TEST_UNMOCKED_EFFECT"] and ctx.errors[0].node_name == "call"


async def test_an_unmocked_child_step_stops_the_parent_run_even_with_on_error_continue(pg):
    w = Env(pg)
    parent, child = await make_pair(w, None)
    parent.workflow.nodes[1].on_error = "continue"
    ctx = await run_parent(w, parent, opts())
    # the run stops at the parent's node; the message and details name the child's path
    assert [(e.error_code, e.node_name, e.details["node"]) for e in ctx.errors] == [("TEST_UNMOCKED_EFFECT", "call", "call/Notify")]
    assert "call/Notify" in ctx.errors[0].error
