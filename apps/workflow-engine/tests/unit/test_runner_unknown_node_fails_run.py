"""A step that fails outside its own error handling (here: a node type nobody registered) must fail the run.

Found in Stage 8: convert-selection emitted a node type that did not exist; the runner logged the error and the run reported success."""

import pytest

from ._runner_fakes import items, make_runner, node, wf

pytestmark = pytest.mark.anyio


async def test_an_unknown_node_type_fails_the_run_instead_of_being_dropped():
    runner, log = make_runner()
    ctx = await runner.run(wf([node("a"), node("x", "NoSuchNodeType"), node("b")], [("a", "x"), ("x", "b")]), "a", items({"k": 1}))
    assert "b" not in log
    assert [e.node_name for e in ctx.errors] == ["x"]
    assert ctx.errors[0].error_code == "NODE_FAILED" and "NoSuchNodeType" in ctx.errors[0].error
