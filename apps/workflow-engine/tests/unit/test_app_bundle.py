"""Capability graph WP-D: compiling an app's parts into a ``wf-template/1`` bundle and the virtual siblings a part is built against
(``services/app_bundle``; the ``generated`` lint profile of ``template_bundle``). Pure: no database, no model.

The catalog profile is the Stage 8 behaviour and must not change: the regression tests here pin that a shipped template is still held to
``.example`` hosts, fake emails, a failure-path test and one entry, and that the generated profile relaxes exactly those and nothing else.
"""

from __future__ import annotations

import copy

import pytest

from src.schemas.interface import generate_interface
from src.services import app_bundle as B
from src.services import app_plan as P
from src.services.template_bundle import TemplateBundleError, lint_bundle, parse_bundle
from src.services.workflow_checker import StaticLookups, check_workflow

REQ = "We need an application to manage operational exceptions."


def node(name, type_, params=None, x=0):
    return {"name": name, "type": type_, "parameters": params or {}, "position": {"x": x, "y": 0}}


def make_case(tid, name, mocks, expect, items, category="normal"):
    return {"id": tid, "name": name, "target": {"kind": "workflow"}, "input": {"items": items}, "mocks": mocks, "expect": expect, "category": category,
            "status": "approved", "locked": True, "source": "ai", "approved_by": "Asha", "approved_at": "2026-10-18T10:00:00+00:00"}


def call(name, part, inputs=None):
    return node(name, "ExecuteWorkflow", {"workflowId": f"@part/{part}", "version": "latest", "inputMode": "map", "inputs": inputs or {}})


def definitions() -> dict[str, dict]:
    assign = {"nodes": [node("Inputs", "ExecuteWorkflowTrigger", {"inputSource": "fields", "fields": [{"name": "category", "type": "string", "required": True}]}),
                        node("Pick owner", "Set", {"mode": "manual", "keepOnlySet": True, "fields": [{"name": "owner_role", "value": "role:ops_analyst"}]}, 240)],
              "connections": [{"source_node": "Inputs", "target_node": "Pick owner"}],
              "tests": [make_case("t_a0000001", "An owner is picked", {}, [{"node": "Pick owner", "path": "owner_role", "op": "eq", "value": "role:ops_analyst"}],
                                  [{"category": "payments"}])]}
    report = {"nodes": [node("Inputs", "ExecuteWorkflowTrigger", {"inputSource": "fields", "fields": [{"name": "since", "type": "string", "required": False}]}),
                        node("Read cases", "DataTable", {"table": "exception_cases", "operation": "list"}, 240),
                        node("Totals", "Set", {"mode": "manual", "keepOnlySet": True, "fields": [{"name": "rows", "value": "{{ $json.status }}"}, {"name": "totals", "value": "1"}]}, 480)],
              "connections": [{"source_node": "Inputs", "target_node": "Read cases"}, {"source_node": "Read cases", "target_node": "Totals"}],
              "tests": [make_case("t_b0000001", "Rows are summed", {"Read cases": {"items": [{"id": 1, "status": "open"}]}}, [{"node": "Totals", "op": "status", "value": "success"}],
                                  [{"since": "2026-01-01"}])]}
    log = {"nodes": [node("Log form", "FormTrigger", {"title": "Log an exception", "fields": [{"name": "category", "type": "string", "required": True, "label": "Category"}]}),
                     call("Assign owner", "assign_owner", {"category": "{{ $json.category }}"}),
                     node("Save", "DataTable", {"table": "exception_cases", "operation": "insert", "data": {"category": "{{ $json.category }}", "status": "open"}}, 480)],
           "connections": [{"source_node": "Log form", "target_node": "Assign owner"}, {"source_node": "Assign owner", "target_node": "Save"}],
           "tests": [make_case("t_c0000001", "An exception is saved", {"Assign owner": {"items": [{"owner_role": "role:ops_analyst"}]}, "Save": {"items": [{"id": 1}]}},
                               [{"node": "Save", "op": "status", "value": "success"}], [{"category": "payments"}])]}
    weekly = {"nodes": [node("Every Monday", "Cron", {"mode": "cron", "cronExpression": "0 8 * * 1"}), call("Build report", "build_report"),
                        node("Send report", "SendEmail", {"toEmail": "role:ops_manager", "subject": "Report", "body": "{{ $json.totals }}"}, 480)],
              "connections": [{"source_node": "Every Monday", "target_node": "Build report"}, {"source_node": "Build report", "target_node": "Send report"}],
              "tests": [make_case("t_d0000001", "The report is sent", {"Build report": {"items": [{"rows": [], "totals": 0}]}, "Send report": {"items": [{"success": True}]}},
                                  [{"node": "Send report", "op": "status", "value": "success"}], [{}])]}
    view = {"nodes": [node("Report form", "FormTrigger", {"title": "Exceptions report", "fields": [{"name": "since", "type": "string", "required": False, "label": "Since"}]}),
                      call("Build report", "build_report"), node("Reply", "Respond", {"statusCode": 200, "body": {"rows": "{{ $json.rows }}"}}, 480)],
            "connections": [{"source_node": "Report form", "target_node": "Build report"}, {"source_node": "Build report", "target_node": "Reply"}],
            "tests": [make_case("t_e0000001", "The report is returned", {"Build report": {"items": [{"rows": [], "totals": 0}]}}, [{"node": "Reply", "op": "status", "value": "success"}],
                                [{"since": "2026-01-01"}])]}
    view["interface"] = generate_interface(view, "Exceptions report", "custom", "some-other-slug")
    log["interface"] = generate_interface(log, "Log an exception", "form", "whatever")
    return {"assign_owner": assign, "build_report": report, "log_exception": log, "weekly_report": weekly, "report_view": view}


