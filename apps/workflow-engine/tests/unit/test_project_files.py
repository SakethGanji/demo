"""One project file tree (LIVE-FILES §1) and its change log: path mapping and validation, read-only and stale-base refusals,
writes through the module save and the app version save, and the changes those saves (and table create / migrate, plan
approve) record. Real Postgres; no model is called."""

from __future__ import annotations

import uuid
from datetime import datetime
from types import SimpleNamespace

import pytest

from src.schemas.app import AppCreateRequest, AppFilePayload, AppUpdateRequest
from src.services import project_files as pf
from src.services.code_module_service import CodeModuleService
from src.services.data_table_service import DataTableService
from src.services.project_service import ProjectError, ProjectService
from tests.unit import wp_b_support
from tests.unit.conftest import anyio_backend, pg, pg_dbname  # noqa: F401
from tests.unit.plan_fixture import qa_plan

pytestmark = pytest.mark.anyio
JSON = {"accept": "application/json"}

PING = '''from studio import api


@api.get("/ping", name="ping")
def ping(req):
    """Ping"""
    return {"ok": True}
'''
PING2 = PING.replace('{"ok": True}', '{"ok": True, "pong": 1}') + '''

@api.get("/pong", name="pong")
def pong(req):
    """Pong"""
    return {"pong": True}
'''
APP = "export default function App() { return <div>hi</div>; }\n"


# ── pure helpers ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("bad", ["", "/etc/passwd", "a/../b", "..", "./a", "a//b", "a/", "C:/x", "a\\b", "a\x00b", None, 3])
def test_check_path_refuses_escapes_and_absolute_paths(bad):
    with pytest.raises(ProjectError) as e:
        pf.check_path(bad)
    assert e.value.status == 422 and e.value.code == "invalid_path"


def test_check_path_accepts_tree_paths():
    for p in ("PLAN.md", "backend/runs.py", "backend/tables/runs.json", "apps/qa/src/App.tsx", "apps/a.b/x..y.ts"):
        assert pf.check_path(p) == p


def test_language_follows_the_extension():
    assert [pf.language_of(p) for p in ("PLAN.md", "plan.json", "backend/x.py", "a/App.tsx", "a/x.ts", "a/s.css", "a/README")] \
        == ["markdown", "json", "python", "tsx", "typescript", "css", "text"]


def test_app_slugs_are_stable_and_deduplicated_in_creation_order():
    t = datetime(2026, 1, 1)
    apps = [SimpleNamespace(id="b", name="QA Workbench", created_at=t.replace(day=2)),
            SimpleNamespace(id="a", name="QA  workbench!", created_at=t),
            SimpleNamespace(id="c", name="QA Workbench", created_at=t.replace(day=3)),
            SimpleNamespace(id="d", name="***", created_at=t)]
    assert pf.app_slugs(apps) == {"a": "qa-workbench", "b": "qa-workbench-2", "c": "qa-workbench-3", "d": "app"}
    # a later app never renames an earlier one
    assert pf.app_slugs(apps[:2]) == {"a": "qa-workbench", "b": "qa-workbench-2"}


def test_diff_stats_counts_lines_added_and_removed():
    assert pf.diff_stats(None, "a\nb\n") == (2, 0)
    assert pf.diff_stats("a\nb\n", None) == (0, 2)
    assert pf.diff_stats("a\nb\nc\n", "a\nB\nc\nd\n") == (2, 1)
    assert pf.diff_stats("same\n", "same\n") == (0, 0)


def test_plan_markdown_renders_the_plan():
    md = pf.plan_markdown(qa_plan(), 3)
    assert md.startswith("# ") and "(v3)" in md.splitlines()[0]
    for word in ("runs", "tests_library", "GET /runs/{id}", "qa_workbench"):
        assert word in md, word


# ── the tree and writes ──────────────────────────────────────────────────────


async def project(pg) -> str:
    async with pg() as s:
        return (await ProjectService(s).create(f"pf-{uuid.uuid4().hex[:8]}")).id


async def make_app(s, pid: str, name: str = "QA Workbench") -> str:
    svc = pf._app_service(s)
    app = await svc.create_app(AppCreateRequest(name=name, definition={"sourceCode": None}, project_id=pid))
    await svc.update_app(app.id, AppUpdateRequest(source_code=APP, files=[
        AppFilePayload(path="App.tsx", content=APP), AppFilePayload(path="styles.css", content="a{}\n", file_type="css")],
        create_version=True, version_trigger="ai", version_prompt="first"))
    return app.id


