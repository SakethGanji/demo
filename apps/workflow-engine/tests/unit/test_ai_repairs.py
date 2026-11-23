"""Stage 6 / DESIGN-STAGE6-AI-BUILDER §6.6: deterministic repairs FIX01-FIX12 as golden cases. Pure: the input is never changed.

Approved tests (and the node names they mention) are never touched: that is the immutability gate (§6.4) seen from the repair side.
"""

from __future__ import annotations

import copy
import json
import time

import pytest

from src.services import ai_grounding as G
from src.services import ai_repairs as R

OPS = {"s_refunds": {"issueRefund": {"method": "POST", "path": "/refunds/{id}", "path_params": ["id"], "responses": {"201": {"shape": {"type": "object"}}}},
                     "listRefunds": {"method": "GET", "path": "/refunds", "responses": {"200": {}}}}}
API_OPS = G.api_operations(OPS, {"s_refunds": {"name": "Refunds"}})
NO_ID = "must not appear"


def n(name, type_, params=None, **extra):
    return {"name": name, "type": type_, "parameters": params if params is not None else {}, "position": {"x": 0, "y": 0}, **extra}


def c(src, dst, out="main", inp="main"):
    return {"source_node": src, "target_node": dst, "source_output": out, "target_input": inp}


def d(nodes, conns=(), tests=()):
    return {"name": "wf", "nodes": list(nodes), "connections": list(conns), "tests": list(tests)}


def fix(defn, only=None, **kw):
    kw.setdefault("api_ops", API_OPS)
    kw.setdefault("operations", OPS)
    return R.apply_repairs(defn, only=[only] if isinstance(only, str) else only, **kw)


def by_name(defn, name):
    return next(x for x in defn["nodes"] if x["name"] == name)


def approved_test(**kw):
    t = {"id": "t_a1b2c301", "name": "t", "status": "approved", "locked": True, "target": {"kind": "workflow"}, "input": {"items": [{}]},
         "mocks": {}, "expect": [], "source": "ai"}
    t.update(kw)
    return t


# ── FIX01 unknown node type ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("wrong,right", [("Httprequest", "HttpRequest"), ("httprequest", "HttpRequest"), ("HTTPRequest", "HttpRequest"), ("Http Request", "HttpRequest"),
                                         ("http_request", "HttpRequest"), ("Sendemail", "SendEmail"), ("Formtrigger", "FormTrigger"), ("Approvals", "Approval")])
def test_fix01_closest_catalog_type_case_folded(wrong, right):
    out, fixes = fix(d([n("X", wrong)]), "FIX01")
    assert by_name(out, "X")["type"] == right
    (f,) = fixes
    assert f["code"] == "FIX01" and f["node"] == "X" and wrong in f["message"] and right in f["message"]


def test_fix01_spec_step_type_is_preferred_over_the_closest_match():
    spec = {"steps": [{"name": "Pay claim", "node_type": "HttpRequest"}]}
    out, _ = fix(d([n("Pay claim", "RestCall")]), "FIX01", spec=spec)
    assert by_name(out, "Pay claim")["type"] == "HttpRequest"


def test_fix01_no_close_match_reports_without_changing():
    out, fixes = fix(d([n("X", "QuantumTeleporter")]), "FIX01")
    assert by_name(out, "X")["type"] == "QuantumTeleporter"
    assert len(fixes) == 1 and fixes[0]["code"] == "FIX01" and fixes[0]["changed"] is False


def test_fix01_cutoff_is_085_so_a_vaguely_similar_name_is_not_taken():
    out, _ = fix(d([n("X", "Sender"), n("Y", "Zap")]), "FIX01")
    assert by_name(out, "X")["type"] == "Sender" and by_name(out, "Y")["type"] == "Zap"


def test_fix01_known_types_are_untouched_and_report_nothing():
    out, fixes = fix(d([n("A", "If"), n("B", "Set")]), "FIX01")
    assert fixes == [] and [x["type"] for x in out["nodes"]] == ["If", "Set"]


def test_fix01_greek_lookalike_does_not_silently_become_a_real_type():
    out, fixes = fix(d([n("X", "Ιf")]), "FIX01")  # Greek capital iota + f
    assert by_name(out, "X")["type"] in ("Ιf", "If")  # either left alone or mapped to the intended type, never to a different one
    assert all(f["code"] == "FIX01" for f in fixes)


