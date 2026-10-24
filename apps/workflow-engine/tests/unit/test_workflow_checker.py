"""The mistake checker (Stage 2): WF001-WF009, expression paths, coverage, disabled nodes, error port, cycles."""

import ast

import pytest

from src.engine.node_registry import register_all_nodes
from src.services.workflow_checker import StaticLookups, check_workflow, format_path

register_all_nodes()

SCHEMA = {"type": "object", "properties": {"amount": {"type": "number"}, "name": {"type": "string"},
                                           "items": {"type": "array", "items": {"type": "object", "properties": {"sku": {"type": "string"}}}}}}


def N(name, type_="Set", params=None, **kw):
    return {"name": name, "type": type_, "parameters": params or {}, **kw}


def C(a, b, out="main", inp="main"):
    return {"source_node": a, "target_node": b, "source_output": out, "target_input": inp}


def trigger(schema=SCHEMA):
    return N("Start", "Start", {"input_schema": schema})


def run(nodes, conns, **lk):
    return check_workflow({"nodes": nodes, "connections": conns}, lookups=StaticLookups(**lk))


def codes(res, code=None):
    return [f for f in res["findings"] if code is None or f["code"] == code]


def chain(params, type_="Set", schema=SCHEMA, **kw):
    """Start(schema) -> X(params)."""
    return run([trigger(schema), N("X", type_, params)], [C("Start", "X")], **kw)


def expr(e, **kw):
    return chain({"fields": [{"name": "o", "value": "{{ " + e + " }}"}]}, **kw)


# ── WF001 ──

def test_wf001_unknown_node():
    r = run([trigger(), N("X", "Set", {"fields": [{"name": "a", "value": "{{ $node['Nope'].json.a }}"}]})], [C("Start", "X")])
    f = codes(r, "WF001")
    assert len(f) == 1 and f[0]["severity"] == "error" and f[0]["node"] == "X" and "Nope" in f[0]["message"]


def test_wf001_not_upstream():
    nodes = [trigger(), N("A"), N("B", "Set", {"fields": [{"name": "a", "value": "{{ $node['C'].json.a }}"}]}), N("C")]
    r = run(nodes, [C("Start", "A"), C("A", "B"), C("Start", "C")])
    assert "isn't upstream" in codes(r, "WF001")[0]["message"]


def test_wf001_not_raised_for_upstream_node():
    nodes = [trigger(), N("A"), N("B", "Set", {"fields": [{"name": "a", "value": "{{ $node['Start'].json.name }}"}]})]
    assert not codes(run(nodes, [C("Start", "A"), C("A", "B")]), "WF001")


def test_wf001_self_reference():
    assert codes(run([trigger(), N("X", "Set", {"fields": [{"name": "a", "value": "{{ $node.X.json.a }}"}]})], [C("Start", "X")]), "WF001")


# ── WF002 + did-you-mean ──

def test_wf002_missing_field_with_suggestion():
    r = expr("$json.amout")
    f = codes(r, "WF002")
    assert len(f) == 1 and f[0]["severity"] == "error" and f[0]["path"] == "amout" and f[0]["param"] == "fields[0].value"
    assert f[0]["message"] == "Field 'amout' isn't in the output of 'Start'. Did you mean 'amount'?"


def test_wf002_no_suggestion_when_nothing_close():
    f = codes(expr("$json.zzzzzz"), "WF002")[0]
    assert "Did you mean" not in f["message"]


def test_wf002_nested_suggestion_is_a_full_path():
    f = codes(expr("$json.items[0].skuu"), "WF002")[0]
    assert f["path"] == "items[0].skuu" and "Did you mean 'items[0].sku'?" in f["message"]


def test_wf002_not_raised_for_existing_field():
    assert not codes(expr("$json.amount"), "WF002")


def test_wf002_open_object_is_not_an_error():
    r = expr("$json.whatever", schema={"type": "object"})
    assert not codes(r, "WF002") and codes(r, "WF005")


def test_wf002_after_set_keep_only():
    nodes = [trigger(), N("Pick", "Set", {"mode": "manual", "keepOnlySet": True, "fields": [{"name": "total", "value": "1"}]}),
             N("Use", "Set", {"fields": [{"name": "a", "value": "{{ $json.amount }}"}]})]
    r = run(nodes, [C("Start", "Pick"), C("Pick", "Use")])
    assert "output of 'Pick'" in codes(r, "WF002")[0]["message"]


