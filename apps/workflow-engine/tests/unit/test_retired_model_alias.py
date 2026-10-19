"""Retired Gemini names keep working via an alias; node defaults are the current model."""

import asyncio
import logging

from src.engine import llm_provider as lp
from src.nodes.ai.llm_chat import LLMChatNode
from src.nodes.ai.ai_agent import AIAgentNode

CURRENT = "gemini-3.7-flash"


def test_alias_maps_retired_names(caplog):
    with caplog.at_level(logging.INFO, logger=lp.logger.name):
        assert lp.resolve_model_alias("gemini-2.0-flash") == CURRENT
        assert lp.resolve_model_alias("gemini-2.0-flash-001") == CURRENT
    assert sum("retired" in r.message for r in caplog.records) == 2
    assert lp.resolve_model_alias("gpt-4o") == "gpt-4o"
    assert lp.resolve_model_alias(CURRENT) == CURRENT


def test_retired_name_reaches_provider_as_current(monkeypatch):
    seen = {}

    async def fake(model, *a, **k):
        seen["model"] = model
        return "ok"

    monkeypatch.setattr(lp, "_call_gemini_vertex", fake)
    assert asyncio.run(lp._call_llm_once("gemini-2.0-flash", [])) == "ok"
    assert seen["model"] == CURRENT


def test_is_configured_accepts_retired_name():
    assert lp.is_configured("gemini-2.0-flash") == lp.is_configured(CURRENT)


def _defaults(cls):
    return {p.name: p.default for p in cls.node_description.properties if p.name in ("model", "memorySummaryModel")}


def test_node_defaults_are_current_model():
    assert _defaults(LLMChatNode)["model"] == CURRENT
    d = _defaults(AIAgentNode)
    assert d and all(v == CURRENT for v in d.values())


def test_llm_chat_without_a_model_calls_its_default_not_the_mock(monkeypatch):
    """A workflow saved through the API or the AI builder omits `model`; it must reach the real default, not mock mode."""
    from datetime import datetime

    from src.engine.types import ExecutionContext, NodeData, NodeDefinition, Workflow

    seen = {}

    async def fake(self, model, *a, **k):
        seen["model"] = model
        return {"response": "ok", "model": model}, {}

    monkeypatch.setattr(LLMChatNode, "_call_llm", fake)
    ctx = ExecutionContext(workflow=Workflow(name="t", nodes=[], connections=[]), execution_id="e",
                           start_time=datetime.now(), mode="manual")
    nd = NodeDefinition(name="Chat", type="LLMChat", parameters={"userMessage": "hi"})
    out = asyncio.run(LLMChatNode().execute(ctx, nd, [NodeData(json={})]))
    assert seen["model"] == CURRENT
    assert out.outputs["main"][0].json["model"] == CURRENT


def test_llm_chat_fails_when_the_reply_was_cut_off(monkeypatch):
    """A MAX_TOKENS stop must fail the node: before, the placeholder text flowed on as the 'report' and got emailed."""
    import pytest

    from src.engine.llm_provider import LLMResponse

    async def truncated(**k):
        return LLMResponse(text="The model exceeded its output token limit.", output_truncated=True)

    monkeypatch.setattr(lp, "call_llm", truncated)
    with pytest.raises(Exception, match="(?i)max tokens"):
        asyncio.run(LLMChatNode()._call_llm(model=CURRENT, system_prompt="", user_message="hi", temperature=0.2, max_tokens=64))


def test_llm_chat_default_max_tokens_leaves_room_for_thinking():
    prop = next(p for p in LLMChatNode.node_description.properties if p.name == "maxTokens")
    assert prop.default >= 8192