# ── FIX02 unknown parameter keys ────────────────────────────────────────────────────────────


def test_fix02_key_written_another_way_is_renamed():
    out, fixes = fix(d([n("Call", "HttpRequest", {"apiRef": {"spec_id": "s_refunds", "operation_id": "issueRefund"}, "Method": "POST", "urll": "https://x"})]), "FIX02")
    p = by_name(out, "Call")["parameters"]
    assert set(p) == {"api_ref", "method", "url"} and p["url"] == "https://x" and p["method"] == "POST"
    assert {f["code"] for f in fixes} == {"FIX02"} and len(fixes) == 3


def test_fix02_key_with_no_match_is_dropped_and_reported_with_the_valid_names():
    out, fixes = fix(d([n("Call", "HttpRequest", {"url": "u", "banana_split": 1})]), "FIX02")
    assert by_name(out, "Call")["parameters"] == {"url": "u"}
    (f,) = fixes
    assert "banana_split" in f["message"] and "url" in f["message"]


def test_fix02_does_not_overwrite_a_parameter_that_is_already_set():
    out, fixes = fix(d([n("Call", "HttpRequest", {"url": "keep", "urll": "other"})]), "FIX02")
    assert by_name(out, "Call")["parameters"]["url"] == "keep" and "urll" not in by_name(out, "Call")["parameters"]
    assert len(fixes) == 1


def test_fix02_contract_keys_not_in_the_catalog_are_kept():
    out, fixes = fix(d([n("Ask", "LLMChat", {"output_schema": {"type": "object"}, "_note": "x"}), n("F", "FormTrigger", {"input_schema": {"type": "object"}})]), "FIX02")
    assert by_name(out, "Ask")["parameters"] == {"output_schema": {"type": "object"}, "_note": "x"}
    assert by_name(out, "F")["parameters"] == {"input_schema": {"type": "object"}}
    assert fixes == []


# ── FIX03 enum values ───────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("given,want", [("post", "POST"), ("Post", "POST"), ("get", "GET"), (" delete ", "DELETE")])
def test_fix03_enum_value_matched_to_the_catalog_option(given, want):
    out, fixes = fix(d([n("Call", "HttpRequest", {"method": given})]), "FIX03")
    if given.strip() == given:
        assert by_name(out, "Call")["parameters"]["method"] == want and fixes[0]["code"] == "FIX03"


def test_fix03_other_option_params_are_folded_too():
    out, _ = fix(d([n("Call", "HttpRequest", {"responseType": "JSON"}), n("Ask", "Approval", {"on_timeout": "Reject"})]), "FIX03")
    assert by_name(out, "Call")["parameters"]["responseType"] == "json"
    assert by_name(out, "Ask")["parameters"]["on_timeout"] == "reject"


def test_fix03_expressions_valid_values_and_nonsense_are_left_alone():
    out, fixes = fix(d([n("A", "HttpRequest", {"method": "{{ $json.m }}"}), n("B", "HttpRequest", {"method": "POST"}), n("C", "HttpRequest", {"method": "TELEPORT"}),
                        n("D", "HttpRequest", {"method": ""}), n("E", "HttpRequest", {"method": 5})]), "FIX03")
    assert [x["parameters"]["method"] for x in out["nodes"]] == ["{{ $json.m }}", "POST", "TELEPORT", "", 5] and fixes == []


# ── FIX04 api_ref ───────────────────────────────────────────────────────────────────────────


def test_fix04_string_api_ref_becomes_the_spec_operation_form_and_method_and_path_params_are_filled():
    out, fixes = fix(d([n("Pay", "HttpRequest", {"api_ref": "refunds.issueRefund"})]), "FIX04")
    p = by_name(out, "Pay")["parameters"]
    assert p["api_ref"] == {"spec_id": "s_refunds", "operation_id": "issueRefund"}
    assert p["method"] == "POST"
    assert p["path_params"] == {"id": "{{ $json.id }}"}
    assert {f["code"] for f in fixes} == {"FIX04"} and len(fixes) == 3


def test_fix04_existing_method_and_path_params_are_kept():
    out, _ = fix(d([n("Pay", "HttpRequest", {"api_ref": {"spec_id": "s_refunds", "operation_id": "issueRefund"}, "method": "PUT", "path_params": {"id": "{{ $json.rid }}"}})]), "FIX04")
    p = by_name(out, "Pay")["parameters"]
    assert p["method"] == "PUT" and p["path_params"] == {"id": "{{ $json.rid }}"}


