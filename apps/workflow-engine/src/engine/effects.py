"""Which node types talk to the outside world (Stage 3A, CONTRACTS-STAGE3A §3).

A test run never calls an outside system: a node whose effect is not ``none`` needs a mock (or pinned
data) in a test, otherwise the run fails with ``TEST_UNMOCKED_EFFECT``.
"""

from __future__ import annotations

from typing import Literal

Effect = Literal["none", "network", "database", "llm", "subworkflow"]

NODE_EFFECTS: dict[str, Effect] = {
    # triggers
    "Start": "none",
    "Webhook": "none",
    "Cron": "none",
    "ErrorTrigger": "none",
    "ExecuteWorkflowTrigger": "none",
    # flow
    "If": "none",
    "Switch": "none",
    "Merge": "none",
    "Wait": "none",
    "Loop": "none",
    "Poll": "network",
    "ExecuteWorkflow": "subworkflow",
    "StopAndError": "none",
    "Respond": "none",
    # data / transform
    "Set": "none",
    "HttpRequest": "network",
    "Code": "none",
    "Filter": "none",
    "ItemLists": "none",
    # these three send their items to the analytics service over HTTP
    "Sample": "network",
    "Profile": "network",
    "Aggregate": "network",
    "DataTable": "database",
    "PythonStep": "none",
    # integrations
    "SendEmail": "network",
    "Postgres": "database",
    "Neo4j": "database",
    "MongoDB": "database",
    # ai
    "LLMChat": "llm",
    "AIAgent": "llm",
    # ui
    "ChatInput": "none",
}


def effect_of(node_type: str) -> Effect:
    """The effect of a node type; a type that isn't listed is treated as ``none``."""
    return NODE_EFFECTS.get(node_type, "none")
