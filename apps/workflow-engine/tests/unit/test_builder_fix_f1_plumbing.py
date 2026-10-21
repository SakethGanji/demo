"""Builder plumbing fixes F1: stub adoption, declared tables, app availability, lookup tolerance, secret warnings."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from src.db.models import ApiRouteModel
from src.services import platform_index as pi
from src.services.api_route_service import ApiRouteService
from src.services.code_module_service import CodeModuleService
from src.services.data_table_service import DataTableService
from src.services.project_service import ProjectError, ProjectService
from tests.unit.conftest import anyio_backend, pg, pg_dbname  # noqa: F401

pytestmark = pytest.mark.anyio

SRC = '''
from studio import api, db, secret

@api.get("/coverage")
def coverage(req):
    return {"ok": True}

@api.post("/ask")
def ask(req):
    return {"ok": True}
'''


async def mk(pg):
    async with pg() as s:
        return (await ProjectService(s).create(f"f1-{uuid.uuid4().hex[:8]}")).id


async def stub(pg, pid, method, path, name=None, schema=None):
    async with pg() as s:
        p = await ProjectService(s).require(pid)
        body = {"method": method, "path": path, "handler": {"type": "stub"}, "auth": "none",
                **({"name": name} if name else {}), **({"response_schema": schema} if schema else {})}
        return (await ApiRouteService(s).create(p, body, source="app_stub"))


async def routes(pg, pid):
    async with pg() as s:
        return list((await s.execute(select(ApiRouteModel).where(ApiRouteModel.project_id == pid))).scalars())


async def test_stub_is_adopted_by_module_not_a_conflict(pg):
    pid = await mk(pg)
    await stub(pg, pid, "GET", "/coverage", name="getCoverage", schema={"type": "object"})
    before = {r.path: r for r in await routes(pg, pid)}
    async with pg() as s:
        p = await ProjectService(s).require(pid)
        out = await CodeModuleService(s).create(p, "qa", SRC)
    after = {r.path: r for r in await routes(pg, pid)}
    cov = after["/coverage"]
    assert cov.id == before["/coverage"].id and cov.name == "getCoverage"
    assert cov.handler_type == "python" and cov.handler_function == "coverage" and cov.source == "code"
    assert cov.handler_module_id == out["module"]["id"] and cov.response_schema == {"type": "object"}
    assert [u["path"] for u in out["routes"]["updated"]] == ["/coverage"] and out["routes"]["updated"][0]["adopted_placeholder"]
    assert [c["path"] for c in out["routes"]["created"]] == ["/ask"]
    # the adopted row is now the module's: removing the decorator removes it, and a re-save is stable
    async with pg() as s:
        p = await ProjectService(s).require(pid)
        await CodeModuleService(s).update(p, out["module"]["id"], SRC)
    assert {r.id for r in await routes(pg, pid) if r.path == "/coverage"} == {cov.id}


async def test_real_owner_still_conflicts(pg):
    pid = await mk(pg)
    async with pg() as s:
        p = await ProjectService(s).require(pid)
        await CodeModuleService(s).create(p, "first", 'from studio import api\n@api.get("/coverage")\ndef a(req):\n    return {}\n')
    with pytest.raises(ProjectError) as e:
        async with pg() as s:
            p = await ProjectService(s).require(pid)
            await CodeModuleService(s).create(p, "second", SRC)
    assert e.value.code == "route_conflict"


async def test_dry_run_adopts_too(pg):
    pid = await mk(pg)
    await stub(pg, pid, "GET", "/coverage")
    async with pg() as s:
        p = await ProjectService(s).require(pid)
        out = await CodeModuleService(s).create(p, "qa", SRC, dry_run=True)
    assert out["routes"]["updated"][0]["path"] == "/coverage" and not out["errors"]


TBL_SRC = 'from studio import api, db\n\n@api.get("/items")\ndef items(req):\n    return db.table("items").list()\n'


async def test_declared_tables_are_created_idempotently(pg):
    pid = await mk(pg)
    decl = [{"name": "items", "columns": [{"name": "title", "type": "string", "required": True}]},
            {"name": "bad name", "columns": []}]
    async with pg() as s:
        p = await ProjectService(s).require(pid)
        out = await CodeModuleService(s).create(p, "inv", TBL_SRC, tables=decl)
        tbl = await DataTableService(s).table_by_name(pid, "items")
    assert out["tables_created"] == ["items"] and out["table_errors"][0]["name"] == "bad name"
    assert tbl is not None and [c["name"] for c in tbl.columns] == ["title"]
    async with pg() as s:
        p = await ProjectService(s).require(pid)
        out2 = await CodeModuleService(s).update(p, out["module"]["id"], TBL_SRC + "\n# x\n", tables=decl)
    assert out2["tables_created"] == []


async def test_used_but_undeclared_table_is_warned(pg):
    pid = await mk(pg)
    async with pg() as s:
        p = await ProjectService(s).require(pid)
        out = await CodeModuleService(s).create(p, "inv", TBL_SRC)
    assert any(w["code"] == "table_missing" and w["table"] == "items" for w in out["warnings"])


SEC_SRC = ('from studio import api, secret\nimport httpx\n\n@api.get("/x")\ndef x(req):\n'
           '    return httpx.get("https://api.example.org/v1", headers={"k": secret("API_KEY")}).json()\n')


async def test_secret_without_allowed_hosts_warns_with_hosts(pg, monkeypatch):
    pid = await mk(pg)
    async with pg() as s:
        p = await ProjectService(s).require(pid)
        out = await CodeModuleService(s).create(p, "ext", SEC_SRC)
    w = [w for w in out["warnings"] if w.get("secret") == "API_KEY"]
    assert w and w[0]["code"] == "secret_missing" and "api.example.org" in w[0]["message"]
    from src.services.secret_service import SecretService

    async def fake(self, project, environment=None):
        return [{"key": "API_KEY", "type": "secret", "environment": "dev", "allowed_hosts": None}]

    monkeypatch.setattr(SecretService, "effective", fake)
    async with pg() as s:
        p = await ProjectService(s).require(pid)
        warns = await CodeModuleService(s).secret_warnings(p, SEC_SRC)
    assert warns and warns[0]["code"] == "secret_no_allowed_hosts" and warns[0]["hosts"] == ["api.example.org"]


# ── lookup tools never raise ────────────────────────────────────────────────

@pytest.mark.parametrize("name,args", [
    ("get_route_signature", [{"method": "GET", "path": "/a"}, {"method": "POST", "path": "/b"}]),
    ("get_route_signature", {"method": None, "path": None}),
    ("get_route_signature", {"method": ["GET", "POST"], "path": ["/a", "/b"]}),
    ("get_route_signature", {"routes": [{"method": "GET", "path": "/a"}]}),
    ("get_route_signature", "/a"),
    ("get_table_schema", {"name": ["a", "b"]}),
    ("get_table_schema", {}),
    ("get_component", {"name": None}),
    ("search_components", {"query": None}),
    ("search_sdk", {"query": ["start", "run"]}),
    ("search_project_knowledge", {"query": "x", "kinds": "route"}),
    ("search_project_knowledge", {"query": None, "kinds": None}),
    ("get_template", 5),
])
async def test_lookup_tools_tolerate_odd_args(pg, name, args):
    pid = await mk(pg)
    async with pg() as s:
        out = await pi.dispatch_tool(s, pid, name, args)
    assert isinstance(out, (dict, list))
    if isinstance(out, dict) and out.get("error"):
        assert "join" not in out["error"]


async def test_batched_route_signature_returns_one_result_each(pg):
    pid = await mk(pg)
    async with pg() as s:
        out = await pi.dispatch_tool(s, pid, "get_route_signature", [{"method": "GET", "path": "/a"}, {"path": "/b"}])
    assert len(out) == 2 and all("error" in o for o in out)


def test_template_readme_truncation_is_stated():
    assert "[truncated: " in pi._cut_readme("x" * 2500) and pi._cut_readme("short") == "short"
