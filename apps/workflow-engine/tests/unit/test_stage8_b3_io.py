"""Stage 8 B3: workflow_io (declared inputs, derived output, Approval closure) and ExecuteWorkflow.contract()."""

from __future__ import annotations

from src.engine import shapes as sh
from src.engine.contracts import node_contract
from src.engine.types import NodeDefinition
from src.nodes.flow.execute_workflow import ExecuteWorkflowNode
from src.services.workflow_checker import ChildWorkflow, StaticLookups
from src.services.workflow_io import call_refs, declared_input, derive_io, human_in_closure, version_spec
from tests.unit._stage8_b3_bank import C, N, call, eligibility_child, lookups_with, notify_child, setf, start, trigger, wf


# ── references ──


def test_version_spec_forms():
    assert version_spec(None) == "draft" and version_spec("") == "draft"
    assert version_spec("LATEST") == "latest" and version_spec(3) == "3" and version_spec("4") == "4"
    assert version_spec(0) == "draft" and version_spec(True, "latest") == "latest" and version_spec("junk", "latest") == "latest"


def test_call_refs_static_targets_only():
    d = wf([
        call("A", "wf_a", "latest"),
        N("Legacy", "ExecuteWorkflow", {"workflowId": "wf_b"}),
        call("Expr", "{{ $json.target }}"),
        N("Locator", "ExecuteWorkflow", {"workflowId": {"value": "wf_c"}, "version": 2}),
        N("Tool", "WorkflowTool", {"workflowId": "wf_d"}),
        N("Empty", "ExecuteWorkflow", {"workflowId": " "}),
    ], [])
    refs = {r.node: (r.workflow_id, r.spec) for r in call_refs(d)}
    assert refs == {"A": ("wf_a", "latest"), "Legacy": ("wf_b", "draft"), "Locator": ("wf_c", "2"), "Tool": ("wf_d", "latest")}


# ── declared input ──


def test_declared_input_fields():
    d = declared_input(eligibility_child())
    assert d.has_trigger and d.trigger == "Inputs" and d.source == "fields"
    assert [(f["name"], f["type"], f["required"]) for f in d.fields] == [("order_id", "string", True), ("amount", "number", True), ("segment", "string", False)]
    assert sh.is_closed_object(d.shape) and d.shape["required"] == ["order_id", "amount"]


def test_declared_input_json_schema_and_legacy():
    schema = {"type": "object", "properties": {"iban": {"type": "string"}}, "required": ["iban"]}
    js = declared_input(wf([N("T", "ExecuteWorkflowTrigger", {"inputSource": "jsonSchema", "inputSchema": schema})], []))
    assert js.source == "jsonSchema" and [f["name"] for f in js.fields] == ["iban"] and js.fields[0]["required"]
    legacy = declared_input(wf([N("T", "ExecuteWorkflowTrigger", {"inputSchema": schema})], []))
    assert legacy.source is None and [f["name"] for f in legacy.fields] == ["iban"]  # schema declared the old way still counts
    bare = declared_input(wf([N("T", "ExecuteWorkflowTrigger", {})], []))
    assert bare.has_trigger and sh.is_unknown(bare.shape) and bare.fields == []


def test_declared_input_passthrough_and_missing_trigger():
    p = declared_input(wf([trigger("T", source="passthrough")], []))
    assert p.has_trigger and p.source == "passthrough" and sh.is_unknown(p.shape)
    none = declared_input(wf([setf("A", x="1")], []))
    assert not none.has_trigger and none.trigger is None


def test_declared_input_prefers_enabled_trigger():
    d = wf([{**trigger("Off", ("a", "string", True)), "disabled": True}, trigger("On", ("b", "string", True))], [])
    assert declared_input(d).trigger == "On"


# ── derive_io: output ──


def test_derive_io_output_is_last_node_shape_without_respond():
    io = derive_io(eligibility_child(), StaticLookups())
    assert io.has_trigger and io.callable and io.reasons == []
    assert {"order_id", "amount", "category", "limit"} <= set(io.output_fields)
    assert io.output_note is None and io.input_source == "fields"
    assert [f["name"] for f in io.input_fields] == ["order_id", "amount", "segment"]


