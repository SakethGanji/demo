"""Factory functions for resolving inline memory and tool configurations.

Memory and tool configs are inline parameters on the AIAgent node.  These
helpers instantiate the implementation classes and call ``get_config()`` to
produce the config dicts that AIAgent already knows how to consume.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from ...engine.types import NodeDefinition

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lightweight NodeDefinition stand-in for calling get_config()
# ---------------------------------------------------------------------------

@dataclass
class _SyntheticNodeDef:
    """Minimal object satisfying the NodeDefinition interface for get_config."""
    name: str = "inline"
    type: str = "inline"
    parameters: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Memory factory
# ---------------------------------------------------------------------------

def _get_memory_classes() -> dict[str, type]:
    """Lazy import of memory provider classes."""
    from .memory.simple_memory import SimpleMemoryNode
    from .memory.buffer_memory import BufferMemoryNode
    from .memory.token_buffer_memory import TokenBufferMemoryNode
    from .memory.conversation_window_memory import ConversationWindowMemoryNode
    from .memory.summary_memory import SummaryMemoryNode
    from .memory.summary_buffer_memory import SummaryBufferMemoryNode
    from .memory.progressive_summary_memory import ProgressiveSummaryMemoryNode
    from .memory.sqlite_memory import SQLiteMemoryNode
    from .memory.vector_memory import VectorMemoryNode
    from .memory.entity_memory import EntityMemoryNode
    from .memory.knowledge_graph_memory import KnowledgeGraphMemoryNode

    return {
        "simple": SimpleMemoryNode,
        "buffer": BufferMemoryNode,
        "tokenBuffer": TokenBufferMemoryNode,
        "conversationWindow": ConversationWindowMemoryNode,
        "summary": SummaryMemoryNode,
        "summaryBuffer": SummaryBufferMemoryNode,
        "progressiveSummary": ProgressiveSummaryMemoryNode,
        "sqlite": SQLiteMemoryNode,
        "vector": VectorMemoryNode,
        "entity": EntityMemoryNode,
        "knowledgeGraph": KnowledgeGraphMemoryNode,
    }


def resolve_memory(memory_type: str, params: dict[str, Any]) -> dict[str, Any] | None:
    """Resolve inline memory parameters into a memory config dict.

    Args:
        memory_type: One of the keys in ``_get_memory_classes()`` (e.g. "simple").
        params: Flat dict of memory parameters (sessionId, maxMessages, etc.).

    Returns:
        Memory config dict (with getHistory/addMessage/clearHistory callables)
        or None if memory_type is unknown.
    """
    if memory_type == "none" or not memory_type:
        return None

    classes = _get_memory_classes()
    cls = classes.get(memory_type)
    if cls is None:
        logger.warning("Unknown memory type: %s", memory_type)
        return None

    node_def = _SyntheticNodeDef(parameters=params)
    instance = cls()
    try:
        return instance.get_config(node_def)
    except Exception:
        logger.error("Failed to resolve memory type '%s'", memory_type, exc_info=True)
        return None


# ---------------------------------------------------------------------------
# Tool factory
# ---------------------------------------------------------------------------

def _get_tool_classes() -> dict[str, type]:
    """Lazy import of tool provider classes."""
    from .tools.calculator_tool import CalculatorToolNode
    from .tools.current_time_tool import CurrentTimeToolNode
    from .tools.random_number_tool import RandomNumberToolNode
    from .tools.text_tool import TextToolNode
    from .tools.http_request_tool import HttpRequestToolNode
    from .tools.code_tool import CodeToolNode
    from .tools.workflow_tool import WorkflowToolNode
    from .tools.neo4j_query_tool import Neo4jQueryToolNode
    from .tools.data_profile_tool import DataProfileToolNode
    from .tools.data_aggregate_tool import DataAggregateToolNode
    from .tools.data_sample_tool import DataSampleToolNode
    from .tools.data_report_tool import DataReportToolNode
    from .tools.mongo_query_tool import MongoQueryToolNode
    from .tools.api_request_tool import ApiRequestToolNode

    return {
        "calculator": CalculatorToolNode,
        "currentTime": CurrentTimeToolNode,
        "randomNumber": RandomNumberToolNode,
        "text": TextToolNode,
        "httpRequest": HttpRequestToolNode,
        "code": CodeToolNode,
        "workflow": WorkflowToolNode,
        "neo4jQuery": Neo4jQueryToolNode,
        "dataProfile": DataProfileToolNode,
        "dataAggregate": DataAggregateToolNode,
        "dataSample": DataSampleToolNode,
        "dataReport": DataReportToolNode,
        "mongoQuery": MongoQueryToolNode,
        "apiRequest": ApiRequestToolNode,
    }


def resolve_tools(
    tool_specs: list[str | dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Resolve inline tool selections into tool definitions + executors.

    Each entry of ``tool_specs`` is either:
      - a string tool key (e.g. ``"calculator"``) for tools that don't need
        per-instance configuration; or
      - a dict of the form ``{"type": "<key>", "parameters": {...}}`` which
        threads node-level parameters into the tool's ``get_config()``. This
        is how the workflow definition supplies, e.g., the Mongo connection
        string and mandatory filter scope for ``mongoQuery``.

    Returns:
        Tuple of ``(tools_list, tool_executors_dict)`` matching the format
        ``AIAgent.execute()`` already consumes.
    """
    tools: list[dict[str, Any]] = []
    tool_executors: dict[str, Any] = {}

    if not tool_specs:
        return tools, tool_executors

    classes = _get_tool_classes()

    for spec in tool_specs:
        if isinstance(spec, str):
            tool_key = spec
            params: dict[str, Any] = {}
        elif isinstance(spec, dict):
            tool_key = spec.get("type") or spec.get("key") or ""
            raw_params = spec.get("parameters") or spec.get("params") or {}
            params = raw_params if isinstance(raw_params, dict) else {}
        else:
            logger.warning("Tool spec is neither str nor dict: %r", spec)
            continue

        cls = classes.get(tool_key)
        if cls is None:
            logger.warning("Unknown tool type: %s", tool_key)
            continue

        node_def = _SyntheticNodeDef(parameters=params)
        instance = cls()
        try:
            config = instance.get_config(node_def)
        except Exception:
            logger.error("Failed to resolve tool '%s'", tool_key, exc_info=True)
            continue

        if not config.get("name"):
            continue

        tools.append({
            "name": config["name"],
            "description": config.get("description", ""),
            "input_schema": config.get("input_schema", {}),
        })

        if "execute" in config:
            tool_executors[config["name"]] = config["execute"]

    return tools, tool_executors
