"""Careful-developer gate: a new project app asks or records a brief before any UI file is written."""
import asyncio
import json
import shutil
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.engine.llm_provider import LLMResponse, ToolCall
from src.services import api_route_service
from src.services import app_builder_ai_service as builder

ROUTES = [SimpleNamespace(method="POST", path="/runs", path_shape="/runs", name="startRun", handler_type="python", auth="none",
                          response_schema={"type": "object"}),
          SimpleNamespace(method="GET", path="/runs/{id}", path_shape="/runs/{}", name="getRun", handler_type="stub", auth="none",
                          response_schema=None),
          SimpleNamespace(method="GET", path="/cases", path_shape="/cases", name="getCases", handler_type="python", auth="none",
                          response_schema=None)]
BRIEF = {"users": "QA engineers", "screens": [{"name": "Library", "purpose": "pick cases"}],
         "journeys": ["select cases, Run, watch"], "routes": [{"method": "POST", "path": "/runs"}, {"method": "GET", "path": "/runs/{rid}"}],
         "states": {"empty": "no cases", "loading": "spinner", "error": "red banner", "live": "polls"},
         "acceptance_criteria": ["Run creates a job"]}


def _call(name, args, i="t"):
    return LLMResponse(tool_calls=[ToolCall(id=i, name=name, args=args)])


def _service(monkeypatch, script):
    monkeypatch.setattr(builder, "clarify_enabled", lambda: True)
    monkeypatch.setattr(api_route_service.ApiRouteService, "rows", AsyncMock(return_value=ROUTES))
    call = AsyncMock(side_effect=script)
    monkeypatch.setattr(builder, "call_llm", call)
    svc = builder.AppBuilderAIService.__new__(builder.AppBuilderAIService)
    svc._project = SimpleNamespace(id="p1")
    svc._app_service = SimpleNamespace(_session=None)
    monkeypatch.setattr(svc, "_build_agent_system_prompt", lambda _: "Build UI")
    return svc, call


# a spec-sized request (150+ words) is not "vague": the domain-question gate (tests/unit/test_app_builder_f3.py) stays out of the way
SPEC_REQUEST = "Build a QA workbench for the payments team. " + "It lists the test cases and shows the runs. " * 20


def _run(svc, files, history=None, message=SPEC_REQUEST):
    async def go():
        return [json.loads(e["data"]) async for e in svc._run_agent_loop(files, [], message, history)]
    return asyncio.run(go())


def test_clarify_questions_end_the_turn_without_files(monkeypatch):
    svc, call = _service(monkeypatch, [_call("ask_clarifying_questions", {
        "questions": [{"question": "Who approves fixes?", "options": ["QA lead", "anyone"], "default": "QA lead"}],
        "brief_so_far": {"users": "QA engineers"}})])
    files = []
    events = _run(svc, files)
    clar = next(e for e in events if e["type"] == "clarify")
    assert clar["questions"][0]["id"] == "q1" and clar["round"] == 1
    text = next(e for e in events if e["type"] == "text")["content"]
    assert builder._CLARIFY_MARKER in text and "1. Who approves fixes?" in text and "QA engineers" in text
    assert files == [] and call.await_count == 1
    assert {"ask_clarifying_questions", "record_brief"} <= {t["name"] for t in call.call_args.kwargs["tools"]}


@pytest.mark.skipif(shutil.which("esbuild") is None, reason="esbuild required")
def test_write_refused_until_brief_recorded_and_routes_validated(monkeypatch):
    app = {"files": [{"path": "App.tsx", "content": "export default function App(){return <div/>}"}]}
    bad = {**BRIEF, "routes": [{"method": "GET", "path": "/invented"}]}
    svc, _ = _service(monkeypatch, [_call("write_files", app, "w1"), _call("record_brief", bad, "b1"),
                                       _call("record_brief", BRIEF, "b2"), _call("write_files", app, "w2"), LLMResponse(text="Done")])
    files = []
    events = _run(svc, files)
    results = [e["result"] for e in events if e["type"] == "tool_result"]
    assert results[0].startswith("Error: this is a new app")
    assert "/invented" in results[1] and "GET /runs/{id} (api.getRun)" in results[1]
    assert results[2].startswith("Brief recorded") and "stubs" in results[2]
    brief = next(e for e in events if e["type"] == "brief")["brief"]
    assert [r["operation"] for r in brief["routes"]] == ["startRun", "getRun"]
    paths = {f["path"] for f in files}
    assert paths == {"App.tsx", "BRIEF.md"}
    assert "- [ ] Run creates a job" in next(f["content"] for f in files if f["path"] == "BRIEF.md")


def test_no_gate_for_existing_apps_or_after_two_rounds(monkeypatch):
    svc, call = _service(monkeypatch, [LLMResponse(text="ok")])
    _run(svc, [{"path": "App.tsx", "content": "x"}])
    assert "record_brief" not in {t["name"] for t in call.call_args.kwargs["tools"]}

    marker = {"role": "assistant", "content": builder._CLARIFY_MARKER + ": 1. x"}
    svc, call = _service(monkeypatch, [_call("ask_clarifying_questions", {"questions": ["again?"]}), LLMResponse(text="ok")])
    events = _run(svc, [], [{"role": "user", "content": "a"}, marker, {"role": "user", "content": "b"}, marker])
    names = {t["name"] for t in call.call_args.kwargs["tools"]}
    assert "ask_clarifying_questions" not in names and "record_brief" in names
    assert not any(e["type"] == "clarify" for e in events)


