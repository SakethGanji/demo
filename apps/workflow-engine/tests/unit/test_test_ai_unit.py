"""Stage 3B AI test helpers (CONTRACTS-STAGE3B §1, §2, §3): adapter parsing, sanitising, never-weaken, repair, variations.
The model is replaced by scripted replies; no real LLM is called."""

from __future__ import annotations

import copy
import json
import re
from types import SimpleNamespace
from typing import Any

import pytest

from src.engine import llm_provider, test_ai
from src.engine.test_ai import TestAIError, ask_json, parse_json_object
from src.services import test_ai_service as svc

pytestmark = pytest.mark.anyio

DEFINITION: dict[str, Any] = {
    "name": "Refund triage",
    "nodes": [
        {"name": "Webhook", "type": "Webhook", "parameters": {}},
        {"name": "Amount over 500", "type": "If", "parameters": {"condition": "{{ $json.amount > 500 }}"}},
        {"name": "Risk API", "type": "HttpRequest", "parameters": {"method": "POST", "url": "http://x.test/"}},
        {"name": "Log", "type": "DataTable", "parameters": {"table": "refunds"}},
    ],
    "connections": [{"source_node": "Webhook", "target_node": "Amount over 500", "source_output": "main", "target_input": "main"}],
    "tests": [{
        "id": "t_a0000001", "name": "locked one", "target": {"kind": "workflow"}, "input": {"items": [{"amount": 1}]},
        "mocks": {}, "expect": [{"node": "Log", "op": "status", "value": "success"}], "category": "normal",
        "status": "approved", "locked": True, "approved_by": "sam", "approved_at": "2026-01-01T00:00:00+00:00", "source": "human",
    }],
    "test_defaults": {"mocks": {"Log": {"items": [{"id": 1}]}}},
}
GOOD = {
    "name": "Risk API down", "target": {"kind": "node", "node": "Risk API"}, "input": {"items": [{"amount": 900}]},
    "mocks": {"Risk API": {"error": {"message": "503"}}}, "expect": [{"node": "Risk API", "op": "status", "value": "error"}],
    "category": "ext_error",
}


def reply_with(*replies: Any):
    """A fake ``ask_json`` that returns the given replies in order and records the prompts."""
    calls: list[dict[str, str]] = []
    queue = list(replies)

    async def fake(task, system, user, *, model=None):
        calls.append({"task": task, "system": system, "user": user})
        nxt = queue.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt

    fake.calls = calls  # type: ignore[attr-defined]
    return fake


def patch_service(monkeypatch, definition: dict[str, Any], ask, share: bool = False) -> list[dict[str, Any]]:
    """Run ``draft_tests`` without a database: the load and the dry run are replaced.

    ``share=True`` hands the service the caller's own object (no copy), so a mutation inside it shows up in the test.
    """
    stored = SimpleNamespace(id="wf_1")

    async def fake_load(repo, workflow_id, override):
        d = (override if override is not None else definition) if share else copy.deepcopy(override if override is not None else definition)
        return stored, d, {"Webhook": {"outputs": {}}}, {t["id"] for t in definition["tests"]}

    async def fake_dry(repo, stored_, d, drafts):
        return [{**t, "ai_run": {"status": "passed", "failures": []}} for t in drafts]

    monkeypatch.setattr(svc, "_load", fake_load)
    monkeypatch.setattr(svc, "_dry_run", fake_dry)
    monkeypatch.setattr(svc, "ask_json", ask)
    return []


# ── adapter ──────────────────────────────────────────────────────────────────────────────────


def test_parse_json_object_variants():
    assert parse_json_object('{"a": 1}') == {"a": 1}
    assert parse_json_object('```json\n{"tests": []}\n```') == {"tests": []}
    assert parse_json_object('Sure! Here you go: {"a": {"b": 2}} hope it helps') == {"a": {"b": 2}}
    assert parse_json_object('[1] then {"ok": true}') == {"ok": True}
    for bad in ("", None, "no json here", "[1, 2]", "{broken"):
        with pytest.raises(TestAIError) as e:
            parse_json_object(bad)
        assert e.value.code == "bad_json"


