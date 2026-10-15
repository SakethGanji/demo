"""UI-DESIGN §10.3 (review E7): adapter contract tests with recorded Anthropic / OpenAI request+response
fixtures, independent of the fake LLM. Tools are given as a dict and as a callable; the responses carry
multiple tool calls, and the OpenAI one has malformed tool-call arguments (one repairable, one not)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from src.engine import fake_llm, llm_provider as lp

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "llm"

EMIT = {"name": "emit_module", "description": "Emit the module",
        "parameters": {"type": "object", "required": ["module_name", "source"], "properties": {
            "module_name": {"type": "string"}, "source": {"type": "string"},
            "tables": {"type": "array", "items": {"type": "object", "properties": {"name": {"type": "string"}}}}}}}


def write_file(path: str, size: int = 0) -> str:
    """Write files to the app.

    Args:
        path: the file path
        size: expected size in bytes
    """
    return "ok"


MSGS = [{"role": "system", "content": "You write modules."}, {"role": "user", "content": "Make refunds"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "call_1", "type": "function", "function": {
            "name": "emit_module", "arguments": "{\"module_name\": \"a\", \"source\": \"x\"}"}}]},
        {"role": "tool", "tool_call_id": "call_1", "content": "ok"}, {"role": "user", "content": "Fix these problems"}]


def _ns(obj, key=None):
    """Recorded JSON → SDK-like attribute objects (tool_use ``input`` stays a dict, as in the SDK)."""
    if key == "input":
        return obj
    if isinstance(obj, dict):
        return NS(**{k: _ns(v, k) for k, v in obj.items()})
    if isinstance(obj, list):
        return [_ns(v) for v in obj]
    return obj


@pytest.fixture(autouse=True)
def no_fake():
    fake_llm.uninstall()
    saved = dict(lp._clients)
    yield
    lp._clients.clear()
    lp._clients.update(saved)


def test_anthropic_request_and_response_contract():
    rec = json.loads((FIX / "anthropic_tools.json").read_text())
    sent = {}

    class Messages:
        async def create(self, **kw):
            sent.update(kw)
            return _ns(rec["response"])
    lp._clients["anthropic"] = NS(messages=Messages())
    r = asyncio.run(lp.call_llm("claude-sonnet-4-6", MSGS, 0.2, tools=[EMIT, write_file], max_tokens=8000))
    assert json.loads(json.dumps(sent)) == rec["request"]
    assert r.text == "Here is the module."
    assert [(t.id, t.name, t.args) for t in r.tool_calls] == [
        ("toolu_01", "emit_module", {"module_name": "refunds", "source": "print(1)"}),
        ("toolu_02", "write_file", {"path": "App.tsx", "size": 10})]
    assert (r.usage.input_tokens, r.usage.output_tokens, r.usage.total_tokens) == (812, 96, 908)
    assert r.malformed_tool_call is False
    # the adapter sends exactly what the shared normaliser produces (what the fake logs)
    assert [{"name": t["name"], "description": t["description"], "input_schema": t["parameters"]}
            for t in lp._normalize_tools([EMIT, write_file], "anthropic")] == rec["request"]["tools"]


class _StubJsonRepair:
    """Deterministic stand-in for the optional ``json-repair`` package (not in requirements.txt and not
    installed in this venv): repairs single quotes and trailing commas, fails on anything else."""

    @staticmethod
    def loads(raw):
        import ast
        import re
        try:
            return ast.literal_eval(re.sub(r",\s*([}\]])", r"\1", raw))
        except (ValueError, SyntaxError):
            return ""


def test_openai_malformed_args_without_json_repair_are_flagged(monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, "json_repair", None)  # import fails → no repair
    rec = json.loads((FIX / "openai_tools.json").read_text())

    class Completions:
        async def create(self, **kw):
            return _ns(rec["response"])
    lp._clients["openai"] = NS(chat=NS(completions=Completions()))
    r = asyncio.run(lp.call_llm("gpt-4o", MSGS, 0.2, tools=[EMIT, write_file], max_tokens=8000))
    assert r.malformed_tool_call is True
    assert [(t.args, t.raw_args) for t in r.tool_calls[1:]] == [({}, "{'path': 'App.tsx', 'size': 10,}"), ({}, "{{{ not json")]


def test_openai_request_and_response_contract_with_malformed_tool_calls(monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, "json_repair", _StubJsonRepair)
    rec = json.loads((FIX / "openai_tools.json").read_text())
    sent = {}

    class Completions:
        async def create(self, **kw):
            sent.update(kw)
            return _ns(rec["response"])
    client = NS(chat=NS(completions=Completions()))
    lp._clients["openai"] = client
    r = asyncio.run(lp.call_llm("gpt-4o", MSGS, 0.2, tools=[EMIT, write_file], max_tokens=8000))
    assert json.loads(json.dumps(sent)) == rec["request"]
    assert [(t.id, t.name) for t in r.tool_calls] == [("call_a", "emit_module"), ("call_b", "write_file"), ("call_c", "write_file")]
    assert r.tool_calls[0].args == {"module_name": "refunds", "source": "print(1)", "tables": []}
    assert r.tool_calls[1].args == {"path": "App.tsx", "size": 10}     # repaired (single quotes, trailing comma)
    assert r.tool_calls[1].raw_args is None
    assert r.tool_calls[2].args == {} and r.tool_calls[2].raw_args == "{{{ not json"   # unrepairable
    assert r.malformed_tool_call is True
    assert (r.usage.input_tokens, r.usage.output_tokens, r.usage.total_tokens) == (700, 80, 780)
    assert [t["function"]["parameters"] for t in rec["request"]["tools"]] == \
        [t["parameters"] for t in lp._normalize_tools([EMIT, write_file], "openai")]


def test_assistant_message_round_trip_matches_openai_shape():
    r = lp.LLMResponse(text=None, tool_calls=[lp.ToolCall(id="c1", name="emit_module", args={"a": 1}),
                                             lp.ToolCall(id="c2", name="emit_module", args={"b": 2})])
    m = r.get_assistant_message()
    assert m["role"] == "assistant" and [tc["id"] for tc in m["tool_calls"]] == ["c1", "c2"]
    assert json.loads(m["tool_calls"][1]["function"]["arguments"]) == {"b": 2}
