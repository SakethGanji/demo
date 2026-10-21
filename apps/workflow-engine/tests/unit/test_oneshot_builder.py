"""The one-shot app builder: the runtime brief, load + check of a real bundle in a real test database, the single fix pass, the
split of a big plan into parallel calls, edits that migrate, and the coordinator's default build path. The model is a fake that
answers with the recorded bundle (tests/unit/oneshot_fixtures/office_bundle.json: the office-supplies app one call wrote)."""

from __future__ import annotations

import asyncio
import copy
import json
import time
import uuid
from pathlib import Path
from typing import Any

import pytest

from src.engine.llm_provider import LLMResponse, ToolCall
from src.services import build_coordinator as bc
from src.services import oneshot_builder as ob
from src.services import platform_index as pi
from src.services.project_service import ProjectService
from tests.unit.conftest import anyio_backend, pg, pg_dbname  # noqa: F401

pytestmark = pytest.mark.anyio
FIX = Path(__file__).parent / "oneshot_fixtures"


def bundle_json() -> dict[str, Any]:
    return json.loads((FIX / "office_bundle.json").read_text())


def typed_bundle_json() -> dict[str, Any]:
    """The recorded bundle with the one type error the type-check finds in it (a bare ``ResourceDef``) written the way the brief now
    tells the writer to: the bundle the platform should load with nothing to report."""
    b = bundle_json()
    for f in b["app"]["files"]:
        if f["path"] == "resources.ts":
            f["content"] = (f["content"].replace("import type { ResourceDef }", "import type { BaseRow, ResourceDef }")
                            .replace("ResourceDef['fields']", "ResourceDef<BaseRow>['fields']").replace(": ResourceDef =", ": ResourceDef<BaseRow> ="))
    return b


def plan_json() -> dict[str, Any]:
    p = json.loads((FIX / "office_plan.json").read_text())
    for a in p.get("apps") or []:
        a["app_id"] = None
    return p


class FakeModel:
    """``emit_bundle`` -> the recorded bundle (or ``bundles`` in order); ``emit_changes`` -> ``changes`` in order."""

    def __init__(self, bundles: list[dict[str, Any]] | None = None, changes: list[dict[str, Any]] | None = None, delay: float = 0.0) -> None:
        self.bundles = list(bundles if bundles is not None else [bundle_json()])
        self.changes = list(changes or [])
        self.delay = delay
        self.calls: list[str] = []
        self.spans: list[tuple[float, float]] = []

    async def __call__(self, model, messages, temperature=0.2, tools=None, **kw):
        name = tools[0]["name"]
        self.calls.append(name)
        t0 = time.monotonic()
        if self.delay:
            await asyncio.sleep(self.delay)
        self.spans.append((t0, time.monotonic()))
        src = self.bundles if name == "emit_bundle" else self.changes
        args = src.pop(0) if len(src) > 1 or (src and name == "emit_changes") else (copy.deepcopy(src[0]) if src else {})
        return LLMResponse(tool_calls=[ToolCall(id=f"c{len(self.calls)}", name=name, args=args)])


@pytest.fixture(autouse=True)
def _generator_db(request, monkeypatch):
    """The module generator (the module check) holds its own import of the session factory: point it at the test database."""
    if "pg" in request.fixturenames:
        pg = request.getfixturevalue("pg")
        monkeypatch.setattr("src.services.module_generator.async_session_factory", pg)


@pytest.fixture
def fake(monkeypatch):
    m = FakeModel()
    monkeypatch.setattr("src.engine.llm_provider.call_llm", m)
    return m


async def new_project(pg) -> str:
    async with pg() as s:
        return (await ProjectService(s).create(f"oneshot-{uuid.uuid4().hex[:8]}")).id


# ── the brief ────────────────────────────────────────────────────────────────


def test_the_brief_is_generated_from_the_platform():
    b = ob.brief()
    for c in pi.kit_components():
        assert c["name"] in b, f"kit {c['kind']} {c['name']} missing from the brief"
    for e in pi.sdk_symbols():
        if e["kind"] == "rule":
            continue
        last = e["name"].split(".")[-1]
        assert last in b, f"SDK symbol {e['name']} missing from the brief"
    for hook in ob._resource_hooks():
        assert f"@res.{hook}" in b
    assert len(ob._resource_hooks()) == 9
    for must in ("rows", "total", "limit", "offset", "req.caller", "emit_bundle", "h-full flex flex-col", "api.<name>(params?, body?, query?)"):
        assert must in b
    from src.services.project_plan import COLUMN_TYPES
    assert all(t in b for t in COLUMN_TYPES)


