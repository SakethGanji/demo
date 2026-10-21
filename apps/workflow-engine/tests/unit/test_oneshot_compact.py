"""The compact profile of the one-shot builder (small / fast models): profile selection, per-feature calls under a token cap,
fix passes that send only the failing files, at most two fix passes, and the plain-text JSON fallback for models without tools.
The model is a fake; no real call is made."""

from __future__ import annotations

import json
from typing import Any

import pytest

from src.engine.llm_provider import LLMResponse, ToolCall
from src.services import oneshot_builder as ob
from tests.unit.conftest import anyio_backend  # noqa: F401

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setenv("WORKFLOW_ONESHOT_PROFILE", "compact")
    monkeypatch.setattr(ob, "_NO_TOOLS", set())


def one_module_plan() -> dict[str, Any]:
    routes = [{"key": "GET /items", "module": "items"}, {"key": "POST /items", "module": "items"},
              {"key": "GET /stats", "module": "items"}, {"key": "GET /tags", "module": "items"}]
    screens = [{"name": f"Screen {i}", "routes": ["GET /items"], "purpose": f"p{i}"} for i in range(7)]
    return {"title": "Small", "modules": [{"key": "items", "kind": "routes", "owns": ["items"]}], "routes": routes,
            "data": {"tables": [{"name": "items", "owner": "items", "columns": [{"name": "title", "type": "string"}]}]},
            "apps": [{"key": "app", "name": "Small App", "screens": screens}]}


MODULE = ('import studio\nfrom studio import api\n\n@api.get("/items", auth="app_token", name="listItems")\n'
          'def list_items(req):\n    """List items. Returns {rows, total, limit, offset}"""\n    return {"rows": []}\n')


