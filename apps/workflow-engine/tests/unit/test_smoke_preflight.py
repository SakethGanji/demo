"""Integration preflight (static: hosts, secrets, egress, Jobs case ids) and the opt-in smoke run (fake transport, scratch tables).
Real Postgres for the smoke run; no model, no real job."""

from __future__ import annotations

import copy
import uuid

import pytest

from src.services import builder_gates as bg
from src.services.backend_templates import selftest
from src.services.code_module_service import CodeModuleService
from src.services.data_table_service import DataTableService
from src.services.project_service import ProjectError, ProjectService
from tests.unit import wp_b_support
from tests.unit.conftest import anyio_backend, pg, pg_dbname  # noqa: F401

pytestmark = pytest.mark.anyio

PLAN = wp_b_support.plan_body(
    environments=[{"name": "dev", "base_url": "http://localhost:4590", "live": False}],
    secrets=[{"name": "BANK_USER", "environments": ["dev"], "allowed_hosts": ["localhost"]},
             {"name": "BANK_PASS", "environments": ["dev"], "allowed_hosts": ["localhost"]}],
    integrations=[{"key": "jobs", "kind": "jobs", "secrets": ["BANK_USER", "BANK_PASS"], "hosts": ["localhost"],
                   "contract": {"test-run": {"case_id_pattern": "^[A-Za-z0-9_.-]+$"}}}])


def secret(key, hosts, env="dev", type_="secret"):
    return {"key": key, "environment": env, "allowed_hosts": hosts, "type": type_, "overridden": False}


GOOD_ROWS = [secret("BANK_USER", ["localhost"]), secret("BANK_PASS", ["localhost"])]


def find(findings, code):
    return [f for f in findings if f["code"] == code]


def test_a_correct_setup_has_no_findings():
    assert bg.preflight_findings(PLAN, sources={}, secret_rows=GOOD_ROWS, egress=["localhost"]) == []


def test_a_secret_without_allowed_hosts_names_the_host_to_set():
    f = find(bg.preflight_findings(PLAN, sources={}, secret_rows=[secret("BANK_USER", None), GOOD_ROWS[1]], egress=[]), "secret_no_allowed_hosts")
    assert len(f) == 1 and f[0]["where"] == "BANK_USER (dev)" and "set allowed_hosts on BANK_USER to `localhost`" in f[0]["fix"]
    assert f[0]["severity"] == "error" and f[0]["environment"] == "dev"


def test_a_host_with_a_port_is_flagged_with_the_hostname_to_use():
    f = find(bg.preflight_findings(PLAN, sources={}, secret_rows=[secret("BANK_USER", ["localhost:4590"]), GOOD_ROWS[1]], egress=[]), "host_has_port")
    assert len(f) == 1 and f[0]["fix"] == "set allowed_hosts on BANK_USER to `localhost` (hostname only, no :4590)"


def test_a_secret_that_may_not_go_to_the_environments_host_is_flagged():
    f = find(bg.preflight_findings(PLAN, sources={}, secret_rows=[secret("BANK_USER", ["bank.example"]), GOOD_ROWS[1]], egress=[]), "host_not_allowed")
    assert len(f) == 1 and "add `localhost` to allowed_hosts on BANK_USER" in f[0]["fix"]


def test_a_missing_secret_in_the_environment_is_flagged():
    f = find(bg.preflight_findings(PLAN, sources={}, secret_rows=[GOOD_ROWS[0], secret("BANK_PASS", ["localhost"], env="uat")], egress=[]), "secret_missing")
    assert [x["where"] for x in f] == ["BANK_PASS (dev)"]


def test_a_host_outside_the_egress_allowlist_is_flagged_and_an_empty_allowlist_allows_all():
    f = find(bg.preflight_findings(PLAN, sources={}, secret_rows=GOOD_ROWS, egress=["api.example.com"]), "egress_blocked")
    assert len(f) == 1 and "add `localhost` to the project's egress allowlist" in f[0]["fix"]
    assert find(bg.preflight_findings(PLAN, sources={}, secret_rows=GOOD_ROWS, egress=[]), "egress_blocked") == []
    assert find(bg.preflight_findings(PLAN, sources={}, secret_rows=GOOD_ROWS, egress=["*.example.com", "localhost"]), "egress_blocked") == []


