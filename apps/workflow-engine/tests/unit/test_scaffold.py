"""The plan scaffold (no model), the patch-scope helpers the fast build path uses, and the inline SDK reference."""

from __future__ import annotations

import re

from src.services.backend_templates import patching, prompts, scaffold
from tests.unit.expense_plan_fixture import expense_plan
from tests.unit.office_plan_fixture import office_plan


def test_crud_and_transition_routes_come_from_the_plan_and_the_rest_is_a_todo_list():
    p = expense_plan()
    exp = scaffold.scaffold_module(p, "expenses")
    compile(exp["source"], "expenses.py", "exec")
    assert len(exp["covered"]) == exp["routes"] == 9 and not [t for t in exp["todo"] if t["kind"] == "route"]
    assert 'expenses.transition(req, "rejected", fields=["rejection_reason"], required=["rejection_reason"])' in exp["source"]
    assert "archive_expense" in exp["source"] and 'archive="archived_at"' in exp["source"]
    # the plan's "who" rule is a TODO for the model; the 403 it names is not asked for twice
    rules = [t for t in exp["todo"] if t["kind"] == "rule"]
    assert len(rules) == 1 and "manager who did not submit it" in rules[0]["text"]
    rep = scaffold.scaffold_module(p, "reports")
    assert rep["covered"] == [] and rep["todo"][0]["kind"] == "route" and scaffold.leftovers(rep["source"]) == ["GET /reports/summary"]
    assert scaffold.scaffold_module(p, "common")["todo"] == []
    msg = scaffold.todo_message(exp["todo"])
    assert "ALREADY SAVED" in msg and "patch_module" in msg and "approve_expense" in msg and "studio.resources import error" in msg


def test_the_audit_table_is_written_through_each_resource():
    src = scaffold.scaffold_module(expense_plan(), "categories")["source"]
    assert 'audit=studio.db.table("audit_log")' in src and "audit_log = studio.resource" not in src


def test_a_patch_that_fills_a_todo_leaves_no_leftover():
    sc = scaffold.scaffold_module(office_plan(), "dashboard")
    assert scaffold.leftovers(sc["source"]) == ["GET /dashboard/summary"]
    new = sc["source"].replace('raise HTTPError(501, {"error": "not_built", "message": "GET /dashboard/summary is not built yet"})',
                               "return {}")
    assert new != sc["source"] and scaffold.leftovers(new) == []


def test_touched_routes_follow_hooks_and_helpers():
    old = scaffold.scaffold_module(office_plan(), "requests")["source"]
    decls = [{"method": "GET", "path": "/requests", "function": "list_requests"},
             {"method": "POST", "path": "/requests/{id}/approve", "function": "approve_request"}]
    # a handler that changed
    new = old.replace("def list_requests(req):", "def list_requests(req):\n    q = 1")
    assert patching.touched_routes(old, new, decls) == {"GET /requests"}
    # a hook on the resource touches every route that uses the resource; a new unrelated helper touches none
    hook = old + '\n\n@requests.before_transition\ndef only_managers(row, to, req):\n    return None\n'
    assert patching.touched_routes(old, hook, decls) == {"GET /requests", "POST /requests/{id}/approve"}
    assert patching.touched_routes(old, old + "\n\ndef helper():\n    return 1\n", decls) == set()
    assert patching.touched_routes(old, "def broken(:\n", decls) is None


def test_the_inline_sdk_reference_names_only_what_exists():
    from src.sdk.static import db, jobs, resources

    text = prompts.SDK_REFERENCE
    for name in re.findall(r"\bt\.(\w+)\(", text):
        assert hasattr(db.Table, name), name
    for name in re.findall(r"\bres\.(\w+)\(", text):
        assert hasattr(resources.Resource, name) or name in resources.HOOKS, name
    for name in re.findall(r"@res\.(\w+)", text):
        assert name in resources.HOOKS, name
    for name in re.findall(r"studio\.jobs\.(\w+)\(", text) + ["get", "events", "frame", "artifacts", "artifact", "cancel", "control"]:
        assert hasattr(jobs, name), name
    for op in re.findall(r"\b(\w+_function|add_import|set_constant)\b", text):
        assert op in patching.OP_NAMES
    assert len(text) < 4500


def test_a_planned_build_gets_the_reference_inline_and_the_lean_prompt_stays_lean():
    from src.sdk.generate import catalogue, reference
    from src.services import module_generator as mg
    from tests.unit.test_builder_wp2 import _facts

    lean = mg.build_system_prompt(reference(), catalogue(), _facts(0, 0))
    assert "the scaffold already declares one per table" not in lean      # only a planned build carries the reference
    assert len(prompts.SDK_REFERENCE) < 4500 and len(lean) + len(prompts.SDK_REFERENCE) < 10000
