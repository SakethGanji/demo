"""Capability graph: the concept checks CGX01-CGX05 (``services/kg_checks``). Each fires and does not fire."""

from __future__ import annotations

from src.services import kg_checks as K


def u(concepts, slots=None, retrieval=None):
    return {"concepts": [{"key": f"concept:{c}", "label": c, "confirmed": True, "score": 0.9, "source": "lexical", "evidence": ""} for c in concepts],
            "slots": {k: {"value": v, "from": "request"} for k, v in (slots or {}).items()}, "retrieval": retrieval or {"subworkflows": []}}


def d(nodes, conns=()):
    return {"nodes": [{"name": n[0], "type": n[1], "parameters": n[2] if len(n) > 2 else {}} for n in nodes],
            "connections": [{"source_node": a, "source_output": p, "target_node": b, "target_input": "main"} for a, p, b in conns]}


def codes(findings):
    return [f["code"] for f in findings]


def test_nothing_to_check_without_an_understanding_or_a_definition():
    assert K.check(None, u(["approval"])) == [] and K.check(d([("A", "Approval")]), None) == [] and K.check(d([("A", "Approval")]), u([])) == []


def test_findings_have_the_checker_shape_and_a_fix_hint():
    f = K.check(d([("A", "Approval", {"assignees": ["someone"]})]), u(["approval"], {"approval.approver_role": "role:ops_head"}))[0]
    assert f["code"] == "CGX01" and f["severity"] == "warning" and f["fix_hint"] and f["fix_hint"] in f["message"]
    assert {"node", "param", "path"} <= set(f)


# CGX01
def test_cgx01_role_not_used_fires_and_clears():
    un = u(["approval"], {"approval.approver_role": "role:ops_head"})
    assert codes(K.check(d([("Approve", "Approval", {"assignees": ["role:ops_analyst"]})]), un)) == ["CGX01"]
    assert K.check(d([("Approve", "Approval", {"assignees": ["role:ops_head"]})]), un) == []
    assert K.check(d([("Approve", "Approval", {"assignees": "role:ops_head"})]), un) == []


def test_cgx01_ignores_defaults_new_roles_and_unconfirmed_concepts():
    wf = d([("Approve", "Approval", {"assignees": ["x"]})])
    assert K.check(wf, {**u(["approval"]), "slots": {"approval.approver_role": {"value": "role:ops_head", "from": "default"}}}) == []
    assert K.check(wf, u(["approval"], {"approval.approver_role": {"new_role": "Branch lead"}})) == []
    assert K.check(wf, u(["refund"], {"approval.approver_role": "role:ops_head"})) == []     # approval is not confirmed


def test_cgx01_one_finding_per_role_even_if_two_slots_use_it():
    un = u(["approval", "escalation"], {"approval.approver_role": "role:ops_head", "escalation.target_role": "role:ops_head"})
    assert codes(f for f in K.check(d([("A", "Approval")]), un) if f["code"] == "CGX01") == ["CGX01"]


# CGX02
ESC = u(["escalation", "sla"], {"sla.due_in": "48h"})


def esc_wf(due="48h", wired=True):
    return d([("First", "Approval", {"due_in": due}), ("Second", "Approval", {})], [("First", "timed_out", "Second")] if wired else [])


def test_cgx02_escalation_needs_due_in_and_a_wired_timed_out():
    assert K.check(esc_wf(), ESC) == []
    assert codes(K.check(esc_wf(wired=False), ESC)) == ["CGX02"]
    assert "timed_out" in K.check(esc_wf(wired=False), ESC)[0]["message"]
    f = K.check(esc_wf(due="24h"), ESC)
    assert codes(f) == ["CGX02"] and "equals the SLA" in f[0]["message"]
    no_due = K.check(d([("First", "Approval", {}), ("Second", "Approval", {})], [("First", "approved", "Second")]), ESC)
    assert codes(no_due) == ["CGX02"] and "no Approval has `due_in`" in no_due[0]["message"]


def test_cgx02_a_cron_sweep_is_an_accepted_alternative_and_other_concepts_are_not_checked():
    sweep = d([("Every hour", "Cron", {}), ("Open cases", "DataTable", {}), ("Escalate", "SendEmail", {})])
    assert K.check(sweep, ESC) == []
    assert K.check(esc_wf(wired=False), u(["approval"])) == []


# CGX03
SUB = {"subworkflows": [{"key": "wf:wf_assign", "label": "Assign owner", "concepts": ["concept:assignment"]}]}


def test_cgx03_reuse_ignored_when_the_steps_were_rebuilt_inline():
    un = u(["assignment"], retrieval=SUB)
    inline = d([("By category", "Switch"), ("Set owner", "Set")])
    assert codes(K.check(inline, un)) == ["CGX03"]
    assert "wf_assign" in K.check(inline, un)[0]["fix_hint"]
    called = d([("Assign", "ExecuteWorkflow", {"workflowId": "wf_assign"})])
    assert K.check(called, un) == []
    assert K.check(d([("Set owner", "Set")]), un) == []          # nothing inline that covers the concept: nothing to say
    assert K.check(inline, u(["refund"], retrieval=SUB)) == []   # the concept is not confirmed


# CGX04
def test_cgx04_schedule_must_match_the_slot():
    un = u(["report", "schedule"], {"report.schedule": "0 8 * * 1"})
    good = d([("Mondays", "Cron", {"mode": "cron", "cronExpression": "0  8 * * 1"})])
    assert K.check(good, un) == []
    assert codes(K.check(d([("Daily", "Cron", {"mode": "cron", "cronExpression": "0 8 * * *"})]), un)) == ["CGX04"]
    assert codes(K.check(d([("Interval", "Cron", {"mode": "interval", "interval": 60})]), un)) == ["CGX04"]
    assert K.check(d([("Form", "FormTrigger")]), un) == []       # no Cron step: not a cron workflow, nothing to compare


# CGX05
def test_cgx05_part_calls_must_match_the_sibling_contract():
    sib = {"@part/assign_owner": {"inputs": [{"name": "category", "required": True}, {"name": "note", "required": False}]}}
    call = lambda inputs: d([("Assign", "ExecuteWorkflow", {"workflowId": "@part/assign_owner", "inputs": inputs})])
    un = u(["approval"])   # CGX05 is independent of the concepts
    assert K.check(call({"category": "x", "note": "y"}), un, siblings=sib) == []
    missing = K.check(call({"note": "y"}), un, siblings=sib)
    assert codes(missing) == ["CGX05"] and "missing category" in missing[0]["message"]
    extra = K.check(call({"category": "x", "bogus": 1}), un, siblings=sib)
    assert codes(extra) == ["CGX05"] and "unknown bogus" in extra[0]["message"]
    assert K.check(call({}), un) == []                              # without siblings there is nothing to compare against
    assert K.check(d([("Real", "ExecuteWorkflow", {"workflowId": "wf_real", "inputs": {}})]), un, siblings=sib) == []


def test_disabled_nodes_are_ignored():
    wf = d([("First", "Approval", {"due_in": "48h"})])
    wf["nodes"][0]["disabled"] = True
    assert codes(K.check(wf, ESC)) == ["CGX02"]