def test_fix04_operation_without_path_params_adds_none():
    out, _ = fix(d([n("List", "HttpRequest", {"api_ref": "listRefunds"})]), "FIX04")
    p = by_name(out, "List")["parameters"]
    assert p["method"] == "GET" and "path_params" not in p


def test_fix04_unknown_api_ref_is_reported_not_invented():
    out, fixes = fix(d([n("Pay", "HttpRequest", {"api_ref": "refunds.nope"})]), "FIX04")
    assert by_name(out, "Pay")["parameters"]["api_ref"] == "refunds.nope"
    assert len(fixes) == 1 and fixes[0]["changed"] is False


def test_fix04_nodes_without_api_ref_and_other_types_are_untouched():
    src = d([n("Raw", "HttpRequest", {"url": "https://api.bank.example/x"}), n("Other", "Set", {"api_ref": "refunds.issueRefund"})])
    out, fixes = fix(src, "FIX04")
    assert out == src and fixes == []


# ── FIX05 duplicate names ───────────────────────────────────────────────────────────────────


def test_fix05_later_duplicates_get_numeric_suffixes():
    out, fixes = fix(d([n("Step", "Set"), n("Step", "Set"), n("Step", "Set")]), "FIX05")
    assert [x["name"] for x in out["nodes"]] == ["Step", "Step 2", "Step 3"]
    assert [f["node"] for f in fixes] == ["Step 2", "Step 3"] and all(f["code"] == "FIX05" for f in fixes)


def test_fix05_skips_suffixes_that_are_taken():
    out, _ = fix(d([n("Step", "Set"), n("Step 2", "Set"), n("Step", "Set")]), "FIX05")
    assert [x["name"] for x in out["nodes"]] == ["Step", "Step 2", "Step 3"]


def test_fix05_never_renames_a_name_an_approved_test_mentions_and_never_uses_one_as_a_new_name():
    t = approved_test(mocks={"Check": {"items": [{}]}}, expect=[{"node": "Check 2", "op": "status", "value": "success"}])
    out, _ = fix(d([n("Check", "Set"), n("Check", "Set")], tests=[t]), "FIX05")
    names = [x["name"] for x in out["nodes"]]
    assert names[0] == "Check" and names[1] == "Check 3", names
    assert out["tests"] == [t]


def test_fix05_unique_names_untouched():
    src = d([n("A", "Set"), n("B", "Set")])
    out, fixes = fix(src, "FIX05")
    assert out == src and fixes == []


# ── FIX06 dangling connections ──────────────────────────────────────────────────────────────


def test_fix06_connections_to_missing_nodes_are_removed():
    out, fixes = fix(d([n("A", "Set"), n("B", "Set")], [c("A", "B"), c("A", "Ghost"), c("Phantom", "B")]), "FIX06")
    assert out["connections"] == [c("A", "B")]
    assert len(fixes) == 2 and all(f["code"] == "FIX06" for f in fixes)
    assert any("Ghost" in f["message"] for f in fixes) and any("Phantom" in f["message"] for f in fixes)


def test_fix06_exact_duplicates_are_removed_and_distinct_ports_are_kept():
    out, _ = fix(d([n("A", "If"), n("B", "Set")], [c("A", "B", "true"), c("A", "B", "true"), c("A", "B", "false")]), "FIX06")
    assert out["connections"] == [c("A", "B", "true"), c("A", "B", "false")]


def test_fix06_garbage_connection_entries_are_dropped_not_raised():
    out, _ = fix(d([n("A", "Set")], [None, 3, "x", {}]), "FIX06")
    assert out["connections"] == []


# ── FIX07 port names ────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("given,want", [("True", "true"), ("yes", "true"), ("TRUE", "true"), ("False", "false"), ("no", "false"), ("else", "false")])
def test_fix07_if_ports(given, want):
    out, fixes = fix(d([n("G", "If"), n("B", "Set")], [c("G", "B", given)]), "FIX07")
    assert out["connections"][0]["source_output"] == want and fixes[0]["code"] == "FIX07"


