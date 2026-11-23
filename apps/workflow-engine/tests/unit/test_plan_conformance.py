"""Plan-conformance gate: the saved project against the approved plan. The comparison is pure (``plan_conformance``); the
DB-backed test drives ``check_plan`` end to end with a fixture plan (``load_approved`` patched). No model."""

from __future__ import annotations

import copy
import uuid

import pytest

from src.services import builder_gates as bg
from src.services import project_knowledge as pk
from src.services.code_module_service import CodeModuleService
from src.services.data_table_service import DataTableService
from src.services.project_service import ProjectService
from tests.unit import wp_b_support
from tests.unit.conftest import anyio_backend, pg, pg_dbname  # noqa: F401

pytestmark = pytest.mark.anyio

COMMON = '''import studio


def audit(event, entity, entity_id, caller, detail=None):
    """Write an audit row"""
    return studio.db.table("audit_log").insert({"event": event})
'''
RUNS = '''from studio import api
from common import audit
import studio


@api.post("/runs", name="start_run")
def start_run(req):
    audit("start", "run", 1, "x")
    return studio.db.table("runs").insert({"test_id": 1})


@api.get("/runs/{id}", name="get_run")
def get_run(req):
    return studio.db.table("runs").get(int(req.params["id"]))
'''
TESTS = '''from studio import api
import studio


@api.get("/tests")
def list_tests(req):
    return studio.db.table("tests").all()
'''
PLAN = wp_b_support.plan_body()
COLS_RUNS = [{"name": "test_id", "type": "integer", "required": True},
             {"name": "status", "type": "string", "required": True, "enum": ["queued", "running", "passed"], "default": "queued"},
             {"name": "run_key", "type": "string", "unique": True}]
ROUTES = [{"method": "POST", "path": "/runs", "name": "start_run", "handler_type": "python", "module": "runs"},
          {"method": "GET", "path": "/runs/{id}", "name": "get_run", "handler_type": "python", "module": "runs"},
          {"method": "GET", "path": "/tests", "name": "list_tests", "handler_type": "python", "module": "tests_library"}]
TABLES = {"runs": {"columns": COLS_RUNS, "rows": 0}, "tests": {"columns": [{"name": "title", "type": "string", "required": True}], "rows": 0},
          "audit_log": {"columns": [{"name": "event", "type": "string"}], "rows": 0}}
SOURCES = {"common": COMMON, "runs": RUNS, "tests_library": TESTS}


def run(plan=PLAN, **over):
    kw = {"sources": SOURCES, "routes": ROUTES, "tables": TABLES, **over}
    return bg.plan_conformance(plan, 1, **kw)


def reasons(rep):
    return sorted((m["route"], m["reason"]) for m in rep["missing"]["routes"])


def test_a_plan_with_no_gaps_is_ok(monkeypatch):
    wp_b_support.install(monkeypatch, PLAN)
    rep = run()
    assert rep["ok"] is True, rep
    assert rep["conformance"]["routes"] == "3/3" and rep["conformance"]["tables"] == "3/3" and rep["conformance"]["columns"] == "5/5"
    assert not any(rep["missing"].values()) and rep["duplicates"] == [] and rep["foreign_writes"] == [] and rep["stale"] == []


def test_a_missing_route_a_stub_only_route_and_a_missing_module_are_reported(monkeypatch):
    wp_b_support.install(monkeypatch, PLAN)
    routes = [dict(ROUTES[0], handler_type="stub", module=None), ROUTES[1]]          # POST /runs is a stub, GET /tests is absent
    rep = run(routes=routes, sources={"common": COMMON, "runs": RUNS})
    assert reasons(rep) == [("GET /tests", "missing"), ("POST /runs", "stub")] and rep["missing"]["modules"] == ["tests_library"]
    assert rep["conformance"]["routes"] == "1/3" and rep["ok"] is False


def test_a_route_in_the_wrong_module_and_an_extra_route(monkeypatch):
    wp_b_support.install(monkeypatch, PLAN)
    routes = [dict(ROUTES[0], module="tests_library"), ROUTES[1], ROUTES[2],
              {"method": "POST", "path": "/secret-admin", "name": "admin", "handler_type": "python", "module": "runs"}]
    rep = run(routes=routes)
    assert reasons(rep) == [("POST /runs", "wrong_module")] and rep["extra"]["routes"] == ["POST /secret-admin"]


