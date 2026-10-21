"""The module save's schema preflight: a second module declaring extra columns on a table the first one created migrates it (the
blind-test F2), destructive declarations refuse the whole save, and with an approved plan the schema must be the plan's.
Real Postgres; the plan is a fixture (``load_approved`` patched)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from src.db.models import ApiRouteModel, CodeModuleModel
from src.services.code_module_service import CodeModuleService
from src.services.data_table_service import DataTableService
from src.services.project_service import ProjectError, ProjectService
from tests.unit import wp_b_support
from tests.unit.conftest import anyio_backend, pg, pg_dbname  # noqa: F401

pytestmark = pytest.mark.anyio

M1 = '''from studio import api
import studio


@api.post("/runs", name="start_run")
def start_run(req):
    """Start a run"""
    return studio.db.table("runs").insert({"test_id": 1})
'''
M2 = '''from studio import api
import studio


@api.get("/runs/{id}", name="get_run")
def get_run(req):
    """One run"""
    return studio.db.table("runs").get(int(req.params["id"]))
'''
BASE = [{"name": "test_id", "type": "integer", "required": True}]


async def project(pg):
    async with pg() as s:
        return (await ProjectService(s).create(f"cms-{uuid.uuid4().hex[:8]}")).id


async def routes(pg, pid):
    async with pg() as s:
        return sorted((r.method, r.path) for r in (await s.execute(select(ApiRouteModel).where(ApiRouteModel.project_id == pid))).scalars())


async def cols(pg, pid, name="runs"):
    async with pg() as s:
        t = await DataTableService(s).table_by_name(pid, name)
        return None if t is None else t.columns


async def test_module_two_declaring_extra_columns_migrates_module_ones_table(pg, monkeypatch):
    wp_b_support.install(monkeypatch, None)
    pid = await project(pg)
    async with pg() as s:
        p = await ProjectService(s).require(pid)
        r1 = await CodeModuleService(s).create(p, "runs", M1, tables=[{"name": "runs", "columns": BASE}])
        assert r1["tables_created"] == ["runs"] and r1["tables_migrated"] == [] and r1["schema_refused"] == []
        svc = DataTableService(s)
        await svc.insert_rows(await svc.table_by_name(pid, "runs"), [{"test_id": 1}])
        r2 = await CodeModuleService(s).create(p, "runs_read", M2, tables=[{"name": "runs", "columns": BASE + [
            {"name": "status", "type": "string", "required": True, "default": "queued", "enum": ["queued", "done"]},
            {"name": "run_key", "type": "string", "unique": True}]}])
        assert [m["table"] for m in r2["tables_migrated"]] == ["runs"] and "table_errors" not in r2
    assert [c["name"] for c in await cols(pg, pid)] == ["test_id", "status", "run_key"]
    async with pg() as s:
        svc = DataTableService(s)
        t = await svc.table_by_name(pid, "runs")
        assert (await svc.list_rows(t))["rows"][0]["data"]["status"] == "queued"          # backfilled
        assert len(await svc.migrations(await ProjectService(s).require(pid), "runs")) == 1


async def test_a_destructive_declaration_refuses_the_save_and_writes_nothing(pg, monkeypatch):
    wp_b_support.install(monkeypatch, None)
    pid = await project(pg)
    async with pg() as s:
        p = await ProjectService(s).require(pid)
        await CodeModuleService(s).create(p, "runs", M1, tables=[{"name": "runs", "columns": BASE}])
        with pytest.raises(ProjectError) as e:
            await CodeModuleService(s).create(p, "runs_read", M2, tables=[{"name": "runs", "columns": [
                {"name": "test_id", "type": "string"}]}])                                    # integer -> string
        assert (e.value.status, e.value.code) == (422, "schema_change_refused") and e.value.extra["refused"]
        with pytest.raises(ProjectError) as e:
            await _seed_row_then_save(s, p)
        assert e.value.code == "schema_change_refused"
    assert await routes(pg, pid) == [("POST", "/runs")]
    assert [c["name"] for c in await cols(pg, pid)] == ["test_id"]
    async with pg() as s:
        assert (await s.execute(select(CodeModuleModel.name).where(CodeModuleModel.project_id == pid))).scalars().all() == ["runs"]


async def _seed_row_then_save(s, p):
    svc = DataTableService(s)
    await svc.insert_rows(await svc.table_by_name(p.id, "runs"), [{"test_id": 1}])
    return await CodeModuleService(s).create(p, "runs_read", M2, tables=[{"name": "runs", "columns": BASE + [
        {"name": "must", "type": "string", "required": True}]}])                              # required, no default, rows exist


async def test_a_unique_conflict_refuses_the_save_before_the_module_is_written(pg, monkeypatch):
    wp_b_support.install(monkeypatch, None)
    pid = await project(pg)
    async with pg() as s:
        p = await ProjectService(s).require(pid)
        await CodeModuleService(s).create(p, "runs", M1, tables=[{"name": "runs", "columns": BASE + [{"name": "k", "type": "string"}]}])
        svc = DataTableService(s)
        await svc.insert_rows(await svc.table_by_name(pid, "runs"), [{"test_id": 1, "k": "a"}, {"test_id": 2, "k": "a"}])
        with pytest.raises(ProjectError) as e:
            await CodeModuleService(s).create(p, "runs_read", M2, tables=[{"name": "runs", "columns": BASE + [
                {"name": "k", "type": "string", "unique": True}]}])
        assert (e.value.status, e.value.code) == (409, "unique_conflict")
    assert await routes(pg, pid) == [("POST", "/runs")]


async def test_dry_run_reports_the_migration_without_applying_it(pg, monkeypatch):
    wp_b_support.install(monkeypatch, None)
    pid = await project(pg)
    async with pg() as s:
        p = await ProjectService(s).require(pid)
        await CodeModuleService(s).create(p, "runs", M1, tables=[{"name": "runs", "columns": BASE}])
        res = await CodeModuleService(s).create(p, "runs_read", M2, dry_run=True, tables=[{"name": "runs", "columns": BASE + [
            {"name": "n", "type": "integer", "default": 0}]}])
        assert res["dry_run"] and [m["table"] for m in res["tables_to_migrate"]] == ["runs"]
    assert [c["name"] for c in await cols(pg, pid)] == ["test_id"]


async def test_no_plan_and_no_tables_behaves_as_before(pg, monkeypatch):
    wp_b_support.install(monkeypatch, None)
    pid = await project(pg)
    async with pg() as s:
        p = await ProjectService(s).require(pid)
        res = await CodeModuleService(s).create(p, "runs", M1)
        assert res["tables_created"] == [] and res["tables_migrated"] == [] and "plan_version" not in res
        assert [w["code"] for w in res["warnings"]] == ["table_missing"]


# ── with an approved plan ────────────────────────────────────────────────────────────────────────────

async def test_with_a_plan_an_unplanned_table_is_422_and_no_routes_are_written(pg, monkeypatch):
    wp_b_support.install(monkeypatch, wp_b_support.plan_body())
    pid = await project(pg)
    async with pg() as s:
        p = await ProjectService(s).require(pid)
        with pytest.raises(ProjectError) as e:
            await CodeModuleService(s).create(p, "runs", M1, tables=[{"name": "scratchpad", "columns": BASE}])
        assert (e.value.status, e.value.code) == (422, "schema_not_in_plan")
        assert [v["code"] for v in e.value.extra["violations"]] == ["unplanned_table"] and e.value.extra["plan_version"] == 1
    assert await routes(pg, pid) == [] and await cols(pg, pid, "scratchpad") is None


async def test_with_a_plan_an_unplanned_column_and_unplanned_code_use_are_refused(pg, monkeypatch):
    wp_b_support.install(monkeypatch, wp_b_support.plan_body())
    pid = await project(pg)
    async with pg() as s:
        p = await ProjectService(s).require(pid)
        with pytest.raises(ProjectError) as e:
            await CodeModuleService(s).create(p, "runs", M1, tables=[{"name": "runs", "columns": BASE + [{"name": "extra", "type": "string"}]}])
        assert [v["code"] for v in e.value.extra["violations"]] == ["unplanned_column"]
        stray = M1.replace('"runs").insert({"test_id": 1})', '"ghosts").insert({"x": 1})')
        with pytest.raises(ProjectError) as e:
            await CodeModuleService(s).create(p, "runs", stray)
        assert [v["code"] for v in e.value.extra["violations"]] == ["unplanned_table"]
        written = M1.replace('{"test_id": 1}', '{"test_id": 1, "sneaky": 2}')
        with pytest.raises(ProjectError) as e:
            await CodeModuleService(s).create(p, "runs", written, tables=[{"name": "runs", "columns": BASE}])
        assert [(v["code"], v["column"]) for v in e.value.extra["violations"]] == [("unplanned_column", "sneaky")]
    assert await routes(pg, pid) == []


async def test_with_a_plan_tables_are_created_from_the_plan_definition_and_routes_get_plan_schemas(pg, monkeypatch):
    wp_b_support.install(monkeypatch, wp_b_support.plan_body())
    pid = await project(pg)
    async with pg() as s:
        p = await ProjectService(s).require(pid)
        res = await CodeModuleService(s).create(p, "runs", M1, tables=[{"name": "runs", "columns": BASE}])
        assert res["plan_version"] == 1 and res["tables_created"] == ["runs"]
    c = {x["name"]: x for x in await cols(pg, pid)}
    assert c["status"]["enum"] == ["queued", "running", "passed"] and c["run_key"]["unique"] is True     # the plan's, not the declared
    async with pg() as s:
        r = (await s.execute(select(ApiRouteModel).where(ApiRouteModel.project_id == pid, ApiRouteModel.name == "start_run"))).scalars().one()
        assert r.request_schema == {"type": "object", "properties": {"test_ids": {}}, "required": ["test_ids"]}
        assert r.response_schema["properties"] == {"id": {"type": "integer"}, "job_id": {"type": "string"}}
        # a second module's route that is in the plan gets its schema too; one the plan does not know gets none
        await CodeModuleService(s).create(p, "runs_read", M2)
        r2 = (await s.execute(select(ApiRouteModel).where(ApiRouteModel.project_id == pid, ApiRouteModel.name == "get_run"))).scalars().one()
        assert r2.response_schema["properties"] == {"id": {"type": "integer"}} and r2.request_schema is None


async def test_a_planned_stub_adopted_by_a_module_keeps_its_own_schema_and_fills_the_missing_one(pg, monkeypatch):
    wp_b_support.install(monkeypatch, wp_b_support.plan_body())
    pid = await project(pg)
    async with pg() as s:
        p = await ProjectService(s).require(pid)
        from src.services.api_route_service import ApiRouteService
        await ApiRouteService(s).create(p, {"name": "start_run", "method": "POST", "path": "/runs", "handler": {"type": "stub"},
                                            "response_schema": {"type": "object", "properties": {"mine": {"type": "string"}}}})
        res = await CodeModuleService(s).create(p, "runs", M1, tables=[{"name": "runs", "columns": BASE}])
        assert res["routes"]["updated"][0].get("adopted_placeholder") is True
        r = (await s.execute(select(ApiRouteModel).where(ApiRouteModel.project_id == pid, ApiRouteModel.name == "start_run"))).scalars().one()
        assert r.response_schema["properties"] == {"mine": {"type": "string"}} and r.request_schema["required"] == ["test_ids"]


async def test_with_a_plan_existing_tables_are_migrated_to_the_plan_definition(pg, monkeypatch):
    wp_b_support.install(monkeypatch, wp_b_support.plan_body())
    pid = await project(pg)
    async with pg() as s:
        p = await ProjectService(s).require(pid)
        svc = DataTableService(s)
        await svc.create_table(p, {"name": "runs", "columns": BASE})          # created before the plan, by hand
        await svc.insert_rows(await svc.table_by_name(pid, "runs"), [{"test_id": 5}])
        res = await CodeModuleService(s).create(p, "runs", M1, tables=[{"name": "runs", "columns": BASE}])
        ops = [o["op"] for m in res["tables_migrated"] for o in m["ops"]]
        assert "add_column" in ops
    c = {x["name"]: x for x in await cols(pg, pid)}
    assert set(c) == {"test_id", "status", "run_key"}
    async with pg() as s:
        svc = DataTableService(s)
        assert (await svc.list_rows(await svc.table_by_name(pid, "runs")))["rows"][0]["data"]["status"] == "queued"
