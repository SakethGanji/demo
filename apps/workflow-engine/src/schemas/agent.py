"""Agent-related Pydantic schemas.

Mirrors the ``agents`` / ``agent_tool_bindings`` / ``agent_sessions`` /
``agent_runs`` / ``agent_run_events`` tables. Kept deliberately thin: the
service layer owns every derivation (role, version bumps, memory keys).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

ToolSource = Literal["builtin", "mcp", "openapi", "node", "promoted"]
RunStatus = Literal["queued", "running", "waiting", "success", "failed", "cancelled"]


# ---------------------------------------------------------------------------
# Tool bindings
# ---------------------------------------------------------------------------


class ToolBindingSchema(BaseModel):
    """One tool an agent may call."""

    source: ToolSource = Field("builtin", description="Where the tool comes from")
    connector_id: str | None = Field(None, description="Connector this tool belongs to")
    tool_key: str = Field(..., description="Tool identifier within its source")
    alias: str | None = Field(None, description="Name the model sees, if renamed")
    config: dict[str, Any] = Field(default_factory=dict, description="Per-binding tool parameters")
    requires_approval: bool = Field(False, description="Hold calls for a human decision")
    enabled: bool = Field(True, description="Unbound-but-remembered when false")
    position: int = Field(0, description="Order the model sees tools in")


class AgentToolsUpdateRequest(BaseModel):
    """Full replacement of an agent's tool bindings."""

    tools: list[ToolBindingSchema] = Field(default_factory=list)


class AvailableToolItem(BaseModel):
    """A tool the studio may offer for binding."""

    key: str
    source: ToolSource = "builtin"
    name: str
    display_name: str
    description: str
    icon: str | None = None
    parameters: list[dict[str, Any]] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------


class AgentCreateRequest(BaseModel):
    """Create a new agent."""

    name: str = Field(..., min_length=1)
    description: str | None = None
    model: str = Field("claude-sonnet-4-20250514")
    system_prompt: str = Field("")
    task_template: str | None = None
    settings: dict[str, Any] = Field(default_factory=dict, description="AIAgent node parameters")
    memory: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None
    folder_id: str | None = None
    team_id: str = "default"
    active: bool = True
    created_by: str | None = None
    tools: list[ToolBindingSchema] | None = Field(None, description="Optional initial bindings")


class AgentUpdateRequest(BaseModel):
    """Partial update. Any config-changing field bumps ``version``."""

    name: str | None = None
    description: str | None = None
    model: str | None = None
    system_prompt: str | None = None
    task_template: str | None = None
    settings: dict[str, Any] | None = None
    memory: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None
    folder_id: str | None = None
    active: bool | None = None
    updated_by: str | None = None
    tools: list[ToolBindingSchema] | None = None


class AgentResponse(BaseModel):
    """Full agent record, including its bindings."""

    id: str
    team_id: str
    folder_id: str | None = None
    name: str
    description: str | None = None
    role: str
    model: str
    system_prompt: str
    task_template: str | None = None
    settings: dict[str, Any] = Field(default_factory=dict)
    memory: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None
    version: int
    active: bool
    tools: list[ToolBindingSchema] = Field(default_factory=list)
    created_by: str | None = None
    updated_by: str | None = None
    created_at: str
    updated_at: str


class AgentListItem(BaseModel):
    """Agent row for list views."""

    id: str
    name: str
    description: str | None = None
    role: str
    model: str
    version: int
    active: bool
    tool_count: int = 0
    session_count: int = 0
    last_run_at: str | None = None
    updated_at: str


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


class SessionCreateRequest(BaseModel):
    """Open a session. Pins the agent version + config at this instant."""

    title: str | None = None
    app_id: str | None = None
    workflow_id: str | None = None
    created_by: str | None = None


class SessionResponse(BaseModel):
    """A session — one continuous body of work with one agent."""

    id: str
    agent_id: str
    agent_name: str | None = None
    team_id: str
    title: str
    status: str
    agent_version: int
    agent_config: dict[str, Any] = Field(default_factory=dict)
    app_id: str | None = None
    workflow_id: str | None = None
    memory_key: str
    holder_id: str | None = None
    run_count: int
    last_run_at: str | None = None
    created_by: str | None = None
    created_at: str
    updated_at: str


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------


class RunTriggerRequest(BaseModel):
    """Start one turn."""

    task: str = Field(..., min_length=1)
    input: dict[str, Any] = Field(default_factory=dict)
    trigger: str = "studio"
    agent_id: str | None = Field(None, description="Only when no session_id is supplied")
    variables: dict[str, str] = Field(default_factory=dict, description="$vars for this run")
    max_run_seconds: int | None = Field(None, ge=1, le=7200)
    created_by: str | None = None


class RunResponse(BaseModel):
    """One turn, queued through terminal."""

    id: str
    session_id: str
    agent_id: str
    team_id: str
    turn: int
    status: RunStatus
    trigger: str
    task: str
    input: dict[str, Any] = Field(default_factory=dict)
    agent_snapshot: dict[str, Any] = Field(default_factory=dict)
    response: str | None = None
    structured_output: dict[str, Any] | None = None
    error: str | None = None
    iterations: int = 0
    tool_call_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    llm_time_ms: float = 0.0
    event_count: int = 0
    created_by: str | None = None
    started_at: str
    ended_at: str | None = None
    cancelled_at: str | None = None


class RunListItem(BaseModel):
    """Run row for list views — no payloads."""

    id: str
    session_id: str
    agent_id: str
    turn: int
    status: RunStatus
    trigger: str
    task: str
    iterations: int = 0
    tool_call_count: int = 0
    event_count: int = 0
    started_at: str
    ended_at: str | None = None


class RunEventItem(BaseModel):
    """One recorded ``agent:*`` event."""

    seq: int
    type: str
    node_name: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    truncated: bool = False
    created_at: str
