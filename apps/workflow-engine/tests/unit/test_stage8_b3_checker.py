"""Stage 8 B3: the checker's sub-workflow codes WF020-WF029 and the typed ExecuteWorkflow output (DESIGN-STAGE8-REUSE 5.4)."""

from __future__ import annotations

from src.services.workflow_checker import ChildWorkflow, StaticLookups, check_workflow
from tests.unit._stage8_b3_bank import (
    PROJECT, C, N, call, eligibility_child, lookups_with, notify_child, refund_parent, setf, start, trigger, wf,
)


def run(defn, lk, **kw):
    return check_workflow(defn, lookups=lk, **kw)


def of(res, code):
    return [f for f in res["findings"] if f["code"] == code]


def lk_elig(**kw):
    return lookups_with({"wf_elig": eligibility_child()}, **kw)


# ── the happy path and typing ──


def test_clean_parent_has_no_subworkflow_findings():
    res = run(refund_parent(), lk_elig())
    assert [f for f in res["findings"] if f["code"] in {f"WF02{i}" for i in range(10)}] == []
    assert [f for f in res["findings"] if f["severity"] == "error"] == []


def test_downstream_fields_are_typed_from_the_child():
    d = refund_parent()
    d["nodes"][2] = setf("Decide", ok="{{ $json.category }}", bad="{{ $json.not_a_field }}")
    res = run(d, lk_elig())
    wf002 = of(res, "WF002")
    assert len(wf002) == 1 and "not_a_field" in wf002[0]["message"] and wf002[0]["node"] == "Decide"
    assert res["shapes_by_node"]["Eligibility"]["outputs"]["main"]["properties"]["limit"]["type"] == "string"


def test_shapes_by_node_inputs_stay_the_upstream_item():
    res = run(refund_parent(), lk_elig())
    assert "customer_id" in res["shapes_by_node"]["Eligibility"]["inputs"]["main"]["properties"]  # not the child's contract


def test_error_output_port_has_child_details():
    d = refund_parent()
    d["nodes"][1]["on_error"] = "error_output"
    d["nodes"].append(setf("Handle", why="{{ $json.error.message }}", c="{{ $json.code }}", ce="{{ $json.child_execution_id }}"))
    d["connections"].append(C("Eligibility", "Handle", "error"))
    res = run(d, lk_elig())
    assert of(res, "WF002") == []


def test_legacy_node_stays_untyped_and_gets_wf023_info():
    d = wf([start(), N("Legacy", "ExecuteWorkflow", {"workflowId": "wf_elig"}), setf("Use", a="{{ $json.anything }}")], [C("Start", "Legacy"), C("Legacy", "Use")])
    res = run(d, lk_elig())
    assert of(res, "WF002") == []
    f = of(res, "WF023")
    assert len(f) == 1 and f[0]["severity"] == "info" and "predates version pinning" in f[0]["message"]


def test_approved_instance_contract_beats_derived():
    d = refund_parent()
    d["nodes"][1]["contract"] = {"source": "declared", "outputs": {"main": {"type": "object", "properties": {"only": {"type": "string"}}}}}
    d["nodes"][2] = setf("Decide", a="{{ $json.only }}", b="{{ $json.category }}")
    res = run(d, lk_elig())
    assert [f["path"] for f in of(res, "WF002")] == ["category"]


def test_disabled_call_is_a_passthrough_and_unchecked():
    d = refund_parent()
    d["nodes"][1]["disabled"] = True
    d["nodes"][1]["parameters"]["workflowId"] = "wf_nope"
    res = run(d, lk_elig())
    assert [f for f in res["findings"] if f["code"].startswith("WF02")] == []


# ── WF020 cycles ──


def test_wf020_self_call():
    d = wf([trigger("In"), call("Me", "wf_me", "draft")], [C("In", "Me")])
    lk = lookups_with({"wf_me": d}, name="wf_me")
    f = of(run(d, lk), "WF020")
    assert len(f) == 1 and f[0]["severity"] == "error" and f[0]["node"] == "Me"
    assert "Child wf_me → Child wf_me" in f[0]["message"] and "can't call itself" in f[0]["message"]


def test_wf020_indirect_cycle_names_the_path():
    a = wf([trigger("In"), call("To B", "wf_b", "latest")], [C("In", "To B")])
    b = wf([trigger("In"), call("To C", "wf_c", "latest")], [C("In", "To C")])
    c = wf([trigger("In"), call("To A", "wf_a", "draft")], [C("In", "To A")])
    lk = lookups_with({"wf_a": a, "wf_b": b, "wf_c": c}, name="wf_a")
    f = of(run(a, lk), "WF020")
    assert [x["node"] for x in f] == ["To B"]
    assert "Child wf_a → Child wf_b → Child wf_c → Child wf_a" in f[0]["message"]


