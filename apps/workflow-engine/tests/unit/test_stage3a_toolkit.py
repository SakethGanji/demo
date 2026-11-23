"""Workflow toolkit (services/workflow_toolkit.py) and the scorecard bar text."""

from __future__ import annotations

import copy

import pytest

from src.engine.node_registry import register_all_nodes
from src.services import workflow_toolkit as tk
from src.services.scorecard import format_bar

register_all_nodes()


def N(name: str, type_: str, params: dict | None = None) -> dict:
    return {"name": name, "type": type_, "parameters": params or {}}


def E(src: str, dst: str, port: str = "main") -> dict:
    return {"source_node": src, "target_node": dst, "source_output": port, "target_input": "main"}


def base() -> dict:
    return {
        "nodes": [N("Start", "Start"), N("Fetch", "Set", {"mode": "manual", "fields": [{"name": "a", "value": "1"}]}),
                  N("Use", "Set", {"mode": "manual", "fields": [
                      {"name": "x", "value": '{{ $node["Fetch"].json.a }}'}, {"name": "y", "value": "{{ $node.Fetch.json.a }}"},
                      {"name": "z", "value": "{{ $('Fetch').first().json.a }}"}, {"name": "w", "value": "{{ $node.Fetcher.json.a }}"}]})],
        "connections": [E("Start", "Fetch"), E("Fetch", "Use")],
        "tests": [{"id": "t_00000001", "name": "t", "target": {"kind": "node", "node": "Fetch"}, "input": {"items": [{}]},
                   "mocks": {"Fetch": {"items": [{"a": 1}]}}, "expect": [{"node": "Fetch", "op": "count", "value": 1}],
                   "category": "normal", "covers": ["Fetch"]},
                  {"id": "t_00000002", "name": "locked", "target": {"kind": "workflow"}, "input": {"items": []}, "mocks": {},
                   "expect": [], "category": "normal", "status": "approved", "locked": True}],
        "test_defaults": {"mocks": {"Fetch": {"items": [{"a": 2}]}}},
    }


def test_add_node_names_and_validates():
    d = base()
    out = tk.add_node(d, {"type": "Set"})
    assert out["nodes"][-1]["name"] == "Set 1" and len(d["nodes"]) == 3
    assert tk.add_node(out, {"type": "Set"})["nodes"][-1]["name"] == "Set 2"
    with pytest.raises(tk.ToolkitError, match="already exists"):
        tk.add_node(d, {"name": "Use", "type": "Set"})
    with pytest.raises(tk.ToolkitError, match="Unknown node type"):
        tk.add_node(d, {"type": "Nope"})


def test_remove_node_drops_connections_and_default_mock():
    out = tk.remove_node(base(), "Fetch")
    assert [n["name"] for n in out["nodes"]] == ["Start", "Use"] and out["connections"] == []
    assert "Fetch" not in out["test_defaults"]["mocks"]
    with pytest.raises(tk.ToolkitError):
        tk.remove_node(base(), "Ghost")


def test_set_parameter_nested_paths():
    out = tk.set_parameter(base(), "Fetch", "fields[0].value", "9")
    assert out["nodes"][1]["parameters"]["fields"][0]["value"] == "9"
    out = tk.set_parameter(out, "Fetch", "options.deep.flag", True)
    assert out["nodes"][1]["parameters"]["options"] == {"deep": {"flag": True}}
    out = tk.set_parameter(out, "Fetch", "fields[1]", {"name": "b", "value": "2"})
    assert len(out["nodes"][1]["parameters"]["fields"]) == 2
    with pytest.raises(tk.ToolkitError):
        tk.set_parameter(out, "Fetch", "fields[7].value", 1)


def test_connect_and_disconnect():
    d = base()
    out = tk.connect(d, "Start", "Use")
    assert len(out["connections"]) == 3 and len(d["connections"]) == 2
    with pytest.raises(tk.ToolkitError, match="already connected"):
        tk.connect(out, "Start", "Use")
    with pytest.raises(tk.ToolkitError):
        tk.connect(d, "Start", "Ghost")
    assert len(tk.disconnect(out, "Start", "Use")["connections"]) == 2
    with pytest.raises(tk.ToolkitError, match="not connected"):
        tk.disconnect(d, "Start", "Use")


