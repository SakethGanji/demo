"""The build stream's live view (LIVE-FILES §2): ``file`` events (PLAN.md, module sources as generated and saved, tables, UI
files), ``route`` events (stub -> built -> passing / failing) and ``preview`` events, and the change log rows a build step's
saves write with the step's builder as the actor. Fake builders from test_build_coordinator plus the real save / app paths
with the model calls replaced; no model is called."""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

from src.services import build_coordinator as bc
from src.services import project_files as pf
from src.services.project_service import ProjectService
from tests.unit import wp_b_support
from tests.unit.conftest import anyio_backend, pg, pg_dbname  # noqa: F401
from tests.unit.test_build_coordinator import FakeBuilders, client, project, run

pytestmark = pytest.mark.anyio

@pytest.fixture(autouse=True)
def _pipeline_mode(monkeypatch):
    """These tests drive the role-split pipeline (the one-shot builder is the default; its tests are test_oneshot_builder.py)."""
    monkeypatch.setenv("WORKFLOW_BUILDER_MODE", "pipeline")


RUNS = '''from studio import api


@api.get("/runs", name="list_runs")
def list_runs(req):
    """Runs"""
    return []


@api.get("/runs/{id}", name="get_run")
def get_run(req):
    """One run"""
    return {}
'''


class LiveBuilders(FakeBuilders):
    async def generate_module(self, project_id, key, instruction, module_id, log, is_cancelled):
        res = await super().generate_module(project_id, key, instruction, module_id, log, is_cancelled)
        return {**res, "source": RUNS} if key == "runs" and res.get("type") == "result" else res

    async def save_module(self, project_id, key, module_id, source, tables):
        out = await super().save_module(project_id, key, module_id, source, tables)
        async with bc._factory()() as s:          # what the real save path records, under the step's context
            await pf.record_changes(s, project_id, [{"path": f"backend/{key}.py", "before": None, "after": source}], commit=True)
        return out

    async def check_module(self, project_id, module_id):
        out = await super().check_module(project_id, module_id)
        if module_id == "mod_runs":
            out = {**out, "checks": [{"route": "GET /runs", "ok": True}, {"route": "GET /runs/7", "ok": False},
                                     {"route": "GET /runs/{id}", "ok": True}, {"route": "POST /runs", "skipped": True}]}
        return out

    async def build_app(self, project_id, app_id, key, instruction, log):
        bc.emit_file("ui", "apps/qa-workbench/App.tsx", "export default 1\n")
        bc.emit({"type": "preview", "app_id": app_id, "version_id": "42"})
        return await super().build_app(project_id, app_id, key, instruction, log)


@pytest.fixture
def events(monkeypatch):
    seen: list[dict[str, Any]] = []
    real = bc._publish

    def publish(build_id, ev):
        seen.append({"_build": build_id, **ev})
        real(build_id, ev)

    monkeypatch.setattr(bc, "_publish", publish)
    return seen


async def test_a_build_streams_files_routes_and_previews(pg, monkeypatch, events):
    f = LiveBuilders()
    monkeypatch.setattr(bc, "make_builders", lambda: f)
    c = await client(pg)
    pid = await project(pg, c)
    b = await run(c, pid)
    assert b["status"] == "done", b
    assert all(e["_build"] == b["id"] for e in events)
    files = [e for e in events if e["type"] == "file"]
    assert files[0]["path"] == "PLAN.md" and files[0]["builder"] == "plan" and files[0]["step_key"] is None
    assert "(v1)" in files[0]["content"]
    for e in files:
        assert set(e) - {"_build"} == {"type", "step_key", "builder", "path", "op", "content", "done", "truncated"}
        assert e["op"] == "write" and e["truncated"] is False
    runs = [e for e in files if e["path"] == "backend/runs.py"]
    assert len(runs) == 2 and all(e["content"] == RUNS and e["step_key"] == "module:runs" and e["builder"] == "backend"
                                  for e in runs)                       # as generated, then as saved
    ui = [e for e in files if e["builder"] == "ui"]
    assert [(e["path"], e["step_key"]) for e in ui] == [("apps/qa-workbench/App.tsx", "app:qa_workbench")]
    assert [e for e in events if e["type"] == "preview"] == [
        {"_build": b["id"], "type": "preview", "app_id": "app_qa_workbench", "version_id": "42"}]

    routes = [e for e in events if e["type"] == "route"]
    stubs = [e for e in routes if e["status"] == "stub"]
    assert ("GET", "/runs/{id}") in {(e["method"], e["path"]) for e in stubs}
    assert all(set(e) - {"_build"} == {"type", "step_key", "method", "path", "name", "status"} for e in routes)
    assert next(e for e in stubs if e["path"] == "/runs/{id}")["step_key"] == "module:runs"
    later = [(e["method"], e["path"], e["name"], e["status"]) for e in routes if e["status"] != "stub"]
    assert later == [("GET", "/runs", "list_runs", "built"), ("GET", "/runs/{id}", "get_run", "built"),
                     ("GET", "/runs", "list_runs", "passing"), ("GET", "/runs/{id}", "get_run", "failing")]
    # the order on the stream: stubs first, then each route's built before its check
    assert routes.index(stubs[-1]) < routes.index(next(e for e in routes if e["status"] == "built"))

    async with pg() as s:
        log = await pf.changes(s, pid, build_id=b["id"])
    assert {(x["path"], x["actor"], x["step_key"]) for x in log} >= {("backend/runs.py", "backend_builder", "module:runs"),
                                                                      ("backend/common.py", "backend_builder", "module:common")}
    assert pf.current_context()["actor"] == "user"                     # nothing leaks out of the steps


