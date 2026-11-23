"""App builder WP-3: stall guard, vague-turn-1 gate with routes present, data contract / invented literals, StepDiff,
truncation retry, parallel screens, lookups."""
import asyncio
import json
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.engine.llm_provider import LLMResponse, ToolCall
from src.services import api_route_service
from src.services import app_builder_ai_service as builder
from src.services.app_validate import (
    OpSig,
    contract_issues,
    literal_issues,
    schema_fields,
)

ROUTES = [SimpleNamespace(method="GET", path="/cases", path_shape="/cases", name="getCases", handler_type="python", auth="none",
                          response_schema=None)]
STUDIO_NM = Path(__file__).resolve().parents[3] / "workflow-studio" / "node_modules"


def _call(name, args, i="t"):
    return LLMResponse(tool_calls=[ToolCall(id=i, name=name, args=args)])


def _svc(monkeypatch, script, project=True):
    monkeypatch.setattr(builder, "clarify_enabled", lambda: True)
    monkeypatch.setattr(api_route_service.ApiRouteService, "rows", AsyncMock(return_value=ROUTES))
    seen: list[dict] = []

    async def fake(**kw):
        seen.append({"tools": [t["name"] for t in kw.get("tools") or []], "tool_choice": kw.get("tool_choice"),
                     "messages": list(kw["messages"]), "model": kw["model"]})
        r = script.pop(0) if script else LLMResponse(text="done")
        return r(kw) if callable(r) else r

    monkeypatch.setattr(builder, "call_llm", fake)
    svc = builder.AppBuilderAIService.__new__(builder.AppBuilderAIService)
    svc._project = SimpleNamespace(id="p1") if project else None
    svc._app_service = SimpleNamespace(_session=None)
    monkeypatch.setattr(svc, "_build_agent_system_prompt", lambda _: "Build UI")
    return svc, seen


def _run(svc, files, message="change it", history=None):
    async def go():
        return [json.loads(e["data"]) async for e in svc._run_agent_loop(files, [], message, history)]
    return asyncio.run(go())


APP = {"path": "App.tsx", "content": "export default function App(){return <div/>}"}


# ── stall guard ────────────────────────────────────────────────────────────


def test_stall_guard_counts_reads_and_resets_on_a_real_edit():
    g = builder.StallGuard(3)
    for _ in range(3):
        g.record("read_files", "## x")
    assert g.exhausted and g.remaining == 0
    g.record("edit_files", "Error: old_string not found in App.tsx.")
    assert g.exhausted  # a failed patch is not progress
    g.record("edit_files", "Edited App.tsx: replaced 3 chars with 4 chars")
    assert not g.exhausted and g.reads == 0
    tools = [{"name": n} for n in ("list_files", "read_files", "edit_files", "write_files", "ask_clarifying_questions", "record_brief")]
    assert {t["name"] for t in builder.forced_tools(tools)} == {"edit_files", "write_files", "ask_clarifying_questions"}


def test_after_the_read_budget_only_edit_tools_are_offered_and_required(monkeypatch):
    reads = [_call("list_files", {}, f"r{i}") if i % 2 else _call("search_files", {"pattern": f"p{i}"}, f"r{i}")
             for i in range(builder.IDLE_TURN_BUDGET)]
    edit = _call("edit_files", {"edits": [{"path": "App.tsx", "old_string": "<div/>", "new_string": "<div>hi</div>"}]}, "e1")
    svc, seen = _svc(monkeypatch, reads + [edit, LLMResponse(text="Done")], project=False)
    files = [dict(APP)]
    events = _run(svc, files)
    # every call before the budget is spent sees the read tools and no forcing
    assert all("list_files" in s["tools"] and s["tool_choice"] is None for s in seen[:builder.IDLE_TURN_BUDGET])
    forced = seen[builder.IDLE_TURN_BUDGET]
    assert set(forced["tools"]) <= builder.FORCED_TOOL_NAMES and "list_files" not in forced["tools"] and forced["tool_choice"] == "required"
    assert any("READ BUDGET USED" in m.get("content", "") for m in forced["messages"] if m["role"] == "user")
    assert "<div>hi</div>" in files[0]["content"]
    assert not any("check in" in e.get("content", "") for e in events if e["type"] == "text")