def test_the_api_call_scan_tolerates_formatting_and_finds_aliased_uses():
    def scan(code):
        return ob.scan_api_calls([{"path": "App.tsx", "content": code}])

    assert scan("await api\n  .getX({id: 1});\nawait api . postY(a);") == ({"getX", "postY"}, False)
    assert scan("const r = { list: api.listRequests, get: (id) => api\n.getRequest({id}) };")[0] == {"listRequests", "getRequest"}
    ops, dyn = scan("const { a, b: renamed, c = 1 } = api;")
    assert ops == {"a", "b", "c"} and dyn is False
    assert scan("import { api as client } from './api/client';\nclient.\n  ping();")[0] == {"ping"}
    assert scan("import { api, ApiError } from './api/client';\n// api.nope()\n/* api.nope2() */\napi.real()") == ({"real"}, False)
    assert scan("const a = api;\na.x();")[1] is True
    assert scan("call(api);")[1] is True and scan("const o = { ...api };")[1] is True and scan("api[name]();")[1] is True
    assert scan("const { rows } = await api.listX();")[0] == {"listX"}          # destructuring a RESULT is not destructuring api
    from src.services import app_builder_ai_service as ab
    assert ab._API_CALL_RE.findall("await api\n   .getX()\napi . putY (1)") == ["getX", "putY"]


# ── load + check on the real bundle ──────────────────────────────────────────


async def test_load_and_check_the_office_supplies_bundle_finds_nothing(pg):
    pid = await new_project(pg)
    bundle = ob.normalize_bundle(typed_bundle_json())
    assert len(bundle["modules"]) == 1 and bundle["modules"][0]["name"] == "requests"
    loaded = await ob.load(pid, bundle)
    assert loaded["errors"] == [], loaded["errors"]
    assert sorted(loaded["tables"]) == sorted(t["name"] for t in bundle["tables"])
    assert loaded["app_id"] and loaded["version_id"]
    chk = await ob.check(pid, bundle, loaded)
    assert chk["errors"] == [], chk["errors"]
    assert chk["routes"] and all(r["status"] == "passing" for r in chk["routes"])
    # route access: every route the app calls is attached
    from src.db.models import AppModel
    async with pg() as s:
        app = await s.get(AppModel, loaded["app_id"])
        ids = app.draft_definition["studio"]["route_ids"]
        assert app.draft_definition["studio"]["oneshot"] == {"modules": ["requests"]}
    called, dyn = ob.scan_api_calls(bundle["app"]["files"])
    assert len(ids) >= len(called) and not dyn
    # loading again is idempotent: no second seeding, the unchanged module is not saved again
    from src.services.data_table_service import DataTableService
    async with pg() as s:
        from src.db.models import ProjectModel
        svc = DataTableService(s)
        n = await svc.row_count((await svc.table_by_name(pid, "approvers")).id)
        again = await ob.load(pid, bundle, app_id=loaded["app_id"])
        assert again["errors"] == [] and await svc.row_count((await svc.table_by_name(pid, "approvers")).id) == n


# ── the fix pass ─────────────────────────────────────────────────────────────


async def test_an_injected_error_gets_exactly_one_fix_pass(pg, monkeypatch):
    """The recorded bundle has a type error the type-check finds (a bare ``ResourceDef``): one fix call repairs it, and the
    build is then clean. A fix that repairs nothing is reported, never retried."""
    from src.services import project_plan as pp

    fixed_files = [f for f in typed_bundle_json()["app"]["files"] if f["path"] == "resources.ts"]
    m = FakeModel(bundles=[bundle_json()], changes=[{"app": {"files": fixed_files}, "message": "typed"}])
    monkeypatch.setattr("src.engine.llm_provider.call_llm", m)
    pid = await new_project(pg)
    res = await ob.build(pid, pp.normalize(plan_json()), app_key="supply_requests")
    assert m.calls == ["emit_bundle", "emit_changes"], m.calls
    assert res["fixed"] is True and res["ok"] is True and res["errors"] == [], res["errors"]
    assert res["timing"]["model_calls"] == 2 and res["timing"]["phases"]["fix"]["model_calls"] == 1
    assert {"write", "first_load", "first_check", "fix_load", "fix_check"} <= set(res["timing"]["phases"])

    m2 = FakeModel(bundles=[bundle_json()], changes=[{"message": "no idea", "app": {"files": [{"path": "notes.md", "content": "x"}]}}])
    monkeypatch.setattr("src.engine.llm_provider.call_llm", m2)
    pid2 = await new_project(pg)
    res2 = await ob.build(pid2, pp.normalize(plan_json()), app_key="supply_requests")
    assert m2.calls == ["emit_bundle", "emit_changes"], "the fix pass runs once, whatever it returns"
    assert res2["ok"] is False and any(e["code"] == "tsc" for e in res2["errors"])


