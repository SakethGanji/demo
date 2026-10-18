"""Unit tests for the n8n importer (Stage 2 contract section 4)."""
from __future__ import annotations

import asyncio
import glob
import json
import os

import pytest
from fastapi import HTTPException

from src.engine.expression_engine import ExpressionContext, expression_engine
from src.engine.types import NodeData
from src.routes.n8n_import import N8nImportRequest, import_n8n_workflow
from src.schemas.workflow import WorkflowCreateRequest
from src.services.n8n_import import import_n8n, translate_expression

FIXTURES = sorted(glob.glob(os.path.join(os.path.dirname(__file__), "..", "fixtures", "n8n", "*.json")))


def node(name, type_, params=None, version=1, pos=(0, 0), **extra):
    return {"name": name, "type": f"n8n-nodes-base.{type_}", "typeVersion": version,
            "position": list(pos), "parameters": params or {}, **extra}


def wf(nodes, connections=None, name="wf"):
    return {"name": name, "nodes": nodes, "connections": connections or {}}


def conn(*targets_per_output):
    return {"main": [[{"node": t, "type": "main", "index": 0} for t in outs] for outs in targets_per_output]}


def one(nodes, connections=None):
    w, r = import_n8n(wf(nodes, connections))
    WorkflowCreateRequest(**w)
    return w, r


def by_name(w, name):
    return next(n for n in w["nodes"] if n["name"] == name)


def evaluate(expr_with_braces, json_data=None, nodes=None):
    ctx = ExpressionContext(json_data=json_data or {}, input_data=[NodeData(json=json_data or {})],
                            node_data=nodes or {}, env={}, execution={}, item_index=0)
    return expression_engine.resolve(expr_with_braces, ctx)


# ---------------------------------------------------------------- node mapping + parameters

def test_triggers_and_start():
    w, r = one([
        node("Manual", "manualTrigger"),
        node("Hook", "webhook", {"path": "orders", "httpMethod": "PUT", "responseMode": "responseNode"}),
        node("Every", "scheduleTrigger", {"rule": {"interval": [{"field": "minutes", "minutesInterval": 5}]}}),
        node("Cron1", "cron", {"triggerTimes": {"item": [{"mode": "everyDay", "hour": 3, "minute": 30}]}}),
    ])
    assert by_name(w, "Manual")["type"] == "Start"
    assert by_name(w, "Hook") ["parameters"] == {"method": "PUT", "path": "orders", "responseMode": "lastNode"}
    assert by_name(w, "Every")["parameters"] == {"mode": "interval", "interval": 300}
    assert by_name(w, "Cron1")["parameters"] == {"mode": "cron", "cronExpression": "30 3 * * *"}
    assert [m["node_type"] for m in r["mapped"]] == ["Start", "Webhook", "Cron", "Cron"]
    assert r["unmapped_nodes"] == []


def test_schedule_cron_expression_and_hours():
    w, _ = one([
        node("A", "scheduleTrigger", {"rule": {"interval": [{"field": "cronExpression", "expression": "*/15 * * * *"}]}}, 1.2),
        node("B", "scheduleTrigger", {"rule": {"interval": [{"field": "hours", "hoursInterval": 2}]}}, 1.2),
    ])
    assert by_name(w, "A")["parameters"] == {"mode": "cron", "cronExpression": "*/15 * * * *"}
    assert by_name(w, "B")["parameters"] == {"mode": "interval", "interval": 7200}


def test_set_v1_values():
    w, _ = one([node("S", "set", {"values": {
        "string": [{"name": "a", "value": "=Hi {{$json.name}}"}],
        "number": [{"name": "n", "value": 5}],
        "boolean": [{"name": "b", "value": True}]}, "keepOnlySet": True}, 1)])
    p = by_name(w, "S")["parameters"]
    assert p["mode"] == "manual" and p["keepOnlySet"] is True
    assert p["fields"] == [{"name": "a", "value": "Hi {{ $json.name }}"}, {"name": "n", "value": 5}, {"name": "b", "value": True}]


def test_set_v3_assignments_and_include_other_fields():
    params = {"assignments": {"assignments": [
        {"id": "1", "name": "x", "type": "string", "value": "={{ $json.a }}"},
        {"id": "2", "name": "y", "type": "number", "value": 3}]}, "includeOtherFields": True}
    w, _ = one([node("S", "set", params, 3.4)])
    p = by_name(w, "S")["parameters"]
    assert p["fields"] == [{"name": "x", "value": "{{ $json.a }}"}, {"name": "y", "value": 3}]
    assert p["keepOnlySet"] is False
    w, _ = one([node("S", "set", {"assignments": {"assignments": []}}, 3.4)])
    assert by_name(w, "S")["parameters"]["keepOnlySet"] is True


