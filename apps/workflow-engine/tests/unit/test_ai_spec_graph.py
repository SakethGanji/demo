"""Capability graph: SPEC010 and SPEC011 (``ai_spec.graph_issues`` and ``validate_spec(understanding=...)``)."""

from __future__ import annotations

from src.services import ai_spec as S


def un(*concepts, slots=None):
    return {"concepts": [{"key": f"concept:{c}", "label": c.replace("_", " ").title(), "confirmed": True} for c in concepts],
            "slots": {k: {"value": v, "from": "request"} for k, v in (slots or {}).items()}}


def spec(steps, tests=()):
    return {"steps": [{"name": n, "node_type": t} for n, t in steps], "tests": list(tests)}


def test(mocks=None, expect=()):
    return {"name": "t", "mocks": mocks or {}, "expect": list(expect)}


def codes(issues):
    return sorted(i["code"] for i in issues)


def test_nothing_without_an_understanding_or_confirmed_concepts():
    assert S.graph_issues(spec([("A", "Approval")]), {"concepts": []}) == []
    assert S.validate_spec(spec([("A", "Set")]), ["Set", "Approval"], []) == S.validate_spec(spec([("A", "Set")]), ["Set", "Approval"], [], understanding=None)


def test_spec010_a_confirmed_concept_needs_a_step_of_a_matching_type():
    f = S.graph_issues(spec([("Start", "Webhook"), ("Pay", "HttpRequest")]), un("approval"))
    assert codes(f) == ["SPEC010"] and f[0]["level"] == "warning" and "Approval" in f[0]["message"]
    assert S.graph_issues(spec([("Start", "Webhook"), ("OK?", "Approval")]), un("approval", slots={"sla.due_in": "48h"}))[0]["code"] == "SPEC011"
    # one signature type is enough; an unrelated concept with no signature match fires its own
    f2 = S.graph_issues(spec([("Start", "Webhook")]), un("intake", "schedule"))
    assert [i["message"] for i in f2 if "Schedule" in i["message"]] and not [i for i in f2 if "Intake" in i["message"]]


def test_spec010_counts_the_base_workflows_nodes_in_modify_mode():
    base = {"nodes": [{"name": "Gate", "type": "Approval"}]}
    assert "SPEC010" in codes(S.graph_issues(spec([("Log", "Set")]), un("approval")))
    assert "SPEC010" not in codes(S.graph_issues(spec([("Log", "Set")]), un("approval"), base))


def approvals(ports, extra=()):
    tests = [test({"OK?": {"port": p, "items": [{}]}}) for p in ports] + list(extra)
    return spec([("Start", "Webhook"), ("OK?", "Approval")], tests)


def test_spec011_each_wired_port_needs_a_test():
    got = S.graph_issues(approvals(["approved"]), un("approval"))
    assert codes(got) == ["SPEC011"] and "'rejected'" in got[0]["message"]
    assert S.graph_issues(approvals(["approved", "rejected"]), un("approval")) == []
    assert codes(S.graph_issues(approvals([]), un("approval"))) == ["SPEC011", "SPEC011"]


def test_spec011_an_assertion_on_the_port_counts_too():
    asserted = approvals(["approved"], [test(expect=[{"node": "OK?", "port": "rejected", "op": "count", "value": 1}])])
    assert S.graph_issues(asserted, un("approval")) == []


def test_spec011_a_time_limit_needs_a_timed_out_test():
    esc = un("escalation", "sla", slots={"sla.due_in": "48h"})
    got = S.graph_issues(approvals(["approved", "rejected"]), esc)
    assert codes(got) == ["SPEC011"] and "timed_out" in got[0]["message"]
    assert S.graph_issues(approvals(["approved", "rejected", "timed_out"]), esc) == []
    # an Approval with an SLA slot needs it too; one without a time limit does not
    assert codes(S.graph_issues(approvals(["approved", "rejected"]), un("approval", slots={"sla.due_in": "4h"}))) == ["SPEC011"]
    assert S.graph_issues(approvals(["approved", "rejected"]), un("approval")) == []


def test_validate_spec_adds_them_as_warnings_only():
    issues = S.validate_spec(approvals(["approved"]), ["Webhook", "Approval"], [], understanding=un("approval"))
    g = [i for i in issues if i["code"] in ("SPEC010", "SPEC011")]
    assert g and all(i["level"] == "warning" for i in g)
    assert not [i for i in S.errors_of(issues) if i["code"] in ("SPEC010", "SPEC011")]
