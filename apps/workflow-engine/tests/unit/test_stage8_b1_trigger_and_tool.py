"""Stage 8 W1 / B1: ExecuteWorkflowTrigger input parameters and validation (DESIGN-STAGE8-REUSE 2.1, 3.8) and the
WorkflowTool agent tool going through the invoker (G9). No database."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.engine import subworkflow as sw
from src.engine.types import ExecutionError, NodeData, NodeDefinition
from src.engine.workflow_runner import WorkflowRunner
from src.nodes.ai.tools.workflow_tool import WorkflowToolNode
from src.nodes.triggers.execute_workflow_trigger import ExecuteWorkflowTriggerNode, declared_input_shape, input_source

from ._runner_fakes import items, node, wf
from .test_stage8_b1_subworkflow import NOW, Repo, child_flow, make_runner, named

pytestmark = pytest.mark.anyio

FIELDS = [
    {"name": "order_id", "type": "string", "required": True, "description": "The order"},
    {"name": "amount", "type": "number", "required": True},
    {"name": "note", "type": "string", "required": False},
    {"name": "tags", "type": "array"},
]


def trig(**params):
    return NodeDefinition(name="trigger", type="ExecuteWorkflowTrigger", parameters=params)


def ctx_for(depth=1, frozen=None):
    runner = WorkflowRunner()
    ctx = runner._create_context(named(wf([node("n")], []), "wf_t"), "manual")
    ctx.execution_depth = depth
    ctx.frozen_now = frozen
    return ctx


# ── contract ─────────────────────────────────────────────────────────────────────────────────────────────────────


def test_input_source_is_fields_for_new_nodes_json_schema_for_legacy_with_a_schema():
    assert input_source({"inputSource": "passthrough"}) == "passthrough"
    assert input_source({"fields": FIELDS}) == "fields"
    assert input_source({"inputSchema": {"type": "object"}}) is None  # legacy: documentation only
    assert input_source({}) is None


def test_fields_become_a_declared_shape_with_required_and_types():
    s = declared_input_shape({"inputSource": "fields", "fields": FIELDS})
    assert s["x-source"] == "declared" and s["required"] == ["order_id", "amount"]
    assert {k: v["type"] for k, v in s["properties"].items()} == {
        "order_id": "string", "amount": "number", "note": "string", "tags": "array"}
    assert s["properties"]["order_id"]["description"] == "The order"
    assert all(v["x-source"] == "declared" for v in s["properties"].values())


def test_contract_adds_the_underscore_fields_and_passthrough_is_unknown():
    node_ = ExecuteWorkflowTriggerNode()
    out = node_.contract({"inputSource": "fields", "fields": FIELDS}, {})["outputs"]["main"]
    assert set(out["properties"]) == {"order_id", "amount", "note", "tags", "_triggeredAt", "_triggerType", "_executionDepth"}
    assert out["required"] == ["order_id", "amount"]
    assert node_.contract({"inputSource": "passthrough", "fields": FIELDS}, {})["outputs"]["main"]["type"] == "unknown"
    assert node_.contract({"inputSource": "fields", "fields": []}, {})["outputs"]["main"]["type"] == "unknown"
    schema = {"type": "object", "properties": {"a": {"type": "string"}}}
    out = node_.contract({"inputSource": "jsonSchema", "inputSchema": schema}, {})["outputs"]["main"]
    assert "a" in out["properties"] and "_triggeredAt" in out["properties"]


# ── validation at run time ───────────────────────────────────────────────────────────────────────────────────────


async def run_trigger(params, data, **ctx_kw):
    return await ExecuteWorkflowTriggerNode().execute(ctx_for(**ctx_kw), trig(**params), data)


async def test_valid_input_passes_and_extra_fields_are_kept():
    res = await run_trigger({"inputSource": "fields", "fields": FIELDS},
                            items({"order_id": "o1", "amount": 5, "extra": "keep"}))
    out = res.outputs["main"][0].json
    assert out["extra"] == "keep" and out["_triggerType"] == "subworkflow" and out["_executionDepth"] == 1


async def test_a_missing_required_field_fails_with_the_path():
    with pytest.raises(sw.SubworkflowError) as e:
        await run_trigger({"inputSource": "fields", "fields": FIELDS}, items({"order_id": "o1"}))
    assert e.value.code == sw.SUBWORKFLOW_INPUT_INVALID == "SUBWORKFLOW_INPUT_INVALID"
    assert "'amount'" in str(e.value) and e.value.details["path"] == "amount"


async def test_a_type_mismatch_on_a_declared_field_fails():
    with pytest.raises(sw.SubworkflowError) as e:
        await run_trigger({"inputSource": "fields", "fields": FIELDS}, items({"order_id": "o1", "amount": "lots"}))
    assert e.value.code == sw.SUBWORKFLOW_INPUT_INVALID and e.value.details["path"] == "amount"
    assert "expected number, got string" in str(e.value)


async def test_every_item_is_validated_and_the_bad_index_is_named():
    with pytest.raises(sw.SubworkflowError) as e:
        await run_trigger({"inputSource": "fields", "fields": FIELDS},
                          items({"order_id": "o1", "amount": 1}, {"order_id": "o2"}))
    assert e.value.details["item_index"] == 1 and "item 1" in str(e.value)


async def test_json_schema_source_validates_too():
    schema = {"type": "object", "required": ["a"], "properties": {"a": {"type": "string"}}}
    with pytest.raises(sw.SubworkflowError):
        await run_trigger({"inputSource": "jsonSchema", "inputSchema": schema}, items({"b": 1}))


async def test_passthrough_accepts_anything():
    res = await run_trigger({"inputSource": "passthrough", "fields": FIELDS}, items({"whatever": 1}))
    assert res.outputs["main"][0].json["whatever"] == 1


async def test_legacy_node_input_schema_stays_documentation():
    schema = {"type": "object", "required": ["a"], "properties": {"a": {"type": "string"}}}
    res = await run_trigger({"inputSchema": schema}, items({"b": 1}))  # no inputSource: not enforced
    assert res.outputs["main"][0].json["b"] == 1


async def test_manual_run_of_the_child_uses_the_default_input_without_validation():
    res = await run_trigger({"inputSource": "fields", "fields": FIELDS, "defaultInput": '{"order_id": "x"}'},
                            [NodeData(json={})], depth=0)
    out = res.outputs["main"][0].json
    assert out["order_id"] == "x" and out["_triggerType"] == "manual"


async def test_an_empty_call_with_required_fields_fails_instead_of_using_the_default():
    with pytest.raises(sw.SubworkflowError):
        await run_trigger({"inputSource": "fields", "fields": FIELDS, "defaultInput": '{"order_id": "x", "amount": 1}'},
                          [NodeData(json={})], depth=1)


async def test_the_trigger_reads_the_frozen_clock():
    res = await run_trigger({}, items({"a": 1}), frozen=NOW)
    assert res.outputs["main"][0].json["_triggeredAt"] == NOW.isoformat()


async def test_a_validation_failure_in_the_child_reaches_the_parent_as_a_failed_call():
    runner, _ = make_runner()
    child = child_flow("wf_child", node("work"), trigger_params={"inputSource": "fields", "fields": FIELDS}, name="Intake")
    parent = named(wf([node("start"), node("call", "ExecuteWorkflow",
                                           parameters={"workflowId": "wf_child", "version": "draft", "inputMode": "map",
                                                       "inputs": {"order_id": "{{ $json.id }}"}})],
                      [("start", "call")]), "wf_parent")
    ctx = await runner.run(parent, "start", items({"id": "o1"}), workflow_repository=Repo(child),
                           subworkflow_invoker=sw.InMemoryInvoker(runner))
    e = ctx.errors[0]
    assert e.error_code == sw.SUBWORKFLOW_FAILED and e.details["child_error_code"] == sw.SUBWORKFLOW_INPUT_INVALID
    assert "amount" in e.details["child_error"]


# ── WorkflowTool ─────────────────────────────────────────────────────────────────────────────────────────────────


def tool_config(**params):
    nd = NodeDefinition(name="tool", type="WorkflowTool", parameters={"workflowId": "wf_child", **params})
    return WorkflowToolNode().get_config(nd)


async def test_tool_calls_the_invoker_with_the_default_version_latest():
    seen = []

    class Fake:
        async def invoke(self, call):
            seen.append(call)
            return sw.SubworkflowResult(
                items=[NodeData(json={"answer": 42})], child_execution_id="exec_c", child_workflow_id=call.workflow_id,
                child_version_number=3, status="success", child_name="Calc",
            )

    runner = WorkflowRunner()
    ctx = runner._create_context(named(wf([node("n")], []), "wf_parent"), "manual")
    ctx.subworkflow_invoker = Fake()
    cfg = tool_config()
    assert cfg["_version"] == "latest"
    out = await cfg["execute"]({"input": {"q": 1}}, ctx)
    (call,) = seen
    assert (call.workflow_id, call.version, call.timeout_seconds) == ("wf_child", "latest", 300.0)
    assert [i.json for i in call.items] == [{"q": 1}] and call.parent_context is ctx
    assert out == {"workflow_id": "wf_child", "workflow_name": "Calc", "version": 3, "child_execution_id": "exec_c",
                   "output": [{"answer": 42}]}


async def test_tool_pins_the_configured_version():
    seen = []

    class Fake:
        async def invoke(self, call):
            seen.append(call.version)
            return sw.SubworkflowResult([], None, "wf_child", None, "success")

    runner = WorkflowRunner()
    ctx = runner._create_context(named(wf([node("n")], []), "wf_parent"), "manual")
    ctx.subworkflow_invoker = Fake()
    for raw, want in (("draft", "draft"), (5, 5), ("7", 7)):
        await tool_config(version=raw)["execute"]({"input": {}}, ctx)
        assert seen[-1] == want
    out = await tool_config(version="nonsense")["execute"]({"input": {}}, ctx)
    assert "Invalid sub-workflow version" in out["error"]


async def test_tool_reports_a_failed_call_with_its_code():
    class Fake:
        async def invoke(self, call):
            return sw.SubworkflowResult(
                [], "exec_c", "wf_child", 2, "failed",
                ExecutionError(node_name="work", error="boom", timestamp=datetime.now(), error_code="X"),
            )

    runner = WorkflowRunner()
    ctx = runner._create_context(named(wf([node("n")], []), "wf_parent"), "manual")
    ctx.subworkflow_invoker = Fake()
    out = await tool_config()["execute"]({"input": {}}, ctx)
    assert out == {"error": "boom", "code": "X", "child_execution_id": "exec_c"}


async def test_tool_without_an_invoker_uses_the_guarded_in_memory_one():
    runner, log = make_runner()
    ctx2 = runner._create_context(named(wf([node("n")], []), "wf_parent"), "manual")
    ctx2.workflow_repository = Repo()
    assert (await tool_config()["execute"]({"input": {}}, ctx2))["code"] == sw.SUBWORKFLOW_NOT_FOUND

    # guards apply to the agent tool as well: a workflow can't call itself through a tool
    own = named(wf([node("trigger", "ExecuteWorkflowTrigger")], []), "wf_child")
    ctx3 = runner._create_context(own, "manual")
    ctx3.call_stack = ("wf_child",)
    ctx3.workflow_repository = Repo(own)
    assert (await tool_config()["execute"]({"input": {}}, ctx3))["code"] == sw.SUBWORKFLOW_CYCLE


async def test_tool_requires_a_workflow_id():
    cfg = WorkflowToolNode().get_config(NodeDefinition(name="t", type="WorkflowTool", parameters={}))
    assert (await cfg["execute"]({"input": {}}, ctx_for()))["error"] == "workflowId is not configured"


async def test_tool_wraps_a_non_object_input():
    seen = []

    class Fake:
        async def invoke(self, call):
            seen.append(call.items[0].json)
            return sw.SubworkflowResult([], None, "wf_child", None, "success")

    ctx = ctx_for()
    ctx.subworkflow_invoker = Fake()
    await tool_config()["execute"]({"input": [1, 2]}, ctx)
    assert seen == [{"data": [1, 2]}]
