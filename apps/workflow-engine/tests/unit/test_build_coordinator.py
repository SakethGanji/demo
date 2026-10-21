"""The build coordinator on a throwaway Postgres, through the builds API in-process. The builders (generator, save path,
app builder, gates) are replaced by ``FakeBuilders`` so each test scripts exactly what every builder call returns; no model
is called. The real builders are covered end to end by tests/integration/test_build_end_to_end.py."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest

from src.services import build_coordinator as bc
from src.services import project_plan as pp
from src.services.project_service import ProjectService
from tests.unit.conftest import anyio_backend, pg, pg_dbname  # noqa: F401
from tests.unit.plan_fixture import qa_plan

pytestmark = pytest.mark.anyio

@pytest.fixture(autouse=True)
def _pipeline_mode(monkeypatch):
    """These tests drive the role-split pipeline (the one-shot builder is the default; its tests are test_oneshot_builder.py)."""
    monkeypatch.setenv("WORKFLOW_BUILDER_MODE", "pipeline")

JSON = {"accept": "application/json"}
MODULES = ["common", "tests_library", "runs", "triage"]
OK_PLAN = {"has_plan": True, "ok": True, "plan_version": 1, "missing": {}, "conformance": {}}


def gap_plan(**missing: Any) -> dict[str, Any]:
    return {"has_plan": True, "ok": False, "plan_version": 1, "missing": missing, "conformance": {"routes": "7/8"}}


class FakeBuilders(bc.Builders):
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.state: dict[str, dict[str, Any]] = {}
        self.gen: dict[str, list[dict[str, Any]]] = {}       # key -> results, popped in order (default: built)
        self.checks: dict[str, list[dict[str, Any]]] = {}    # module key -> check results, popped in order (default: ok)
        self.plans: list[dict[str, Any]] = []                # check_plan results, popped in order (default: ok)
        self.inputs: list[dict[str, Any]] = []               # input_needed results, popped in order (default: nothing)
        self.block: dict[str, asyncio.Event] = {}            # generate_module for key waits on the event
        self.started: dict[str, asyncio.Event] = {}
        self.ids: dict[str, str] = {}

    async def scaffold_module(self, project_id, key, body):
        return None            # these tests drive the one-generation-per-module path; the fast path has its own tests

    async def module_state(self, project_id, key):
        return self.state.get(key) or {"module_id": self.ids.get(key), "built_against": None}

    async def generate_module(self, project_id, key, instruction, module_id, log, is_cancelled):
        self.calls.append(("generate", key, instruction))
        self.started.setdefault(key, asyncio.Event()).set()
        if key in self.block:
            await self.block[key].wait()
        await log(f"generating {key}")
        q = self.gen.get(key)
        return q.pop(0) if q else {"type": "result", "status": "built", "source": f"# {key}\n", "tables": []}

    async def save_module(self, project_id, key, module_id, source, tables):
        self.calls.append(("save", key))
        self.ids[key] = module_id or f"mod_{key}"
        return self.ids[key]

    async def check_module(self, project_id, module_id):
        key = module_id.removeprefix("mod_")
        self.calls.append(("check_module", key))
        q = self.checks.get(key)
        return q.pop(0) if q else {"ok": True, "problems": []}

    async def input_needed(self, project_id, body):
        self.calls.append(("input",))
        return self.inputs.pop(0) if self.inputs else {"environment": "dev", "secrets": [], "tables": [], "message": ""}

    async def ensure_app(self, project_id, app):
        self.calls.append(("ensure_app", app["key"]))
        return f"app_{app['key']}"

    async def build_app(self, project_id, app_id, key, instruction, log):
        self.calls.append(("build_app", key, instruction))
        return {"saved": True, "errors": [], "text": "done"}

    async def check_app(self, project_id, app_id):
        self.calls.append(("check_app", app_id))
        return {"ok": True, "problems": []}

    async def check_plan(self, project_id):
        self.calls.append(("check_plan",))
        return self.plans.pop(0) if self.plans else dict(OK_PLAN)

    async def check_project(self, project_id):
        self.calls.append(("check_project",))
        return {"ok": True, "static": [], "self_test": {"passed": 3, "failed": 0}, "plan": await self.check_plan(project_id)}


def secrets_input(set_: bool) -> dict[str, Any]:
    return {"environment": "dev", "secrets": [{"name": n, "purpose": "bank login", "set": set_} for n in ("BANK_USER", "BANK_PASS")],
            "tables": [], "message": "" if set_ else "Set 2 secret(s) in 'dev': BANK_USER, BANK_PASS."}


@pytest.fixture
def fake(monkeypatch):
    f = FakeBuilders()
    monkeypatch.setattr(bc, "make_builders", lambda: f)
    return f


async def client(pg):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from src.core.dependencies import get_db_session
    from src.routes import builds, project_plan

    app = FastAPI()
    app.include_router(project_plan.router, prefix="/api")
    app.include_router(builds.router, prefix="/api")

    async def sess():
        async with pg() as s:
            yield s

    app.dependency_overrides[get_db_session] = sess
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


async def project(pg, c, *, approve: bool = True) -> str:
    async with pg() as s:
        pid = (await ProjectService(s).create(f"build-{uuid.uuid4().hex[:8]}")).id
    if approve:
        r = await c.post(f"/api/projects/{pid}/plan/draft", json={"body": qa_plan()}, headers=JSON)
        assert r.status_code == 200, r.text
        r = await c.post(f"/api/projects/{pid}/plan/1/approve", json={})
        assert r.status_code == 200, r.text
    return pid


async def wait_for(c, pid, bid, statuses, timeout=10.0) -> dict[str, Any]:
    for _ in range(int(timeout / 0.05)):
        b = (await c.get(f"/api/projects/{pid}/builds/{bid}")).json()
        if b["status"] in statuses:
            return b
        await asyncio.sleep(0.05)
    raise AssertionError(f"build stayed {b['status']}: {[(s['key'], s['status']) for s in b['steps']]}")


async def run(c, pid, body=None, statuses=bc.TERMINAL) -> dict[str, Any]:
    r = await c.post(f"/api/projects/{pid}/builds", json=body or {})
    assert r.status_code == 202, r.text
    return await wait_for(c, pid, r.json()["id"], statuses)


def steps(b) -> dict[str, dict[str, Any]]:
    return {s["key"]: s for s in b["steps"]}


async def test_steps_follow_build_order_then_input_apps_and_verify(pg, fake):
    c = await client(pg)
    pid = await project(pg, c)
    b = await run(c, pid)
    assert b["status"] == "done", b
    assert [s["key"] for s in b["steps"]] == [f"module:{m}" for m in MODULES] + ["input", "app:qa_workbench", "verify:1", "verify:final"]
    assert [x[1] for x in fake.calls if x[0] == "generate"] == MODULES
    s = steps(b)
    assert s["module:runs"]["title"] == "Backend · Runs" and s["module:runs"]["builder"] == "backend"
    assert s["module:runs"]["module_id"] == "mod_runs" and s["module:runs"]["status"] == "passed"
    assert s["input"]["status"] == "skipped" and s["input"]["kind"] == "input" and s["input"]["builder"] == "check"
    app = s["app:qa_workbench"]
    assert app["builder"] == "ui" and app["app_id"] == "app_qa_workbench" and app["status"] == "passed"
    assert ("build_app", "qa_workbench", "Build the QA Workbench app from the approved plan.") in fake.calls
    assert b["report"]["built"] == MODULES + ["qa_workbench"] and b["report"]["failed"] == []
    assert b["report"]["plan_check"] == {"ok": True, "missing": {}} and b["report"]["app_ids"] == ["app_qa_workbench"]
    assert b["plan_version"] == 1 and b["started_at"] and b["finished_at"]
    for st in b["steps"]:
        assert {"key", "kind", "title", "builder", "status", "attempt", "started_at", "finished_at", "summary", "problems",
                "module_id", "app_id", "log"} <= set(st)
    listed = (await c.get(f"/api/projects/{pid}/builds")).json()
    assert [x["id"] for x in listed] == [b["id"]] and all("log" not in st for st in listed[0]["steps"])
    assert steps(b)["module:runs"]["log"] == ["generating runs", "Saved runs.py", "Running the module check"]


async def test_modules_built_against_the_version_are_skipped_unless_rebuild(pg, fake):
    c = await client(pg)
    pid = await project(pg, c)
    fake.state = {"common": {"module_id": "mod_common", "built_against": 1},
                  "runs": {"module_id": "mod_runs", "built_against": 1}}
    fake.checks = {"runs": [{"ok": False, "problems": [{"code": "self_test_failed", "message": "GET /runs 500"}]}]}
    b = await run(c, pid)
    s = steps(b)
    assert s["module:common"]["status"] == "skipped" and s["module:common"]["module_id"] == "mod_common"
    # built against V but its check fails: rebuilt
    assert s["module:runs"]["status"] == "passed"
    assert [x[1] for x in fake.calls if x[0] == "generate"] == ["tests_library", "runs", "triage"]
    assert "common" in b["report"]["built"]

    fake.calls.clear()
    b = await run(c, pid, {"rebuild": True})
    assert [x[1] for x in fake.calls if x[0] == "generate"] == MODULES


async def test_partial_result_gets_exactly_one_follow_up(pg, fake):
    c = await client(pg)
    pid = await project(pg, c)
    fake.gen = {"runs": [{"type": "result", "status": "partial", "missing": ["POST /runs/{id}/retry"], "source": "# r\n", "tables": []}]}
    b = await run(c, pid)
    gens = [x for x in fake.calls if x[0] == "generate" and x[1] == "runs"]
    assert len(gens) == 2 and "POST /runs/{id}/retry" in gens[1][2]
    s = steps(b)["module:runs"]
    assert s["status"] == "passed" and s["attempt"] == 2 and b["status"] == "done"


async def test_a_failing_module_does_not_stop_the_build_and_ends_partial(pg, fake):
    c = await client(pg)
    pid = await project(pg, c)
    err = {"type": "error", "code": "generation_failed", "message": "the module did not pass analysis", "problems": []}
    fake.gen = {"tests_library": [dict(err), dict(err), dict(err)]}
    b = await run(c, pid)
    s = steps(b)
    assert s["module:tests_library"]["status"] == "failed" and s["module:tests_library"]["attempt"] == 2
    assert s["module:tests_library"]["problems"][0]["code"] == "generation_failed"
    assert s["module:runs"]["status"] == "passed" and s["module:triage"]["status"] == "passed"
    assert s["app:qa_workbench"]["status"] == "passed"
    assert b["status"] == "partial" and "tests_library" in b["report"]["failed"]
    assert len([x for x in fake.calls if x[0] == "generate" and x[1] == "tests_library"]) == 2


async def test_verify_fixes_owners_and_is_bounded(pg, fake):
    c = await client(pg)
    pid = await project(pg, c)
    gap = gap_plan(routes=[{"route": "POST /runs/{id}/retry", "module": "runs", "reason": "missing"}],
                   screens=[{"app": "qa_workbench", "screen": "Runs"}])
    fake.plans = [dict(gap) for _ in range(10)]
    b = await run(c, pid)
    keys = [s["key"] for s in b["steps"]]
    assert keys[6:] == ["verify:1", "fix:runs:1", "fix:app:qa_workbench:1", "verify:2", "fix:runs:2",
                        "fix:app:qa_workbench:2", "verify:final"]
    assert len([x for x in fake.calls if x[0] == "check_plan"]) == bc.MAX_VERIFY_ROUNDS + 1
    fix = [x for x in fake.calls if x[0] == "generate" and x[1] == "runs"][1]
    assert "POST /runs/{id}/retry" in fix[2]
    assert any(x[0] == "build_app" and "Runs" in x[2] for x in fake.calls[1:])
    assert b["status"] == "partial"
    assert b["report"]["plan_check"]["ok"] is False and b["report"]["plan_check"]["missing"]["routes"][0]["module"] == "runs"
    assert steps(b)["fix:runs:1"]["kind"] == "fix" and steps(b)["fix:runs:1"]["builder"] == "backend"


async def test_cancel_stops_inside_a_builder_call(pg, fake):
    c = await client(pg)
    pid = await project(pg, c)
    fake.block["tests_library"] = asyncio.Event()
    fake.started["tests_library"] = asyncio.Event()
    r = await c.post(f"/api/projects/{pid}/builds", json={})
    bid = r.json()["id"]
    await asyncio.wait_for(fake.started["tests_library"].wait(), 5)
    r = await c.post(f"/api/projects/{pid}/builds/{bid}/cancel")
    assert r.status_code == 200 and r.json()["status"] == "cancelled", r.text
    s = steps(r.json())
    assert s["module:common"]["status"] == "passed"
    assert s["module:tests_library"]["status"] == "failed" and s["module:tests_library"]["summary"] == "Cancelled"
    assert s["module:runs"]["status"] == "skipped" and s["app:qa_workbench"]["status"] == "skipped"
    assert ("save", "tests_library") not in fake.calls
    # a finished build is returned as it is
    again = await c.post(f"/api/projects/{pid}/builds/{bid}/cancel")
    assert again.json()["status"] == "cancelled"


async def test_one_active_build_per_project_and_an_approved_plan_is_required(pg, fake):
    c = await client(pg)
    pid = await project(pg, c)
    fake.block["common"] = asyncio.Event()
    r = await c.post(f"/api/projects/{pid}/builds", json={})
    assert r.status_code == 202 and r.json()["status"] == "queued"
    bid = r.json()["id"]
    r2 = await c.post(f"/api/projects/{pid}/builds", json={})
    assert r2.status_code == 409 and r2.json()["detail"]["code"] == "build_in_progress"
    assert r2.json()["detail"]["build_id"] == bid
    fake.block["common"].set()
    await wait_for(c, pid, bid, bc.TERMINAL)
    assert (await c.post(f"/api/projects/{pid}/builds", json={})).status_code == 202

    bare = await project(pg, c, approve=False)
    r = await c.post(f"/api/projects/{bare}/builds", json={})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "no_approved_plan"
    r = await c.post(f"/api/projects/{pid}/builds", json={"app_key": "nope"})
    assert r.status_code in (404, 409)


async def test_a_restart_marks_active_builds_interrupted(pg, fake):
    from src.db.build_models import ProjectBuildModel

    c = await client(pg)
    pid = await project(pg, c)
    async with pg() as s:
        st = bc.initial_steps(qa_plan(), None)
        st[0].update(status="passed")
        st[1].update(status="running")
        s.add(ProjectBuildModel(id="build_orphan_" + uuid.uuid4().hex[:6], project_id=pid, plan_version=1, status="running", steps=st))
        await s.commit()
    assert await bc.startup_sweep(pg) >= 1
    b = (await c.get(f"/api/projects/{pid}/builds")).json()[0]
    assert b["status"] == "failed" and b["reason"] == "interrupted"
    s = steps(b)
    assert s["module:common"]["status"] == "passed" and s["module:tests_library"]["status"] == "failed"
    assert s["module:runs"]["status"] == "skipped"
    # no longer active: a new build may start
    assert (await c.post(f"/api/projects/{pid}/builds", json={})).status_code == 202


async def test_input_step_is_skipped_when_nothing_is_needed(pg, fake):
    c = await client(pg)
    pid = await project(pg, c)
    fake.inputs = [secrets_input(True)]
    b = await run(c, pid)
    s = steps(b)["input"]
    assert s["status"] == "skipped" and b["status"] == "done"
    order = [x[0] for x in fake.calls]
    assert order.index("input") > max(i for i, x in enumerate(order) if x == "generate")
    assert order.index("input") < order.index("build_app")


async def test_a_missing_secret_pauses_the_build_and_continue_resumes_to_the_app(pg, fake):
    c = await client(pg)
    pid = await project(pg, c)
    fake.inputs = [secrets_input(False), secrets_input(True)]
    r = await c.post(f"/api/projects/{pid}/builds", json={})
    bid = r.json()["id"]
    b = await wait_for(c, pid, bid, ("waiting_for_input",))
    s = steps(b)["input"]
    assert s["status"] == "waiting_for_input" and s["input"]["secrets"][0] == {"name": "BANK_USER", "purpose": "bank login", "set": False}
    assert s["input"]["tables"] == [] and "BANK_USER" in s["input"]["message"]
    assert steps(b)["app:qa_workbench"]["status"] == "pending" and not any(x[0] == "build_app" for x in fake.calls)
    # still active while waiting
    assert (await c.post(f"/api/projects/{pid}/builds", json={})).json()["detail"]["code"] == "build_in_progress"

    r = await c.post(f"/api/projects/{pid}/builds/{bid}/continue", json={})
    assert r.status_code == 200 and r.json()["status"] != "waiting_for_input", r.text
    b = await wait_for(c, pid, bid, bc.TERMINAL)
    assert b["status"] == "done" and steps(b)["input"]["status"] == "passed"
    assert steps(b)["app:qa_workbench"]["status"] == "passed"
    r = await c.post(f"/api/projects/{pid}/builds/{bid}/continue", json={})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "build_not_waiting"


async def test_continue_with_input_still_missing_records_it_and_goes_on(pg, fake):
    c = await client(pg)
    pid = await project(pg, c)
    fake.inputs = [secrets_input(False), secrets_input(False)]
    r = await c.post(f"/api/projects/{pid}/builds", json={})
    bid = r.json()["id"]
    await wait_for(c, pid, bid, ("waiting_for_input",))
    await c.post(f"/api/projects/{pid}/builds/{bid}/continue", json={"skip": True})
    b = await wait_for(c, pid, bid, bc.TERMINAL)
    s = steps(b)["input"]
    assert s["status"] == "skipped" and "BANK_USER" in s["summary"] and "still missing" in s["summary"]
    assert steps(b)["app:qa_workbench"]["status"] == "passed"


async def test_cancel_while_waiting_for_input(pg, fake):
    c = await client(pg)
    pid = await project(pg, c)
    fake.inputs = [secrets_input(False)]
    r = await c.post(f"/api/projects/{pid}/builds", json={})
    bid = r.json()["id"]
    await wait_for(c, pid, bid, ("waiting_for_input",))
    r = await c.post(f"/api/projects/{pid}/builds/{bid}/cancel")
    assert r.json()["status"] == "cancelled", r.text
    s = steps(r.json())
    assert s["input"]["status"] == "failed" and s["input"]["summary"] == "Cancelled"
    assert s["app:qa_workbench"]["status"] == "skipped" and not any(x[0] == "build_app" for x in fake.calls)


async def test_sse_starts_with_a_snapshot_and_ends_with_done(pg, fake):
    import json

    c = await client(pg)
    pid = await project(pg, c)
    fake.block["common"] = asyncio.Event()
    bid = (await c.post(f"/api/projects/{pid}/builds", json={})).json()["id"]
    events: list[dict[str, Any]] = []

    async def read():
        async with c.stream("GET", f"/api/projects/{pid}/builds/{bid}/events") as r:
            assert r.status_code == 200
            async for line in r.aiter_lines():
                if line.startswith("event:"):
                    assert line.split(":", 1)[1].strip() == "message"
                if line.startswith("data:"):
                    events.append(json.loads(line[5:]))

    reader = asyncio.create_task(read())
    await asyncio.sleep(0.3)
    fake.block["common"].set()
    await asyncio.wait_for(reader, 15)
    assert events[0]["type"] == "snapshot" and events[0]["build"]["id"] == bid
    assert events[-1]["type"] == "done" and events[-1]["build"]["status"] == "done"
    kinds = {e["type"] for e in events}
    assert {"step", "log"} <= kinds
    assert all("log" not in e["step"] for e in events if e["type"] == "step")
    assert any(e["type"] == "log" and e["step_key"] == "module:common" for e in events)


def test_input_request_finds_unset_secrets_and_tables_needing_rows():
    body = qa_plan()
    body["data"]["tables"].append({"name": "environments", "owner": "common", "description": "reference list of target environments",
                                   "columns": [{"name": "name", "type": "text"}]})
    p = pp.normalize(body)
    rows = [{"key": "BANK_USER", "environment": "dev", "type": "secret"}, {"key": "BANK_PASS", "environment": "prod", "type": "secret"},
            {"key": "BANK_TOTP", "environment": "default", "type": "secret"}]
    out = bc.input_request(p, rows, {"tests": 0})
    assert {s["name"]: s["set"] for s in out["secrets"]} == {"BANK_USER": True, "BANK_PASS": False, "BANK_TOTP": True}
    assert [t["name"] for t in out["tables"]] == ["environments"]
    assert out["tables"][0]["suggested_min"] == bc.SUGGESTED_MIN_ROWS and out["tables"][0]["rows"] == 0
    assert "BANK_PASS" in out["message"] and "environments" in out["message"]
    assert bc.input_request(p, rows, {"environments": 5})["tables"] == []


async def test_a_question_from_the_ui_builder_is_a_problem_not_a_silent_success(pg, monkeypatch):
    import json

    from src.services import app_builder_ai_service as builder

    async def asks(self, req):
        assert req.plan_app == "qa_workbench" and req.app_id == "app_x"
        yield {"event": "message", "data": json.dumps({"type": "clarify", "questions": [{"id": "q1", "question": "Which runs matter?"}]})}
        yield {"event": "message", "data": json.dumps({"type": "partial", "missing": ["Runs"]})}
        yield {"event": "done", "data": "{}"}

    monkeypatch.setattr(builder.AppBuilderAIService, "stream_chat", asks)
    lines: list[str] = []

    async def log(m):
        lines.append(m)

    out = await bc.Builders().build_app("proj_x", "app_x", "qa_workbench", "Build the QA Workbench app", log)
    assert out["saved"] is False
    assert [e["code"] for e in out["errors"]] == ["app_builder_asked", "screen_missing"]
    assert "Which runs matter?" in out["errors"][0]["message"]


async def test_oneshot_one_module_holding_every_planned_route_leaves_no_not_written_gaps():
    """Coverage is judged by the planned routes present, not by module names."""
    import types

    routes = [{"method": "GET", "path": "/expenses"}, {"method": "GET", "path": "/approvals"}, {"method": "GET", "path": "/reports"}]
    body = {"modules": [{"key": k, "kind": "routes"} for k in ("expenses", "approvals", "reports")],
            "routes": [{"key": f"GET /{k}", "module": k} for k in ("expenses", "approvals", "reports")]}
    seen: list[dict[str, Any]] = []

    async def update(st, **kw):
        st.update(kw)
        seen.append(st)

    stub = types.SimpleNamespace(body=body, _update=update)
    steps_ = [{"key": f"module:{k}"} for k in ("expenses", "approvals", "reports")]
    res = {"errors": [], "modules": [{"name": "inbox", "id": "m1", "routes": routes}],
           "routes": [{**r, "module": "inbox", "status": "passing"} for r in routes]}
    await bc.Coordinator._oneshot_steps(stub, None, steps_, res)
    assert [s["status"] for s in steps_] == ["passed"] * 3
    assert not any(s.get("problems") for s in steps_)
    # a planned route that was never built still fails its module
    res["routes"] = res["routes"][:2]
    steps_ = [{"key": f"module:{k}"} for k in ("expenses", "approvals", "reports")]
    await bc.Coordinator._oneshot_steps(stub, None, steps_, res)
    assert [s["status"] for s in steps_] == ["passed", "passed", "failed"]


def test_report_keeps_saved_with_problems_apart_from_not_built():
    from types import SimpleNamespace

    co = object.__new__(bc.Coordinator)
    co.targets = {"fix:app:qa:1": "app:qa"}
    co.row = SimpleNamespace(steps=[
        {"key": "module:runs", "kind": "module", "status": "passed"},
        {"key": "module:triage", "kind": "module", "status": "failed"},
        {"key": "app:qa", "kind": "app", "status": "failed", "app_id": "app_1"},
        {"key": "fix:app:qa:1", "kind": "fix", "status": "partial"},      # the last word on the app: saved, one problem left
    ])
    assert co._report_lists() == (["runs"], ["qa"], ["triage"], ["app_1"])
