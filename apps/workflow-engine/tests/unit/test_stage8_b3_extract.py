"""Stage 8 B3: convert selection to sub-workflow (DESIGN-STAGE8-REUSE 7.2-7.3): the plan and every refusal CS01-CS10."""

from __future__ import annotations

import copy

import pytest

from src.services.subworkflow_extract import CHILD_ID_PLACEHOLDER, ExtractPlan, Refusal, bind_child_id, extract
from src.services.workflow_checker import ChildWorkflow, StaticLookups, check_workflow
from tests.unit._stage8_b3_bank import PROJECT, C, N, call, setf, start, trigger, wf

SCHEMA = {"type": "object", "properties": {"order_id": {"type": "string"}, "amount": {"type": "number"}}, "required": ["order_id"]}


def at(n: dict, x: int, y: int = 0) -> dict:
    return {**n, "position": {"x": x, "y": y}}


def bank() -> dict:
    """Start -> Customer lookup -> Classify -> Compute fee -> Notify; Compute fee reads Customer lookup, Notify reads Compute fee."""
    return wf(
        [
            at(start(SCHEMA), 0),
            at(setf("Customer lookup", tier="premier", name="A. Customer"), 200),
            at(setf("Classify", category="{{ $json.amount }}"), 400),
            at(setf("Compute fee", fee='{{ $node["Customer lookup"].json.tier }}'), 600),
            at(setf("Notify", msg="{{ $node['Compute fee'].json.fee }}"), 800),
        ],
        [C("Start", "Customer lookup"), C("Customer lookup", "Classify"), C("Classify", "Compute fee"), C("Compute fee", "Notify")],
    )


def ok(r) -> ExtractPlan:
    assert isinstance(r, ExtractPlan), getattr(r, "message", r)
    return r


def refused(r, code: str) -> Refusal:
    assert isinstance(r, Refusal), "expected a refusal"
    assert r.code == code, (r.code, r.message)
    return r


def node(defn: dict, name: str) -> dict:
    return next(n for n in defn["nodes"] if n["name"] == name)


def conns(defn: dict) -> set[tuple]:
    return {(c["source_node"], c["source_output"], c["target_node"], c["target_input"]) for c in defn["connections"]}


def codes(res, *, severity=None) -> list[str]:
    return [f["code"] for f in res["findings"] if severity is None or f["severity"] == severity]


# ── the plan ──


def test_linear_selection_becomes_one_call_in_place():
    plan = ok(extract(bank(), ["Classify", "Compute fee"], "Fee calc"))
    parent = plan.parent_definition
    assert [n["name"] for n in parent["nodes"]] == ["Start", "Customer lookup", "Notify", "Fee calc"]
    assert conns(parent) == {("Start", "main", "Customer lookup", "main"), ("Customer lookup", "main", "Fee calc", "main"), ("Fee calc", "main", "Notify", "main")}
    child = plan.child_definition
    assert [n["name"] for n in child["nodes"]] == ["Inputs", "Classify", "Compute fee"]
    assert conns(child) == {("Inputs", "main", "Classify", "main"), ("Classify", "main", "Compute fee", "main")}
    assert plan.entry == "Classify" and plan.exit == {"node": "Compute fee", "port": "main", "return_node": None}
    assert plan.call_node == "Fee calc" and plan.trigger_node == "Inputs" and plan.selection == ["Classify", "Compute fee"]


def test_call_node_parameters():
    plan = ok(extract(bank(), ["Classify", "Compute fee"], "Fee calc"))
    n = node(plan.parent_definition, "Fee calc")
    p = n["parameters"]
    assert n["type"] == "ExecuteWorkflow" and n["on_error"] == "stop"
    assert (p["version"], p["inputMode"], p["includeItem"], p["mode"], p["workflowId"]) == ("latest", "map", True, "once", CHILD_ID_PLACEHOLDER)
    assert p["inputs"] == {"Customer_lookup_tier": '{{ $node["Customer lookup"].json.tier }}'}


def test_child_trigger_fields_come_from_what_reaches_the_selection():
    plan = ok(extract(bank(), ["Classify", "Compute fee"], "Fee calc"))
    p = node(plan.child_definition, "Inputs")["parameters"]
    assert p["inputSource"] == "fields"
    by = {f["name"]: f for f in p["fields"]}
    assert by["order_id"] == {"name": "order_id", "type": "string", "required": True, "description": ""}
    assert by["amount"]["type"] == "number" and by["amount"]["required"] is False
    assert by["tier"]["type"] == "string"  # flows through from Customer lookup
    assert by["Customer_lookup_tier"]["required"] is True and by["Customer_lookup_tier"]["type"] == "string"
    assert [i["origin"] for i in plan.inputs if i["name"] == "Customer_lookup_tier"] == [{"node": "Customer lookup", "path": "tier"}]


