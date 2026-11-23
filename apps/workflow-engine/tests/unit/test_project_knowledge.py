"""Project knowledge store: indexers vs the registered routes, ranked retrieval within a token budget, idempotent re-indexing.
Real Postgres (throwaway database); no model."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from src.db.models import ApiRouteModel
from src.services import platform_index as pi
from src.services import project_knowledge as pk
from src.services.code_module_service import CodeModuleService
from src.services.data_table_service import DataTableService
from src.services.project_service import ProjectService
from tests.unit.conftest import anyio_backend, pg, pg_dbname  # noqa: F401

pytestmark = pytest.mark.anyio

MODULE = '''
from studio import api
import studio

@api.get("/cases")
def list_cases(req):
    """List cases, optionally by status."""
    status = req.query.get("status")
    rows = studio.db.table("cases").all()
    return {"items": rows, "total": len(rows)}

@api.post("/cases/{id}/approve", auth="app_token")
def approve_case(req):
    """Approve a triage fix for one case."""
    body = req.json() or {}
    note = body.get("note")
    who = body["approver"]
    if not who:
        raise studio.HTTPError(400, {"error": "approver"})
    studio.db.table("cases").update(req.params["id"], {"status": "approved", "note": note})
    return {"id": req.params["id"], "status": "approved"}

@api.post("/runs")
def start_run(req):
    """Start a test run and return its job id."""
    job = studio.jobs.start({"type": "test-run"})
    return {"job_id": job["job_id"]}
'''


async def new_project(sf) -> str:
    async with sf() as s:
        return (await ProjectService(s).create(f"pk-{uuid.uuid4().hex[:8]}")).id


async def test_index_module_rows_match_the_registered_routes(pg):
    pid = await new_project(pg)
    async with pg() as s:
        project = await ProjectService(s).require(pid)
        await CodeModuleService(s).create(project, "cases_api", MODULE)
        res = await pk.index_module(s, pid, MODULE, "cases_api")
        assert res["routes"] == 3 and res["created"] == 3
        registered = (await s.execute(select(ApiRouteModel).where(ApiRouteModel.project_id == pid))).scalars().all()
        rows = await pk.list_kind(s, pid, "route")
    want = {(r.method, r.path, r.handler_function) for r in registered}
    got = {(r["body"]["method"], r["body"]["path"], r["body"]["function"]) for r in rows}
    assert want == got and len(got) == 3
    approve = next(r for r in rows if r["key"] == "POST /cases/{id}/approve")["body"]
    assert approve["auth"] == "app_token" and approve["handler"] == "python module cases_api"
    assert approve["path_params"] == ["id"] and approve["body"] == ["note", "approver"]
    assert approve["response"] == ["id", "status"] and 400 in approve["statuses"] and approve["tables"] == ["cases"]
    listing = next(r for r in rows if r["key"] == "GET /cases")["body"]
    assert listing["query"] == ["status"] and listing["response"] == ["items", "total"]
    assert next(r for r in rows if r["key"] == "POST /runs")["body"]["uses_jobs"] is True


async def test_reindexing_is_idempotent_and_prunes_removed_routes(pg):
    pid = await new_project(pg)
    async with pg() as s:
        await pk.index_module(s, pid, MODULE, "m")
        first = {r["key"]: r["version"] for r in await pk.list_kind(s, pid, "route")}
        again = await pk.index_module(s, pid, MODULE, "m")
        assert again["unchanged"] == 3 and again["created"] == 0 and again["updated"] == 0 and again["pruned"] == 0
        assert {r["key"]: r["version"] for r in await pk.list_kind(s, pid, "route")} == first  # no version bump
        slim = MODULE.split('@api.post("/runs")')[0]
        res = await pk.index_module(s, pid, slim, "m")
        assert res["pruned"] == 1 and "POST /runs" not in {r["key"] for r in await pk.list_kind(s, pid, "route")}
        broken = await pk.index_module(s, pid, "def (", "m")  # a module that does not parse leaves the rows alone
        assert broken["error"] and len(await pk.list_kind(s, pid, "route")) == 2


async def test_upsert_versions_status_and_validation(pg):
    pid = await new_project(pg)
    async with pg() as s:
        a = await pk.upsert(s, pid, "decision", "auth", title="Use SSO", body={"why": "policy"})
        assert (a["version"], a["status"], a["changed"]) == (1, "draft", "created")
        same = await pk.upsert(s, pid, "decision", "auth", title="Use SSO", body={"why": "policy"})
        assert same["changed"] == "unchanged" and same["version"] == 1
        b = await pk.upsert(s, pid, "decision", "auth", title="Use SSO", body={"why": "policy v2"}, status="approved")
        assert (b["version"], b["status"], b["changed"]) == (2, "approved", "updated")
        kept = await pk.upsert(s, pid, "decision", "auth", title="Use SSO", body={"why": "policy v2"})
        assert kept["status"] == "approved" and kept["version"] == 2  # status=None keeps the existing one
        for bad in ({"kind": "nope"}, {"kind": "spec", "status": "final"}, {"kind": "spec", "source": "x"}):
            with pytest.raises(pk.KnowledgeError):
                await pk.upsert(s, pid, key="k", **bad)


async def test_retrieve_ranks_by_relevance_and_filters_kinds(pg):
    pid = await new_project(pg)
    async with pg() as s:
        await pk.index_module(s, pid, MODULE, "m")
        await pk.upsert(s, pid, "failure", "approve-500", title="approve triage fix returned 500",
                        body={"symptom": "approve route 500", "fix": "check approver"}, source="failure")
        await pk.upsert(s, pid, "requirement", "r1", title="Weekly report", body={"text": "email a weekly summary"})
        out = await pk.retrieve(s, pid, "approve triage fix")
        keys = [i["key"] for i in out["items"]]
        assert "POST /cases/{id}/approve" in keys[:2] and "approve-500" in keys[:2] and "r1" not in keys
        only = await pk.retrieve(s, pid, "approve", kinds=["route"])
        assert {i["kind"] for i in only["items"]} == {"route"}
        start = await pk.retrieve(s, pid, "start run")
        assert start["items"][0]["key"] == "POST /runs"
        exact = await pk.retrieve(s, pid, "GET /cases")
        assert exact["items"][0]["key"] == "GET /cases"
        with pytest.raises(pk.KnowledgeError):
            await pk.retrieve(s, pid, "x", kinds=["bogus"])


async def test_retrieve_never_exceeds_the_budget_and_marks_truncation(pg):
    pid = await new_project(pg)
    async with pg() as s:
        for i in range(30):
            await pk.upsert(s, pid, "decision", f"d{i}", title=f"orders decision {i}",
                            body={"detail": "orders pipeline " + "word " * 40 + str(i)})
        for budget in (300, 120, 40, 12, 4, 1):
            out = await pk.retrieve(s, pid, "orders decision", token_budget=budget)
            assert pk.est_tokens(out["text"]) <= budget and out["tokens"] <= budget, budget
            assert out["truncated"] is True
        big = await pk.retrieve(s, pid, "orders decision", token_budget=300)
        assert "more matching rows omitted" in big["text"] and big["omitted"] > 0 and big["items"]
        roomy = await pk.retrieve(s, pid, "orders decision", token_budget=20000)
        assert roomy["truncated"] is False and len(roomy["items"]) == 30 and "omitted" not in roomy["text"]


async def test_index_tables_and_getters(pg):
    pid = await new_project(pg)
    async with pg() as s:
        project = await ProjectService(s).require(pid)
        await DataTableService(s).create_table(project, {"name": "cases", "columns": [
            {"name": "title", "type": "string", "required": True}, {"name": "status", "type": "string", "default": "open"}]})
        res = await pk.index_tables(s, pid)
        assert res["tables"] == 1 and res["created"] == 1
        assert (await pk.index_tables(s, pid))["unchanged"] == 1
        schema = await pi.get_table_schema(s, pid, "cases")
        assert [c["name"] for c in schema["columns"]] == ["title", "status"] and schema["columns"][1]["default"] == "open"
        assert await pi.get_table_schema(s, pid, "nope") is None
        await pk.index_module(s, pid, MODULE, "m")
        sig = await pi.get_route_signature(s, pid, "post", "/cases/{case_id}/approve")  # param names may differ
        assert sig and sig["function"] == "approve_case"
        assert (await pi.dispatch_tool(s, pid, "get_table_schema", {"name": "cases"}))["name"] == "cases"
        assert (await pi.dispatch_tool(s, pid, "search_project_knowledge", {"query": "approve case"}))["items"]


APP = [
    {"path": "src/App.tsx", "content": "/** Case triage app */\nimport { DataTable, Button } from '../kit'\nimport { api } from '../api/client'\n"
     "export default function App() {\n  const r = api.getCases()\n  return <DataTable rows={[]} columns={[]} rowKey={(r) => r.id} "
     "filters={[{ id: 'a', label: 'A', test: (x) => x.a > 1 }]} />\n}\n"},
    {"path": "src/views/Detail.tsx", "content": "import { Drawer } from '../kit'\nimport { api } from '../api/client'\n"
     "export function Detail() {\n  api.postCasesApprove(1)\n  return <Drawer open onClose={() => {}} title=\"x\">hi</Drawer>\n}\n"},
    {"path": "src/kit/ui.tsx", "content": "export function Button() { return null }"},
]


async def test_index_app_screens_and_component_use_are_idempotent(pg):
    pid = await new_project(pg)
    async with pg() as s:
        res = await pk.index_app(s, pid, APP)
        assert res["screens"] == 2 and res["component_uses"] == 2 and res["created"] == 4
        screens = {r["key"]: r["body"] for r in await pk.list_kind(s, pid, "screen")}
        assert screens["src/views/Detail.tsx"]["api_calls"] == ["postCasesApprove"]
        assert screens["src/App.tsx"]["kit_components"] == ["DataTable"] and screens["src/App.tsx"]["title"] == "Case triage app"
        use = (await pk.get(s, pid, "component_use", "src/App.tsx#DataTable"))["body"]
        assert use["props_used"] == ["columns", "filters", "rowKey", "rows"] and "src/kit/ui.tsx#Button" not in str(screens)
        again = await pk.index_app(s, pid, APP)
        assert again["unchanged"] == 4 and again["pruned"] == 0
        gone = await pk.index_app(s, pid, APP[:1])
        assert gone["pruned"] == 2  # the Detail screen and its Drawer use


async def test_reindex_project_rebuilds_from_modules_and_tables(pg):
    pid = await new_project(pg)
    async with pg() as s:
        project = await ProjectService(s).require(pid)
        await CodeModuleService(s).create(project, "cases_api", MODULE)
        out = await pk.reindex_project(s, pid)
        assert out["rows_by_kind"]["route"] == 3
        again = await pk.reindex_project(s, pid)
        assert all(m["created"] == 0 and m["updated"] == 0 for m in again["modules"])
