"""Disabled nodes pass their input straight to main; the fields survive parameter resolution."""

import pytest

from src.core.exceptions import ValidationError
from src.engine.types import NodeData
from src.repositories.workflow_repository import definition_to_workflow
from src.schemas.workflow import NodeDefinitionSchema, WorkflowCreateRequest
from src.services.workflow_service import WorkflowService

from ._runner_fakes import items, make_runner, node, wf

pytestmark = pytest.mark.anyio


async def test_disabled_node_passes_multiple_items_through_unchanged():
    runner, log = make_runner()
    w = wf([node("a"), node("b", disabled=True), node("c")], [("a", "b"), ("b", "c")])
    ctx = await runner.run(w, "a", items({"n": 1}, {"n": 2}, {"n": 3}))
    assert log == ["a", "c"]  # b never executed
    assert [i.json["n"] for i in ctx.node_states["b"]] == [1, 2, 3]
    assert all(i.json["via"] == "a" for i in ctx.node_states["b"])  # untouched by b
    assert [i.json["n"] for i in ctx.node_states["c"]] == [1, 2, 3]
    assert ctx.node_metrics["b"]["status"] == "success"
    assert not ctx.errors


async def test_disabled_node_emits_node_events():
    runner, _ = make_runner()
    events = []
    w = wf([node("a"), node("b", disabled=True)], [("a", "b")])
    await runner.run(w, "a", items({}), on_event=events.append)
    names = [(e.type.value, e.node_name) for e in events if e.node_name == "b"]
    assert len(names) == 2  # start + complete


def test_disabled_and_on_error_survive_parameter_resolution():
    runner, _ = make_runner()
    from src.engine.types import ExecutionContext
    from datetime import datetime

    nd = node("n", disabled=True, on_error="error_output", label="L")
    w = wf([nd], [])
    ctx = ExecutionContext(workflow=w, execution_id="e", start_time=datetime.now(), mode="manual")
    resolved = runner._resolve_node_parameters(ctx, nd, [NodeData(json={})])
    assert resolved.disabled is True
    assert resolved.on_error == "error_output"


def test_schema_and_stored_definition_roundtrip():
    s = NodeDefinitionSchema(name="n", type="Set", disabled=True, on_error="error_output")
    d = s.to_dataclass()
    assert d.disabled and d.on_error == "error_output"
    stored = {"nodes": [{"name": "n", "type": "Set", "disabled": True, "on_error": "continue"}], "connections": []}
    n = definition_to_workflow(stored, "w").nodes[0]
    assert n.disabled and n.on_error == "continue" and n.continue_on_fail


def test_disabled_trigger_is_a_validation_error_on_save():
    svc = WorkflowService(None, None, None)
    req = WorkflowCreateRequest(
        name="w",
        nodes=[{"name": "hook", "type": "Webhook", "disabled": True}, {"name": "s", "type": "Set"}],
        connections=[{"source_node": "hook", "target_node": "s"}],
    )
    with pytest.raises(ValidationError) as e:
        svc._validate_workflow(req)
    assert e.value.message == "Trigger node 'hook' is disabled"


def test_disabled_non_trigger_is_fine_on_save():
    svc = WorkflowService(None, None, None)
    req = WorkflowCreateRequest(
        name="w",
        nodes=[{"name": "hook", "type": "Webhook"}, {"name": "s", "type": "Set", "disabled": True}],
        connections=[{"source_node": "hook", "target_node": "s"}],
    )
    svc._validate_workflow(req)