def test_outside_reference_is_rewritten_to_the_trigger_field():
    plan = ok(extract(bank(), ["Classify", "Compute fee"], "Fee calc"))
    assert node(plan.child_definition, "Compute fee")["parameters"]["fields"][0]["value"] == '{{ $node["Inputs"].json.Customer_lookup_tier }}'
    assert node(plan.child_definition, "Classify")["parameters"]["fields"][0]["value"] == "{{ $json.amount }}"  # $json stays


def test_reference_to_the_exit_from_outside_follows_the_call_node():
    plan = ok(extract(bank(), ["Classify", "Compute fee"], "Fee calc"))
    assert node(plan.parent_definition, "Notify")["parameters"]["fields"][0]["value"] == "{{ $node[\"Fee calc\"].json.fee }}"


def test_dot_form_references_are_handled_both_ways():
    d = bank()
    node(d, "Compute fee")["parameters"]["fields"][0]["value"] = "{{ $node.Customer_lookup.json.tier }}"
    d["nodes"][1]["name"] = "Customer_lookup"
    d["connections"] = [C("Start", "Customer_lookup"), C("Customer_lookup", "Classify"), C("Classify", "Compute fee"), C("Compute fee", "Notify")]
    node(d, "Notify")["parameters"]["fields"] = [{"name": "m", "value": "{{ $node['Compute fee'].json.fee }}"}]
    plan = ok(extract(d, ["Classify", "Compute fee"], "Fee calc"))
    assert node(plan.child_definition, "Compute fee")["parameters"]["fields"][0]["value"] == '{{ $node["Inputs"].json.Customer_lookup_tier }}'
    assert node(plan.parent_definition, "Notify")["parameters"]["fields"][0]["value"] == '{{ $node["Fee calc"].json.fee }}'


def test_same_outside_value_is_one_field_and_names_do_not_collide():
    d = bank()
    node(d, "Compute fee")["parameters"]["fields"] = [
        {"name": "a", "value": '{{ $node["Customer lookup"].json.tier }}'},
        {"name": "b", "value": '{{ $node["Customer lookup"].json.tier == "x" }}'},
        {"name": "c", "value": '{{ $node["Customer lookup"].json.name }}'},
    ]
    plan = ok(extract(d, ["Classify", "Compute fee"], "Fee calc"))
    assert set(node(plan.parent_definition, "Fee calc")["parameters"]["inputs"]) == {"Customer_lookup_tier", "Customer_lookup_name"}
    values = [f["value"] for f in node(plan.child_definition, "Compute fee")["parameters"]["fields"]]
    assert values[0] == values[1].replace(' == "x"', "") == '{{ $node["Inputs"].json.Customer_lookup_tier }}'


def test_field_name_collision_gets_a_suffix():
    d = bank()
    node(d, "Compute fee")["parameters"]["fields"] = [
        {"name": "a", "value": '{{ $node["Customer lookup"].json.tier }}'},
    ]
    d["nodes"].insert(2, setf("Customer_lookup", tier="x"))
    d["connections"] = [C("Start", "Customer lookup"), C("Customer lookup", "Customer_lookup"), C("Customer_lookup", "Classify"),
                        C("Classify", "Compute fee"), C("Compute fee", "Notify")]
    node(d, "Compute fee")["parameters"]["fields"].append({"name": "b", "value": '{{ $node["Customer_lookup"].json.tier }}'})
    plan = ok(extract(d, ["Classify", "Compute fee"], "Fee calc"))
    assert set(node(plan.parent_definition, "Fee calc")["parameters"]["inputs"]) == {"Customer_lookup_tier", "Customer_lookup_tier_2"}


def test_nested_path_reference_keeps_its_full_path():
    d = bank()
    node(d, "Customer lookup")["parameters"]["fields"].append({"name": "address", "value": "x"})
    node(d, "Compute fee")["parameters"]["fields"] = [{"name": "z", "value": '{{ $node["Customer lookup"].json.name.upper() }}'}]
    r = extract(d, ["Classify", "Compute fee"], "Fee calc")
    assert isinstance(r, Refusal) and r.code == "CS07"  # a method call on the value is not a plain field