def test_derive_io_respond_wins_and_equal_shapes_collapse():
    d = wf(
        [trigger("In", ("x", "string", True)), N("Branch", "If", {"condition": "{{ $json.x == 'a' }}"}),
         setf("A", y="1"), setf("B", y="2"), N("Respond A", "Respond"), N("Respond B", "Respond")],
        [C("In", "Branch"), C("Branch", "A", "true"), C("Branch", "B", "false"), C("A", "Respond A"), C("B", "Respond B")],
    )
    io = derive_io(d, StaticLookups())
    assert io.output_note is None and "y" in io.output_fields and "x" in io.output_fields


def test_derive_io_two_distinct_shapes_union():
    d = wf(
        [trigger("In", ("x", "string", True)), N("Branch", "If", {"condition": "{{ $json.x == 'a' }}"}), setf("A", approved="true"), setf("B", reason="no")],
        [C("In", "Branch"), C("Branch", "A", "true"), C("Branch", "B", "false")],
    )
    io = derive_io(d, StaticLookups())
    assert {"approved", "reason", "x"} <= set(io.output_fields)  # lenient union of the two terminal shapes
    assert io.output_note is None


def test_derive_io_more_than_three_shapes_is_unknown_with_reason():
    nodes = [trigger("In", ("x", "string", True))]
    conns = []
    for i in range(4):
        nodes.append(setf(f"S{i}", **{f"f{i}": "1"}))
        conns.append(C("In", f"S{i}"))
    io = derive_io(wf(nodes, conns), StaticLookups())
    assert sh.is_unknown(io.output) and "4 different shapes" in (io.output_note or "")


def test_derive_io_unknown_terminal_is_unknown():
    d = wf([trigger("In", ("x", "string", True)), N("Call API", "HttpRequest", {"url": "https://api.bank.example/x", "method": "GET"})], [C("In", "Call API")])
    io = derive_io(d, StaticLookups())
    assert sh.is_unknown(io.output) and io.output_note


def test_derive_io_approved_contract_on_return_node_wins():
    d = eligibility_child()
    d["nodes"][1]["contract"] = {"source": "declared", "outputs": {"main": {"type": "object", "properties": {"eligible": {"type": "boolean"}}}}}
    io = derive_io(d, StaticLookups())
    assert io.output_fields == ["eligible"]


def test_derive_io_ignores_unreachable_and_sticky_terminals():
    d = eligibility_child()
    d["nodes"] += [N("Note", "StickyNote", {}), setf("Orphan", z="1")]
    io = derive_io(d, StaticLookups())
    assert "z" not in io.output_fields and io.output_note is None


def test_derive_io_no_trigger_is_not_callable():
    io = derive_io(wf([setf("A", x="1")], []), StaticLookups())
    assert not io.has_trigger and not io.callable and "no Execute Workflow Trigger" in io.reasons[0]
    assert sh.is_unknown(io.input)


def test_derive_io_never_raises_on_garbage():
    assert derive_io(None).callable is False
    assert derive_io({"nodes": [{"name": "x"}, "junk", None], "connections": ["bad"]}).has_trigger is False


# ── Approval closure ──


def _approval_child() -> dict:
    return wf([trigger("In", ("x", "string", True)), N("Manager OK", "Approval", {})], [C("In", "Manager OK")])


def test_human_in_closure_direct_and_nested():
    assert human_in_closure(_approval_child()) == []
    grand = {"gc": ChildWorkflow("gc", "Grandchild", "p", draft=_approval_child(), versions={1: _approval_child()}, published_version=1)}
    mid = wf([trigger("In", ("x", "string", True)), call("Down", "gc", "latest")], [C("In", "Down")])
    lk = StaticLookups(children=grand)
    assert human_in_closure(mid, lk) == ["Grandchild"]
    io = derive_io(mid, lk)
    assert io.has_human and not io.callable and io.human_path == ["Grandchild"]
    assert "Approval" in io.reasons[0] and "Grandchild" in io.reasons[0]


def test_human_in_closure_survives_cycles_and_missing():
    a = wf([trigger("In"), call("ToB", "wf_b", "draft")], [C("In", "ToB")])
    b = wf([trigger("In"), call("ToA", "wf_a", "draft"), call("Gone", "wf_gone", "latest")], [C("In", "ToA"), C("ToA", "Gone")])
    lk = StaticLookups(children={"wf_a": ChildWorkflow("wf_a", "A", "p", draft=a), "wf_b": ChildWorkflow("wf_b", "B", "p", draft=b)})
    assert human_in_closure(a, lk) is None


