"""Capability graph WP-D: the application plan (``services/app_plan``; CONTRACTS-CAPGRAPH.md 5.2). Pure: no database, no model.

Covers the recipe plan for the team A request (the PLAN section 5.2 shape), adapting from a validated pattern (slot diff, same part keys and
columns), a reused sub-workflow replacing a recipe part, the dependency helpers the composer uses, and each of APP001-APP008.
"""

from __future__ import annotations

import asyncio
import copy

import pytest

from src.services import app_plan as P
from src.services import kg_understand as U
from tests.unit._kg_support import make_slice, pattern, subworkflow

REQ_A = ("We need an application to manage operational exceptions: anyone in payments ops can log an exception, each one gets an owner based on "
         "its category, if the owner hasn't resolved it within 48 hours escalate it to the head of operations, and every Monday send the ops "
         "managers a report of open and closed exceptions.")
REQ_B = ("Build an app for card fraud alerts: log an alert, assign an analyst by region, escalate to the fraud lead if it isn't handled in 4 hours, "
         "and send fraud managers a daily summary.")


def understand(request, sl=None, answers=None):
    ctx = U.UnderstandContext("proj_a", "t1", sl or make_slice())
    return asyncio.run(U.understand(ctx, request, answers={"q_shape": "app", **(answers or {})}))


def plan_a(**kw):
    sl = make_slice()
    return P.plan_app(understand(REQ_A, sl), sl, request=REQ_A, **kw)


def by_key(plan):
    return {p["key"]: p for p in plan["parts"]}


# ── the recipe plan (team A) ──

def test_the_team_a_request_gives_the_section_5_2_plan():
    plan = plan_a()
    assert plan["v"] == 1 and plan["title"] == "Operational exceptions" and plan["entity"] == "exception" and plan["adapted_from"] is None
    parts = by_key(plan)
    assert set(parts) == {"assign_owner", "build_report", "log_exception", "weekly_report", "report_view"}
    assert {k for k, p in parts.items() if p["role"] == "subworkflow"} == {"assign_owner", "build_report"}
    assert all(p["trigger"] == "execute" for p in parts.values() if p["role"] == "subworkflow")
    log = parts["log_exception"]
    assert (log["role"], log["trigger"], log["depends_on"], log["tables"]) == ("entry", "form", ["assign_owner"], ["cases"])
    assert log["interface"] == {"kind": "form", "slug": "log-exception"}
    assert {"concept:intake", "concept:exception_case", "concept:approval", "concept:sla", "concept:escalation"} <= set(log["concepts"])
    weekly = parts["weekly_report"]
    assert (weekly["trigger"], weekly["cron"], weekly["depends_on"], weekly["recipients_role"]) == ("cron", "0 8 * * 1", ["build_report"], "role:ops_manager")
    assert {"concept:report", "concept:schedule"} <= set(weekly["concepts"])
    view = parts["report_view"]
    assert (view["trigger"], view["depends_on"], view["interface"]) == ("form", ["build_report"], {"kind": "custom", "slug": "exceptions-report", "screen": "report_table"})
    assert parts["assign_owner"]["contract"] == {"inputs": [{"name": "category", "type": "string", "required": True}], "output_fields": ["owner_role"]}
    assert parts["build_report"]["contract"]["output_fields"] == ["rows", "totals"]
    assert [t["name"] for t in plan["tables"]] == ["exception_cases"]
    assert [c["name"] for c in plan["tables"][0]["columns"]] == ["category", "status", "owner_role", "opened_at"]      # `id` is every row's own, not a column
    assert {c["name"]: c["type"] for c in plan["tables"][0]["columns"]}["opened_at"] == "datetime"
    assert plan["screens"] == ["form: Log an exception", "task: Resolve exception (Approvals inbox)", "task: Escalated exception", "page: Exceptions report (table)"]
    assert P.validate_app_plan(plan) == []


def test_sub_workflows_come_first_and_build_order_puts_children_before_parents():
    plan = plan_a()
    assert [p["role"] for p in plan["parts"]][:2] == ["subworkflow", "subworkflow"]
    order = P.build_order(plan)
    assert set(order) == set(by_key(plan))
    assert order.index("assign_owner") < order.index("log_exception") and order.index("build_report") < order.index("weekly_report") < len(order)
    assert order.index("build_report") < order.index("report_view")
    assert P.closure(plan, "log_exception") == ["assign_owner"] and P.closure(plan, "assign_owner") == []


def test_answered_fields_become_columns_and_the_entity_names_the_table_and_parts():
    sl = make_slice()
    u = understand(REQ_B, sl, answers={"q_exception_case.fields": ["region", "amount"]})
    plan = P.plan_app(u, sl, request=REQ_B)
    assert plan["entity"] == "alert" and plan["tables"][0]["name"] == "alert_cases"
    cols = {c["name"]: c["type"] for c in plan["tables"][0]["columns"]}
    assert cols["region"] == "string" and cols["amount"] == "number"        # the deciding field and the listed fields
    parts = by_key(plan)
    assert "log_alert" in parts and parts["log_alert"]["title"] == "Log an alert" and parts["log_alert"]["interface"]["slug"] == "log-alert"
    assert parts["weekly_report"]["cron"] == "0 8 * * *" and parts["weekly_report"]["title"] == "Daily alerts report"
    assert P.validate_app_plan(plan) == []