def test_child_and_parent_pass_the_checker():
    plan = ok(extract(bank(), ["Classify", "Compute fee"], "Fee calc"))
    child_res = check_workflow(plan.child_definition, lookups=StaticLookups())
    assert [f for f in child_res["findings"] if f["severity"] == "error"] == []
    parent = bind_child_id(plan, "wf_fee")
    child_pub = ChildWorkflow("wf_fee", "Fee calc", PROJECT, draft=plan.child_definition, versions={1: plan.child_definition}, published_version=1)
    lk = StaticLookups(children={"wf_fee": child_pub}, current_project_id=PROJECT, current_workflow_id="wf_main")
    res = check_workflow(parent, lookups=lk)
    assert [f for f in res["findings"] if f["severity"] == "error"] == []
    assert not any(f["code"].startswith("WF02") and f["severity"] != "info" for f in res["findings"])


def test_bind_child_id_does_not_touch_the_plan():
    plan = ok(extract(bank(), ["Classify", "Compute fee"], "Fee calc"))
    bound = bind_child_id(plan, "wf_123")
    assert node(bound, "Fee calc")["parameters"]["workflowId"] == "wf_123"
    assert node(plan.parent_definition, "Fee calc")["parameters"]["workflowId"] == CHILD_ID_PLACEHOLDER
    assert ok(extract(bank(), ["Classify"], "X", child_workflow_id="wf_given")).parent_definition["nodes"][-1]["parameters"]["workflowId"] == "wf_given"


def test_the_input_definition_is_never_mutated():
    d = bank()
    before = copy.deepcopy(d)
    extract(d, ["Classify", "Compute fee"], "Fee calc")
    extract(d, ["Customer lookup", "Compute fee"], "Bad")
    assert d == before


def test_selection_order_and_duplicates_do_not_matter():
    a = ok(extract(bank(), ["Compute fee", "Classify", "Classify"], "Fee calc"))
    b = ok(extract(bank(), ["Classify", "Compute fee"], "Fee calc"))
    assert a.to_dict() == b.to_dict()


def test_selection_at_the_end_of_the_graph():
    plan = ok(extract(bank(), ["Compute fee", "Notify"], "Tail"))
    assert plan.exit["node"] == "Notify" and plan.warnings == []
    assert conns(plan.parent_definition) == {("Start", "main", "Customer lookup", "main"), ("Customer lookup", "main", "Classify", "main"), ("Classify", "main", "Tail", "main")}


def test_single_node_selection_is_fine():
    plan = ok(extract(bank(), ["Classify"], "Only"))
    assert plan.entry == "Classify" and plan.exit["node"] == "Classify"


def test_positions_child_is_normalised_and_call_sits_at_the_centroid():
    plan = ok(extract(bank(), ["Classify", "Compute fee"], "Fee calc"))
    assert node(plan.child_definition, "Classify")["position"] == {"x": 320.0, "y": 120.0}
    assert node(plan.child_definition, "Compute fee")["position"] == {"x": 520.0, "y": 120.0}
    assert node(plan.child_definition, "Inputs")["position"]["x"] < 320
    assert node(plan.parent_definition, "Fee calc")["position"] == {"x": 500.0, "y": 0.0}


def test_nodes_without_positions_are_fine():
    d = bank()
    for n in d["nodes"]:
        n.pop("position")
    plan = ok(extract(d, ["Classify", "Compute fee"], "Fee calc"))
    assert "position" not in node(plan.parent_definition, "Fee calc")


def test_selected_nodes_keep_their_settings():
    d = bank()
    node(d, "Classify").update({"on_error": "continue", "retry_on_fail": 3, "label": "Classify it", "disabled": True,
                                "pinned_data": [{"json": {"a": 1}}]})
    plan = ok(extract(d, ["Classify", "Compute fee"], "Fee calc"))
    c = node(plan.child_definition, "Classify")
    assert (c["on_error"], c["retry_on_fail"], c["label"], c["disabled"], c["pinned_data"]) == ("continue", 3, "Classify it", True, [{"json": {"a": 1}}])


def test_call_name_and_trigger_name_avoid_clashes():
    d = bank()
    node(d, "Classify")["name"] = "Inputs"
    d["connections"] = [C("Start", "Customer lookup"), C("Customer lookup", "Inputs"), C("Inputs", "Compute fee"), C("Compute fee", "Notify")]
    plan = ok(extract(d, ["Inputs", "Compute fee"], "Notify"))
    assert plan.trigger_node == "Inputs 2" and plan.call_node == "Notify 2"
    assert [n["name"] for n in plan.child_definition["nodes"]][:2] == ["Inputs 2", "Inputs"]


def test_a_blank_name_still_gets_a_usable_one():
    assert ok(extract(bank(), ["Classify"], "  ")).call_node == "Sub-workflow"


# ── shapes, entry, exit ──