def test_set_raw_json_mode():
    w, _ = one([node("S", "set", {"mode": "raw", "jsonOutput": "={ \"a\": {{ $json.a }} }"}, 3.4)])
    p = by_name(w, "S")["parameters"]
    assert p["mode"] == "json" and p["jsonData"] == '{ "a": {{ $json.a }} }'


def test_if_v1_conditions():
    params = {"conditions": {
        "number": [{"value1": "={{$json.n}}", "operation": "larger", "value2": 5}],
        "string": [{"value1": "={{$json.s}}", "operation": "contains", "value2": "x"}]},
        "combineOperation": "any"}
    w, _ = one([node("If", "if", params, 1)])
    cond = by_name(w, "If")["parameters"]["condition"]
    assert cond == '{{ ("x" in str($json.s)) or ($json.n > 5) }}'
    assert evaluate(cond, {"n": 9, "s": "a"}) is True
    assert evaluate(cond, {"n": 1, "s": "a"}) is False
    assert evaluate(cond, {"n": 1, "s": "zxz"}) is True


def test_if_v2_conditions_and_operators():
    params = {"conditions": {"combinator": "and", "conditions": [
        {"leftValue": "={{ $json.status }}", "rightValue": "ok", "operator": {"type": "string", "operation": "equals"}},
        {"leftValue": "={{ $json.items }}", "operator": {"type": "array", "operation": "notEmpty", "singleValue": True}},
        {"leftValue": "={{ $json.count }}", "rightValue": "3", "operator": {"type": "number", "operation": "gte"}},
        {"leftValue": "={{ $json.flag }}", "operator": {"type": "boolean", "operation": "true", "singleValue": True}},
        {"leftValue": "={{ $json.name }}", "rightValue": "A", "operator": {"type": "string", "operation": "startsWith"}},
        {"leftValue": "={{ $json.missing }}", "operator": {"type": "string", "operation": "notExists", "singleValue": True}},
    ]}}
    w, r = one([node("If", "if", params, 2.2)])
    cond = by_name(w, "If")["parameters"]["condition"]
    good = {"status": "ok", "items": [1], "count": 4, "flag": True, "name": "Abe"}
    assert evaluate(cond, good) is True
    assert evaluate(cond, {**good, "status": "bad"}) is False
    assert r["untranslated_expressions"] == []


def test_if_unsupported_operator_is_reported_not_dropped():
    params = {"conditions": {"conditions": [
        {"leftValue": "={{ $json.s }}", "rightValue": "^a", "operator": {"type": "string", "operation": "regex"}}]}}
    w, r = one([node("If", "if", params, 2)])
    assert by_name(w, "If")["parameters"]["condition"] == "{{ True }}"
    assert r["untranslated_expressions"][0]["node"] == "If"


def test_filter_maps_to_expression_mode():
    params = {"conditions": {"conditions": [{"leftValue": "={{ $json.n }}", "rightValue": 2, "operator": {"type": "number", "operation": "gt"}}]}}
    w, _ = one([node("F", "filter", params, 2)])
    assert by_name(w, "F")["parameters"] == {"mode": "expression", "condition": "{{ ($json.n > 2) }}"}


def test_switch_v3_rules_native_and_expression_and_fallback():
    params = {"rules": {"values": [
        {"conditions": {"combinator": "and", "conditions": [
            {"leftValue": "={{ $json.k }}", "rightValue": "a", "operator": {"type": "string", "operation": "equals"}}]}},
        {"conditions": {"combinator": "and", "conditions": [
            {"leftValue": "={{ $json.k }}", "rightValue": "b", "operator": {"type": "string", "operation": "equals"}},
            {"leftValue": "={{ $json.n }}", "rightValue": 1, "operator": {"type": "number", "operation": "equals"}}]}},
    ]}, "options": {"fallbackOutput": "extra"}}
    w, _ = one([node("Sw", "switch", params, 3), node("A", "noOp"), node("B", "noOp"), node("C", "noOp")],
               {"Sw": conn(["A"], ["B"], ["C"])})
    p = by_name(w, "Sw")["parameters"]
    assert p["numberOfOutputs"] == 2 and p["mode"] == "rules"
    assert p["rules"][0] == {"output": 0, "field": "{{ $json.k }}", "operation": "equals", "value": "a"}
    assert p["rules"][1]["operation"] == "isTrue" and p["rules"][1]["output"] == 1
    assert evaluate(p["rules"][1]["field"], {"k": "b", "n": 1}) is True
    ports = {(c["target_node"]): c["source_output"] for c in w["connections"] if c["source_node"] == "Sw"}
    assert ports == {"A": "output0", "B": "output1", "C": "fallback"}


