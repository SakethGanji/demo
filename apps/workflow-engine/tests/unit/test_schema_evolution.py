"""Additive schema evolution of data tables (rows are JSONB, columns are metadata): backfill, unique / enum enforcement,
the recorded migrations table, and destructive ops refused. Real Postgres (throwaway database); no model."""

from __future__ import annotations

import uuid

import pytest

from src.services.data_table_service import DataTableService
from src.services.project_service import ProjectError, ProjectService
from tests.unit import wp_b_support
from tests.unit.conftest import anyio_backend, pg, pg_dbname  # noqa: F401

pytestmark = pytest.mark.anyio


async def make(pg, columns, rows=()):
    async with pg() as s:
        p = await ProjectService(s).create(f"se-{uuid.uuid4().hex[:8]}")
        svc = DataTableService(s)
        t = await svc.create_table(p, {"name": "runs", "columns": columns})
        if rows:
            await svc.insert_rows(await svc.table_by_name(p.id, "runs"), list(rows))
        return p.id, t["id"]


async def with_svc(pg, pid):
    s = pg()
    sess = await s.__aenter__()
    p = await ProjectService(sess).require(pid)
    return s, sess, p, DataTableService(sess)


async def rows_of(pg, pid):
    async with pg() as s:
        svc = DataTableService(s)
        t = await svc.table_by_name(pid, "runs")
        return [r["data"] for r in (await svc.list_rows(t, sort="id"))["rows"]], t.columns


async def expect_error(coro, status, code):
    with pytest.raises(ProjectError) as e:
        await coro
    assert (e.value.status, e.value.code) == (status, code), (e.value.status, e.value.code, e.value.message)
    return e.value


async def test_add_column_with_default_backfills_rows_and_update_row_still_works(pg):
    pid, _ = await make(pg, [{"name": "title", "type": "string"}], [{"title": "a"}, {"title": "b"}])
    async with pg() as s:
        p = await ProjectService(s).require(pid)
        svc = DataTableService(s)
        ops = [{"op": "add_column", "column": {"name": "status", "type": "string", "required": True, "default": "queued",
                                               "enum": ["queued", "done"]}}]
        res = await svc.apply_migration(p, "runs", ops, plan_version=3, actor="tester")
        assert res["backfilled"] == 2 and not res["noop"]
        t = await svc.table_by_name(pid, "runs")
        rows = (await svc.list_rows(t, sort="id"))["rows"]
        assert [r["data"]["status"] for r in rows] == ["queued", "queued"]
        again = await svc.update_row(t, rows[0]["id"], {"title": "a2"}, merge=True)   # required column present: still valid
        assert again.data == {"title": "a2", "status": "queued"}
        hist = await svc.migrations(p, "runs")
        assert len(hist) == 1 and hist[0]["plan_version"] == 3 and hist[0]["applied_by"] == "tester" and hist[0]["ops"] == ops


async def test_required_column_without_default_on_a_table_with_rows_is_refused_and_changes_nothing(pg):
    pid, _ = await make(pg, [{"name": "title", "type": "string"}], [{"title": "a"}])
    async with pg() as s:
        p = await ProjectService(s).require(pid)
        err = await expect_error(DataTableService(s).apply_migration(
            p, "runs", [{"op": "add_column", "column": {"name": "k", "type": "string", "required": True}}]), 422, "schema_change_refused")
        assert err.extra["ops"]
    data, cols = await rows_of(pg, pid)
    assert [c["name"] for c in cols] == ["title"] and data == [{"title": "a"}]


@pytest.mark.parametrize("op", [
    {"op": "drop_column", "column": "title"},
    {"op": "rename_column", "column": "title", "to": "name"},
    {"op": "type_change", "column": "title", "type": "integer"},
    {"op": "remove_enum_values", "column": "title", "values": ["x"]},
    {"op": "drop_table"},
    {"op": "add_column", "column": {"name": "title", "type": "integer"}},          # same name, other type = a type change
])
async def test_destructive_ops_are_422_and_change_nothing(pg, op):
    pid, _ = await make(pg, [{"name": "title", "type": "string"}], [{"title": "a"}])
    async with pg() as s:
        p = await ProjectService(s).require(pid)
        await expect_error(DataTableService(s).apply_migration(p, "runs", [op]), 422, "schema_change_refused")
    data, cols = await rows_of(pg, pid)
    assert cols == [{"name": "title", "type": "string", "required": False}] and data == [{"title": "a"}]