def make_plan():
    import asyncio

    from src.services import kg_understand as U
    from tests.unit._kg_support import make_slice

    sl = make_slice()
    u = asyncio.run(U.understand(U.UnderstandContext("proj_a", "t1", sl), REQ + " Anyone can log an exception, each gets an owner based on its category, "
                                 "escalate within 48 hours to the head of operations, and every Monday send the ops managers a report of exceptions.",
                                 answers={"q_shape": "app"}))
    return P.plan_app(u, sl, request=REQ)


def compiled(defs=None):
    return B.compile_bundle("ab_0123456789ab", make_plan(), REQ, defs or definitions())


def issues(raw, profile="generated"):
    return {i.code for i in lint_bundle(parse_bundle(raw), expected_slug=raw["slug"], profile=profile)}


# ── tokens ──

def test_part_references_become_template_tokens_and_the_bundle_passes_the_generated_lint():
    raw = compiled()
    assert raw["format"] == "wf-template/1" and raw["slug"] == "app-0123456789ab" and raw["app"] == {"title": "Operational exceptions", "build_id": "ab_0123456789ab"}
    wf = {w["key"]: w for w in raw["workflows"]}
    assert {k: (w["role"], w["publish"]) for k, w in wf.items()} == {"assign_owner": ("subworkflow", True), "build_report": ("subworkflow", True), "log_exception": ("entry", False),
                                                                     "weekly_report": ("entry", False), "report_view": ("entry", False)}
    log = {n["name"]: n for n in wf["log_exception"]["definition"]["nodes"]}
    assert log["Assign owner"]["parameters"]["workflowId"] == "@tpl:workflow/assign_owner" and log["Save"]["parameters"]["table"] == "@tpl:table/cases"
    assert {n["name"]: n for n in wf["build_report"]["definition"]["nodes"]}["Read cases"]["parameters"]["table"] == "@tpl:table/cases"
    assert [t["name"] for t in raw["tables"]] == ["exception_cases"] and raw["tables"][0]["key"] == "cases"
    assert "@part/" not in repr(raw)
    assert issues(raw) == set()


def test_interfaces_take_the_planned_slug_and_the_report_page_gets_a_table():
    raw = compiled()
    wf = {w["key"]: w["definition"] for w in raw["workflows"]}
    assert wf["log_exception"]["interface"]["slug"] == "log-exception" and wf["report_view"]["interface"]["slug"] == "exceptions-report"
    comps = wf["report_view"]["interface"]["screens"]["result"]["components"]
    assert {"type": "table", "bind": "result.rows"} in comps


def test_a_reused_part_becomes_the_real_workflow_id_not_a_token():
    plan = make_plan()
    for p in plan["parts"]:
        if p["key"] == "assign_owner":
            p["reuse"] = {"capability": "wf:wf_ab12", "label": "Assign owner"}
    defs = {k: v for k, v in definitions().items() if k != "assign_owner"}
    raw = B.compile_bundle("ab_0123456789ab", plan, REQ, defs)
    assert [w["key"] for w in raw["workflows"] if w["role"] == "subworkflow"] == ["build_report"]
    log = next(w for w in raw["workflows"] if w["key"] == "log_exception")["definition"]
    assert {n["name"]: n for n in log["nodes"]}["Assign owner"]["parameters"]["workflowId"] == "wf_ab12"
    assert issues(raw) == set()