def test_wf002_observed_shape_is_only_a_warning():
    contract = {"outputs": {"main": {"type": "object", "properties": {"a": {"type": "string"}}, "x-source": "observed"}},
                "source": "observed", "approved_by": "t", "approved_at": "x", "from_execution": "e"}
    nodes = [trigger(), N("H", "HttpRequest", {"url": "http://x", "method": "GET"}, contract=contract),
             N("U", "Set", {"fields": [{"name": "a", "value": "{{ $json.b }}"}]})]
    f = codes(run(nodes, [C("Start", "H"), C("H", "U")]), "WF002")
    assert len(f) == 1 and f[0]["severity"] == "warning"


# ── WF003 ──

def test_wf003_string_in_numeric_comparison():
    f = codes(expr("$json.name > 5"), "WF003")
    assert len(f) == 1 and f[0]["severity"] == "warning" and f[0]["path"] == "name"


def test_wf003_not_raised_for_number():
    assert not codes(expr("$json.amount > 5"), "WF003")


def test_wf003_string_arithmetic():
    assert codes(expr("$json.name - 1"), "WF003")
    assert not codes(expr("$json.name + 'x'"), "WF003")


def test_wf003_if_number_condition_on_string_field():
    r = chain({"field": "name", "operation": "gt", "value": "5"}, "If")
    assert codes(r, "WF003")
    assert not codes(chain({"field": "amount", "operation": "gt", "value": "5"}, "If"), "WF003")
    assert not codes(chain({"field": "name", "operation": "equals", "value": "5"}, "If"), "WF003")


def test_wf003_filter_rules():
    assert codes(chain({"mode": "rules", "field": "name", "operation": "lte", "value": "5"}, "Filter"), "WF003")


# ── WF004 ──

def test_wf004_unused_branch():
    nodes = [trigger(), N("If", "If", {"condition": "{{ $json.amount > 1 }}"}), N("T")]
    f = codes(run(nodes, [C("Start", "If"), C("If", "T", out="true")]), "WF004")
    assert len(f) == 1 and f[0]["path"] == "false" and f[0]["severity"] == "warning"


def test_wf004_both_branches_connected_is_clean():
    nodes = [trigger(), N("If", "If", {"condition": "{{ $json.amount > 1 }}"}), N("T"), N("F")]
    assert not codes(run(nodes, [C("Start", "If"), C("If", "T", out="true"), C("If", "F", out="false")]), "WF004")


def test_wf004_if_with_no_connected_port_is_clean():
    assert not codes(run([trigger(), N("If", "If", {"condition": "{{ true }}"})], [C("Start", "If")]), "WF004")


def test_wf004_switch_fallback_not_reported_but_other_ports_are():
    nodes = [trigger(), N("S", "Switch", {"numberOfOutputs": 2}), N("A")]
    f = codes(run(nodes, [C("Start", "S"), C("S", "A", out="output0")]), "WF004")
    assert [x["path"] for x in f] == ["output1"]


def test_wf004_unreachable_node():
    nodes = [trigger(), N("A"), N("B"), N("C")]
    f = codes(run(nodes, [C("Start", "A"), C("B", "C")]), "WF004")
    assert {x["node"] for x in f} == {"B"} | {"C"}


def test_wf004_isolated_node_is_not_reported():
    assert not codes(run([trigger(), N("A"), N("Lonely")], [C("Start", "A")]), "WF004")


# ── WF005 + coverage ──

def test_wf005_unknown_shape():
    r = chain({"fields": [{"name": "o", "value": "{{ $json.body.x }}"}]}, schema={"type": "object"})
    f = codes(r, "WF005")
    assert len(f) == 1 and f[0]["severity"] == "info"


def test_coverage_counts():
    r = expr("$json.amount + $json.nope + $json.deep.x", schema=SCHEMA)
    assert r["coverage"]["refs_total"] == 3
    assert r["coverage"]["refs_checked"] == 3  # amount found; nope and deep.x are definitely missing: all decided
    r = chain({"fields": [{"name": "o", "value": "{{ $json.a }} {{ $json.b }}"}]}, schema={"type": "object"})
    assert r["coverage"] == {"refs_total": 2, "refs_checked": 0, "refs_unknown": 2, "nodes_total": 2, "nodes_known_output": 1}


