"""Core type definitions for the workflow engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Literal


class NoOutputSignal:
    """Special signal to indicate a branch produced no output (for Merge node)."""

    _instance: NoOutputSignal | None = None

    def __new__(cls) -> NoOutputSignal:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "NO_OUTPUT_SIGNAL"


NO_OUTPUT_SIGNAL = NoOutputSignal()


class RecursionLimitError(Exception):
    """Raised when subworkflow execution exceeds max depth."""

    pass


class WorkflowStopSignal(Exception):
    """Signal to stop workflow execution gracefully."""

    def __init__(self, message: str = "Workflow stopped", error_type: str = "error"):
        self.message = message
        self.error_type = error_type  # "error" or "warning"
        super().__init__(message)


@dataclass
class WebhookResponse:
    """Custom response for webhook-triggered workflows."""

    status_code: int = 200
    body: Any = None
    headers: dict[str, str] | None = None
    content_type: str = "application/json"


@dataclass
class NodeData:
    """Data item passed between nodes."""

    json: dict[str, Any]
    binary: dict[str, bytes] | None = None


@dataclass
class ExecutionContext:
    """Context for a workflow execution."""

    workflow: Workflow
    execution_id: str
    start_time: datetime
    mode: Literal["manual", "webhook", "cron"]

    # Node execution state
    node_states: dict[str, list[NodeData]] = field(default_factory=dict)

    # For loop support: track execution per iteration
    node_run_counts: dict[str, int] = field(default_factory=dict)

    # For Merge node: track which inputs have been received
    pending_inputs: dict[str, dict[str, list[NodeData] | NoOutputSignal]] = field(
        default_factory=dict
    )

    # For SplitInBatches: stateful node data
    node_internal_state: dict[str, dict[str, Any]] = field(default_factory=dict)

    # Error tracking
    errors: list[ExecutionError] = field(default_factory=list)

    # Shared HTTP client for performance
    http_client: Any | None = None  # httpx.AsyncClient

    # Subworkflow support: depth tracking
    execution_depth: int = 0  # Current nesting level
    max_execution_depth: int = 10  # Configurable limit
    parent_execution_id: str | None = None  # For tracing/debugging

    # Workflow repository for subworkflow loading
    workflow_repository: Any | None = None  # WorkflowRepository

    # Custom webhook response (set by RespondToWebhook node)
    webhook_response: WebhookResponse | None = None

    # Subworkflow input data (set by ExecuteWorkflow node)
    subworkflow_input: list[NodeData] | None = None

    # Event callback for real-time streaming (passed through to subworkflows)
    on_event: ExecutionEventCallback | None = None


@dataclass
class ExecutionError:
    """Error that occurred during execution."""

    node_name: str
    error: str
    timestamp: datetime


@dataclass
class NodeExecutionResult:
    """
    Multi-output result from node execution.

    Keys are output names: "main", "true", "false", "loop", "done", etc.
    None value signals that branch should propagate NO_OUTPUT_SIGNAL.
    """

    outputs: dict[str, list[NodeData] | None]


@dataclass
class ExecutionJob:
    """Job in the execution queue."""

    node_name: str
    input_data: list[NodeData]
    source_node: str | None
    source_output: str
    run_index: int


@dataclass
class ExecutionRecord:
    """Execution record for history."""

    id: str
    workflow_id: str
    workflow_name: str
    status: Literal["running", "success", "failed"]
    mode: Literal["manual", "webhook", "cron"]
    start_time: datetime
    end_time: datetime | None = None
    node_data: dict[str, list[NodeData]] = field(default_factory=dict)
    errors: list[ExecutionError] = field(default_factory=list)


@dataclass
class StoredWorkflow:
    """Stored workflow with metadata."""

    id: str
    name: str
    workflow: Workflow
    active: bool
    created_at: datetime
    updated_at: datetime


class ExecutionEventType(str, Enum):
    """Types of execution events for SSE streaming."""

    EXECUTION_START = "execution:start"
    NODE_START = "node:start"
    NODE_COMPLETE = "node:complete"
    NODE_ERROR = "node:error"
    EXECUTION_COMPLETE = "execution:complete"
    EXECUTION_ERROR = "execution:error"
    # Agent-specific events
    AGENT_THINKING = "agent:thinking"
    AGENT_TOOL_CALL = "agent:tool_call"
    AGENT_TOOL_RESULT = "agent:tool_result"
    AGENT_TOKEN = "agent:token"


@dataclass
class ExecutionEvent:
    """Real-time execution event for SSE streaming."""

    type: ExecutionEventType
    execution_id: str
    timestamp: datetime
    node_name: str | None = None
    node_type: str | None = None
    data: list[NodeData] | None = None
    error: str | None = None
    progress: dict[str, int] | None = None
    subworkflow_parent_node: str | None = None
    subworkflow_id: str | None = None


# Callback type for receiving execution events
ExecutionEventCallback = Callable[[ExecutionEvent], None]


# --- Workflow Schema Types ---


@dataclass
class NodeDefinition:
    """Definition of a node in a workflow."""

    name: str
    type: str
    parameters: dict[str, Any] = field(default_factory=dict)
    position: dict[str, float] | None = None
    pinned_data: list[NodeData] | None = None
    label: str | None = None
    retry_on_fail: int = 0
    retry_delay: int = 1000
    continue_on_fail: bool = False


@dataclass
class Connection:
    """Connection between two nodes."""

    source_node: str
    target_node: str
    source_output: str = "main"
    target_input: str = "main"
    connection_type: Literal["normal", "subnode"] = "normal"
    slot_name: str | None = None  # For subnode connections
    waypoints: list[dict[str, float]] | None = None  # Manual edge routing


# --- Subnode Types ---


@dataclass
class SubnodeSlotDefinition:
    """Defines a slot on a parent node that can accept subnodes."""

    name: str  # "chatModel", "memory", "tools"
    display_name: str  # "Chat Model", "Memory", "Tools"
    slot_type: str  # "model", "memory", "tool"
    required: bool = False  # Is at least one subnode required?
    multiple: bool = False  # Can accept multiple subnodes? (tools=True)
    accepted_node_types: list[str] | None = None  # Restrict to specific types


@dataclass
class ResolvedSubnode:
    """A subnode resolved for execution."""

    node_name: str
    node_type: str
    slot_name: str
    slot_type: str
    config: dict[str, Any]  # Resolved parameters from subnode


@dataclass
class SubnodeContext:
    """All subnodes resolved for a parent node."""

    models: list[ResolvedSubnode] = field(default_factory=list)
    memory: list[ResolvedSubnode] = field(default_factory=list)
    tools: list[ResolvedSubnode] = field(default_factory=list)


@dataclass
class Workflow:
    """Workflow definition."""

    name: str
    nodes: list[NodeDefinition]
    connections: list[Connection]
    id: str | None = None
    description: str | None = None
    settings: dict[str, Any] = field(default_factory=dict)