def test_read_tool_called_past_the_budget_is_refused(monkeypatch):
    svc, _ = _svc(monkeypatch, [_call("search_files", {"pattern": f"p{i}"}, f"r{i}") for i in range(builder.IDLE_TURN_BUDGET)]
                  + [_call("read_files", {"paths": ["App.tsx"]}, "x"), LLMResponse(text="ok")], project=False)
    events = _run(svc, [dict(APP)])
    results = [e["result"] for e in events if e["type"] == "tool_result"]
    assert results[-1].startswith("Error: read budget used")


def test_ranged_read_and_closest_line_hint():
    svc = builder.AppBuilderAIService.__new__(builder.AppBuilderAIService)
    body = "\n".join(f"line {i}" for i in range(1, 400))
    ws = [{"path": "views/A.tsx", "content": body}]
    out = svc._tool_read_files({"paths": ["views/A.tsx:10-12"]}, ws)
    assert "(lines 10-12 of 399)" in out and "  10 | line 10" in out and "line 13" not in out
    assert builder.parse_read_spec("a/b.tsx:5") == ("a/b.tsx", 5, 5) and builder.parse_read_spec("a.tsx") == ("a.tsx", None, None)
    miss = svc._tool_edit_files({"edits": [{"path": "views/A.tsx", "old_string": "line 20 "}]}, ws)
    assert "Closest text in the file" in miss and "  20 | line 20" in miss


# ── turn-1 gate with routes present ────────────────────────────────────────


def test_vague_ask_on_a_new_app_asks_domain_questions_even_though_routes_exist(monkeypatch):
    qs = {"questions": [{"question": "Who approves AI fixes: QA lead or any engineer?", "options": ["QA lead", "any engineer"], "default": "QA lead"}]}
    svc, seen = _svc(monkeypatch, [_call("write_files", {"files": [{"path": "App.tsx", "content": "x"}]}, "w"),
                                   _call("record_brief", {"users": "qa"}, "b"), _call("ask_clarifying_questions", qs, "q")])
    files = []
    events = _run(svc, files, message="Build a QA tool")
    assert builder.is_vague_request("Build a QA tool") and not builder.is_vague_request("word " * 200)
    # nothing may be written on a vague first turn: write_files is not even offered
    assert "write_files" not in seen[0]["tools"] and "ask_clarifying_questions" in seen[0]["tools"]
    system = seen[0]["messages"][0]["content"]
    for topic in ("who uses it", "core journeys", "live", "approvals", "dashboard"):
        assert topic in system.lower() or topic in system
    results = [e["result"] for e in events if e["type"] == "tool_result"]
    assert results[1].startswith("Error: the request is short, so ask first")  # record_brief refused server-side
    clar = next(e for e in events if e["type"] == "clarify")
    assert clar["round"] == 1 and clar["max_rounds"] == 2
    assert files == []


def test_second_round_is_allowed_after_answers_and_a_long_request_is_not_vague(monkeypatch):
    marker = {"role": "assistant", "content": builder._CLARIFY_MARKER + ": 1. who approves?"}
    svc, seen = _svc(monkeypatch, [_call("ask_clarifying_questions", {"questions": ["Which env?"]}, "q")])
    events = _run(svc, [], message="QA leads approve; use defaults", history=[{"role": "user", "content": "Build a QA tool"}, marker])
    assert any(e["type"] == "clarify" and e["round"] == 2 for e in events)
    assert "second round" in seen[0]["messages"][0]["content"]


def test_clarify_answers_are_recorded_as_knowledge(monkeypatch):
    svc, _ = _svc(monkeypatch, [])
    rec = []

    async def remember(kind, key, title, body, **kw):
        rec.append((kind, key, body))
    monkeypatch.setattr(svc, "_remember", remember)
    marker = {"role": "assistant", "content": builder._CLARIFY_MARKER + ": 1. who approves?"}
    req = SimpleNamespace(app_id="app_1", message="QA lead approves",
                          conversation_history=[{"role": "user", "content": "Build a QA tool"}, marker])
    asyncio.run(svc._persist_clarify_answers(req))
    assert rec[0][0] == "clarify_answer" and rec[0][1] == "app_1:round1"
    assert rec[0][2]["answers"] == "QA lead approves" and "who approves" in rec[0][2]["questions"]
    rec.clear()
    asyncio.run(svc._persist_clarify_answers(SimpleNamespace(app_id="a", message="x", conversation_history=[{"role": "user", "content": "x"}])))
    assert rec == []