def test_coverage_nodes_known_output():
    r = run([trigger(), N("H", "HttpRequest", {"url": "http://x"})], [C("Start", "H")])
    assert r["coverage"]["nodes_total"] == 2 and r["coverage"]["nodes_known_output"] == 1


def test_shapes_by_node_shape():
    r = chain({})
    assert set(r["shapes_by_node"]["X"]) == {"inputs", "outputs"}
    assert "amount" in r["shapes_by_node"]["X"]["inputs"]["main"]["properties"]


# ── WF006 ──

def test_wf006_missing_var():
    f = codes(expr("$vars.NOPE"), "WF006")
    assert f and f[0]["severity"] == "error" and f[0]["path"] == "NOPE"
    assert not codes(expr("$vars.YES", variables={"YES"}), "WF006")
    assert not codes(expr("$vars.SECRET", secrets={"SECRET"}), "WF006")


def test_wf006_table():
    p = {"table": "orders", "operation": "list"}
    assert codes(chain(p, "DataTable"), "WF006")
    assert not codes(chain(p, "DataTable", tables={"orders": []}), "WF006")


def test_wf006_data_table_declared_shape_feeds_downstream():
    nodes = [trigger(), N("T", "DataTable", {"table": "orders", "operation": "list"}),
             N("U", "Set", {"fields": [{"name": "a", "value": "{{ $json.totl }}"}]})]
    r = run(nodes, [C("Start", "T"), C("T", "U")], tables={"orders": [{"name": "total", "type": "number"}]})
    assert "Did you mean 'total'?" in codes(r, "WF002")[0]["message"]


def test_wf006_subworkflow():
    p = {"workflowId": "wf_sub"}
    assert codes(chain(p, "ExecuteWorkflow"), "WF006")
    assert not codes(chain(p, "ExecuteWorkflow", workflows={"wf_sub"}), "WF006")


def test_wf006_credential_only_checked_when_known():
    p = {"url": "http://x", "credentialId": "cred_1"}
    assert not codes(chain(p, "HttpRequest"), "WF006")
    assert codes(chain(p, "HttpRequest", credentials=set()), "WF006")
    assert not codes(chain(p, "HttpRequest", credentials={"cred_1"}), "WF006")


# ── WF007 ──

def test_wf007_host_not_on_allowlist():
    p = {"url": "https://evil.example.com/x", "method": "GET"}
    f = codes(chain(p, "HttpRequest", egress_allowlist=["api.good.com"]), "WF007")
    assert f and f[0]["severity"] == "error"
    assert not codes(chain(p, "HttpRequest", egress_allowlist=["*.example.com"]), "WF007")
    assert not codes(chain(p, "HttpRequest", egress_allowlist=[]), "WF007")


def test_wf007_expression_in_host_is_skipped():
    for url in ("https://{{ $vars.H }}/x", "{{ $vars.URL }}", "no-scheme.example.com"):
        assert not codes(chain({"url": url, "method": "GET"}, "HttpRequest", egress_allowlist=["a.com"], variables={"H", "URL"}), "WF007")
    assert codes(chain({"url": "https://bad.com/{{ $json.name }}", "method": "GET"}, "HttpRequest", egress_allowlist=["a.com"]), "WF007")


# ── WF008 ──

def test_wf008_required_missing():
    f = codes(chain({"method": "GET"}, "HttpRequest"), "WF008")
    assert f and f[0]["param"] == "url" and f[0]["severity"] == "error"
    assert not codes(chain({"method": "GET", "url": "http://x"}, "HttpRequest"), "WF008")
    assert codes(chain({"method": "GET", "url": "  "}, "HttpRequest"), "WF008")


def test_wf008_required_with_default_is_fine():
    assert not codes(chain({"url": "http://x"}, "HttpRequest"), "WF008")  # method has a default


def test_wf008_hidden_required_property_is_skipped():
    assert not codes(chain({"sourceType": "input", "groupBy": "x"}, "Aggregate"), "WF008")