def test_wf020_uses_the_unsaved_definition_not_the_stored_one():
    stored_a = wf([trigger("In")], [])  # saved A calls nothing
    b = wf([trigger("In"), call("To A", "wf_a", "draft")], [C("In", "To A")])
    lk = lookups_with({"wf_a": stored_a, "wf_b": b}, name="wf_a")
    assert of(run(stored_a, lk), "WF020") == []
    edited = wf([trigger("In"), call("To B", "wf_b", "draft")], [C("In", "To B")])
    assert len(of(run(edited, lk), "WF020")) == 1


def test_wf020_published_edges_count_too():
    a = wf([trigger("In"), call("To B", "wf_b", "latest")], [C("In", "To B")])
    b_draft = wf([trigger("In")], [])
    b_pub = wf([trigger("In"), call("To A", "wf_a", "draft")], [C("In", "To A")])
    lk = StaticLookups(children={"wf_a": ChildWorkflow("wf_a", "A", PROJECT, draft=a, versions={1: a}, published_version=1),
                                 "wf_b": ChildWorkflow("wf_b", "B", PROJECT, draft=b_draft, versions={1: b_pub}, published_version=1)},
                       current_workflow_id="wf_a", current_project_id=PROJECT)
    assert len(of(run(a, lk), "WF020")) == 1


def test_wf020_diamond_is_not_a_cycle():
    top = wf([trigger("In"), call("L", "wf_l", "latest"), call("R", "wf_r", "latest")], [C("In", "L"), C("In", "R")])
    leaf_calls = lambda: wf([trigger("In"), call("Down", "wf_leaf", "latest")], [C("In", "Down")])  # noqa: E731
    lk = lookups_with({"wf_top": top, "wf_l": leaf_calls(), "wf_r": leaf_calls(), "wf_leaf": notify_child()}, name="wf_top")
    assert of(run(top, lk), "WF020") == []


def test_wf020_needs_a_workflow_id_and_flags_both_call_nodes_on_the_cycle():
    d = wf([trigger("In"), call("Me", "wf_x", "draft")], [C("In", "Me")])
    assert of(run(d, lookups_with({"wf_x": d}, name="")), "WF020") == []  # unknown id: can't place the workflow in the graph
    assert len(of(run(d, lookups_with({"wf_x": d}, name=""), workflow_id="wf_x"), "WF020")) == 1  # explicit id works


def test_wf020_ignores_disabled_calls():
    d = wf([trigger("In"), {**call("Me", "wf_me", "draft"), "disabled": True}], [C("In", "Me")])
    assert of(run(d, lookups_with({"wf_me": d}, name="wf_me")), "WF020") == []


def test_wf020_workflow_tool_edges_count():
    a = wf([trigger("In"), N("Tool", "WorkflowTool", {"workflowId": "wf_b"})], [])
    b = wf([trigger("In"), call("Back", "wf_a", "latest")], [C("In", "Back")])
    lk = lookups_with({"wf_a": a, "wf_b": b}, name="wf_a")
    f = of(run(a, lk), "WF020")
    assert [x["node"] for x in f] == ["Tool"]


# ── WF021 / WF022 / WF023 ──


def test_wf021_child_without_trigger():
    child = wf([setf("Only", a="1")], [])
    f = of(run(refund_parent(), lookups_with({"wf_elig": child})), "WF021")
    assert len(f) == 1 and f[0]["severity"] == "error" and "can't be called" in f[0]["message"] and f[0]["node"] == "Eligibility"


def test_wf022_latest_never_published_warns_in_draft_and_errors_at_publish():
    child = eligibility_child()
    lk = StaticLookups(children={"wf_elig": ChildWorkflow("wf_elig", "Refund eligibility", PROJECT, draft=child)}, current_project_id=PROJECT)
    f = of(run(refund_parent(), lk), "WF022")
    assert len(f) == 1 and f[0]["severity"] == "warning" and "never been published" in f[0]["message"]
    assert of(run(refund_parent(), lk, for_publish=True), "WF022")[0]["severity"] == "error"


def test_wf022_missing_pinned_version_is_always_an_error():
    f = of(run(refund_parent(version=7), lk_elig()), "WF022")
    assert len(f) == 1 and f[0]["severity"] == "error" and "no version 7" in f[0]["message"]
    assert of(run(refund_parent(version=1), lk_elig()), "WF022") == []