# ── data contract and invented literals ────────────────────────────────────

SIGS = {"getCases": OpSig(fields={"rows", "total", "case_key", "title", "status"}, known=True),
        "getUnknown": OpSig(fields=set(), known=False)}


def test_contract_check_catches_a_field_the_route_never_returns():
    files = [{"path": "views/Library.tsx", "content": (
        "const res = await api.getCases();\n"
        "setRows(res.items);\n"                       # invented: the route returns rows
        "const t = res.total;\n"
        "res.rows.map((r) => r.title + r.last_green)\n")}]  # invented: last_green
    issues = contract_issues(files, SIGS)
    assert any("`res.items`" in i and "api.getCases" in i and "rows" in i for i in issues)
    assert any("`r.last_green`" in i for i in issues)
    assert not any("res.total" in i or "r.title" in i for i in issues)


def test_contract_check_follows_usepoll_destructuring_and_skips_unknown_routes():
    files = [{"path": "App.tsx", "content": (
        "const { data: cases } = usePoll(() => api.getCases(), 2000);\n"
        "cases?.rows.map((c) => c.priority);\n"
        "const { count } = await api.getCases();\n"
        "const x = await api.getUnknown();\nx.anything\n")}]
    issues = contract_issues(files, SIGS)
    assert any("`c.priority`" in i for i in issues) and any("`count`" in i for i in issues)
    assert not any("anything" in i for i in issues)  # an unknown signature is never guessed at
    assert contract_issues([{"path": "kit/ui.tsx", "content": "const r = await api.getCases(); r.zzz"}], SIGS) == []


def test_schema_fields_walks_nested_properties():
    assert schema_fields({"type": "object", "properties": {"a": {"type": "array", "items": {"type": "object", "properties": {"b": {}}}}}}) == {"a", "b"}


def test_literal_check_flags_data_that_looks_invented():
    files = [{"path": "views/Triage.tsx", "content": (
        "export function T(){ return (<div>\n<span>Last green: Passed (320ms)</span>\n<h3>TC-218 pay payee</h3>\n"
        "<b>{row.status}</b>\n<span>92%</span></div>) }\nconst MOCK_RUNS = [{id: 1}]\n")}]
    issues = literal_issues(files)
    assert any("Passed (320ms)" in i for i in issues) and any("TC-218" in i for i in issues) and any("MOCK_RUNS" in i for i in issues)
    assert not any("row.status" in i for i in issues)
    assert literal_issues([{"path": "views/Ok.tsx", "content": "export const A = () => <h2>Triage queue</h2>"}]) == []


@pytest.mark.skipif(shutil.which("esbuild") is None, reason="esbuild required")
def test_invented_field_is_sent_back_for_repair_before_delivery(monkeypatch):
    bad = {"files": [{"path": "App.tsx", "content": (
        "/** app */\ndeclare const api: any\nexport default function App(){ const go = async () => { const res = await api.getCases(); return res.items }\n"
        "return <button onClick={go}>x</button> }\n")}]}
    fixed = {"edits": [{"path": "App.tsx", "old_string": "res.items", "new_string": "res.rows"}]}
    svc, seen = _svc(monkeypatch, [_call("write_files", bad, "w"), LLMResponse(text="Done"), _call("edit_files", fixed, "e"), LLMResponse(text="Done")])
    monkeypatch.setattr(svc, "_op_sigs", AsyncMock(return_value={"getCases": OpSig(fields={"rows", "total"}, known=True)}))
    monkeypatch.setattr(svc, "_remember", AsyncMock())
    svc._brief_required = False
    files = [dict(APP)]
    _run(svc, files)
    nudges = [m["content"] for s in seen for m in s["messages"] if m["role"] == "user" and "Data check" in m.get("content", "")]
    assert nudges and "res.items" in nudges[0]
    assert "res.rows" in files[0]["content"]


# ── truncation and parallel screens ────────────────────────────────────────