def test_a_missing_table_a_missing_column_and_a_differing_column(monkeypatch):
    wp_b_support.install(monkeypatch, PLAN)
    tables = {"runs": {"columns": [{"name": "test_id", "type": "string", "required": True},          # type differs
                                   {"name": "status", "type": "string", "enum": ["queued"]}], "rows": 2}}      # not required, enum short, no run_key
    rep = run(tables=tables)
    assert rep["missing"]["tables"] == ["tests", "audit_log"] and rep["missing"]["columns"] == [{"table": "runs", "column": "run_key"}]
    got = sorted((d["column"], d["what"]) for d in rep["differs"])
    assert got == [("status", "enum"), ("status", "required"), ("test_id", "type")]
    assert rep["drift"] and rep["drift"][0]["table"] == "runs" and rep["ok"] is False


def test_drift_lists_the_ops_still_pending_for_a_column_the_plan_added(monkeypatch):
    wp_b_support.install(monkeypatch, PLAN)
    tables = {**TABLES, "runs": {"columns": COLS_RUNS[:2], "rows": 0}}
    rep = run(tables=tables)
    ops = [o["op"] for d in rep["drift"] for o in d["ops"]]
    assert "add_column" in ops and rep["missing"]["columns"] == [{"table": "runs", "column": "run_key"}]


def test_a_foreign_write_is_reported_unless_the_module_is_a_listed_writer(monkeypatch):
    wp_b_support.install(monkeypatch, PLAN)
    sneaky = TESTS + '\n\ndef x():\n    studio.db.table("runs").update(1, {"status": "passed"})\n'
    rep = run(sources={**SOURCES, "tests_library": sneaky})
    assert [(f["module"], f["table"], f["owner"]) for f in rep["foreign_writes"]] == [("tests_library", "runs", "runs")] and not rep["ok"]
    plan = copy.deepcopy(PLAN)
    plan["data"]["tables"][0]["writers"] = ["tests_library"]
    assert run(plan, sources={**SOURCES, "tests_library": sneaky})["foreign_writes"] == []
    # writing a table you own, or a table the plan lists you as a writer of ('tests' is written by runs), is fine
    ok_writer = RUNS + '\n\ndef y():\n    studio.db.table("tests").update(1, {"title": "t"})\n'
    assert run(sources={**SOURCES, "runs": ok_writer})["foreign_writes"] == []


def test_a_duplicated_helper_is_reported_by_fingerprint_and_by_planned_name(monkeypatch):
    wp_b_support.install(monkeypatch, PLAN)
    copy_of_audit = TESTS + '\n\ndef audit(event, entity, entity_id, caller, detail=None):\n    """again"""\n    return studio.db.table("audit_log").insert({"event": event})\n'
    rep = run(sources={**SOURCES, "tests_library": copy_of_audit})
    assert [(d["name"], d["modules"]) for d in rep["duplicates"]] == [("audit", ["common", "tests_library"])] and not rep["ok"]
    renamed_locals = TESTS + '\n\ndef audit(e, a, b, c, d=None):\n    return studio.db.table("audit_log").insert({"event": e})\n'
    assert run(sources={**SOURCES, "tests_library": renamed_locals})["duplicates"]       # same logic, other names: still a copy
    other_logic = TESTS + '\n\ndef audit(event):\n    return 1\n'
    got = run(sources={**SOURCES, "tests_library": other_logic})["duplicates"]
    assert got and got[0]["name"] == "audit"                                             # a planned helper name redefined, whatever its body


def test_a_helper_called_without_importing_the_shared_module_is_reported(monkeypatch):
    wp_b_support.install(monkeypatch, PLAN)
    rep = run(sources={**SOURCES, "runs": RUNS.replace("from common import audit\n", "")})
    assert [(d["name"], d.get("not_imported")) for d in rep["duplicates"]] == [("audit", True)]


def test_stale_modules_are_listed_with_the_version_they_were_built_against(monkeypatch):
    wp_b_support.install(monkeypatch, PLAN)
    rep = bg.plan_conformance(PLAN, 3, sources=SOURCES, routes=ROUTES, tables=TABLES, built_against={"runs": 2, "common": 3})
    assert [(s["module"], s["built_against"], s["approved"]) for s in rep["stale"]] == [("runs", 2, 3)] and rep["ok"] is False


def test_screen_gaps_and_unbound_apps_are_reported(monkeypatch):
    plan = copy.deepcopy(PLAN)
    plan["apps"] = [{"key": "wb", "name": "WB", "screens": [{"name": "Library"}, {"name": "Runs"}]},
                    {"key": "other", "name": "Other", "screens": [{"name": "Home"}]}]
    wp_b_support.install(monkeypatch, plan)
    apps = [{"key": "wb", "bound": True, "exists": True, "screens": plan["apps"][0]["screens"], "screen_gaps": ["Runs"],
             "unused_routes": ["POST /runs"]},
            {"key": "other", "bound": False, "exists": False, "screens": plan["apps"][1]["screens"]}]
    rep = run(plan, apps=apps)
    assert rep["missing"]["screens"] == [{"app": "wb", "screen": "Runs"}] and rep["missing"]["apps"] == [{"app": "other", "reason": "not_bound"}]
    assert rep["conformance"]["screens"] == "1/3" and rep["apps"] == [{"app": "wb", "unused_routes": ["POST /runs"]}]


