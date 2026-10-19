"""Node contracts (BaseNode.contract + engine.contracts.node_contract), table-driven per node."""

import copy
from datetime import datetime

import pytest

from src.engine import shapes as sh
from src.engine.contracts import node_contract
from src.engine.node_registry import node_registry, register_all_nodes
from src.engine.shapes import lookup, object_of, scalar
from src.engine.types import ExecutionContext, NodeData, NodeDefinition
from src.repositories.workflow_repository import definition_to_workflow
from src.schemas.workflow import NodeDefinitionSchema

from ._runner_fakes import make_runner, node, wf

register_all_nodes()

S, I = scalar("string"), scalar("integer")


def inp(**props):
    return {"main": object_of(props, list(props))}


def out(type_, params=None, inputs=None, **node_kw):
    """outputs of one node via the single checker entry point."""
    return node_contract(NodeDefinition(name="n", type=type_, parameters=params or {}, **node_kw), inputs if inputs is not None else inp(a=S, b=I))["outputs"]


def props(shape):
    return set(shape["properties"])


# ── default / observed ──

def test_every_registered_node_has_a_contract_with_its_real_ports():
    for t in node_registry.list():
        node_ = node_registry.get(t)
        c = node_.contract({}, inp(a=S))
        assert set(c["outputs"]) == set(node_.output_ports({})), t
        assert set(c) == {"inputs", "outputs", "error"}


@pytest.mark.parametrize("t", ["HttpRequest", "Postgres", "MongoDB", "Neo4j", "ExecuteWorkflow", "Profile", "LLMChat", "AIAgent", "Code", "PythonStep", "DataTable"])
def test_observed_nodes_are_unknown_without_a_contract(t):
    o = out(t, {"table": "t"} if t == "DataTable" else {})
    assert o["main"]["type"] == "unknown"


def test_unknown_node_type_does_not_raise():
    c = node_contract(NodeDefinition(name="n", type="Nope"), inp(a=S))
    assert c["outputs"] == {"main": sh.unknown()}


# ── declared ──

def test_start_default_and_input_schema():
    o = out("Start", inputs={})["main"]
    assert props(o) == {"triggeredAt", "mode"} and o["x-source"] == "declared"
    schema = {"type": "object", "properties": {"q": {"type": "string"}}}
    assert props(out("Start", {"input_schema": schema}, {})["main"]) == {"q"}


def test_webhook_default_and_input_schema():
    o = out("Webhook", inputs={})["main"]
    assert props(o) == {"body", "headers", "query", "method"}
    assert lookup(o, ["body", "anything"])["type"] == "unknown"       # body unknown
    assert lookup(o, ["headers", "x-id"])["type"] == "unknown"        # headers open object
    assert lookup(o, ["nope"]) is None
    schema = {"type": "object", "properties": {"body": {"type": "object", "properties": {"id": {"type": "integer"}}}}}
    assert lookup(out("Webhook", {"input_schema": schema}, {})["main"], ["body", "id"])["type"] == "integer"


def test_cron_and_chat_input_and_error_trigger():
    assert props(out("Cron", inputs={})["main"]) == {"triggeredAt", "mode", "schedule"}
    assert props(out("ChatInput", inputs={})["main"]) == {"message", "timestamp", "session_id", "history", "user_label"}   # Stage 5
    assert props(out("ErrorTrigger", inputs={})["main"]) == {"failedWorkflow", "errors", "timestamp"}


def test_execute_workflow_trigger_schema_plus_underscore_fields():
    assert out("ExecuteWorkflowTrigger", inputs={})["main"]["type"] == "unknown"
    schema = {"type": "object", "properties": {"order_id": {"type": "string"}}}
    m = out("ExecuteWorkflowTrigger", {"inputSchema": schema}, {})["main"]
    assert props(m) == {"order_id", "_triggeredAt", "_triggerType", "_executionDepth"}


def test_code_python_step_llm_agent_output_schema():
    schema = {"type": "object", "properties": {"score": {"type": "number"}}}
    for t in ("Code", "PythonStep", "LLMChat"):
        o = out(t, {"output_schema": schema})["main"]
        assert props(o) == {"score"} and o["x-source"] == "declared", t
    # AIAgent's runtime item is {response, structured: <schema>, toolCalls, iterations}
    o = out("AIAgent", {"output_schema": schema})["main"]
    assert {"response", "structured"} <= props(o) and o["x-source"] == "declared"
    assert props(o["properties"]["structured"]) == {"score"} and o["properties"]["structured"]["x-source"] == "declared"


