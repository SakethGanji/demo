"""The plan flow end to end (DESIGN section 3.4), through the real HTTP routers (``api_router``) and a real throwaway Postgres.

The only fake is the model call (``llm_provider.call_llm``); the plan store, the plan conversation, the module generator, the module
save with its schema preflight, the data-table migration and the plan-conformance / preflight gates are all the real services.
``load_approved`` and the gates are NOT patched: the point is that the three packages connect.

Each step has its own test and rebuilds its prerequisites through the same helpers, so a failure names the step that broke.

    1. draft -> answer -> approve
    2. generate + save common, tests_library, runs
    3. /plan/check: runs complete, triage (not built) and the app missing
    4. revise (add a column to runs, additive) and approve
    5. regenerate runs -> schema_pending; the save migrates the column; /plan/check has no drift
    6. the preflight findings match the secrets set up
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from tests.unit.conftest import anyio_backend, pg, pg_dbname  # noqa: F401
from tests.unit.plan_fixture import qa_plan

pytestmark = pytest.mark.anyio
JSON = {"accept": "application/json"}

PROMPT = ("Build a QA workbench: a library of tests, runs started through the Jobs service against our bank app (dev and "
          "prod), and triage of failed runs with retries. Login uses the bank credentials.")

# ── module sources the fake model "writes" ──────────────────────────────────────────────────────────────────────────

COMMON = '''import studio


def audit(event, entity, entity_id, caller, detail=None):
    """Write one audit row."""
    return studio.db.table("audit_log").insert({"event": event, "entity": entity, "detail": detail})


def page(rows, limit, offset):
    """Shape a list response."""
    return {"rows": rows[offset:offset + limit], "total": len(rows), "limit": limit, "offset": offset}
'''
COMMON_TABLES = [{"name": "audit_log", "columns": [{"name": "event", "type": "string", "required": True}]}]

TESTS_LIBRARY = '''from studio import api
from common import audit, page
import studio


@api.get("/tests", name="list_tests")
def list_tests(req):
    """List tests. Returns {rows, total, limit, offset}"""
    return page(studio.db.table("tests").all(), 50, 0)


@api.post("/tests", name="create_test")
def create_test(req):
    """Create a test. Returns the row."""
    body = req.json or {}
    row = studio.db.table("tests").insert({"test_key": body.get("test_key"), "title": body.get("title")})
    audit("test.created", "tests", row.get("id"), "api")
    return row
'''
TESTS_TABLES = [{"name": "tests", "columns": [{"name": "title", "type": "string"}]}]


def runs_source(extra_column: bool = False) -> str:
    owner = ', "owner": "x"' if extra_column else ""
    return f'''from studio import api
from common import audit, page
import studio


@api.post("/runs", name="start_run")
def start_run(req):
    """Start a run. Returns {{id, job_id}}"""
    row = studio.db.table("runs").insert({{"test_id": 1, "status": "queued"{owner}}})
    audit("run.started", "runs", row.get("id"), "api")
    return row


@api.get("/runs", name="list_runs")
def list_runs(req):
    """List runs. Returns {{rows, total, limit, offset}}"""
    return page(studio.db.table("runs").all(), 50, 0)


@api.get("/runs/{{id}}", name="get_run")
def get_run(req):
    """One run."""
    return studio.db.table("runs").get(int(req.params["id"]))


@api.post("/runs/{{id}}/sync", name="sync_run")
def sync_run(req):
    """Sync a run from Jobs."""
    return studio.db.table("runs").update(int(req.params["id"]), {{"status": "running"}})


@api.post("/runs/{{id}}/cancel", name="cancel_run")
def cancel_run(req):
    """Cancel a run."""
    audit("run.cancelled", "runs", int(req.params["id"]), "api")
    return studio.db.table("runs").update(int(req.params["id"]), {{"status": "cancelled"}})
'''


# ── the fake model ───────────────────────────────────────────────────────────────────────────────────────────────────


def _plan_core_and_surface() -> tuple[dict, dict]:
    p = qa_plan()
    for i in p["integrations"]:      # the credentials are not known until the user answers
        i["secrets"] = []
    core = {k: p[k] for k in ("title", "summary", "data", "modules", "integrations", "environments", "build_order", "roles_note")}
    core["requirements"] = {"assumptions": [], "acceptance": p["requirements"]["acceptance"]}
    core["questions"] = [{"id": "q_creds", "topic": "credentials", "question": "Which credentials log in to the bank app?"},
                         {"id": "q_ret", "topic": "retention", "question": "How long are runs kept?",
                          "options": ["forever", "90 days"], "default": "forever"}]
    return core, {k: p[k] for k in ("routes", "apps", "nfr")}


class FakeModel:
    """Plan-conversation tools answer from canned plan parts; ``emit_module`` returns whatever ``next_module`` holds."""

    def __init__(self) -> None:
        self.core, self.surface = _plan_core_and_surface()
        self.calls: list[str] = []
        self.next_module: dict[str, Any] | None = None

    async def __call__(self, model, messages, temperature=0.2, tools=None, **kw):
        name = tools[0]["name"]
        self.calls.append(name)
        if name == "emit_plan_core":
            args = self.core
        elif name == "emit_plan_surface":
            args = self.surface
        elif name == "emit_plan_revision":
            args = {"integrations": qa_plan()["integrations"]}     # wires the named secrets into the integration
        elif name == "emit_module":
            assert self.next_module is not None, "the generator called the model without a module queued"
            args = self.next_module
        else:
            raise AssertionError(f"unexpected tool {name}")
        tc = SimpleNamespace(name=name, args=args, id=f"t{len(self.calls)}")
        return SimpleNamespace(tool_calls=[tc], text=None, output_truncated=False,
                               get_assistant_message=lambda: {"role": "assistant", "content": None})


# ── the world: a project behind the real routers ─────────────────────────────────────────────────────────────────────


class World:
    def __init__(self, client, pid: str, model: FakeModel) -> None:
        self.c, self.pid, self.model = client, pid, model
        self.module_ids: dict[str, str] = {}

    @property
    def p(self) -> str:
        return f"/api/projects/{self.pid}"

    # step 1
    async def draft_answer_approve(self) -> dict:
        r = await self.c.post(f"{self.p}/plan/draft", json={"prompt": PROMPT}, headers=JSON)
        assert r.status_code == 200, r.text
        plan = r.json()["plan"]
        assert plan["version"] == 1 and plan["phase"] == "questions" and plan["questions"], plan
        rounds = 0
        while plan["phase"] == "questions":
            rounds += 1
            assert rounds <= 3, "the conversation must end after at most two question rounds"
            answers = {q["id"]: (q.get("default") or "yes") for q in plan["questions"]}
            for q in plan["questions"]:
                if q["topic"] == "credentials":
                    answers[q["id"]] = "Project secrets BANK_USER, BANK_PASS, BANK_TOTP"
            r = await self.c.post(f"{self.p}/plan/draft", json={"version": 1, "answers": answers}, headers=JSON)
            assert r.status_code == 200, r.text
            plan = r.json()["plan"]
        assert plan["phase"] == "review"
        r = await self.c.post(f"{self.p}/plan/1/approve", json={})
        assert r.status_code == 200, r.text
        return r.json()

    # step 2
    async def generate(self, key: str, source: str, tables: list[dict], *, module_name: str | None = None,
                       module_id: str | None = None) -> dict:
        self.model.next_module = {"module_name": module_name or key, "source": source, "summary": f"{key} built",
                                  "remaining": [], "tables": tables}
        r = await self.c.post(f"{self.p}/modules/generate", headers=JSON,
                              json={"prompt": f"Build the {key} module [plan:{key}]", "self_test": False, "clarify": True,
                                    **({"module_id": module_id} if module_id else {})})
        assert r.status_code == 200, r.text
        return r.json()

    async def build(self, key: str, source: str, tables: list[dict]) -> dict:
        res = await self.generate(key, source, tables)
        assert res["status"] == "built" and res["plan_module"] == key, res
        r = await self.c.post(f"{self.p}/modules", json={"name": key, "source": res["source"], "tables": res["tables"]})
        assert r.status_code == 201, r.text
        self.module_ids[key] = r.json()["module"]["id"]
        return {"generate": res, "save": r.json()}

    async def build_three(self) -> dict[str, dict]:
        out = {"common": await self.build("common", COMMON, COMMON_TABLES)}
        out["tests_library"] = await self.build("tests_library", TESTS_LIBRARY, TESTS_TABLES)
        out["runs"] = await self.build("runs", runs_source(), [{"name": "runs", "columns": [{"name": "test_id", "type": "integer"}]}])
        return out

    async def check(self) -> dict:
        r = await self.c.post(f"{self.p}/plan/check")
        assert r.status_code == 200, r.text
        return r.json()

    # step 4
    async def revise_add_runs_column(self) -> dict:
        # v1 carries what the answers settled (secrets, roles, limits, retention); the revision is v1 plus the one column
        rev = (await self.c.get(f"{self.p}/plan/1")).json()["body"]
        next(t for t in rev["data"]["tables"] if t["name"] == "runs")["columns"].append({"name": "owner", "type": "string"})
        r = await self.c.post(f"{self.p}/plan/revise", json={"body": rev})
        assert r.status_code == 200, r.text
        v2 = r.json()
        assert v2["version"] == 2 and v2["diff"]["breaking"] is False, v2
        r = await self.c.post(f"{self.p}/plan/2/approve", json={})
        assert r.status_code == 200, r.text
        return r.json()


@pytest.fixture
async def world(pg, monkeypatch):
    from cryptography.fernet import Fernet
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from src.core.config import settings
    from src.core.dependencies import get_db_session
    from src.engine import llm_provider
    from src.routes import api_router

    model = FakeModel()
    monkeypatch.setattr(llm_provider, "call_llm", model)
    monkeypatch.setattr(llm_provider, "is_configured", lambda m: True)
    monkeypatch.setattr("src.services.module_generator.async_session_factory", pg)
    monkeypatch.setattr(settings, "encryption_key", Fernet.generate_key().decode(), raising=False)

    app = FastAPI()
    app.include_router(api_router)

    async def sess():
        async with pg() as s:
            yield s

    app.dependency_overrides[get_db_session] = sess
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/api/projects", json={"name": f"e2e-{uuid.uuid4().hex[:8]}"})
        assert r.status_code == 201, r.text
        yield World(c, r.json()["id"], model)


def route_state(rep: dict) -> dict[str, str]:
    return {m["route"]: m["reason"] for m in rep["missing"]["routes"]}


# ── 1 ────────────────────────────────────────────────────────────────────────────────────────────────────────────────


async def test_step1_draft_answer_approve(world):
    out = await world.draft_answer_approve()
    assert out["status"] == "approved" and out["version"] == 1 and out["approved_at"]
    assert world.model.calls[:2] == ["emit_plan_core", "emit_plan_surface"] and "emit_plan_revision" in world.model.calls
    got = (await world.c.get(f"{world.p}/plan")).json()
    assert got["approved"]["version"] == 1 and got["draft"] is None
    body = got["approved"]["body"]
    assert {s["name"] for s in body["secrets"]} == {"BANK_USER", "BANK_PASS", "BANK_TOTP"}      # taken from the answers
    assert {m["table"] for m in out["migrations"]} == {"tests", "runs", "audit_log"}
    assert not [i for i in got["approved"]["issues"] if i["severity"] == "error"]


# ── 2 ────────────────────────────────────────────────────────────────────────────────────────────────────────────────


async def test_step2_generate_and_save_each_module_through_the_module_api(world):
    await world.draft_answer_approve()
    built = await world.build_three()
    order = [m["name"] for m in (await world.c.get(f"{world.p}/modules")).json()]
    assert sorted(order) == ["common", "runs", "tests_library"]

    # the plan was followed with no question round, and the result says which plan and what is left to build
    runs = built["runs"]["generate"]
    assert runs["plan_version"] == 1 and runs["status"] == "built"
    assert runs["next_modules"] == ["triage"]
    assert {t["name"] for t in runs["tables"]} >= {"runs"}
    # the tables were created from the plan's definition, not the model's one-column declaration
    tbls = {t["name"]: t for t in (await world.c.get(f"{world.p}/tables")).json()}
    cols = {c["name"] for c in tbls["runs"]["columns"]}
    assert {"test_id", "status", "job_id"} <= cols
    assert {c["name"] for c in tbls["tests"]["columns"]} >= {"test_key", "title", "steps"}
    assert built["runs"]["save"]["tables_created"] == ["runs"]


# ── 3 ────────────────────────────────────────────────────────────────────────────────────────────────────────────────


async def test_step3_plan_check_shows_runs_complete_and_the_rest_missing(world):
    await world.draft_answer_approve()
    rep = await world.check()
    assert rep["has_plan"] and rep["ok"] is False and rep["plan_version"] == 1
    assert set(rep["missing"]["modules"]) == {"common", "tests_library", "runs", "triage"}     # nothing is built yet

    await world.build_three()
    rep = await world.check()
    assert rep["ok"] is False
    assert rep["missing"]["modules"] == ["triage"]
    routes = route_state(rep)
    assert list(routes) == ["POST /triage/{id}/retry"], routes                                  # every runs / tests route exists
    assert rep["missing"]["tables"] == []
    assert not any(m.get("module") == "runs" for m in rep["missing"].get("columns", [])), rep["missing"]
    assert rep["duplicates"] == [] if isinstance(rep["duplicates"], list) else not any(rep["duplicates"].values())
    assert rep["stale"] == []
    assert rep["conformance"]["routes"] == "7/8"
    assert rep["missing"]["apps"] or any(a for a in rep.get("apps", []) if not a.get("exists"))


# ── 4 ────────────────────────────────────────────────────────────────────────────────────────────────────────────────


async def test_step4_revise_adds_a_column_additively_and_approves(world):
    await world.draft_answer_approve()
    await world.build_three()
    out = await world.revise_add_runs_column()
    assert out["version"] == 2 and out["status"] == "approved"
    assert out["migrations"] == [{"version": 2, "table": "runs", "refused": [],
                                  "ops": [{"op": "add_column", "column": {"name": "owner", "type": "string", "required": False}}]}]
    got = (await world.c.get(f"{world.p}/plan")).json()
    assert got["approved"]["version"] == 2 and [v["status"] for v in got["versions"]] == ["approved", "superseded"]
    # the saved table is behind the new plan until a module is rebuilt: the gate says so
    rep = await world.check()
    assert rep["plan_version"] == 2 and rep["ok"] is False
    assert any(d["table"] == "runs" for d in rep["drift"]), rep["drift"]
    assert "runs" in {x["module"] for x in rep["stale"]}


# ── 5 ────────────────────────────────────────────────────────────────────────────────────────────────────────────────


async def test_step5_regenerating_runs_reports_schema_pending_and_the_save_migrates(world):
    await world.draft_answer_approve()
    await world.build_three()
    await world.revise_add_runs_column()

    res = await world.generate("runs", runs_source(extra_column=True),
                               [{"name": "runs", "columns": [{"name": "test_id", "type": "integer"}]}], module_id=world.module_ids["runs"])
    assert res["plan_version"] == 2 and res["status"] == "built"
    pending = [w for w in res["analysis"]["warnings"] if w["code"] == "schema_pending"]
    assert len(pending) == 1 and "owner" in pending[0]["message"], res["analysis"]["warnings"]
    assert {c["name"] for t in res["tables"] if t["name"] == "runs" for c in t["columns"]} >= {"owner"}

    r = await world.c.put(f"{world.p}/modules/{world.module_ids['runs']}", json={"source": res["source"], "tables": res["tables"]})
    assert r.status_code == 200, r.text
    migrated = r.json()["tables_migrated"]
    assert [m["table"] for m in migrated] == ["runs"] and r.json()["schema_refused"] == []
    cols = {c["name"] for t in (await world.c.get(f"{world.p}/tables")).json() if t["name"] == "runs" for c in t["columns"]}
    assert "owner" in cols

    rep = await world.check()
    assert rep["drift"] == [], rep["drift"]
    assert "runs" not in {x["module"] for x in rep["stale"]}, rep["stale"]          # rebuilt against v2
    assert not any(m.get("table") == "runs" for m in rep["missing"].get("columns", []))
    assert rep["missing"]["modules"] == ["triage"]            # still the only module not built

    # saving the same declaration again is a no-op (the migration is idempotent)
    r = await world.c.put(f"{world.p}/modules/{world.module_ids['runs']}", json={"source": res["source"], "tables": res["tables"]})
    assert r.status_code == 200, r.text
    assert r.json()["tables_migrated"] == []


# ── 6 ────────────────────────────────────────────────────────────────────────────────────────────────────────────────


async def test_step6_preflight_findings_match_the_secrets_set_up(world):
    await world.draft_answer_approve()
    await world.build_three()

    def pre(rep, code):
        return [f for f in rep["preflight"] if f["code"] == code]

    # nothing is set up: every integration secret is missing in every planned environment, each with a fix naming the host
    rep = await world.check()
    missing = pre(rep, "secret_missing")
    assert {(f["where"]) for f in missing} == {f"{n} ({e})" for n in ("BANK_USER", "BANK_PASS", "BANK_TOTP") for e in ("dev", "prod")}
    dev = next(f for f in missing if f["where"] == "BANK_USER (dev)")
    assert "allowed_hosts `localhost`" in dev["fix"]
    assert rep["ok"] is False

    async def put(key: str, env: str, hosts: list[str]):
        r = await world.c.post(f"{world.p}/secrets", json={"key": key, "value": "v-" + key, "environment": env, "allowed_hosts": hosts})
        assert r.status_code == 201, r.text

    # secrets exist, but BANK_USER carries a port: the hostname rule is named, with the exact fix
    for env, host in (("dev", "localhost"), ("prod", "bank.example.com")):
        for key in ("BANK_PASS", "BANK_TOTP"):
            await put(key, env, [host])
    await put("BANK_USER", "dev", ["localhost:4590"])
    await put("BANK_USER", "prod", ["bank.example.com"])
    rep = await world.check()
    assert pre(rep, "secret_missing") == []
    ported = pre(rep, "host_has_port")
    assert [f["where"] for f in ported] == ["BANK_USER (dev)"]
    assert "set allowed_hosts on BANK_USER to `localhost`" in ported[0]["fix"]

    # correct it through the API: the findings go away
    secrets = (await world.c.get(f"{world.p}/secrets")).json()
    sid = next(s["id"] for s in secrets if s["key"] == "BANK_USER" and s["environment"] == "dev")
    r = await world.c.put(f"{world.p}/secrets/{sid}", json={"allowed_hosts": ["localhost"]})
    assert r.status_code == 200, r.text
    rep = await world.check()
    errs = [f for f in rep["preflight"] if f["severity"] == "error"]
    assert errs == [], errs


async def test_step5b_a_revision_only_marks_the_modules_it_affects_as_stale(world):
    """The revision touched one column of ``runs``; the revise response lists exactly that as affected, so ``common`` and
    ``tests_library`` (built against v1, unchanged in v2) are not stale, and once ``runs`` is rebuilt the plan is conformant."""
    await world.draft_answer_approve()
    await world.build_three()
    await world.revise_add_runs_column()
    rep = await world.check()
    assert {x["module"] for x in rep["stale"]} == {"runs"}, rep["stale"]
    res = await world.generate("runs", runs_source(extra_column=True),
                               [{"name": "runs", "columns": [{"name": "test_id", "type": "integer"}]}], module_id=world.module_ids["runs"])
    r = await world.c.put(f"{world.p}/modules/{world.module_ids['runs']}", json={"source": res["source"], "tables": res["tables"]})
    assert r.status_code == 200, r.text
    rep = await world.check()
    assert rep["stale"] == [], rep["stale"]
    assert rep["drift"] == [] and rep["missing"]["modules"] == ["triage"]