async def test_ask_json_calls_provider_with_the_contract_settings(monkeypatch):
    seen: dict[str, Any] = {}

    async def fake_call(model, messages, temperature=0.2, tools=None, **kw):
        seen.update(model=model, messages=messages, temperature=temperature, kw=kw)
        return llm_provider.LLMResponse(text='```json\n{"tests": []}\n```')

    monkeypatch.setattr(llm_provider, "is_configured", lambda m: True)
    monkeypatch.setattr(llm_provider, "call_llm", fake_call)
    assert await ask_json("draft_tests", "TEST_AI:DRAFT_TESTS\nrules", "{}") == {"tests": []}
    assert seen["temperature"] == 0.2 and seen["kw"]["response_format"] == {"type": "json_object"}
    assert seen["model"] == test_ai.TEST_AI_MODEL
    assert seen["messages"][0]["role"] == "system" and seen["messages"][0]["content"].startswith("TEST_AI:DRAFT_TESTS")


async def test_ask_json_errors(monkeypatch):
    monkeypatch.setattr(llm_provider, "is_configured", lambda m: False)
    with pytest.raises(TestAIError) as e:
        await ask_json("variations", "TEST_AI:VARIATIONS\nx", "{}")
    assert e.value.code == "not_configured"

    monkeypatch.setattr(llm_provider, "is_configured", lambda m: True)

    async def text(model, messages, temperature=0.2, tools=None, **kw):
        return llm_provider.LLMResponse(text="not json")

    async def boom(model, messages, temperature=0.2, tools=None, **kw):
        raise RuntimeError("quota")

    monkeypatch.setattr(llm_provider, "call_llm", text)
    with pytest.raises(TestAIError) as e:
        await ask_json("variations", "TEST_AI:VARIATIONS\nx", "{}")
    assert e.value.code == "bad_json"
    monkeypatch.setattr(llm_provider, "call_llm", boom)
    with pytest.raises(TestAIError) as e:
        await ask_json("variations", "TEST_AI:VARIATIONS\nx", "{}")
    assert e.value.code == "provider_failed" and "quota" not in e.value.message
    with pytest.raises(ValueError):  # the first line is fixed per task
        await ask_json("variations", "something else", "{}")


# ── sanitising ───────────────────────────────────────────────────────────────────────────────


def test_sanitize_rebuilds_every_test():
    taken = {"t_a0000001"}
    evil = {**GOOD, "id": "t_a0000001", "status": "approved", "locked": True, "approved_by": "me",
            "approved_at": "2026-02-02T00:00:00+00:00", "source": "human"}
    accepted, rejected = svc.sanitize_tests([evil, GOOD], DEFINITION, taken)
    assert rejected == [] and len(accepted) == 2
    ids = [t["id"] for t in accepted]
    assert "t_a0000001" not in ids and len(set(ids)) == 2 and all(re.fullmatch(r"t_[0-9a-f]{8}", i) for i in ids)
    for t in accepted:
        assert t["status"] == "draft" and t["locked"] is False and t["source"] == "ai"
        assert "approved_by" not in t and "approved_at" not in t
    assert set(ids) <= taken


@pytest.mark.parametrize("mutate,needle", [
    (lambda t: t.update(target={"kind": "node", "node": "Nope"}), "doesn't exist"),
    (lambda t: t.update(mocks={"Nope": {"items": [{"a": 1}]}}), "doesn't exist"),
    (lambda t: t.update(expect=[{"node": "Nope", "op": "status", "value": "error"}]), "doesn't exist"),
    (lambda t: t.update(expect=[{"node": "Risk API", "op": "explode", "value": 1}]), "unknown assertion op"),
    (lambda t: t.update(expect=[{"node": "Risk API", "op": "status", "value": "weird"}]), "status value"),
    (lambda t: t.update(expect=[]), "no assertions"),
    (lambda t: t.update(category="mystery"), "invalid test"),
    (lambda t: t.update(name=""), "invalid test"),
    (lambda t: t.update(mocks={"Risk API": {"nonsense": 1}}), "must have one of"),
    (lambda t: t.update(mocks={"Log": {"http": {"status": 200}}}), "HttpRequest nodes only"),
    (lambda t: t.update(input={"items": ["x"]}), "list of objects"),
    (lambda t: t.update(covers=["Ghost"]), "doesn't exist"),
])
def test_sanitize_rejects_with_a_reason(mutate, needle):
    bad = copy.deepcopy(GOOD)
    mutate(bad)
    accepted, rejected = svc.sanitize_tests([bad], DEFINITION, set())
    assert accepted == [] and len(rejected) == 1
    assert needle in rejected[0]["reason"] and rejected[0]["test"] == bad