def test_fan_in_to_one_entry_is_allowed():
    d = wf([at(start(SCHEMA), 0), setf("A", a="1"), setf("B", b="1"), setf("Join", j="1"), setf("After", z="1")],
           [C("Start", "A"), C("Start", "B"), C("A", "Join"), C("B", "Join"), C("Join", "After")])
    plan = ok(extract(d, ["Join"], "Joined"))
    assert conns(plan.parent_definition) >= {("A", "main", "Joined", "main"), ("B", "main", "Joined", "main"), ("Joined", "main", "After", "main")}
    assert len([c for c in plan.parent_definition["connections"] if c["target_node"] == "Joined"]) == 2


def test_exit_through_a_branch_port_adds_a_return_node():
    d = wf([start(SCHEMA), setf("Prep", p="1"), N("Over limit?", "If", {"condition": "{{ $json.amount > 500 }}"}), setf("Manager", m="1"), setf("Auto", a="1")],
           [C("Start", "Prep"), C("Prep", "Over limit?"), C("Over limit?", "Manager", "true"), C("Over limit?", "Auto", "false")])
    refused(extract(d, ["Prep", "Over limit?"], "Triage"), "CS04")  # both branches leave: two exits
    d2 = wf([start(SCHEMA), setf("Prep", p="1"), N("Over limit?", "If", {"condition": "{{ $json.amount > 500 }}"}), setf("Manager", m="1")],
            [C("Start", "Prep"), C("Prep", "Over limit?"), C("Over limit?", "Manager", "true")])
    plan = ok(extract(d2, ["Prep", "Over limit?"], "Triage"))
    assert plan.exit == {"node": "Over limit?", "port": "true", "return_node": "Return"}
    child = plan.child_definition
    assert node(child, "Return")["type"] == "NoOp" and ("Over limit?", "true", "Return", "main") in conns(child)
    assert conns(plan.parent_definition) >= {("Triage", "main", "Manager", "main")}  # the call's output is main
    assert plan.warnings == []


def test_return_name_avoids_clashes():
    d = wf([start(SCHEMA), setf("Return", p="1"), N("Gate", "If", {"condition": "{{ $json.amount > 5 }}"}), setf("Next", n="1")],
           [C("Start", "Return"), C("Return", "Gate"), C("Gate", "Next", "true")])
    plan = ok(extract(d, ["Return", "Gate"], "Gate flow"))
    assert plan.exit["return_node"] == "Return 2"


def test_exit_error_output_moves_to_the_call_node():
    d = wf([start(SCHEMA), {**setf("Pay", paid="1"), "on_error": "error_output"}, setf("Done", d="1"), setf("Alert", a="1")],
           [C("Start", "Pay"), C("Pay", "Done"), C("Pay", "Alert", "error")])
    plan = ok(extract(d, ["Pay"], "Payment"))
    call_node = node(plan.parent_definition, "Payment")
    assert call_node["on_error"] == "error_output"
    assert conns(plan.parent_definition) >= {("Payment", "main", "Done", "main"), ("Payment", "error", "Alert", "main")}
    assert node(plan.child_definition, "Pay")["on_error"] == "stop"
    assert "CSW2" in [w["code"] for w in plan.warnings]


def test_unknown_entry_shape_falls_back_to_passthrough():
    d = wf([N("Fetch", "HttpRequest", {"url": "https://core.bank.example/a", "method": "GET"}), setf("Use", u="1"), setf("After", a="1")],
           [C("Fetch", "Use"), C("Use", "After")])
    d["nodes"].insert(0, N("Hook", "Webhook", {}))
    d["connections"].insert(0, C("Hook", "Fetch"))
    plan = ok(extract(d, ["Use"], "Use it"))
    assert node(plan.child_definition, "Inputs")["parameters"]["inputSource"] == "passthrough"
    assert [w["code"] for w in plan.warnings] == ["WF029"]


def test_outside_reference_of_unknown_type_makes_the_trigger_passthrough():
    d = bank()
    d["nodes"][1] = N("Customer lookup", "HttpRequest", {"url": "https://crm.bank.example/c", "method": "GET"}, position={"x": 200, "y": 0})
    plan = ok(extract(d, ["Classify", "Compute fee"], "Fee calc"))
    assert node(plan.child_definition, "Inputs")["parameters"]["inputSource"] == "passthrough"
    assert "WF029" in [w["code"] for w in plan.warnings]
    assert "Customer_lookup_tier" in node(plan.parent_definition, "Fee calc")["parameters"]["inputs"]  # still mapped


def test_selection_with_no_outgoing_edge_and_several_ends_warns():
    d = wf([start(SCHEMA), setf("Split", s="1"), setf("Log", l="1"), setf("Mail", m="1")], [C("Start", "Split"), C("Split", "Log"), C("Split", "Mail")])
    plan = ok(extract(d, ["Split", "Log", "Mail"], "Fan out"))
    assert plan.exit["node"] is None and [w["code"] for w in plan.warnings] == ["CSW1"]


