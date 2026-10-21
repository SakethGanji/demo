"""Silent partial builds: planned screens/routes missing, calls to routes the project lacks, and route attachment."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from src.engine.llm_provider import LLMResponse, ToolCall
from src.schemas.app_builder import AppBuilderChatRequest
from src.services import app_builder_ai_service as builder
from src.services.app_validate import (called_operations, screen_gaps, unknown_route_calls, unused_planned_routes)

CLIENT = {"path": "api/client.ts", "content": "export const api = {\n  /** GET /cases */\n  getCases: (query?: any): Promise<any> => call('GET', `/cases`, { query }),\n}\n"}


def f(path, content):
    return {"path": path, "content": content}


# ---- pure checks -------------------------------------------------------------------------------------------

def test_screen_gaps_finds_planned_screens_without_a_file_or_component():
    files = [f("views/Library.tsx", "export default function Library(){return <div>cases</div>}"),
             f("views/Runs.tsx", "export function RunHistory(){return null}"),
             f("App.tsx", "// nav: Coverage, Audit log\nexport default function App(){return null}")]
    plan = [{"name": "Library"}, {"name": "Run history"}, {"name": "Coverage map"}, {"name": "Audit log"}]
    assert screen_gaps(plan, files) == ["Coverage map", "Audit log"]  # a nav label in App.tsx is not a screen


def test_single_screen_plan_is_the_app_itself():
    assert screen_gaps([{"name": "Library"}], [f("App.tsx", "export default function App(){return null}")]) == []
    assert screen_gaps([{"name": "Library"}], []) == ["Library"]


def test_screen_gaps_accepts_a_heading_in_another_file():
    files = [f("views/Overview.tsx", "<h2>Coverage map</h2>")]
    assert screen_gaps([{"name": "Coverage map"}], files) == []


def test_unknown_route_calls_flag_operations_missing_from_the_client():
    files = [CLIENT, f("views/Live.tsx", "api.getCases(); api.getRunEvents({id:1}); api.getRunEvents({id:2})")]
    out = unknown_route_calls(files)
    assert len(out) == 1 and "api.getRunEvents" in out[0] and "declare_endpoint" in out[0]


def test_unknown_route_calls_ignores_comments_kit_and_missing_client():
    assert unknown_route_calls([f("views/A.tsx", "// api.nope()\n")]) == []
    assert unknown_route_calls([CLIENT, f("views/A.tsx", "// api.nope()\napi.getCases()")]) == []
    assert unknown_route_calls([f("views/A.tsx", "api.anything()")]) == []  # no client to compare with


def test_unused_planned_routes():
    files = [CLIENT, f("views/A.tsx", "api.getCases()")]
    plan = [{"method": "GET", "path": "/cases", "operation": "getCases"}, {"method": "GET", "path": "/runs", "operation": "getRuns"}]
    assert called_operations(files) == {"getCases"}
    assert unused_planned_routes(plan, files) == ["GET /runs"]


# ---- the agent loop ----------------------------------------------------------------------------------------

def write(path, content, id_="w"):
    return LLMResponse(tool_calls=[ToolCall(id=id_, name="write_files", args={"files": [{"path": path, "content": content}]})])


def make_service(monkeypatch, responses):
    call = AsyncMock(side_effect=responses)
    monkeypatch.setattr(builder, "call_llm", call)
    monkeypatch.setattr(builder, "_generated_syntax_errors", AsyncMock(return_value=None))
    monkeypatch.setattr(builder, "typecheck_errors", AsyncMock(return_value=None))
    svc = builder.AppBuilderAIService.__new__(builder.AppBuilderAIService)
    monkeypatch.setattr(svc, "_build_agent_system_prompt", lambda _: "Build UI")
    return svc, call


def run_loop(svc, ws):
    async def go():
        return [json.loads(e["data"]) async for e in svc._run_agent_loop(ws, [], "Build a live view")]
    return asyncio.run(go())


BAD = "export default function App(){ api.getRunEvents({id:1}); return <div>live</div> }"
GOOD = "export default function App(){ api.getCases(); return <div>live</div> }"


def test_unfixed_call_to_a_missing_route_ends_as_partial_not_done(monkeypatch):
    svc, call = make_service(monkeypatch, [write("App.tsx", BAD)] + [LLMResponse(text="Built the live view")] * 4)
    ws = [dict(CLIENT)]
    events = run_loop(svc, ws)
    assert call.await_count == 4  # write, done, nudge #1, nudge #2 -> final
    partial = [e for e in events if e.get("type") == "partial"]
    assert partial and "api.getRunEvents" in partial[0]["missing"][0]
    final = [e for e in events if e.get("type") == "text"][-1]["content"]
    assert "Partial build" in final and "getRunEvents" in final


def test_missing_route_call_is_repaired_automatically_within_budget(monkeypatch):
    svc, call = make_service(monkeypatch, [write("App.tsx", BAD), LLMResponse(text="Done"), write("App.tsx", GOOD, "w2"), LLMResponse(text="Fixed")])
    events = run_loop(svc, [dict(CLIENT)])
    assert not any(e.get("type") == "partial" for e in events)
    nudges = [m["content"] for m in call.call_args.kwargs["messages"] if m["role"] == "user" and "Delivery check" in m["content"]]
    assert len(nudges) == 1 and "declare_endpoint" in nudges[0]


def test_missing_planned_screen_is_nudged_then_reported(monkeypatch):
    svc, call = make_service(monkeypatch, [LLMResponse(tool_calls=[ToolCall(id="w", name="write_files", args={"files": [
        {"path": "App.tsx", "content": GOOD}, {"path": "views/Library.tsx", "content": "export default function Library(){ return null }"}]})])]
        + [LLMResponse(text="All done")] * 4)
    plan = {"screens": [{"name": "Library"}, {"name": "Triage drawer"}], "routes": [{"method": "GET", "path": "/cases", "operation": "getCases"}]}
    orig = svc._delivery_gaps

    def with_plan(ws, touched, report=False):
        svc._brief_data = plan   # as record_brief would have left it
        return orig(ws, touched, report)
    svc._delivery_gaps = with_plan
    events = run_loop(svc, [dict(CLIENT)])
    partial = [e for e in events if e.get("type") == "partial"]
    assert partial and any("planned route" not in m for m in partial[0]["missing"]) and any("Triage drawer" in m for m in partial[0]["missing"]) and not any("Library" in m for m in partial[0]["missing"])


def test_clean_build_has_no_delivery_nudge(monkeypatch):
    svc, call = make_service(monkeypatch, [write("App.tsx", GOOD), LLMResponse(text="Done")])
    events = run_loop(svc, [dict(CLIENT)])
    assert call.await_count == 2 and not any(e.get("type") == "partial" for e in events)


# ---- route attachment --------------------------------------------------------------------------------------

def attach_service(rows, app):
    svc = builder.AppBuilderAIService.__new__(builder.AppBuilderAIService)
    svc._project = SimpleNamespace(id="p1")
    svc._declared = []
    session = MagicMock()
    session.get = AsyncMock(return_value=app)
    session.commit = AsyncMock()
    svc._app_service = SimpleNamespace(_session=session)
    return svc, session


def test_called_routes_are_attached_to_the_app(monkeypatch):
    from unittest.mock import patch
    rows = [SimpleNamespace(id="r1", name="getCases"), SimpleNamespace(id="r2", name="getRuns"), SimpleNamespace(id="r3", name="getUnused")]
    app = SimpleNamespace(draft_definition={"studio": {"route_ids": ["r9"]}, "sourceCode": None})
    svc, session = attach_service(rows, app)
    ws = [dict(CLIENT), f("views/A.tsx", "api.getCases(); api.getRuns()")]
    with patch("src.services.api_route_service.ApiRouteService") as R, patch("sqlalchemy.orm.attributes.flag_modified"):
        R.return_value.rows = AsyncMock(return_value=rows)
        out = asyncio.run(svc._attach_routes(AppBuilderChatRequest(app_id="a1", message="x"), ws))
    assert out == ["r9", "r1", "r2"]
    assert app.draft_definition["studio"]["route_ids"] == ["r9", "r1", "r2"] and app.draft_definition["sourceCode"] is None
    session.commit.assert_awaited()


def test_attachment_is_idempotent_and_skips_commit_when_unchanged():
    from unittest.mock import patch
    rows = [SimpleNamespace(id="r1", name="getCases")]
    app = SimpleNamespace(draft_definition={"studio": {"route_ids": ["r1"]}})
    svc, session = attach_service(rows, app)
    with patch("src.services.api_route_service.ApiRouteService") as R:
        R.return_value.rows = AsyncMock(return_value=rows)
        out = asyncio.run(svc._attach_routes(AppBuilderChatRequest(app_id="a1", message="x"), [dict(CLIENT), f("v.tsx", "api.getCases()")]))
    assert out == ["r1"]
    session.commit.assert_not_awaited()
