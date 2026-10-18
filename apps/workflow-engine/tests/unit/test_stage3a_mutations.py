"""Planted-bug operators (services/mutations.py): each operator, determinism, the cap of 30."""

from __future__ import annotations

import copy
import json
from pathlib import Path

from src.services.mutations import MAX_MUTANTS, generate_mutants

REFUND = json.loads((Path(__file__).resolve().parents[1] / "fixtures" / "workflows" / "refund_triage.json").read_text())


def N(name: str, type_: str, params: dict | None = None, **kw) -> dict:
    return {"name": name, "type": type_, "parameters": params or {}, **kw}


def E(src: str, dst: str, port: str = "main") -> dict:
    return {"source_node": src, "target_node": dst, "source_output": port, "target_input": "main"}


def D(nodes: list[dict], conns: list[dict] | None = None) -> dict:
    return {"nodes": nodes, "connections": conns or [], "tests": [], "test_defaults": {}}


def by_op(mutants: list[dict], op: str) -> list[dict]:
    return [m for m in mutants if m["operator"] == op]


def node(defn: dict, name: str) -> dict:
    return next(n for n in defn["nodes"] if n["name"] == name)


def test_flip_compare_on_an_if_expression():
    d = D([N("Start", "Start"), N("Big", "If", {"condition": "{{ $json.amount > 500 }}"})])
    ms = by_op(generate_mutants(d), "flip_compare")
    assert len(ms) == 1 and ms[0]["node"] == "Big"
    assert node(ms[0]["definition"], "Big")["parameters"]["condition"] == "{{ $json.amount >= 500 }}"
    assert node(d, "Big")["parameters"]["condition"] == "{{ $json.amount > 500 }}"  # the original is untouched


def test_flip_compare_each_comparison_and_equality():
    d = D([N("A", "If", {"condition": "{{ $json.a <= 1 and $json.b == 2 }}"})])
    got = sorted(node(m["definition"], "A")["parameters"]["condition"] for m in by_op(generate_mutants(d), "flip_compare"))
    assert got == ["{{ $json.a < 1 and $json.b == 2 }}", "{{ $json.a <= 1 and $json.b != 2 }}"]


def test_flip_compare_structured_if_filter_and_switch_rules():
    d = D([
        N("If1", "If", {"field": "n", "operation": "gt", "value": "5"}),
        N("Flt", "Filter", {"mode": "rules", "field": "n", "operation": "equals", "value": "5"}),
        N("Sw", "Switch", {"mode": "rules", "numberOfOutputs": 2,
                           "rules": [{"output": 0, "field": "n", "operation": "lte", "value": "1"}]}),
        N("Contains", "If", {"field": "n", "operation": "contains", "value": "x"}),
    ])
    ms = by_op(generate_mutants(d), "flip_compare")
    assert {m["node"] for m in ms} == {"If1", "Flt", "Sw"}
    assert node(next(m for m in ms if m["node"] == "If1")["definition"], "If1")["parameters"]["operation"] == "gte"
    assert node(next(m for m in ms if m["node"] == "Flt")["definition"], "Flt")["parameters"]["operation"] == "notEquals"
    assert node(next(m for m in ms if m["node"] == "Sw")["definition"], "Sw")["parameters"]["rules"][0]["operation"] == "lt"


def test_flip_compare_leaves_arrows_and_filter_expression_mode_alone():
    d = D([N("A", "If", {"condition": "{{ [1,2].map(x => x) }}"})])
    assert by_op(generate_mutants(d), "flip_compare") == []


def test_change_set_value_number_string_bool():
    d = D([N("S", "Set", {"mode": "manual", "fields": [
        {"name": "n", "value": 4}, {"name": "s", "value": "hi"}, {"name": "b", "value": True}, {"name": "o", "value": None}]})])
    ms = by_op(generate_mutants(d), "change_set_value")
    assert len(ms) == 3
    vals = sorted(str(m["definition"]["nodes"][0]["parameters"]["fields"][i]["value"])
                  for m in ms for i in range(4) if m["definition"]["nodes"][0]["parameters"]["fields"][i] != d["nodes"][0]["parameters"]["fields"][i])
    assert vals == ["5", "False", "hi_x"]


def test_swap_branches_swaps_true_and_false():
    d = D([N("Start", "Start"), N("If", "If"), N("T", "Set"), N("F", "Set")],
          [E("Start", "If"), E("If", "T", "true"), E("If", "F", "false")])
    ms = by_op(generate_mutants(d), "swap_branches")
    assert len(ms) == 1
    conns = {(c["target_node"], c["source_output"]) for c in ms[0]["definition"]["connections"] if c["source_node"] == "If"}
    assert conns == {("T", "false"), ("F", "true")}


def test_drop_node_disables_non_triggers_only():
    d = D([N("Start", "Start"), N("A", "Set"), N("B", "Set", disabled=True), N("Note", "StickyNote")])
    ms = by_op(generate_mutants(d), "drop_node")
    assert [m["node"] for m in ms] == ["A"]
    assert node(ms[0]["definition"], "A")["disabled"] is True


def test_break_expression_replaces_a_json_reference_with_none():
    d = D([N("Start", "Start"), N("S", "Set", {"mode": "manual", "fields": [{"name": "x", "value": "{{ $json.body.amount + 1 }}"}]}),
           N("Plain", "Set", {"mode": "manual", "fields": [{"name": "y", "value": "$json.nothing"}]})])
    ms = by_op(generate_mutants(d), "break_expression")
    assert len(ms) == 1 and ms[0]["node"] == "S"
    assert node(ms[0]["definition"], "S")["parameters"]["fields"][0]["value"] == "{{ None + 1 }}"


def test_each_mutant_has_the_contract_fields_and_unique_ids():
    ms = generate_mutants(REFUND)
    assert ms and all({"id", "operator", "node", "description", "definition"} <= set(m) for m in ms)
    assert len({m["id"] for m in ms}) == len(ms)
    assert {"flip_compare", "swap_branches", "drop_node", "break_expression"} <= {m["operator"] for m in ms}
    assert all(m["definition"] != REFUND for m in ms)


def test_selection_is_deterministic_and_capped_at_30():
    nodes = [N(f"S{i}", "Set", {"mode": "manual", "fields": [{"name": "a", "value": i}, {"name": "b", "value": "x"}]}) for i in range(40)]
    d = D(nodes)
    a, b = generate_mutants(d, seed=3), generate_mutants(d, seed=3)
    assert len(a) == MAX_MUTANTS == 30
    assert [m["id"] for m in a] == [m["id"] for m in b]
    assert [m["id"] for m in generate_mutants(d, seed=4)] != [m["id"] for m in a]
    assert len(generate_mutants(d, limit=5)) == 5
    before = copy.deepcopy(d)
    generate_mutants(d)
    assert d == before


def test_a_workflow_without_anything_to_break_has_no_mutants():
    assert generate_mutants(D([N("Start", "Start")])) == []