def test_compile_refuses_a_missing_part_and_a_leftover_reference():
    defs = definitions()
    del defs["assign_owner"]
    with pytest.raises(B.BundleCompileError) as e:
        B.compile_bundle("ab_0123456789ab", make_plan(), REQ, defs)
    assert [i.code for i in e.value.issues] == ["APP_PART_MISSING"]
    defs = definitions()
    {n["name"]: n for n in defs["log_exception"]["nodes"]}["Save"]["parameters"]["data"]["note"] = "see @part/assign_owner"
    with pytest.raises(B.BundleCompileError) as e:
        B.compile_bundle("ab_0123456789ab", make_plan(), REQ, defs)
    assert [i.code for i in e.value.issues] == ["APP_PART_REF"]


def test_egress_hosts_are_the_literal_hosts_of_http_steps_and_vars_are_declared():
    defs = definitions()
    defs["weekly_report"]["nodes"].append(node("Notify", "HttpRequest", {"method": "POST", "url": "https://hooks.bank.com/notify", "headers": {"X-Key": "{{ $vars.NOTIFY_KEY }}"}}, 720))
    raw = B.compile_bundle("ab_0123456789ab", make_plan(), REQ, defs)
    assert raw["requires"]["egress_hosts"] == ["hooks.bank.com"] and [v["name"] for v in raw["requires"]["variables"]] == ["NOTIFY_KEY"]
    assert issues(raw) == set()                                      # declared, so the generated profile accepts a non-.example host


# ── profiles ──

def test_catalog_profile_still_refuses_what_a_shipped_template_must_not_have():
    defs = definitions()
    defs["weekly_report"]["nodes"].append(node("Notify", "HttpRequest", {"method": "POST", "url": "https://hooks.bank.com/notify"}, 720))
    defs["assign_owner"]["nodes"][1]["parameters"]["fields"].append({"name": "contact", "value": "real.person@bank.com"})
    for k in defs:
        defs[k]["tests"][0]["category"] = "normal"                    # no test of a failure path anywhere
    raw = B.compile_bundle("ab_0123456789ab", make_plan(), REQ, defs)
    assert {"TPL_HOST_NOT_EXAMPLE", "TPL_FAKE_DATA", "TPL_TEST_NO_FAILURE"} <= issues(raw, "catalog")
    assert issues(raw, "generated") == set()
    # a clean `.example` host and a failure test make the catalog profile pass too (nothing else differs between profiles)
    ok = compiled()
    for w in ok["workflows"]:
        w["definition"]["tests"][0]["category"] = "missing"
    ok["ci"]["min_tests_per_workflow"] = 1
    assert issues(ok, "catalog") == set() and issues(ok, "generated") == set()


def test_one_entry_unless_the_bundle_is_an_app():
    raw = compiled()
    assert "TPL_ENTRY_COUNT" not in issues(raw, "generated")
    assert "TPL_ENTRY_COUNT" in issues({k: v for k, v in raw.items() if k != "app"}, "generated")
    assert "TPL_ENTRY_COUNT" in issues({k: v for k, v in raw.items() if k != "app"}, "catalog")


def wfd(raw, key):
    return next(w for w in raw["workflows"] if w["key"] == key)["definition"]


def test_the_generated_profile_keeps_every_secret_and_test_rule():
    raw = compiled()
    bad = copy.deepcopy(raw)
    {n["name"]: n for n in wfd(bad, "assign_owner")["nodes"]}["Pick owner"]["parameters"]["fields"].append({"name": "k", "value": "sk-abcdefghijklmnopqrstuv"})
    assert "TPL_SECRET_VALUE" in issues(bad)
    bad = copy.deepcopy(raw)
    wfd(bad, "assign_owner")["tests"][0]["status"] = "draft"
    assert "TPL_TEST_NOT_APPROVED" in issues(bad)
    bad = copy.deepcopy(raw)
    wfd(bad, "assign_owner")["tests"] = []
    assert "TPL_TEST_COUNT" in issues(bad)
    bad = copy.deepcopy(raw)
    wfd(bad, "assign_owner")["nodes"].append(node("Ask", "Approval", {}, 900))
    assert "TPL_APPROVAL_IN_SUB" in issues(bad)