def test_switch_v1_rules_and_expression_mode():
    params = {"dataType": "number", "value1": "={{$json.n}}", "rules": {"rules": [
        {"operation": "larger", "value2": 10, "output": 0}, {"operation": "smaller", "value2": 5, "output": 1}]}}
    w, _ = one([node("Sw", "switch", params, 1)])
    p = by_name(w, "Sw")["parameters"]
    assert p["rules"] == [
        {"output": 0, "field": "{{ $json.n }}", "operation": "gt", "value": "10"},
        {"output": 1, "field": "{{ $json.n }}", "operation": "lt", "value": "5"}]
    assert p["numberOfOutputs"] == 2
    w, _ = one([node("Sw", "switch", {"mode": "expression", "output": "={{ $json.i }}", "numberOutputs": 3}, 3)])
    assert by_name(w, "Sw")["parameters"] == {"mode": "expression", "numberOfOutputs": 3, "outputIndex": "{{ $json.i }}"}


def test_http_request_v4_all_parts():
    params = {
        "method": "post", "url": "=https://api.x.com/{{ $json.id }}",
        "sendHeaders": True, "headerParameters": {"parameters": [{"name": "X-Key", "value": "={{ $vars.KEY }}"}]},
        "sendQuery": True, "queryParameters": {"parameters": [{"name": "page", "value": "={{ $json.p }}"}, {"name": "q", "value": "x"}]},
        "sendBody": True, "contentType": "json", "specifyBody": "json", "jsonBody": "={\"a\": \"{{ $json.a }}\"}",
        "options": {"response": {"response": {"responseFormat": "text"}}}}
    w, _ = one([node("H", "httpRequest", params, 4.2)])
    p = by_name(w, "H")["parameters"]
    assert p["method"] == "POST"
    assert p["url"] == "https://api.x.com/{{ $json.id }}?page={{ $json.p }}&q=x"
    assert p["headers"] == [{"name": "X-Key", "value": "{{ $vars.KEY }}"}]
    assert p["body"] == '{"a": "{{ $json.a }}"}'
    assert p["responseType"] == "text"


def test_http_request_v4_keypair_body_and_v1_ui():
    params = {"url": "https://x", "method": "POST", "sendBody": True, "specifyBody": "keypair",
              "bodyParameters": {"parameters": [{"name": "a", "value": "1"}, {"name": "b", "value": "={{ $json.b }}"}]}}
    w, _ = one([node("H", "httpRequest", params, 4.1)])
    assert json.loads(by_name(w, "H")["parameters"]["body"]) == {"a": "1", "b": "{{ $json.b }}"}
    v1 = {"url": "https://x", "requestMethod": "POST",
          "headerParametersUi": {"parameter": [{"name": "h", "value": "v"}]},
          "queryParametersUi": {"parameter": [{"name": "q", "value": "1"}]},
          "bodyParametersUi": {"parameter": [{"name": "k", "value": "v"}]}}
    w, _ = one([node("H", "httpRequest", v1, 1)])
    p = by_name(w, "H")["parameters"]
    assert p["method"] == "POST" and p["url"] == "https://x?q=1"
    assert p["headers"] == [{"name": "h", "value": "v"}] and json.loads(p["body"]) == {"k": "v"}