def test_brief_requires_reading_untyped_handlers_and_handler_source_extracts_helpers(monkeypatch):
    svc, _ = _service(monkeypatch, [])
    svc._read_ops = set()
    files = []
    text, brief = asyncio.run(svc._record_brief_now({**BRIEF, "routes": [{"method": "GET", "path": "/cases"}]}, files))
    assert brief is None and 'read_backend` with ["getCases"]' in text
    svc._read_ops = {"getCases"}
    text, brief = asyncio.run(svc._record_brief_now({**BRIEF, "routes": [{"method": "GET", "path": "/cases"}]}, files))
    assert brief is not None and files[0]["path"] == "BRIEF.md"

    src = ("import studio\n\ndef helper(x):\n    return {'rows': x}\n\ndef unrelated():\n    pass\n\n"
           "@api.get('/cases')\ndef list_cases(req):\n    return helper([])\n")
    out = builder.handler_source(src, "list_cases")
    assert "@api.get('/cases')" in out and "def helper" in out and "unrelated" not in out


def test_stored_row_samples_keep_the_shape_of_passthrough_fields():
    src = "x = studio.db.table('triage_findings').get(1)\ny = studio.db.table(\"run_results\").list()\nstudio.db.table('triage_findings')"
    assert builder.tables_read_by(src) == ["triage_findings", "run_results"]
    row = {"evidence": [{"kind": "error", "text": "e" * 300}, {"kind": "network", "text": "GET /a"}, {"kind": "log", "text": "z"}],
           "deep": {"a": {"b": {"c": {"d": 1}}}}}
    out = builder.sample_value(row)
    assert out["evidence"][0]["kind"] == "error" and out["evidence"][0]["text"].endswith("…") and len(out["evidence"]) == 3
    assert out["evidence"][2] == "…" and out["deep"]["a"] == {"b": "…"}


def test_dropped_api_calls_flag_lost_behaviour_but_not_requested_removals():
    before = {"App.tsx": "api.getRunsByIdStatus({id}); api.getRunsByIdEvents({id}); api.getCases()",
              "api/client.ts": "api.unused()", "kit/hooks.ts": "api.kitThing()"}
    after = [{"path": "App.tsx", "content": "api.getRunsByIdStatus({id}); api.getRunsById({id})"},
             {"path": "views/L.tsx", "content": "await api.getCases()"}]
    assert builder.dropped_api_calls(before, after, "keep run_results rows") == ["getRunsByIdEvents"]
    assert builder.dropped_api_calls(before, after, "stop calling getRunsByIdEvents") == []
    # one of two call sites lost (the poll) while the initial load still calls it
    before = {"App.tsx": "poll(){ api.getRunsByIdEvents({id}) } load(){ api.getRunsByIdEvents({id}) }"}
    assert builder.dropped_api_calls(before, [{"path": "App.tsx", "content": "load(){ api.getRunsByIdEvents({id}) }"}], "x") \
        == ["getRunsByIdEvents"]


@pytest.mark.skipif(shutil.which("esbuild") is None, reason="esbuild required")
def test_edit_that_drops_a_call_gets_one_regression_prompt(monkeypatch):
    app = "export default function App(){ api.getCases(); api.getRuns(); return <div/> }"
    edit = {"edits": [{"path": "App.tsx", "old_string": " api.getRuns();", "new_string": ""}]}
    svc, call = _service(monkeypatch, [_call("edit_files", edit), LLMResponse(text="Done"), LLMResponse(text="Intended")])
    monkeypatch.setattr(builder, "typecheck_errors", AsyncMock(return_value=None))
    events = _run(svc, [{"path": "App.tsx", "content": app}])
    prompt = call.call_args_list[2].kwargs["messages"][-1]["content"]
    assert "`api.getRuns`" in prompt and call.await_count == 3
    assert [e["content"] for e in events if e["type"] == "text"] == ["Intended"]


def test_hand_rolled_polling_is_flagged_outside_the_kit_only():
    files = [{"path": "App.tsx", "content": "useEffect(() => { const t = setInterval(poll, 1000); return () => clearInterval(t) })"},
             {"path": "views/A.tsx", "content": "// never setInterval(x)\n/* setInterval(y) */ usePoll(fn, 1000)"},
             {"path": "kit/hooks.ts", "content": "setInterval(tick, 1000)"}, {"path": "BRIEF.md", "content": "setInterval("}]
    assert builder.hand_rolled_polling(files) == ["App.tsx"]


def test_unresolved_imports_are_reported_with_a_hint():
    files = [{"path": "App.tsx", "content": "import { api } from './api/client'\nimport V from './views/A'\nimport React from 'react'"},
             {"path": "views/A.tsx", "content": "import { api } from './api/client'\nimport { Card } from '../kit'\nimport _ from 'lodash'"},
             {"path": "api/client.ts", "content": ""}, {"path": "kit/index.ts", "content": ""}]
    err = builder.unresolved_imports(files)
    assert "views/A.tsx: cannot resolve './api/client' — did you mean '../api/client'?" in err
    assert "cannot import 'lodash'" in err and "App.tsx:" not in err
    files[1]["content"] = "import { api } from '../api/client'\nimport { Card } from '../kit'"
    assert builder.unresolved_imports(files) is None


def test_import_check_ignores_comments_and_the_generated_client():
    files = [{"path": "api/client.ts", "content": "// Import it as `import { api } from './api/client'`.\n"},
             {"path": "App.tsx", "content": "/** usage: import x from './nope' */\n// import y from './nope2'\nimport React from 'react'"}]
    assert builder.unresolved_imports(files) is None
