"""The plan API on a throwaway Postgres: draft -> edit -> approve leaves exactly one approved version, a breaking revision
needs ``accept_breaking``, approval mirrors the plan into project knowledge, and a stale edit is refused. No model is
called: drafts are saved from a body (the model path is covered by test_plan_clarify)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from src.db.plan_models import ProjectPlanModel
from src.services import project_knowledge as pk
from src.services.project_service import ProjectService
from tests.unit.conftest import anyio_backend, pg, pg_dbname  # noqa: F401
from tests.unit.plan_fixture import qa_plan

pytestmark = pytest.mark.anyio
JSON = {"accept": "application/json"}


async def client(pg):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from src.core.dependencies import get_db_session
    from src.routes import project_plan

    app = FastAPI()
    app.include_router(project_plan.router, prefix="/api")

    async def sess():
        async with pg() as s:
            yield s

    app.dependency_overrides[get_db_session] = sess
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


async def project(pg) -> str:
    async with pg() as s:
        return (await ProjectService(s).create(f"plan-{uuid.uuid4().hex[:8]}")).id


async def draft(c, pid, body) -> dict:
    r = await c.post(f"/api/projects/{pid}/plan/draft", json={"body": body}, headers=JSON)
    assert r.status_code == 200, r.text
    return r.json()["plan"]


async def statuses(pg, pid) -> dict[int, str]:
    async with pg() as s:
        rows = (await s.execute(select(ProjectPlanModel).where(ProjectPlanModel.project_id == pid))).scalars()
        return {r.version: r.status for r in rows}


async def test_draft_edit_approve_then_revise_keeps_one_approved_version(pg):
    pid = await project(pg)
    c = await client(pg)
    assert (await c.get(f"/api/projects/{pid}/plan")).json() == {"approved": None, "draft": None, "versions": []}
    v1 = await draft(c, pid, qa_plan())
    assert v1["version"] == 1 and v1["phase"] == "review"

    body = v1["body"]
    body["title"] = "QA Workbench (edited)"
    r = await c.put(f"/api/projects/{pid}/plan/1", json={"body": body, "base_updated_at": v1["updated_at"]})
    assert r.status_code == 200, r.text
    assert r.json()["body"]["title"] == "QA Workbench (edited)"

    r = await c.post(f"/api/projects/{pid}/plan/1/approve", json={})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["status"] == "approved" and out["approved_at"]
    assert {m["table"] for m in out["migrations"]} == {"tests", "runs", "audit_log"}
    assert all(m["ops"][0]["op"] == "create_table" for m in out["migrations"])

    r = await c.put(f"/api/projects/{pid}/plan/1", json={"body": body})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "plan_not_draft"

    # an additive revision: a new column and a new route
    rev = qa_plan()
    rev["data"]["tables"][0]["columns"].append({"name": "owner", "type": "string"})
    r = await c.post(f"/api/projects/{pid}/plan/revise", json={"body": rev})
    assert r.status_code == 200, r.text
    v2 = r.json()
    assert v2["version"] == 2 and v2["base_version"] == 1 and v2["status"] == "draft"
    assert v2["diff"]["breaking"] is False and v2["affected"]["tables"] == ["tests"]
    assert (await c.get(f"/api/projects/{pid}/plan/2/diff")).json()["against"] == 1

    r = await c.post(f"/api/projects/{pid}/plan/2/approve", json={})
    assert r.status_code == 200, r.text
    assert r.json()["migrations"] == [{"version": 2, "table": "tests", "refused": [],
                                       "ops": [{"op": "add_column", "column": {"name": "owner", "type": "string",
                                                                               "required": False}}]}]
    assert await statuses(pg, pid) == {1: "superseded", 2: "approved"}
    got = (await c.get(f"/api/projects/{pid}/plan")).json()
    assert got["approved"]["version"] == 2 and got["draft"] is None and len(got["versions"]) == 2
    assert [v["version"] for v in (await c.get(f"/api/projects/{pid}/plan/versions")).json()] == [2, 1]
    assert len(got["approved"]["body"]["migrations"]) == 4


async def test_breaking_change_needs_accept_breaking(pg):
    pid = await project(pg)
    c = await client(pg)
    await draft(c, pid, qa_plan())
    assert (await c.post(f"/api/projects/{pid}/plan/1/approve", json={})).status_code == 200

    rev = qa_plan()
    rev["routes"] = [r for r in rev["routes"] if r["key"] != "POST /triage/{id}/retry"]
    r = await c.post(f"/api/projects/{pid}/plan/revise", json={"body": rev})
    assert r.json()["diff"]["breaking"] is True
    r = await c.post(f"/api/projects/{pid}/plan/2/approve", json={})
    assert r.status_code == 409, r.text
    d = r.json()["detail"]
    assert d["code"] == "breaking_change" and "triage" in d["diff"]["affected"]["modules"]
    assert await statuses(pg, pid) == {1: "approved", 2: "draft"}
    r = await c.post(f"/api/projects/{pid}/plan/2/approve", json={"accept_breaking": True})
    assert r.status_code == 200, r.text
    assert await statuses(pg, pid) == {1: "superseded", 2: "approved"}


async def test_approval_is_refused_while_the_plan_has_errors_or_open_questions(pg):
    pid = await project(pg)
    c = await client(pg)
    bad = qa_plan()
    bad["routes"][0]["module"] = "nowhere"
    v1 = await draft(c, pid, bad)
    assert any(i["code"] == "PLAN003" for i in v1["issues"])
    r = await c.post(f"/api/projects/{pid}/plan/1/approve", json={})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "plan_invalid"
    async with pg() as s:
        row = (await s.execute(select(ProjectPlanModel).where(ProjectPlanModel.project_id == pid))).scalar_one()
        row.questions = [{"id": "q1", "topic": "roles", "question": "?"}]
        await s.commit()
    r = await c.post(f"/api/projects/{pid}/plan/1/approve", json={})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "plan_questions_open"


async def test_approval_mirrors_the_plan_into_knowledge(pg):
    pid = await project(pg)
    c = await client(pg)
    await draft(c, pid, qa_plan())
    r = await c.post(f"/api/projects/{pid}/plan/1/approve", json={})
    assert r.json()["knowledge"] == {"spec": 1, "clarify_answer": 2, "table_schema": 3, "requirement": 3}
    async with pg() as s:
        answers = {x["key"]: x for x in await pk.list_kind(s, pid, "clarify_answer")}
        assert set(answers) == {"plan:dom_credentials", "plan:dom_retention"}
        a = answers["plan:dom_credentials"]
        assert a["status"] == "approved" and a["source"] == "human"
        tables = {x["key"]: x for x in await pk.list_kind(s, pid, "table_schema")}
        assert tables["runs"]["source_ref"] == "plan:v1" and tables["runs"]["source"] == "design"
        assert {x["key"] for x in await pk.list_kind(s, pid, "requirement")} == {"plan:ac:ac1", "plan:ac:ac2", "plan:ac:ac3"}
        assert (await pk.get(s, pid, "spec", "plan"))["body"]["build_order"][0] == "common"

    rev = qa_plan()       # a later version drops an answer: its knowledge row goes too
    rev["requirements"]["answers"] = rev["requirements"]["answers"][:1]
    await c.post(f"/api/projects/{pid}/plan/revise", json={"body": rev})
    assert (await c.post(f"/api/projects/{pid}/plan/2/approve", json={})).status_code == 200
    async with pg() as s:
        assert {x["key"] for x in await pk.list_kind(s, pid, "clarify_answer")} == {"plan:dom_credentials"}


async def test_a_stale_edit_is_refused(pg):
    pid = await project(pg)
    c = await client(pg)
    v1 = await draft(c, pid, qa_plan())
    first = await c.put(f"/api/projects/{pid}/plan/1", json={"body": {**v1["body"], "title": "A"},
                                                             "base_updated_at": v1["updated_at"]})
    assert first.status_code == 200
    r = await c.put(f"/api/projects/{pid}/plan/1", json={"body": {**v1["body"], "title": "B"},
                                                         "base_updated_at": v1["updated_at"]})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "plan_conflict"
    assert (await c.get(f"/api/projects/{pid}/plan/1")).json()["body"]["title"] == "A"


async def test_errors_and_modes(pg, monkeypatch):
    pid = await project(pg)
    c = await client(pg)
    assert (await c.get(f"/api/projects/{pid}/plan/3")).status_code == 404
    assert (await c.get("/api/projects/nope/plan")).status_code == 404
    r = await c.post(f"/api/projects/{pid}/plan/revise", json={})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "plan_not_approved"
    r = await c.post(f"/api/projects/{pid}/plan/draft", json={"prompt": "x", "body": {}}, headers=JSON)
    assert r.status_code == 422
    from src.engine import llm_provider

    monkeypatch.setattr(llm_provider, "is_configured", lambda model: False)
    r = await c.post(f"/api/projects/{pid}/plan/draft", json={"prompt": "a QA workbench"}, headers=JSON)
    assert r.status_code == 503 and r.json()["detail"]["code"] == "llm_unavailable"
    from src.services import plan_clarify as pc

    key = pc.try_acquire(pid)
    try:
        r = await c.post(f"/api/projects/{pid}/plan/draft", json={"body": qa_plan()}, headers=JSON)
        assert r.status_code == 409 and r.json()["detail"]["code"] == "plan_in_progress"
    finally:
        pc.release(key)