async def test_a_refused_op_in_a_batch_rolls_back_the_good_ones(pg):
    pid, _ = await make(pg, [{"name": "title", "type": "string"}], [{"title": "a"}])
    async with pg() as s:
        p = await ProjectService(s).require(pid)
        await expect_error(DataTableService(s).apply_migration(p, "runs", [
            {"op": "add_column", "column": {"name": "n", "type": "integer", "default": 0}},
            {"op": "drop_column", "column": "title"}]), 422, "schema_change_refused")
    data, cols = await rows_of(pg, pid)
    assert data == [{"title": "a"}] and len(cols) == 1


async def test_set_unique_with_duplicates_is_409_with_samples_and_unique_rows_pass(pg):
    pid, _ = await make(pg, [{"name": "k", "type": "string"}], [{"k": "x"}, {"k": "x"}, {"k": "y"}])
    async with pg() as s:
        p = await ProjectService(s).require(pid)
        e = await expect_error(DataTableService(s).apply_migration(p, "runs", [{"op": "set_unique", "column": "k"}]),
                               409, "unique_conflict")
        assert e.extra["column"] == "k" and e.extra["samples"][0]["value"] == "x" and len(e.extra["samples"][0]["row_ids"]) == 2
    _, cols = await rows_of(pg, pid)
    assert not cols[0].get("unique")
    async with pg() as s:
        p = await ProjectService(s).require(pid)
        svc = DataTableService(s)
        t = await svc.table_by_name(pid, "runs")
        for r in (await svc.list_rows(t))["rows"]:
            if r["data"]["k"] == "x":
                await svc.update_row(t, r["id"], {"k": "x" + str(r["id"])}, merge=True)
        res = await svc.apply_migration(p, "runs", [{"op": "set_unique", "column": "k"}])
        assert res["applied"] and res["columns"][0]["unique"] is True


async def test_unique_is_enforced_on_insert_and_update_and_null_never_collides(pg):
    pid, _ = await make(pg, [{"name": "k", "type": "string", "unique": True}, {"name": "n", "type": "integer"}])
    async with pg() as s:
        svc = DataTableService(s)
        t = await svc.table_by_name(pid, "runs")
        a, b = [r.id for r in await svc.insert_rows(t, [{"k": "a"}, {"k": "b"}])]
        e = await expect_error(svc.insert_rows(t, [{"k": "a"}]), 409, "unique_violation")
        assert e.extra["column"] == "k" and e.extra["value"] == "a"
        await expect_error(svc.insert_rows(t, [{"k": "z"}, {"k": "z"}]), 409, "unique_violation")   # repeated in one request
        await expect_error(svc.update_row(t, b, {"k": "a"}, merge=True), 409, "unique_violation")
        assert (await svc.update_row(t, a, {"k": "a", "n": 2}, merge=True)).data == {"k": "a", "n": 2}  # own value is fine
        await svc.insert_rows(t, [{"n": 1}, {"n": 2}, {"k": None}])                                   # nulls do not collide
        assert (await svc.list_rows(t, count=True))["count"] == 5


async def test_enum_is_enforced_on_insert_and_update_through_the_datatable_node_path(pg):
    from datetime import datetime

    from src.engine.types import ExecutionContext, NodeData, NodeDefinition, Workflow
    from src.nodes.data.data_table import DataTableNode
    
    pid, _ = await make(pg, [{"name": "status", "type": "string", "required": True, "enum": ["queued", "done"], "default": "queued"}])
    ctx = ExecutionContext(workflow=Workflow(name="w", nodes=[], connections=[]), execution_id="e1",
                           start_time=datetime.now(), mode="manual", project_id=pid)

    async def run(**params):
        return await DataTableNode().execute(ctx, NodeDefinition(name="T", type="DataTable", parameters={"table": "runs", **params}),
                                             [NodeData(json={})])

    out = await run(operation="insert", data={"status": "done"})
    rid = out.outputs["main"][0].json["id"]
    with pytest.raises(RuntimeError) as e:
        await run(operation="insert", data={"status": "bogus"})
    assert e.value.code == "row_invalid"
    with pytest.raises(RuntimeError) as e:
        await run(operation="update", rowId=rid, data={"status": "bogus"})
    assert e.value.code == "row_invalid"
    assert (await run(operation="update", rowId=rid, data={"status": "queued"})).outputs["main"][0].json["status"] == "queued"