def test_loop_wait_respond_stop_exec():
    w, _ = one([
        node("L", "splitInBatches", {"batchSize": 5}, 3),
        node("W", "wait", {"amount": 2, "unit": "minutes"}, 1.1),
        node("W2", "wait", {"amount": 1, "unit": "days"}, 1.1),
        node("R", "respondToWebhook", {"respondWith": "json", "responseBody": "={{ $json }}",
                                       "options": {"responseCode": 201, "responseHeaders": {"entries": [{"name": "X", "value": "y"}]}}}, 1.1),
        node("St", "stopAndError", {"errorMessage": "=bad {{ $json.x }}"}),
        node("E", "executeWorkflow", {"workflowId": {"__rl": True, "value": "abc", "mode": "id"}}, 1.1),
    ])
    assert by_name(w, "L")["parameters"] == {"batchSize": 5, "maxIterations": 0}
    assert by_name(w, "W")["parameters"] == {"unit": "minutes", "duration": 2}
    assert by_name(w, "W2")["parameters"] == {"unit": "hours", "duration": 24}
    assert by_name(w, "R")["parameters"] == {"statusCode": 201, "contentType": "application/json", "body": "{{ $json }}",
                                              "headers": [{"name": "X", "value": "y"}]}
    assert by_name(w, "St")["parameters"] == {"errorType": "error", "message": "bad {{ $json.x }}"}
    assert by_name(w, "E")["parameters"] == {"workflowId": "abc", "inputMode": "passThrough"}


def test_wait_on_webhook_is_unmapped():
    _, r = one([node("W", "wait", {"resume": "webhook"}, 1.1)])
    assert r["unmapped_nodes"][0]["n8n_name"] == "W"


def test_postgres_execute_query_and_unsupported_insert():
    w, r = one([
        node("Q", "postgres", {"operation": "executeQuery", "query": "=select * from t where id = $1",
                               "options": {"queryReplacement": "={{ [$json.id] }}"}}, 2.5),
        node("Ins", "postgres", {"table": "t"}, 2.5),
    ])
    p = by_name(w, "Q")["parameters"]
    assert p["operation"] == "query" and p["query"] == "select * from t where id = $1"
    assert p["queryParameters"] == "{{ [$json.id] }}" and "$vars." in p["connectionString"]
    assert [u["n8n_name"] for u in r["unmapped_nodes"]] == ["Ins"]
    assert by_name(w, "Ins")["type"] == "Set" and by_name(w, "Ins")["disabled"] is True


def test_mongo_find_and_aggregate_item_lists():
    w, r = one([
        node("M", "mongoDb", {"operation": "find", "collection": "c", "query": "={\"a\": {{ $json.a }} }", "limit": 3}, 1),
        node("Agg", "aggregate", {"aggregate": "aggregateAllItemData", "destinationFieldName": "all"}, 1),
        node("Agg2", "aggregate", {"aggregate": "aggregateIndividualFields", "fieldsToAggregate": {"fieldToAggregate": [{"fieldToAggregate": "id"}]}}, 1),
        node("So", "splitOut", {"fieldToSplitOut": "rows"}, 1),
        node("IL", "itemLists", {"operation": "limit", "maxItems": 4}, 3),
    ])
    assert by_name(w, "M")["parameters"]["filter"] == '{"a": {{ $json.a }} }'
    assert by_name(w, "M")["parameters"]["operation"] == "find"
    assert by_name(w, "Agg")["type"] == "ItemLists"
    assert by_name(w, "Agg")["parameters"] == {"operation": "summarize", "summarizeField": "all", "includeCount": False}
    assert by_name(w, "Agg2")["parameters"]["aggregations"] == [{"field": "id", "aggOperation": "collect", "outputField": "id"}]
    assert by_name(w, "So")["parameters"]["arrayField"] == "rows"
    assert by_name(w, "IL")["parameters"] == {"operation": "limit", "maxItems": 4, "offset": 0}
    assert not r["unmapped_nodes"]


def test_noop_is_set_passthrough():
    w, r = one([node("N", "noOp")])
    assert by_name(w, "N")["type"] == "Set" and by_name(w, "N")["parameters"]["fields"] == []
    assert r["unmapped_nodes"] == []


def test_execute_workflow_trigger_and_error_handling_flags():
    w, _ = one([node("T", "executeWorkflowTrigger", {}, 1),
                node("H", "httpRequest", {"url": "https://x"}, 4, onError="continueRegularOutput",
                     retryOnFail=True, maxTries=3, waitBetweenTries=500)])
    assert by_name(w, "T")["type"] == "ExecuteWorkflowTrigger"
    h = by_name(w, "H")
    assert h["on_error"] == "continue" and h["retry_on_fail"] == 2 and h["retry_delay"] == 500


def test_http_error_output_port():
    w, _ = one([node("H", "httpRequest", {"url": "https://x"}, 4, onError="continueErrorOutput"),
                node("Ok", "noOp"), node("Bad", "noOp")], {"H": conn(["Ok"], ["Bad"])})
    ports = {c["target_node"]: c["source_output"] for c in w["connections"] if c["source_node"] == "H"}
    assert ports == {"Ok": "main", "Bad": "error"} and by_name(w, "H")["on_error"] == "error_output"


