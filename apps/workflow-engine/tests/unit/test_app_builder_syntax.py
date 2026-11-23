"""Actual compiler feedback must prevent a broken generated app from completing."""
import asyncio
import shutil
from unittest.mock import AsyncMock

import pytest

from src.engine.llm_provider import LLMResponse, ToolCall
from src.services import app_builder_ai_service as builder

pytestmark = pytest.mark.skipif(shutil.which('esbuild') is None, reason='esbuild required')


def test_compiler_rejects_mismatched_jsx_without_materializing_paths():
    files = [{'path': '../../untrusted/App.tsx', 'content': 'export default function App(){return <div></main>}'}]
    error = asyncio.run(builder._generated_syntax_errors(files))
    assert error and 'closing' in error


def test_agent_repairs_compiler_error_before_completing(monkeypatch):
    bad = 'export default function App(){return <div></main>}'
    good = 'export default function App(){return <div>Ready</div>}'
    def write(content):
        return LLMResponse(tool_calls=[ToolCall(id=content, name='write_files', args={'files': [{'path': 'App.tsx', 'content': content}]})])
    call = AsyncMock(side_effect=[write(bad), LLMResponse(text='Done'), write(good), LLMResponse(text='Fixed')])
    monkeypatch.setattr(builder, 'call_llm', call)
    service = builder.AppBuilderAIService.__new__(builder.AppBuilderAIService)
    monkeypatch.setattr(service, '_build_agent_system_prompt', lambda _: 'Build UI')
    files = []
    async def collect():
        return [event async for event in service._run_agent_loop(files, [], 'Build UI')]
    events = asyncio.run(collect())
    assert files[0]['content'] == good
    assert call.await_count == 4
    assert any('Repairing generated code syntax' in event['data'] for event in events)
    assert any('failed syntax validation' in (m.get('content') or '') for m in call.call_args.kwargs['messages'])


def test_exhausted_syntax_repairs_emit_error_and_done_without_code(monkeypatch):
    import json
    from src.schemas.app_builder import AppBuilderChatRequest
    bad = 'export default function App(){return <div></main>}'
    write = LLMResponse(tool_calls=[ToolCall(id='write', name='write_files', args={'files': [{'path': 'App.tsx', 'content': bad}]})])
    call = AsyncMock(side_effect=[write, LLMResponse(text='Done'), LLMResponse(text='Done'), LLMResponse(text='Done')])
    monkeypatch.setattr(builder, 'call_llm', call)
    service = builder.AppBuilderAIService.__new__(builder.AppBuilderAIService)
    monkeypatch.setattr(service, '_build_agent_system_prompt', lambda _: 'Build UI')
    monkeypatch.setattr(service, '_resolve_current_files', AsyncMock(return_value=([], None)))
    monkeypatch.setattr(service, '_load_project_api', AsyncMock())
    async def collect():
        return [json.loads(e['data']) async for e in service.stream_chat(AppBuilderChatRequest(message='Build UI'))]
    events = asyncio.run(collect())
    assert not any(e['type'] == 'code' for e in events)
    assert events[-2]['type'] == 'error'
    assert 'after two repairs' in events[-2]['message']
    assert events[-1]['type'] == 'done'
    assert call.await_count == 4