# ── a big plan is written feature by feature, in parallel ───────────────────


def big_plan() -> dict[str, Any]:
    mods = [{"key": k, "kind": "routes", "owns": [f"{k}_t{i}" for i in range(2)]} for k in ("alpha", "beta", "gamma")]
    mods.append({"key": "common", "kind": "shared", "owns": []})
    routes = [{"key": f"GET /{k}/{i}", "module": k} for k in ("alpha", "beta", "gamma") for i in range(6)]
    tables = [{"name": f"{k}_t{i}", "owner": k, "columns": []} for k in ("alpha", "beta", "gamma") for i in range(2)]
    screens = [{"name": f"{k.title()} list", "routes": [f"GET /{k}/0", f"GET /{k}/1"], "purpose": f"{k} records"} for k in ("alpha", "beta", "gamma")]
    return {"title": "Big", "modules": mods, "routes": routes, "data": {"tables": tables}, "apps": [{"key": "big", "name": "Big App", "screens": screens}]}


async def test_a_big_plan_is_split_by_feature_and_the_calls_overlap(monkeypatch):
    plan = big_plan()
    assert ob.is_big(plan) and not ob.is_big({**plan, "routes": plan["routes"][:5], "data": {"tables": plan["data"]["tables"][:2]}})
    feats = ob.features(plan, "big")
    assert [f["key"] for f in feats] == ["alpha", "beta", "gamma"]
    assert [s["path"] for s in feats[0]["screens"]] == ["views/AlphaList.tsx"] and feats[1]["tables"] == ["beta_t0", "beta_t1"]

    class SplitModel(FakeModel):
        async def __call__(self, model, messages, temperature=0.2, tools=None, **kw):
            user = messages[-1]["content"]
            key = next(k for k in ("alpha", "beta", "gamma") if f"the `{k}` feature area" in user)
            self.calls.append(key)
            t0 = time.monotonic()
            await asyncio.sleep(0.3)
            self.spans.append((t0, time.monotonic()))
            view = f"views/{key.title()}List.tsx"
            return LLMResponse(tool_calls=[ToolCall(id=key, name="emit_bundle", args={
                "tables": [{"name": f"{key}_t0", "columns": [{"name": "x", "type": "string"}]}], "seed": {},
                "module": {"name": "wrongname", "source": "import studio\n"},
                "app": {"files": [{"path": view, "content": "export default function V() { return null }"},
                                  {"path": "App.tsx", "content": "// a feature must not write the entry"}]}})])

    m = SplitModel()
    monkeypatch.setattr("src.engine.llm_provider.call_llm", m)
    timer = ob.Timer()
    t0 = time.monotonic()
    bundle = await ob.write_app(plan, app_key="big", timer=timer)
    wall = time.monotonic() - t0
    assert sorted(m.calls) == ["alpha", "beta", "gamma"] and timer.calls == 3
    assert wall < 0.75, f"three 0.3 s calls took {wall:.2f} s: they ran one after the other"
    assert max(a for a, _ in m.spans) < min(b for _, b in m.spans), "every call started before any call finished"
    assert sorted(x["name"] for x in bundle["modules"]) == ["alpha", "beta", "gamma"]       # named by the plan, not by the model
    app = bundle["app"]["files"]
    assert app[0]["path"] == "App.tsx" and "AppShell" in app[0]["content"] and "// a feature must not write" not in app[0]["content"]
    for k in ("Alpha", "Beta", "Gamma"):
        assert f"import {k}List from './views/{k}List';" in app[0]["content"]
    assert sorted(t["name"] for t in bundle["tables"]) == ["alpha_t0", "beta_t0", "gamma_t0"]