async def test_tree_maps_modules_tables_routes_and_apps(pg, monkeypatch):
    wp_b_support.install(monkeypatch, None)
    pid = await project(pg)
    async with pg() as s:
        p = await ProjectService(s).require(pid)
        await CodeModuleService(s).create(p, "runs", PING, tables=[{"name": "runs", "columns": [{"name": "n", "type": "integer"}]}])
        await make_app(s, pid)
        await make_app(s, pid)
        files = await pf.list_files(s, p)
    by = {f["path"]: f for f in files}
    assert list(by) == ["backend/runs.py", "backend/tables/runs.json", "backend/routes.json",
                        "apps/qa-workbench/App.tsx", "apps/qa-workbench/styles.css",
                        "apps/qa-workbench-2/App.tsx", "apps/qa-workbench-2/styles.css"]
    assert by["backend/runs.py"]["kind"] == "module" and by["backend/runs.py"]["writable"] is True
    assert by["backend/runs.py"]["size"] == len(PING.encode()) and by["backend/runs.py"]["ref"]["type"] == "module"
    assert by["backend/tables/runs.json"]["kind"] == "table" and by["backend/tables/runs.json"]["writable"] is False
    assert by["backend/routes.json"]["kind"] == "routes" and by["apps/qa-workbench/App.tsx"]["kind"] == "app"
    assert all({"path", "kind", "size", "updated_at", "writable", "ref"} == set(f) for f in files)
    async with pg() as s:
        p = await ProjectService(s).require(pid)
        routes = await pf.read_file(s, p, "backend/routes.json")
        table = await pf.read_file(s, p, "backend/tables/runs.json")
        with pytest.raises(ProjectError) as e:
            await pf.read_file(s, p, "backend/nope.py")
    assert e.value.status == 404 and e.value.code == "file_not_found"
    assert routes["language"] == "json" and '"path": "/ping"' in routes["content"] and '"module": "runs"' in routes["content"]
    assert '"name": "runs"' in table["content"] and '"rows": 0' in table["content"]


async def test_read_only_files_and_stale_bases_are_refused(pg, monkeypatch):
    wp_b_support.install(monkeypatch, None)
    pid = await project(pg)
    async with pg() as s:
        p = await ProjectService(s).require(pid)
        await CodeModuleService(s).create(p, "runs", PING, tables=[{"name": "runs", "columns": [{"name": "n", "type": "integer"}]}])
        for path, kind in (("backend/routes.json", "routes"), ("backend/tables/runs.json", "table")):
            with pytest.raises(ProjectError) as e:
                await pf.write_file(s, p, path, "{}")
            assert (e.value.status, e.value.code, e.value.extra["kind"]) == (409, "read_only", kind)
            assert pf.READ_ONLY[kind] in e.value.message
        cur = await pf.read_file(s, p, "backend/runs.py")
        with pytest.raises(ProjectError) as e:
            await pf.write_file(s, p, "backend/runs.py", PING2, base_updated_at="2000-01-01T00:00:00")
        assert (e.value.status, e.value.code, e.value.extra["updated_at"]) == (409, "conflict", cur["updated_at"])
        with pytest.raises(ProjectError) as e:
            await pf.write_file(s, p, "backend/../x.py", PING2)
        assert e.value.code == "invalid_path"
        assert (await pf.read_file(s, p, "backend/runs.py"))["content"] == PING


async def test_module_write_goes_through_the_module_save_and_is_recorded(pg, monkeypatch):
    wp_b_support.install(monkeypatch, None)
    pid = await project(pg)
    async with pg() as s:
        p = await ProjectService(s).require(pid)
        await CodeModuleService(s).create(p, "runs", PING)
        cur = await pf.read_file(s, p, "backend/runs.py")
        out = await pf.write_file(s, p, "backend/runs.py", PING2, base_updated_at=cur["updated_at"])
    assert out["path"] == "backend/runs.py" and out["updated_at"] != cur["updated_at"]
    assert out["result"]["module"]["name"] == "runs"
    async with pg() as s:
        p = await ProjectService(s).require(pid)
        assert (await pf.read_file(s, p, "backend/runs.py"))["content"] == PING2
        assert '"/pong"' in (await pf.read_file(s, p, "backend/routes.json"))["content"]       # routes followed the save
        hist = await pf.history(s, pid, "backend/runs.py")
    assert [(h["op"], h["actor"]) for h in hist] == [("update", "user"), ("create", "user")]
    assert (hist[0]["lines_added"], hist[0]["lines_removed"]) == pf.diff_stats(PING, PING2)
    assert (hist[1]["lines_added"], hist[1]["lines_removed"]) == (len(PING.splitlines()), 0)
    assert hist[0]["build_id"] is None and hist[0]["step_key"] is None


