"""Public gate endpoints' service layer: the builders' own gates run on SAVED artifacts (services/builder_gates.py).
Real Postgres (throwaway database) and the real sandbox for the module self-test; no model."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from src.db.models import DataTableModel
from src.sandbox.backend import availability
from src.services import builder_gates as bg
from src.services.app_validate import OpSig
from src.services.code_module_service import CodeModuleService
from src.services.data_table_service import DataTableService
from src.services.project_service import ProjectService
from tests.unit.conftest import anyio_backend, pg, pg_dbname  # noqa: F401

pytestmark = pytest.mark.anyio

needs_sandbox = pytest.mark.skipif(not availability()[0], reason="sandbox backend unavailable")

TEMPLATE = '''from studio import api, HTTPError
import studio


def _tbl():
    return studio.db.table("cases")


@api.post("/cases")
def create_case(req):
    """Create a case. Returns the row"""
    body = req.json() or {}
    if not body.get("title"):
        raise HTTPError(400, {"error": "title required"})
    return _tbl().insert({"title": body["title"], "kind": body.get("kind", "ui")})


@api.get("/cases")
def list_cases(req):
    """List cases, optionally by kind. Returns {rows}"""
    __FILTER__
    return {"rows": rows}
'''
CLEAN = TEMPLATE.replace("__FILTER__", 'k = req.query.get("kind")\n    rows = _tbl().all({"kind": k} if k else None)')
BUGGY = TEMPLATE.replace("__FILTER__", "rows = _tbl().all()")   # ignores ?kind=

SCENARIO = [
    {"id": "a", "call": "POST /cases", "body": {"title": "A", "kind": "ui"}, "expect": {"status": 200}},
    {"id": "b", "call": "POST /cases", "body": {"title": "B", "kind": "api"}, "expect": {"status": 200}},
    {"id": "filter", "call": "GET /cases", "query": {"kind": "ui"},
     "expect": {"status": 200, "all": {"path": "rows", "where": {"kind": "ui"}}, "max_len": {"path": "rows", "n": 1}}},
]


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    from cryptography.fernet import Fernet

    from src.core.config import settings
    from src.engine.node_registry import node_registry, register_all_nodes

    if not node_registry.list():
        register_all_nodes()
    monkeypatch.setattr(settings, "encryption_key", Fernet.generate_key().decode(), raising=False)


async def seed(pg, monkeypatch, source):
    # the generator's own _db helper looks the factory up in its module namespace
    monkeypatch.setattr("src.services.module_generator.async_session_factory", pg)
    async with pg() as s:
        p = await ProjectService(s).create(f"bg-{uuid.uuid4().hex[:8]}")
        await DataTableService(s).create_table(p, {"name": "cases", "columns": [
            {"name": "title", "type": "string", "required": True}, {"name": "kind", "type": "string"}]})
        m = await CodeModuleService(s).create(p, "cases_api", source)
        return p.id, m["module"]["id"]


async def scratch_tables(pg, pid):
    async with pg() as s:
        rows = (await s.execute(select(DataTableModel).where(DataTableModel.project_id == pid))).scalars().all()
        return sorted(t.name for t in rows)


@needs_sandbox
async def test_a_seeded_filter_bug_fails_the_self_test(pg, monkeypatch):
    pid, mid = await seed(pg, monkeypatch, BUGGY)
    out = await bg.check_module(pid, mid, SCENARIO)
    assert out["self_test"]["available"] and out["self_test"]["failed"] >= 1
    bad = [r for r in out["self_test"]["checks"] if r.get("ok") is False]
    assert any(r["step"] == "filter" and "leaked a row" in r["failure"] for r in bad), bad
    assert out["ok"] is False


@needs_sandbox
async def test_a_clean_module_has_no_failures_and_scratch_tables_are_cleaned_up(pg, monkeypatch):
    pid, mid = await seed(pg, monkeypatch, CLEAN)
    out = await bg.check_module(pid, "cases_api", SCENARIO)      # by name as well as by id
    assert out["static"] == [] and out["self_test"]["failed"] == 0, out["self_test"]
    assert out["self_test"]["passed"] >= len(SCENARIO) and out["ok"] is True
    assert await scratch_tables(pg, pid) == ["cases"]            # every scratch copy dropped
    bad = await bg.check_module(pid, mid, SCENARIO)
    assert await scratch_tables(pg, pid) == ["cases"]
    assert bad["self_test"]["failed"] == 0


async def test_static_findings_come_from_the_generators_checks(pg, monkeypatch):
    src = CLEAN + '\n@api.get("/x")\ndef x(req):\n    return {"who": req.headers.get("x-role"), "n": undefined_thing}\n'
    pid, _mid = await seed(pg, monkeypatch, src)
    out = await bg.check_module(pid, "cases_api", [])
    codes = {p["code"] for p in out["static"]}
    assert "undefined_name" in codes and out["ok"] is False


async def test_an_app_reading_a_field_the_route_never_returns_is_a_contract_finding(pg, monkeypatch):
    from src.services.app_builder_ai_service import AppBuilderAIService

    async def sigs(self):
        return {"listCases": OpSig(fields={"rows", "title", "kind"}, known=True)}
    monkeypatch.setattr(AppBuilderAIService, "_op_sigs", sigs)
    async with pg() as s:
        p = await ProjectService(s).create(f"bg-{uuid.uuid4().hex[:8]}")
        files = [{"path": "App.tsx", "content": (
            "export default function App() {\n  const run = async () => {\n    const res = await api.listCases();\n"
            "    return res.items.length + res.rows[0].title;\n  };\n  return <div onClick={run}>x</div>;\n}\n")}]
        out = await bg.check_app_files(s, p, files)
    assert any("`res.items`" in i and "api.listCases" in i for i in out["contract"]), out
    assert out["ok"] is False and set(out) >= {"tsc", "syntax", "contract", "call_shape", "invented_literals"}


async def test_a_check_is_logged_as_a_decision_row(pg, monkeypatch):
    from src.services import project_knowledge as pk

    pid, _mid = await seed(pg, monkeypatch, CLEAN)
    out = await bg.check_module(pid, "cases_api", [])
    async with pg() as s:
        await bg.record_check(s, pid, "module", "cases_api", out)
        row = await pk.get(s, pid, "decision", "check:module:cases_api")
    assert row and "check module cases_api" in row["body"]["decision"]