def test_version_string_number_is_not_a_wf008():
    res = run(refund_parent(version="1"), lk_elig())
    assert of(res, "WF008") == [] and of(res, "WF022") == []


def test_invalid_version_is_reported():
    f = of(run(refund_parent(version="soon"), lk_elig()), "WF022")
    assert any("Invalid sub-workflow version" in x["message"] for x in f)


def test_wf023_draft_pin_is_info_then_error_at_publish():
    f = of(run(refund_parent(version="draft"), lk_elig()), "WF023")
    assert len(f) == 1 and f[0]["severity"] == "info" and "unsaved edits" in f[0]["message"]
    assert of(run(refund_parent(version="draft"), lk_elig(), for_publish=True), "WF023")[0]["severity"] == "error"


def test_draft_pin_still_reads_the_drafts_contract():
    child_draft = wf([trigger("Inputs", ("order_id", "string", True), ("amount", "number", True)), setf("New", brand_new="1")], [C("Inputs", "New")])
    lk = StaticLookups(children={"wf_elig": ChildWorkflow("wf_elig", "E", PROJECT, draft=child_draft, versions={1: eligibility_child()}, published_version=1)},
                       current_project_id=PROJECT)
    d = refund_parent(version="draft")
    d["nodes"][2] = setf("Decide", a="{{ $json.brand_new }}")
    assert of(run(d, lk), "WF002") == []
    d["nodes"][1]["parameters"]["version"] = "latest"
    assert len(of(run(d, lk), "WF002")) == 1  # the published version doesn't have it


# ── WF024 ──


def test_wf024_required_input_unmapped_in_map_mode_is_an_error():
    d = refund_parent(inputs={"order_id": "{{ $json.order_id }}"})
    f = of(run(d, lk_elig()), "WF024")
    assert [(x["severity"], x["path"]) for x in f] == [("error", "amount")]
    assert "Required input 'amount' of 'Child wf_elig' isn't mapped" in f[0]["message"]


def test_wf024_include_item_covers_missing_required_when_the_item_has_it():
    d = refund_parent(inputs={}, includeItem=True)
    assert of(run(d, lk_elig()), "WF024") == []


def test_wf024_include_item_but_item_lacks_field_is_a_warning():
    d = refund_parent(inputs={"order_id": "{{ $json.order_id }}"}, includeItem=True)
    d["nodes"][0] = start({"type": "object", "properties": {"order_id": {"type": "string"}}})
    f = of(run(d, lk_elig()), "WF024")
    assert [(x["severity"], x["path"]) for x in f] == [("warning", "amount")]


def test_wf024_type_mismatch_is_a_warning():
    d = refund_parent(inputs={"order_id": "{{ $json.amount }}", "amount": "{{ $json.order_id }}"})
    f = of(run(d, lk_elig()), "WF024")
    assert {(x["path"], x["severity"]) for x in f} == {("order_id", "warning"), ("amount", "warning")}
    assert any("expects a string but gets a number" in x["message"] for x in f)


def test_wf024_integer_into_number_is_fine_and_literal_string_into_number_warns():
    d = refund_parent(inputs={"order_id": "{{ $json.order_id }}", "amount": "12"})
    f = of(run(d, lk_elig()), "WF024")
    assert len(f) == 1 and "expects a number but gets a string" in f[0]["message"]


def test_wf024_undeclared_mapped_field_warns():
    d = refund_parent(inputs={"order_id": "{{ $json.order_id }}", "amount": "{{ $json.amount }}", "extra": "{{ $json.order_id }}"})
    f = of(run(d, lk_elig()), "WF024")
    assert [(x["severity"], x["path"]) for x in f] == [("warning", "extra")]


def test_wf024_expression_of_unknown_shape_is_not_judged():
    d = refund_parent(inputs={"order_id": "{{ $json.order_id.upper() }}", "amount": "{{ $json.amount + 1 }}"})
    assert of(run(d, lk_elig()), "WF024") == []


def test_wf024_passthrough_mode_checks_the_incoming_item():
    d = refund_parent(inputMode="passThrough")
    d["nodes"][0] = start({"type": "object", "properties": {"order_id": {"type": "string"}, "amount": {"type": "string"}}})
    f = of(run(d, lk_elig()), "WF024")
    assert [(x["severity"], x["path"]) for x in f] == [("warning", "amount")]
    d["nodes"][0] = start({"type": "object", "properties": {"order_id": {"type": "string"}}})
    assert [(x["severity"], x["path"]) for x in of(run(d, lk_elig()), "WF024")] == [("warning", "amount")]  # missing required


