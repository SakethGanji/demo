"""Lean change calls of the one-shot builder: an edit / fix pass sends the request or errors, an index of the app and only the
relevant files; the model can read more (``read_files``) and answers with targeted ``edits``. The model is a fake."""

from __future__ import annotations

import json
from typing import Any

import pytest

from src.engine.llm_provider import LLMResponse, ToolCall
from src.services import oneshot_builder as ob
from tests.unit.conftest import anyio_backend  # noqa: F401
from tests.unit.test_oneshot_builder import bundle_json

pytestmark = pytest.mark.anyio


def app_bundle() -> dict[str, Any]:
    b = ob.normalize_bundle(bundle_json())
    big = "// filler\n" * 400
    b["app"]["files"] += [
        {"path": "views/Reports.tsx", "content": "/** Reports screen: charts. */\nimport { Chart } from '../lib/chart';\nexport default function Reports() { return null }\n" + big},
        {"path": "lib/chart.ts", "content": "/** Chart helper. */\nexport function Chart() { return null }\n" + big},
        {"path": "views/Unrelated.tsx", "content": "/** Something else. */\nexport default function Unrelated() { return null }\n" + big},
    ]
    return b


class Seq:
    """A fake model answering with a scripted list of ``(tool name, args)``; records every call."""

    def __init__(self, *replies: tuple[str, dict[str, Any]]) -> None:
        self.replies = list(replies)
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, model, messages, temperature=0.2, tools=None, **kw):
        self.calls.append({"tools": [t["name"] for t in tools or []], "user": messages[-1]["content"]})
        name, args = self.replies.pop(0)
        return LLMResponse(tool_calls=[ToolCall(id=f"c{len(self.calls)}", name=name, args=args)])


@pytest.fixture(autouse=True)
def _full(monkeypatch):
    monkeypatch.setenv("WORKFLOW_ONESHOT_PROFILE", "full")
    monkeypatch.setattr(ob, "_NO_TOOLS", set())


def test_the_edit_prompt_has_the_index_and_only_the_selected_files():
    b = app_bundle()
    user, shown = ob.edit_prompt(b, "add a Reports chart legend")
    assert "views/Reports.tsx" in shown and "lib/chart.ts" in shown            # named + imported by it
    assert "views/Unrelated.tsx" not in shown
    assert '<file path="views/Unrelated.tsx">' not in user and "views/Unrelated.tsx (" in user         # indexed, not sent
    assert "Something else." in user                                                                  # its summary line
    assert "## The app (index)" in user and "Backend modules (routes):" in user and "read_files" in user
    # a purely visual request leaves the backend module out; a data request brings it in
    assert not any(p.startswith("backend/") for p in shown)
    _, shown2 = ob.edit_prompt(b, "only managers may approve a request over 500")
    assert any(p.startswith("backend/") for p in shown2)
    assert len(user) < len(ob.bundle_text(b))


def test_the_index_lists_routes_tables_and_exports():
    idx = ob.bundle_index(app_bundle())
    assert "supply_requests(" in idx and "-> " in idx and "[app_token]" in idx
    assert "views/Reports.tsx" in idx and "[exports default Reports]" in idx


def test_selection_is_capped_by_size():
    b = app_bundle()
    shown = ob.select_for_request(b, "change the Reports chart and the Unrelated screen", cap=1500)
    assert len(shown) >= 1 and sum(len(t) for _, t in shown[1:]) <= 1500


def test_apply_edits_requires_exactly_one_match():
    text, probs = ob.apply_edits("a b a", [{"old": "b", "new": "c"}])
    assert text == "a c a" and probs == []
    text, probs = ob.apply_edits("a b a", [{"old": "a", "new": "x"}, {"old": "zz", "new": "y"}, {"old": "", "new": "q"}])
    assert text == "a b a" and len(probs) == 3 and "occurs 2 times" in probs[0] and "not found" in probs[1]