def test_part_brief_and_siblings_carry_the_fake_llm_marker_and_the_contracts():
    plan = plan_a()
    brief = P.part_brief(plan, "log_exception")
    assert brief["part_key"] == "log_exception" and brief["calls"] == ["@part/assign_owner"] and brief["tables"][0]["name"] == "exception_cases"
    assert brief["interface"]["slug"] == "log-exception" and brief["trigger"] == "form"
    sib = P.siblings_of(plan, "log_exception")
    assert list(sib) == ["@part/assign_owner"] and sib["@part/assign_owner"]["inputs"][0]["name"] == "category"
    assert sib["@part/assign_owner"]["output_fields"] == ["owner_role"]
    assert P.siblings_of(plan, "assign_owner") == {}


# ── adapt from a validated pattern ──

def test_adapting_from_a_pattern_clones_its_parts_and_tables_and_records_the_slot_diff():
    base = make_slice()
    a = plan_a()
    skeleton = {"parts": [{"key": p["key"].replace("exception", "{entity}"), "role": p["role"], "trigger": p["trigger"], "steps": [], "concepts": p["concepts"], "rules": []}
                          for p in a["parts"]],
                "tables": [{"key": "cases", "columns": ["category", "status", "owner_role", "opened_at"]}]}
    pat = pattern("pattern:abc123", "Case management app", sorted({c for p in a["parts"] for c in p["concepts"]}), shape="app", validated=1,
                  slots={"sla.due_in": "48h", "report.schedule": "0 8 * * 1", "escalation.target_role": "role:ops_head", "exception_case.entity": "exception",
                         "assignment.by_field": "category"})
    pat["props"]["skeleton"] = skeleton
    sl = make_slice(extra=[pat], extra_edges=[(pat, "implements", c) for c in pat["props"]["concepts"]])
    u = understand(REQ_B, sl)
    assert u["adapt_from"] and u["adapt_from"]["pattern"] == "pattern:abc123"
    plan = P.plan_app(u, sl, request=REQ_B)
    assert plan["adapted_from"]["pattern"] == "pattern:abc123" and plan["adapted_from"]["label"] == "Case management app"
    assert plan["adapted_from"]["validated_count"] == 1 and plan["adapted_from"]["source_project"] == "Payments Ops"
    diff = {d["slot"]: (d["from"], d["to"]) for d in plan["adapted_from"]["diff"]}
    assert diff["sla.due_in"] == ("48h", "4h") and diff["report.schedule"] == ("0 8 * * 1", "0 8 * * *")
    assert diff["escalation.target_role"] == ("role:ops_head", "role:fraud_lead") and diff["exception_case.entity"] == ("exception", "alert")
    assert diff["assignment.by_field"] == ("category", "region")
    parts = by_key(plan)
    assert set(parts) == {"assign_owner", "build_report", "log_alert", "weekly_report", "report_view"}      # the entity token is substituted
    assert parts["weekly_report"]["cron"] == "0 8 * * *" and parts["weekly_report"]["title"] == "Daily alerts report"
    assert plan["tables"][0]["name"] == "alert_cases"
    assert [c["name"] for c in plan["tables"][0]["columns"]] == ["category", "status", "owner_role", "opened_at"]   # the pattern's columns, so the skeleton hash stays
    assert P.validate_app_plan(plan) == []
    # the same request without the pattern composes from recipes and says nothing about adaptation
    fresh = P.plan_app(understand(REQ_B, base), base, request=REQ_B)
    assert fresh["adapted_from"] is None


def test_a_pattern_without_a_skeleton_falls_back_to_the_recipes():
    pat = pattern("pattern:nosk", "Empty skeleton", ["concept:intake", "concept:escalation", "concept:report"], shape="app")
    sl = make_slice(extra=[pat], extra_edges=[(pat, "implements", c) for c in pat["props"]["concepts"]])
    u = understand(REQ_A, sl)
    assert u["adapt_from"]
    plan = P.plan_app(u, sl, request=REQ_A)
    assert plan["adapted_from"] is None and "log_exception" in by_key(plan)


# ── reuse ──

def test_a_callable_sub_workflow_that_implements_the_concept_replaces_the_recipe_part():
    wf = subworkflow("wf_ab12", "Assign owner")
    sl = make_slice(extra=[wf], extra_edges=[(wf, "implements", "concept:assignment")])
    u = understand(REQ_A, sl)
    plan = P.plan_app(u, sl, request=REQ_A)
    part = by_key(plan)["assign_owner"]
    assert part["reuse"] == {"capability": "wf:wf_ab12", "label": "Assign owner"}
    assert "assign_owner" not in P.build_order(plan) and "log_exception" in P.build_order(plan)
    assert P.siblings_of(plan, "log_exception")["@part/assign_owner"]["reuse_workflow_id"] == "wf_ab12"
    assert P.validate_app_plan(plan, callable_keys={"wf:wf_ab12"}) == []
    assert [i["code"] for i in P.validate_app_plan(plan, callable_keys=set())] == ["APP008"]     # APP008: it must be callable in THIS project


