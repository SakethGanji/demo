"""Which node types talk to the outside world (Stage 3A, CONTRACTS-STAGE3A §3).

A test run never calls an outside system: a node whose effect is not ``none`` needs a mock (or pinned
data) in a test, otherwise the run fails with ``TEST_UNMOCKED_EFFECT``.
"""

from __future__ import annotations

from typing import Literal

Effect = Literal["none", "network", "database", "llm", "subworkflow", "human"]

NODE_EFFECTS: dict[str, Effect] = {
    # triggers
    "Start": "none",
    "Webhook": "none",
    "Cron": "none",
    "ErrorTrigger": "none",
    "ExecuteWorkflowTrigger": "none",
    "FormTrigger": "none",
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
    "NoOp": "none",
    # a person decides: a test run must mock it (a mock is {items, port: approved|rejected|timed_out})
    "Approval": "human",
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


#: Node types a smoke run may call for real: read-style network steps only. Database, email, LLM, analytics-sending
#: and sub-workflow nodes never go live (they could write to a production system on every scheduled tick).
LIVE_SAFE_TYPES: frozenset[str] = frozenset({"HttpRequest"})


def can_go_live(node_type: str) -> bool:
    return node_type in LIVE_SAFE_TYPES