async def test_enum_and_metadata_survive_validation_and_the_service_rejects_bad_enums(pg):
    pid, _ = await make(pg, [{"name": "s", "type": "string", "enum": ["a", "b"], "ref": "tests.id", "index": True, "description": "d"}])
    _, cols = await rows_of(pg, pid)
    assert cols[0] == {"name": "s", "type": "string", "required": False, "enum": ["a", "b"], "ref": "tests.id", "index": True,
                       "description": "d"}
    with pytest.raises(ProjectError):
        DataTableService._validate_columns([{"name": "s", "type": "string", "enum": []}])
    with pytest.raises(ProjectError):
        DataTableService._validate_columns([{"name": "s", "type": "integer", "enum": ["a"]}])
    with pytest.raises(ProjectError):
        DataTableService._validate_columns([{"name": "s", "type": "string", "ref": "bad ref"}])


async def test_add_enum_values_widens_and_constraining_a_free_column_checks_rows(pg):
    pid, _ = await make(pg, [{"name": "s", "type": "string", "enum": ["a"]}, {"name": "f", "type": "string"}],
                        [{"s": "a", "f": "x"}])
    async with pg() as s:
        p = await ProjectService(s).require(pid)
        svc = DataTableService(s)
        res = await svc.apply_migration(p, "runs", [{"op": "add_enum_values", "column": "s", "values": ["b", "a"]}])
        assert res["columns"][0]["enum"] == ["a", "b"]
        await expect_error(svc.apply_migration(p, "runs", [{"op": "add_enum_values", "column": "f", "values": ["y"]}]),
                           409, "rows_incompatible")


async def test_a_migration_applied_twice_is_a_noop_and_recorded_once(pg):
    pid, _ = await make(pg, [{"name": "title", "type": "string"}], [{"title": "a"}])
    ops = [{"op": "add_column", "column": {"name": "n", "type": "integer", "default": 7}}, {"op": "set_index", "column": "title"}]
    async with pg() as s:
        p = await ProjectService(s).require(pid)
        svc = DataTableService(s)
        first = await svc.apply_migration(p, "runs", ops)
        second = await svc.apply_migration(p, "runs", ops)
        assert first["applied"] and not first["noop"] and second["noop"] and second["applied"] == []
        assert len(await svc.migrations(p, "runs")) == 1
    data, cols = await rows_of(pg, pid)
    assert data == [{"title": "a", "n": 7}] and cols[0]["index"] is True


async def test_dry_run_reports_without_writing(pg):
    pid, _ = await make(pg, [{"name": "title", "type": "string"}], [{"title": "a"}])
    async with pg() as s:
        p = await ProjectService(s).require(pid)
        svc = DataTableService(s)
        res = await svc.apply_migration(p, "runs", [{"op": "add_column", "column": {"name": "n", "type": "integer", "default": 1}}],
                                        dry_run=True)
        assert res["dry_run"] and res["backfilled"] == 1 and res["applied"]
        assert await svc.migrations(p, "runs") == []
    data, cols = await rows_of(pg, pid)
    assert data == [{"title": "a"}] and len(cols) == 1


async def test_ensure_tables_migrate_applies_additive_and_reports_refused(pg, monkeypatch):
    wp_b_support.install(monkeypatch, None)
    pid, _ = await make(pg, [{"name": "title", "type": "string"}, {"name": "n", "type": "integer"}], [{"title": "a", "n": 1}])
    async with pg() as s:
        p = await ProjectService(s).require(pid)
        svc = DataTableService(s)
        declared = [{"name": "runs", "columns": [{"name": "title", "type": "string"},
                                                 {"name": "state", "type": "string", "default": "new"}]}]   # n not mentioned = kept
        plain = await svc.ensure_tables(p, declared)
        assert plain["existing"] == ["runs"] and plain["migrated"] == [] and plain["refused"] == []
        res = await svc.ensure_tables(p, declared, migrate=True, plan_version=2)
        assert res["migrated"][0]["table"] == "runs" and res["refused"] == [] and res["errors"] == []
        bad = await svc.ensure_tables(p, [{"name": "runs", "columns": [{"name": "title", "type": "integer"}]}], migrate=True)
        assert bad["refused"] and bad["refused"][0]["table"] == "runs" and bad["migrated"] == []
    data, cols = await rows_of(pg, pid)
    assert data == [{"title": "a", "n": 1, "state": "new"}] and [c["name"] for c in cols] == ["title", "n", "state"]


async def test_update_table_keeps_metadata_a_client_did_not_send_and_checks_enum_against_rows(pg):
    pid, tid = await make(pg, [{"name": "s", "type": "string", "unique": True, "enum": ["a", "b"]}], [{"s": "a"}])
    async with pg() as s:
        p = await ProjectService(s).require(pid)
        svc = DataTableService(s)
        out = await svc.update_table(p, tid, {"columns": [{"name": "s", "type": "string", "required": False}]})
        assert out["columns"][0]["unique"] is True and out["columns"][0]["enum"] == ["a", "b"]
        await expect_error(svc.update_table(p, tid, {"columns": [{"name": "s", "type": "string", "enum": ["b"]}]}),
                           409, "rows_incompatible")


