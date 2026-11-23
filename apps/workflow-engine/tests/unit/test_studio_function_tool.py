"""Agent surface of nodes-as-functions (no LLM needed): tool config + token minting."""

from __future__ import annotations

import pytest

from src.engine.node_registry import register_all_nodes
from src.nodes.ai.inline_config import resolve_tools


def test_studio_function_tool_resolves_from_catalogue():
    register_all_nodes()
    tools, executors = resolve_tools([{"type": "studioFunction", "parameters": {"function": "http_request"}}])
    assert [t["name"] for t in tools] == ["studio_http_request"]
    schema = tools[0]["input_schema"]
    assert "url" in schema["properties"]["parameters"]["properties"]
    assert "studio_http_request" in executors


def test_unknown_function_is_skipped():
    register_all_nodes()
    tools, _ = resolve_tools([{"type": "studioFunction", "parameters": {"function": "webhook"}}])
    assert tools == []


@pytest.mark.anyio
async def test_executor_without_context_errors():
    from src.nodes.ai.tools.studio_function_tool import run_studio_function
    assert (await run_studio_function("Set", {"parameters": {}}, None))["error"]


@pytest.fixture
def anyio_backend():
    return "asyncio"
