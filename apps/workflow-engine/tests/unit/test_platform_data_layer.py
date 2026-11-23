"""Platform data layer: studio.db rows are literal data (never expression/secret evaluated), module saves reconcile
stacked route decorators idempotently, ProjectError never leaks as a 500, secrets report the envs they are missing in.
Real Postgres (throwaway database); no model."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from src.db.models import ApiRouteModel, DataTableRowModel
from src.services.code_module_service import CodeModuleService
from src.services.data_table_service import DataTableService
from src.services.project_service import ProjectError, ProjectService
from src.services.secret_service import SecretService
from tests.unit.conftest import anyio_backend, pg, pg_dbname  # noqa: F401

pytestmark = pytest.mark.anyio

LITERALS = ["{{ $vars.X }}", "{{ 1+1 }}", "{{totp:key}}", "{{var}}", "a {{ $vars.SECRET_KEY }} b", "{{ $json.nope }}"]


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    from cryptography.fernet import Fernet

    from src.core.config import settings
    from src.engine.node_registry import node_registry, register_all_nodes

    if not node_registry.list():
        register_all_nodes()
    monkeypatch.setattr(settings, "encryption_key", Fernet.generate_key().decode(), raising=False)


async def project(pg):
    async with pg() as s:
        p = await ProjectService(s).create(f"pd-{uuid.uuid4().hex[:8]}")
        return p.id, p.team_id


async def broker_call(pg, pid, team, params, monkeypatch, env="default"):
    from src.broker.service import BrokerService
    from src.broker.tokens import BrokerClaims

    async def nolog(self, *a, **k):
        return None

    monkeypatch.setattr(BrokerService, "_log", nolog)
    c = BrokerClaims(project_id=pid, team_id=team, environment=env, execution_id=None, origin="python_route")
    return await BrokerService(session_factory=pg).call(c, "data_table", None, params, None)


async def test_studio_db_values_are_stored_and_read_back_literally(pg, monkeypatch):
    pid, team = await project(pg)
    async with pg() as s:
        p = await ProjectService(s).require(pid)
        await DataTableService(s).create_table(p, {"name": "notes", "columns": [{"name": "body", "type": "string"}]})
        # a real secret that user text must never be able to resolve
        await SecretService(s).create(p, {"key": "SECRET_KEY", "value": "hunter2-very-secret", "type": "secret"})
    for text in LITERALS:
        st, body = await broker_call(pg, pid, team, {"table": "notes", "operation": "insert", "data": {"body": text}}, monkeypatch)
        assert st == 200, (text, body)
        assert body["items"][0].get("body") == text, body
        rid = body["items"][0]["id"]
        st, got = await broker_call(pg, pid, team, {"table": "notes", "operation": "get", "rowId": rid}, monkeypatch)
        assert st == 200 and got["items"][0]["body"] == text
        st, upd = await broker_call(pg, pid, team, {"table": "notes", "operation": "update", "rowId": rid,
                                                    "data": {"body": text + "!"}}, monkeypatch)
        assert st == 200 and upd["items"][0]["body"] == text + "!"
    async with pg() as s:
        raw = (await s.execute(select(DataTableRowModel.data))).scalars().all()
    assert "hunter2-very-secret" not in str(raw)


async def test_workflow_node_params_still_evaluate_expressions(pg):
    """The author's own node parameters keep working: only broker (code-origin) calls are literal."""
    from datetime import datetime

    from src.engine.types import ExecutionContext, NodeData, NodeDefinition, Workflow
    from src.nodes.data.data_table import DataTableNode

    pid, _ = await project(pg)
    async with pg() as s:
        p = await ProjectService(s).require(pid)
        await DataTableService(s).create_table(p, {"name": "notes", "columns": [{"name": "body", "type": "string"}]})

    def ctx(**extras):
        c = ExecutionContext(workflow=Workflow(name="w", nodes=[], connections=[]), execution_id="e1",
                             start_time=datetime.now(), mode="manual", project_id=pid)
        c.extras.update(extras)
        return c

    node = NodeDefinition(name="T", type="DataTable", parameters={
        "table": "notes", "operation": "insert", "data": {"body": "x {{ 1+1 }}"}})
    out = await DataTableNode().execute(ctx(), node, [NodeData(json={})])
    assert str(out.outputs["main"][0].json["body"]) == "x 2"          # workflow author: evaluated, as before
    out = await DataTableNode().execute(ctx(literal_params=True), node, [NodeData(json={})])
    assert out.outputs["main"][0].json["body"] == "x {{ 1+1 }}"       # code/user data: literal


