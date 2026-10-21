"""LIVE-FILES §5: the app builder owns UI and backend. ``build_backend`` runs the module generator on a planned module (an update
when it is saved, else new), saves it through the normal save path, streams ``file`` / ``route`` events on the chat stream and
hands the agent a compact result. The planner owns the database: a module, table or column the approved plan does not have
ends the turn with a ``plan_revision`` card (drafted by the planner) instead of a save. Fake builders; no model is called."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

from src.engine.llm_provider import LLMResponse, ToolCall
from src.services import app_builder_ai_service as builder
from src.services import build_coordinator as bc
from src.services import module_generator as mg
from src.services.project_service import ProjectError
from tests.unit.builder_plan_fixture import BODY, install
from tests.unit.test_app_builder_plan_brief import MSG, _service

RUNS = '''from studio import api


@api.get("/runs", name="list_runs")
def list_runs(req):
    """Runs"""
    return []


@api.post("/runs", name="start_run")
def start_run(req):
    """Start"""
    return {}
'''


class FakeBuilders:
    """The coordinator's builder seams, recorded; the generator streams a draft as the real one does."""

    def __init__(self, saved: dict[str, str] | None = None, gen: dict | None = None, save_error: ProjectError | None = None):
        self.saved = saved or {}
        self.gen = gen
        self.save_error = save_error
        self.generated: list[tuple] = []
        self.saves: list[tuple] = []

    async def module_state(self, project_id, key):
        return {"module_id": self.saved.get(key), "built_against": None}

    async def generate_module(self, project_id, key, instruction, module_id, log, is_cancelled):
        self.generated.append((key, instruction, module_id))
        await log("Generating")
        bc.emit_file("backend", f"backend/{key}.py", "draft", done=False)
        return self.gen or {"type": "result", "source": RUNS, "tables": [{"name": "runs"}],
                            "self_test": [{"route": "GET /runs", "ok": True}, {"route": "POST /runs", "ok": False}]}

    async def save_module(self, project_id, key, module_id, source, tables):
        if self.save_error is not None:
            raise self.save_error
        self.saves.append((key, module_id, source, tables))
        bc.emit_file("tables", "backend/tables/runs.json", "{}")   # what the real save streams per created table
        return module_id or f"mod_{key}"


def _svc(monkeypatch, script=(), fake=None, **kw):
    svc, call, store = _service(monkeypatch, list(script) or [LLMResponse(text="Done")], **kw)
    fake = fake or FakeBuilders()
    monkeypatch.setattr(svc, "_backend_builders", lambda: fake)
    monkeypatch.setattr(bc, "_active", AsyncMock(return_value=None))
    revise = AsyncMock(return_value={"version": 4, "diff": {"changes": [{"kind": "table", "key": "tags", "change": "added"}]}})
    monkeypatch.setattr(svc, "_revise_plan", revise)
    svc._approved = {"version": 3, "body": BODY}
    return svc, call, fake, revise


def _tool(svc, args, working_set=None):
    events: list[dict] = []
    text, revision = asyncio.run(svc._build_backend_now(args, working_set if working_set is not None else [], events.append))
    return json.loads(text), revision, events


def test_existing_module_is_generated_and_saved_as_an_update(monkeypatch):
    svc, _, fake, _ = _svc(monkeypatch, fake=FakeBuilders(saved={"runs": "mod_runs"}))
    res, revision, _ = _tool(svc, {"module": "runs", "instruction": "add a status filter to GET /runs", "reason": "filter UI"})
    assert revision is None
    assert fake.generated[0][0] == "runs" and fake.generated[0][2] == "mod_runs"
    assert "add a status filter" in fake.generated[0][1] and "[plan:runs]" in fake.generated[0][1]
    assert fake.saves[0][:2] == ("runs", "mod_runs") and fake.saves[0][3] == [{"name": "runs"}]
    assert res["status"] == "partial" and res["module"] == "runs"     # POST /runs failed its self-test
    assert {(r["method"], r["path"], r["status"]) for r in res["routes"]} == {("GET", "/runs", "passing"), ("POST", "/runs", "failing")}
    assert any("POST /runs" in p["message"] for p in res["problems"])


def test_planned_module_not_saved_yet_is_built_new(monkeypatch):
    svc, _, fake, _ = _svc(monkeypatch)
    res, _, _ = _tool(svc, {"module": "triage", "instruction": "build triage", "reason": "triage screen"})
    assert fake.generated[0][2] is None and fake.saves[0][1] is None
    assert res["module_id"] == "mod_triage"