def test_truncated_output_is_retried_in_smaller_pieces(monkeypatch):
    svc, seen = _svc(monkeypatch, [LLMResponse(output_truncated=True), _call("write_files", {"files": [{"path": "App.tsx", "content": "const a = 1\nexport default function App(){return <div/>}"}]}), LLMResponse(text="ok")], project=False)
    files = [dict(APP)]
    events = _run(svc, files)
    assert any(e.get("phase") == "retrying" for e in events)
    assert any("cut off" in m["content"] for m in seen[1]["messages"] if m["role"] == "user")
    assert "const a = 1" in files[0]["content"]


def test_screens_are_generated_concurrently(monkeypatch):
    starts: list[float] = []

    async def slow(**kw):
        starts.append(time.perf_counter())
        await asyncio.sleep(0.25)
        path = next(l for l in kw["messages"][-1]["content"].splitlines() if l.startswith("## Your screen: ")).split(": ", 1)[1]
        return _call("write_files", {"files": [{"path": path, "content": f"/** {path} */\nexport default function S(props: any){{return <div/>}}"}]})

    monkeypatch.setattr(builder, "call_llm", slow)
    svc = builder.AppBuilderAIService.__new__(builder.AppBuilderAIService)
    svc._project = None
    ws: list[dict] = []
    screens = [{"path": f"views/S{i}.tsx", "purpose": "show a DataTable of cases", "props": "selected: string[]"} for i in range(4)]
    t0 = time.perf_counter()
    out = asyncio.run(svc._build_screens_now({"screens": screens + [{"path": "App.tsx", "purpose": "no"}]}, ws))
    took = time.perf_counter() - t0
    assert {f["path"] for f in ws} == {f"views/S{i}.tsx" for i in range(4)}
    assert out.count("Built views/") == 4 and "Error: App.tsx" in out and "4 of 4 screens written concurrently" in out
    assert took < 0.25 * 4 * 0.8          # 4 serial calls would take >= 1.0s; 3 at a time takes ~0.5s
    assert max(starts) - min(starts) < 0.25 * 1.5 and builder.MAX_PARALLEL_SCREENS == 3


# ── lookups, prompt, models ────────────────────────────────────────────────


def test_component_lookup_returns_props_and_the_referenced_types():
    from src.services import platform_index as pi
    drawer = builder.format_lookup("get_component", pi.get_component("Drawer"))
    assert "variant?" in drawer and "docked" in drawer
    poll = builder.format_lookup("get_component", pi.get_component("usePoll"))
    assert "PollOptions" in poll and "until" in poll
    step = builder.format_lookup("search_components", pi.search_components("readable diff of test steps"))
    assert step.startswith("## StepDiff")
    assert builder.format_lookup("get_component", {"error": "nope"}) == "Error: nope"


def test_prompt_is_lean_and_taught_by_lookup_not_a_prop_list():
    assert len(builder._AGENT_SYSTEM_PROMPT) + len(builder._BRIEF_PROMPT) < 10_500  # was 11_961 + 1_583
    for name in ("StepDiff", "GuardChecks", "CommandPalette", "RowActions", "AiChip"):
        assert f"- {name}:" in builder._KIT_PROMPT
    assert "get_component" in builder._KIT_PROMPT and "Never guess a prop" in builder._KIT_PROMPT
    svc = builder.AppBuilderAIService.__new__(builder.AppBuilderAIService)
    svc._project = object()
    names = {t["name"] for t in svc._build_agent_tools()}
    assert {"search_components", "get_component", "get_route_signature", "get_table_schema", "build_screens"} <= names


def test_light_and_heavy_models_differ_when_the_provider_has_a_sibling(monkeypatch):
    monkeypatch.delenv("WORKFLOW_APP_BUILDER_LIGHT_MODEL", raising=False)
    monkeypatch.setattr("src.engine.llm_provider.is_configured", lambda m: True)
    assert builder._pick_light_model("gemini-3.8-flash") == "gemini-3.7-flash"
    monkeypatch.setattr("src.engine.llm_provider.is_configured", lambda m: False)
    assert builder._pick_light_model("gemini-3.8-flash") == "gemini-3.8-flash"  # never a model that cannot be called


# ── StepDiff rendering (real React render through esbuild + node) ──────────