def test_send_email_declared():
    assert {"success", "to", "subject", "message_id", "error"} <= props(out("SendEmail")["main"])


def test_aggregate_and_sample_declared_metadata():
    assert props(out("Aggregate")["main"]) == {"success", "original_count", "group_count", "columns", "totals", "result_file"}
    assert {"sampled_count", "preview", "reproducibility"} <= props(out("Sample")["main"])


# ── passthrough family ──

@pytest.mark.parametrize("t", ["Filter", "Wait", "Respond"])
def test_passthrough_nodes(t):
    i = inp(a=S, b=I)
    assert out(t, inputs=i)["main"] == i["main"]


def test_if_outputs_true_false_passthrough():
    i = inp(a=S)
    o = out("If", inputs=i)
    assert set(o) == {"true", "false"} and o["true"] == i["main"] == o["false"]


def test_switch_outputs_follow_number_of_outputs_and_fallback():
    assert set(out("Switch", {"numberOfOutputs": 3})) == {"output0", "output1", "output2", "fallback"}
    assert set(out("Switch")) == {"output0", "output1", "fallback"}  # default 2
    assert set(out("Switch", {"numberOfOutputs": "4"})) == {"output0", "output1", "output2", "output3", "fallback"}
    assert out("Switch", inputs=inp(a=S))["output1"] == inp(a=S)["main"]


def test_poll_outputs():
    o = out("Poll")
    assert set(o) == {"loop", "done", "timeout"} and o["done"] == inp(a=S, b=I)["main"]


def test_loop_outputs_loop_and_done():
    o = out("Loop")
    assert set(o) == {"loop", "done"}
    assert o["loop"] == inp(a=S, b=I)["main"]
    assert props(o["done"]) == {"_loop_completed", "total_items", "items"}
    assert o["done"]["properties"]["items"]["items"] == o["loop"]
    it = out("Loop", {"maxIterations": 3})["loop"]
    assert props(it) == {"a", "b", "i"}
    assert out("Loop", {"maxIterations": "{{ $json.n }}"})["loop"]["properties"]["i"]["type"] == "integer"


def test_stop_and_error_modes():
    assert out("StopAndError")["main"] == inp(a=S, b=I)["main"]
    w = out("StopAndError", {"errorType": "warning"})["main"]
    assert props(w) == {"a", "b", "_warning"}


# ── Set ──

def test_set_include_other_adds_fields_and_keeps_input():
    o = out("Set", {"fields": [{"name": "c", "value": "x"}, {"name": "d", "value": "{{ $json.b }}"}, {"name": "e", "value": "{{ 1 + 2 }}"}]})["main"]
    assert props(o) == {"a", "b", "c", "d", "e"}
    assert o["properties"]["c"]["type"] == "string"
    assert o["properties"]["d"]["type"] == "integer"      # copied from $json.b
    assert o["properties"]["e"]["type"] == "unknown"


def test_set_keep_only_set_drops_input_fields():
    o = out("Set", {"keepOnlySet": True, "fields": [{"name": "c", "value": "x"}]})["main"]
    assert props(o) == {"c"}
    assert lookup(o, ["a"]) is None  # definitely gone


def test_set_dotted_names_delete_and_rename():
    o = out("Set", {"fields": [{"name": "user.name", "value": "x"}],
                    "deleteFields": [{"path": "a"}],
                    "renameFields": [{"from": "b", "to": "renamed"}]})["main"]
    assert props(o) == {"user", "renamed"}
    assert lookup(o, ["user", "name"])["type"] == "string"
    assert o["properties"]["renamed"]["type"] == "integer"


def test_set_json_mode():
    o = out("Set", {"mode": "json", "jsonData": '{"x": 1, "y": "s"}', "keepOnlySet": True})["main"]
    assert o["properties"]["x"]["type"] == "integer" and o["properties"]["y"]["type"] == "string"
    assert out("Set", {"mode": "json", "jsonData": '{"x": {{ $json.a }}}'})["main"]["type"] == "unknown"


def test_set_with_unknown_input_and_include_other_is_unknown_but_keep_only_is_known():
    u = {"main": sh.unknown()}
    assert out("Set", {"fields": [{"name": "c", "value": "x"}]}, u)["main"]["type"] == "unknown"
    assert props(out("Set", {"keepOnlySet": True, "fields": [{"name": "c", "value": "x"}]}, u)["main"]) == {"c"}


