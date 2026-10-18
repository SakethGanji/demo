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