# ---------------------------------------------------------------- connections

def test_if_port_mapping():
    w, _ = one([node("If", "if", {}, 1), node("T", "noOp"), node("F", "noOp")], {"If": conn(["T"], ["F"])})
    ports = {c["target_node"]: c["source_output"] for c in w["connections"] if c["source_node"] == "If"}
    assert ports == {"T": "true", "F": "false"}


def test_loop_port_mapping_by_version():
    for version, expected in ((3, {"D": "done", "B": "loop"}), (1, {"D": "loop", "B": "loop"})):
        w, _ = one([node("L", "splitInBatches", {}, version), node("D", "noOp"), node("B", "noOp")],
                   {"L": conn(["D"], ["B"])})
        ports = {c["target_node"]: c["source_output"] for c in w["connections"] if c["source_node"] == "L"}
        assert ports == expected


def test_merge_inputs_and_modes():
    nodes = [node("A", "manualTrigger"), node("B", "noOp"), node("M", "merge", {"mode": "append"}, 3)]
    cs = {"A": conn(["B", "M"]), "B": {"main": [[{"node": "M", "type": "main", "index": 1}]]}}
    w, _ = one(nodes, cs)
    edges = [c for c in w["connections"] if c["target_node"] == "M"]
    assert {c["source_node"] for c in edges} == {"A", "B"}
    assert all(c["target_input"] == "main" for c in edges)  # Merge has one dynamic input "main"
    for params, mode in (({"mode": "wait"}, "waitForAll"), ({"mode": "combine", "combineBy": "combineByPosition"}, "combinePairs"),
                         ({"mode": "mergeByKey", "propertyName1": "id", "propertyName2": "id"}, "keepMatches")):
        w, _ = one([node("M", "merge", params, 2)])
        assert by_name(w, "M")["parameters"]["mode"] == mode
    w, _ = one([node("M", "merge", {"mode": "mergeByKey", "propertyName1": "id"}, 2)])
    assert by_name(w, "M")["parameters"]["matchField"] == "id"
    w, r = one([node("M", "merge", {"mode": "combine", "combineBy": "combineAll"}, 3)])
    assert by_name(w, "M")["parameters"]["mode"] == "append" and r["notes"]


def test_non_main_connection_types_and_dangling_targets_ignored():
    w, _ = one([node("A", "manualTrigger"), node("B", "noOp")],
               {"A": {"main": [[{"node": "B", "type": "main", "index": 0}, {"node": "Ghost", "type": "main", "index": 0}]],
                      "ai_tool": [[{"node": "B", "type": "ai_tool", "index": 0}]]},
                "Nobody": conn(["B"])})
    assert w["connections"] == [{"source_node": "A", "target_node": "B", "source_output": "main", "target_input": "main"}]


# ---------------------------------------------------------------- Code / unknown / sticky / names / positions

def test_code_nodes_disabled_with_source_kept():
    src = "return items.map(i => ({json: {x: i.json.a * 2}}));"
    w, r = one([node("Js", "code", {"jsCode": src}, 2), node("Fn", "function", {"functionCode": "return items;"}, 1)])
    for name, source in (("Js", src), ("Fn", "return items;")):
        n = by_name(w, name)
        assert n["type"] == "Code" and n["disabled"] is True and n["parameters"]["code"] == source
        assert n["parameters"]["n8n_language"] == "javascript"
    assert r["disabled_code_nodes"] == ["Js", "Fn"]


def test_unknown_node_becomes_disabled_placeholder_and_connections_survive():
    w, r = one([node("Start", "manualTrigger"), node("Post to Slack", "slack", {"channel": "x"}, 2.3), node("After", "noOp")],
               {"Start": conn(["Post to Slack"]), "Post to Slack": conn(["After"])})
    n = by_name(w, "Post to Slack")
    assert n["type"] == "Set" and n["disabled"] is True and n["parameters"]["n8n_type"] == "n8n-nodes-base.slack"
    assert r["unmapped_nodes"] == [{"n8n_name": "Post to Slack", "n8n_type": "n8n-nodes-base.slack", "reason": r["unmapped_nodes"][0]["reason"]}]
    assert "slack" in r["unmapped_nodes"][0]["reason"]
    pairs = {(c["source_node"], c["target_node"]) for c in w["connections"]}
    assert pairs == {("Start", "Post to Slack"), ("Post to Slack", "After")}


