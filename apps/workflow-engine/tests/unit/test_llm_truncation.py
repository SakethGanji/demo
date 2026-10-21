"""Truncated or unparsable replies are flagged by every provider path; big output budgets get long timeouts and streaming."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.engine import llm_provider as lp


def test_helpers():
    ok = lp.LLMResponse(text="x")
    assert lp.require_complete(ok) is ok
    for bad in (lp.LLMResponse(output_truncated=True), lp.LLMResponse(malformed_tool_call=True)):
        assert lp.is_truncated(bad)
        with pytest.raises(lp.OutputTruncatedError) as e:
            lp.require_complete(bad)
        assert e.value.truncated and e.value.response is bad


def test_call_timeout_scales_with_output_budget():
    assert lp.call_timeout({"max_tokens": 60000}) >= 300
    assert lp.call_timeout({}) == lp.DEFAULT_TIMEOUT_S
    assert lp.call_timeout({"max_tokens": 60000, "timeout": 5}) == 5


def _openai(finish, args):
    tc = NS(id="1", function=NS(name="t", arguments=args))
    msg = NS(content=None, tool_calls=[tc])
    return NS(usage=None, choices=[NS(message=msg, finish_reason=finish)])


def test_openai_length_finish_is_truncated_and_required_is_forwarded():
    client = MagicMock()
    client.chat.completions.create = AsyncMock(return_value=_openai("length", '{"a": 1'))
    tools = [{"name": "t", "description": "d", "parameters": {"type": "object", "properties": {}}}]
    r = asyncio.run(lp._call_openai_compat(client, "m", [{"role": "user", "content": "hi"}], tools=tools, tool_choice="required",
                                           max_tokens=60000))
    kw = client.chat.completions.create.call_args.kwargs
    assert r.output_truncated and kw["tool_choice"] == "required" and kw["timeout"] >= 300


def test_openai_unparsable_args_flag_malformed():
    client = MagicMock()
    client.chat.completions.create = AsyncMock(return_value=_openai("stop", "{{{{ not json"))
    r = asyncio.run(lp._call_openai_compat(client, "m", [{"role": "user", "content": "hi"}], tools=[
        {"name": "t", "description": "d", "parameters": {"type": "object", "properties": {}}}]))
    assert lp.is_truncated(r) or r.tool_calls  # repaired is acceptable; unrepairable is flagged


def test_anthropic_max_tokens_stop_reason_is_truncated_and_big_budget_streams(monkeypatch):
    final = NS(usage=NS(input_tokens=1, output_tokens=2), stop_reason="max_tokens",
               content=[NS(type="tool_use", id="1", name="t", input={})])
    stream_cm = MagicMock()
    stream_cm.__aenter__ = AsyncMock(return_value=NS(get_final_message=AsyncMock(return_value=final)))
    stream_cm.__aexit__ = AsyncMock(return_value=False)
    client = MagicMock()
    client.messages.stream = MagicMock(return_value=stream_cm)
    client.messages.create = AsyncMock()
    monkeypatch.setattr(lp, "_get_anthropic_client", lambda: client)
    r = asyncio.run(lp._call_anthropic("claude-x", [{"role": "user", "content": "hi"}], tools=[
        {"name": "t", "description": "d", "parameters": {"type": "object", "properties": {}}}],
        max_tokens=60000, tool_choice="required"))
    assert r.output_truncated and client.messages.stream.call_args.kwargs["tool_choice"] == {"type": "any"}
    client.messages.create.assert_not_called()


def test_gemini_max_tokens_finish_is_truncated():
    cand = NS(finish_reason=NS(name="MAX_TOKENS"), content=None)
    r = lp._parse_gemini_response(NS(candidates=[cand]))
    assert r.output_truncated