@pytest.mark.parametrize("given,want", [("approve", "approved"), ("Approved", "approved"), ("reject", "rejected"), ("denied", "rejected"), ("timeout", "timed_out"),
                                        ("Timed Out", "timed_out"), ("expired", "timed_out")])
def test_fix07_approval_ports(given, want):
    out, _ = fix(d([n("A", "Approval"), n("B", "Set")], [c("A", "B", given)]), "FIX07")
    assert out["connections"][0]["source_output"] == want


def test_fix07_loop_and_switch_ports():
    out, _ = fix(d([n("L", "Loop"), n("S", "Switch", {"numberOfOutputs": 3}), n("B", "Set")], [c("L", "B", "each"), c("L", "B", "finished"), c("S", "B", "output1"), c("S", "B", "default")]), "FIX07")
    assert [x["source_output"] for x in out["connections"]] == ["loop", "done", "output1", "fallback"]


def test_fix07_correct_ports_unknown_ports_and_main_are_left_alone():
    src = d([n("G", "If"), n("B", "Set"), n("T", "Webhook")], [c("G", "B", "true"), c("G", "B", "banana"), c("T", "B", "main")])
    out, fixes = fix(src, "FIX07")
    assert out["connections"] == src["connections"] and fixes == []


def test_fix07_normalising_two_connections_to_one_collapses_the_duplicate():
    out, _ = fix(d([n("G", "If"), n("B", "Set")], [c("G", "B", "true"), c("G", "B", "True"), c("G", "B", "yes")]), "FIX07")
    assert out["connections"] == [c("G", "B", "true")]


# ── FIX08 expression syntax ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("given,want", [
    ("{{$json.a}}", "{{ $json.a }}"),
    ("{{   $json.a   }}", "{{ $json.a }}"),
    ("={{ $json.a }}", "{{ $json.a }}"),
    ("={{$json.a}}", "{{ $json.a }}"),
    ("Hello {{$json.name}}, bye", "Hello {{ $json.name }}, bye"),
    ("{{ $json.a }", "{{ $json.a }}"),
    ("{{ $json.a", "{{ $json.a }}"),
    ("{{$json.a}} and {{$json.b}}", "{{ $json.a }} and {{ $json.b }}"),
])
def test_fix08_golden(given, want):
    out, fixes = fix(d([n("Call", "HttpRequest", {"url": given})]), "FIX08")
    assert by_name(out, "Call")["parameters"]["url"] == want
    assert fixes and fixes[0]["code"] == "FIX08" and fixes[0]["node"] == "Call"


def test_fix08_nested_parameters_are_normalised():
    out, _ = fix(d([n("Call", "HttpRequest", {"body": {"a": ["{{$json.x}}", {"b": "={{$json.y}}"}]}, "headers": {"X": "{{$json.z}}"}})]), "FIX08")
    p = by_name(out, "Call")["parameters"]
    assert p["body"] == {"a": ["{{ $json.x }}", {"b": "{{ $json.y }}"}]} and p["headers"] == {"X": "{{ $json.z }}"}


def test_fix08_already_normal_text_code_nodes_and_schemas_are_untouched():
    src = d([n("A", "Set", {"fields": [{"name": "a", "value": "{{ $json.a }}"}], "note": "no braces = here"}),
             n("Code1", "Code", {"code": "x = '{{$json.a}}'"}),
             n("Ask", "LLMChat", {"output_schema": {"description": "{{$json.a}}"}})])
    out, fixes = fix(src, "FIX08")
    assert out == src and fixes == []


def test_fix08_does_not_touch_text_that_only_looks_like_braces():
    src = d([n("A", "Set", {"v": "{ not an expression }", "w": "}} stray", "x": "a = {'k': 1}"})])
    out, _ = fix(src, "FIX08")
    assert out == src


# ── FIX09 $node references ──────────────────────────────────────────────────────────────────


def test_fix09_references_follow_a_rename_in_the_same_round():
    src = d([n("Issue refund", "HttpRequest"), n("Notify", "HttpRequest", {"body": '{{ $node["HttpRequest 1"].json.id }} / {{ $node[\'HttpRequest 1\'].json.ok }}'})])
    out, fixes = fix(src, "FIX09", renames={"HttpRequest 1": "Issue refund"})
    assert by_name(out, "Notify")["parameters"]["body"].count("Issue refund") == 2 and "HttpRequest 1" not in by_name(out, "Notify")["parameters"]["body"]
    assert fixes[0]["code"] == "FIX09"


