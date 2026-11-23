"""Project-level gate (POST /projects/{p}/check): a scenario across SEVERAL modules in one scratch copy of all tables, seeded
fixtures, a realistic fake Jobs service, and the standing job flow. Includes the round-3 bug: an approve path that compared
the Jobs-normalised steps (extra ``"state": "visible"``) with the stored steps answered 409 on every approval, which no
per-module self-test could see because it needs both modules.

Also the static credential provenance check. Real Postgres (throwaway database) and the real sandbox; no model."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from src.db.models import DataTableModel
from src.sandbox.backend import availability
from src.services import builder_gates as bg
from src.services.backend_templates import checks, selftest
from src.services.code_module_service import CodeModuleService
from src.services.data_table_service import DataTableService
from src.services.project_service import ProjectService
from tests.unit import wp_b_support
from tests.unit.conftest import anyio_backend, pg, pg_dbname  # noqa: F401

pytestmark = pytest.mark.anyio

needs_sandbox = pytest.mark.skipif(not availability()[0], reason="sandbox backend unavailable")

LIB = '''from studio import api, HTTPError
import studio


@api.get("/cases/{id}", name="get_case")
def get_case(req):
    """Read one case. Returns the row"""
    c = studio.db.table("cases").get(int(req.params["id"]))
    if c is None:
        raise HTTPError(404, {"error": "not found"})
    return c
'''

RUNS = '''from studio import api, HTTPError
import studio


def _canon(steps):
    return [{k: v for k, v in s.items() if k != "state"} for s in steps]


@api.post("/runs", name="start_run")
def start_run(req):
    """Start a run for one case. Body {case_id}. Returns the run"""
    b = req.json() or {}
    case = studio.db.table("cases").get(b.get("case_id")) if b.get("case_id") else None
    if case is None:
        raise HTTPError(400, {"error": "case_id required"})
    job = studio.jobs.start({"type": "test-run", "label": "check", "target": {"base_url": "http://127.0.0.1:1"}, "policy": {"forbidden_names": []}, "cases": [
        {"id": str(case["id"]), "kind": "ui", "title": case["title"], "steps": case["steps"]}]})
    return studio.db.table("runs").insert({"job_id": job["job_id"], "case_id": case["id"], "status": "running", "synced": False})


@api.post("/runs/sync-pending", name="sync_pending")
def sync_pending(req):
    """Finish every unsynced run. Idempotent. Returns {synced}"""
    n = 0
    for run in studio.db.table("runs").all({"synced": False}):
        j = studio.jobs.get(run["job_id"])
        studio.db.table("runs").update(run["id"], {"status": j["status"], "synced": True})
        n += 1
    return {"synced": n}


@api.get("/runs/{id}", name="get_run")
def get_run(req):
    """One run with its results. Returns {run, results}"""
    run = studio.db.table("runs").get(int(req.params["id"]))
    if run is None:
        raise HTTPError(404, {"error": "not found"})
    j = studio.jobs.get(run["job_id"])
    return {"run": run, "results": [{"id": run["id"], "case_id": run["case_id"], "status": r["status"]} for r in j["results"]]}


@api.post("/results/{id}/triage", name="start_triage")
def start_triage(req):
    """Triage a failed result. Returns the finding"""
    run = studio.db.table("runs").get(int(req.params["id"]))
    if run is None:
        raise HTTPError(404, {"error": "not found"})
    job = studio.jobs.start({"type": "triage", "job_id": run["job_id"], "case_id": str(run["case_id"])})
    return studio.db.table("findings").insert({"case_id": run["case_id"], "triage_job_id": job["job_id"], "status": "triaging"})


@api.get("/findings/{id}", name="get_finding")
def get_finding(req):
    """One finding; a finished triage job is folded in. Returns the finding"""
    f = studio.db.table("findings").get(int(req.params["id"]))
    if f is None:
        raise HTTPError(404, {"error": "not found"})
    if f["status"] == "triaging":
        out = studio.jobs.get(f["triage_job_id"])["output"]["proposed_fix"]
        f = studio.db.table("findings").update(f["id"], {"status": "pending", "steps_before": out["steps_before"],
                                                          "steps_after": out["steps_after"]})
    return f


@api.post("/findings/{id}/decision", name="decide_finding")
def decide_finding(req):
    """Body {decision: approve, actor}. approve patches the case with the proposed steps. 409 when the case moved on"""
    f = studio.db.table("findings").get(int(req.params["id"]))
    b = req.json() or {}
    if f is None or f["status"] != "pending" or b.get("decision") != "approve":
        raise HTTPError(409, {"error": "not pending"})
    case = studio.db.table("cases").get(f["case_id"])
    STALE_CHECK
    studio.db.table("cases").update(case["id"], {"steps": _canon(f["steps_after"])})
    studio.db.table("findings").update(f["id"], {"status": "accepted"})
    return {"applied": True}
'''
# the round-3 bug: Jobs-normalised steps (with "state") compared with the stored ones
BUGGY_RUNS = RUNS.replace("STALE_CHECK", 'if case["steps"] != f["steps_before"]:\n        raise HTTPError(409, {"error": "stale case"})')
FIXED_RUNS = RUNS.replace("STALE_CHECK", 'if _canon(case["steps"]) != _canon(f["steps_before"]):\n        raise HTTPError(409, {"error": "stale case"})')
STEPS = [{"action": "click", "target": {"role": "button", "name": "Go"}}, {"action": "assert_text", "target": {"text": "Done"}}]
FIXTURES = {"cases": [{"title": "Go flow", "steps": STEPS}]}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    from cryptography.fernet import Fernet

    from src.core.config import settings
    from src.engine.node_registry import node_registry, register_all_nodes

    if not node_registry.list():
        register_all_nodes()
    monkeypatch.setattr(settings, "encryption_key", Fernet.generate_key().decode(), raising=False)


async def seed(pg, monkeypatch, runs_source):
    monkeypatch.setattr("src.services.module_generator.async_session_factory", pg)
    monkeypatch.setattr("src.db.session.async_session_factory", pg)
    async with pg() as s:
        p = await ProjectService(s).create(f"bgp-{uuid.uuid4().hex[:8]}")
        svc = DataTableService(s)
        await svc.create_table(p, {"name": "cases", "columns": [{"name": "title", "type": "string", "required": True},
                                                                  {"name": "steps", "type": "json"}]})
        await svc.create_table(p, {"name": "runs", "columns": [{"name": "job_id", "type": "string"}, {"name": "case_id", "type": "integer"},
                                                                 {"name": "status", "type": "string"}, {"name": "synced", "type": "boolean"}]})
        await svc.create_table(p, {"name": "findings", "columns": [
            {"name": "case_id", "type": "integer"}, {"name": "triage_job_id", "type": "string"}, {"name": "status", "type": "string"},
            {"name": "steps_before", "type": "json"}, {"name": "steps_after", "type": "json"}]})
        await CodeModuleService(s).create(p, "qa_library", LIB)
        await CodeModuleService(s).create(p, "qa_runs", runs_source)
        return p.id


async def table_names(pg, pid):
    async with pg() as s:
        rows = (await s.execute(select(DataTableModel.name).where(DataTableModel.project_id == pid))).scalars().all()
        return sorted(rows)


# ── the round-3 blind spot ──────────────────────────────────────────────────────────────────────────

@needs_sandbox
async def test_standing_scenario_catches_the_normalised_steps_409(pg, monkeypatch):
    pid = await seed(pg, monkeypatch, BUGGY_RUNS)
    out = await bg.check_project(pid, fixtures=FIXTURES, baseline=False)
    st = out["self_test"]
    assert st["standing"]["ran"] is True, st["standing"]
    bad = [r for r in st["checks"] if r.get("ok") is False]
    assert [r["step"] for r in bad] == ["standing:approve", "standing:case_after"], bad      # 409, and the case never changed
    assert "409" in bad[0]["failure"] and "did not change" in bad[1]["failure"] and out["ok"] is False
    assert await table_names(pg, pid) == ["cases", "findings", "runs"]         # scratch copies dropped
    assert out["scratch"]["leftover"] == [] and out["scratch"]["real_tables_changed"] == []


@needs_sandbox
async def test_standing_scenario_passes_on_the_fixed_pair_and_asserts_the_case_changed(pg, monkeypatch):
    pid = await seed(pg, monkeypatch, FIXED_RUNS)
    out = await bg.check_project(pid, fixtures=FIXTURES, baseline=False)
    st = out["self_test"]
    assert st["failed"] == 0, [r for r in st["checks"] if r.get("ok") is False]
    steps = [r["step"] for r in st["checks"]]
    assert steps[0] == "standing:start_run" and steps[-1] == "standing:case_after" and "standing:approve" in steps
    assert out["ok"] is True and st["seeds"]["fixtures"] == {"cases": 1}
    # real tables untouched: no run, no finding was written to the project's own data
    async with pg() as s:
        svc = DataTableService(s)
        for name in ("runs", "findings"):
            assert await svc.row_count((await svc.table_by_name(pid, name)).id) == 0
    assert await table_names(pg, pid) == ["cases", "findings", "runs"]


@needs_sandbox
async def test_a_scenario_may_call_routes_of_several_modules_and_clone_real_rows(pg, monkeypatch):
    pid = await seed(pg, monkeypatch, FIXED_RUNS)
    async with pg() as s:
        svc = DataTableService(s)
        await svc.insert_rows(await svc.table_by_name(pid, "cases"), [{"title": "real", "steps": STEPS}])
    scenario = [
        {"id": "start", "call": "POST /runs", "body": {"case_id": "{cases_id}"}, "expect": {"status": 200, "has": ["job_id"]},
         "save": {"rid": "id"}},
        {"id": "case", "call": "GET /cases/{cases_id}", "expect": {"status": 200, "equals": {"title": "real"}}},
        {"id": "run", "call": "GET /runs/{rid}", "expect": {"status": 200, "has": ["run.job_id"]}},
    ]
    out = await bg.check_project(pid, scenario=scenario, clone_rows=3, standing=False, baseline=False)
    assert out["self_test"]["failed"] == 0, out["self_test"]["checks"]
    assert out["self_test"]["seeds"]["cloned"] == {"cases": 1}
    async with pg() as s:                                             # the real case row is where it was
        svc = DataTableService(s)
        assert await svc.row_count((await svc.table_by_name(pid, "cases")).id) == 1


async def test_fake_jobs_returns_the_documented_shapes():
    ns: dict = {}
    exec(selftest.fake_jobs_source({"fail": "all"}), ns)   # noqa: S102 - the fake is plain Python text
    jobs = ns["_st_jobs"]
    run = jobs.start({"type": "test-run", "cases": [{"id": "TC-1", "kind": "ui", "steps": STEPS}, {"id": "TC-2", "kind": "api", "requests": []}]})
    got = jobs.get(run["job_id"])
    assert got["status"] == "failed" and got["progress"]["total"] == 2
    r = got["results"][0]
    assert r["status"] == "failed" and r["failed_step"] == {"index": 0, "step": STEPS[0], "description": "click Go"} and r["error"]
    ev = jobs.events(run["job_id"], 0)
    assert {e["type"] for e in ev["events"]} >= {"case_start", "step_fail", "case_end"} and ev["next"] == len(ev["events"])
    tri = jobs.get(jobs.start({"type": "triage", "job_id": run["job_id"], "case_id": "TC-1"})["job_id"])["output"]
    fix = tri["proposed_fix"]
    assert tri["verdict"] == "app_changed" and fix["steps_before"][0]["state"] == "visible" and "state" not in STEPS[0]
    assert fix["steps_after"][0]["target"]["name"] == "Go (renamed)" and fix["steps_after"][0]["state"] == "visible"
    none = ns["_StJobs"]()
    none.CFG = {"fail": "none"}
    assert none.get(none.start({"type": "test-run", "cases": [{"id": "A", "steps": []}]})["job_id"])["status"] == "passed"


def test_deep_save_paths_and_not_equals():
    body = {"a": {"results": [{"id": 7, "case_id": 3}]}, "steps": [1]}
    assert selftest.dig(body, "~results.0.id") == 7
    assert selftest.dig_any(body, "nope|~case_id") == 3
    assert selftest.check_expect({"not_equals": {"steps": [1]}}, 200, body, {}) is not None
    assert selftest.check_expect({"not_equals": {"steps": [2]}}, 200, body, {}) is None


# ── the plan gate ────────────────────────────────────────────────────────────────────────────────────

def _qa_plan(extra_route: bool = False) -> dict:
    routes = [{"key": k, "module": m} for k, m in (("GET /cases/{id}", "qa_library"), ("POST /runs", "qa_runs"),
                                                   ("POST /runs/sync-pending", "qa_runs"), ("GET /runs/{id}", "qa_runs"),
                                                   ("POST /results/{id}/triage", "qa_runs"), ("GET /findings/{id}", "qa_runs"),
                                                   ("POST /findings/{id}/decision", "qa_runs"))]
    if extra_route:
        routes.append({"key": "GET /export", "module": "qa_runs"})
    cols = {"cases": [{"name": "title", "type": "string", "required": True}, {"name": "steps", "type": "json"}],
            "runs": [{"name": "job_id", "type": "string"}, {"name": "case_id", "type": "integer"}, {"name": "status", "type": "string"},
                     {"name": "synced", "type": "boolean"}],
            "findings": [{"name": "case_id", "type": "integer"}, {"name": "triage_job_id", "type": "string"}, {"name": "status", "type": "string"},
                         {"name": "steps_before", "type": "json"}, {"name": "steps_after", "type": "json"}]}
    owner = {"cases": "qa_library", "runs": "qa_runs", "findings": "qa_runs"}
    return wp_b_support.plan_body(
        modules=[{"key": "qa_library", "kind": "routes"}, {"key": "qa_runs", "kind": "routes"}], routes=routes, apps=[],
        data={"tables": [{"name": n, "owner": owner[n], "writers": ["qa_runs"] if n == "cases" else [], "columns": c} for n, c in cols.items()]})


async def test_ok_is_false_when_the_plan_check_fails_even_if_every_scenario_passes(pg, monkeypatch):
    pid = await seed(pg, monkeypatch, FIXED_RUNS)
    wp_b_support.install(monkeypatch, _qa_plan(extra_route=True))           # the plan promises GET /export, which was never built
    out = await bg.check_project(pid, fixtures=FIXTURES, baseline=False)
    assert out["plan"]["has_plan"] and out["plan"]["ok"] is False
    assert [m["route"] for m in out["plan"]["missing"]["routes"]] == ["GET /export"] and out["ok"] is False
    assert out["self_test"]["failed"] == 0 or not availability()[0]           # the scenarios themselves are not what failed


@needs_sandbox
async def test_ok_requires_the_plan_to_be_met_and_is_green_when_it_is(pg, monkeypatch):
    pid = await seed(pg, monkeypatch, FIXED_RUNS)
    wp_b_support.install(monkeypatch, _qa_plan())
    out = await bg.check_project(pid, fixtures=FIXTURES, baseline=False)
    assert out["plan"]["ok"] is True, out["plan"]
    assert out["self_test"]["failed"] == 0 and out["ok"] is True
    wp_b_support.install(monkeypatch, None)
    assert "plan" not in await bg.check_project(pid, fixtures=FIXTURES, baseline=False)


# ── credential provenance ───────────────────────────────────────────────────────────────────────────

def _creds(body_code: str) -> list[str]:
    src = ("from studio import api\nimport studio\n\n\n@api.post(\"/go\")\ndef go(req):\n    b = req.json() or {}\n"
           + "".join(f"    {ln}\n" for ln in body_code.strip().splitlines()))
    return [p["code"] for p in checks.job_start_problems(src)]


ALLOWED = [
    'creds = {"username": studio.secret("QA_USER"), "password": studio.secret("QA_PASS")}\nstudio.jobs.start({"type": "test-run", "credentials": creds, "cases": []})',
    'studio.jobs.start({"type": "test-run", "credentials": {"username": studio.secret("U")}, "cases": []})',
    # computed: a comprehension, update(), item assignment, ** unpacking, helper function, derived strings
    'creds = {k.lower(): studio.secret(k) for k in ["QA_USER", "QA_PASS"]}\nstudio.jobs.start({"type": "test-run", "credentials": creds})',
    'creds = {}\ncreds.update({"username": studio.secret("U")})\ncreds["password"] = studio.secret("P")\nstudio.jobs.start({"type": "test-run", "credentials": creds})',
    'base = {"username": studio.secret("U")}\ncreds = {**base, "password": studio.secret("P").strip()}\nstudio.jobs.start({"type": "test-run", "credentials": creds})',
    'pw = studio.secret("P")\ncreds = dict(username=studio.secret("U"), password=pw, token=f"Bearer {pw}")\nstudio.jobs.start({"type": "test-run", "credentials": creds})',
    'creds = _creds()\nstudio.jobs.start({"type": "test-run", "credentials": creds})',
]
REJECTED = [
    'studio.jobs.start({"type": "test-run", "credentials": {"username": "admin", "password": "hunter2"}})',
    'studio.jobs.start({"type": "test-run", "credentials": {"username": studio.secret("U"), "password": b["password"]}})',
    'creds = {"username": studio.secret("U")}\ncreds["password"] = b.get("password")\nstudio.jobs.start({"type": "test-run", "credentials": creds})',
    'creds = {}\ncreds.update(b.get("creds") or {})\nstudio.jobs.start({"type": "test-run", "credentials": creds})',
    'creds = {"username": studio.secret("U"), "password": "x" + str(b.get("p"))}\nstudio.jobs.start({"type": "test-run", "credentials": creds})',
    'creds = {**b, "username": studio.secret("U")}\nstudio.jobs.start({"type": "test-run", "credentials": creds})',
    'studio.jobs.start({"type": "test-run", "credentials": b})',
    'creds = {"username": studio.secret("U"), "password": "literal"}\nstudio.jobs.start({"type": "test-run", "credentials": creds})',
    'creds = _creds_from_request(b)\nstudio.jobs.start({"type": "test-run", "credentials": creds})',
]


@pytest.mark.parametrize("code", ALLOWED)
def test_credentials_derived_from_studio_secret_are_allowed(code):
    extra = '\n\ndef _creds():\n    return {"username": studio.secret("U"), "password": studio.secret("P")}\n'
    src = ("from studio import api\nimport studio\n\n\n@api.post(\"/go\")\ndef go(req):\n    b = req.json() or {}\n"
           + "".join(f"    {ln}\n" for ln in code.strip().splitlines()) + extra)
    probs = [p["code"] for p in checks.all_problems(src) if p["code"] in ("insecure_credentials", "browser_credentials")]
    assert probs == [], (code, probs)


@pytest.mark.parametrize("code", REJECTED)
def test_literal_or_request_derived_credentials_are_rejected(code):
    extra = '\n\ndef _creds_from_request(b):\n    return {"username": b.get("u"), "password": b.get("p")}\n'
    src = ("from studio import api\nimport studio\n\n\n@api.post(\"/go\")\ndef go(req):\n    b = req.json() or {}\n"
           + "".join(f"    {ln}\n" for ln in code.strip().splitlines()) + extra)
    assert "insecure_credentials" in [p["code"] for p in checks.job_start_problems(src)], code