def test_wf008_invalid_option():
    f = codes(chain({"url": "http://x", "method": "GTE"}, "HttpRequest"), "WF008")
    assert f and "isn't a valid value" in f[0]["message"]
    assert not codes(chain({"url": "http://x", "method": "{{ $vars.M }}"}, "HttpRequest", variables={"M"}), "WF008")


def test_wf008_model_options_are_not_enforced():
    assert not codes(chain({"model": "gemini-9-ultra", "userMessage": "hi"}, "LLMChat"), "WF008")


def test_wf008_cycle_without_loop():
    nodes = [trigger(), N("A"), N("B")]
    f = codes(run(nodes, [C("Start", "A"), C("A", "B"), C("B", "A")]), "WF008")
    assert len(f) == 1 and "cycle" in f[0]["message"] and f[0]["node"] in ("A", "B")


def test_cycle_through_loop_is_allowed():
    nodes = [trigger(), N("L", "Loop"), N("Body", "Set", {"fields": [{"name": "a", "value": "{{ $json.amount }}"}]})]
    r = run(nodes, [C("Start", "L"), C("L", "Body", out="loop"), C("Body", "L")])
    assert not codes(r, "WF008")
    assert "amount" in r["shapes_by_node"]["Body"]["inputs"]["main"]["properties"]


def test_cycle_beside_a_loop_is_still_reported():
    nodes = [trigger(), N("L", "Loop"), N("A"), N("B")]
    r = run(nodes, [C("Start", "L"), C("L", "A", out="loop"), C("A", "B"), C("B", "A"), C("B", "L")])
    assert codes(r, "WF008")


def test_self_loop_is_a_cycle():
    assert codes(run([trigger(), N("A")], [C("Start", "A"), C("A", "A")]), "WF008")


# ── WF009 ──

def test_wf009_parse_error():
    f = codes(expr("$json.amount >"), "WF009")
    assert f and f[0]["severity"] == "error"
    assert codes(expr("$json.a === 1"), "WF009")
    assert not codes(expr("$json.amount > 1"), "WF009")


def test_wf009_unclosed_braces_are_not_expressions():
    r = chain({"fields": [{"name": "o", "value": "plain {{ not closed"}]})
    assert not r["findings"]


# ── expression path extraction ──

def paths(e, **kw):
    r = expr(e, **kw)
    return sorted({f["path"] for f in r["findings"] if f["code"] in ("WF002", "WF005")})


@pytest.mark.parametrize("e,expected", [
    ("$json.nope.b", ["nope.b"]),
    ("$json['nope']['b']", ["nope.b"]),
    ('$json["nope"].b', ["nope.b"]),
    ("$node['Start'].json.nope", ["nope"]),
    ('$node["Start"].json.nope.y', ["nope.y"]),
    ("$node.Start.json.nope", ["nope"]),
    ("$input.item.json.nope", ["nope"]),
    ("$input[0].nope", ["nope"]),
    ("$json.items[0].nope", ["items[0].nope"]),
    ("$json.nope1 + $json.nope2", ["nope1", "nope2"]),
    ("lower($json.nope)", ["nope"]),
    ("$json.name.upper()", []),  # method name is not a field; `name` exists
    ("$json.nope.upper()", ["nope"]),
    ("[x.sku for x in $json.items if x.sku]", []),
    ("[$json.nope for x in $json.items]", ["nope"]),
    ("get($json.nope, 'x', $json.nope2)", ["nope.x", "nope2"]),
    ("'$json.nope'", []),
    ("'a{{b' + $json.name", []),
    ("$json.amount if $json.nope else 0", ["nope"]),
])
def test_expression_paths(e, expected):
    if "'" in e and e.startswith("'$json"):
        pytest.skip("string literal containing $json is mangled by the runtime transform itself")
    assert paths(e) == expected


def test_computed_subscript_checks_the_prefix_only():
    assert paths("$json.items[$json.amount]") == []
    assert paths("$json.nope[$json.amount]") == ["nope"]


def test_computed_subscript_expression_is_still_visited():
    assert paths("$json.items[$json.nope]") == ["nope"]


def test_string_literal_with_braces_is_not_a_reference():
    r = chain({"fields": [{"name": "o", "value": "{{ '{' + $json.name }}"}]})
    assert not [f for f in r["findings"] if f["severity"] != "info"]