# ── Merge ──

M2 = {"in0": object_of({"a": S}, ["a"]), "in1": object_of({"b": I}, ["b"])}


def test_merge_append_is_union():
    assert props(out("Merge", {"mode": "append"}, M2)["main"]) == {"a", "b"}
    assert props(out("Merge", None, M2)["main"]) == {"a", "b"}  # default mode


def test_merge_combine_pairs_namespaces_by_input():
    o = out("Merge", {"mode": "combinePairs"}, M2)["main"]
    assert props(o) == {"input0", "input1"} and props(o["properties"]["input1"]) == {"b"}


def test_merge_wait_for_all_and_keep_matches():
    w = out("Merge", {"mode": "waitForAll"}, M2)["main"]
    assert props(w) == {"inputs"} and w["properties"]["inputs"]["items"]["type"] == "array"
    assert props(out("Merge", {"mode": "keepMatches"}, M2)["main"]) == {"a"}


def test_merge_no_inputs_unknown():
    assert out("Merge", {}, {})["main"]["type"] == "unknown"


# ── ItemLists ──

@pytest.mark.parametrize("op", ["sort", "limit", "removeDuplicates", "concatenate"])
def test_item_lists_passthrough_ops(op):
    assert out("ItemLists", {"operation": op})["main"] == inp(a=S, b=I)["main"]


def test_item_lists_aggregate():
    o = out("ItemLists", {"operation": "aggregate", "groupBy": "a", "aggregations": [
        {"field": "b", "aggOperation": "sum"}, {"field": "b", "aggOperation": "count", "outputField": "n"},
        {"field": "b", "aggOperation": "collect"}, {"field": "a", "aggOperation": "first"}]})["main"]
    assert props(o) == {"a", "_count", "b_sum", "n", "b_collect", "a_first"}
    assert o["properties"]["b_sum"]["type"] == "number" and o["properties"]["n"]["type"] == "integer"
    assert o["properties"]["b_collect"]["items"]["type"] == "integer"
    assert o["properties"]["a_first"]["type"] == "string" and o["properties"]["a_first"]["nullable"]


def test_item_lists_summarize():
    o = out("ItemLists", {"operation": "summarize", "summarizeField": "rows"})["main"]
    assert props(o) == {"rows", "count"} and o["properties"]["rows"]["items"]["properties"].keys() == {"a", "b"}
    assert props(out("ItemLists", {"operation": "summarize", "includeCount": False})["main"]) == {"items"}


def test_item_lists_split_out():
    i = {"main": object_of({"id": I, "tags": sh.array_of(S)}, ["id", "tags"])}
    o = out("ItemLists", {"operation": "splitOut", "arrayField": "tags"}, i)["main"]
    assert props(o) == {"id", "tags"} and o["properties"]["tags"]["type"] == "string"
    only = out("ItemLists", {"operation": "splitOut", "arrayField": "tags", "includeOther": False}, i)["main"]
    assert props(only) == {"tags"}
    rows = {"main": object_of({"rows": sh.array_of(object_of({"x": I})), "k": S})}
    assert props(out("ItemLists", {"operation": "splitOut", "arrayField": "rows"}, rows)["main"]) == {"k", "x"}
    assert out("ItemLists", {"operation": "splitOut", "arrayField": "missing"}, i)["main"] == i["main"]


# ── DataTable ──

COLS = {"orders": [{"name": "email", "type": "string", "required": True}, {"name": "total", "type": "number"},
                   {"name": "at", "type": "datetime"}, {"name": "meta", "type": "json"}]}


def test_data_table_read_from_table_columns():
    c = node_contract(NodeDefinition(name="t", type="DataTable", parameters={"table": "orders"}), inp(a=S), table_columns=COLS)
    o = c["outputs"]["main"]
    assert props(o) == {"id", "created_at", "updated_at", "email", "total", "at", "meta"} and o["x-source"] == "declared"
    assert o["properties"]["id"]["type"] == "integer" and o["required"] == ["id", "created_at", "updated_at", "email"]
    assert o["properties"]["created_at"]["format"] == "date-time"
    assert o["properties"]["at"]["format"] == "date-time" and o["properties"]["meta"]["type"] == "unknown"
    assert lookup(o, ["nope"]) is None