def test_an_orphan_start_node_can_be_the_entry():
    d = wf([setf("Seed", s="1"), setf("Next", n="1")], [C("Seed", "Next")])
    plan = ok(extract(d, ["Seed", "Next"], "No trigger"))
    assert plan.entry == "Seed" and node(plan.child_definition, "Inputs")["parameters"]["inputSource"] == "passthrough"
    assert plan.parent_definition["connections"] == []


def test_a_whole_loop_can_move():
    d = wf([start(SCHEMA), N("Each", "Loop", {"batchSize": 1}), setf("Body", b="1"), setf("After", a="1")],
           [C("Start", "Each"), C("Each", "Body", "loop"), C("Body", "Each"), C("Each", "After", "done")])
    plan = ok(extract(d, ["Each", "Body"], "Loop body"))
    assert plan.exit == {"node": "Each", "port": "done", "return_node": "Return"}
    assert ("Body", "main", "Each", "main") in conns(plan.child_definition)


def test_other_definition_keys_survive():
    d = bank()
    d["settings"] = {"timezone": "UTC", "max_execution_time": 600}
    plan = ok(extract(d, ["Classify"], "X"))
    assert plan.parent_definition["settings"] == {"timezone": "UTC", "max_execution_time": 600}
    assert plan.child_definition["settings"] == {}


# ── tests of the parent (step 8) ──


def with_tests() -> dict:
    d = bank()
    d["tests"] = [
        {"id": "t_00000001", "name": "fee shape", "target": {"kind": "workflow"}, "input": {"items": [{"order_id": "o"}]},
         "mocks": {"Compute fee": {"items": [{"fee": "5"}]}}, "expect": [{"node": "Compute fee", "op": "status", "value": "success"}],
         "covers": ["Compute fee"], "status": "approved", "locked": True},
        {"id": "t_00000002", "name": "category", "target": {"kind": "workflow"}, "input": {"items": []}, "mocks": {},
         "expect": [{"node": "Classify", "path": "category", "op": "exists"}, {"node": "Notify", "op": "status", "value": "success"}], "covers": ["Classify", "Notify"]},
        {"id": "t_00000003", "name": "node test", "target": {"kind": "node", "node": "Compute fee"}, "input": {"items": []}, "mocks": {}, "expect": []},
        {"id": "t_00000004", "name": "untouched", "target": {"kind": "workflow"}, "input": {"items": []}, "mocks": {"Notify": {"items": [{}]}},
         "expect": [{"node": "Notify", "op": "status", "value": "success"}]},
    ]
    d["test_defaults"] = {"mocks": {"Compute fee": {"http": {"status": 201, "body": {"fee": 5}}}, "Classify": {"items": [{"category": "a"}]}, "Notify": {"items": [{}]}}}
    return d


def test_tests_on_the_exit_move_to_the_call():
    plan = ok(extract(with_tests(), ["Classify", "Compute fee"], "Fee calc"))
    t1 = plan.parent_definition["tests"][0]
    assert t1["mocks"] == {"Fee calc": {"items": [{"fee": "5"}]}}
    assert t1["expect"][0]["node"] == "Fee calc" and t1["covers"] == ["Fee calc"]
    assert plan.parent_definition["tests"][2]["target"] == {"kind": "node", "node": "Fee calc"}


def test_tests_on_other_inner_nodes_are_reported_not_deleted():
    plan = ok(extract(with_tests(), ["Classify", "Compute fee"], "Fee calc"))
    assert plan.affected_tests == [{"id": "t_00000002", "name": "category", "nodes": ["Classify"]}]
    t2 = plan.parent_definition["tests"][1]
    assert t2["expect"][0]["node"] == "Classify" and len(plan.parent_definition["tests"]) == 4


def test_tests_that_do_not_touch_the_selection_are_unchanged():
    d = with_tests()
    plan = ok(extract(d, ["Classify", "Compute fee"], "Fee calc"))
    assert plan.parent_definition["tests"][3] == d["tests"][3]


def test_default_mocks_split_between_parent_and_child():
    plan = ok(extract(with_tests(), ["Classify", "Compute fee"], "Fee calc"))
    pm = plan.parent_definition["test_defaults"]["mocks"]
    assert pm["Notify"] == {"items": [{}]}
    assert pm["Fee calc"] == {"items": [{"statusCode": 201, "headers": {}, "body": {"fee": 5}}]}  # an http mock becomes the items it stands for
    assert "Compute fee" not in pm and "Classify" not in pm
    assert plan.child_definition["test_defaults"]["mocks"] == {"Classify": {"items": [{"category": "a"}]}}