class Recorder:
    def __init__(self, fail_tools: bool = False, text: str | None = None, changes: list[dict[str, Any]] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.fail_tools, self.text, self.changes = fail_tools, text, list(changes or [])

    async def __call__(self, model, messages, temperature=0.2, tools=None, **kw):
        self.calls.append({"tools": [t["name"] for t in tools or []], "messages": messages, "max_tokens": kw.get("max_tokens")})
        if tools and self.fail_tools:
            raise RuntimeError("400 INVALID_ARGUMENT: this model does not support tool use / function calling")
        if not tools:
            return LLMResponse(text=self.text)
        name = tools[0]["name"]
        if name == "emit_bundle":
            args = {"tables": [{"name": "items", "columns": [{"name": "title", "type": "string"}]}],
                    "module": {"name": "whatever", "source": MODULE}, "tests": []}
        elif name == "emit_views":
            user = messages[-1]["content"]
            import re
            paths = re.findall(r"`(views/\w+\.tsx)`", user)
            args = {"app": {"files": [{"path": p, "content": "export default function V() { return null }"} for p in paths]}}
        else:
            args = self.changes.pop(0) if self.changes else {"message": "nothing"}
        return LLMResponse(tool_calls=[ToolCall(id=f"c{len(self.calls)}", name=name, args=args)])


def test_profile_selection(monkeypatch):
    monkeypatch.delenv("WORKFLOW_ONESHOT_PROFILE")
    monkeypatch.setenv("WORKFLOW_ONESHOT_MODEL", "x-flash-1")
    assert ob.profile() == "full", "the default is full, whatever the model"
    monkeypatch.setenv("WORKFLOW_ONESHOT_PROFILE", "auto")
    for name in ("x-flash-1", "Some-GEMMA-2", "vendor-haiku-9", "gpt-mini", "m-lite", "m-nano", "m-small-1"):
        monkeypatch.setenv("WORKFLOW_ONESHOT_MODEL", name)
        assert ob.profile() == "compact", name
    for name in ("big-pro-1", "opus-9", "gemini-pro-3"):             # "mini" inside "gemini" is not a size marker
        monkeypatch.setenv("WORKFLOW_ONESHOT_MODEL", name)
        assert ob.profile() == "full", name
    monkeypatch.setenv("WORKFLOW_ONESHOT_PROFILE", "compact")
    assert ob.profile() == "compact"
    monkeypatch.setenv("WORKFLOW_ONESHOT_PROFILE", "full")
    monkeypatch.setenv("WORKFLOW_ONESHOT_MODEL", "x-flash-1")
    assert ob.profile() == "full"
    monkeypatch.setenv("WORKFLOW_ONESHOT_PROFILE", "auto")
    assert ob.profile() == "compact"
    assert ob.no_tools("a-gemma-b") and not ob.no_tools("x-flash-1")


async def test_compact_splits_a_small_plan_into_backend_then_screen_groups(monkeypatch):
    monkeypatch.setenv("WORKFLOW_ONESHOT_MODEL", "x-flash-1")
    rec = Recorder()
    monkeypatch.setattr("src.engine.llm_provider.call_llm", rec)
    plan = one_module_plan()
    assert not ob.is_big(plan) and ob.features(plan, "app") == []
    timer = ob.Timer()
    bundle = await ob.write_app(plan, app_key="app", timer=timer)
    names = [c["tools"][0] for c in rec.calls]
    assert names[0] == "emit_bundle" and names[1:] == ["emit_views"] * 3, names       # 7 screens -> groups of 3, 3, 1
    assert timer.calls == 4
    assert all(c["max_tokens"] <= ob.COMPACT_MAX_TOKENS for c in rec.calls)
    assert "Backend routes" in rec.calls[1]["messages"][-1]["content"] and "api.listItems" in rec.calls[1]["messages"][-1]["content"]
    assert [m["name"] for m in bundle["modules"]] == ["items"]
    files = [f["path"] for f in bundle["app"]["files"]]
    assert files[0] == "App.tsx" and len(files) == 8 and "AppShell" in bundle["app"]["files"][0]["content"]


async def test_full_profile_still_writes_a_small_plan_in_one_call(monkeypatch):
    monkeypatch.setenv("WORKFLOW_ONESHOT_PROFILE", "full")
    monkeypatch.setenv("WORKFLOW_ONESHOT_MODEL", "big-pro-1")
    rec = Recorder()
    monkeypatch.setattr("src.engine.llm_provider.call_llm", rec)
    await ob.write_app(one_module_plan(), app_key="app")
    assert [c["tools"][0] for c in rec.calls] == ["emit_bundle"]
    assert rec.calls[0]["max_tokens"] == ob.MAX_OUTPUT_TOKENS


def fix_bundle() -> dict[str, Any]:
    big = "x = 1\n" * 2000
    return {"tables": [{"name": "items", "columns": [{"name": "title", "type": "string"}]}], "seed": {},
            "modules": [{"name": "items", "source": MODULE}, {"name": "other", "source": "import studio\n" + big}],
            "app": {"name": "A", "files": [{"path": "App.tsx", "content": "// app " + big}, {"path": "views/Bad.tsx", "content": "// bad"},
                                           {"path": "views/Good.tsx", "content": "// good " + big}]},
            "tests": []}


async def test_compact_fix_pass_sends_only_failing_files_and_accepts_partial_changes(monkeypatch):
    monkeypatch.setenv("WORKFLOW_ONESHOT_MODEL", "x-flash-1")
    rec = Recorder(changes=[{"app": {"files": [{"path": "views/Bad.tsx", "content": "// fixed"}]}}])
    monkeypatch.setattr("src.engine.llm_provider.call_llm", rec)
    bundle = fix_bundle()
    errs = [{"where": "app", "code": "tsc", "message": "views/Bad.tsx(3,1): TS2304 cannot find name"}]
    raw = await ob.fix_pass(bundle, errs, timer=ob.Timer())
    sent = rec.calls[0]["messages"][-1]["content"]
    assert "views/Bad.tsx" in sent and "// bad" in sent
    assert "// good" not in sent and "// app" not in sent and "x = 1" not in sent
    assert rec.calls[0]["tools"] == ["emit_changes", "read_files"]          # the other files are one read away
    merged = ob.merge_changes(bundle, raw)
    by = {f["path"]: f["content"] for f in merged["app"]["files"]}
    assert by["views/Bad.tsx"] == "// fixed" and by["views/Good.tsx"].startswith("// good") and len(merged["modules"]) == 2
    # the full profile is just as lean now: only the failing file, the rest in the index
    monkeypatch.setenv("WORKFLOW_ONESHOT_PROFILE", "full")
    await ob.fix_pass(bundle, errs, timer=ob.Timer())
    sent = rec.calls[1]["messages"][-1]["content"]
    assert "// bad" in sent and "// good" not in sent and "views/Good.tsx" in sent


def test_failing_parts_resolve_module_route_and_scenario_errors():
    b = fix_bundle()
    b["tests"] = [{"name": "t1", "steps": [{"as": "alice", "op": "listItems"}]}]
    sel = ob.failing_parts(b, [{"where": "scenario:t1", "code": "scenario_failed", "message": "x"}])
    assert sel["tests"] == {"t1"} and sel["modules"] == {"items"} and not sel["files"]
    sel = ob.failing_parts(b, [{"where": "route:GET /items", "code": "http_500", "message": "x"}])
    assert sel["modules"] == {"items"}
    sel = ob.failing_parts(b, [{"where": "client", "code": "client_stale", "message": "?"}])
    assert sel["modules"] == {"items", "other"} and len(sel["files"]) == 3          # unresolved -> everything


async def _build_with(monkeypatch, model_name: str, passes_seen: list[str]) -> dict[str, Any]:
    monkeypatch.setenv("WORKFLOW_ONESHOT_MODEL", model_name)
    rec = Recorder(changes=[{"app": {"files": [{"path": "views/Bad.tsx", "content": f"// try {i}"}]}} for i in range(5)])
    monkeypatch.setattr("src.engine.llm_provider.call_llm", rec)

    async def write_app(plan, **kw):
        return fix_bundle()

    async def load_check(project_id, bundle, **kw):
        passes_seen.append(kw["phase"])
        err = {"where": "app", "code": "tsc", "message": "views/Bad.tsx(1,1): still broken"}
        return ({"errors": [], "app_id": "a1", "version_id": None, "modules": [], "route_ids": []}, {"errors": [err], "routes": []})

    async def autotest(*a, **kw):
        return {"total": 0, "passed": 0, "results": []}

    monkeypatch.setattr(ob, "write_app", write_app)
    monkeypatch.setattr(ob, "_load_check", load_check)
    monkeypatch.setattr(ob, "autotest", autotest)
    res = await ob.build("p", {"title": "T", "apps": []})
    res["fix_calls"] = sum(1 for c in rec.calls if c["tools"][:1] == ["emit_changes"])
    return res


async def test_compact_runs_at_most_two_fix_passes_and_full_one(monkeypatch):
    seen: list[str] = []
    res = await _build_with(monkeypatch, "x-flash-1", seen)
    assert res["fix_calls"] == 2 and res["fixed"] and not res["ok"] and seen == ["first", "fix", "fix2"], seen
    seen = []
    monkeypatch.setenv("WORKFLOW_ONESHOT_PROFILE", "full")
    res = await _build_with(monkeypatch, "big-pro-1", seen)
    assert res["fix_calls"] == 1 and seen == ["first", "fix"], seen


FENCED = """Here is the app:
```json
{"tables": [{"name": "items", "columns": [{"name": "title", "type": "string",},],},],
 "module": {"name": "items", "source": "import studio\\nx = [1, 2,]\\n"},
 "app": {"files": [{"path": "App.tsx", "content": "export default function App() { return null }"}]}}
```
Hope that helps."""


def test_json_from_text_tolerates_fences_prose_and_trailing_commas():
    got = ob._json_from_text(FENCED)
    assert got["module"]["source"] == "import studio\nx = [1, 2,]\n"           # a comma inside a string is left alone
    assert got["tables"][0]["columns"][0]["name"] == "title"
    assert ob._json_from_text('```\n{"a": 1}\n```') == {"a": 1}
    assert ob._json_from_text("no json here") is None and ob._json_from_text(None) is None


async def test_no_tools_model_gets_the_schema_in_the_prompt_and_fenced_json_is_parsed(monkeypatch):
    monkeypatch.setenv("WORKFLOW_ONESHOT_MODEL", "vendor-gemma-9")
    rec = Recorder(text=FENCED)
    monkeypatch.setattr("src.engine.llm_provider.call_llm", rec)
    out = await ob._ask("SYS", "USER", ob.EMIT_BUNDLE, phase="write", timer=ob.Timer())
    assert rec.calls[0]["tools"] == [] and out["module"]["name"] == "items"
    sys_prompt = rec.calls[0]["messages"][0]["content"]
    assert "emit_bundle" in sys_prompt and '"required"' in sys_prompt and "ONE JSON object" in sys_prompt
    # the fix pass takes the same route
    rec2 = Recorder(text='```json\n{"message": "ok", "app": {"files": [{"path": "views/Bad.tsx", "content": "// f"}]},}\n```')
    monkeypatch.setattr("src.engine.llm_provider.call_llm", rec2)
    raw = await ob.fix_pass(fix_bundle(), [{"where": "app", "code": "tsc", "message": "views/Bad.tsx(1,1): e"}], timer=ob.Timer())
    assert rec2.calls[0]["tools"] == [] and raw["app"]["files"][0]["content"] == "// f"
    with pytest.raises(ob.WriteError):
        monkeypatch.setattr("src.engine.llm_provider.call_llm", Recorder(text="sorry, I cannot"))
        await ob._ask("S", "U", ob.EMIT_BUNDLE, phase="write", timer=ob.Timer())


async def test_a_provider_refusal_of_tools_falls_back_to_json_and_is_remembered(monkeypatch):
    monkeypatch.setenv("WORKFLOW_ONESHOT_MODEL", "x-flash-1")
    rec = Recorder(fail_tools=True, text=json.dumps({"tables": [], "module": {"name": "m", "source": "s"}, "app": {"files": []}}))
    monkeypatch.setattr("src.engine.llm_provider.call_llm", rec)
    out = await ob._ask("S", "U", ob.EMIT_BUNDLE, phase="write", timer=ob.Timer())
    assert out["module"]["name"] == "m" and [bool(c["tools"]) for c in rec.calls] == [True, False]
    await ob._ask("S", "U", ob.EMIT_BUNDLE, phase="write", timer=ob.Timer())
    assert [bool(c["tools"]) for c in rec.calls] == [True, False, False], "the refusal is remembered: no second tool attempt"