def test_fix09_a_rename_is_not_applied_while_the_old_name_still_exists():
    src = d([n("Old", "Set"), n("User", "Set", {"v": '{{ $node["Old"].json.a }}'})])
    out, fixes = fix(src, "FIX09", renames={"Old": "New"})
    assert out == src and fixes == []


def test_fix09_case_and_spacing_mismatch_is_snapped_to_the_one_real_node():
    src = d([n("Manager review", "Approval"), n("User", "Set", {"v": '{{ $node["manager  Review"].json.a }}'})])
    out, _ = fix(src, "FIX09")
    assert 'Manager review' in by_name(out, "User")["parameters"]["v"]


def test_fix09_ambiguous_or_unknown_references_are_left_alone():
    src = d([n("Pay", "Set"), n("pay", "Set"), n("User", "Set", {"v": '{{ $node["PAY"].json.a }} {{ $node["Ghost"].json.a }}'})])
    out, fixes = fix(src, "FIX09")
    assert by_name(out, "User")["parameters"]["v"] == src["nodes"][2]["parameters"]["v"] and fixes == []


# ── FIX10 connect the trigger ───────────────────────────────────────────────────────────────


def test_fix10_one_trigger_and_one_orphan_root_are_connected():
    out, fixes = fix(d([n("Form", "FormTrigger"), n("Check", "Set")]), "FIX10")
    assert out["connections"] == [c("Form", "Check")]
    assert fixes[0]["code"] == "FIX10" and fixes[0]["node"] == "Check"


def test_fix10_picks_the_only_root_in_a_longer_chain():
    out, _ = fix(d([n("Form", "FormTrigger"), n("A", "Set"), n("B", "Set")], [c("A", "B")]), "FIX10")
    assert c("Form", "A") in out["connections"] and len(out["connections"]) == 2


@pytest.mark.parametrize("nodes,conns", [
    ([n("Form", "FormTrigger"), n("A", "Set"), n("B", "Set")], []),                      # two roots: ambiguous
    ([n("Form", "FormTrigger"), n("A", "Set")], [c("Form", "A")]),                       # already connected
    ([n("F1", "FormTrigger"), n("F2", "Webhook"), n("A", "Set")], []),                   # two triggers
    ([n("A", "Set"), n("B", "Set")], [c("A", "B")]),                                     # no trigger
    ([n("Form", "FormTrigger")], []),                                                    # nothing to connect
])
def test_fix10_ambiguous_cases_change_nothing(nodes, conns):
    out, fixes = fix(d(nodes, conns), "FIX10")
    assert out["connections"] == conns and fixes == []


# ── FIX11 report only ───────────────────────────────────────────────────────────────────────

SPEC11 = {"steps": [{"name": "Form", "node_type": "FormTrigger"}, {"name": "Gate", "node_type": "If"}, {"name": "Manager", "node_type": "Approval"},
                    {"name": "Pay", "node_type": "HttpRequest"}]}


def test_fix11_reports_an_unconnected_port_while_a_later_step_has_no_input_and_changes_nothing():
    src = d([n("Form", "FormTrigger"), n("Gate", "If"), n("Manager", "Approval"), n("Pay", "HttpRequest")], [c("Form", "Gate"), c("Gate", "Pay", "false")])
    out, fixes = fix(src, "FIX11", spec=SPEC11)
    assert out == src, "FIX11 never guesses a connection"
    f = [x for x in fixes if x["node"] == "Gate"]
    assert f and f[0]["code"] == "FIX11" and f[0]["changed"] is False and "true" in f[0]["message"]


def test_fix11_silent_when_every_step_has_an_input():
    src = d([n("Form", "FormTrigger"), n("Gate", "If"), n("Manager", "Approval"), n("Pay", "HttpRequest")],
            [c("Form", "Gate"), c("Gate", "Manager", "true"), c("Gate", "Pay", "false")])
    _, fixes = fix(src, "FIX11", spec=SPEC11)
    assert fixes == []


def test_fix11_needs_a_spec():
    _, fixes = fix(d([n("Form", "FormTrigger"), n("Gate", "If"), n("Pay", "HttpRequest")], [c("Form", "Gate")]), "FIX11")
    assert fixes == []


# ── FIX12 form schema ───────────────────────────────────────────────────────────────────────

