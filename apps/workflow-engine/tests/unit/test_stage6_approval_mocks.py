"""Stage 6 fixer: the decision-only Approval mock, the AIAgent mock normalisation and the minimum-tests rule (SPEC009).

A spec's tests are written before the workflow exists, so an Approval mock cannot list the items that reach it. The decision-only form
``{port?, approval: {decision, ...}}`` has no items: test mode emits each REAL incoming item plus ``approval`` on the decision's port."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.engine.test_mode import TestRunOptions, mock_kind, plan_mock
from src.engine.types import ExecutionContext, NodeData, Workflow
from src.services import ai_default_mocks, ai_spec
from src.services.test_ai_service import validate_test

from ._runner_fakes import PassNode, items, make_runner, node, wf

pytestmark = pytest.mark.anyio

NOW = datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc)


def ctx():
    return ExecutionContext(workflow=Workflow(name="t", nodes=[], connections=[]), execution_id="e", start_time=NOW, mode="manual")


DECIDED = {"port": "approved", "approval": {"decision": "approved", "comment": "fine", "decided_by": "Asha"}}


# ── test mode ────────────────────────────────────────────────────────────────

def test_mock_kind_knows_the_decision_only_form_and_items_still_wins():
    assert mock_kind(DECIDED) == "approval"
    assert mock_kind({"approval": {"decision": "approved"}, "items": [{}]}) == "items"  # the old form is unchanged
    assert mock_kind({"approval": "yes"}) == "unknown"


def test_decision_only_mock_emits_each_real_incoming_item_plus_approval_on_the_port():
    incoming = [NodeData(json={"statusCode": 200, "headers": {}, "body": {"x": 1}}), NodeData(json={"statusCode": 200, "headers": {}, "body": {"x": 2}})]
    plan = plan_mock(DECIDED, "Review", "Approval", incoming, ctx())
    assert plan.kind == "items" and list(plan.outputs) == ["approved"]
    out = [i.json for i in plan.outputs["approved"]]
    assert [o["body"] for o in out] == [{"x": 1}, {"x": 2}] and all(o["statusCode"] == 200 for o in out)
    assert out[0]["approval"] == {"decision": "approved", "comment": "fine", "form_data": {}, "decided_by": "Asha"}


def test_the_port_defaults_to_the_decision_and_timed_out_gets_a_system_decider():
    plan = plan_mock({"approval": {"decision": "rejected"}}, "Review", "Approval", items({"a": 1}), ctx())
    assert list(plan.outputs) == ["rejected"] and plan.outputs["rejected"][0].json["approval"]["decided_by"] == "tester"
    plan = plan_mock({"approval": {"decision": "timed_out"}}, "Review", "Approval", items({"a": 1}), ctx())
    assert list(plan.outputs) == ["timed_out"] and plan.outputs["timed_out"][0].json["approval"]["decided_by"] == "system:timeout"


def test_the_items_form_still_replaces_the_output():
    mock = {"items": [{"only": "this", "approval": {"decision": "approved"}}], "port": "approved"}
    plan = plan_mock(mock, "Review", "Approval", items({"a": 1}), ctx())
    assert [i.json for i in plan.outputs["approved"]] == [{"only": "this", "approval": {"decision": "approved"}}]


def test_expressions_in_the_decision_resolve_against_the_incoming_item():
    mock = {"approval": {"decision": "approved", "comment": "ok {{ $json.id }}"}}
    plan = plan_mock(mock, "Review", "Approval", items({"id": 7}), ctx())
    assert plan.outputs["approved"][0].json["approval"]["comment"] == "ok 7"


def test_a_decision_only_mock_on_another_node_is_a_bad_mock():
    plan = plan_mock(DECIDED, "Fetch", "HttpRequest", items({}), ctx())
    assert plan.kind == "raise" and plan.error.code == "TEST_BAD_MOCK"


def test_a_rules_mock_can_decide_per_item():
    mock = {"rules": [{"when": {"path": "amount", "op": "gt", "value": 100}, "then": {"approval": {"decision": "rejected"}}}],
            "default": {"approval": {"decision": "approved"}}}
    plan = plan_mock(mock, "Review", "Approval", items({"amount": 500}, {"amount": 5}), ctx())
    assert [i.json["amount"] for i in plan.outputs["rejected"]] == [500] and [i.json["amount"] for i in plan.outputs["approved"]] == [5]


async def test_a_run_passes_the_real_item_through_the_mocked_approval():
    runner, log = make_runner()
    runner._registry._nodes["Approval"] = PassNode(log)
    w = wf([node("a"), node("Review", "Approval"), node("z")], [("a", "Review"), ("Review", "z", "approved")])
    opts = TestRunOptions(mocks={"Review": DECIDED}, now=NOW, seed=1, persist=False)
    ctx_ = await runner.run(w, "a", items({"amount": 700, "who": "x"}), test_options=opts)
    assert not ctx_.errors and "Review" not in log
    z = ctx_.node_states["z"][0].json
    assert z["amount"] == 700 and z["approval"]["decision"] == "approved" and z["approval"]["comment"] == "fine"


# ── validation of authored tests ─────────────────────────────────────────────

def _test(mock):
    return {"name": "t", "target": {"kind": "workflow"}, "input": {"items": [{}]}, "mocks": {"Review": mock},
            "expect": [{"node": "Review", "op": "status", "value": "success", "subject": "node"}]}


DEFN = {"nodes": [{"name": "Review", "type": "Approval"}, {"name": "Fetch", "type": "HttpRequest"}]}


def test_validate_test_accepts_the_decision_only_mock_on_an_approval_only():
    assert validate_test(_test(DECIDED), DEFN) is None
    bad = _test(DECIDED)
    bad["mocks"] = {"Fetch": DECIDED}
    assert "Approval nodes only" in validate_test(bad, DEFN)
    assert "decision must be" in validate_test(_test({"approval": {"decision": "maybe"}}), DEFN)


def test_the_default_approval_mock_is_decision_only():
    m = ai_default_mocks.approval_mock("rejected")
    assert "items" not in m and m["port"] == "rejected" and m["approval"]["decision"] == "rejected" and mock_kind(m) == "approval"


# ── AIAgent mocks ────────────────────────────────────────────────────────────

def _spec(agent_items, step_type="AIAgent"):
    return {"steps": [{"name": "Check", "node_type": step_type}], "tests": [{"mocks": {"Check": {"items": agent_items}}}]}


def test_a_flat_agent_mock_is_wrapped_as_structured():
    spec = _spec([{"complete": True, "missing": []}])
    ai_spec.normalize_agent_mocks(spec)
    assert spec["tests"][0]["mocks"]["Check"]["items"] == [{"response": "", "structured": {"complete": True, "missing": []}}]


def test_an_agent_mock_in_the_runtime_shape_and_other_steps_are_left_alone():
    right = [{"response": "ok", "structured": {"complete": True}}]
    spec = _spec(right)
    ai_spec.normalize_agent_mocks(spec)
    assert spec["tests"][0]["mocks"]["Check"]["items"] == right
    other = _spec([{"complete": True}], step_type="LLMChat")
    ai_spec.normalize_agent_mocks(other)
    assert other["tests"][0]["mocks"]["Check"]["items"] == [{"complete": True}]


def test_parse_spec_normalises_agent_mocks_of_the_model_reply():
    import json
    test = {"name": "T", "target": {"kind": "workflow"}, "input": {"items": [{}]}, "mocks": {"Check": {"items": [{"complete": False}]}},
            "expect": [{"node": "Check", "op": "status", "value": "success", "subject": "node"}]}
    reply = {"title": "x", "rules": [{"id": "r1", "text": "t"}], "steps": [{"name": "Check", "node_type": "AIAgent"}],
             "tests": [{"ref": "T1", "rules": ["r1"], "test_json": json.dumps(test)}]}
    spec, _ = ai_spec.parse_spec(reply)
    assert spec["tests"][0]["mocks"]["Check"]["items"] == [{"response": "", "structured": {"complete": False}}]


# ── SPEC009: enough tests ────────────────────────────────────────────────────

def _steps(*types):
    return [{"name": f"s{i}", "node_type": t} for i, t in enumerate(types)]


@pytest.mark.parametrize("types, need", [
    (("Webhook", "Set"), 3),
    (("Webhook", "If", "SendEmail"), 3),
    (("FormTrigger", "HttpRequest", "If", "Approval", "HttpRequest"), 4),
    (("FormTrigger", "If", "Approval", "If", "Approval"), 6),
    (("Webhook", "Switch", "If"), 5),
    (("Webhook",) + ("If",) * 20, 8),
])
def test_required_test_count(types, need):
    assert ai_spec.required_test_count(_steps(*types)) == need


def _spec_with(types, n_tests):
    steps = [{"name": f"s{i}", "node_type": t, "purpose": "", "rules": []} for i, t in enumerate(types)]
    tests = [{"id": f"t_{i:08x}", "name": f"t{i}", "target": {"kind": "workflow"}, "input": {"items": [{}]}, "mocks": {},
              "expect": [{"node": "s0", "op": "status", "value": "success", "subject": "node"}]} for i in range(n_tests)]
    return {"spec_version": 1, "title": "x", "trigger": {"kind": "webhook", "fields": []}, "rules": [{"id": "r1", "text": "t"}], "steps": steps,
            "tests": tests, "rule_tests": {"r1": [t["id"] for t in tests]}}


def test_too_few_tests_is_a_repairable_warning_not_an_error():
    spec = _spec_with(("Webhook", "If", "Approval", "Set"), 1)
    issues = [i for i in ai_spec.validate_spec(spec, ["Webhook", "If", "Approval", "Set"], []) if i["code"] == "SPEC009"]
    assert len(issues) == 1 and issues[0]["level"] == "warning" and "at least 4" in issues[0]["message"]
    assert not [i for i in ai_spec.errors_of(issues)]


def test_enough_tests_and_modify_mode_raise_no_spec009():
    cat = ["Webhook", "If", "Approval", "Set"]
    ok = ai_spec.validate_spec(_spec_with(("Webhook", "If", "Approval", "Set"), 4), cat, [])
    assert not [i for i in ok if i["code"] == "SPEC009"]
    few = ai_spec.validate_spec(_spec_with(("Webhook", "If", "Approval", "Set"), 1), cat, [], mode="modify")
    assert not [i for i in few if i["code"] == "SPEC009"]


def test_required_test_count_asks_one_test_per_rule():
    """Live eval regression: 4 rules and one If gave a floor of 3, so a spec with 3 tests left a rule's listed value unproven."""
    assert ai_spec.required_test_count(_steps("FormTrigger", "If", "HttpRequest"), 4) == 4
    assert ai_spec.required_test_count(_steps("FormTrigger", "If"), 2) == 3
    assert ai_spec.required_test_count(_steps("FormTrigger"), 20) == ai_spec.MAX_REQUIRED_TESTS
