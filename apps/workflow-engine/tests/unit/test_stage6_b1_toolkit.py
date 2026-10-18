"""Stage 6 B1: the four additive toolkit ops (set_parameters, set_node_field, set_test_default_mock, set_interface)."""

from __future__ import annotations

import copy

import pytest

from src.engine.node_registry import register_all_nodes
from src.services import workflow_toolkit as tk

register_all_nodes()


def base() -> dict:
    return {
        "nodes": [
            {"name": "Start", "type": "Start", "parameters": {}, "position": {"x": 1, "y": 2}},
            {"name": "Call", "type": "HttpRequest", "parameters": {"url": "http://a.test", "method": "GET"}, "position": {"x": 3, "y": 4}},
        ],
        "connections": [{"source_node": "Start", "target_node": "Call", "source_output": "main", "target_input": "main"}],
        "tests": [], "test_defaults": {},
    }


def op(_name: str, **args) -> dict:
    return {"op": _name, "args": args}


def test_all_four_ops_are_registered_and_the_old_ones_remain():
    assert {"set_parameters", "set_node_field", "set_test_default_mock", "set_interface"} <= set(tk._OPS)
    assert {"add_node", "remove_node", "set_parameter", "connect", "disconnect", "rename_node", "add_test", "update_test"} <= set(tk._OPS)


def test_set_parameters_replaces_the_whole_object_and_never_mutates_the_input():
    d = base()
    before = copy.deepcopy(d)
    out = tk.apply_ops(d, [op("set_parameters", node="Call", parameters={"url": "http://b.test"})])
    assert out["nodes"][1]["parameters"] == {"url": "http://b.test"}      # method is gone: replaced, not merged
    assert d == before
    assert out["nodes"][1]["position"] == {"x": 3, "y": 4}


def test_set_parameters_copies_its_value_so_later_edits_do_not_alias():
    params = {"url": "http://b.test", "headers": {"a": "1"}}
    out = tk.apply_ops(base(), [op("set_parameters", node="Call", parameters=params)])
    params["headers"]["a"] = "changed"
    assert out["nodes"][1]["parameters"]["headers"] == {"a": "1"}


@pytest.mark.parametrize("bad", [None, [], "x", 3])
def test_set_parameters_needs_an_object(bad):
    with pytest.raises(tk.OpError) as e:
        tk.apply_ops(base(), [op("set_parameters", node="Call", parameters=bad)])
    assert e.value.op == "set_parameters" and "object" in e.value.message


def test_set_parameters_unknown_node_and_missing_args_point_at_the_op():
    with pytest.raises(tk.OpError) as e:
        tk.apply_ops(base(), [op("set_parameter", node="Start", path="a", value=1), op("set_parameters", node="Nope", parameters={})])
    assert e.value.op_index == 1 and "No node named 'Nope'" in e.value.message
    with pytest.raises(tk.OpError) as e2:
        tk.apply_ops(base(), [op("set_parameters", node="Call")])
    assert "Missing argument" in e2.value.message


def test_set_node_field_label_disabled_on_error():
    out = tk.apply_ops(base(), [
        op("set_node_field", node="Call", field="label", value="  Ask risk  "),
        op("set_node_field", node="Call", field="disabled", value=True),
        op("set_node_field", node="Call", field="on_error", value="continue"),
    ])
    n = out["nodes"][1]
    assert n["label"] == "Ask risk" and n["disabled"] is True and n["on_error"] == "continue"
    cleared = tk.apply_ops(out, [op("set_node_field", node="Call", field="label", value=None)])
    assert "label" not in cleared["nodes"][1]


def test_set_node_field_on_error_drops_the_legacy_flag_that_would_contradict_it():
    d = base()
    d["nodes"][1]["continue_on_fail"] = True
    out = tk.apply_ops(d, [op("set_node_field", node="Call", field="on_error", value="stop")])
    assert "continue_on_fail" not in out["nodes"][1] and out["nodes"][1]["on_error"] == "stop"