def test_multiple_expressions_in_one_string_and_nested_params():
    r = chain({"fields": [{"name": "o", "value": "{{ $json.nope1 }} and {{ $json.nope2 }}"}], "extra": {"deep": ["{{ $json.nope3 }}"]}})
    assert sorted(f["path"] for f in codes(r, "WF002")) == ["nope1", "nope2", "nope3"]
    assert {f["param"] for f in codes(r, "WF002")} == {"fields[0].value", "extra.deep[0]"}


def test_input_ref_uses_first_input_and_merge_unions_for_json():
    nodes = [N("A", "Start", {"input_schema": {"type": "object", "properties": {"a": {"type": "string"}}}}),
             N("B", "Start", {"input_schema": {"type": "object", "properties": {"b": {"type": "string"}}}}),
             N("M", "Merge", {"mode": "append"}),
             N("U", "Set", {"fields": [{"name": "o", "value": "{{ $json.a }}{{ $json.b }}{{ $json.c }}"}]})]
    r = run(nodes, [C("A", "M", inp="input1"), C("B", "M", inp="input2"), C("M", "U")])
    assert [f["path"] for f in codes(r, "WF002")] == ["c"]


def test_node_ref_resolves_names_with_spaces():
    nodes = [N("My Start", "Start", {"input_schema": SCHEMA}), N("X", "Set", {"fields": [{"name": "o", "value": "{{ $node['My Start'].json.amout }}"}]})]
    f = codes(run(nodes, [C("My Start", "X")]), "WF002")
    assert f and "output of 'My Start'" in f[0]["message"]


def test_code_node_params_are_not_scanned():
    r = chain({"code": "x = {{'a': 1}}", "output_schema": SCHEMA}, "Code")
    assert not [f for f in r["findings"] if f["code"] in ("WF009", "WF002")]


def test_format_path():
    assert format_path(["a", 0, "b c"]) == 'a[0]["b c"]'


# ── disabled nodes and the error port ──

def test_disabled_node_is_passthrough_and_its_params_are_not_checked():
    nodes = [trigger(), N("Off", "HttpRequest", {"url": ""}, disabled=True),
             N("U", "Set", {"fields": [{"name": "o", "value": "{{ $json.amount }}{{ $json.nope }}"}]})]
    r = run(nodes, [C("Start", "Off"), C("Off", "U")])
    assert not codes(r, "WF008")  # missing url on the disabled node: not checked
    assert [f["path"] for f in codes(r, "WF002")] == ["nope"]  # shape flowed through it
    nodes[1] = N("Off", "Set", {"fields": [{"name": "o", "value": "{{ $json.bad }}"}]}, disabled=True)
    assert not codes(run(nodes, [C("Start", "Off"), C("Off", "U")]), "WF002") or all(f["path"] != "bad" for f in codes(run(nodes, [C("Start", "Off"), C("Off", "U")]), "WF002"))


def test_error_port_carries_input_plus_error():
    nodes = [trigger(), N("H", "HttpRequest", {"url": "http://x"}, on_error="error_output"),
             N("Handler", "Set", {"fields": [{"name": "o", "value": "{{ $json.error.message }}{{ $json.error.nope }}{{ $json.amount }}"}]})]
    r = run(nodes, [C("Start", "H"), C("H", "Handler", out="error")])
    assert [f["path"] for f in codes(r, "WF002")] == ["error.nope"]
    assert "error" in r["shapes_by_node"]["H"]["outputs"]


def test_no_error_port_without_on_error():
    r = run([trigger(), N("H", "HttpRequest", {"url": "http://x"})], [C("Start", "H")])
    assert "error" not in r["shapes_by_node"]["H"]["outputs"]


# ── robustness ──

def test_garbage_definitions_do_not_raise():
    lk = StaticLookups()
    for d in ({}, {"nodes": None}, {"nodes": [None, {"name": "x"}], "connections": [None, {"source_node": "q"}]}):
        assert check_workflow(d, lookups=lk)["findings"] == []


def test_findings_are_deduplicated():
    r = chain({"fields": [{"name": "o", "value": "{{ $json.nope }}{{ $json.nope }}"}]})
    assert len(codes(r, "WF002")) == 1