def test_test_mocks_of_inner_nodes_move_into_the_child_defaults():
    d = with_tests()
    d["tests"][1]["mocks"] = {"Classify": {"items": [{"category": "z"}]}}
    plan = ok(extract(d, ["Classify", "Compute fee"], "Fee calc"))
    assert plan.child_definition["test_defaults"]["mocks"]["Classify"] == {"items": [{"category": "z"}]}
    assert plan.parent_definition["tests"][1]["mocks"] == {}


def test_a_draft_test_is_generated_for_the_child():
    plan = ok(extract(bank(), ["Classify", "Compute fee"], "Fee calc"))
    (t,) = plan.child_definition["tests"]
    assert t["status"] == "draft" and t["locked"] is False and t["target"] == {"kind": "workflow"} and t["id"].startswith("t_") and len(t["id"]) == 10
    assert t["input"]["items"][0]["order_id"] == "example" and t["input"]["items"][0]["amount"] == 1
    assert {"node": "Compute fee", "op": "status", "value": "success"} in t["expect"]
    sampled = ok(extract(bank(), ["Classify"], "X", sample_input=[{"order_id": "o-9", "amount": 5}]))
    assert sampled.child_definition["tests"][0]["input"]["items"] == [{"order_id": "o-9", "amount": 5}]


def test_the_return_node_is_what_the_child_test_asserts_on():
    d = wf([start(SCHEMA), setf("Prep", p="1"), N("Gate", "If", {"condition": "{{ $json.amount > 5 }}"}), setf("Next", n="1")],
           [C("Start", "Prep"), C("Prep", "Gate"), C("Gate", "Next", "true")])
    plan = ok(extract(d, ["Prep", "Gate"], "Gate flow"))
    assert plan.child_definition["tests"][0]["expect"][0]["node"] == "Return"


def test_interface_result_follows_the_exit():
    d = bank()
    d["interface"] = {"kind": "form", "trigger": "Start", "result": "Compute fee"}
    plan = ok(extract(d, ["Classify", "Compute fee"], "Fee calc"))
    assert plan.parent_definition["interface"]["result"] == "Fee calc"
    d["interface"]["result"] = "Classify"
    plan = ok(extract(d, ["Classify", "Compute fee"], "Fee calc"))
    assert "CSW4" in [w["code"] for w in plan.warnings]


# ── refusals ──


def test_cs01_empty_unknown_and_trivial_selection():
    assert refused(extract(bank(), [], "X"), "CS01").message == "Select the steps to move."
    assert "isn't in this workflow" in refused(extract(bank(), ["Nope"], "X"), "CS01").message
    d = bank()
    d["nodes"].append(N("Memo", "StickyNote", {}))
    refused(extract(d, ["Memo"], "X"), "CS01")
    d["nodes"].append(N("Pass", "NoOp", {}))
    refused(extract(d, ["Pass"], "X"), "CS01")


def test_cs02_trigger_in_selection():
    r = refused(extract(bank(), ["Start", "Customer lookup"], "X"), "CS02")
    assert r.message == "Triggers stay in the parent." and r.nodes == ["Start"]
    for t in ("Webhook", "Cron", "FormTrigger", "ChatInput", "ExecuteWorkflowTrigger", "ErrorTrigger"):
        d = wf([N("T", t, {}), setf("A", a="1")], [C("T", "A")])
        refused(extract(d, ["T", "A"], "X"), "CS02")


def test_cs03_two_starting_points():
    d = wf([start(SCHEMA), setf("A", a="1"), setf("B", b="1"), setf("C", c="1")], [C("Start", "A"), C("Start", "B"), C("A", "C")])
    r = refused(extract(d, ["A", "B"], "X"), "CS03")
    assert r.message == "The selection has 2 starting points (A, B). Include the step where they meet, or select less." and r.nodes == ["A", "B"]


def test_cs03_a_second_root_inside_the_selection_counts():
    d = wf([start(SCHEMA), setf("A", a="1"), setf("Stray", s="1")], [C("Start", "A")])
    refused(extract(d, ["A", "Stray"], "X"), "CS03")


def test_cs03_entry_that_takes_several_inputs():
    d = wf([start(SCHEMA), setf("A", a="1"), setf("B", b="1"), N("Combine", "Merge", {}), setf("After", z="1")],
           [C("Start", "A"), C("Start", "B"), C("A", "Combine"), C("B", "Combine"), C("Combine", "After")])
    r = refused(extract(d, ["Combine", "After"], "X"), "CS03")
    assert "takes more than one input" in r.message and r.nodes == ["Combine"]


