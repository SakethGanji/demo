"""Test recipes for Approval and FormTrigger (Stage 5, DESIGN-STAGE5-UIS 5.7): a draft per decision, mocked, never waiting."""

from __future__ import annotations

import pytest

from src.engine.effects import NODE_EFFECTS, effect_of
from src.engine.node_registry import node_registry, register_all_nodes
from src.engine.test_recipes import SuggestContext, recipe_for, suggest_for_workflow
from src.services.node_catalog import build_node_catalog

register_all_nodes()


def N(name, type_, params=None, **kw):
    return {"name": name, "type": type_, "parameters": params or {}, **kw}


def defn(*nodes):
    return {"nodes": list(nodes), "connections": [], "tests": [], "test_defaults": {}}


def test_effects_and_registry_completeness():
    assert NODE_EFFECTS["Approval"] == "human" and NODE_EFFECTS["FormTrigger"] == "none"
    assert effect_of("Approval") == "human"
    assert "Approval" in node_registry.list() and "FormTrigger" in node_registry.list()
    assert set(node_registry.list()) <= set(NODE_EFFECTS)
    cat = {c["type"]: c for c in build_node_catalog()}
    assert cat["Approval"]["effect"] == "human" and cat["Approval"]["recipe"]["categories"] == ["normal"]
    assert cat["FormTrigger"]["effect"] == "none"


def test_the_approval_recipe_has_one_draft_per_decision_with_a_port_mock():
    node = N("Review", "Approval", {"due_in": "3h"})
    drafts = recipe_for("Approval").suggest(node, {}, SuggestContext(defn(node)))
    assert [d["name"] for d in drafts] == ["Review: approved path", "Review: rejected path", "Review: timed out path"]
    for d, port in zip(drafts, ("approved", "rejected", "timed_out")):
        mock = d["mocks"]["Review"]
        assert mock["port"] == port and mock["items"][0]["approval"]["decision"] == port
        assert d["expect"] == [{"node": "Review", "port": port, "op": "count", "value": 1}]
        assert d["status"] == "draft" and d["source"] == "suggested" and d["target"] == {"kind": "node", "node": "Review"}


def test_the_timed_out_draft_only_when_the_node_can_time_out():
    can = N("A", "Approval", {"on_timeout": "reject", "due_in": "1h"})
    cannot = N("B", "Approval", {"on_timeout": "approve"})  # no deadline, and a timeout would approve: no timed_out port to test
    assert len(recipe_for("Approval").suggest(can, {}, SuggestContext(defn(can)))) == 3
    assert [d["name"] for d in recipe_for("Approval").suggest(cannot, {}, SuggestContext(defn(cannot)))] == ["B: approved path", "B: rejected path"]


def test_drafts_have_stable_ids_and_dedupe_across_calls():
    node = N("Review", "Approval")
    d = defn(node)
    a = suggest_for_workflow(d, {}, SuggestContext(d))
    b = suggest_for_workflow(d, {}, SuggestContext(d))
    assert [x["id"] for x in a] == [x["id"] for x in b] and len({x["id"] for x in a}) == len(a) == 3


def test_form_trigger_gets_a_trigger_recipe():
    trig = N("Form", "FormTrigger", {"input_schema": {"type": "object", "properties": {"amount": {"type": "number"}}, "required": ["amount"]}})
    d = defn(trig)
    out = suggest_for_workflow(d, {}, SuggestContext(d))
    assert len(out) == 1 and out[0]["target"] == {"kind": "workflow"} and out[0]["name"].startswith("Form:")
    assert recipe_for("FormTrigger").categories == ["normal"]


@pytest.mark.anyio
async def test_a_suggested_approval_test_runs_green_without_waiting():
    """The drafts run as they are in test mode: the mock answers, nothing pauses, nothing is created."""
    from src.engine.test_mode import TestRunOptions
    from src.engine.types import Connection, NodeData, NodeDefinition, Workflow
    from src.engine.workflow_runner import WorkflowRunner
    from src.engine.test_mode import parse_now

    wf = Workflow(name="w", nodes=[NodeDefinition("Start", "Start"), NodeDefinition("Review", "Approval"), NodeDefinition("Done", "Set", {"mode": "manual", "fields": []})],
                  connections=[Connection("Start", "Review"), Connection("Review", "Done", "rejected")])
    node = N("Review", "Approval")
    for draft in recipe_for("Approval").suggest(node, {}, SuggestContext(defn(node))):
        opts = TestRunOptions(mocks=draft["mocks"], now=parse_now(None), seed=1, persist=False)
        ctx = await WorkflowRunner().run(wf, "Review", [NodeData(json=i) for i in draft["input"]["items"]], test_options=opts)
        assert ctx.errors == [] and ctx.suspensions == []
        port = draft["mocks"]["Review"]["port"]
        assert [p for p, its in ctx.test_outputs["Review"].items() if its] == [port]