def test_data_table_delete_and_missing_table():
    d = node_contract(NodeDefinition(name="t", type="DataTable", parameters={"table": "orders", "operation": "delete"}), {}, table_columns=COLS)
    assert props(d["outputs"]["main"]) == {"id", "deleted"}
    for p in ({"table": "other"}, {"table": "{{ $json.t }}"}, {}):
        r = node_contract(NodeDefinition(name="t", type="DataTable", parameters=p), {}, table_columns=COLS)
        assert r["outputs"]["main"]["type"] == "unknown"


# ── disabled / override / error port ──

def test_disabled_node_is_passthrough_whatever_its_type():
    i = inp(a=S)
    assert out("Set", {"fields": [{"name": "c", "value": "x"}]}, i, disabled=True)["main"] == i["main"]
    assert set(out("If", inputs=i, disabled=True)) == {"main"}
    assert out("HttpRequest", inputs=i, disabled=True)["main"] == i["main"]
    assert props(out("Merge", None, M2, disabled=True)["main"]) == {"a", "b"}


def test_override_wins_for_observed_node_and_is_stamped_observed():
    ov = {"outputs": {"main": {"type": "object", "properties": {"rows": {"type": "array"}}}}, "source": "observed", "approved_by": "me"}
    o = out("Postgres", contract=ov)["main"]
    assert props(o) == {"rows"} and o["x-source"] == "observed"


def test_override_replaces_computed_outputs_of_a_derived_node():
    ov = {"outputs": {"main": object_of({"z": S})}, "approved_by": "me"}
    assert props(out("Filter", contract=ov)["main"]) == {"z"}


def test_override_does_not_apply_to_disabled_node():
    ov = {"outputs": {"main": object_of({"z": S})}}
    assert props(out("Postgres", inputs=inp(a=S), contract=ov, disabled=True)["main"]) == {"a"}


def test_error_output_adds_error_port_with_input_plus_error():
    c = node_contract(NodeDefinition(name="n", type="HttpRequest", on_error="error_output"), inp(a=S))
    e = c["outputs"]["error"]
    assert set(c["outputs"]) == {"main", "error"}
    assert props(e) == {"a", "error"} and props(e["properties"]["error"]) == {"message", "node"}
    assert c["error"] == e
    assert "error" not in out("HttpRequest")  # only with error_output
    assert "error" not in node_contract(NodeDefinition(name="n", type="Set", on_error="continue"), inp(a=S))["outputs"]


def test_node_contract_does_not_mutate_inputs_or_definition():
    i = inp(a=S, b=I)
    nd = NodeDefinition(name="n", type="Set", parameters={"fields": [{"name": "c", "value": "{{ $json.b }}"}]},
                        contract={"outputs": {"main": object_of({"z": S})}})
    before = (copy.deepcopy(i), copy.deepcopy(nd))
    node_contract(nd, i)
    assert (i, nd) == before


def test_contract_exception_falls_back_to_unknown(monkeypatch):
    monkeypatch.setattr(node_registry.get("Set").__class__, "contract", lambda *a, **k: 1 / 0)
    try:
        assert node_contract(NodeDefinition(name="n", type="Set"), inp(a=S))["outputs"]["main"]["type"] == "unknown"
    finally:
        monkeypatch.undo()


# ── the field survives every NodeDefinition rebuild ──

CONTRACT = {"outputs": {"main": {"type": "object", "properties": {"rows": {"type": "array"}}}}, "source": "observed",
            "approved_by": "me", "approved_at": "2026-01-01T00:00:00", "from_execution": "ex1"}


def test_contract_survives_runner_parameter_resolution():
    runner, _ = make_runner()
    nd = node("n", contract=CONTRACT, disabled=True, on_error="error_output")
    ctx = ExecutionContext(workflow=wf([nd], []), execution_id="e", start_time=datetime.now(), mode="manual")
    resolved = runner._resolve_node_parameters(ctx, nd, [NodeData(json={})])
    assert resolved.contract == CONTRACT


def test_contract_survives_schema_and_stored_definition_roundtrip():
    s = NodeDefinitionSchema(name="n", type="Postgres", contract=CONTRACT)
    assert s.to_dataclass().contract == CONTRACT
    assert NodeDefinitionSchema(name="n", type="Set").to_dataclass().contract is None
    stored = {"nodes": [{"name": "n", "type": "Postgres", "contract": CONTRACT}], "connections": []}
    assert definition_to_workflow(stored, "w").nodes[0].contract == CONTRACT
    assert definition_to_workflow({"nodes": [{"name": "n", "type": "Set"}], "connections": []}, "w").nodes[0].contract is None