# ── edits ────────────────────────────────────────────────────────────────────


async def test_an_edit_adds_a_column_and_the_platform_migrates(pg, monkeypatch):
    pid = await new_project(pg)
    typed = ob.normalize_bundle(typed_bundle_json())
    loaded = await ob.load(pid, typed)
    assert loaded["errors"] == []
    app_id = loaded["app_id"]
    cur = await ob.current_bundle(pid, app_id)
    assert [m["name"] for m in cur["modules"]] == ["requests"] and "supply_requests" in {t["name"] for t in cur["tables"]}
    assert any(f["path"] == "App.tsx" for f in cur["app"]["files"]) and not any(f["path"].startswith("kit/") for f in cur["app"]["files"])
    table = next(t for t in cur["tables"] if t["name"] == "supply_requests")
    edited = {**table, "columns": table["columns"] + [{"name": "priority", "type": "string", "enum": ["low", "normal", "high"], "default": "normal"}]}
    notes = {"path": "views/Notes.tsx", "content": "/** Notes view. */\nexport default function Notes() { return null }\n"}
    m = FakeModel(changes=[{"message": "Added a priority column.", "tables": [edited], "app": {"files": [notes]}}])
    monkeypatch.setattr("src.engine.llm_provider.call_llm", m)
    res = await ob.edit_app(pid, app_id, "track a priority (low, normal, high) on every request")
    assert m.calls == ["emit_changes"], (m.calls, res["errors"])
    assert res["ok"] is True, res["errors"]
    assert res["changes"]["tables"] == ["supply_requests"] and res["changes"]["files"] == ["views/Notes.tsx"] and res["changes"]["modules"] == []
    assert res["message"] == "Added a priority column." and res["version_id"]
    from src.services.data_table_service import DataTableService
    async with pg() as s:
        svc = DataTableService(s)
        t = await svc.table_by_name(pid, "supply_requests")
        assert "priority" in [c["name"] for c in t.columns]
        rows = (await svc.list_rows(t, limit=5))["rows"]
        assert rows and all(r["data"].get("priority") == "normal" for r in rows), "existing rows get the column's default"
    # a question changes nothing and costs one call
    m2 = FakeModel(changes=[{"message": "Requests have a status."}])
    monkeypatch.setattr("src.engine.llm_provider.call_llm", m2)
    q = await ob.edit_app(pid, app_id, "what statuses exist?")
    assert q["ok"] and q["message"] == "Requests have a status." and not any(q["changes"].values()) and m2.calls == ["emit_changes"]


def test_a_plan_gets_the_new_columns_additively():
    from src.services import project_plan as pp

    body = pp.normalize(plan_json())
    nb = ob.plan_with_tables(body, [{"name": "supply_requests", "columns": [{"name": "item", "type": "string"}, {"name": "priority", "type": "string"}]},
                                    {"name": "extra", "description": "d", "columns": [{"name": "x", "type": "integer"}]}])
    t = next(t for t in nb["data"]["tables"] if t["name"] == "supply_requests")
    assert [c["name"] for c in t["columns"]].count("priority") == 1 and "extra" in {x["name"] for x in nb["data"]["tables"]}
    assert ob.plan_with_tables(nb, [{"name": "supply_requests", "columns": [{"name": "priority", "type": "string"}]}]) is None


# ── the coordinator's default path ───────────────────────────────────────────


