"""WP-C: with an approved plan the app builder records BRIEF.md from the plan's screens and routes, offers no clarify tool,
declares unbuilt planned routes as stubs with the plan's fields, and leaves apps with no plan entry alone."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

from src.engine.llm_provider import LLMResponse
from src.services import api_route_service
from src.services import app_builder_ai_service as builder
from src.services.api_route_service import path_shape
from tests.unit.builder_plan_fixture import BODY, install

TESTS = SimpleNamespace(method="GET", path="/tests", path_shape="/tests", name="getTests", handler_type="python", auth="none",
                        response_schema={"type": "object"})


class _Rows:
    """The project's routes; ``create`` (a declared stub) adds one, as the real service would."""
    def __init__(self):
        self.items = [TESTS]
        self.created: list[dict] = []

    async def rows(self, project_id):
        return list(self.items)

    async def create(self, project, payload, source=None):
        self.created.append(payload)
        r = SimpleNamespace(method=payload["method"], path=payload["path"], path_shape=path_shape(payload["path"]),
                            name="postRuns", handler_type="stub", auth="none", response_schema=payload.get("response_schema"))
        self.items.append(r)
        return {"route": {"id": "r1", "name": r.name, "method": r.method, "path": r.path}}

    async def to_dict(self, project, r):
        return {"name": r.name}


def _service(monkeypatch, script, *, plan_body=BODY, request=None, project_routes=None):
    install(monkeypatch, plan_body)
    store = project_routes or _Rows()
    monkeypatch.setattr(builder, "clarify_enabled", lambda: True)
    monkeypatch.setattr(api_route_service.ApiRouteService, "rows", lambda self, pid: store.rows(pid))
    monkeypatch.setattr(api_route_service.ApiRouteService, "create", lambda self, project, payload, source=None: store.create(project, payload, source))
    monkeypatch.setattr(api_route_service.ApiRouteService, "to_dict", lambda self, project, r: store.to_dict(project, r))
    call = AsyncMock(side_effect=script)
    monkeypatch.setattr(builder, "call_llm", call)
    svc = builder.AppBuilderAIService.__new__(builder.AppBuilderAIService)
    svc._project = SimpleNamespace(id="p1", default_environment="default")
    svc._app_service = SimpleNamespace(_session=None)
    svc._request = request or SimpleNamespace(plan_app=None, app_id=None)
    svc._declared = []
    monkeypatch.setattr(svc, "_build_agent_system_prompt", lambda _: "Build UI")

    async def no_client(*a, **k):
        raise RuntimeError("no client in unit test")
    monkeypatch.setattr("src.routes.api_routes.client_source", no_client, raising=False)
    monkeypatch.setattr(svc, "_remember", AsyncMock())
    return svc, call, store


MSG = "Build the QA workbench. " + "It lists the tests and starts runs. " * 20


def _run(svc, files, history=None):
    async def go():
        return [json.loads(e["data"]) async for e in svc._run_agent_loop(files, [], MSG, history)]
    return asyncio.run(go())


def test_plan_replaces_the_brief_round_and_the_clarify_tool(monkeypatch):
    svc, call, store = _service(monkeypatch, [LLMResponse(text="Done")])
    files: list = []
    events = _run(svc, files)
    tools = {t["name"] for t in call.call_args.kwargs["tools"]}
    assert "ask_clarifying_questions" not in tools
    brief = next(e for e in events if e["type"] == "brief")["brief"]
    assert [s["name"] for s in brief["screens"]] == ["Library"]
    assert [(r["method"], r["path"]) for r in brief["routes"]] == [("GET", "/tests"), ("POST", "/runs")]
    md = next(f["content"] for f in files if f["path"] == "BRIEF.md")
    assert "Library" in md and "/runs" in md and "Run creates a job" in md
    assert any(e["type"] == "plan" and e["version"] == 3 for e in events)
    system = call.call_args.kwargs["messages"][0]["content"]
    assert "approved project plan v3" in system and "needs a plan revision" in system
    assert svc._brief_required is False and svc._clarify_allowed is False