def test_wf024_custom_mode_checks_keys_and_types():
    d = refund_parent(inputMode="custom", customInput={"order_id": "o-1", "amount": "many"})
    f = of(run(d, lk_elig()), "WF024")
    assert [x["path"] for x in f] == ["amount"]
    d = refund_parent(inputMode="custom", customInput='{"order_id": "o-1"}')
    f = of(run(d, lk_elig()), "WF024")
    assert [(x["severity"], x["path"]) for x in f] == [("warning", "amount")]


def test_wf024_inputs_that_are_not_json_are_called_out():
    d = refund_parent(inputs="{not json")
    assert len(of(run(d, lk_elig()), "WF024")) == 1


def test_wf024_legacy_node_never_errors():
    d = wf([start(), N("Legacy", "ExecuteWorkflow", {"workflowId": "wf_elig"})], [C("Start", "Legacy")])
    assert [f for f in of(run(d, lk_elig()), "WF024") if f["severity"] == "error"] == []


# ── WF025 ──


def test_wf025_approval_in_child():
    child = wf([trigger("Inputs", ("order_id", "string", True)), N("Manager approval", "Approval", {})], [C("Inputs", "Manager approval")])
    f = of(run(refund_parent(inputs={"order_id": "{{ $json.order_id }}"}), lookups_with({"wf_elig": child})), "WF025")
    assert len(f) == 1 and f[0]["severity"] == "error" and f[0]["node"] == "Eligibility"
    assert "approvals must be in the top-level workflow" in f[0]["message"]


def test_wf025_approval_two_levels_down_names_where():
    mid = wf([trigger("Inputs", ("order_id", "string", True)), call("Deeper", "wf_deep", "latest", inputMode="passThrough")], [C("Inputs", "Deeper")])
    deep = wf([trigger("Inputs", ("order_id", "string", True)), N("Sign off", "Approval", {})], [C("Inputs", "Sign off")])
    lk = lookups_with({"wf_elig": mid, "wf_deep": deep})
    f = of(run(refund_parent(inputs={"order_id": "{{ $json.order_id }}"}), lk), "WF025")
    assert len(f) == 1 and "Child wf_deep" in f[0]["message"]


def test_wf025_approval_in_the_parent_itself_is_fine():
    d = refund_parent()
    d["nodes"].append(N("Manager OK", "Approval", {}))
    d["connections"].append(C("Decide", "Manager OK"))
    assert of(run(d, lk_elig()), "WF025") == []


# ── WF026 / WF027 / WF028 / WF029 ──


def test_wf026_child_output_cannot_be_typed():
    child = wf([trigger("Inputs", ("order_id", "string", True), ("amount", "number", True)),
                N("Call core", "HttpRequest", {"url": "https://core.bank.example/x", "method": "GET"})], [C("Inputs", "Call core")])
    f = of(run(refund_parent(), lookups_with({"wf_elig": child})), "WF026")
    assert len(f) == 1 and f[0]["severity"] == "warning" and f[0]["node"] == "Eligibility" and "can't be type-checked" in f[0]["message"]


def test_wf026_not_raised_for_legacy_merged_nodes():
    child = wf([trigger("Inputs", ("order_id", "string", True)), N("Call core", "HttpRequest", {"url": "https://core.bank.example/x", "method": "GET"})],
               [C("Inputs", "Call core")])
    d = wf([start(), N("Legacy", "ExecuteWorkflow", {"workflowId": "wf_elig"})], [C("Start", "Legacy")])
    assert of(run(d, lookups_with({"wf_elig": child})), "WF026") == []


def test_wf026_info_for_respond_in_a_callable_workflow():
    d = wf([trigger("Inputs", ("x", "string", True)), N("Answer", "Respond", {"statusCode": 200})], [C("Inputs", "Answer")])
    f = of(run(d, StaticLookups()), "WF026")
    assert len(f) == 1 and f[0]["severity"] == "info" and f[0]["node"] == "Answer" and "ignored" in f[0]["message"]
    plain = wf([N("Hook", "Webhook", {}), N("Answer", "Respond", {})], [C("Hook", "Answer")])
    assert of(run(plain, StaticLookups()), "WF026") == []