async def test_the_sse_route_relays_the_live_events(pg, monkeypatch):
    import asyncio

    f = LiveBuilders()
    monkeypatch.setattr(bc, "make_builders", lambda: f)
    c = await client(pg)
    pid = await project(pg, c)
    f.block["common"] = asyncio.Event()
    bid = (await c.post(f"/api/projects/{pid}/builds", json={})).json()["id"]
    got: list[dict[str, Any]] = []

    async def read():
        async with c.stream("GET", f"/api/projects/{pid}/builds/{bid}/events") as r:
            async for line in r.aiter_lines():
                if line.startswith("data:"):
                    got.append(json.loads(line[5:]))

    reader = asyncio.create_task(read())
    await asyncio.sleep(0.3)
    f.block["common"].set()
    await asyncio.wait_for(reader, 15)
    kinds = [e["type"] for e in got]
    assert kinds[0] == "snapshot" and kinds[-1] == "done"
    assert {"file", "route", "preview", "step", "log"} <= set(kinds)


def test_file_events_are_capped():
    big = "x" * (bc.FILE_CAP + 10)
    e = bc.file_event("backend", "backend/a.py", big, step_key="module:a")
    assert e["truncated"] is True and len(e["content"].encode()) == bc.FILE_CAP and e["step_key"] == "module:a"
    e = bc.file_event("ui", "apps/a/x.tsx", "", op="delete")
    assert e == {"type": "file", "builder": "ui", "path": "apps/a/x.tsx", "op": "delete", "content": "", "done": True,
                 "truncated": False}


def test_emit_outside_a_step_publishes_nothing(events):
    bc.emit_file("backend", "backend/a.py", "x")
    assert events == []


def test_route_statuses_pick_the_most_specific_route_and_failures_win():
    routes = [("GET", "/runs/{id}"), ("GET", "/runs/latest"), ("POST", "/runs/{id}/cancel"), ("GET", "/tests")]
    checks = [{"route": "GET /runs/latest", "ok": True}, {"route": "GET /runs/12", "ok": True},
              {"route": "GET /runs/13?x=1", "ok": False}, {"route": "POST /runs/{id}/cancel", "ok": True},
              {"route": "GET /tests", "ok": True, "skipped": True}, {"route": "nonsense", "ok": False}, {"ok": False}]
    assert bc.route_statuses(routes, checks) == {("GET", "/runs/latest"): "passing", ("GET", "/runs/{id}"): "failing",
                                                  ("POST", "/runs/{id}/cancel"): "passing"}


# ── the real builders' hooks ─────────────────────────────────────────────────