def test_a_generated_bundle_may_name_the_projects_own_api_spec_by_id():
    defs = definitions()
    defs["weekly_report"]["nodes"].append(node("Call API", "HttpRequest", {"api_ref": {"spec_id": "spec_123", "operation_id": "send"}}, 720))
    raw = B.compile_bundle("ab_0123456789ab", make_plan(), REQ, defs)
    assert issues(raw, "generated") == set() and "TPL_TOKEN_MISUSE" in issues(raw, "catalog")


def test_loaded_template_lints_with_the_generated_profile_and_raises_on_issues():
    lt = B.loaded_template(compiled())
    assert lt.bundle.slug == "app-0123456789ab" and lt.bundle.app.build_id == "ab_0123456789ab" and lt.hash
    bad = compiled()
    wfd(bad, "assign_owner")["tests"][0]["status"] = "draft"
    with pytest.raises(TemplateBundleError):
        B.loaded_template(bad)


# ── virtual siblings ──

def lookups_for(plan, key, built=None):
    children, tables = B.virtuals(plan, key, built or {}, "proj_a")
    return children, StaticLookups(tables=tables, children=children, workflows=set(children), current_project_id="proj_a")


def codes_of(defn, lookups):
    return [(f["code"], f["severity"]) for f in check_workflow(defn, lookups=lookups)["findings"]]


def test_a_stub_sibling_gives_the_checker_the_real_contract():
    plan = make_plan()
    children, lk = lookups_for(plan, "log_exception")
    assert list(children) == ["@part/assign_owner"] and children["@part/assign_owner"].published_version == 1
    log = definitions()["log_exception"]
    found = codes_of(log, lk)
    assert not [c for c in found if c[0] in ("WF006", "WF021")], found
    io = lk.workflow_io("@part/assign_owner", "latest")
    assert io.output_fields == ["owner_role"] and [f["name"] for f in io.input_fields] == ["category"] and io.callable


def test_the_checker_still_catches_a_wrong_call_to_a_virtual_sibling():
    plan = make_plan()
    _children, lk = lookups_for(plan, "log_exception")
    ghost = definitions()["log_exception"]
    {n["name"]: n for n in ghost["nodes"]}["Assign owner"]["parameters"]["workflowId"] = "@part/not_a_part"
    assert ("WF006", "error") in codes_of(ghost, lk)
    unmapped = definitions()["log_exception"]
    {n["name"]: n for n in unmapped["nodes"]}["Assign owner"]["parameters"]["inputs"] = {}
    assert ("WF024", "error") in codes_of(unmapped, lk)                                  # the required `category` input of the contract is unmapped


def test_planned_tables_are_known_to_the_checker_and_a_built_sibling_replaces_its_stub():
    plan = make_plan()
    children, lk = lookups_for(plan, "log_exception")
    assert lk.table_exists("exception_cases") and "status" in [c["name"] for c in lk.table_column_defs("exception_cases")]
    built = definitions()["assign_owner"]
    children, _ = lookups_for(plan, "log_exception", {"assign_owner": built})
    assert children["@part/assign_owner"].draft["nodes"][1]["name"] == "Pick owner"       # the real definition, not the stub
    kids, _ = lookups_for(plan, "assign_owner")
    assert kids == {}                                                                      # nothing to call
    kids, _ = lookups_for(plan, "weekly_report")
    assert list(kids) == ["@part/build_report"]


def test_a_reused_part_gets_no_virtual_child():
    plan = make_plan()
    next(p for p in plan["parts"] if p["key"] == "assign_owner")["reuse"] = {"capability": "wf:wf_ab12", "label": "Assign owner"}
    assert B.virtuals(plan, "log_exception", {}, "proj_a")[0] == {}


def test_a_node_name_that_ends_with_a_question_mark_is_a_node_for_the_test_lints():
    """Stage 8's lint stripped a trailing `?` before looking a test's node up, so a step named "Is payments?" (every If the AI names) was 'not a
    node of this workflow'. Both spellings are accepted now."""
    defs = definitions()
    defs["assign_owner"]["nodes"].append(node("Is payments?", "If", {"condition": "{{ $json.category == 'payments' }}"}, 480))
    defs["assign_owner"]["connections"].append({"source_node": "Inputs", "target_node": "Is payments?"})
    defs["assign_owner"]["tests"][0]["expect"].append({"node": "Is payments?", "port": "true", "op": "count", "value": 1})
    assert "TPL_TEST_UNKNOWN_NODE" not in issues(B.compile_bundle("ab_0123456789ab", make_plan(), REQ, defs))