def test_an_environment_without_a_usable_base_url_is_flagged():
    plan = copy.deepcopy(PLAN)
    plan["environments"][0]["base_url"] = ""
    assert find(bg.preflight_findings(plan, sources={}, secret_rows=GOOD_ROWS, egress=[]), "env_no_base_url")


CASE_SRC = '''from studio import api
import studio


@api.post("/runs")
def start(req):
    run_key, case_key = "r1", "c1"
    cases = [{"id": CASE_ID, "kind": "ui", "title": "t", "steps": []}]
    return studio.jobs.start({"type": "test-run", "cases": cases})
'''


@pytest.mark.parametrize("case_id,bad", [
    ('f"{run_key}#{case_key}"', True),
    ('"TC#1"', True),
    ('"TC 1"', True),
    ('f"{run_key}-{case_key}"', False),
    ('"TC-1.a_b"', False),
    ("str(case_key)", False),               # computed: never guessed at
])
def test_case_ids_with_characters_the_service_rejects_are_flagged_statically(case_id, bad):
    f = find(bg.preflight_findings(PLAN, sources={"runs": CASE_SRC.replace("CASE_ID", case_id)}, secret_rows=GOOD_ROWS, egress=[]), "case_id_invalid")
    assert bool(f) is bad
    if bad:
        assert "#" in f[0]["message"] or "'" in f[0]["message"] or " " in f[0]["message"]
        assert f[0]["where"].startswith("runs.py line ") and "letters, digits" in f[0]["fix"]


def test_a_template_variable_is_not_a_missing_secret_but_a_secret_the_plan_lacks_is():
    src = '''from studio import api
import studio


@api.post("/runs")
def start(req):
    steps = [{"action": "fill", "value": "{{tok}}"}, {"action": "fill", "value": "{{BANK_PASS}}"}]
    return {"u": studio.secret("BANK_USER"), "steps": steps, "x": studio.secret("NOT_PLANNED")}
'''
    f = bg.preflight_findings(PLAN, sources={"runs": src}, secret_rows=GOOD_ROWS, egress=[])
    assert [(x["code"], x["where"]) for x in f] == [("secret_not_in_plan", "NOT_PLANNED")]
    assert not any("tok" in x["message"] for x in f)


def test_a_plan_secret_without_hosts_is_a_warning():
    plan = copy.deepcopy(PLAN)
    plan["secrets"][0]["allowed_hosts"] = []
    f = find(bg.preflight_findings(plan, sources={}, secret_rows=GOOD_ROWS, egress=[]), "plan_secret_no_hosts")
    assert len(f) == 1 and f[0]["severity"] == "warning"


def test_rewrite_for_scratch_real_mode_keeps_studio_jobs_and_still_maps_tables():
    src = 'import studio\n\n\ndef f():\n    studio.db.table("runs").all()\n    return studio.jobs.start({"type": "test-run"})\n'
    fake = selftest.rewrite_for_scratch(src, {"runs": "runs__st1"}, {})
    real = selftest.rewrite_for_scratch(src, {"runs": "runs__st1"}, {"mode": "real"})
    assert "_st_jobs.start" in fake and "class _StJobs" in fake
    assert "studio.jobs.start" in real and "_st_jobs" not in real and "class _StJobs" not in real and '"runs__st1"' in real
    assert real.count("\n") == src.count("\n")                                    # line numbers still match the original


# ── classification ──────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("status,body,code,expect", [
    (502, {"error": {"code": "secret_not_bound", "message": "BANK_USER is not bound"}}, "secret_not_bound", "secrets"),
    (403, {"error": "host_not_allowed: localhost"}, "host_not_allowed", "allowed_hosts"),
    (400, {"error": "cases.0.id: must match ^[A-Za-z0-9_.-]+$"}, "upstream_validation", "verbatim"),
    (504, {"error": "upstream timeout"}, "timeout", "reachable"),
    (500, {"error": "boom"}, "error", "message"),
])
def test_smoke_errors_are_classified_with_fix_text(status, body, code, expect):
    got = bg.classify_smoke_error(status, body)
    assert got["code"] == code and expect in got["fix"] and got["message"]
    if code == "upstream_validation":
        assert "must match ^[A-Za-z0-9_.-]+$" in got["message"]


def test_until_grammar():
    assert bg._until("status in [passed,failed,blocked]", {"status": "failed"})
    assert not bg._until("status in [passed,failed]", {"status": "running"})
    assert bg._until("run.status == done", {"run": {"status": "done"}}) and not bg._until("status == done", {})