async def test_stacked_decorators_save_and_resave_idempotently(pg):
    pid, _ = await project(pg)
    v1 = '''
from studio import api

@api.get("/a")
@api.get("/a/x")
def a(req):
    return {}

@api.get("/b")
def b(req):
    return {}
'''
    v2 = v1.replace('@api.get("/a/x")', '@api.get("/a/y")\n@api.get("/a/z")')
    v3 = '''
from studio import api

@api.get("/a/y")
def a(req):
    return {}
'''
    async with pg() as s:
        p = await ProjectService(s).require(pid)
        svc = CodeModuleService(s)
        out = await svc.create(p, "stack", v1)
        mid = out["module"]["id"]
        assert len(out["routes"]["created"]) == 3
        for src, n in ((v1, 3), (v2, 4), (v2, 4), (v3, 1), (v1, 3)):
            res = await svc.update(p, mid, src)
            rows = (await s.execute(select(ApiRouteModel).where(ApiRouteModel.project_id == pid))).scalars().all()
            assert len(rows) == n, (n, [(r.method, r.path) for r in rows])
            assert len({(r.method, r.path_shape) for r in rows}) == n
            assert all(i.get("id") for i in res["routes"]["created"] + res["routes"]["updated"])
        again = await svc.update(p, mid, v1)
        assert again["routes"]["created"] == [] and again["routes"]["removed"] == []


async def test_same_route_name_twice_in_a_module_is_a_422_not_a_500(pg):
    pid, _ = await project(pg)
    src = '''
from studio import api

@api.get("/a", name="dup")
def a(req):
    return {}

@api.get("/b", name="dup")
def b(req):
    return {}
'''
    async with pg() as s:
        p = await ProjectService(s).require(pid)
        with pytest.raises(ProjectError) as e:
            await CodeModuleService(s).create(p, "dupn", src)
    assert e.value.status == 422


async def test_project_error_from_generation_is_a_4xx_json(pg, monkeypatch):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from src.core.dependencies import get_db_session
    from src.routes import modules
    from src.services import module_generator as mg

    class Boom:
        def __init__(self, *a, **k):
            self.last_problems = []

        async def events(self):
            raise ProjectError(422, "invalid_module", "bad module")
            yield  # pragma: no cover

    pid, _ = await project(pg)
    monkeypatch.setattr(mg, "Generation", Boom)
    monkeypatch.setattr("src.engine.llm_provider.is_configured", lambda *_: True)
    app = FastAPI()
    app.include_router(modules.router, prefix="/api")

    async def sess():
        async with pg() as s:
            yield s

    app.dependency_overrides[get_db_session] = sess
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(f"/api/projects/{pid}/modules/generate", json={"prompt": "x"}, headers={"accept": "application/json"})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "invalid_module", r.text


async def test_missing_secret_names_the_envs_that_have_it_and_list_flags_missing_in(pg, monkeypatch):
    pid, team = await project(pg)
    async with pg() as s:
        p = await ProjectService(s).require(pid)
        envs = [e["name"] for e in p.environments]
        assert len(envs) >= 2
        only, other = envs[0], envs[1]
        await SecretService(s).create(p, {"key": "ONLY_HERE", "value": "v", "type": "string", "environment": only})
        rows = await SecretService(s).effective(p)
    row = next(r for r in rows if r["key"] == "ONLY_HERE")
    assert row["defined_in"] == [only] and other in row["missing_in"] and only not in row["missing_in"]
    from src.broker.service import BrokerService
    from src.broker.tokens import BrokerClaims
    c = BrokerClaims(project_id=pid, team_id=team, environment=other, execution_id=None, origin="python_route")
    _st, body = await BrokerService(session_factory=pg).call(
        c, "http_request", None, {"url": "https://example.com/{{ $vars.ONLY_HERE }}", "method": "GET"}, None)
    assert body["error"]["code"] == "missing_secret"
    assert only in body["error"]["message"] and body["error"]["defined_in"] == [only]