async def test_app_write_creates_a_new_version_with_that_file_changed(pg):
    pid = await project(pg)
    async with pg() as s:
        aid = await make_app(s, pid)
        await s.commit()
    async with pg() as s:
        p = await ProjectService(s).require(pid)
        cur = await pf.read_file(s, p, "apps/qa-workbench/styles.css")
        out = await pf.write_file(s, p, "apps/qa-workbench/styles.css", "a{color:red}\nb{}\n", base_updated_at=cur["updated_at"])
        await s.commit()
    assert out["result"]["app_id"] == aid and out["result"]["version_number"] == 2
    async with pg() as s:
        p = await ProjectService(s).require(pid)
        assert (await pf.read_file(s, p, "apps/qa-workbench/styles.css"))["content"] == "a{color:red}\nb{}\n"
        assert (await pf.read_file(s, p, "apps/qa-workbench/App.tsx"))["content"] == APP
        assert (await pf.read_file(s, p, "apps/qa-workbench/App.tsx"))["updated_at"] == out["updated_at"]
        css = await pf.history(s, pid, "apps/qa-workbench/styles.css")
        tsx = await pf.history(s, pid, "apps/qa-workbench/App.tsx")
        with pytest.raises(ProjectError) as e:          # the old base is stale now
            await pf.write_file(s, p, "apps/qa-workbench/App.tsx", "x", base_updated_at=cur["updated_at"])
    assert e.value.code == "conflict"
    assert [(h["op"], h["lines_added"], h["lines_removed"]) for h in css] == [("update", 2, 1), ("create", 1, 0)]
    assert css[0]["summary"] == "v2: Edited styles.css"
    assert [h["op"] for h in tsx] == ["create"]             # unchanged in v2: no row


async def test_table_create_and_migrate_are_recorded_but_scratch_tables_are_not(pg):
    pid = await project(pg)
    async with pg() as s:
        p = await ProjectService(s).require(pid)
        svc = DataTableService(s)
        await svc.create_table(p, {"name": "runs", "columns": [{"name": "n", "type": "integer"}]})
        await svc.create_table(p, {"name": "runs__st0a1b2c", "columns": [{"name": "n", "type": "integer"}]})
        await svc.apply_migration(p, "runs", [{"op": "add_column", "column": {"name": "label", "type": "string"}}])
        log = await pf.changes(s, pid)
    assert [(c["path"], c["op"]) for c in log] == [("backend/tables/runs.json", "update"), ("backend/tables/runs.json", "create")]
    assert log[0]["summary"].startswith("migrated") and log[0]["lines_added"] > 0 and log[1]["summary"] == "created"


async def test_record_changes_uses_the_context_and_never_fails_the_save(pg):
    pid = await project(pg)
    async with pg() as s:
        with pf.change_context("backend_builder", "bld_1", "module:runs"):
            n = await pf.record_changes(s, pid, [{"path": "backend/a.py", "before": None, "after": "x\n"},
                                                 {"path": "backend/b.py", "before": "y\n", "after": "y\n"},
                                                 {"path": "backend/c.py", "before": "z\n", "after": None}], commit=True)
        assert n == 2
        # a bad row (unknown actor) is logged and dropped; the caller's session still works
        assert await pf.record_changes(s, pid, [{"path": "x", "before": None, "after": "1"}], actor="nobody", commit=True) == 0
        assert await pf.record_changes(s, pid, [{"path": "backend/d.py", "before": None, "after": "1"}], commit=True) == 1
        log = await pf.changes(s, pid)
        only = await pf.changes(s, pid, build_id="bld_1")
    assert [(c["path"], c["op"], c["actor"]) for c in log] == [
        ("backend/d.py", "create", "user"), ("backend/c.py", "delete", "backend_builder"), ("backend/a.py", "create", "backend_builder")]
    assert (log[1]["lines_added"], log[1]["lines_removed"]) == (0, 1)
    assert {c["path"] for c in only} == {"backend/a.py", "backend/c.py"}
    assert all(c["build_id"] == "bld_1" and c["step_key"] == "module:runs" for c in only)