@pytest.mark.parametrize("field,value", [
    ("position", {"x": 1, "y": 1}), ("type", "Set"), ("name", "X"), ("parameters", {}), ("bogus", 1),
    ("disabled", "yes"), ("disabled", 1), ("on_error", "ignore"), ("on_error", None), ("label", 5),
])
def test_set_node_field_refuses_other_fields_and_bad_values(field, value):
    with pytest.raises(tk.OpError):
        tk.apply_ops(base(), [op("set_node_field", node="Call", field=field, value=value)])


def test_set_test_default_mock_creates_and_replaces():
    mock = {"http": {"status": 200, "body": {"ok": True}}}
    out = tk.apply_ops(base(), [op("set_test_default_mock", node="Call", mock=mock)])
    assert out["test_defaults"]["mocks"]["Call"] == mock
    mock["http"]["status"] = 500                                    # no aliasing
    assert out["test_defaults"]["mocks"]["Call"]["http"]["status"] == 200
    again = tk.apply_ops(out, [op("set_test_default_mock", node="Call", mock={"items": [{"a": 1}]})])
    assert again["test_defaults"]["mocks"] == {"Call": {"items": [{"a": 1}]}}


def test_set_test_default_mock_keeps_other_defaults_and_validates():
    d = base()
    d["test_defaults"] = {"now": "2026-01-01T00:00:00Z", "seed": 3, "mocks": {"Start": {"items": []}}}
    out = tk.apply_ops(d, [op("set_test_default_mock", node="Call", mock={"error": {"message": "boom"}})])
    assert out["test_defaults"]["seed"] == 3 and set(out["test_defaults"]["mocks"]) == {"Start", "Call"}
    for bad in (None, {}, {"nonsense": 1}, "text", [1]):
        with pytest.raises(tk.OpError):
            tk.apply_ops(base(), [op("set_test_default_mock", node="Call", mock=bad)])
    with pytest.raises(tk.OpError):
        tk.apply_ops(base(), [op("set_test_default_mock", node="Nope", mock={"items": []})])


def test_set_test_default_mock_survives_a_null_test_defaults():
    d = base()
    d["test_defaults"] = None
    out = tk.apply_ops(d, [op("set_test_default_mock", node="Call", mock={"items": []})])
    assert out["test_defaults"]["mocks"]["Call"] == {"items": []}


def test_remove_node_still_drops_the_default_mock_that_set_test_default_mock_added():
    d = tk.apply_ops(base(), [op("set_test_default_mock", node="Call", mock={"items": []})])
    out = tk.apply_ops(d, [op("remove_node", name="Call")])
    assert "Call" not in out["test_defaults"]["mocks"]


def test_rename_follows_a_default_mock_set_by_the_new_op():
    d = tk.apply_ops(base(), [op("set_test_default_mock", node="Call", mock={"items": []})])
    out = tk.apply_ops(d, [op("rename_node", old="Call", new="Ask")])
    assert set(out["test_defaults"]["mocks"]) == {"Ask"}


def test_set_interface_validates_and_sets_and_removes():
    iface = {"slug": "refund-request", "kind": "form", "title": "Request a refund", "screens": {}}
    out = tk.apply_ops(base(), [op("set_interface", interface=iface)])
    assert out["interface"]["slug"] == "refund-request" and out["interface"]["kind"] == "form"
    gone = tk.apply_ops(out, [op("set_interface", interface=None)])
    assert "interface" not in gone


@pytest.mark.parametrize("bad", ["x", [], 5, {"slug": "A B"}, {"slug": "x"}, {"slug": "ok-slug", "kind": "nonsense"}])
def test_set_interface_refuses_invalid_interfaces(bad):
    with pytest.raises(tk.OpError) as e:
        tk.apply_ops(base(), [op("set_interface", interface=bad)])
    assert "interface" in e.value.message.lower()


def test_ops_apply_in_order_and_the_first_refusal_stops_the_batch():
    with pytest.raises(tk.OpError) as e:
        tk.apply_ops(base(), [
            op("set_node_field", node="Call", field="disabled", value=True),
            op("set_node_field", node="Call", field="on_error", value="nope"),
            op("set_parameters", node="Call", parameters={}),
        ])
    assert e.value.op_index == 1


def test_unknown_op_message_lists_the_new_ops():
    with pytest.raises(tk.OpError) as e:
        tk.apply_ops(base(), [op("explode")])
    assert "set_interface" in e.value.message