def test_rename_rewrites_refs_connections_tests_and_mocks():
    d = base()
    d["pinned"] = {"Fetch": [{"json": {"a": 1}}]}
    before = copy.deepcopy(d)
    out = tk.rename_node(d, "Fetch", "Load")
    assert d == before
    assert [n["name"] for n in out["nodes"]] == ["Start", "Load", "Use"]
    assert {(c["source_node"], c["target_node"]) for c in out["connections"]} == {("Start", "Load"), ("Load", "Use")}
    vals = [f["value"] for f in out["nodes"][2]["parameters"]["fields"]]
    assert vals == ['{{ $node["Load"].json.a }}', "{{ $node.Load.json.a }}", "{{ $('Load').first().json.a }}", "{{ $node.Fetcher.json.a }}"]
    t = out["tests"][0]
    assert t["target"]["node"] == "Load" and "Load" in t["mocks"] and t["expect"][0]["node"] == "Load" and t["covers"] == ["Load"]
    assert "Load" in out["test_defaults"]["mocks"] and "Fetch" not in out["test_defaults"]["mocks"]
    assert "Load" in out["pinned"]


def test_rename_refuses_clashes_and_empty():
    with pytest.raises(tk.ToolkitError, match="already exists"):
        tk.rename_node(base(), "Fetch", "Use")
    with pytest.raises(tk.ToolkitError):
        tk.rename_node(base(), "Fetch", " ")
    assert tk.rename_node(base(), "Fetch", "Fetch") == base()


def test_add_test_joins_as_draft_with_an_id():
    t = {"name": "new", "target": {"kind": "workflow"}, "input": {"items": []}, "expect": [], "category": "normal"}
    out = tk.add_test(base(), t)
    new = out["tests"][-1]
    assert new["id"].startswith("t_") and len(new["id"]) == 10 and new["status"] == "draft" and new["locked"] is False
    with pytest.raises(tk.ToolkitError, match="draft"):
        tk.add_test(base(), {**t, "status": "approved"})
    with pytest.raises(tk.ToolkitError, match="already exists"):
        tk.add_test(out, {**t, "id": new["id"]})
    with pytest.raises(tk.ToolkitError, match="Invalid test"):
        tk.add_test(base(), {"name": "bad", "category": "bogus"})


def test_update_test_refuses_locked_and_approval_fields():
    out = tk.update_test(base(), "t_00000001", {"name": "renamed"})
    assert out["tests"][0]["name"] == "renamed"
    with pytest.raises(tk.ToolkitError, match="locked"):
        tk.update_test(base(), "t_00000002", {"name": "x"})
    with pytest.raises(tk.ToolkitError, match="approve / unlock"):
        tk.update_test(base(), "t_00000001", {"status": "approved"})
    with pytest.raises(tk.ToolkitError, match="No test"):
        tk.update_test(base(), "t_ffffffff", {})


def test_apply_ops_reports_the_failing_op():
    out = tk.apply_ops(base(), [{"op": "add_node", "args": {"name": "N2", "type": "Set"}}, {"op": "connect", "args": {"source": "Use", "target": "N2"}}])
    assert out["nodes"][-1]["name"] == "N2" and out["connections"][-1]["target_node"] == "N2"
    with pytest.raises(tk.OpError) as e:
        tk.apply_ops(base(), [{"op": "connect", "args": {"source": "Start", "target": "Use"}}, {"op": "remove_node", "args": {"name": "Ghost"}}])
    assert e.value.detail() == {"op_index": 1, "op": "remove_node", "message": "No node named 'Ghost'"}
    with pytest.raises(tk.OpError) as e:
        tk.apply_ops(base(), [{"op": "fly", "args": {}}])
    assert e.value.op_index == 0 and "Unknown op" in e.value.message
    with pytest.raises(tk.OpError, match="Missing argument"):
        tk.apply_ops(base(), [{"op": "rename_node", "args": {"old": "Fetch"}}])


def test_bar_text_singular_and_plural():
    s = {"passed": 14, "total": 14}
    assert format_bar(s, 1, {"caught": 19, "total": 20}) == "14/14 passing · 1 gap · 19/20 planted bugs caught"
    assert format_bar(s, 0, {"caught": 1, "total": 1}) == "14/14 passing · 0 gaps · 1/1 planted bug caught"
    assert format_bar({"passed": 0, "total": 0}, 2, None) == "0/0 passing · 2 gaps"
