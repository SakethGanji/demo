"""stop_at: partial runs end after the named node."""

import pytest

from src.engine.graph import ancestors, disabled_trigger_message
from src.engine.types import Connection

from ._runner_fakes import items, make_runner, node, wf

pytestmark = pytest.mark.anyio


async def test_stop_at_middle_of_chain_runs_exactly_two_nodes():
    runner, log = make_runner()
    w = wf([node("a"), node("b"), node("c")], [("a", "b"), ("b", "c")])
    ctx = await runner.run(w, "a", items({"x": 1}), stop_at="b")
    assert log == ["a", "b"]
    assert "c" not in ctx.node_states
    assert ctx.node_states["b"][0].json["via"] == "b"
    assert not ctx.errors


async def test_no_stop_at_runs_everything():
    runner, log = make_runner()
    w = wf([node("a"), node("b"), node("c")], [("a", "b"), ("b", "c")])
    await runner.run(w, "a", items({"x": 1}))
    assert log == ["a", "b", "c"]


async def test_stop_at_branch_node_runs_neither_branch():
    runner, log = make_runner()
    w = wf(
        [node("a"), node("if", "Branch"), node("yes"), node("no")],
        [("a", "if"), ("if", "yes", "true"), ("if", "no", "false")],
    )
    ctx = await runner.run(w, "a", items({"x": 1}), stop_at="if")
    assert log == ["a", "if"]
    assert "yes" not in ctx.node_states and "no" not in ctx.node_states


async def test_stop_at_start_node_runs_only_that_node():
    runner, log = make_runner()
    w = wf([node("a"), node("b")], [("a", "b")])
    await runner.run(w, "a", items({}), stop_at="a")
    assert log == ["a"]


async def test_start_at_middle_node_with_stop_at_runs_one_node():
    runner, log = make_runner()
    w = wf([node("a"), node("b"), node("c")], [("a", "b"), ("b", "c")])
    ctx = await runner.run(w, "b", items({"v": 1}), stop_at="b")
    assert log == ["b"]
    assert ctx.node_states["b"][0].json == {"v": 1, "via": "b"}


def test_ancestors_excludes_self_and_handles_cycles():
    conns = [Connection("a", "b"), Connection("b", "c"), Connection("c", "b")]
    assert ancestors(conns, "c") == ["b", "a"]
    assert ancestors(conns, "a") == []


def test_disabled_trigger_message():
    assert disabled_trigger_message([node("t", "Webhook", disabled=True)]) == "Trigger node 't' is disabled"
    assert disabled_trigger_message([node("t", "Webhook"), node("x", "Set", disabled=True)]) is None