def test_sanitize_odd_shapes():
    assert svc.sanitize_tests("nope", DEFINITION, set()) == ([], [{"reason": "'tests' is not a list"}])
    acc, rej = svc.sanitize_tests([42, GOOD], DEFINITION, set())
    assert len(acc) == 1 and rej[0]["reason"] == "a test is not an object"


# ── draft_tests (model + dry run replaced) ───────────────────────────────────────────────────


async def test_draft_never_touches_existing_tests(monkeypatch):
    """A reply that reuses a locked test's id, flips locked/approved, or 'edits' it still yields only new drafts."""
    before = copy.deepcopy(DEFINITION)
    reuse = {**GOOD, "id": "t_a0000001", "name": "locked one", "locked": True, "status": "approved", "approved_by": "x"}
    edit = {"id": "t_a0000001", "name": "locked one", "target": {"kind": "workflow"}, "input": {"items": [{"amount": 2}]},
            "expect": [{"node": "Log", "op": "status", "value": "error"}], "category": "normal", "locked": False, "status": "draft"}
    ask = reply_with({"tests": [reuse, edit]})
    patch_service(monkeypatch, DEFINITION, ask, share=True)  # the service works on the very object we compare afterwards
    out = await svc.draft_tests(None, "wf_1", None, [{"node": "Risk API", "category": "ext_error"}], 8)
    assert DEFINITION == before and json.dumps(DEFINITION, sort_keys=True) == json.dumps(before, sort_keys=True)
    assert len(out["drafts"]) == 2 and out["rejected"] == []
    for d in out["drafts"]:
        assert d["id"] != "t_a0000001" and d["status"] == "draft" and d["locked"] is False and d["source"] == "ai"
        assert "approved_by" not in d and d["ai_run"]["status"] == "passed"
    assert out["cells"] == [{"node": "Risk API", "category": "ext_error"}] and out["model"] == test_ai.TEST_AI_MODEL


async def test_draft_prompt_is_structured_data(monkeypatch):
    ask = reply_with({"tests": [GOOD]})
    patch_service(monkeypatch, DEFINITION, ask)
    await svc.draft_tests(None, "wf_1", None, [{"node": "Risk API", "category": "ext_error"}], 5)
    call = ask.calls[0]
    assert call["task"] == "draft_tests"
    user = json.loads(call["user"])
    assert {"definition", "shapes_by_node", "node_catalog", "test_defaults", "existing_tests", "target_cells", "grammar"} <= set(user)
    assert [n["name"] for n in user["definition"]["nodes"]] == ["Webhook", "Amount over 500", "Risk API", "Log"]
    assert {e["type"] for e in user["node_catalog"]} == {"Webhook", "If", "HttpRequest", "DataTable"}
    assert user["existing_tests"] == [{"id": "t_a0000001", "name": "locked one", "category": "normal",
                                       "target": {"kind": "workflow"}, "covers": ["Log"]}]
    assert user["test_defaults"] == {"mocks": DEFINITION["test_defaults"]["mocks"]}
    assert user["target_cells"] == [{"node": "Risk API", "category": "ext_error"}] and user["max_tests"] == 5
    g = user["grammar"]
    assert set(g["Assertion"]["shape"]["op"]) == set(svc.ASSERTION_OPS) and len(svc.ASSERTION_OPS) == 16
    assert g["TestCase"]["category"] == list(svc.TEST_CATEGORIES) and g["worked_example"]["tests"]
    # the grammar's own worked example is accepted by the sanitiser once it is adapted to the workflow's nodes
    assert re.match(r"TEST_AI:DRAFT_TESTS\n", svc._DRAFT_SYSTEM) and svc._VARIATIONS_SYSTEM.startswith("TEST_AI:VARIATIONS\n")
    assert "{\"tests\"" in svc._DRAFT_SYSTEM


async def test_worked_example_passes_the_validator():
    d = {"nodes": [{"name": n, "type": t} for n, t in
                   [("Amount over 500", "If"), ("Risk API", "HttpRequest")]]}
    acc, rej = svc.sanitize_tests(svc.GRAMMAR["worked_example"]["tests"], d, set())
    assert len(acc) == 2 and rej == []


async def test_repair_round_fixes_invalid_tests(monkeypatch):
    bad = {**GOOD, "expect": [{"node": "Risk API", "op": "explode", "value": 1}]}
    ask = reply_with({"tests": [bad]}, {"tests": [GOOD]})
    patch_service(monkeypatch, DEFINITION, ask)
    out = await svc.draft_tests(None, "wf_1", None, [{"node": "Risk API", "category": "ext_error"}], 8)
    assert len(ask.calls) == 2 and len(out["drafts"]) == 1 and out["rejected"] == []
    repair = json.loads(ask.calls[1]["user"])["repair"]
    assert "unknown assertion op" in repair["rejected"][0]["reason"] and repair["rejected"][0]["test"] == bad