async def test_edits_apply_and_untouched_files_stay():
    b = app_bundle()
    reports = next(f for f in b["app"]["files"] if f["path"] == "views/Reports.tsx")["content"]
    m = Seq(("emit_changes", {"message": "done", "app": {"files": [{"path": "views/Reports.tsx", "edits": [
        {"old": "Reports screen: charts.", "new": "Reports screen: charts with a legend."}]}]}}))
    import src.engine.llm_provider as lp
    lp_call = lp.call_llm
    lp.call_llm = m
    try:
        user, shown = ob.edit_prompt(b, "add a legend to the Reports chart")
        raw = await ob.ask_changes(user, shown, b, phase="edit", timer=ob.Timer())
    finally:
        lp.call_llm = lp_call
    assert m.calls[0]["tools"] == ["emit_changes", "read_files"]
    merged = ob.merge_changes(b, raw)
    by = {f["path"]: f["content"] for f in merged["app"]["files"]}
    assert by["views/Reports.tsx"] == reports.replace("charts.", "charts with a legend.", 1)
    assert by["lib/chart.ts"] == next(f for f in b["app"]["files"] if f["path"] == "lib/chart.ts")["content"]


async def test_a_read_round_returns_the_file_then_the_model_answers(monkeypatch):
    b = app_bundle()
    m = Seq(("read_files", {"paths": ["views/Unrelated.tsx", "table:supply_requests", "nope.ts"]}),
            ("emit_changes", {"app": {"files": [{"path": "views/Unrelated.tsx", "edits": [{"old": "Something else.", "new": "Other."}]}]}}))
    monkeypatch.setattr("src.engine.llm_provider.call_llm", m)
    user, shown = ob.edit_prompt(b, "add a Reports chart legend")
    raw = await ob.ask_changes(user, shown, b, phase="edit", timer=ob.Timer())
    assert "Something else." not in m.calls[0]["user"].split("## Files shown in full")[1]
    second = m.calls[1]["user"]
    assert "## Files you asked to read" in second and '<file path="views/Unrelated.tsx">' in second
    assert '"name":"supply_requests"' in second and "no such file" in second
    assert raw["app"]["files"][0]["content"].startswith("/** Other. */")


async def test_reads_are_limited_to_three_extra_rounds(monkeypatch):
    b = app_bundle()
    m = Seq(*[("read_files", {"paths": ["App.tsx"]})] * 3, ("emit_changes", {"message": "nothing to change"}))
    monkeypatch.setattr("src.engine.llm_provider.call_llm", m)
    user, shown = ob.edit_prompt(b, "what does this do?")
    raw = await ob.ask_changes(user, shown, b, phase="edit", timer=ob.Timer())
    assert raw["message"] == "nothing to change"
    assert m.calls[2]["tools"] == ["emit_changes", "read_files"] and m.calls[3]["tools"] == ["emit_changes"]


async def test_a_missing_old_string_is_reported_once_and_corrected(monkeypatch):
    b = app_bundle()
    good = {"path": "lib/chart.ts", "edits": [{"old": "Chart helper.", "new": "Charts."}]}
    bad = {"path": "views/Reports.tsx", "edits": [{"old": "NOT THERE", "new": "x"}]}
    fixed = {"path": "views/Reports.tsx", "edits": [{"old": "Reports screen: charts.", "new": "Reports."}]}
    m = Seq(("emit_changes", {"app": {"files": [good, bad]}}), ("emit_changes", {"message": "ok", "app": {"files": [fixed]}}))
    monkeypatch.setattr("src.engine.llm_provider.call_llm", m)
    user, shown = ob.edit_prompt(b, "tweak Reports")
    raw = await ob.ask_changes(user, shown, b, phase="edit", timer=ob.Timer())
    assert len(m.calls) == 2 and "could not be applied" in m.calls[1]["user"] and "NOT THERE" in m.calls[1]["user"]
    by = {f["path"]: f["content"] for f in raw["app"]["files"]}
    assert by["lib/chart.ts"].startswith("/** Charts. */") and by["views/Reports.tsx"].startswith("/** Reports. */")
    assert raw["message"] == "ok"
    # a second failure is not retried again
    m2 = Seq(("emit_changes", {"app": {"files": [bad]}}), ("emit_changes", {"app": {"files": [bad]}}))
    monkeypatch.setattr("src.engine.llm_provider.call_llm", m2)
    with pytest.raises(ob.WriteError):
        await ob.ask_changes(user, shown, b, phase="edit", timer=ob.Timer())
    assert len(m2.calls) == 2