@pytest.mark.skipif(shutil.which("esbuild") is None or shutil.which("node") is None or not (STUDIO_NM / "react-dom" / "server.js").exists(),
                    reason="esbuild, node and the studio node_modules are required")
def test_step_diff_renders_readable_steps_not_json():
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        (t / "kit").mkdir()
        for f in builder.kit_files():
            (t / f["path"]).write_text(f["content"], encoding="utf-8")
        (t / "entry.tsx").write_text("""
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { StepDiff, diffSteps, parseSteps } from './kit'
const before = [{ action: 'goto', target: '/pay' }, { action: 'click', group: 'Review', target: { role: 'button', name: 'Continue' } }, { action: 'assert', target: '.iban', value: 'GB** ****' }, { action: 'wait', value: '1s' }]
const after = JSON.stringify([{ action: 'goto', target: '/pay' }, { action: 'click', group: 'Review', target: { role: 'button', name: 'Review payment' } }, { action: 'assert', target: '.iban', value: 'GB** ****' }, { action: 'screenshot', target: 'review' }])
console.log(JSON.stringify({ html: renderToStaticMarkup(<StepDiff before={before} after={after} />),
  kinds: diffSteps(parseSteps(before), parseSteps(after)).map((r) => r.kind) }))
""")
        r = subprocess.run(["esbuild", "entry.tsx", "--bundle", "--platform=node", "--jsx=automatic", "--outfile=out.cjs", "--log-level=error"],
                           cwd=tmp, capture_output=True, text=True, check=False, env={"PATH": subprocess.os.environ["PATH"], "NODE_PATH": str(STUDIO_NM)})
        assert r.returncode == 0, r.stderr
        run = subprocess.run(["node", "out.cjs"], cwd=tmp, capture_output=True, text=True, check=False)
        assert run.returncode == 0, run.stderr
    data = json.loads(run.stdout)
    assert data["kinds"] == ["same", "changed", "same", "changed"]
    html = data["html"]
    assert "button &quot;Continue&quot;" in html and "button &quot;Review payment&quot;" in html and "line-through" in html  # locators read as text; old struck, new shown
    assert "&quot;name&quot;" not in html and "Review</span>" in html
    assert "~2 changed" in html and "no assertion changed" in html
    assert "&quot;action&quot;" not in html and '\\"' not in html and "{&quot;" not in html  # never an escaped JSON string


def test_import_check_ignores_from_and_import_inside_strings():
    code = ("import React from 'react'\nimport { api } from '../api/client'\nimport {\n  A,\n  B,\n} from '../kit'\nimport './x.css'\n"
            "const ok = lower.includes('from') || lower.includes('import')\nconst lazy = () => import('./Lazy')\nexport { Q } from '../kit'\n")
    assert [a or b for a, b in builder._IMPORT_RE.findall(code)] == ["react", "../api/client", "../kit", "./x.css", "./Lazy", "../kit"]
    files = [{"path": "views/A.tsx", "content": code}, {"path": "api/client.ts", "content": ""}, {"path": "kit/index.ts", "content": ""},
             {"path": "views/Lazy.tsx", "content": ""}, {"path": "views/x.css", "content": ""}]
    assert builder.unresolved_imports(files) is None


def test_repair_phase_allows_only_a_few_reads_then_restores_the_budget():
    g = builder.StallGuard(builder.READ_BUDGET)
    g.repair()
    assert g.budget == builder.REPAIR_READ_BUDGET
    for _ in range(builder.REPAIR_READ_BUDGET):
        g.record("search_files", "x")
    assert g.exhausted
    g.record("edit_files", "Edited App.tsx: replaced 1 chars with 2 chars")
    assert g.budget == builder.READ_BUDGET and not g.exhausted
    # platform lookups are the cheap path: eight of them stay well inside the budget
    for _ in range(8):
        g.record("get_component", "## X")
    assert not g.exhausted


def test_idle_turns_exhaust_the_guard_even_when_every_lookup_is_cheap():
    g = builder.StallGuard()
    for _ in range(builder.IDLE_TURN_BUDGET):
        g.tick()
        g.record("get_route_signature", "Route GET /x")
    assert g.exhausted and g.reads < g.budget  # one lookup per turn still stalls
    g.tick()
    g.record("edit_files", "Edited App.tsx: replaced 1 chars with 2 chars")
    assert not g.exhausted and g.turns == 0