def test_an_unbuilt_planned_route_becomes_a_stub_with_the_plan_fields(monkeypatch):
    svc, _, store = _service(monkeypatch, [LLMResponse(text="Done")])
    events = _run(svc, [])
    assert len(store.created) == 1                                   # GET /tests exists; POST /runs does not
    c = store.created[0]
    assert (c["method"], c["path"]) == ("POST", "/runs") and c["handler"] == {"type": "stub"}
    assert c["request_schema"] == {"type": "object", "properties": {"test_ids": {}}, "required": ["test_ids"]}
    assert c["response_schema"]["properties"] == {"id": {"type": "integer"}, "job_id": {"type": "string"}}
    assert any(e["type"] == "endpoints_declared" for e in events)
    brief = next(e for e in events if e["type"] == "brief")["brief"]
    assert next(r for r in brief["routes"] if r["path"] == "/runs").get("stub") is True


def test_final_message_names_the_plan_and_the_stubs(monkeypatch):
    app = {"files": [{"path": "App.tsx", "content": "export default function App(){return <div/>}"}]}
    from src.engine.llm_provider import ToolCall
    svc, _, _ = _service(monkeypatch, [LLMResponse(tool_calls=[ToolCall(id="w", name="write_files", args=app)]),
                                       LLMResponse(text="All built.")])
    monkeypatch.setattr(builder, "typecheck_errors", AsyncMock(return_value=""), raising=False)
    monkeypatch.setattr(svc, "_delivery_gaps", lambda *a, **k: [])
    events = _run(svc, [])
    final = [e["content"] for e in events if e["type"] == "text"][-1]
    assert "Built from approved plan v3" in final and "POST /runs" in final


def test_app_with_no_plan_entry_keeps_the_old_behaviour(monkeypatch):
    other = {**BODY, "apps": [{"key": "a", "name": "A", "screens": []}, {"key": "b", "name": "B", "screens": []}]}
    svc, call, store = _service(monkeypatch, [LLMResponse(text="ok")], plan_body=other)
    events = _run(svc, [])
    assert svc._plan is None and store.created == []
    assert "ask_clarifying_questions" in {t["name"] for t in call.call_args.kwargs["tools"]}
    assert not any(e["type"] == "brief" for e in events)


def test_matching_by_request_app_id_and_name(monkeypatch):
    body = {**BODY, "apps": [{**BODY["apps"][0], "app_id": "app_1"}, {**BODY["apps"][0], "key": "other", "name": "Other", "app_id": "app_2"}]}
    svc, _, _ = _service(monkeypatch, [LLMResponse(text="ok")], plan_body=body,
                         request=SimpleNamespace(plan_app=None, app_id="app_2"))
    plan = asyncio.run(svc._plan_for_app())
    assert plan["key"] == "other"
    svc._request = SimpleNamespace(plan_app="qa_workbench", app_id=None)
    assert asyncio.run(svc._plan_for_app())["key"] == "qa_workbench"
    svc._request = SimpleNamespace(plan_app=None, app_id=None)           # two apps, nothing identifies this one
    assert asyncio.run(svc._plan_for_app()) is None


def test_chat_request_accepts_plan_app():
    from src.schemas.app_builder import AppBuilderChatRequest

    assert AppBuilderChatRequest(message="x", plan_app="qa_workbench").plan_app == "qa_workbench"
    assert AppBuilderChatRequest(message="x").plan_app is None


def test_planned_route_without_a_response_schema_does_not_drop_the_plan(monkeypatch):
    """A built python route with no response schema made the plan's brief fail, so the builder fell back to asking
    questions, which a headless build cannot answer. With a plan it builds and is told to read the handler first."""
    bare = SimpleNamespace(**{**vars(TESTS), "response_schema": None})
    store = _Rows()
    store.items = [bare]
    svc, call, _ = _service(monkeypatch, [LLMResponse(text="Done")], project_routes=store)
    events = _run(svc, [])
    assert svc._plan is not None and any(e["type"] == "brief" for e in events)
    assert "ask_clarifying_questions" not in {t["name"] for t in call.call_args.kwargs["tools"]}
    first = " ".join(str(m.get("content")) for m in call.call_args.kwargs["messages"])
    assert "read_backend" in first and "getTests" in first