async def test_the_real_module_save_streams_tables_and_records_as_the_builder(pg, monkeypatch, events):
    wp_b_support.install(monkeypatch, None)
    async with pg() as s:
        pid = (await ProjectService(s).create(f"live-{uuid.uuid4().hex[:8]}")).id
    with bc.step_scope("bld_live", "module:runs", "backend_builder"):
        mid = await bc.Builders().save_module(pid, "runs", None, RUNS, [{"name": "runs", "columns": [{"name": "n", "type": "integer"}]}])
    assert mid
    tables = [e for e in events if e["type"] == "file" and e["builder"] == "tables"]
    assert [(e["path"], e["step_key"], e["_build"]) for e in tables] == [("backend/tables/runs.json", "module:runs", "bld_live")]
    t = json.loads(tables[0]["content"])
    assert (t["name"], t["owner"], t["rows"], [c["name"] for c in t["columns"]]) == ("runs", None, 0, ["n"])
    async with pg() as s:
        log = await pf.changes(s, pid)
    assert {(x["path"], x["op"], x["actor"], x["build_id"], x["step_key"]) for x in log} == {
        ("backend/runs.py", "create", "backend_builder", "bld_live", "module:runs"),
        ("backend/tables/runs.json", "create", "backend_builder", "bld_live", "module:runs")}


async def test_the_real_app_build_streams_each_written_file_then_a_preview(pg, monkeypatch, events):
    from src.schemas.app import AppCreateRequest
    from src.services.app_builder_ai_service import AppBuilderAIService
    from src.services.client_codegen import CLIENT_PATH

    async def stream_chat(self, request):        # the model-driven turn, replaced: two edit-tool writes, then the code event
        await self.on_files([{"path": "App.tsx", "content": "export default 1\n"}, {"path": f"src/{CLIENT_PATH}", "content": "c"}])
        await self.on_files([{"path": "App.tsx", "content": "export default 2\n"}, {"path": "old.tsx", "content": None}])
        yield {"event": "message", "data": json.dumps({"type": "code", "files": [
            {"path": "App.tsx", "content": "export default 2\n"}, {"path": "styles.css", "content": "a{}\n"}]})}

    monkeypatch.setattr(AppBuilderAIService, "stream_chat", stream_chat)
    async with pg() as s:
        pid = (await ProjectService(s).create(f"live-{uuid.uuid4().hex[:8]}")).id
        aid = (await bc._app_service(s).create_app(AppCreateRequest(name="QA Workbench", definition={"sourceCode": None},
                                                                   project_id=pid))).id

    async def log(m):
        return None

    with bc.step_scope("bld_ui", "app:qa", "ui_builder"):
        out = await bc.Builders().build_app(pid, aid, "qa", "Build it", log)
    assert out["saved"] is True and out["errors"] == []
    seen = [(e["type"], e.get("path"), e.get("op"), e.get("content")) for e in events]
    assert seen[:-1] == [("file", "apps/qa-workbench/App.tsx", "write", "export default 1\n"),
                         ("file", "apps/qa-workbench/App.tsx", "write", "export default 2\n"),
                         ("file", "apps/qa-workbench/old.tsx", "delete", ""),
                         ("file", "apps/qa-workbench/styles.css", "write", "a{}\n")]     # not streamed by a tool: sent at save
    assert seen[-1][0] == "preview" and events[-1]["app_id"] == aid and events[-1]["version_id"]
    assert "step_key" not in events[-1] and all(e["step_key"] == "app:qa" for e in events[:-1])
    async with pg() as s:
        log_rows = await pf.changes(s, pid)
    assert {(x["path"], x["actor"], x["build_id"]) for x in log_rows} == {
        ("apps/qa-workbench/App.tsx", "ui_builder", "bld_ui"), ("apps/qa-workbench/styles.css", "ui_builder", "bld_ui")}


async def test_the_app_builder_reports_what_an_edit_tool_wrote():
    from src.services.app_builder_ai_service import AppBuilderAIService

    svc = AppBuilderAIService.__new__(AppBuilderAIService)
    got: list[list[dict[str, Any]]] = []

    async def on_files(files):
        got.append(files)

    svc.on_files = on_files
    ws = [{"path": "App.tsx", "content": "2"}, {"path": "new.tsx", "content": "n"}]
    await svc._report_files({"App.tsx": "1", "gone.tsx": "g"}, ws)
    await svc._report_files({"App.tsx": "2", "new.tsx": "n"}, ws)              # nothing changed: no call
    assert got == [[{"path": "App.tsx", "content": "2"}, {"path": "new.tsx", "content": "n"}, {"path": "gone.tsx", "content": None}]]

    async def broken(files):
        raise RuntimeError("boom")

    svc.on_files = broken
    await svc._report_files({}, ws)                                            # a failing listener never breaks the turn
