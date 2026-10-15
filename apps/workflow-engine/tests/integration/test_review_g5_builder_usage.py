"""UI-DESIGN-REVIEW.md fixes, group 5 — app builder SSE, declared endpoints, api-usage, publish slug.

C5 SSE error + done, C6 per-call stubs / tool on every turn / client regenerated, C11 stub status +
literal scan beyond fetch(), D14 api_calls cached at version creation, C4 publish slug_taken.
The builder tests drive the real AppBuilderAIService in-process against the test database with a
scripted ``call_llm`` (the provider adapters are not under test here).
"""

from __future__ import annotations

import asyncio
import json
import shutil

import pytest

from .conftest import DB_HOST, DB_PASSWORD, DB_PORT, DB_USER, uniq
from .journeys._helpers import new_project, save_app

pytestmark = pytest.mark.integration


# ── in-process builder harness ───────────────────────────────────────────────


def _run_builder(test_db, body: dict, script):
    """Run AppBuilderAIService.stream_chat with ``script(turn, kwargs) -> LLMResponse`` as the LLM."""
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker

    import src.services.app_builder_ai_service as svc_mod
    from src.repositories.api_test_repository import ApiTestRepository
    from src.schemas.app_builder import AppBuilderChatRequest
    from src.services.app_service import AppService
    from src.services.bundle_storage import PostgresBundleStorage
    from src.services.file_storage import PostgresFileStorage

    calls: list[dict] = []

    async def fake_call_llm(**kwargs):
        calls.append(kwargs)
        out = script(len(calls), kwargs)
        if isinstance(out, BaseException):
            raise out
        return out

    async def go():
        engine = create_async_engine(
            f"postgresql+asyncpg://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{test_db.name}",
            connect_args={"server_settings": {"search_path": '"workflow-app"'}})
        sf = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        events = []
        try:
            async with sf() as s:
                service = svc_mod.AppBuilderAIService(ApiTestRepository(s),
                                                      AppService(s, PostgresFileStorage(s), PostgresBundleStorage(s)))
                async for ev in service.stream_chat(AppBuilderChatRequest(**body)):
                    events.append({"event": ev["event"], "data": json.loads(ev["data"]) if isinstance(ev["data"], str) else ev["data"]})
        finally:
            await engine.dispose()
        return events

    import src.db.session as db_session

    async def _forbidden(*a, **k):  # this harness must never reach the engine's default (non-test) database
        raise AssertionError("default async_session_factory used from the in-process builder test")

    orig, orig_sf = svc_mod.call_llm, db_session.async_session_factory
    svc_mod.call_llm, db_session.async_session_factory = fake_call_llm, _forbidden
    try:
        return asyncio.run(go()), calls
    finally:
        svc_mod.call_llm, db_session.async_session_factory = orig, orig_sf


def _resp(text=None, tool_calls=()):
    from src.engine.llm_provider import LLMResponse, ToolCall
    return LLMResponse(text=text, tool_calls=[ToolCall(id=f"t{i}", name=n, args=a) for i, (n, a) in enumerate(tool_calls)])


def _types(events):
    return [e["data"].get("type") if e["event"] == "message" else e["event"] for e in events]


# ── C5 ───────────────────────────────────────────────────────────────────────


def test_c5_llm_failure_emits_error_then_done(api, test_db):
    events, _ = _run_builder(test_db, {"message": "hi", "api_execution_ids": [], "conversation_history": []},
                             lambda n, kw: RuntimeError("provider exploded"))
    types = _types(events)
    assert types[-2:] == ["error", "done"], types
    err = events[-2]["data"]
    assert err["code"] == "llm_failed" and err["message"]
    assert "Traceback" not in err["message"]