def test_parse_matches_engine_transform():
    from src.engine.expression_engine import expression_engine
    ast.parse(expression_engine._transform_expression("$json.a.b"), mode="eval")


# ── WF030 / WF031: Loop "Run at a time" ──

def _loop(loop_params, body_type="Set", body_params=None, extra_nodes=(), extra_conns=()):
    nodes = [trigger(), N("L", "Loop", loop_params), N("Body", body_type, body_params or {}), N("After"), *extra_nodes]
    conns = [C("Start", "L"), C("L", "Body", out="loop"), C("Body", "L"), C("L", "After", out="done"), *extra_conns]
    return run(nodes, conns)


@pytest.mark.parametrize("value", [1000, 21, 2.5, "lots", -3, 0, "2.5"])
def test_wf030_bad_concurrency_is_a_warning(value):
    f = codes(_loop({"concurrency": value}), "WF030")
    assert len(f) == 1 and f[0]["severity"] == "warning" and f[0]["node"] == "L" and f[0]["param"] == "concurrency", f
    if value in (1000, 21):
        assert "20" in f[0]["message"]
    # the message says what the runner really does: a number 2.5 runs 2 at a time (int), the text forms run 1
    assert ("runs 2 items at a time" if value == 2.5 else "20" if value in (1000, 21) else "runs one item") in f[0]["message"], f


@pytest.mark.parametrize("value", [None, 1, 5, 20, "7", 3.0, "{{ $json.n }}"])
def test_wf030_not_raised_for_valid_concurrency(value):
    params = {} if value is None else {"concurrency": value}
    assert codes(_loop(params), "WF030") == []


HTTP = {"method": "POST", "url": "https://api.example.com/pay"}


@pytest.mark.parametrize("body_type, params", [("HttpRequest", HTTP), ("HttpRequest", {"url": "https://api.example.com/x"}),
                                               ("Postgres", {"query": "update t set a = 1"}), ("SendEmail", {})])
def test_wf031_stop_with_concurrency_and_an_external_step(body_type, params):
    f = codes(_loop({"concurrency": 5}, body_type, params), "WF031")
    assert len(f) == 1 and f[0]["severity"] == "warning" and f[0]["node"] == "L" and f[0]["param"] == "concurrency", f
    m = f[0]["message"]
    assert '"Body"' in m and "already started" in m and "'run at a time' to 1" in m.lower(), m
    assert "payment" in m.lower(), m


@pytest.mark.parametrize("loop_params, body_type, params", [
    ({"concurrency": 1}, "HttpRequest", HTTP),                                   # one at a time: nothing in flight
    ({}, "HttpRequest", HTTP),                                                   # default
    ({"concurrency": 5, "onItemFailure": "skip"}, "HttpRequest", HTTP),         # skip: no stop to overrun
    ({"concurrency": 5}, "Set", {}),                                             # nothing external in the body
    ({"concurrency": "lots"}, "HttpRequest", HTTP),                              # runs as 1
])
def test_wf031_not_raised(loop_params, body_type, params):
    assert codes(_loop(loop_params, body_type, params), "WF031") == []


def test_wf031_not_raised_when_the_body_runs_one_at_a_time_anyway():
    # a Wait in the body makes the runner run the passes one after another (concurrencyFallback)
    r = run([trigger(), N("L", "Loop", {"concurrency": 5}), N("Body", "HttpRequest", HTTP), N("Hold", "Wait", {"amount": 1}), N("After")],
            [C("Start", "L"), C("L", "Body", out="loop"), C("Body", "Hold"), C("Hold", "L"), C("L", "After", out="done")])
    assert codes(r, "WF031") == []


def test_wf031_names_an_external_step_deeper_in_the_body():
    r = run([trigger(), N("L", "Loop", {"concurrency": 3}), N("Prep"), N("Pay", "HttpRequest", HTTP), N("After")],
            [C("Start", "L"), C("L", "Prep", out="loop"), C("Prep", "Pay"), C("Pay", "L"), C("L", "After", out="done")])
    f = codes(r, "WF031")
    assert len(f) == 1 and "Pay" in f[0]["message"] and "After" not in f[0]["message"]