# ── the smoke run ───────────────────────────────────────────────────────────────────────────────────

RUNS_SRC = '''from studio import api
import studio


@api.post("/runs", name="start_run")
def start_run(req):
    return studio.db.table("runs").insert({"status": "queued"})


@api.get("/runs/{id}", name="get_run")
def get_run(req):
    return studio.db.table("runs").get(int(req.params["id"]))
'''
SMOKE = {"environment": "dev", "start": "POST /runs", "body": {"test_ids": ["{tests_id}"]}, "poll": "GET /runs/{id}",
         "until": "status in [passed,failed,blocked]", "expect": {"status": 200}, "timeout_s": 0.3,
         "fixtures": {"tests": [{"title": "read-only case"}]}}


async def smoke_project(pg, monkeypatch, smoke=SMOKE):
    plan = copy.deepcopy(PLAN)
    plan["integrations"][0]["smoke"] = smoke
    wp_b_support.install(monkeypatch, plan)
    monkeypatch.setattr("src.services.module_generator.async_session_factory", pg)
    async with pg() as s:
        p = await ProjectService(s).create(f"smk-{uuid.uuid4().hex[:8]}")
        svc = DataTableService(s)
        await svc.create_table(p, {"name": "tests", "columns": [{"name": "title", "type": "string"}]})
        await svc.create_table(p, {"name": "runs", "columns": [{"name": "status", "type": "string"}]})
        await svc.insert_rows(await svc.table_by_name(p.id, "tests"), [{"title": "real"}])
        await CodeModuleService(s).create(p, "runs", RUNS_SRC)
        return p.id


class FakeBroker:
    """The stand-in for the sandboxed route call: records calls, answers from a script, writes nothing to the DB."""
    def __init__(self, script):
        self.script, self.calls = list(script), []

    async def __call__(self, method, path, query, body, read_only, headers):
        self.calls.append((method, path, body))
        r = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        return r


@pytest.fixture
def secrets_ok(monkeypatch):
    from src.services.secret_service import SecretService

    async def effective(self, project, environment=None):
        return GOOD_ROWS
    monkeypatch.setattr(SecretService, "effective", effective)


async def test_smoke_passes_when_the_job_finishes_and_never_touches_real_tables(pg, monkeypatch, secrets_ok):
    pid = await smoke_project(pg, monkeypatch)
    fake = FakeBroker([{"status": 200, "body": {"id": 7, "job_id": "j1", "status": "running"}},
                       {"status": 200, "body": {"id": 7, "status": "running"}},
                       {"status": 200, "body": {"id": 7, "status": "passed"}}])
    out = await bg.smoke_run(pid, "jobs", call=fake, poll_interval=0.01)
    assert out["ok"] is True and out["outcome"] == "passed" and out["started"] is True and out["job"] == 7
    assert [c[:2] for c in fake.calls] == [("POST", "/runs"), ("GET", "/runs/7"), ("GET", "/runs/7")]
    assert isinstance(fake.calls[0][2]["test_ids"][0], int)                      # {tests_id} was filled from the seeded fixture row
    assert out["scratch"]["real_tables_changed"] == [] and out["scratch"]["leftover"] == []
    async with pg() as s:
        svc = DataTableService(s)
        names = [t["name"] for t in await svc.list_tables(await ProjectService(s).require(pid))]
        assert sorted(names) == ["runs", "tests"]                                # scratch copies dropped
        assert await svc.row_count((await svc.table_by_name(pid, "tests")).id) == 1


async def test_smoke_classifies_an_unbound_secret_at_start(pg, monkeypatch, secrets_ok):
    pid = await smoke_project(pg, monkeypatch)
    fake = FakeBroker([{"status": 502, "body": {"error": {"code": "secret_not_bound", "message": "BANK_USER is not bound"}},
                        "error_code": "broker_error"}])
    out = await bg.smoke_run(pid, "jobs", call=fake)
    assert out["ok"] is False and out["outcome"] == "failed" and out["error"]["code"] == "secret_not_bound"
    assert "secrets" in out["error"]["fix"] and out["steps"][0]["ok"] is False and len(fake.calls) == 1
    assert out["scratch"]["real_tables_changed"] == []


