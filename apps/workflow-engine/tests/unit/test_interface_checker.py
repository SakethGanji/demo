"""Checker codes WF013-WF019 (DESIGN-STAGE5-UIS section 4.5), the FormTrigger / ChatInput nodes and the graph helpers."""

from __future__ import annotations

import copy
import json
from datetime import datetime
from pathlib import Path

import pytest

from src.engine import graph
from src.engine.node_registry import node_registry, register_all_nodes
from src.engine.types import NodeData, NodeDefinition
from src.services.workflow_checker import StaticLookups, check_workflow

register_all_nodes()
pytestmark = pytest.mark.anyio
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "workflows"


def N(name, type_, params=None, **kw):
    return {"name": name, "type": type_, "parameters": params or {}, **kw}


def C(a, b, out="main"):
    return {"source_node": a, "target_node": b, "source_output": out, "target_input": "main"}


def check(definition):
    return check_workflow(definition, lookups=StaticLookups())["findings"]


def only(findings, code):
    return [f for f in findings if f["code"] == code]


FORM = {"type": "object", "properties": {"amount": {"type": "number"}}, "required": ["amount"]}


def approval_flow(approval_params=None, ports=("approved", "rejected", "timed_out"), upstream=()):
    nodes = [N("Form", "FormTrigger", {"input_schema": FORM}), *upstream,
             N("Review", "Approval", {"title": "t", "due_in": "3h", **(approval_params or {})}),
             N("A", "Set"), N("R", "Set"), N("T", "Set")]
    conns = [C("Form", "Review")]
    for port, target in zip(("approved", "rejected", "timed_out"), "ART"):
        if port in ports:
            conns.append(C("Review", target, port))
    return {"nodes": nodes, "connections": conns}


# ── WF013 ────────────────────────────────────────────────────────────────────────────────────


def test_wf013_unrenderable_form_fields():
    schema = {"type": "object", "properties": {"name": {"type": "string"}, "tags": {"type": "array"}, "addr": {"type": "object", "properties": {}}}}
    found = only(check({"nodes": [N("Form", "FormTrigger", {"input_schema": schema})], "connections": []}), "WF013")
    assert [(f["severity"], f["path"], f["node"]) for f in found] == [("warning", "tags", "Form"), ("warning", "addr", "Form")]


def test_wf013_quiet_for_a_plain_form():
    assert only(check({"nodes": [N("Form", "FormTrigger", {"input_schema": FORM})], "connections": []}), "WF013") == []


# ── WF014 / WF015 / WF016 ────────────────────────────────────────────────────────────────────


def test_a_complete_approval_has_no_findings():
    found = check(approval_flow())
    assert [f for f in found if f["code"] in ("WF014", "WF015", "WF016")] == []


def test_wf014_approval_inside_a_loop():
    flow = approval_flow(upstream=[N("Each", "Loop")])
    flow["connections"] = [C("Form", "Each"), C("Each", "Review"), C("Review", "A", "approved"), C("Review", "R", "rejected"), C("Review", "T", "timed_out")]
    assert [f["node"] for f in only(check(flow), "WF014")] == ["Review"]


def test_wf015_unconnected_rejected_and_timed_out():
    found = only(check(approval_flow(ports=("approved",))), "WF015")
    assert sorted(f["path"] for f in found) == ["rejected", "timed_out"] and all(f["severity"] == "warning" for f in found)


def test_wf015_timed_out_only_matters_with_a_deadline_and_the_default_policy():
    no_due = only(check(approval_flow({"due_in": ""}, ports=("approved", "rejected"))), "WF015")
    assert no_due == []
    approves = only(check(approval_flow({"on_timeout": "approve"}, ports=("approved", "rejected"))), "WF015")
    assert approves == []


def test_wf015_is_quiet_when_nothing_is_connected_yet():
    assert only(check(approval_flow(ports=())), "WF015") == []


def test_wf016_four_eyes_is_info():
    found = only(check(approval_flow({"four_eyes": True})), "WF016")
    assert [(f["severity"], f["param"]) for f in found] == [("info", "four_eyes")]
    assert only(check(approval_flow({"four_eyes": False})), "WF016") == []


def test_disabled_approvals_are_not_checked():
    flow = approval_flow({"four_eyes": True}, ports=("approved",))
    next(n for n in flow["nodes"] if n["name"] == "Review")["disabled"] = True
    assert [f for f in check(flow) if f["code"] in ("WF014", "WF015", "WF016")] == []


