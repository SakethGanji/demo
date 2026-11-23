"""Stage 8 B9: n8n Execute Workflow / Execute Workflow Trigger import (DESIGN-STAGE8-REUSE 2.5).

An n8n child id can never be resolved here, so every imported call is *unbound* (``workflowId: ""``) plus a note;
the checker then reports it and the owner picks the child."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from src.engine.node_registry import register_all_nodes
from src.schemas.workflow import WorkflowCreateRequest
from src.services.n8n_import import import_n8n
from src.services.workflow_checker import ChildWorkflow, StaticLookups, check_workflow

register_all_nodes()
FIX = Path(__file__).resolve().parents[1] / "fixtures" / "n8n"


def fixture(name: str) -> dict:
    return json.loads((FIX / f"{name}.json").read_text())


def node(name, type_, params=None, version=1, **extra):
    return {"name": name, "type": f"n8n-nodes-base.{type_}", "typeVersion": version, "position": [0, 0],
            "parameters": params or {}, **extra}


def imp(nodes):
    w, r = import_n8n({"name": "t", "nodes": nodes, "connections": {}})
    return {n["name"]: n for n in w["nodes"]}, r


def params_of(nodes, name):
    by, _ = imp(nodes)
    return by[name]["parameters"]


def notes_for(report, name):
    return [n["message"] for n in report["notes"] if n["node"] == name]


def by_name(w, name):
    return next(n for n in w["nodes"] if n["name"] == name)


# ---- ExecuteWorkflow -------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("wid", [
    "42",
    {"__rl": True, "value": "AbC123xyz", "mode": "id"},
    {"__rl": True, "value": "Xk3pQ9aLmN2sT7vB", "mode": "list", "cachedResultName": "KYC"},
    {"__rl": True, "value": "https://n8n.example.test/workflow/9", "mode": "url"},
])
def test_database_child_is_never_bound(wid):
    by, r = imp([node("E", "executeWorkflow", {"workflowId": wid}, 1.2)])
    p = by["E"]["parameters"]
    assert p["workflowId"] == "" and p["version"] == "latest" and p["inputMode"] == "passThrough"
    assert not by["E"]["disabled"] and r["unmapped_nodes"] == []
    msgs = notes_for(r, "E")
    assert len(msgs) == 1 and "without a child" in msgs[0]


def test_note_names_the_n8n_target_so_the_owner_can_find_it():
    _, r = imp([node("E", "executeWorkflow", {"workflowId": {"__rl": True, "value": "Xk3", "mode": "list", "cachedResultName": "KYC check"}}, 1.2)])
    assert "Xk3" in notes_for(r, "E")[0] and "KYC check" in notes_for(r, "E")[0]


@pytest.mark.parametrize("source,extra,phrase", [
    ("parameter", {"workflowJson": "{\"nodes\": []}"}, "inline workflow JSON"),
    ("url", {"workflowUrl": "https://x.test/w.json"}, "URL"),
    ("localFile", {"workflowPath": "/data/w.json"}, "local file"),
])
def test_non_database_sources_are_valid_unbound_nodes_not_placeholders(source, extra, phrase):
    by, r = imp([node("E", "executeWorkflow", {"source": source, **extra}, 1)])
    assert by["E"]["type"] == "ExecuteWorkflow" and by["E"]["parameters"]["workflowId"] == ""
    assert r["unmapped_nodes"] == [] and phrase in notes_for(r, "E")[0]
    assert "workflowJson" not in json.dumps(by["E"]["parameters"])  # the inline JSON is not smuggled into the node


def test_unknown_source_is_unbound_with_a_warning():
    by, r = imp([node("E", "executeWorkflow", {"source": "somethingNew"}, 1)])
    assert by["E"]["parameters"]["workflowId"] == "" and "somethingNew" in notes_for(r, "E")[0]


def test_expression_child_id_is_not_carried_over():
    by, r = imp([node("E", "executeWorkflow", {"workflowId": {"__rl": True, "value": "={{ $json.child }}", "mode": "id"}}, 1.2)])
    assert by["E"]["parameters"]["workflowId"] == ""
    assert "computed" in notes_for(r, "E")[0]


def test_define_below_maps_to_map_inputs_with_translated_expressions():
    p = params_of([node("E", "executeWorkflow", {
        "workflowId": "1",
        "workflowInputs": {"mappingMode": "defineBelow", "value": {
            "a": "={{ $json.x }}", "b": "literal", "c": "={{ $json.p }}-{{ $json.q }}", "skipped": None, "n": 3}}}, 1.2)], "E")
    assert p["inputMode"] == "map"
    assert p["inputs"] == {"a": "{{ $json.x }}", "b": "literal", "c": "{{ $json.p }}-{{ $json.q }}", "n": 3}
    assert "includeItem" not in p  # n8n's defineBelow sends only the mapped fields


def test_define_below_with_no_values_is_an_empty_map():
    p = params_of([node("E", "executeWorkflow", {"workflowId": "1", "workflowInputs": {"mappingMode": "defineBelow", "value": {}}}, 1.2)], "E")
    assert p["inputMode"] == "map" and p["inputs"] == {}


def test_auto_map_and_missing_inputs_pass_everything_through():
    for wi in ({"mappingMode": "autoMapInputData", "value": {}}, None):
        params = {"workflowId": "1"} | ({"workflowInputs": wi} if wi else {})
        p = params_of([node("E", "executeWorkflow", params, 1.2)], "E")
        assert p["inputMode"] == "passThrough" and "inputs" not in p


def test_mapping_mode_other_than_known_is_warned_and_passed_through():
    by, r = imp([node("E", "executeWorkflow", {"workflowInputs": {"mappingMode": "weird"}}, 1.2)])
    assert by["E"]["parameters"]["inputMode"] == "passThrough"
    assert any("weird" in m for m in notes_for(r, "E"))


def test_untranslatable_input_expression_is_reported_like_every_other_node():
    _, r = imp([node("E", "executeWorkflow", {"workflowInputs": {"mappingMode": "defineBelow", "value": {"a": "={{ $node['X'].foo( }}"}}}, 1.2)])
    assert isinstance(r["untranslated_expressions"], list)  # never a crash; kept in the standard report channel


def test_type_conversion_options_are_warned_only_when_set():
    base = {"mappingMode": "defineBelow", "value": {"a": "1"}}
    _, quiet = imp([node("E", "executeWorkflow", {"workflowInputs": base | {"attemptToConvertTypes": False}}, 1.2)])
    assert not any("convert" in m for m in notes_for(quiet, "E"))
    _, loud = imp([node("E", "executeWorkflow", {"workflowInputs": base | {"attemptToConvertTypes": True, "convertFieldsToString": True}}, 1.2)])
    assert sum("convert" in m for m in notes_for(loud, "E")) == 2


@pytest.mark.parametrize("mode,expected", [("each", "each"), ("once", None), (None, None)])
def test_mode_each_maps_and_once_is_the_default(mode, expected):
    params = {"workflowId": "1"} | ({"mode": mode} if mode else {})
    p = params_of([node("E", "executeWorkflow", params, 1.2)], "E")
    assert p.get("mode") == expected


def test_unknown_mode_runs_once_with_a_warning():
    by, r = imp([node("E", "executeWorkflow", {"mode": "batch"}, 1.2)])
    assert "mode" not in by["E"]["parameters"] and any("batch" in m for m in notes_for(r, "E"))


def test_wait_for_sub_workflow_false_is_a_warning_and_true_is_silent():
    _, off = imp([node("E", "executeWorkflow", {"options": {"waitForSubWorkflow": False}}, 1.2)])
    assert any("fire and forget" in m for m in notes_for(off, "E"))
    _, on = imp([node("E", "executeWorkflow", {"options": {"waitForSubWorkflow": True}}, 1.2)])
    assert not any("fire and forget" in m for m in notes_for(on, "E"))


def test_other_options_are_warned():
    _, r = imp([node("E", "executeWorkflow", {"options": {"somethingElse": 1}}, 1.2)])
    assert any("somethingElse" in m for m in notes_for(r, "E"))


def test_garbage_parameters_never_crash():
    for params in ({"workflowId": None}, {"workflowId": {}}, {"workflowInputs": "x"}, {"workflowInputs": {"value": "x"}},
                   {"options": "x"}, {"source": None}, {"workflowId": 7}):
        by, _ = imp([node("E", "executeWorkflow", params, 1.2)])
        assert by["E"]["type"] == "ExecuteWorkflow" and by["E"]["parameters"]["workflowId"] == ""


def test_imported_call_passes_the_request_schema_and_normalizes_as_a_new_node():
    from src.nodes.flow.execute_workflow import normalize_params

    w, _ = import_n8n(fixture("subworkflow_parent_kyc_check"))
    WorkflowCreateRequest(**w)
    p = normalize_params(by_name(w, "Run KYC Check")["parameters"])
    assert not p["legacy"] and p["version"] == "latest" and p["inputMode"] == "map" and p["mode"] == "each"
    assert p["outputMode"] == "items"


# ---- ExecuteWorkflowTrigger ------------------------------------------------------------------------------------------


def test_workflow_inputs_become_fields():
    p = params_of([node("T", "executeWorkflowTrigger", {"inputSource": "workflowInputs", "workflowInputs": {"values": [
        {"name": "a", "type": "string"}, {"name": "b", "type": "number"}, {"name": "c", "type": "boolean"},
        {"name": "d", "type": "array"}, {"name": "e", "type": "object"}, {"name": "f"}]}}, 1.1)], "T")
    assert p["inputSource"] == "fields"
    assert [(f["name"], f["type"], f["required"]) for f in p["fields"]] == [
        ("a", "string", False), ("b", "number", False), ("c", "boolean", False),
        ("d", "array", False), ("e", "object", False), ("f", "string", False)]


def test_any_typed_and_duplicate_inputs_are_warned_not_declared():
    by, r = imp([node("T", "executeWorkflowTrigger", {"workflowInputs": {"values": [
        {"name": "a", "type": "any"}, {"name": "b", "type": "string"}, {"name": "b", "type": "number"}, {"name": " ", "type": "string"}]}}, 1.1)])
    assert [f["name"] for f in by["T"]["parameters"]["fields"]] == ["b"]
    assert by["T"]["parameters"]["fields"][0]["type"] == "string"
    msgs = " | ".join(notes_for(r, "T"))
    assert "'a'" in msgs and "'any'" in msgs and "declared twice" in msgs


def test_no_declared_inputs_means_passthrough():
    p = params_of([node("T", "executeWorkflowTrigger", {"inputSource": "workflowInputs", "workflowInputs": {"values": []}}, 1.1)], "T")
    assert p == {"inputSource": "passthrough"}


def test_passthrough_and_typeversion_one_accept_everything():
    assert params_of([node("T", "executeWorkflowTrigger", {"inputSource": "passthrough"}, 1.1)], "T") == {"inputSource": "passthrough"}
    assert params_of([node("T", "executeWorkflowTrigger", {}, 1)], "T") == {"inputSource": "passthrough"}


def test_typeversion_1_1_without_a_source_defaults_to_declared_fields():
    p = params_of([node("T", "executeWorkflowTrigger", {"workflowInputs": {"values": [{"name": "a", "type": "number"}]}}, 1.1)], "T")
    assert p["inputSource"] == "fields" and p["fields"][0]["type"] == "number"


def test_json_example_infers_types_and_keeps_the_example_as_default_input():
    example = {"id": "p1", "amount": 1.5, "count": 2, "ok": True, "xs": [1], "o": {"k": 1}, "nothing": None}
    by, r = imp([node("T", "executeWorkflowTrigger", {"inputSource": "jsonExample", "jsonExample": json.dumps(example, indent=2)}, 1.1)])
    p = by["T"]["parameters"]
    assert {f["name"]: f["type"] for f in p["fields"]} == {"id": "string", "amount": "number", "count": "number", "ok": "boolean", "xs": "array", "o": "object"}
    assert json.loads(p["defaultInput"]) == example
    assert any("'nothing'" in m for m in notes_for(r, "T"))


@pytest.mark.parametrize("raw", ["not json", "[1, 2]", "42", None, ""])
def test_unusable_json_example_degrades_to_passthrough_with_a_warning(raw):
    by, r = imp([node("T", "executeWorkflowTrigger", {"inputSource": "jsonExample", "jsonExample": raw}, 1.1)])
    assert by["T"]["parameters"] == {"inputSource": "passthrough"}
    assert any("JSON example" in m for m in notes_for(r, "T"))


def test_unknown_input_source_accepts_everything_with_a_warning():
    by, r = imp([node("T", "executeWorkflowTrigger", {"inputSource": "mystery"}, 1.1)])
    assert by["T"]["parameters"] == {"inputSource": "passthrough"} and "mystery" in notes_for(r, "T")[0]


def test_trigger_variants_fixture_imports_cleanly():
    w, r = import_n8n(fixture("subworkflow_child_triggers"))
    WorkflowCreateRequest(**w)
    assert r["unmapped_nodes"] == []
    assert [n["type"] for n in w["nodes"]] == ["ExecuteWorkflowTrigger"] * 3


# ---- import -> checker -----------------------------------------------------------------------------------------------


def _codes(result):
    return [f["code"] for f in result["findings"]]


def test_unbound_child_gets_the_required_workflow_finding_and_never_a_false_binding():
    w, _ = import_n8n(fixture("subworkflow_parent_kyc_check"))
    res = check_workflow(w, lookups=StaticLookups())
    mine = [f for f in res["findings"] if f.get("node") == "Run KYC Check" and f["severity"] == "error"]
    assert [f["code"] for f in mine] == ["WF008"] and mine[0]["param"] == "workflowId"
    assert not [c for c in _codes(res) if c in ("WF006", "WF020", "WF021", "WF022", "WF023", "WF027")]


def test_every_unbound_variant_is_flagged_by_the_checker():
    w, r = import_n8n(fixture("subworkflow_variants"))
    res = check_workflow(w, lookups=StaticLookups())
    flagged = {f["node"] for f in res["findings"] if f["code"] == "WF008" and f["param"] == "workflowId"}
    calls = {n["name"] for n in w["nodes"] if n["type"] == "ExecuteWorkflow"}
    assert flagged == calls and len(calls) == 6
    assert {n["node"] for n in r["notes"]} >= calls


def _bind(w, wid):
    w = copy.deepcopy(w)
    by_name(w, "Run KYC Check")["parameters"]["workflowId"] = wid
    return w


def _kyc_lookups(child, **kw):
    return StaticLookups(children={"wf_kyc": ChildWorkflow("wf_kyc", "KYC check", None, draft=child, versions={1: child},
                                                           published_version=1, **kw)}, current_workflow_id="wf_parent")


def test_binding_the_imported_child_makes_the_imported_parent_check_clean_and_typed():
    parent, _ = import_n8n(fixture("subworkflow_parent_kyc_check"))
    child, _ = import_n8n(fixture("subworkflow_child_kyc_check"))
    res = check_workflow(_bind(parent, "wf_kyc"), lookups=_kyc_lookups(child), workflow_id="wf_parent")
    assert [f for f in res["findings"] if f["severity"] == "error" or f["code"].startswith("WF02")] == []
    # the child's derived output types the parent's next node: no "can't check kyc_status"
    assert not any(f.get("param") == "kyc_status" for f in res["findings"])


def test_binding_a_child_without_a_trigger_is_wf021_and_an_unpublished_one_is_wf022():
    parent, _ = import_n8n(fixture("subworkflow_parent_kyc_check"))
    no_trigger = {"nodes": [{"name": "S", "type": "Set", "parameters": {}}], "connections": []}
    res = check_workflow(_bind(parent, "wf_kyc"), lookups=_kyc_lookups(no_trigger), workflow_id="wf_parent")
    assert "WF021" in _codes(res)

    child, _ = import_n8n(fixture("subworkflow_child_kyc_check"))
    lk = StaticLookups(children={"wf_kyc": ChildWorkflow("wf_kyc", "KYC check", None, draft=child)}, current_workflow_id="wf_parent")
    res = check_workflow(_bind(parent, "wf_kyc"), lookups=lk, workflow_id="wf_parent")
    assert "WF022" in _codes(res)


def test_a_mapping_to_an_imported_child_field_of_the_wrong_type_is_wf024():
    parent, _ = import_n8n(fixture("subworkflow_parent_kyc_check"))
    child, _ = import_n8n(fixture("subworkflow_child_kyc_check"))
    bound = _bind(parent, "wf_kyc")
    by_name(bound, "Run KYC Check")["parameters"]["inputs"]["monthly_income"] = "not a number"
    res = check_workflow(bound, lookups=_kyc_lookups(child), workflow_id="wf_parent")
    assert "WF024" in _codes(res)