async def test_repair_is_only_one_round_and_reports_leftovers(monkeypatch):
    bad = {**GOOD, "category": "nope"}
    ask = reply_with({"tests": [bad]}, {"tests": [bad]})
    patch_service(monkeypatch, DEFINITION, ask)
    out = await svc.draft_tests(None, "wf_1", None, [{"node": "Risk API", "category": "ext_error"}], 8)
    assert len(ask.calls) == 2 and out["drafts"] == [] and len(out["rejected"]) == 1
    assert "invalid test" in out["rejected"][0]["reason"]


async def test_repair_failure_keeps_first_round_rejections(monkeypatch):
    bad = {**GOOD, "category": "nope"}
    ask = reply_with({"tests": [GOOD, bad]}, TestAIError("provider_failed", "x"))
    patch_service(monkeypatch, DEFINITION, ask)
    out = await svc.draft_tests(None, "wf_1", None, [{"node": "Risk API", "category": "ext_error"}], 8)
    assert len(out["drafts"]) == 1 and len(out["rejected"]) == 1


async def test_max_limits_drafts_and_cells(monkeypatch):
    ask = reply_with({"tests": [GOOD, GOOD, GOOD]})
    patch_service(monkeypatch, DEFINITION, ask)
    cells = [{"node": "Risk API", "category": "ext_error"}, {"node": "Log", "category": "normal"}]
    out = await svc.draft_tests(None, "wf_1", None, cells, 2)
    assert len(out["cells"]) == 2 and len(out["drafts"]) == 2
    assert any("max_tests" in r["reason"] for r in out["rejected"])


async def test_bad_cells_are_refused(monkeypatch):
    patch_service(monkeypatch, DEFINITION, reply_with())
    for cell in ({"node": "Nope", "category": "normal"}, {"node": "Log", "category": "weird"}):
        with pytest.raises(svc.AuthoringError) as e:
            await svc.draft_tests(None, "wf_1", None, [cell], 3)
        assert e.value.status == 422


async def test_no_cells_means_no_model_call(monkeypatch):
    ask = reply_with()
    patch_service(monkeypatch, DEFINITION, ask)

    async def none(*a, **k):
        return []

    monkeypatch.setattr(svc, "_gap_cells", none)
    out = await svc.draft_tests(None, "wf_1", None, None, 4)
    assert out["drafts"] == [] and out["cells"] == [] and ask.calls == []


async def test_model_errors_propagate(monkeypatch):
    patch_service(monkeypatch, DEFINITION, reply_with(TestAIError("not_configured", "no key")))
    with pytest.raises(TestAIError) as e:
        await svc.draft_tests(None, "wf_1", None, [{"node": "Log", "category": "normal"}], 3)
    assert e.value.code == "not_configured"


# ── variations ───────────────────────────────────────────────────────────────────────────────


async def test_propose_variations_shapes(monkeypatch):
    ask = reply_with({"variations": [
        {"items": [{"amount": "900"}]}, {"items": [{"amount": None}, {"amount": -1}]}, [{"amount": 0}], {"amount": 1e9},
        {"items": []}, "junk", {"items": ["x"]},
    ]})
    monkeypatch.setattr(svc, "ask_json", ask)
    out = await svc.propose_variations(DEFINITION, {"Webhook": {}}, [{"amount": 10}], n=3)
    assert out == [[{"amount": "900"}], [{"amount": None}, {"amount": -1}], [{"amount": 0}]]
    call = ask.calls[0]
    assert call["task"] == "variations" and call["system"].startswith("TEST_AI:VARIATIONS")
    assert json.loads(call["user"])["base_items"] == [{"amount": 10}] and json.loads(call["user"])["count"] == 3


async def test_propose_variations_accepts_alternate_key_and_bad_reply(monkeypatch):
    monkeypatch.setattr(svc, "ask_json", reply_with({"inputs": [{"items": [{"a": 1}]}]}, {"nothing": 1}))
    assert await svc.propose_variations(DEFINITION, {}, [{"a": 0}]) == [[{"a": 1}]]
    assert await svc.propose_variations(DEFINITION, {}, [{"a": 0}]) == []
