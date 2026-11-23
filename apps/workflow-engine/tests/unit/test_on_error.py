"""on_error: stop / continue / error_output, with retries and the legacy continue_on_fail."""

import pytest

from src.schemas.workflow import NodeDefinitionSchema
from src.repositories.workflow_repository import definition_to_workflow

from ._runner_fakes import items, make_runner, node, wf

pytestmark = pytest.mark.anyio


def _chain(on_error, **kw):
    # a -> f (fails) -> ok_main (main) ; f -> ok_err (error)
    return wf(
        [node("a"), node("f", "Fail", on_error=on_error, retry_delay=0, **kw), node("ok_main"), node("ok_err")],
        [("a", "f"), ("f", "ok_main"), ("f", "ok_err", "error")],
    )


async def test_stop_is_default_and_ends_run_with_error():
    runner, log = make_runner()
    ctx = await runner.run(_chain("stop"), "a", items({"k": 1}))
    assert log == ["a", "f"]
    assert [e.node_name for e in ctx.errors] == ["f"]
    assert ctx.node_metrics["f"]["status"] == "error"


async def test_continue_sends_error_item_to_main():
    runner, log = make_runner()
    ctx = await runner.run(_chain("continue"), "a", items({"k": 1}))
    assert log == ["a", "f", "ok_main"]
    assert ctx.node_states["f"][0].json["error"] == "boom"
    assert [e.node_name for e in ctx.errors] == ["f"]  # unchanged continue_on_fail behaviour


async def test_error_output_routes_items_to_error_branch_only():
    runner, log = make_runner()
    ctx = await runner.run(_chain("error_output"), "a", items({"k": 1}, {"k": 2}))
    assert log == ["a", "f", "ok_err"]  # main branch got nothing
    assert not ctx.errors
    assert ctx.node_metrics["f"]["status"] == "success"
    out = ctx.node_states["ok_err"]
    assert [i.json["k"] for i in out] == [1, 2]
    assert out[0].json["error"] == {"message": "boom", "node": "f"}
    # the failed node's own state is the error items: input json + error
    failed = ctx.node_states["f"]
    assert [(i.json["k"], i.json["via"]) for i in failed] == [(1, "a"), (2, "a")]


async def test_error_output_with_nothing_connected_drops_items_and_continues():
    runner, log = make_runner()
    w = wf([node("a"), node("f", "Fail", on_error="error_output")], [("a", "f")])
    ctx = await runner.run(w, "a", items({"k": 1}))
    assert log == ["a", "f"]
    assert not ctx.errors


async def test_error_output_only_after_retries_exhausted():
    runner, log = make_runner()
    ctx = await runner.run(_chain("error_output", retry_on_fail=2), "a", items({"k": 1}))
    assert log.count("f") == 3  # 1 try + 2 retries, then routed
    assert "ok_err" in log and "ok_main" not in log
    assert ctx.node_metrics["f"]["retries"] == 2
    assert not ctx.errors


async def test_stop_with_retries_still_fails_after_all_attempts():
    runner, log = make_runner()
    ctx = await runner.run(_chain("stop", retry_on_fail=1), "a", items({"k": 1}))
    assert log.count("f") == 2
    assert "ok_err" not in log
    assert "after 2 attempts" in ctx.errors[0].error


async def test_error_branch_not_triggered_on_success():
    runner, log = make_runner()
    w = wf(
        [node("a"), node("p", on_error="error_output"), node("ok_main"), node("ok_err")],
        [("a", "p"), ("p", "ok_main"), ("p", "ok_err", "error")],
    )
    await runner.run(w, "a", items({"k": 1}))
    assert log == ["a", "p", "ok_main"]


def test_legacy_continue_on_fail_maps_to_continue():
    assert NodeDefinitionSchema(name="n", type="Set", continue_on_fail=True).to_dataclass().on_error == "continue"
    assert NodeDefinitionSchema(name="n", type="Set").to_dataclass().on_error == "stop"
    # explicit on_error wins over the legacy flag
    d = NodeDefinitionSchema(name="n", type="Set", continue_on_fail=True, on_error="error_output").to_dataclass()
    assert d.on_error == "error_output" and not d.continue_on_fail
    stored = {"nodes": [{"name": "n", "type": "Set", "continue_on_fail": True}], "connections": []}
    assert definition_to_workflow(stored, "w").nodes[0].on_error == "continue"
    assert node("n", continue_on_fail=True).on_error == "continue"


async def test_legacy_continue_on_fail_runs_as_continue():
    runner, log = make_runner()
    w = wf([node("a"), node("f", "Fail", continue_on_fail=True), node("z")], [("a", "f"), ("f", "z")])
    await runner.run(w, "a", items({}))
    assert log == ["a", "f", "z"]
