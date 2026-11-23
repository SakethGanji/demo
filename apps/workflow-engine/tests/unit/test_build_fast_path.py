"""The fast build path end to end on a throwaway Postgres: the real coordinator, the real scaffold, module save path and gates;
only the model is fake. The "office supply requests" plan has five CRUD / transition routes the scaffold builds and two things
only a model can write (the manager-only rule and the dashboard count)."""

from __future__ import annotations

import asyncio
import json
import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from src.services import build_coordinator as bc
from src.services.project_service import ProjectService
from tests.unit.conftest import anyio_backend, pg, pg_dbname  # noqa: F401
from tests.unit.office_plan_fixture import office_plan

pytestmark = pytest.mark.anyio

@pytest.fixture(autouse=True)
def _pipeline_mode(monkeypatch):
    """These tests drive the role-split pipeline (the one-shot builder is the default; its tests are test_oneshot_builder.py)."""
    monkeypatch.setenv("WORKFLOW_BUILDER_MODE", "pipeline")

JSON = {"accept": "application/json"}

DASHBOARD = '''@api.get("/dashboard/summary", description="how many requests are pending, approved, rejected")
def summary_dashboard(req):
    """Counts. Returns {pending, approved, rejected}"""
    table = studio.db.table("requests")
    return {s: table.count({"status": s}) for s in ("pending", "approved", "rejected")}'''
MANAGER_RULE = '''@requests.before_transition
def only_managers_decide(row, to, req):
    if "manager" not in ((req.caller or {}).get("roles") or []):
        raise error(403, "forbidden", "only a manager may decide a request")'''