def test_route_digest_lists_response_fields_within_its_budget(monkeypatch):
    from src.services import project_knowledge as pk
    routes = [SimpleNamespace(id=f"r{i}", name=f"op{i}", method="GET", path=f"/p{i}", auth="none") for i in range(40)]
    monkeypatch.setattr(api_route_service.ApiRouteService, "rows", AsyncMock(return_value=routes))
    rows = [{"body": {"method": "GET", "path": "/p1", "response": ["rows", "total"], "response_shape": "object", "body": []}}]
    monkeypatch.setattr(pk, "list_kind", AsyncMock(return_value=rows))
    svc = builder.AppBuilderAIService.__new__(builder.AppBuilderAIService)
    svc._project = SimpleNamespace(id="p1")
    svc._app_service = SimpleNamespace(_session=None)
    digest = asyncio.run(svc._route_digest({"r1"}, cap=700))
    assert "api.op1 GET /p1 [focus] → object {rows, total}" in digest.splitlines()[1]  # focus routes first
    assert "fields unknown (read_backend)" in digest and "more routes: call get_route_signature" in digest and len(digest) < 900


def test_edit_matches_through_whitespace_drift_but_never_through_ambiguity():
    svc = builder.AppBuilderAIService.__new__(builder.AppBuilderAIService)
    ws = [{"path": "views/A.tsx", "content": "function A() {\n    return (\n      <div className=\"a\">\n        <b>x</b>\n      </div>\n    )\n}\n"}]
    out = svc._tool_edit_files({"edits": [{"path": "views/A.tsx", "old_string": "<div className=\"a\">\n <b>x</b>\n</div>",
                                           "new_string": "<section><b>y</b></section>"}]}, ws)
    assert "matched ignoring whitespace" in out and "<section><b>y</b></section>" in ws[0]["content"] and "<div" not in ws[0]["content"]
    ws = [{"path": "B.tsx", "content": "a = foo( 1, 2 )\nb = foo( 1, 2 )\n"}]
    assert svc._tool_edit_files({"edits": [{"path": "B.tsx", "old_string": "foo(  1,   2  )", "new_string": "bar()"}]}, ws).startswith("Error")


def test_search_files_can_be_limited_to_a_path_and_skips_the_kit():
    svc = builder.AppBuilderAIService.__new__(builder.AppBuilderAIService)
    ws = [{"path": "views/A.tsx", "content": "const summary = 1"}, {"path": "views/B.tsx", "content": "summary"},
          {"path": "kit/ui.tsx", "content": "summary"}]
    assert "views/B.tsx" not in svc._tool_search_files({"pattern": "summary", "path": "views/A"}, ws)
    both = svc._tool_search_files({"pattern": "summary"}, ws)
    assert "views/A.tsx" in both and "views/B.tsx" in both and "kit/ui.tsx" not in both


def test_type_errors_arrive_with_the_edit_that_caused_them(monkeypatch):
    monkeypatch.setattr(builder, "typecheck_errors", AsyncMock(return_value="App.tsx(3,9): error TS2304: Cannot find name 'summary'.\nviews/A.tsx(1,1): error TS2307: Cannot find module './Nope'."))
    svc = builder.AppBuilderAIService.__new__(builder.AppBuilderAIService)
    g = builder.StallGuard()
    out = asyncio.run(svc._typecheck_feedback([], g))
    assert "Cannot find name 'summary'" in out and "TS2307" not in out and g.budget == builder.REPAIR_READ_BUDGET
    monkeypatch.setattr(builder, "typecheck_errors", AsyncMock(return_value="views/A.tsx(1,1): error TS2307: Cannot find module './Nope'."))
    assert asyncio.run(svc._typecheck_feedback([], builder.StallGuard())) == ""


def test_body_passed_as_second_argument_on_a_route_without_path_params_is_flagged():
    sigs = {"start_test_run": OpSig(fields={"id"}, known=True, path_params=False), "decide": OpSig(fields={"x"}, known=True, path_params=True)}
    files = [{"path": "views/T.tsx", "content": "await api.start_test_run({}, { case_ids: ids });\nawait api.decide({ id }, { decision });\nawait api.start_test_run({ case_ids })"}]
    issues = contract_issues(files, sigs)
    assert len(issues) == 1 and "api.start_test_run({}, …)" in issues[0] and "FIRST argument" in issues[0]


