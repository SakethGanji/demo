"""Capability graph WP-A: deterministic concept signatures and slot read-back over workflow definitions."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.services import kg_signatures as ks

TEMPLATES = Path(__file__).resolve().parents[2] / "templates"


def node(name, type_, **params):
    return {"name": name, "type": type_, "parameters": params}


def conn(a, b, port="main"):
    return {"source_node": a, "target_node": b, "source_output": port, "target_input": "main"}


def keys(defn) -> dict[str, ks.Detection]:
    return {d["concept"]: d for d in ks.detect(defn)}


def escalation_defn(**over) -> dict:
    first = {"assignees": ["role:ops_analyst"], "due_in": "48h", "title": "Resolve"}
    first.update(over)
    return {
        "nodes": [node("Log", "FormTrigger"), node("Resolve", "Approval", **first),
                  node("Escalated", "Approval", assignees=["role:ops_head"], title="Escalated"), node("Done", "Set")],
        "connections": [conn("Log", "Resolve"), conn("Resolve", "Done", "approved"), conn("Resolve", "Escalated", "timed_out"),
                        conn("Escalated", "Done", "approved")],
    }


def test_refund_triage_template_gives_approval_and_refund():
    raw = json.loads((TEMPLATES / "refund-triage" / "template.json").read_text())
    main = next(w for w in raw["workflows"] if w["role"] == "entry")["definition"]
    found = keys(main)
    assert "concept:approval" in found and "concept:refund" in found      # Approval after an If
    assert found["concept:approval"]["evidence"]["nodes"] == ["Manager approval"]
    assert "concept:escalation" not in found                              # no due_in, no timed_out
    assert "concept:intake" in found                                      # FormTrigger


def test_escalation_needs_due_in_and_a_wired_timed_out_port():
    found = keys(escalation_defn())
    esc = found["concept:escalation"]
    assert esc["evidence"] == {"nodes": ["Resolve"], "ports": ["timed_out"]}
    assert esc["slots"] == {"sla.due_in": "48h", "escalation.after": "48h", "escalation.target_role": "role:ops_head"}
    assert found["concept:sla"]["slots"] == {"sla.due_in": "48h"}
    assert found["concept:approval"]["slots"]["approval.approver_role"] == "role:ops_analyst"
    # the second Approval follows the first: four-eyes by shape, with both roles read back
    assert found["concept:four_eyes_approval"]["slots"] == {"four_eyes_approval.approver_role": "role:ops_analyst",
                                                              "four_eyes_approval.second_approver_role": "role:ops_head"}
    # due_in set but the port is not wired -> SLA yes, Escalation no
    d = escalation_defn()
    d["connections"] = [c for c in d["connections"] if c["source_output"] != "timed_out"]
    d["nodes"] = [n for n in d["nodes"] if n["name"] != "Escalated"]
    found = keys(d)
    assert "concept:sla" in found and "concept:escalation" not in found
    # no due_in -> neither
    found = keys(escalation_defn(due_in=""))
    assert "concept:sla" not in found and "concept:escalation" not in found


def test_cron_gives_schedule_and_the_expression_slot():
    d = {"nodes": [node("Mondays", "Cron", mode="cron", cronExpression="0 8 * * 1")], "connections": []}
    found = keys(d)
    assert found["concept:schedule"]["slots"] == {"schedule.cron": "0 8 * * 1"}
    # a Cron followed by DataTable and SendEmail is a report as well, with the recipients role
    d = {"nodes": [node("Mondays", "Cron", cronExpression="0 8 * * 1"), node("Read", "DataTable", table="cases", operation="list"),
                   node("Send", "SendEmail", toEmail="role:ops_manager")],
         "connections": [conn("Mondays", "Read"), conn("Read", "Send")]}
    found = keys(d)
    # `follows` means the DataTable/SendEmail are upstream of the Cron, so this order is NOT the report signature
    assert "concept:report" not in found
    assert found["concept:notification"]["slots"] == {"notification.channel": "node:SendEmail"}
    d = {"nodes": [node("Read", "DataTable", table="cases", operation="list"), node("Send", "SendEmail", toEmail="role:ops_manager"),
                   node("Mondays", "Cron", cronExpression="0 8 * * 1")],
         "connections": [conn("Read", "Send"), conn("Send", "Mondays")]}
    rep = keys(d)["concept:report"]
    assert rep["slots"] == {"report.schedule": "0 8 * * 1", "report.recipients_role": "role:ops_manager"}


def test_datatable_insert_with_a_status_column_gives_exception_case():
    d = {"nodes": [node("Store", "DataTable", table="payment_exception_cases", operation="insert",
                        data={"id": "{{ $json.id }}", "status": "open", "category": "{{ $json.category }}"})], "connections": []}
    found = keys(d)
    assert found["concept:exception_case"]["slots"] == {"exception_case.entity": "payment_exception"}
    # reading a table, or a table without status, is not an exception case
    d["nodes"][0]["parameters"]["operation"] = "list"
    assert "concept:exception_case" not in keys(d)
    d["nodes"][0]["parameters"].update(operation="insert", data={"id": "1", "category": "x"})
    assert "concept:exception_case" not in keys(d)


def test_switch_gives_assignment_and_the_deciding_field():
    d = {"nodes": [node("By category", "Switch", rules=[{"field": "{{ $json.category }}", "operation": "equals", "value": "a", "output": 0}]),
                   node("Set owner", "Set")], "connections": [conn("By category", "Set owner", "output0")]}
    assert keys(d)["concept:assignment"]["slots"] == {"assignment.by_field": "category"}
    d = {"nodes": [node("By region", "Switch", field="region")], "connections": []}
    assert keys(d)["concept:assignment"]["slots"] == {"assignment.by_field": "region"}


def test_weak_node_types_alone_say_nothing():
    d = {"nodes": [node("Call", "HttpRequest", api_ref={"spec_id": "s", "operation_id": "o"}), node("Loop", "Loop"),
                   node("Reply", "Respond", body={"a": 1})], "connections": []}
    assert ks.detect(d) == []


def test_given_concepts_and_junk_input():
    only_cron = [{"key": "concept:schedule", "props": {"signatures": [{"node_type": "Cron"}]}}]
    d = {"nodes": [node("C", "Cron", cronExpression="* * * * *"), node("A", "Approval")], "connections": []}
    assert [x["concept"] for x in ks.detect(d, concepts=only_cron)] == ["concept:schedule"]
    assert ks.detect({}) == [] and ks.detect(None) == []   # type: ignore[arg-type]


def test_the_seed_concepts_all_have_loadable_signatures():
    for c in ks._seed_concepts():
        for s in ks._signatures_of(c):
            assert s["node_type"]