async def test_the_default_build_is_one_model_call_for_a_small_plan(pg, monkeypatch):
    from tests.unit.test_build_coordinator import client, run

    monkeypatch.delenv("WORKFLOW_BUILDER_MODE", raising=False)
    assert ob.builder_mode() == "oneshot"
    m = FakeModel(bundles=[typed_bundle_json()])
    monkeypatch.setattr("src.engine.llm_provider.call_llm", m)
    seen: list[dict[str, Any]] = []
    real = bc._publish
    monkeypatch.setattr(bc, "_publish", lambda bid, ev: (seen.append(ev), real(bid, ev))[1])
    c = await client(pg)
    async with pg() as s:
        pid = (await ProjectService(s).create(f"build-{uuid.uuid4().hex[:8]}")).id
    plan = plan_json()
    for rt in plan["routes"]:        # the recorded bundle answers list routes with the {rows,...} envelope; the old plan's response shapes differ
        rt.pop("response", None)
    r = await c.post(f"/api/projects/{pid}/plan/draft", json={"body": plan}, headers={"accept": "application/json"})
    assert r.status_code == 200, r.text
    r = await c.post(f"/api/projects/{pid}/plan/1/approve", json={})
    assert r.status_code == 200, r.text

    b = await run(c, pid, statuses=bc.TERMINAL)
    assert b["status"] == "done", [(s["key"], s["status"], s["summary"], [p["message"] for p in s["problems"]]) for s in b["steps"]]
    assert m.calls == ["emit_bundle"], "the whole app, one model call"
    st = {s["key"]: s for s in b["steps"]}
    assert st["module:requests"]["status"] == "passed" and st["module:requests"]["module_id"]
    assert st["module:common"]["status"] == "skipped"                      # the shared helpers live in the app's module
    assert st["input"]["status"] == "skipped"                              # no starting-rows stop
    assert st["app:supply_requests"]["status"] == "passed" and st["app:supply_requests"]["app_id"]
    assert b["report"]["timing"]["model_calls"] == 1 and {"write", "first_load", "first_check"} <= set(b["report"]["timing"]["phases"])
    assert b["report"]["plan_check"]["ok"] and b["report"]["failed"] == []
    # the live view: files for the module and every app file, routes stub -> built -> passing, a preview, the tables
    files = {e["path"] for e in seen if e["type"] == "file"}
    assert "backend/requests.py" in files and any(p.endswith("/App.tsx") for p in files) and any(p.startswith("backend/tables/") for p in files)
    by_route: dict[str, list[str]] = {}
    for e in seen:
        if e["type"] == "route":
            by_route.setdefault(f"{e['method']} {e['path']}", []).append(e["status"])
    assert by_route["GET /requests/mine"][0] == "stub" and by_route["GET /requests/mine"][-1] == "passing" and "built" in by_route["GET /requests/mine"]
    assert any(e["type"] == "preview" and e["app_id"] == st["app:supply_requests"]["app_id"] for e in seen)
    assert seen[-1]["type"] == "done"

    # an edit of the built app records the plan revision (no approval gate) and migrates
    app_id = st["app:supply_requests"]["app_id"]
    cur = await ob.current_bundle(pid, app_id)
    table = next(t for t in cur["tables"] if t["name"] == "supply_requests")
    edited = {**table, "columns": table["columns"] + [{"name": "cost_centre", "type": "string"}]}
    m2 = FakeModel(changes=[{"message": "ok", "tables": [edited]}])
    monkeypatch.setattr("src.engine.llm_provider.call_llm", m2)
    res = await ob.edit_app(pid, app_id, "add a cost centre")
    assert res["ok"], res["errors"]
    assert res["plan_revision"] == {"version": 2, "from": 1}, res["plan_revision"]
    async with pg() as s:
        from src.services import project_plan as pp
        rec = await pp.load_approved(s, pid)
        t = next(t for t in rec["body"]["data"]["tables"] if t["name"] == "supply_requests")
        assert rec["version"] == 2 and "cost_centre" in [x["name"] for x in t["columns"]]


# ── app builder chat edits on a one-shot app ────────────────────────────────