def test_events_go_out_on_the_chat_stream(monkeypatch):
    script = [LLMResponse(tool_calls=[ToolCall(id="b", name="build_backend",
                                               args={"module": "runs", "instruction": "list and start runs", "reason": "Library"})]),
              LLMResponse(text="Done")]
    svc, call, fake, _ = _svc(monkeypatch, script)
    events = asyncio.run(_collect(svc))
    files = [e for e in events if e["type"] == "file"]
    assert [(f["builder"], f["path"], f["done"]) for f in files] == [
        ("backend", "backend/runs.py", False), ("backend", "backend/runs.py", True),
        ("tables", "backend/tables/runs.json", True), ("backend", "backend/runs.py", True)]
    assert all(f["step_key"] == "module:runs" for f in files)
    assert files[-1]["content"] == RUNS
    routes = [(e["method"], e["path"], e["status"]) for e in events if e["type"] == "route"]
    assert routes == [("GET", "/runs", "built"), ("POST", "/runs", "built"), ("GET", "/runs", "passing"), ("POST", "/runs", "failing")]
    assert any(e["type"] == "phase" and e.get("phase") == "backend" and e["message"] == "Generating" for e in events)
    result = next(e for e in events if e["type"] == "tool_result" and e["tool"] == "build_backend")
    assert json.loads(result["result"])["status"] == "partial"
    # the agent sees the compact result and carries on
    second = call.call_args_list[1].kwargs["messages"]
    tool_msg = next(m for m in second if m.get("role") == "tool" and m.get("name") == "build_backend")
    assert set(json.loads(tool_msg["content"])) >= {"status", "module", "routes", "problems"}


async def _collect(svc):
    return [json.loads(e["data"]) async for e in svc._run_agent_loop([], [], MSG, None)]


def test_save_refused_as_not_in_plan_asks_the_planner_and_ends_the_turn(monkeypatch):
    err = ProjectError(422, "schema_not_in_plan", "1 schema item(s) are not in the approved plan (v3): table 'tags' ...",
                       violations=[{"code": "unplanned_table", "table": "tags", "message": "table 'tags' is not in the approved plan"}])
    script = [LLMResponse(tool_calls=[ToolCall(id="b", name="build_backend",
                                               args={"module": "runs", "instruction": "tag runs", "reason": "tag filter"})]),
              LLMResponse(text="should not be called")]
    svc, call, fake, revise = _svc(monkeypatch, script, fake=FakeBuilders(saved={"runs": "mod_runs"}, save_error=err))
    events = asyncio.run(_collect(svc))
    assert fake.saves == []
    card = next(e for e in events if e["type"] == "plan_revision")
    assert card["version"] == 4 and card["diff"]["changes"][0]["key"] == "tags" and "tags" in card["reason"]
    assert call.await_count == 1                                     # the turn ended at the card
    reason, instruction, module = revise.call_args.args
    assert "tags" in reason and instruction == "tag runs" and module == "runs"
    last = [e for e in events if e["type"] == "text"][-1]["content"]
    assert "plan v4" in last and "Pending backend build: `runs` (plan v4)" in last


def test_unplanned_module_or_generator_schema_problems_never_save(monkeypatch):
    svc, _, fake, revise = _svc(monkeypatch)
    res, revision, _ = _tool(svc, {"module": "tags", "instruction": "tag runs", "reason": "tags"})
    assert res["status"] == "needs_plan_revision" and revision["version"] == 4
    assert fake.generated == [] and fake.saves == []
    gen = {"type": "error", "code": "generation_invalid", "message": "problems",
           "problems": [{"code": "unplanned_column", "message": "column 'tag' of 'runs' is not in the approved plan"}]}
    svc, _, fake, revise = _svc(monkeypatch, fake=FakeBuilders(gen=gen))
    res, revision, _ = _tool(svc, {"module": "runs", "instruction": "tag runs", "reason": "tags"})
    assert res["status"] == "needs_plan_revision" and fake.saves == [] and revise.await_count == 1