def test_contract_check_follows_a_type_annotated_binding():
    files = [{"path": "views/Inbox.tsx", "content": "const res: any = await api.getCases();\nconst items = Array.isArray(res) ? res : res?.rows || res?.items || [];"}]
    issues = contract_issues(files, SIGS)
    assert len(issues) == 1 and "`res.items`" in issues[0]


# ── CommandPalette in the studio preview iframe (real browser: esbuild bundle + playwright + chromium) ──────────────

_PALETTE_SCRIPT = r"""
const { chromium } = require('playwright')
const fs = require('fs')
;(async () => {
  let browser
  try { browser = await chromium.launch() } catch (e) { console.log(JSON.stringify({ skip: String(e.message).slice(0, 120) })); return }
  const page = await browser.newPage()
  const js = fs.readFileSync('bundle.js', 'utf8').replace(/<\/script/g, '<\\/script')
  const css = '.fixed{position:fixed}.inset-0{inset:0}.absolute{position:absolute}.relative{position:relative}.z-50{z-index:50}.flex{display:flex}.justify-center{justify-content:center}.p-4{padding:1rem}.pt-\\[12vh\\]{padding-top:12vh}.w-full{width:100%}.max-w-xl{max-width:36rem}'
  const srcdoc = `<!doctype html><style>${css}</style><body><div id="root"></div><script>${js}</script></body>`
  await page.setContent('<button id="chrome">studio chrome</button><iframe id="f" style="width:900px;height:600px;border:0"></iframe>')
  await page.evaluate((s) => { document.getElementById('f').srcdoc = s }, srcdoc)
  const f = page.frameLocator('#f')
  await f.locator('#openBtn').waitFor()
  const out = {}
  const open = async () => { await f.locator('#openBtn').click(); await f.locator('[data-testid=command-palette]').waitFor() }
  const gone = async () => (await f.locator('[data-testid=command-palette]').count()) === 0
  const behind = async () => { await f.locator('#behind').click({ timeout: 2000 }); return Number(await f.locator('#count').innerText()) }

  await open(); out.focus_on_input = await f.locator('input[aria-label=Question]').evaluate((el) => el === el.ownerDocument.activeElement)
  await page.keyboard.press('Escape'); out.esc_in_input = await gone(); out.click_after_esc = await behind()
  await open(); await f.locator('[role=dialog]').click({ position: { x: 5, y: 5 } })     // focus on the dialog, not the input
  await page.keyboard.press('Escape'); out.esc_in_dialog = await gone()
  await open(); await f.locator('[data-testid=palette-backdrop]').click({ position: { x: 5, y: 5 } }); out.backdrop_click = await gone()
  out.click_after_backdrop = await behind()
  await open(); await f.locator('input[aria-label=Question]').fill('typed'); out.typing_keeps_open = !(await gone())
  await page.keyboard.press('Escape'); out.closes_with_text = await gone()
  await open(); await page.locator('#chrome').focus(); await page.keyboard.press('Escape'); out.esc_from_parent_focus = await gone()
  await open(); out.reopen_has_focus = await f.locator('input[aria-label=Question]').evaluate((el) => el === el.ownerDocument.activeElement)
  out.close_calls = Number(await f.locator('#closes').innerText())
  await browser.close()
  console.log(JSON.stringify(out))
})().catch((e) => { console.log(JSON.stringify({ error: String(e.stack || e).slice(0, 800) })) })
"""


@pytest.mark.skipif(shutil.which("esbuild") is None or shutil.which("node") is None or not (STUDIO_NM / "playwright").exists(),
                    reason="esbuild, node and the studio playwright are required")