def test_c5_unexpected_exception_still_ends_with_done(api, test_db, monkeypatch):
    import src.services.app_builder_ai_service as svc_mod

    async def broken(self, request):
        raise KeyError("boom")
    monkeypatch.setattr(svc_mod.AppBuilderAIService, "_resolve_current_files", broken)
    events, _ = _run_builder(test_db, {"message": "hi", "api_execution_ids": [], "conversation_history": []},
                             lambda n, kw: _resp(text="ok"))
    assert _types(events)[-2:] == ["error", "done"] and events[-2]["data"]["code"] == "internal"


def test_c5_http_stream_always_ends_with_done(api):
    """Over HTTP (whatever LLM is configured): the last event is `done`; an `error` event, if any, precedes it."""
    body = {"message": "hi", "api_execution_ids": [], "conversation_history": []}
    seen = []
    with api.http.stream("POST", "/api/ai/app-builder/chat", json=body, headers={"Content-Type": "application/json"},
                         timeout=120) as resp:
        assert resp.status_code == 200
        ev = None
        for line in resp.iter_lines():
            if line.startswith("event:"):
                ev = line[6:].strip()
            elif line.startswith("data:") and line[5:].strip():
                seen.append((ev, json.loads(line[5:].strip())))
    assert seen and seen[-1][0] == "done", seen[-3:]
    errs = [i for i, (e, d) in enumerate(seen) if d.get("type") == "error"]
    assert all(i == len(seen) - 2 for i in errs)


# ── C6 ───────────────────────────────────────────────────────────────────────


def test_c6_declare_endpoint_on_first_turn_creates_stub_in_call_and_regenerates_client(api, test_db):
    P, S = new_project(api, "c6")
    api.post(f"/projects/{P}/routes", {"method": "GET", "path": "/orders", "name": "listOrders", "handler": {"type": "stub"}})
    A = api.post(f"/projects/{P}/apps", {"name": uniq("app"), "definition": {}}).json()["id"]
    seen_tools: list[list[str]] = []
    tool_results: list[str] = []

    def script(n, kw):
        seen_tools.append([t["name"] for t in kw["tools"]])
        if n == 1:
            return _resp(tool_calls=[
                ("declare_endpoint", {"method": "GET", "path": "/regions", "name": "listRegions"}),
                ("declare_endpoint", {"method": "POST", "path": "/regions", "name": "listOrders"}),  # name clash → error
                ("declare_endpoint", {"method": "GET", "path": "/orders"}),                          # exists already
            ])
        tool_results.extend(m["content"] for m in kw["messages"] if m.get("role") == "tool")
        return _resp(text="done")

    events, calls = _run_builder(test_db, {"message": "Build a regions page", "app_id": A, "project_id": P,
                                           "api_execution_ids": [], "conversation_history": []}, script)
    assert "declare_endpoint" in seen_tools[0], seen_tools   # offered on the very first request
    types = _types(events)
    assert types[-1] == "done" and "error" not in types
    decl = [e["data"] for e in events if e["data"].get("type") == "endpoints_declared"]
    results = [r for d in decl for r in d["results"]]
    assert [(r["method"], r["path"], r["status"]) for r in results] == [
        ("GET", "/regions", "created"), ("POST", "/regions", "error"), ("GET", "/orders", "existing")]
    assert results[1]["error"]["code"] == "name_taken"
    assert [r["path"] for d in decl for r in d["routes"]] == ["/regions"]
    # each stub was created inside its tool call: the stub event precedes the next model turn
    first_decl = types.index("endpoints_declared")
    assert first_decl < [i for i, e in enumerate(events) if e["data"].get("type") == "phase"][1]
    # the model saw per-stub results, and the regenerated client, before its next turn
    assert any("created" in t and "/regions" in t for t in tool_results)
    assert any("listRegions" in json.dumps(m) for m in calls[1]["messages"])
    code = next(e["data"] for e in events if e["data"].get("type") == "code")
    client = next(f["content"] for f in code["files"] if f["path"] == "api/client.ts")
    assert "listRegions" in client
    routes = {(r["method"], r["path"]): r for r in api.get(f"/projects/{P}/routes").json()}
    assert routes[("GET", "/regions")]["source"] == "app_stub" and ("POST", "/regions") not in routes


