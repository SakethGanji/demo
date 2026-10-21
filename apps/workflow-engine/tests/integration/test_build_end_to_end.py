"""A build end to end (ORCHESTRATOR.md): approve the QA plan, POST /builds, and the real coordinator drives the real module
generator, the module save path, the app builder and the gates through to a report. Only the model calls are fake (the
plan conversation and ``emit_module`` through ``llm_provider.call_llm``, the app builder through its own ``call_llm``)."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest

from src.engine.llm_provider import LLMResponse, ToolCall
from tests.integration.test_plan_end_to_end import (COMMON, COMMON_TABLES, TESTS_LIBRARY, TESTS_TABLES, FakeModel, World,
                                                    runs_source)
from tests.integration.test_plan_end_to_end import world as _world  # noqa: F401
from tests.unit.conftest import anyio_backend, pg, pg_dbname  # noqa: F401

pytestmark = pytest.mark.anyio

@pytest.fixture(autouse=True)
def _pipeline_mode(monkeypatch):
    """These tests drive the role-split pipeline (the one-shot builder is the default; its tests are test_oneshot_builder.py)."""
    monkeypatch.setenv("WORKFLOW_BUILDER_MODE", "pipeline")


TRIAGE = '''from studio import api
from common import audit
import studio


@api.post("/triage/{id}/retry", name="retry_run")
def retry_run(req):
    """Re-run a failed run. Returns the new run."""
    old = studio.db.table("runs").get(int(req.params["id"]))
    row = studio.db.table("runs").insert({"test_id": old["test_id"] if old else 1, "status": "queued"})
    audit("run.retried", "runs", row.get("id"), "api")
    return row
'''
SOURCES = {"common": (COMMON, COMMON_TABLES), "tests_library": (TESTS_LIBRARY, TESTS_TABLES),
           "runs": (runs_source(), [{"name": "runs", "columns": [{"name": "test_id", "type": "integer"}]}]),
           "triage": (TRIAGE, [])}
APP = '''import { useEffect, useState } from "react";

export default function App() {
  const [tab, setTab] = useState<"Library" | "Runs">("Library");
  useEffect(() => {}, []);
  return (
    <div>
      <nav><button onClick={() => setTab("Library")}>Library</button><button onClick={() => setTab("Runs")}>Runs</button></nav>
      {tab === "Library" ? <section>Library</section> : <section>Runs</section>}
    </div>
  );
}
'''


class BuildModel(FakeModel):
    """``emit_module`` answers with the source of the module the prompt names (``[plan:<key>]``)."""

    async def __call__(self, model, messages, temperature=0.2, tools=None, **kw):
        if tools and tools[0]["name"] == "emit_module":
            text = json.dumps(messages, default=str)
            key = next(k for k in ("tests_library", "common", "runs", "triage") if f"[plan:{k}]" in text)
            src, tables = SOURCES[key]
            self.next_module = {"module_name": key, "source": src, "summary": f"{key} built", "remaining": [], "tables": tables}
        return await super().__call__(model, messages, temperature=temperature, tools=tools, **kw)


class AppModel:
    """The app builder's model: one ``write_files`` with App.tsx, then a closing message."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.wrote = False

    async def __call__(self, *a, tools=None, **kw):
        names = [t["name"] for t in tools or []]
        self.calls.append(names)
        if not self.wrote and "write_files" in names:
            self.wrote = True
            return LLMResponse(tool_calls=[ToolCall(id="w1", name="write_files", args={"files": [{"path": "App.tsx", "content": APP}]})])
        return LLMResponse(text="Built the QA Workbench.")


@pytest.fixture
async def world(_world: World, monkeypatch):
    from src.services import app_builder_ai_service as builder

    m = BuildModel()
    m.core, m.surface = _world.model.core, _world.model.surface
    monkeypatch.setattr("src.engine.llm_provider.call_llm", m)
    _world.model = m
    _world.app_model = AppModel()
    monkeypatch.setattr(builder, "call_llm", _world.app_model)
    # with qa_plan the app builder refuses the plan's brief (its GET routes have no response schema) and would ask the user;
    # a build cannot answer, so this run builds without the clarify round (the coordinator's handling of a question is unit-tested)
    monkeypatch.setenv("WORKFLOW_APP_BUILDER_CLARIFY", "off")
    # this test scripts a model that emits whole modules (the one-generation-per-module path); the scaffold fast path, where the
    # model only patches, is covered by tests/unit/test_build_fast_path.py
    from src.services import build_coordinator as bc

    async def no_scaffold(self, project_id, key, body):
        return None

    monkeypatch.setattr(bc.Builders, "scaffold_module", no_scaffold)
    monkeypatch.setattr(builder, "typecheck_errors", lambda *a, **k: asyncio.sleep(0, ""), raising=False)
    return _world