def test_one_backend_build_at_a_time(monkeypatch):
    svc, _, fake, _ = _svc(monkeypatch)
    monkeypatch.setattr(bc, "_active", AsyncMock(return_value=SimpleNamespace(id="build_1")))
    res, _, _ = _tool(svc, {"module": "runs", "instruction": "x", "reason": "y"})
    assert res["status"] == "busy" and "build_1" in res["problems"][0]["message"] and fake.generated == []
    monkeypatch.setattr(bc, "_active", AsyncMock(return_value=None))
    builder._BACKEND_BUILDS.add("p1")
    try:
        assert _tool(svc, {"module": "runs", "instruction": "x", "reason": "y"})[0]["status"] == "busy"
    finally:
        builder._BACKEND_BUILDS.discard("p1")
    mg._ACTIVE.add(("p1", "someone"))
    try:
        assert _tool(svc, {"module": "runs", "instruction": "x", "reason": "y"})[0]["status"] == "busy"
    finally:
        mg._ACTIVE.discard(("p1", "someone"))
    assert fake.generated == []
    assert _tool(svc, {"module": "runs", "instruction": "x", "reason": "y"})[0]["status"] != "busy"
    assert "p1" not in builder._BACKEND_BUILDS                       # released after the build


def test_the_api_client_is_refreshed_after_a_build(monkeypatch):
    svc, _, _, _ = _svc(monkeypatch)

    async def client(session, project, env):
        return "export const api = { listRuns() {} }", None
    monkeypatch.setattr("src.routes.api_routes.client_source", client, raising=False)
    ws = [{"path": "api/client.ts", "content": "old", "parsed_index": None}]
    _tool(svc, {"module": "runs", "instruction": "x", "reason": "y"}, ws)
    assert [f["content"] for f in ws if f["path"] == "api/client.ts"] == ["export const api = { listRuns() {} }"]


def test_prompt_and_tool_offered_only_with_an_approved_plan(monkeypatch):
    svc, call, _, _ = _svc(monkeypatch)
    asyncio.run(_collect(svc))
    tools = {t["name"] for t in call.call_args.kwargs["tools"]}
    assert {"build_backend", "declare_endpoint"} <= tools
    system = call.call_args.kwargs["messages"][0]["content"]
    assert "build_backend" in system and "before the UI" in system and "never design" in system.lower()
    # no approved plan: the old flow, declare_endpoint only
    svc, call, _, _ = _svc(monkeypatch)
    pp = install(monkeypatch, BODY)
    monkeypatch.setattr(pp, "load_approved", AsyncMock(return_value=None))
    asyncio.run(_collect(svc))
    tools = {t["name"] for t in call.call_args.kwargs["tools"]}
    assert "build_backend" not in tools and "declare_endpoint" in tools
    assert "build_backend" not in call.call_args.kwargs["messages"][0]["content"]
    res, _, _ = _tool(svc, {"module": "runs", "instruction": "x", "reason": "y"})
    assert res["status"] == "error" and "declare_endpoint" in res["problems"][0]["message"]


def test_a_pending_backend_build_is_resumed_on_the_next_message(monkeypatch):
    svc, call, _, _ = _svc(monkeypatch)
    history = [{"role": "user", "content": "tag runs"},
               {"role": "assistant", "content": "The planner drafted plan v3 ...\n\nPending backend build: `runs` (plan v3): tag runs"}]
    asyncio.run(_collect_h(svc, history))
    system = call.call_args.kwargs["messages"][0]["content"]
    assert "plan v3 is approved" in system and "`runs`" in system and "tag runs" in system


async def _collect_h(svc, history):
    return [json.loads(e["data"]) async for e in svc._run_agent_loop([], [], "continue", history)]


def test_revise_plan_runs_the_planner_revision(monkeypatch):
    from src.services import plan_clarify as pc

    seen = {}

    class Conv:
        def __init__(self, project_id, req, actor=None, is_disconnected=None, timeout_s=None, revise_prompt=None):
            seen.update(project_id=project_id, req=req, revise_prompt=revise_prompt)

        async def events(self):
            yield {"type": "status", "message": "Revising"}
            yield {"type": "result", "plan": {"version": 5}, "diff": {"changes": []}, "affected": {}}

    monkeypatch.setattr(pc, "PlanConversation", Conv)
    svc, _, _, _ = _svc(monkeypatch)
    out = asyncio.run(builder.AppBuilderAIService._revise_plan(svc, "runs need tags", "tag runs", "runs"))
    assert out == {"version": 5, "diff": {"changes": []}}
    assert seen["project_id"] == "p1" and seen["req"] is None
    assert "tag runs" in seen["revise_prompt"] and "runs need tags" in seen["revise_prompt"]
    assert pc.try_acquire("p1") == "p1"                              # the planner lock was released
    pc.release("p1")