def test_langchain_nodes_are_unmapped():
    n = {"name": "Agent", "type": "@n8n/n8n-nodes-langchain.agent", "typeVersion": 1, "position": [0, 0], "parameters": {}}
    w, r = import_n8n(wf([node("T", "manualTrigger"), n]))
    assert r["unmapped_nodes"][0]["n8n_name"] == "Agent"


def test_sticky_notes_dropped_silently():
    w, r = one([node("T", "manualTrigger"), node("Note", "stickyNote", {"content": "hi"})])
    assert [n["name"] for n in w["nodes"]] == ["T"]
    assert r["unmapped_nodes"] == [] and r["mapped"][0]["n8n_name"] == "T"


def test_only_sticky_notes_rejected():
    with pytest.raises(ValueError):
        import_n8n(wf([node("Note", "stickyNote")]))


def test_positions():
    w, _ = one([node("T", "manualTrigger", pos=(120, -40.5))])
    assert by_name(w, "T")["position"] == {"x": 120.0, "y": -40.5}


def test_names_unique_valid_and_refs_follow_renames():
    nodes = [node("Trigger", "manualTrigger"), node('He said "hi"', "set", {"assignments": {"assignments": [
        {"name": "a", "value": "1"}]}}, 3.4), node("Use", "set", {"assignments": {"assignments": [
            {"name": "v", "value": "={{ $('He said \"hi\"').item.json.a }}"}]}}, 3.4),
             node("dup", "noOp"), node("Dup", "noOp"), node("  ", "noOp")]
    w, _ = one(nodes, {"Trigger": conn(['He said "hi"']), 'He said "hi"': conn(["Use"])})
    names = [n["name"] for n in w["nodes"]]
    assert len({n.lower() for n in names}) == len(names) and all(n.strip() and '"' not in n for n in names)
    renamed = next(n for n in names if n.startswith("He said"))
    assert by_name(w, "Use")["parameters"]["fields"][0]["value"] == f'{{{{ $node["{renamed}"].json.a }}}}'
    assert {(c["source_node"], c["target_node"]) for c in w["connections"]} >= {("Trigger", renamed), (renamed, "Use")}


def test_start_added_when_no_trigger_mapped():
    w, r = one([node("Form", "formTrigger", {}, 2), node("Do", "noOp")], {"Form": conn(["Do"])})
    assert by_name(w, "Start")["type"] == "Start"
    assert {"source_node": "Start", "target_node": "Form", "source_output": "main", "target_input": "main"} in w["connections"]
    assert r["notes"]


def test_workflow_name_and_input_unwrapping():
    w, _ = import_n8n(wf([node("T", "manualTrigger")], name="Orders"))
    assert w["name"] == "Orders"
    w, _ = import_n8n(wf([node("T", "manualTrigger")], name="Orders"), name="Override")
    assert w["name"] == "Override"
    wrapped = {"workflow": {"workflow": wf([node("T", "manualTrigger")], name="Wrapped")}}
    assert import_n8n(wrapped)[0]["name"] == "Wrapped"
    assert import_n8n(json.dumps(wf([node("T", "manualTrigger")])))[0]["nodes"]


@pytest.mark.parametrize("bad", [{}, {"nodes": []}, {"nodes": [{"name": "x"}]}, {"nodes": "x"}, [], "not json", 3,
                                 {"nodes": [{"name": "x", "type": "t"}], "connections": []}])
def test_non_n8n_input_raises_value_error(bad):
    with pytest.raises(ValueError):
        import_n8n(bad)


def test_findings_default_empty_and_report_shape():
    _, r = import_n8n(wf([node("T", "manualTrigger")]))
    assert r["findings"] == []
    assert set(r) >= {"mapped", "unmapped_nodes", "untranslated_expressions", "disabled_code_nodes", "findings"}


# ---------------------------------------------------------------- expressions