class FakeModel:
    """Answers each module's patch pass with the canned patch; records every call."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []          # (module key, tool name)
        self.gate: asyncio.Event | None = None          # when set, a call waits for it (to hold a build mid-pass)
        self.entered = asyncio.Event()

    async def __call__(self, model, messages, temperature=0.2, tools=None, **kw):
        text = json.dumps(messages, default=str)
        key = "dashboard" if "[plan:dashboard]" in text else "requests"
        names = [t["name"] for t in tools or []]
        assert "emit_module" not in names, "a saved scaffold is only patched"
        self.entered.set()
        if self.gate is not None:
            await self.gate.wait()
        self.calls.append((key, "patch_module"))
        if key == "dashboard":
            ops = [{"op": "replace_function", "name": "summary_dashboard", "source": DASHBOARD}]
        else:
            ops = [{"op": "add_import", "source": "from studio.resources import error"},
                   {"op": "add_function", "source": MANAGER_RULE}]
        tc = SimpleNamespace(name="patch_module", id=f"t{len(self.calls)}",
                             args={"ops": ops, "summary": f"{key} rules", "remaining": []})
        return SimpleNamespace(tool_calls=[tc], text=None, output_truncated=False,
                               get_assistant_message=lambda: {"role": "assistant", "content": None})


@pytest.fixture
async def world(pg, monkeypatch, request):
    from cryptography.fernet import Fernet
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from src.core.config import settings
    from src.core.dependencies import get_db_session
    from src.engine import llm_provider
    from src.engine.node_registry import register_all_nodes
    from src.routes import api_router

    register_all_nodes()          # the broker's function index (studio.db -> DataTable) needs the nodes registered
    model = FakeModel()
    monkeypatch.setattr(llm_provider, "call_llm", model)
    monkeypatch.setattr(llm_provider, "is_configured", lambda m: True)
    monkeypatch.setattr("src.services.module_generator.async_session_factory", pg)
    monkeypatch.setattr(settings, "encryption_key", Fernet.generate_key().decode(), raising=False)
    app = FastAPI()
    app.include_router(api_router)

    async def sess():
        async with pg() as s:
            yield s

    app.dependency_overrides[get_db_session] = sess
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        async with pg() as s:
            pid = (await ProjectService(s).create(f"office-{uuid.uuid4().hex[:8]}")).id
        r = await c.post(f"/api/projects/{pid}/plan/draft", json={"body": getattr(request, "param", None) or office_plan()}, headers=JSON)
        assert r.status_code == 200, r.text
        r = await c.post(f"/api/projects/{pid}/plan/1/approve", json={})
        assert r.status_code == 200, r.text
        yield SimpleNamespace(c=c, pid=pid, model=model, p=f"/api/projects/{pid}")


async def start(w, plan: dict[str, Any] | None = None) -> str:
    r = await w.c.post(f"{w.p}/builds", json={})
    assert r.status_code == 202, r.text
    return r.json()["id"]


async def wait_done(w, bid: str, timeout: float = 120.0) -> dict[str, Any]:
    for _ in range(int(timeout / 0.25)):
        b = (await w.c.get(f"{w.p}/builds/{bid}")).json()
        if b["status"] in bc.TERMINAL:
            return b
        await asyncio.sleep(0.25)
    raise AssertionError(f"build still {b['status']}: {[(s['key'], s['status']) for s in b['steps']]}")


async def test_the_office_plan_builds_from_the_scaffold_and_the_model_only_fills_the_todos(world):
    r = await world.c.post(f"{world.p}/builds", json={})
    assert r.status_code == 202, r.text
    b = await wait_done(world, r.json()["id"])
    steps = {s["key"]: s for s in b["steps"]}
    detail = [(s["key"], s["status"], s["summary"], s["problems"][:3]) for s in b["steps"]]

    # the model was called once per module, as a patch, and only for the TODOs
    assert sorted(world.model.calls) == [("dashboard", "patch_module"), ("requests", "patch_module")], detail
    assert b["report"]["timing"]["model_calls"] == 2
    assert all(s["duration_s"] is not None for s in b["steps"] if s["kind"] == "module"), detail
    assert steps["module:requests"]["model_calls"] == 1 and steps["module:dashboard"]["model_calls"] == 1

    mods = {m["name"]: m for m in (await world.c.get(f"{world.p}/modules")).json()}
    assert set(mods) == {"requests", "dashboard"}
    src = (await world.c.get(f"{world.p}/modules/{mods['requests']['id']}")).json()["source"]
    for scaffolded in ("requests.list(req)", "requests.get(req)", "requests.create(req)", 'requests.transition(req, "approved")',
                       'requests.transition(req, "rejected", fields=["rejection_reason"], required=["rejection_reason"])'):
        assert scaffolded in src, src
    assert "only_managers_decide" in src and "not_built" not in src
    assert b["status"] in ("done", "partial"), detail
    assert steps["module:requests"]["status"] == "passed" and steps["module:dashboard"]["status"] == "passed", detail
    assert b["report"]["built"] == ["requests", "dashboard"] or set(b["report"]["built"]) == {"requests", "dashboard"}


def covered_plan() -> dict[str, Any]:
    """The office plan without the dashboard and the manager-only rule: nothing is left for a model."""
    plan = office_plan()
    plan["modules"] = plan["modules"][:1]
    plan["routes"] = [{**r, "errors": [e for e in r.get("errors", []) if e["code"] != "forbidden"]}
                      for r in plan["routes"] if r["module"] == "requests"]
    for tr in plan["data"]["tables"][0]["state_machine"]["transitions"]:
        tr["who"] = "any"
    plan["build_order"] = ["requests"]
    return plan


@pytest.mark.parametrize("world", [covered_plan()], indirect=True)
async def test_a_plan_the_scaffold_fully_covers_is_built_with_no_model_call(world):
    b = await wait_done(world, await start(world))
    assert world.model.calls == [], world.model.calls
    s = {x["key"]: x for x in b["steps"]}
    assert s["module:requests"]["status"] == "passed" and s["module:requests"]["model_calls"] == 0, s["module:requests"]
    assert "no model call" in s["module:requests"]["summary"] and b["report"]["timing"]["model_calls"] == 0
    # built against the plan: the next build skips it
    b2 = await wait_done(world, await start(world))
    assert {x["key"]: x["status"] for x in b2["steps"]}["module:requests"] == "skipped"


async def test_cancel_stops_the_parallel_passes(world):
    world.model.gate = asyncio.Event()                  # the model never answers
    bid = await start(world)
    await asyncio.wait_for(world.model.entered.wait(), 60)
    r = await world.c.post(f"{world.p}/builds/{bid}/cancel")
    assert r.status_code == 200 and r.json()["status"] == "cancelled", r.text
    b = (await world.c.get(f"{world.p}/builds/{bid}")).json()
    assert b["status"] == "cancelled" and not [x for x in b["steps"] if x["status"] in ("running", "pending")]
    # the modules (scaffolds) stay saved; a new build can start (no stale busy guard)
    world.model.gate = None
    b2 = await wait_done(world, await start(world))
    assert b2["status"] in ("done", "partial")


# ── pieces of the fast path, without a database ─────────────────────────────────────────────────────────────────────


def test_a_saved_planned_module_is_only_patched_and_a_full_rewrite_is_refused(monkeypatch):
    from tests.unit.test_builder_blind3_fixes import RUNS_SRC, _drive, _emit, _facts

    saved = SimpleNamespace(id="mod-runs", name="runs", source=RUNS_SRC, project_id="proj")
    patch = ("patch_module", {"ops": [{"op": "add_function", "source": "def helper_one():\n    return 1\n"}], "summary": "p",
                              "remaining": []})
    events, sent, _ = _drive(monkeypatch, [[_emit()], [patch]], saved_rows=[saved], facts=_facts(["runs"]),
                             req_kw={"patch_only": True})
    assert "emit_module" not in sent[0]["tools"] and "patch_module" in sent[0]["tools"]
    assert "SDK reference" in sent[0]["messages"][0]["content"] or "Resource (the scaffold" in sent[0]["messages"][0]["content"]
    assert "a full rewrite with emit_module is refused" in str(sent[1]["messages"][-1]["content"])
    res = events[-1]
    assert res["type"] == "result" and res["mode"] == "patch" and res["stats"]["model_calls"] == 2, events[-1]


def test_failures_of_routes_that_are_still_stubs_are_not_failures_of_the_scaffold():
    checks = [{"route": "GET /dashboard/summary", "ok": False, "step": "list"},
              {"route": "GET /requests/999999999", "ok": False, "step": "missing"},
              {"route": "GET /requests", "ok": True}]
    chk = {"ok": False, "checks": checks,
           "problems": [{"code": "self_test_failed", "message": "x"}, {"code": "static", "message": "keep me"}]}
    out = bc.drop_stub_failures(chk, ["GET /dashboard/summary", "GET /requests/{id}"])
    assert [c["route"] for c in out["checks"]] == ["GET /requests"] and out["ok"] is False
    assert [p["code"] for p in out["problems"]] == ["static"]
    assert bc.drop_stub_failures(chk, []) is chk


def test_modules_are_layered_by_what_they_depend_on(monkeypatch):
    from src.services import project_plan as pp

    plan = office_plan()
    plan["modules"].append({"key": "stock", "kind": "routes"})
    c = bc.Coordinator("b", bc._Run(), object())
    c.body = pp.normalize(plan)
    assert c._layers(["requests", "dashboard", "stock"]) == [["requests", "stock"], ["dashboard"]]
    monkeypatch.setenv("WORKFLOW_BUILD_CONCURRENCY", "2")
    assert bc.concurrency() == 2
    monkeypatch.setenv("WORKFLOW_BUILD_CONCURRENCY", "junk")
    assert bc.concurrency() == bc.DEFAULT_CONCURRENCY