# ── the routes ───────────────────────────────────────────────────────────────


async def client(pg):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from src.core.dependencies import get_db_session
    from src.routes import project_files, project_plan

    app = FastAPI()
    app.include_router(project_plan.router, prefix="/api")
    app.include_router(project_files.router, prefix="/api")

    async def sess():
        async with pg() as s:
            yield s

    app.dependency_overrides[get_db_session] = sess
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


async def test_routes_list_read_write_history_and_changes(pg, monkeypatch):
    pid = await project(pg)
    c = await client(pg)
    r = await c.post(f"/api/projects/{pid}/plan/draft", json={"body": qa_plan()}, headers=JSON)
    assert r.status_code == 200, r.text
    r = await c.post(f"/api/projects/{pid}/plan/1/approve", json={})
    assert r.status_code == 200, r.text
    wp_b_support.install(monkeypatch, None)              # the module save below without the plan's schema rules
    async with pg() as s:
        p = await ProjectService(s).require(pid)
        await CodeModuleService(s).create(p, "runs", PING)

    files = (await c.get(f"/api/projects/{pid}/files")).json()["files"]
    assert [f["path"] for f in files][:3] == ["PLAN.md", "plan.json", "backend/runs.py"]
    assert files[0]["kind"] == "plan" and files[0]["writable"] is False and files[0]["ref"]["type"] == "plan"

    r = await c.get(f"/api/projects/{pid}/files/content", params={"path": "PLAN.md"})
    assert r.status_code == 200 and r.json()["language"] == "markdown" and "(v1)" in r.json()["content"]
    cur = (await c.get(f"/api/projects/{pid}/files/content", params={"path": "backend/runs.py"})).json()
    assert set(cur) == {"path", "content", "language", "kind", "writable", "updated_at"} and cur["content"] == PING

    r = await c.put(f"/api/projects/{pid}/files/content", json={"path": "PLAN.md", "content": "# mine"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "read_only" and "revision" in r.json()["detail"]["message"]
    r = await c.put(f"/api/projects/{pid}/files/content",
                    json={"path": "backend/runs.py", "content": PING2, "base_updated_at": "2000-01-01T00:00:00"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "conflict"
    r = await c.put(f"/api/projects/{pid}/files/content", json={"path": "/etc/passwd", "content": "x"})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "invalid_path"
    r = await c.put(f"/api/projects/{pid}/files/content", json={"path": "backend/runs.py", "content": PING2, "extra": 1})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "invalid_request"
    r = await c.get(f"/api/projects/{pid}/files/content", params={"path": "apps/nope/App.tsx"})
    assert r.status_code == 404 and r.json()["detail"]["code"] == "file_not_found"

    r = await c.put(f"/api/projects/{pid}/files/content",
                    json={"path": "backend/runs.py", "content": PING2, "base_updated_at": cur["updated_at"]})
    assert r.status_code == 200, r.text
    assert set(r.json()) == {"path", "updated_at", "result"} and r.json()["path"] == "backend/runs.py"

    hist = (await c.get(f"/api/projects/{pid}/files/history", params={"path": "backend/runs.py"})).json()
    assert [h["op"] for h in hist] == ["update", "create"]
    assert set(hist[0]) == {"id", "path", "op", "actor", "build_id", "step_key", "summary", "lines_added", "lines_removed",
                            "created_at"}
    log = (await c.get(f"/api/projects/{pid}/changes", params={"limit": 50})).json()
    plan_rows = [(x["path"], x["op"], x["actor"]) for x in log if x["path"] in ("PLAN.md", "plan.json")]
    assert sorted(plan_rows) == [("PLAN.md", "create", "plan"), ("plan.json", "create", "plan")]
    assert len((await c.get(f"/api/projects/{pid}/changes", params={"limit": 1})).json()) == 1
    assert (await c.get(f"/api/projects/{pid}/changes", params={"build_id": "bld_none"})).json() == []
    r = await c.get("/api/projects/proj_missing_xyz/files")
    assert r.status_code == 404