TRANSLATED = [
    ("$json.a", "$json.a"),
    ("$json.a.b['c']", "$json.a.b['c']"),
    ("$('Fetch').item.json.y", '$node["Fetch"].json.y'),
    ('$("Fetch").first().json.y', '$node["Fetch"].json.y'),
    ("$('Fetch').last().json.y", '$node["Fetch"].json.y'),
    ('$node["Fetch"].json.y', '$node["Fetch"].json.y'),
    ("$node['Fetch'].json.y", '$node["Fetch"].json.y'),
    ("$node.Fetch.json.y", '$node["Fetch"].json.y'),
    ("$input.item.json.z", "$json.z"),
    ("$input.first().json.z", "$json.z"),
    ("$json.items.length", "length($json.items)"),
    ("$json.a === 'x' && $json.b !== 2 || !$json.c", "$json.a == 'x' and $json.b != 2 or not $json.c"),
    ("$json.name.toLowerCase()", "lower($json.name)"),
    ("$json.name.toUpperCase().trim()", "trim(upper($json.name))"),
    ("$json.name.startsWith('A')", "startswith($json.name, 'A')"),
    ("$json.list.join(', ')", "join($json.list, ', ')"),
    ("JSON.stringify($json.obj)", "json_stringify($json.obj)"),
    ("parseInt($json.n) + 1", "int($json.n) + 1"),
    ("Math.round($json.n)", "round($json.n)"),
    ("$json.a?.b", "$json.a.b"),
    ("$vars.KEY", "$vars.KEY"),
    ("$json.a === 'it\\'s && ok'", "$json.a == 'it\\'s && ok'"),
]

UNTRANSLATABLE = [
    "$json.a ? 'x' : 'y'",
    "$('Src').all().map(i => i.json.a)",
    "new Date().toISOString()",
    "$now.toISO()",
    "Object.keys($json)",
    "`tpl ${$json.a}`",
    "$json.a.replace('x', 'y')",
    "$items('Src')",
    "DateTime.now()",
    "$json.a ?? 'd'",
]


@pytest.mark.parametrize("js,expected", TRANSLATED)
def test_expression_translation_table(js, expected):
    assert translate_expression(js) == expected


@pytest.mark.parametrize("js", UNTRANSLATABLE)
def test_untranslatable_expressions_return_none(js):
    assert translate_expression(js) is None


def test_untranslatable_is_verbatim_and_reported():
    expr = "={{ $json.a.replace('x', 'y') }} and {{ $json.ok }}"
    w, r = one([node("S", "set", {"assignments": {"assignments": [{"name": "v", "value": expr}]}}, 3.4)])
    value = by_name(w, "S")["parameters"]["fields"][0]["value"]
    assert value == "{{ $json.a.replace('x', 'y') }} and {{ $json.ok }}"
    assert r["untranslated_expressions"] == [{"node": "S", "param": "assignments.v", "expression": "{{ $json.a.replace('x', 'y') }}"}]


def test_expression_prefix_stripped_and_literals_untouched():
    w, r = one([node("S", "set", {"assignments": {"assignments": [
        {"name": "a", "value": "plain {{ not an expression }}"}, {"name": "b", "value": "=literal"}]}}, 3.4)])
    f = by_name(w, "S")["parameters"]["fields"]
    assert f == [{"name": "a", "value": "plain { { not an expression }}"}, {"name": "b", "value": "literal"}]
    assert r["untranslated_expressions"] == []


@pytest.mark.parametrize("js,data,expected", [
    ("$json.name.toLowerCase()", {"name": "ABC"}, "abc"),
    ("$json.items.length", {"items": [1, 2, 3]}, 3),
    ("$json.a === 'x' && $json.b !== 2", {"a": "x", "b": 3}, True),
    ("!$json.flag", {"flag": False}, True),
    ("parseInt($json.n) + 1", {"n": "4"}, 5),
])
def test_translated_expressions_evaluate_in_our_engine(js, data, expected):
    assert evaluate("{{ " + translate_expression(js) + " }}", data) == expected


def test_node_reference_evaluates():
    t = translate_expression("$('Fetch').item.json.y")
    out = evaluate("{{ " + t + " }}", {}, {"Fetch": {"json": {"y": 42}}})
    assert out == 42


# ---------------------------------------------------------------- route (called directly; TestClient lives in integration tests)

def test_route_returns_validated_workflow_and_report(monkeypatch):
    from src.services import check_lookups
    from src.services.workflow_checker import StaticLookups

    async def fake_lookups(session, project_id, *a, **kw):
        return StaticLookups(variables=set(), tables={}, egress_allowlist=[])

    monkeypatch.setattr(check_lookups, "build_lookups", fake_lookups)
    body = N8nImportRequest(n8n=wf([node("T", "manualTrigger"), node("X", "slack")], {"T": conn(["X"])}), name="Mine")
    out = asyncio.run(import_n8n_workflow(body, session=None))
    assert isinstance(out["report"]["findings"], list)
    assert out["workflow"]["name"] == "Mine" and len(out["workflow"]["nodes"]) == 2
    assert out["report"]["unmapped_nodes"][0]["n8n_name"] == "X"


