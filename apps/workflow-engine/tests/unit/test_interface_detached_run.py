"""``start_detached_run``: the id comes back as soon as the execution row exists; the run outlives the caller (DESIGN-STAGE5-UIS section 6.1)."""

from __future__ import annotations

import asyncio

import pytest

from src.db import session as db_session
from src.engine import execution_registry
from src.services import interface_service
from src.services.execution_service import ExecutionService

pytestmark = pytest.mark.anyio


class DummySession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


@pytest.fixture
def patched(monkeypatch):
    monkeypatch.setattr(db_session, "async_session_factory", lambda: DummySession())
    state = {"calls": [], "finish": asyncio.Event(), "done": asyncio.Event()}

    async def run(self, workflow_id, input_data=None, **kw):
        state["calls"].append({"workflow_id": workflow_id, "input": input_data, **kw})
        if state.get("raise_early"):
            raise state["raise_early"]
        await kw["on_context_ready"]("exec_42", "prod", None)
        await state["finish"].wait()
        if state.get("raise_late"):
            raise state["raise_late"]
        return None, "context"

    monkeypatch.setattr(ExecutionService, "run_saved_workflow", run)
    return state


async def start(**kw):
    return await interface_service.start_detached_run(
        "wf_1", item={"a": 1}, start_node="Form", environment="prod", version_id=7, extras={"x": 1},
        metadata={"interface": {"slug": "s"}}, **kw)


async def test_the_id_returns_while_the_run_is_still_going(patched):
    exec_id = await start()
    assert exec_id == "exec_42" and not patched["finish"].is_set()
    task = execution_registry.running_executions["exec_42"]
    assert not task.done()
    patched["finish"].set()
    await task
    assert "exec_42" not in execution_registry.running_executions


async def test_the_run_is_started_from_the_trigger_with_the_item_and_no_substitution(patched):
    await start()
    call = patched["calls"][0]
    assert call["start_node_name"] == "Form" and call["version_id"] == 7 and call["environment"] == "prod" and call["mode"] == "api"
    assert call["substitute_inputs"] is False and call["extras"] == {"x": 1} and call["metadata"] == {"interface": {"slug": "s"}}
    assert [d.json for d in call["initial_data_override"]] == [{"a": 1}] and call["timeout_s"] == interface_service.RUN_TIMEOUT_S
    patched["finish"].set()
    await asyncio.sleep(0)


async def test_on_finished_receives_the_context(patched):
    seen = []

    async def finished(ctx):
        seen.append(ctx)
        patched["done"].set()

    await start(on_finished=finished)
    patched["finish"].set()
    await asyncio.wait_for(patched["done"].wait(), 2)
    assert seen == ["context"]


async def test_a_crash_after_the_id_is_reported_to_on_finished_as_none(patched):
    seen = []

    async def finished(ctx):
        seen.append(ctx)
        patched["done"].set()

    patched["raise_late"] = RuntimeError("node blew up")
    await start(on_finished=finished)
    patched["finish"].set()
    await asyncio.wait_for(patched["done"].wait(), 2)
    assert seen == [None]


async def test_a_failure_before_the_execution_exists_reaches_the_caller(patched):
    patched["raise_early"] = ValueError("workflow is gone")
    with pytest.raises(ValueError, match="workflow is gone"):
        await start()
    assert "exec_42" not in execution_registry.running_executions