def test_cs04_two_exits():
    d = wf([start(SCHEMA), setf("A", a="1"), setf("B", b="1"), setf("C", c="1")], [C("Start", "A"), C("A", "B"), C("A", "C")])
    ok(extract(d, ["A"], "X"))  # one node, one exit port: both targets hang off 'main'
    d2 = wf([start(SCHEMA), N("Gate", "If", {"condition": "{{ $json.amount > 5 }}"}), setf("Yes", y="1"), setf("No", n="1")],
            [C("Start", "Gate"), C("Gate", "Yes", "true"), C("Gate", "No", "false")])
    r = refused(extract(d2, ["Gate"], "X"), "CS04")
    assert r.message == "The selection hands data out from 2 places; a sub-workflow returns one."
    assert r.nodes == ["Gate"]


def test_cs04_two_nodes_each_handing_data_out():
    d = wf([start(SCHEMA), setf("A", a="1"), setf("B", b="1"), setf("X", x="1"), setf("Y", y="1")],
           [C("Start", "A"), C("A", "B"), C("A", "X"), C("B", "Y")])
    r = refused(extract(d, ["A", "B"], "T"), "CS04")
    assert r.nodes == ["A", "B"]


def test_cs04_errors_from_a_node_that_is_not_the_exit():
    d = wf([start(SCHEMA), {**setf("A", a="1"), "on_error": "error_output"}, setf("B", b="1"), setf("Alert", a="1")],
           [C("Start", "A"), C("A", "B"), C("A", "Alert", "error")])
    r = refused(extract(d, ["A", "B"], "X"), "CS04")
    assert "errors" in r.message and r.nodes == ["A"]


def test_cs05_not_convex():
    r = refused(extract(bank(), ["Customer lookup", "Compute fee"], "X"), "CS05")
    assert r.message == "Classify sits between selected steps; include it or leave both out." and r.nodes == ["Classify"]


def test_cs05_several_between():
    d = wf([start(SCHEMA), setf("A", a="1"), setf("M1", m="1"), setf("M2", m="2"), setf("Z", z="1")],
           [C("Start", "A"), C("A", "M1"), C("M1", "M2"), C("M2", "Z")])
    r = refused(extract(d, ["A", "Z"], "X"), "CS05")
    assert r.nodes == ["M1", "M2"] and "sit between" in r.message


def test_cs05_detour_through_a_side_branch():
    d = wf([start(SCHEMA), setf("A", a="1"), setf("Side", s="1"), setf("B", b="1")], [C("Start", "A"), C("A", "B"), C("A", "Side"), C("Side", "B")])
    refused(extract(d, ["A", "B"], "X"), "CS05")


def test_cs06_approval_stays():
    d = wf([start(SCHEMA), setf("A", a="1"), N("Manager OK", "Approval", {}), setf("B", b="1")], [C("Start", "A"), C("A", "Manager OK"), C("Manager OK", "B")])
    r = refused(extract(d, ["A", "Manager OK"], "X"), "CS06")
    assert r.message == "Approvals stay in the top-level workflow." and r.nodes == ["Manager OK"]


def test_cs06_a_call_to_a_child_with_an_approval_cannot_move():
    child = wf([trigger("Inputs"), N("Sign off", "Approval", {})], [C("Inputs", "Sign off")])
    lk = StaticLookups(children={"wf_sign": ChildWorkflow("wf_sign", "Sign", PROJECT, draft=child, versions={1: child}, published_version=1)})
    d = wf([start(SCHEMA), call("Ask", "wf_sign", "latest"), setf("After", a="1")], [C("Start", "Ask"), C("Ask", "After")])
    r = refused(extract(d, ["Ask", "After"], "X", lk), "CS06")
    assert r.nodes == ["Ask"]
    ok(extract(d, ["Ask", "After"], "X"))  # without lookups the closure isn't known: the runtime guard (WF025) still protects


def test_cs06_respond_stays_in_a_webhook_workflow():
    d = wf([N("Hook", "Webhook", {}), setf("A", a="1"), N("Answer", "Respond", {})], [C("Hook", "A"), C("A", "Answer")])
    r = refused(extract(d, ["A", "Answer"], "X"), "CS06")
    assert r.message == "This Respond answers the caller; it can't move." and r.nodes == ["Answer"]
    d2 = wf([N("Tick", "Cron", {}), setf("A", a="1"), N("Answer", "Respond", {})], [C("Tick", "A"), C("A", "Answer")])
    ok(extract(d2, ["A", "Answer"], "X"))  # nobody to answer in a scheduled workflow
    d3 = copy.deepcopy(d2)
    d3["interface"] = {"kind": "form"}
    refused(extract(d3, ["A", "Answer"], "X"), "CS06")