def test_route_400_on_non_n8n_input():
    with pytest.raises(HTTPException) as e:
        asyncio.run(import_n8n_workflow(N8nImportRequest(n8n={"hello": "world"}), session=None))
    assert e.value.status_code == 400 and "n8n" in e.value.detail


def test_route_registered_before_workflow_id_routes():
    from src.routes import api_router
    paths = [getattr(r, "path", "") for r in api_router.routes]
    imp = paths.index("/api/workflows/import/n8n")
    assert all(i > imp for i, p in enumerate(paths) if p.startswith("/api/workflows/{workflow_id}"))


# ---------------------------------------------------------------- fixtures

def test_ten_real_fixtures_present():
    assert len(FIXTURES) == 10


@pytest.mark.parametrize("path", FIXTURES, ids=[os.path.basename(p)[:28] for p in FIXTURES])
def test_fixture_imports_into_valid_workflow(path):
    with open(path) as f:
        raw = json.load(f)
    w, r = import_n8n(raw)
    req = WorkflowCreateRequest(**w)
    names = [n.name for n in req.nodes]
    assert len(set(names)) == len(names)
    non_sticky = [n for n in raw["nodes"] if not n["type"].endswith("stickyNote")]
    assert len(r["mapped"]) + len(r["unmapped_nodes"]) == len(non_sticky)
    assert len(req.nodes) in (len(non_sticky), len(non_sticky) + 1)  # +1 when a Start was added
    for c in req.connections:
        assert c.source_node in names and c.target_node in names
    for u in r["unmapped_nodes"]:
        assert by_name(w, next(n["name"] for n in w["nodes"] if n["name"] == u["n8n_name"]))["disabled"] is True
    for name in r["disabled_code_nodes"]:
        assert by_name(w, name)["type"] == "Code" and by_name(w, name)["disabled"] is True
    for e in r["untranslated_expressions"]:
        assert e["node"] in names and e["expression"]


def test_index_then_property_translates_and_evaluates():
    ctx = ExpressionContext(json_data={"items": [{"name": "a"}]}, input_data=[], node_data={"Src": {"json": {"items": [{"name": "b"}]}}},
                            env={}, execution={}, item_index=0, vars={})
    assert translate_expression("$json.items[0].name") == "$json.items[0].name"
    assert translate_expression("$('Src').item.json.items[0].name") == '$node["Src"].json.items[0].name'
    assert expression_engine.resolve("{{ $json.items[0].name }}", ctx) == "a"
    assert expression_engine.resolve('{{ $node["Src"].json.items[0].name }}', ctx) == "b"


def test_http_parameter_names_are_translated():
    w, r = one([node("T", "manualTrigger"), node("H", "httpRequest", {
        "url": "https://x.test", "method": "POST", "sendBody": True, "sendHeaders": True, "sendQuery": True,
        "headerParameters": {"parameters": [{"name": "={{ $json.h }}", "value": "v"}]},
        "queryParameters": {"parameters": [{"name": "={{ $json.q }}", "value": "v"}]},
        "bodyParameters": {"parameters": [{"name": "={{ $json.mode }}", "value": "1"}]}}, 4)], {"T": conn(["H"])})
    p = by_name(w, "H")["parameters"]
    assert p["headers"][0]["name"] == "{{ $json.h }}"
    assert "{{ $json.q }}=v" in p["url"]
    assert json.loads(p["body"].replace("{{ $json.mode }}", "m")) == {"m": "1"}
    assert "={{" not in json.dumps(p) and r["untranslated_expressions"] == []


def test_http_untranslatable_name_is_reported():
    w, r = one([node("T", "manualTrigger"), node("H", "httpRequest", {
        "url": "https://x.test", "sendQuery": True,
        "queryParameters": {"parameters": [{"name": "={{ $json.a ? 1 : 2 }}", "value": "v"}]}}, 4)], {"T": conn(["H"])})
    assert [u["param"] for u in r["untranslated_expressions"]] == ["query.name"]


def test_literal_braces_are_defused_and_noted():
    w, r = one([node("S", "set", {"assignments": {"assignments": [{"name": "a", "value": "hi {{ $json.x }}"}]}}, 3.4)])
    assert by_name(w, "S")["parameters"]["fields"][0]["value"] == "hi { { $json.x }}"
    assert any("{{" in n["message"] for n in r["notes"])