SPEC12 = {"trigger": {"kind": "form", "fields": [
    {"name": "customer_email", "type": "string", "required": True, "label": "Customer email"},
    {"name": "amount", "type": "number", "required": True, "label": "Amount (USD)"},
    {"name": "reason", "type": "string", "required": False}]}}


def test_fix12_fills_input_schema_from_the_spec_trigger_fields():
    out, fixes = fix(d([n("Form", "FormTrigger")]), "FIX12", spec=SPEC12)
    p = by_name(out, "Form")["parameters"]
    s = p["input_schema"]
    assert s["type"] == "object" and set(s["properties"]) == {"customer_email", "amount", "reason"}
    assert s["properties"]["amount"]["type"] == "number" and s["properties"]["customer_email"]["type"] == "string"
    assert s["required"] == ["customer_email", "amount"]
    assert s["properties"]["customer_email"]["title"] == "Customer email"
    assert p["field_order"] == ["customer_email", "amount", "reason"]
    assert fixes[0]["code"] == "FIX12" and fixes[0]["node"] == "Form"


def test_fix12_keeps_an_input_schema_the_model_already_wrote():
    mine = {"type": "object", "properties": {"only": {"type": "string"}}, "required": []}
    out, fixes = fix(d([n("Form", "FormTrigger", {"input_schema": mine})]), "FIX12", spec=SPEC12)
    assert by_name(out, "Form")["parameters"]["input_schema"] == mine and fixes == []


@pytest.mark.parametrize("kind", ["webhook", "cron", "manual", "chat", "execute"])
def test_fix12_only_for_form_triggers(kind):
    spec = {"trigger": {"kind": kind, "fields": SPEC12["trigger"]["fields"]}}
    src = d([n("Form", "FormTrigger")])
    out, fixes = fix(src, "FIX12", spec=spec)
    assert out == src and fixes == []


def test_fix12_form_without_fields_is_untouched():
    src = d([n("Form", "FormTrigger")])
    out, fixes = fix(src, "FIX12", spec={"trigger": {"kind": "form", "fields": []}})
    assert out == src and fixes == []


# ── whole pipeline ──────────────────────────────────────────────────────────────────────────


def messy():
    return d([n("Form", "Formtrigger"), n("Gate", "If", {"condition": "{{$json.amount > 500}}"}), n("Gate", "Set"),
              n("Manager", "Approvals"), n("Pay", "Httprequest", {"api_ref": "refunds.issueRefund", "Method": "post", "banana": 1, "body": {"a": "={{$json.amount}}"}})],
             [c("Gate", "Manager", "True"), c("Gate", "Pay", "false"), c("Manager", "Pay", "approve"), c("Manager", "Nowhere")])


def test_pipeline_runs_the_repairs_in_order_and_the_result_is_coherent():
    out, fixes = fix(messy(), spec=SPEC12 | {"steps": [{"name": "Form", "node_type": "FormTrigger"}, {"name": "Gate", "node_type": "If"}, {"name": "Manager", "node_type": "Approval"},
                                                      {"name": "Pay", "node_type": "HttpRequest"}]})
    codes = [f["code"] for f in fixes]
    assert codes == sorted(codes), "FIX01..FIX12 run in order"
    assert {"FIX01", "FIX02", "FIX03", "FIX04", "FIX05", "FIX06", "FIX07", "FIX08", "FIX12"} <= set(codes)
    assert [x["type"] for x in out["nodes"]] == ["FormTrigger", "If", "Set", "Approval", "HttpRequest"]
    assert [x["name"] for x in out["nodes"]] == ["Form", "Gate", "Gate 2", "Manager", "Pay"]
    assert {(x["source_node"], x["target_node"], x["source_output"]) for x in out["connections"]} == {
        ("Gate", "Manager", "true"), ("Gate", "Pay", "false"), ("Manager", "Pay", "approved")}
    pay = by_name(out, "Pay")["parameters"]
    assert pay["method"] == "POST" and "banana" not in pay and pay["body"]["a"] == "{{ $json.amount }}"
    assert by_name(out, "Gate")["parameters"]["condition"] == "{{ $json.amount > 500 }}"


def test_pipeline_is_idempotent():
    spec = {"trigger": SPEC12["trigger"], "steps": []}
    once, _ = fix(messy(), spec=spec)
    twice, fixes = fix(once, spec=spec)
    assert twice == once
    assert [f for f in fixes if f.get("changed", True)] == []