async def test_smoke_times_out_when_the_job_never_finishes(pg, monkeypatch, secrets_ok):
    pid = await smoke_project(pg, monkeypatch)
    fake = FakeBroker([{"status": 200, "body": {"id": 3}}, {"status": 200, "body": {"id": 3, "status": "running"}}])
    out = await bg.smoke_run(pid, "jobs", call=fake, poll_interval=0.05)
    assert out["outcome"] == "timeout" and out["error"]["code"] == "timeout" and out["ok"] is False
    assert out["scratch"]["leftover"] == [] and out["scratch"]["real_tables_changed"] == []


async def test_smoke_reports_the_upstream_validation_error_verbatim(pg, monkeypatch, secrets_ok):
    pid = await smoke_project(pg, monkeypatch)
    fake = FakeBroker([{"status": 400, "body": {"error": "cases.0.id: must match ^[A-Za-z0-9_.-]+$"}}])
    out = await bg.smoke_run(pid, "jobs", call=fake)
    assert out["error"]["code"] == "upstream_validation" and "cases.0.id: must match" in out["error"]["message"]


async def test_smoke_stops_before_starting_a_job_when_the_preflight_fails(pg, monkeypatch):
    from src.services.secret_service import SecretService

    async def effective(self, project, environment=None):
        return [secret("BANK_USER", ["localhost:4590"]), GOOD_ROWS[1]]
    monkeypatch.setattr(SecretService, "effective", effective)
    pid = await smoke_project(pg, monkeypatch)
    fake = FakeBroker([{"status": 200, "body": {"id": 1}}])
    out = await bg.smoke_run(pid, "jobs", call=fake)
    assert out["outcome"] == "preflight_failed" and out["started"] is False and fake.calls == []
    assert out["findings"][0]["code"] == "host_has_port"


async def test_smoke_refuses_unknown_integration_missing_smoke_and_no_plan(pg, monkeypatch, secrets_ok):
    pid = await smoke_project(pg, monkeypatch)
    with pytest.raises(ProjectError) as e:
        await bg.smoke_run(pid, "nope")
    assert e.value.code == "integration_not_found"
    with pytest.raises(ProjectError) as e:
        await bg.smoke_run(pid, "jobs", environment="prod")
    assert e.value.code == "unknown_environment"
    wp_b_support.install(monkeypatch, wp_b_support.plan_body(integrations=[{"key": "jobs", "kind": "jobs"}]))
    with pytest.raises(ProjectError) as e:
        await bg.smoke_run(pid, "jobs")
    assert e.value.code == "no_smoke"
    wp_b_support.install(monkeypatch, None)
    with pytest.raises(ProjectError) as e:
        await bg.smoke_run(pid, "jobs")
    assert e.value.code == "no_plan"


async def test_check_plan_shows_the_smoke_state_and_record_check_stores_it(pg, monkeypatch, secrets_ok):
    monkeypatch.setattr("src.db.session.async_session_factory", pg)
    pid = await smoke_project(pg, monkeypatch)
    assert (await bg.check_plan(pid))["smoke"] == {"jobs": "not_run"}
    out = await bg.smoke_run(pid, "jobs", call=FakeBroker([{"status": 400, "body": {"error": "bad"}}]))
    async with pg() as s:
        await bg.record_check(s, pid, "smoke", "jobs", out)
    assert (await bg.check_plan(pid))["smoke"] == {"jobs": "failed"}
    out = await bg.smoke_run(pid, "jobs", call=FakeBroker([{"status": 200, "body": {"id": 1, "status": "passed"}}]), poll_interval=0.01)
    async with pg() as s:
        await bg.record_check(s, pid, "smoke", "jobs", out)
    assert (await bg.check_plan(pid))["smoke"] == {"jobs": "passed"}


async def test_plan_check_and_smoke_endpoints(pg, monkeypatch, secrets_ok):
    import httpx
    from fastapi import FastAPI

    from src.core.dependencies import get_db_session
    from src.routes import builder_gates as gates_routes

    monkeypatch.setattr("src.db.session.async_session_factory", pg)
    pid = await smoke_project(pg, monkeypatch)
    app = FastAPI()
    app.include_router(gates_routes.router)

    async def session():
        async with pg() as s:
            yield s
    app.dependency_overrides[get_db_session] = session
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(f"/projects/{pid}/plan/check")
        assert r.status_code == 200 and r.json()["has_plan"] is True and r.json()["smoke"] == {"jobs": "not_run"}
        r = await c.post(f"/projects/{pid}/smoke", json={"integration": "nope"})
        assert r.status_code == 404 and r.json()["detail"]["code"] == "integration_not_found"