def test_human_counts_disabled_approval_like_the_runtime_guard():
    d = _approval_child()
    d["nodes"][1]["disabled"] = True
    assert human_in_closure(d) == []


# ── ExecuteWorkflow.contract() ──


def _contract(params: dict, lookups: dict | None, inputs: dict | None = None):
    inputs = inputs or {"main": sh.object_of({"order_id": sh.scalar("string", "declared")}, ["order_id"], "declared")}
    return ExecuteWorkflowNode().contract(params, inputs, lookups)


def test_contract_without_lookups_is_unknown_like_before():
    c = _contract({"workflowId": "wf_elig", "version": "latest"}, None)
    assert sh.is_unknown(c["outputs"]["main"])
    assert c["inputs"]["main"]["properties"]["order_id"]["type"] == "string"  # the upstream item, as for any node


def test_contract_typed_from_child():
    lk = lookups_with({"wf_elig": eligibility_child()})
    c = _contract({"workflowId": "wf_elig", "version": "latest"}, {"workflow_io": lk.workflow_io})
    out = c["outputs"]["main"]
    assert sh.is_closed_object(out) and {"order_id", "amount", "category"} <= set(out["properties"])
    assert c["inputs"]["main"]["required"] == ["order_id", "amount"]  # the child's input contract (section 2.4)
    assert out["x-source"] in ("derived", "declared")


def test_contract_error_shape_carries_child_details():
    lk = lookups_with({"wf_elig": eligibility_child()})
    c = _contract({"workflowId": "wf_elig", "version": 1}, {"workflow_io": lk.workflow_io})
    err = c["error"]
    assert {"error", "code", "child_execution_id", "failed_node", "order_id"} <= set(err["properties"])
    assert "code" in err["required"] and "child_execution_id" not in err["required"]


def test_contract_legacy_merged_and_unresolvable_stay_unknown():
    lk = lookups_with({"wf_elig": eligibility_child()})
    legacy = _contract({"workflowId": "wf_elig"}, {"workflow_io": lk.workflow_io})  # no version = legacy = merged
    assert sh.is_unknown(legacy["outputs"]["main"])
    merged = _contract({"workflowId": "wf_elig", "version": "latest", "outputMode": "merged"}, {"workflow_io": lk.workflow_io})
    assert sh.is_unknown(merged["outputs"]["main"])
    for params in ({"workflowId": "wf_nope", "version": "latest"}, {"workflowId": "{{ $json.t }}", "version": "latest"},
                   {"workflowId": "", "version": "latest"}, {"workflowId": "wf_elig", "version": "junk"}):
        assert sh.is_unknown(_contract(params, {"workflow_io": lk.workflow_io})["outputs"]["main"])


def test_contract_unknown_child_output_stays_unknown_but_inputs_are_typed():
    child = wf([trigger("In", ("x", "string", True)), N("Call", "HttpRequest", {"url": "https://x.example/a", "method": "GET"})], [C("In", "Call")])
    lk = lookups_with({"c": child})
    c = _contract({"workflowId": "c", "version": "latest"}, {"workflow_io": lk.workflow_io})
    assert sh.is_unknown(c["outputs"]["main"]) and sh.is_closed_object(c["inputs"]["main"])


def test_node_contract_entry_point_unchanged_without_lookups():
    nd = NodeDefinition(name="X", type="ExecuteWorkflow", parameters={"workflowId": "wf", "version": "latest"})
    c = node_contract(nd, {"main": sh.unknown()})
    assert sh.is_unknown(c["outputs"]["main"])


# ── lookups cache ──


def test_static_lookups_workflow_io_is_cached_and_cycle_safe():
    d = eligibility_child()
    lk = lookups_with({"a": d})
    assert lk.workflow_io("a", "latest") is lk.workflow_io("a", "latest")
    assert lk.workflow_io("a", 9) is None and lk.workflow_io("missing", "latest") is None
    # a child that (illegally) calls itself must not recurse forever
    me = wf([trigger("In", ("x", "string", True)), call("Self", "me", "draft")], [C("In", "Self")])
    lk2 = StaticLookups(children={"me": ChildWorkflow("me", "Me", "p", draft=me)})
    assert lk2.workflow_io("me", "draft") is not None
    assert notify_child() and start()