async def set_secrets(w: World) -> None:
    for env, host in (("dev", "localhost"), ("prod", "bank.example.com")):
        for key in ("BANK_USER", "BANK_PASS", "BANK_TOTP"):
            r = await w.c.post(f"{w.p}/secrets", json={"key": key, "value": "v-" + key, "environment": env, "allowed_hosts": [host]})
            assert r.status_code == 201, r.text


async def wait_done(w: World, bid: str, timeout: float = 180.0) -> dict[str, Any]:
    for _ in range(int(timeout / 0.25)):
        b = (await w.c.get(f"{w.p}/builds/{bid}")).json()
        if b["status"] in ("done", "partial", "failed", "cancelled"):
            return b
        await asyncio.sleep(0.25)
    raise AssertionError(f"build still {b['status']}: {[(s['key'], s['status']) for s in b['steps']]}")


async def test_a_build_takes_the_approved_plan_to_built_modules_and_a_bound_app(world: World):
    await world.draft_answer_approve()
    await set_secrets(world)

    r = await world.c.post(f"{world.p}/builds", json={})
    assert r.status_code == 202, r.text
    bid = r.json()["id"]
    assert r.json()["status"] == "queued" and r.json()["plan_version"] == 1

    events: list[dict[str, Any]] = []

    async def read():
        async with world.c.stream("GET", f"{world.p}/builds/{bid}/events") as resp:
            assert resp.status_code == 200
            async for line in resp.aiter_lines():
                if line.startswith("data:"):
                    events.append(json.loads(line[5:]))

    reader = asyncio.create_task(read())
    b = await wait_done(world, bid)
    await asyncio.wait_for(reader, 30)

    s = {x["key"]: x for x in b["steps"]}
    assert b["status"] in ("done", "partial"), (b["reason"], [(x["key"], x["status"], x["summary"], x["problems"][:2]) for x in b["steps"]])
    # every module was generated and saved through the module save path
    mods = {m["name"]: m for m in (await world.c.get(f"{world.p}/modules")).json()}
    assert set(mods) == {"common", "tests_library", "runs", "triage"}
    for k in mods:
        st = s[f"module:{k}"]
        assert st["status"] in ("passed", "partial") and st["module_id"] == mods[k]["id"], st
        assert st["builder"] == "backend" and st["kind"] == "module" and st["attempt"] >= 1
    assert s["input"]["status"] == "skipped" and all(x["set"] for x in s["input"]["input"]["secrets"])
    # the app was created and bound to the plan, and the builder's files were saved as a version
    app = s["app:qa_workbench"]
    assert app["app_id"] and app["title"] == "UI · QA Workbench" and app["builder"] == "ui"
    plan = (await world.c.get(f"{world.p}/plan")).json()["approved"]["body"]
    assert plan["apps"][0]["app_id"] == app["app_id"]
    saved = (await world.c.get(f"/api/apps/{app['app_id']}")).json()
    assert "Library" in json.dumps(saved), json.dumps([s[k] for k in s if "app" in k], indent=1)[:6000]
    # the report matches the final plan check
    rep = b["report"]
    assert set(rep) >= {"built", "failed", "plan_check", "app_ids"} and rep["app_ids"] == [app["app_id"]]
    assert set(rep["built"]) | set(rep["failed"]) == {"common", "tests_library", "runs", "triage", "qa_workbench"}
    check = (await world.c.post(f"{world.p}/plan/check")).json()
    assert rep["plan_check"]["ok"] == check["ok"]
    assert b["status"] == ("done" if check["ok"] and not rep["failed"] else "partial")
    assert [x["key"] for x in b["steps"]][-1] == "verify:final" and "verify:1" in s
    # the stream: a snapshot first, done last, steps and log lines between
    assert events[0]["type"] == "snapshot" and events[-1]["type"] == "done"
    assert events[-1]["build"]["status"] == b["status"]
    assert {"step", "log"} <= {e["type"] for e in events}
    # one build at a time; a finished one leaves room for the next
    listed = (await world.c.get(f"{world.p}/builds")).json()
    assert [x["id"] for x in listed] == [bid]