# ── C11 / D14 ────────────────────────────────────────────────────────────────


def test_c11_api_usage_stub_status_and_literals_beyond_fetch(api, test_db):
    P, S = new_project(api, "c11")
    W = api.post(f"/projects/{P}/workflows", {"name": uniq("w"), "nodes": [
        {"name": "Start", "type": "Start", "parameters": {}, "position": {"x": 0, "y": 0}}], "connections": []}).json()["id"]
    api.post(f"/projects/{P}/routes", {"method": "GET", "path": "/refunds/stats", "name": "refundStats", "handler": {"type": "stub"}})
    api.post(f"/projects/{P}/routes", {"method": "GET", "path": "/orders/{id}", "name": "getOrder",
                                       "handler": {"type": "workflow", "workflow_id": W}})
    A = api.post(f"/projects/{P}/apps", {"name": uniq("app"), "definition": {}}).json()["id"]
    src = (f"import {{ api }} from './api/client'\n"
           f"const ORDER_URL = `/api/p/{S}/orders/${{1}}`\n"
           f"export default function App() {{\n"
           f"  api.refundStats()\n"
           f"  axios.get('/api/p/{S}/orders/7')\n"
           f"  return <a href=\"/api/p/{S}/nope\">x</a>\n"
           f"}}\n")
    save_app(api, A, src)
    rows = api.get(f"/apps/{A}/api-usage").json()
    by_line = {r["line"]: r for r in rows}
    assert by_line[4]["status"] == "stub" and by_line[4]["operation"] == "refundStats"
    assert by_line[2]["kind"] == "literal" and by_line[2]["operation"] == "getOrder" and by_line[2]["status"] == "ok"
    assert by_line[5]["kind"] == "literal" and by_line[5]["operation"] == "getOrder"
    assert by_line[6]["status"] == "missing" and by_line[6]["path"] == "/nope"
    assert len(rows) == 4

    # D14: the scan is cached in app_files.parsed_index at version creation and read from there
    cached = test_db.query("select f.parsed_index from app_files f join apps a on a.current_version_id = f.version_id "
                           "where a.id = %s and f.path = 'App.tsx'", (A,))[0][0]
    assert isinstance(cached.get("api_calls"), list) and len(cached["api_calls"]) == 4
    tampered = [c for c in cached["api_calls"] if c.get("operation") != "refundStats"]
    test_db.query("update app_files set parsed_index = jsonb_set(parsed_index, '{api_calls}', %s::jsonb) "
                  "where version_id = (select current_version_id from apps where id = %s) and path = 'App.tsx'",
                  (json.dumps(tampered), A))
    assert not any(r["operation"] == "refundStats" for r in api.get(f"/apps/{A}/api-usage").json())


# ── C4 ───────────────────────────────────────────────────────────────────────


@pytest.mark.skipif(shutil.which("esbuild") is None, reason="esbuild not on PATH")
def test_c4_publish_with_taken_slug_is_409_slug_taken(api):
    P, _ = new_project(api, "c4")
    src = "export default function App(){return <div>x</div>}"
    a1 = api.post(f"/projects/{P}/apps", {"name": uniq("a"), "definition": {}}).json()["id"]
    a2 = api.post(f"/projects/{P}/apps", {"name": uniq("a"), "definition": {}}).json()["id"]
    for a in (a1, a2):
        save_app(api, a, src)
    slug = uniq("taken")
    assert api.post(f"/apps/{a1}/publish", {"slug": slug, "access": "public"}).status_code == 200
    r = api.post(f"/apps/{a2}/publish", {"slug": slug, "access": "public"})
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "slug_taken" and r.json()["detail"]["slug"] == slug
    r = api.put(f"/apps/{a2}", {"slug": slug})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "slug_taken"
    # other publish errors keep their 400 string detail
    r = api.post(f"/apps/{a2}/publish", {"slug": slug.upper() + "!!", "access": "public"})
    assert r.status_code in (400, 422)