async def test_whole_file_content_still_works(monkeypatch):
    b = app_bundle()
    new = {"path": "views/New.tsx", "content": "/** New. */\nexport default function New() { return null }\n"}
    m = Seq(("emit_changes", {"app": {"files": [new, {"path": "lib/chart.ts", "content": "/** Rewritten. */\n"}]},
                              "modules": [{"name": "requests", "source": "import studio\n"}]}))
    monkeypatch.setattr("src.engine.llm_provider.call_llm", m)
    user, shown = ob.edit_prompt(b, "add a new screen")
    raw = await ob.ask_changes(user, shown, b, phase="edit", timer=ob.Timer())
    merged = ob.merge_changes(b, raw)
    by = {f["path"]: f["content"] for f in merged["app"]["files"]}
    assert by["views/New.tsx"].startswith("/** New. */") and by["lib/chart.ts"] == "/** Rewritten. */\n"
    assert merged["modules"][0]["source"] == "import studio\n"


async def test_edits_on_a_module_apply(monkeypatch):
    b = app_bundle()
    src = b["modules"][0]["source"]
    old = next(l for l in src.splitlines() if l.strip().startswith("@api")).strip()
    m = Seq(("emit_changes", {"modules": [{"name": b["modules"][0]["name"], "edits": [{"old": old, "new": "# changed\n" + old}]}]}))
    monkeypatch.setattr("src.engine.llm_provider.call_llm", m)
    raw = await ob.ask_changes("req", [], b, phase="edit", timer=ob.Timer())
    assert raw["modules"][0]["source"] == src.replace(old, "# changed\n" + old, 1)


async def test_the_fix_pass_sends_failing_files_and_an_index_for_both_profiles(monkeypatch):
    b = app_bundle()
    errs = [{"where": "app", "code": "tsc", "message": "views/Reports.tsx(3,1): TS2304 cannot find name 'x'"}]
    for prof in ("full", "compact"):
        monkeypatch.setenv("WORKFLOW_ONESHOT_PROFILE", prof)
        m = Seq(("emit_changes", {"message": "fixed"}))
        monkeypatch.setattr("src.engine.llm_provider.call_llm", m)
        await ob.fix_pass(b, errs, timer=ob.Timer())
        sent = m.calls[0]["user"]
        assert '<file path="views/Reports.tsx">' in sent and '<file path="views/Unrelated.tsx">' not in sent
        assert "views/Unrelated.tsx (" in sent and len(sent) < len(ob.bundle_text(b))


async def test_plain_text_models_can_read_and_edit(monkeypatch):
    monkeypatch.setenv("WORKFLOW_ONESHOT_MODEL", "gemma-test")
    b = app_bundle()
    replies = [json.dumps({"read_files": ["lib/chart.ts"]}),
               json.dumps({"app": {"files": [{"path": "lib/chart.ts", "edits": [{"old": "Chart helper.", "new": "Charts."}]}]}})]
    seen: list[str] = []

    async def fake(model, messages, temperature=0.2, tools=None, **kw):
        seen.append(messages[-1]["content"])
        return LLMResponse(text=replies.pop(0))

    monkeypatch.setattr("src.engine.llm_provider.call_llm", fake)
    raw = await ob.ask_changes("req", ["views/Reports.tsx"], b, phase="edit", timer=ob.Timer())
    assert '<file path="lib/chart.ts">' in seen[1] and raw["app"]["files"][0]["content"].startswith("/** Charts. */")


def test_extension_points_append_sections():
    ob.CONTEXT_SECTIONS.append(lambda plan, bundle: "## Database\nschema here")
    ob.BRIEF_SECTIONS.append(lambda: "## Extra\nbrief extra")
    try:
        assert "schema here" in ob._context({"title": "x"}, None, None, None)
        assert "schema here" in ob.edit_prompt(app_bundle(), "tweak")[0]
        assert ob.brief().rstrip().endswith("brief extra")
    finally:
        ob.CONTEXT_SECTIONS.pop()
        ob.BRIEF_SECTIONS.pop()