def test_pipeline_never_changes_its_input_and_the_output_does_not_alias_it():
    src = messy()
    before = copy.deepcopy(src)
    out, _ = fix(src, spec=SPEC12)
    assert src == before
    by_name(out, "Pay")["parameters"]["zzz"] = 1
    out["connections"].clear()
    assert src == before


def test_only_limits_the_repairs_that_run():
    _, fixes = fix(messy(), ["FIX05", "FIX06"])
    assert {f["code"] for f in fixes} == {"FIX05", "FIX06"}


def test_approved_tests_are_never_modified_by_any_repair():
    t = approved_test(name="Gate test", mocks={"Pay": {"http": {"status": 201, "body": {"id": "={{$json.x}}"}}}},
                      input={"items": [{"amount": "{{$json.a}}"}]}, expect=[{"node": "Gate", "port": "True", "op": "count", "value": 1}], covers=["Pay", "Gate"])
    draft = approved_test(id="t_deadbeef", status="draft", locked=False, mocks={"Pay": {"items": [{"v": "={{$json.q}}"}]}})
    src = d(messy()["nodes"], messy()["connections"], tests=[t, draft])
    before = copy.deepcopy(src["tests"])
    out, _ = fix(src, spec=SPEC12)
    assert out["tests"] == before


def test_names_in_approved_tests_survive_every_repair_pass():
    t = approved_test(mocks={"Pay": {"items": [{}]}, "Manager": {"items": [{}], "port": "approved"}}, expect=[{"node": "Gate", "op": "status", "value": "success"}])
    out, _ = fix(d(messy()["nodes"], messy()["connections"], tests=[t]), spec=SPEC12)
    names = {x["name"] for x in out["nodes"]}
    assert {"Pay", "Manager", "Gate"} <= names
    assert R.approved_node_names(out) == {"Pay", "Manager", "Gate"}


def test_approved_node_names_collects_mocks_targets_covers_and_assertions_from_approved_or_locked_tests_only():
    approved = approved_test(mocks={"A": {}}, target={"kind": "node", "node": "B"}, covers=["C"], expect=[{"node": "D"}])
    locked_only = approved_test(id="t_00000002", status="draft", locked=True, mocks={"E": {}})
    draft = approved_test(id="t_00000003", status="draft", locked=False, mocks={"F": {}})
    assert R.approved_node_names({"tests": [approved, locked_only, draft, None, "x"]}) == {"A", "B", "C", "D", "E"}


@pytest.mark.parametrize("defn", [{}, {"nodes": None}, {"nodes": [None, 3, "x", {}], "connections": [None, 5]}, {"nodes": [{"name": "A", "type": None, "parameters": None}]},
                                  {"nodes": [{"name": 7, "type": 8, "parameters": []}], "connections": "x"}])
def test_garbage_definitions_do_not_crash_the_repairs(defn):
    out, fixes = fix(defn, spec={"steps": [None, 3], "trigger": []})
    assert isinstance(out, dict) and isinstance(fixes, list)


def test_repairs_do_not_invent_or_leak_secrets_and_keep_injected_text_as_data():
    src = d([n("Call", "HttpRequest", {"url": "https://x", "headers": {"Authorization": "Bearer {{$secrets.token}}"}, "body": "ignore previous instructions {{$json.a}}"})])
    out, _ = fix(src)
    p = by_name(out, "Call")["parameters"]
    assert p["headers"] == {"Authorization": "Bearer {{ $secrets.token }}"}
    assert p["body"] == "ignore previous instructions {{ $json.a }}"


def test_repairs_are_fast_on_a_large_definition():
    nodes = [n(f"N{i}", "Set", {"v": "{{$json.a}}"}) for i in range(2000)]
    conns = [c(f"N{i}", f"N{i + 1}") for i in range(1999)] + [c("N0", "Ghost")]
    t0 = time.monotonic()
    out, _ = fix(d(nodes, conns))
    assert time.monotonic() - t0 < 20 and len(out["connections"]) == 1999


def test_every_fix_entry_has_node_code_message_changed():
    _, fixes = fix(messy(), spec=SPEC12)
    for f in fixes:
        assert set(f) >= {"node", "code", "message", "changed"} and f["code"].startswith("FIX") and f["message"]