async def test_a_chat_edit_of_a_one_shot_app_is_one_call_and_includes_data_changes(pg, monkeypatch):
    import json as _json

    from src.repositories.api_test_repository import ApiTestRepository
    from src.schemas.app_builder import AppBuilderChatRequest
    from src.services.app_builder_ai_service import AppBuilderAIService

    pid = await new_project(pg)
    loaded = await ob.load(pid, ob.normalize_bundle(typed_bundle_json()))
    assert loaded["errors"] == []
    app_id = loaded["app_id"]
    cur = await ob.current_bundle(pid, app_id)
    table = next(t for t in cur["tables"] if t["name"] == "notifications")
    edited = {**table, "columns": table["columns"] + [{"name": "channel", "type": "string"}]}
    note = {"path": "views/Channel.tsx", "content": "/** Channel picker. */\nexport default function Channel() { return null }\n"}
    m = FakeModel(changes=[{"message": "Notifications now carry a channel.", "tables": [edited], "app": {"files": [note]}}])
    monkeypatch.setattr("src.engine.llm_provider.call_llm", m)
    monkeypatch.setattr("src.services.app_builder_ai_service.call_llm", m)      # the agent loop must not run
    async with pg() as s:
        from src.services.app_service import AppService
        from src.services.bundle_storage import PostgresBundleStorage
        from src.services.file_storage import PostgresFileStorage

        svc = AppBuilderAIService(ApiTestRepository(s), AppService(s, PostgresFileStorage(s), PostgresBundleStorage(s)))
        events = []
        async for ev in svc.stream_chat(AppBuilderChatRequest(message="add a channel to notifications", app_id=app_id, project_id=pid)):
            if ev.get("event") == "message":
                events.append(_json.loads(ev["data"]))
    assert m.calls == ["emit_changes"], m.calls
    kinds = [e["type"] for e in events]
    assert "error" not in kinds, events
    text = next(e["content"] for e in events if e["type"] == "text")
    assert "channel" in text
    code = next(e for e in events if e["type"] == "code")
    assert "views/Channel.tsx" in {f["path"] for f in code["files"]}
    assert next(e for e in events if e["type"] == "stats")["model_calls"] == 1 if "stats" in kinds else code["stats"]["model_calls"] == 1
    from src.services.data_table_service import DataTableService
    async with pg() as s:
        t = await DataTableService(s).table_by_name(pid, "notifications")
        assert "channel" in [c["name"] for c in t.columns]


# ── polish: preview users, client-before-preview order, progress, checklist noise ────────────────────────────


def test_the_brief_names_the_preview_users_and_asks_for_a_role_screen():
    b = ob.brief()
    for uid, role in (("alice", "staff"), ("bob", "manager"), ("carol", "admin")):
        assert f"`{uid}`" in b and role in b
    assert "req.caller.id" in b and "mgr-ana" in b            # told not to invent ids
    assert "screen to manage the role table" in b
    assert "next to that field" in b and "clear a field's error" in b


async def test_the_client_is_regenerated_after_the_module_and_before_the_app_and_preview(pg, monkeypatch):
    from src.routes import api_routes
    from src.services.app_service import AppService

    order: list[str] = []
    real_client = api_routes.client_source

    async def client_spy(*a, **k):
        order.append("client")
        src, h = await real_client(*a, **k)
        assert "listRequests" in src or "getRequestsMine" in src or "api" in src
        return src, h

    real_update = AppService.update_app

    async def update_spy(self, aid, req):
        order.append("app_saved")
        return await real_update(self, aid, req)

    def emit_spy(ev):
        if ev.get("type") == "preview":
            order.append("preview")
        elif ev.get("type") == "file" and ev["path"].startswith("backend/") and ev.get("done"):
            order.append("module_saved")

    monkeypatch.setattr(api_routes, "client_source", client_spy)
    monkeypatch.setattr(AppService, "update_app", update_spy)
    monkeypatch.setattr(bc, "emit", emit_spy)
    pid = await new_project(pg)
    loaded = await ob.load(pid, ob.normalize_bundle(typed_bundle_json()))
    assert loaded["errors"] == [], loaded["errors"]
    assert order == ["module_saved", "client", "app_saved", "preview"], order
    assert loaded["client_ops"] > 0


async def test_build_announces_code_and_phases_before_the_checks(pg, monkeypatch):
    m = FakeModel(bundles=[typed_bundle_json()])
    monkeypatch.setattr("src.engine.llm_provider.call_llm", m)
    events: list[tuple[str, str, bool | None]] = []
    monkeypatch.setattr(bc, "emit", lambda ev: events.append((ev["type"], ev.get("path", ""), ev.get("done"))))
    logs: list[str] = []

    async def log(msg):
        logs.append(msg)
        events.append(("log", msg, None))

    pid = await new_project(pg)
    async with pg() as s:
        from src.services.app_service import AppService  # noqa: F401
    app_id = None
    res = await ob.build(pid, plan_json(), app_id=app_id, log=log, fix=False)
    first_route = next(i for i, e in enumerate(events) if e[0] == "route" )
    assert any(e[0] == "file" and e[1].startswith("backend/") and e[2] is False for e in events[:first_route])
    text = " | ".join(logs)
    for phrase in ("Writing", "Loading", "Saving the backend", "Checking the backend routes", "Type-checking"):
        assert phrase in text, text
    assert res["ok"], res["errors"]