def test_unplanned_tables_and_secrets_used_by_code_are_reported_and_a_jobs_integration_must_start_a_job(monkeypatch):
    plan = copy.deepcopy(PLAN)
    plan["integrations"] = [{"key": "jobs", "kind": "jobs", "secrets": ["BANK_USER"]}]
    wp_b_support.install(monkeypatch, plan)
    code = RUNS + '\n\ndef z():\n    studio.db.table("ghosts").all()\n    return studio.secret("OTHER_KEY")\n'
    rep = run(plan, sources={**SOURCES, "runs": code})
    assert rep["unplanned"]["tables"] == [{"table": "ghosts", "modules": ["runs"]}] and rep["unplanned"]["secrets"] == ["OTHER_KEY"]
    assert rep["missing"]["jobs"] == ["jobs"]
    starts = RUNS + '\n\ndef j():\n    return studio.jobs.start({"type": "test-run"})\n'
    assert run(plan, sources={**SOURCES, "runs": starts})["missing"]["jobs"] == []


async def test_check_plan_end_to_end_reports_what_is_missing_and_ok_once_it_is_all_built(pg, monkeypatch):
    monkeypatch.setattr("src.db.session.async_session_factory", pg)
    wp_b_support.install(monkeypatch, PLAN)
    async with pg() as s:
        pid = (await ProjectService(s).create(f"cp-{uuid.uuid4().hex[:8]}")).id
    async with pg() as s:
        p = await ProjectService(s).require(pid)
        svc = CodeModuleService(s)
        await svc.create(p, "common", COMMON, tables=[{"name": "audit_log", "columns": [{"name": "event", "type": "string"}]}])
        await svc.create(p, "runs", RUNS)
    rep = await bg.check_plan(pid)
    assert rep["has_plan"] and rep["plan_version"] == 1 and rep["ok"] is False
    assert rep["missing"]["modules"] == ["tests_library"] and ("GET /tests", "missing") in reasons(rep)
    assert rep["missing"]["tables"] == ["runs", "tests"]
    # the rest of the plan gets built: modules, then the tables (the save creates them from the plan definition)
    async with pg() as s:
        p = await ProjectService(s).require(pid)
        await CodeModuleService(s).create(p, "tests_library", TESTS, tables=[{"name": "tests", "columns": [{"name": "title", "type": "string", "required": True}]}])
        runs_id = next(m["id"] for m in await CodeModuleService(s).list(p) if m["name"] == "runs")
        await CodeModuleService(s).update(p, runs_id, RUNS, tables=[{"name": "runs", "columns": [{"name": "test_id", "type": "integer", "required": True}]}])
    rep = await bg.check_plan(pid)
    assert rep["ok"] is True, rep
    # a module last built against an older plan version is stale
    async with pg() as s:
        await pk.upsert(s, pid, "acceptance", "selftest:runs", title="x", body={"plan_version": 0}, source="design")
    rep = await bg.check_plan(pid)
    assert [x["module"] for x in rep["stale"]] == ["runs"] and rep["ok"] is False
    # no plan, no gate
    wp_b_support.install(monkeypatch, None)
    assert await bg.check_plan(pid) == {"has_plan": False, "ok": True}


def test_a_one_shot_build_that_folds_every_module_into_one_is_complete(monkeypatch):
    wp_b_support.install(monkeypatch, PLAN)
    folded = [dict(r, module="app") for r in ROUTES]                  # one module serves every planned route
    rep = run(routes=folded, sources={"app": RUNS + TESTS})
    assert reasons(rep) == [] and rep["missing"]["modules"] == []
    assert rep["conformance"]["routes"] == "3/3"
    # a folded module whose route is still missing stays missing
    rep = run(routes=folded[:2], sources={"app": RUNS})
    assert reasons(rep) == [("GET /tests", "missing")] and "tests_library" in rep["missing"]["modules"]


def test_a_folded_module_may_write_the_tables_and_hold_the_helpers_of_the_modules_it_serves(monkeypatch):
    wp_b_support.install(monkeypatch, PLAN)
    folded = [dict(r, module="app") for r in ROUTES]
    rep = run(routes=folded, sources={"app": COMMON + RUNS + TESTS})
    assert rep["foreign_writes"] == [] and rep["duplicates"] == [] and rep["ok"] is True, rep
