"""Recipes, boundary extraction and data categories (CONTRACTS-STAGE3A section 7)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.engine.data_categories import ABSENT, is_absent, values_for
from src.engine.effects import NODE_EFFECTS
from src.engine.node_registry import node_registry, register_all_nodes
from src.engine.test_recipes import (
    SuggestContext, boundary_values, conditions_of, parse_expression_condition, recipe_for, suggest_for_workflow,
)
from src.services.workflow_checker import StaticLookups, check_workflow

register_all_nodes()
FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "workflows" / "refund_triage.json"


def refund():
    return json.loads(FIXTURE.read_text().replace("__REFUND_API_URL__", "http://x.test/a"))


def drafts(definition, node=None):
    shapes = check_workflow(definition, lookups=StaticLookups())["shapes_by_node"]
    return suggest_for_workflow(definition, shapes, SuggestContext(definition), node)


def test_every_registered_type_has_a_recipe():
    for t in node_registry.list():
        r = recipe_for(t)
        assert isinstance(r.categories, list) and callable(r.suggest)
    assert set(node_registry.list()) <= set(NODE_EFFECTS)


def test_ai_nodes_have_an_empty_recipe_and_outside_nodes_the_ext_columns():
    for t in ("LLMChat", "AIAgent"):
        assert recipe_for(t).categories == [] and recipe_for(t).checks == []
    for t, e in NODE_EFFECTS.items():
        if e in ("network", "database", "subworkflow"):
            assert {"ext_success", "ext_error", "ext_timeout", "ext_empty"} <= set(recipe_for(t).categories)
    assert "boundary" in recipe_for("If").categories and "boundary" not in recipe_for("Set").categories


@pytest.mark.parametrize("expr,op,value", [
    ("{{ $json.amount > 500 }}", "gt", 500), ("{{ $json.amount >= 500 }}", "gte", 500),
    ("{{ $json.amount < 500 }}", "lt", 500), ("{{ $json.amount <= 500 }}", "lte", 500),
    ("{{ $json.amount == 500 }}", "equals", 500), ("{{ $json.amount != 500 }}", "notEquals", 500),
    ("{{ 500 < $json.amount }}", "gt", 500), ("{{ $json.status == 'ok' }}", "equals", "ok"),
])
def test_expression_conditions_parse(expr, op, value):
    c = parse_expression_condition(expr)
    assert c and c.op == op and c.value == value and c.path in (["amount"], ["status"])


def test_compound_expressions_are_not_parsed():
    assert parse_expression_condition("{{ $json.a > 1 && $json.b < 2 }}") is None


@pytest.mark.parametrize("op,ftype,expected", [
    ("gt", "integer", [500, 501]), ("gt", "number", [500, 500.01]), ("gt", "unknown", [500, 501, 500.01]),
    ("gte", "integer", [500, 499]), ("lt", "integer", [500, 499]), ("lt", "number", [500, 499.99]),
    ("lte", "integer", [500, 501]), ("equals", "integer", [500, 501]), ("notEquals", "number", [500, 500.01]),
])
def test_boundary_values_per_operator(op, ftype, expected):
    from src.engine.test_recipes import Cond

    assert boundary_values(Cond(["amount"], op, 500), ftype) == expected


def test_structured_conditions_of_if_filter_switch():
    c = conditions_of({"type": "If", "parameters": {"field": "score", "operation": "gte", "value": "70"}})
    assert (c[0].path, c[0].op, c[0].value) == (["score"], "gte", "70")
    f = conditions_of({"type": "Filter", "parameters": {"mode": "expression", "condition": "{{ $json.n < 3 }}"}})
    assert f[0].op == "lt" and f[0].expr
    sw = conditions_of({"type": "Switch", "parameters": {"rules": [
        {"output": 0, "field": "a", "operation": "equals", "value": "x"},
        {"output": 1, "field": "{{ $json.b }}", "operation": "gt", "value": "5"}]}})
    assert [(x.path, x.rule) for x in sw] == [(["a"], 0), (["b"], 1)]
    assert conditions_of({"type": "If", "parameters": {"condition": "{{ complicated(x) }}"}}) == []


def test_refund_boundaries_trace_through_set_to_the_webhook_body():
    ds = drafts(refund(), "Amount over 500")
    by = {d["input"]["items"][0]["body"]["amount"]: d for d in ds if d["category"] == "boundary"}
    assert {500, 501, 500.01} <= set(by)
    assert by[501]["target"] == {"kind": "workflow"} and by[501]["covers"] == ["Amount over 500"]
    assert by[500]["expect"][0] == {"node": "Amount over 500", "port": "false", "op": "count", "value": 1}
    assert any(d["category"] == "normal" for d in ds)


def test_drafts_are_deterministic_and_have_stable_content_ids():
    a, b = drafts(refund()), drafts(refund())
    assert [d["id"] for d in a] == [d["id"] for d in b] and len({d["id"] for d in a}) == len(a)
    assert all(d["id"].startswith("t_") and len(d["id"]) == 10 and d["source"] == "suggested" and d["status"] == "draft" for d in a)


def test_switch_drafts_use_the_nodes_own_rule_order():
    d = {"nodes": [
        {"name": "Hook", "type": "Webhook", "parameters": {}},
        {"name": "Route", "type": "Switch", "parameters": {"numberOfOutputs": 2, "rules": [
            {"output": 0, "field": "kind", "operation": "equals", "value": "a"},
            {"output": 1, "field": "kind", "operation": "contains", "value": "b"}]}},
    ], "connections": [{"source_node": "Hook", "target_node": "Route", "source_output": "main", "target_input": "main"}]}
    ports = {x["expect"][0]["port"] for x in drafts(d, "Route")}
    assert {"output0", "output1", "fallback"} <= ports


def test_outside_node_without_spec_gets_four_drafts():
    d = refund()
    ds = drafts(d, "Risk API")
    assert sorted(x["category"] for x in ds) == ["ext_empty", "ext_error", "ext_success", "ext_timeout"]
    assert all(x["mocks"] for x in ds)


def test_ai_node_gets_no_drafts():
    d = {"nodes": [{"name": "Chat", "type": "LLMChat", "parameters": {}}], "connections": []}
    assert drafts(d) == []


def test_values_for_each_type():
    n = values_for({"type": "integer", "minimum": 1, "maximum": 10})
    assert n["normal"] == [1] and {1, 0, 10, 11} <= set(n["boundary"]) and any(is_absent(v) for v in n["missing"]) and None in n["missing"]
    assert "abc" in n["wrong_type"] and 2 ** 53 in n["unusual"]
    f = values_for({"type": "number", "minimum": 0.5})
    assert 0.5 in f["boundary"] and 0.49 in f["boundary"]
    s = values_for({"type": "string", "maxLength": 5, "enum": ["a", "b"]})
    assert s["normal"] == ["a"] and "xxxxx" in s["boundary"] and "xxxxxx" in s["boundary"] and "" in s["missing"]
    assert any("日本" in v for v in s["unusual"]) and any("😀" in v for v in s["unusual"]) and 12345 in s["wrong_type"]
    assert values_for({"type": "boolean"})["normal"] == [True, False]
    a = values_for({"type": "array", "items": {"type": "integer"}})
    assert [] in a["boundary"] and [1, 1] in a["unusual"]
    assert values_for({"type": "object", "properties": {"x": {"type": "integer"}}})["normal"] == [{"x": 1}]
    assert values_for({"type": "unknown"})["wrong_type"] == []
    assert values_for({"type": "string"}) == values_for({"type": "string"}) and ABSENT is values_for({"type": "string"})["missing"][0]