def test_command_palette_closes_on_escape_and_backdrop_inside_an_iframe():
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        (t / "kit").mkdir()
        for f in builder.kit_files():
            (t / f["path"]).write_text(f["content"], encoding="utf-8")
        (t / "entry.tsx").write_text("""
import React, { useState } from 'react'
import { createRoot } from 'react-dom/client'
import { CommandPalette } from './kit'
function App() {
  const [open, setOpen] = useState(false)
  const [n, setN] = useState(0)
  const [closes, setCloses] = useState(0)
  return <div>
    <button id="openBtn" onClick={() => setOpen(true)}>Open</button>
    <button id="behind" onClick={() => setN((x) => x + 1)}>Behind</button>
    <span id="count">{n}</span><span id="closes">{closes}</span>
    <CommandPalette open={open} onClose={() => { setCloses((c) => c + 1); setOpen(false) }} onAsk={async (q) => q} />
  </div>
}
createRoot(document.getElementById('root')!).render(<App />)
""")
        (t / "pw.cjs").write_text(_PALETTE_SCRIPT)
        env = {"PATH": subprocess.os.environ["PATH"], "NODE_PATH": str(STUDIO_NM), "HOME": subprocess.os.environ.get("HOME", "")}
        r = subprocess.run(["esbuild", "entry.tsx", "--bundle", "--platform=browser", "--format=iife", "--jsx=automatic",
                            "--define:process.env.NODE_ENV=\"production\"", "--outfile=bundle.js", "--log-level=error"],
                           cwd=tmp, capture_output=True, text=True, check=False, env=env)
        assert r.returncode == 0, r.stderr
        run = subprocess.run(["node", "pw.cjs"], cwd=tmp, capture_output=True, text=True, check=False, env=env, timeout=90)
        assert run.returncode == 0, run.stderr
    out = json.loads(run.stdout.strip().splitlines()[-1])
    if "skip" in out:
        pytest.skip(f"chromium unavailable: {out['skip']}")
    assert "error" not in out, out.get("error")
    assert out["focus_on_input"] and out["reopen_has_focus"]
    assert out["esc_in_input"] and out["click_after_esc"] == 1          # Escape closes it and the page behind is clickable again
    assert out["esc_in_dialog"]                                          # ... also when focus is on the dialog, not the input
    assert out["backdrop_click"] and out["click_after_backdrop"] == 2
    assert out["typing_keeps_open"] and out["closes_with_text"]
    assert out["esc_from_parent_focus"]                                  # focus left in the studio chrome around the preview
    assert out["close_calls"] == 5                                       # one onClose per user action, never two for one Escape


def test_a_planned_row_schema_is_not_taken_as_a_python_routes_whole_response(monkeypatch):
    """Round 11: the inbox read res.total/res.unread, which the handler returns, but the route had no signature row yet and
    its planned schema (the row shape) was treated as the full response, so a working build was labelled partly built."""
    from src.services import platform_index as pi
    routes = [SimpleNamespace(name="getNotifications", method="GET", path="/notifications", handler_type="python",
                              response_schema={"type": "object", "properties": {"kind": {"type": "string"}}}),
              SimpleNamespace(name="getStats", method="GET", path="/stats", handler_type="workflow",
                              response_schema={"type": "object", "properties": {"total": {"type": "integer"}}})]
    monkeypatch.setattr(api_route_service.ApiRouteService, "rows", AsyncMock(return_value=routes))
    sig_rows = {"/notifications": None, "/stats": None}
    monkeypatch.setattr(pi, "get_route_signature", AsyncMock(side_effect=lambda s, p, m, path: sig_rows[path]))
    svc = builder.AppBuilderAIService.__new__(builder.AppBuilderAIService)
    svc._project = SimpleNamespace(id="p1")
    svc._app_service = SimpleNamespace(_session=None)
    ensured = AsyncMock()
    monkeypatch.setattr(svc, "_ensure_knowledge", ensured)

    sigs = asyncio.run(svc._op_sigs())
    ensured.assert_awaited()                          # routes written this turn are indexed before they are judged
    assert sigs["getNotifications"].known is False    # unknown, so never guessed at
    assert sigs["getStats"].known is True             # a non-python route's schema is all there is
    files = [{"path": "Inbox.tsx", "content": "const res = await api.getNotifications({});\nconst n = res.unread;\n"}]
    assert contract_issues(files, sigs) == []

    sig_rows["/notifications"] = {"response": ["rows", "total", "unread"], "tables": []}
    sigs = asyncio.run(svc._op_sigs())
    assert sigs["getNotifications"].known is True and {"rows", "total", "unread", "kind"} <= sigs["getNotifications"].fields