def test_wf027_cross_project_call():
    lk = lookups_with({"wf_elig": eligibility_child()}, project="proj_other")
    lk.current_project_id = PROJECT
    f = of(run(refund_parent(), lk), "WF027")
    assert len(f) == 1 and f[0]["severity"] == "error" and "another project" in f[0]["message"]
    assert of(run(refund_parent(), lk), "WF024") == [] and of(run(refund_parent(), lk), "WF002") == []  # nothing else is said about it


def test_wf028_chain_deeper_than_the_limit():
    c3 = notify_child()
    c2 = wf([trigger("Inputs", ("customer_id", "string", True)), call("Down", "c3", "latest", inputMode="passThrough")], [C("Inputs", "Down")])
    c1 = wf([trigger("Inputs", ("order_id", "string", True)), call("Down", "c2", "latest", inputMode="passThrough")], [C("Inputs", "Down")])
    top = wf([start(), call("Go", "c1", "latest", inputMode="passThrough")], [C("Start", "Go")])
    lk = lookups_with({"c1": c1, "c2": c2, "c3": c3}, name="top", max_depth=3)
    assert of(run(top, lk), "WF028") == []  # a chain of exactly 3 is allowed
    lk.max_depth = 2
    f = of(run(top, lk), "WF028")
    assert len(f) == 1 and f[0]["severity"] == "warning" and "3 nested" in f[0]["message"] and "2 levels" in f[0]["message"]


def test_wf029_passthrough_child():
    child = wf([trigger("Inputs", source="passthrough"), setf("Any", a="1")], [C("Inputs", "Any")])
    f = of(run(refund_parent(), lookups_with({"wf_elig": child})), "WF029")
    assert len(f) == 1 and f[0]["severity"] == "info" and of(run(refund_parent(), lookups_with({"wf_elig": child})), "WF024") == []


def test_wf029_legacy_trigger_without_declaration():
    child = wf([N("Inputs", "ExecuteWorkflowTrigger", {}), setf("Any", a="1")], [C("Inputs", "Any")])
    assert len(of(run(refund_parent(), lookups_with({"wf_elig": child})), "WF029")) == 1


# ── WF006 on workflowId ──


def test_wf006_expression_workflow_id_is_an_error_for_new_nodes_and_a_warning_for_legacy():
    d = wf([start(), call("Dyn", "{{ $json.target }}", "latest")], [C("Start", "Dyn")])
    f = of(run(d, lk_elig()), "WF006")
    assert len(f) == 1 and f[0]["severity"] == "error" and "chosen, not computed" in f[0]["message"]
    legacy = wf([start(), N("Dyn", "ExecuteWorkflow", {"workflowId": "{{ $json.target }}"})], [C("Start", "Dyn")])
    assert of(run(legacy, lk_elig()), "WF006")[0]["severity"] == "warning"


def test_wf006_missing_child_unchanged_and_silences_the_rest():
    d = refund_parent()
    d["nodes"][1]["parameters"]["workflowId"] = "wf_ghost"
    res = run(d, lk_elig())
    assert len(of(res, "WF006")) == 1 and [f for f in res["findings"] if f["code"] in ("WF021", "WF022", "WF023", "WF024", "WF025")] == []


# ── compatibility ──


def test_lookups_without_the_new_methods_still_work():
    class Old:
        egress_allowlist: list = []
        def vars_exist(self, n): return True
        def secret_exists(self, n): return True
        def table_exists(self, n): return True
        def table_columns(self, n): return None
        def table_column_defs(self, n): return None
        def workflow_exists(self, n): return True
        def credential_exists(self, n): return True
        def api_operation(self, s, o): return None

    res = check_workflow(refund_parent(), lookups=Old())
    assert [f for f in res["findings"] if f["code"] in {f"WF02{i}" for i in range(10)}] == []


def test_check_never_raises_when_a_lookup_blows_up():
    class Boom(StaticLookups):
        def workflow_io(self, *a): raise RuntimeError("db down")
        def workflow_meta(self, *a): raise RuntimeError("db down")
        def call_graph(self): raise RuntimeError("db down")

    lk = Boom(workflows={"wf_elig"}, current_workflow_id="x")
    res = check_workflow(refund_parent(), lookups=lk)
    assert of(res, "WF020") == []


def test_publish_gate_is_a_pure_severity_switch():
    d = refund_parent(version="draft")
    sev = lambda r: {(f["code"], f["severity"]) for f in r["findings"] if f["code"].startswith("WF02")}  # noqa: E731
    assert sev(run(d, lk_elig())) == {("WF023", "info")}
    assert sev(run(d, lk_elig(), for_publish=True)) == {("WF023", "error")}