async def test_migrate_endpoint_applies_dry_runs_and_refuses(pg):
    import httpx
    from fastapi import FastAPI

    from src.core.dependencies import get_db_session
    from src.routes import tables as tables_routes

    pid, tid = await make(pg, [{"name": "title", "type": "string"}], [{"title": "a"}])
    app = FastAPI()
    app.include_router(tables_routes.router)

    async def session():
        async with pg() as s:
            yield s
    app.dependency_overrides[get_db_session] = session
    add = [{"op": "add_column", "column": {"name": "n", "type": "integer", "default": 3}}]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        base = f"/projects/{pid}/tables/{tid}"
        r = await c.post(f"{base}/migrate", json={"ops": add, "dry_run": True})
        assert r.status_code == 200 and r.json()["dry_run"] is True and r.json()["backfilled"] == 1
        assert (await c.get(f"{base}/migrations")).json() == []
        r = await c.post(f"{base}/migrate", json={"ops": add, "plan_version": 4}, headers={"x-user-id": "ana"})
        assert r.status_code == 200 and r.json()["applied"] == add
        hist = (await c.get(f"{base}/migrations")).json()
        assert len(hist) == 1 and hist[0]["plan_version"] == 4 and hist[0]["applied_by"] == "ana"
        r = await c.post(f"{base}/migrate", json={"ops": [{"op": "drop_column", "column": "title"}]})
        assert r.status_code == 422 and r.json()["detail"]["code"] == "schema_change_refused"
        r = await c.post(f"{base}/migrate", json={"ops": []})
        assert r.status_code == 422
    data, cols = await rows_of(pg, pid)
    assert data == [{"title": "a", "n": 3}] and [c["name"] for c in cols] == ["title", "n"]


async def test_set_required_set_default_and_the_other_diff_table_ops(pg):
    pid, _ = await make(pg, [{"name": "a", "type": "string", "unique": True}, {"name": "b", "type": "string"},
                             {"name": "c", "type": "string", "required": True}],
                        [{"a": "x", "b": "y", "c": "z"}, {"a": "w", "c": "z"}])
    async with pg() as s:
        p = await ProjectService(s).require(pid)
        svc = DataTableService(s)
        await expect_error(svc.apply_migration(p, "runs", [{"op": "set_required", "column": "b", "value": True}]), 422, "schema_change_refused")
        res = await svc.apply_migration(p, "runs", [
            {"op": "set_default", "column": "b", "default": "d"}, {"op": "set_required", "column": "b", "value": True},   # backfills the gap
            {"op": "set_required", "column": "c", "value": False}, {"op": "set_unique", "column": "a", "value": False},
            {"op": "set_index", "columns": ["a"]}, {"op": "set_index", "columns": ["a", "b"]},                           # composite: no effect
            {"op": "set_description", "column": None, "description": "runs table"}, {"op": "set_ref", "column": "a", "ref": None}])
        by = {c["name"]: c for c in res["columns"]}
        assert by["b"]["required"] is True and by["b"]["default"] == "d" and "unique" not in by["a"] and by["a"]["index"] is True
        assert by["c"]["required"] is False and res["backfilled"] == 1
        t = await svc.table_by_name(pid, "runs")
        assert t.description == "runs table" and [r["data"].get("b") for r in (await svc.list_rows(t, sort="id"))["rows"]] == ["y", "d"]


async def test_migrate_accepts_exactly_what_diff_table_produces(pg):
    from src.services.schema_migrations import diff_table

    pid, _ = await make(pg, [{"name": "title", "type": "string"}], [{"title": "a"}])
    planned = {"name": "runs", "columns": [
        {"name": "title", "type": "string", "unique": True, "index": True, "description": "t"},
        {"name": "status", "type": "string", "required": True, "default": "q", "enum": ["q", "r"], "ref": "tests.status"}]}
    async with pg() as s:
        p = await ProjectService(s).require(pid)
        svc = DataTableService(s)
        t = await svc.table_by_name(pid, "runs")
        d = diff_table(t.columns, planned, 1)
        assert d["refused"] == [] and d["ops"]
        await svc.apply_migration(p, "runs", d["ops"], plan_version=2)
        t = await svc.table_by_name(pid, "runs")
        assert diff_table(t.columns, planned, 1) == {"ops": [], "refused": []}        # converged