# ── WF017-WF019 through the checker ──────────────────────────────────────────────────────────


def test_the_refund_fixture_is_clean_apart_from_the_unregistered_approval_type():
    d = json.loads((FIXTURES / "refund_triage_ui.json").read_text())
    found = check({"nodes": d["nodes"], "connections": d["connections"], "interface": d["interface"]})
    assert [f for f in found if f["code"] in ("WF013", "WF014", "WF015", "WF016", "WF017", "WF018", "WF019")] == []


def test_wf017_blocks_through_the_checker():
    d = json.loads((FIXTURES / "refund_triage_ui.json").read_text())
    d["interface"]["trigger_node"] = "Classify"
    found = check({"nodes": d["nodes"], "connections": d["connections"], "interface": d["interface"]})
    assert [(f["code"], f["severity"]) for f in found if f["code"] == "WF017"] == [("WF017", "error")]


def test_the_chat_fixture_is_clean():
    d = json.loads((FIXTURES / "chat_support_ui.json").read_text())
    found = check({"nodes": d["nodes"], "connections": d["connections"], "interface": d["interface"]})
    assert [f for f in found if f["code"].startswith("WF01")] == []


# ── nodes and graph ──────────────────────────────────────────────────────────────────────────


def test_triggers_include_the_ui_nodes():
    assert {"FormTrigger", "ChatInput"} <= graph.TRIGGER_NODE_TYPES
    assert node_registry.has("FormTrigger")


def _def(type_, **params):
    return NodeDefinition(name="n", type=type_, parameters=params)


async def test_form_trigger_passes_the_submission_through():
    node = node_registry.get("FormTrigger")
    item = {"amount": 5, "_submission": {"id": "sub_1", "submitted_at": "t", "mode": "live"}}
    res = await node.execute(None, _def("FormTrigger"), [NodeData(json=item)])
    assert res.outputs["main"][0].json == item


async def test_form_trigger_unwraps_a_webhook_style_manual_run():
    node = node_registry.get("FormTrigger")
    wrapped = {"body": {"amount": 5}, "headers": {}, "query": {}, "method": "POST", "triggeredAt": "x"}
    out = (await node.execute(None, _def("FormTrigger"), [NodeData(json=wrapped)])).outputs["main"][0].json
    assert out["amount"] == 5 and out["_submission"]["mode"] == "manual" and "body" not in out


async def test_form_trigger_without_input_still_emits_one_item():
    out = (await node_registry.get("FormTrigger").execute(None, _def("FormTrigger"), [])).outputs["main"]
    assert len(out) == 1 and out[0].json["_submission"]["id"] is None


def test_form_trigger_contract_is_the_schema_plus_an_open_submission():
    node = node_registry.get("FormTrigger")
    shape = node.contract({"input_schema": FORM}, {"main": {"type": "unknown"}})["outputs"]["main"]
    assert set(shape["properties"]) == {"amount", "_submission"} and shape["required"] == ["amount", "_submission"]
    assert shape["properties"]["_submission"]["type"] == "object" and "properties" not in shape["properties"]["_submission"]
    empty = node.contract({}, {"main": {"type": "unknown"}})["outputs"]["main"]
    assert set(empty["properties"]) == {"_submission"}


async def test_chat_input_gains_session_and_history():
    node = node_registry.get("ChatInput")
    sent = {"message": "hi", "timestamp": "t", "session_id": "chs_1", "history": [{"role": "user", "content": "a", "at": "x"}], "user_label": "Dana"}
    out = (await node.execute(None, _def("ChatInput"), [NodeData(json=sent)])).outputs["main"][0].json
    assert out == sent
    bare = (await node.execute(None, _def("ChatInput"), [NodeData(json={"message": "hi"})])).outputs["main"][0].json
    assert bare["session_id"] == "" and bare["history"] == [] and "timestamp" in bare
    empty = (await node.execute(None, _def("ChatInput"), [])).outputs["main"][0].json
    assert empty["history"] == [] and empty["message"] == ""


def test_chat_input_contract_lists_history_and_session_as_optional():
    shape = node_registry.get("ChatInput").contract({}, {"main": {"type": "unknown"}})["outputs"]["main"]
    assert {"message", "timestamp", "session_id", "history", "user_label"} == set(shape["properties"])
    assert shape["required"] == ["message", "timestamp"]
    assert shape["properties"]["history"]["type"] == "array"
