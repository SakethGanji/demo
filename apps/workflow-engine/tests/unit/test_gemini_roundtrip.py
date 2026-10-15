"""Gemini tool history must preserve provider-signed content across agent turns."""
import json
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from google.genai import types
from google.genai.errors import ClientError, ServerError

from src.engine.llm_provider import _call_openai_compat, _convert_messages_to_gemini_content, _is_retryable, _parse_gemini_response
from src.engine import llm_provider


def test_signed_parallel_tool_calls_roundtrip_without_losing_native_parts():
    first = types.Part.from_function_call(name="read_files", args={"paths": ["App.tsx"]})
    first.thought_signature = b"\x00\x01opaque-signed-part\xff"
    second = types.Part.from_function_call(name="get_project_summary", args={})
    content = types.Content(role="model", parts=[types.Part(text="Inspecting the app"), first, second])
    response = types.GenerateContentResponse(candidates=[types.Candidate(content=content, finish_reason="STOP")])
    parsed = _parse_gemini_response(response)
    assert [c.name for c in parsed.tool_calls] == ["read_files", "get_project_summary"]
    # The agent stores generic messages; JSON persistence must not corrupt bytes.
    message = json.loads(json.dumps(parsed.get_assistant_message()))
    converted, _ = _convert_messages_to_gemini_content([message, {
        "role": "tool", "tool_call_id": parsed.tool_calls[0].id,
        "name": "read_files", "content": '{"files": []}',
    }])
    assert converted[0].model_dump() == content.model_dump()
    assert converted[0].parts[1].thought_signature == first.thought_signature
    assert converted[1].parts[0].function_response.name == "read_files"


@pytest.mark.parametrize("code", [429, 500, 503])
def test_google_sdk_transient_errors_are_retryable(code):
    error_type = ServerError if code >= 500 else ClientError
    assert _is_retryable(error_type(code, {"error": {"code": code, "message": "temporary"}}))


def test_google_sdk_invalid_request_is_not_retried():
    assert not _is_retryable(ClientError(400, {"error": {"code": 400, "message": "invalid request"}}))


def test_native_metadata_does_not_leak_into_openai_requests():
    create = AsyncMock(return_value=SimpleNamespace(usage=None, choices=[]))
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    message = {"role": "assistant", "content": "done", "_gemini_content": {"role": "model", "parts": []}}
    asyncio.run(_call_openai_compat(client, "gpt-4o", [message], 0.2))
    assert create.call_args.kwargs["messages"] == [{"role": "assistant", "content": "done"}]
    assert "_gemini_content" in message  # preserving history for a return to Gemini


def quota_error(seconds):
    return ClientError(429, {"error": {"code": 429, "message": "quota", "details": [
        {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": f"{seconds}s"},
    ]}})


def test_retry_waits_for_google_quota_window(monkeypatch):
    call = AsyncMock(side_effect=[quota_error(30), llm_provider.LLMResponse(text="ok")])
    sleep = AsyncMock()
    monkeypatch.setattr(llm_provider, "_call_llm_once", call)
    monkeypatch.setattr(llm_provider.asyncio, "sleep", sleep)
    result = asyncio.run(llm_provider.call_llm("gemini-2.5-flash", [{"role": "user", "content": "hi"}]))
    assert result.text == "ok"
    sleep.assert_awaited_once_with(30.25)
    assert call.await_count == 2


def test_daily_quota_does_not_retry_or_wait_for_hours(monkeypatch):
    call = AsyncMock(side_effect=quota_error(3600))
    sleep = AsyncMock()
    monkeypatch.setattr(llm_provider, "_call_llm_once", call)
    monkeypatch.setattr(llm_provider.asyncio, "sleep", sleep)
    with pytest.raises(ClientError):
        asyncio.run(llm_provider.call_llm("gemini-2.5-flash", [{"role": "user", "content": "hi"}]))
    assert call.await_count == 1
    sleep.assert_not_awaited()


def test_same_model_errors_stop_instead_of_resetting_on_escalation(monkeypatch):
    from src.services import app_builder_ai_service as builder

    call = AsyncMock(return_value=llm_provider.LLMResponse(malformed_tool_call=True))
    monkeypatch.setattr(builder, "call_llm", call)
    monkeypatch.setattr(builder, "_HEAVY_MODEL", "gemini-3.5-flash")
    monkeypatch.setattr(builder, "_LIGHT_MODEL", "gemini-3.5-flash")
    service = builder.AppBuilderAIService.__new__(builder.AppBuilderAIService)
    monkeypatch.setattr(service, "_build_agent_system_prompt", lambda _: "Build UI")

    async def collect():
        return [event async for event in service._run_agent_loop([], [], "Build UI")]

    events = asyncio.run(collect())
    assert call.await_count == builder.MAX_CONSECUTIVE_ERRORS
    payloads = [json.loads(event["data"]) for event in events]
    assert not any(p.get("phase") == "escalating" for p in payloads)
    assert "trouble generating" in payloads[-1]["content"]