def test_cs07_outside_reference_that_is_not_a_plain_field():
    for expr in ('{{ $node["Customer lookup"].json }}', '{{ $node["Customer lookup"].json.get("tier") }}',
                 '{{ $node["Customer lookup"] }}', '{{ $node["Customer lookup"].json.tier.lower() }}'):
        d = bank()
        node(d, "Compute fee")["parameters"]["fields"] = [{"name": "fee", "value": expr}]
        r = refused(extract(d, ["Classify", "Compute fee"], "X"), "CS07")
        assert r.nodes == ["Compute fee", "Customer lookup"] and "Customer lookup" in r.message and "Compute fee" in r.message


def test_cs07_names_the_expression_and_parameter():
    d = bank()
    node(d, "Compute fee")["parameters"]["fields"] = [{"name": "fee", "value": '{{ $node["Customer lookup"].json }}'}]
    r = refused(extract(d, ["Classify", "Compute fee"], "X"), "CS07")
    assert 'fields[0].value' in r.message and '$node["Customer lookup"].json' in r.message


def test_cs08_outside_node_reads_an_inner_node():
    d = bank()
    node(d, "Notify")["parameters"]["fields"] = [{"name": "m", "value": '{{ $node["Classify"].json.category }}'}]
    r = refused(extract(d, ["Classify", "Compute fee"], "X"), "CS08")
    assert r.nodes == ["Notify", "Classify"] and "Notify" in r.message and "Classify" in r.message


def test_cs08_only_the_exit_may_be_read():
    d = bank()
    node(d, "Notify")["parameters"]["fields"] = [{"name": "m", "value": '{{ $node["Compute fee"].json.fee }}'}]
    ok(extract(d, ["Classify", "Compute fee"], "X"))


def test_cs09_agent_tool_crossing():
    d = wf([start(SCHEMA), N("Agent", "AIAgent", {}), N("Lookup tool", "WorkflowTool", {"workflowId": "wf_x"})], [C("Start", "Agent"), C("Lookup tool", "Agent", "main", "tool")])
    r = refused(extract(d, ["Agent"], "X"), "CS09")
    assert r.message == "Move the agent together with its tools." and set(r.nodes) == {"Lookup tool", "Agent"}


def test_cs09_part_of_a_loop():
    d = wf([start(SCHEMA), N("Each", "Loop", {"batchSize": 1}), setf("Body", b="1"), setf("After", a="1")],
           [C("Start", "Each"), C("Each", "Body", "loop"), C("Body", "Each"), C("Each", "After", "done")])
    for sel in (["Body"], ["Each"]):
        r = refused(extract(d, sel, "X"), "CS09")
        assert r.message == "Select the whole loop." and r.nodes == ["Each", "Body"]


def test_cs10_child_already_calls_the_parent():
    d = wf([start(SCHEMA), call("To refund", "wf_other", "latest"), setf("After", a="1")], [C("Start", "To refund"), C("To refund", "After")])
    back = wf([trigger("Inputs"), call("Back", "wf_main", "latest")], [C("Inputs", "Back")])
    lk = StaticLookups(children={"wf_other": ChildWorkflow("wf_other", "Other", PROJECT, draft=back, versions={1: back}, published_version=1)})
    r = refused(extract(d, ["To refund", "After"], "Piece", lk, parent_workflow_id="wf_main", parent_name="Refunds"), "CS10")
    assert r.message == "This would make 'Refunds' and 'Piece' call each other." and r.nodes == ["To refund"]
    ok(extract(d, ["To refund", "After"], "Piece", StaticLookups(), parent_workflow_id="wf_main"))  # nothing known about wf_other: no refusal
    selfcall = wf([start(SCHEMA), call("Me", "wf_main", "latest")], [C("Start", "Me")])
    refused(extract(selfcall, ["Me"], "Piece", lk, parent_workflow_id="wf_main"), "CS10")


def test_refusals_serialise():
    r = extract(bank(), [], "X")
    assert r.to_dict() == {"code": "CS01", "message": "Select the steps to move.", "nodes": []}


@pytest.mark.parametrize("selection,code", [
    ([], "CS01"), (["Start"], "CS02"), (["Customer lookup", "Compute fee"], "CS05"),
])
def test_refusals_never_build_a_plan(selection, code):
    assert refused(extract(bank(), selection, "X"), code)


def test_garbage_definitions_do_not_raise():
    assert isinstance(extract({}, ["A"], "X"), Refusal)
    assert isinstance(extract({"nodes": [None, {"name": "A"}], "connections": ["x"]}, ["A"], "X"), Refusal)