# ── APP001-APP008 ──

def codes(plan, **kw):
    return sorted({i["code"] for i in P.validate_app_plan(plan, **kw) if i["severity"] == "error"})


def mutate(fn):
    plan = copy.deepcopy(plan_a())
    fn(plan)
    return plan


def test_app001_malformed_plans():
    assert codes("nope") == ["APP001"] and codes({"v": 2, "title": "x", "parts": []}) == ["APP001"]
    assert codes(mutate(lambda p: p.update(title=" "))) == ["APP001"]
    assert "APP001" in codes(mutate(lambda p: p["parts"][0].update(key="Bad Key")))
    assert "APP001" in codes(mutate(lambda p: p["parts"][1].update(key=p["parts"][0]["key"])))
    assert "APP001" in codes(mutate(lambda p: p["tables"][0]["columns"].append({"name": "status", "type": "string"})))     # a column listed twice
    assert "APP001" in codes(mutate(lambda p: p["parts"][0]["contract"].update(inputs=[])))                                # a sub-workflow without inputs


def test_app002_unknown_dependency_or_table():
    assert codes(mutate(lambda p: by_key(p)["log_exception"].update(depends_on=["ghost"]))) == ["APP002"]
    assert codes(mutate(lambda p: by_key(p)["log_exception"].update(tables=["nope"]))) == ["APP002"]


def test_app003_cycles():
    def cyc(p):
        by_key(p)["assign_owner"]["depends_on"] = ["build_report"]
        by_key(p)["build_report"]["depends_on"] = ["assign_owner"]
    assert codes(mutate(cyc)) == ["APP003"]
    assert codes(mutate(lambda p: by_key(p)["assign_owner"].update(depends_on=["assign_owner"]))) == ["APP003"]


def test_app004_limits_and_an_entry_is_required():
    def many(p):
        for i in range(3):
            p["parts"].append({**copy.deepcopy(by_key(p)["weekly_report"]), "key": f"extra_{i}"})
    assert codes(mutate(many)) == ["APP004"]
    def tables(p):
        p["tables"] += [{"key": f"t{i}", "name": f"t{i}", "columns": [{"name": "name", "type": "string"}]} for i in range(3)]
    assert codes(mutate(tables)) == ["APP004"]
    def no_entry(p):
        p["parts"] = [x for x in p["parts"] if x["role"] == "subworkflow"]
    assert codes(mutate(no_entry)) == ["APP004"]


def test_app005_no_approval_concept_in_a_sub_workflow():
    assert codes(mutate(lambda p: by_key(p)["assign_owner"]["concepts"].append("concept:escalation"))) == ["APP005"]
    assert codes(mutate(lambda p: by_key(p)["assign_owner"]["concepts"].append("concept:approval"))) == ["APP005"]


def test_app006_triggers_and_roles():
    assert "APP006" in codes(mutate(lambda p: by_key(p)["log_exception"].update(trigger="telepathy")))
    assert "APP006" in codes(mutate(lambda p: by_key(p)["assign_owner"].update(trigger="form")))       # a sub-workflow is started by a workflow
    assert "APP006" in codes(mutate(lambda p: by_key(p)["log_exception"].update(trigger="execute")))
    assert "APP006" in codes(mutate(lambda p: by_key(p)["log_exception"].update(role="boss")))
    assert "APP006" in codes(mutate(lambda p: by_key(p)["log_exception"].update(depends_on=["weekly_report"])))   # only sub-workflows can be called


def test_app007_columns_names_and_cron():
    assert codes(mutate(lambda p: p["tables"][0]["columns"][0].update(type="blob"))) == ["APP007"]
    assert codes(mutate(lambda p: p["tables"][0].update(name="bad name!"))) == ["APP007"]
    assert codes(mutate(lambda p: by_key(p)["weekly_report"].update(cron="every monday"))) == ["APP007"]
    assert codes(mutate(lambda p: p["tables"][0].update(columns=[]))) == ["APP007"]
    assert codes(mutate(lambda p: p["tables"][0]["columns"].append({"name": "id", "type": "string"}))) == ["APP007"]   # reserved by every data table


def test_app008_reuse_must_be_a_callable_sub_workflow():
    assert codes(mutate(lambda p: by_key(p)["assign_owner"].update(reuse={"capability": "api:notify.send"}))) == ["APP008"]
    assert codes(mutate(lambda p: by_key(p)["assign_owner"].update(reuse={"capability": "wf:wf_x"})), callable_keys=set()) == ["APP008"]
    assert codes(mutate(lambda p: by_key(p)["assign_owner"].update(reuse={"capability": "wf:wf_x"})), callable_keys={"wf:wf_x"}) == []
