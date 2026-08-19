"""SQLModel database models."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlmodel import Column, Field, SQLModel
from sqlalchemy import Column as SAColumn, Index, Integer, BigInteger, String, ForeignKey, text
from sqlalchemy.dialects.postgresql import JSONB


# ---------------------------------------------------------------------------
# Identity & access
# ---------------------------------------------------------------------------


class UserModel(SQLModel, table=True):
    """SSO-backed user identity. PK is the SSO subject/external ID."""

    __tablename__ = "users"

    id: str = Field(primary_key=True)  # SSO subject / external ID
    email: str = Field(index=True)
    display_name: str | None = Field(default=None)
    avatar_url: str | None = Field(default=None)
    sso_provider: str | None = Field(default=None)  # "okta", "entra", "google"
    last_login_at: datetime | None = Field(default=None)
    disabled: bool = Field(default=False)
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


class TeamModel(SQLModel, table=True):
    """Team / workspace — the top-level resource boundary."""

    __tablename__ = "teams"

    id: str = Field(primary_key=True)
    name: str
    slug: str = Field(unique=True)  # URL-safe identifier
    description: str | None = Field(default=None)
    created_by: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


class TeamMemberModel(SQLModel, table=True):
    """User membership in a team with role-based access."""

    __tablename__ = "team_members"
    __table_args__ = (
        Index("idx_team_members_unique", "team_id", "user_id", unique=True),
    )

    id: int | None = Field(default=None, sa_column=Column(Integer, primary_key=True, autoincrement=True))
    team_id: str = Field(foreign_key="teams.id", index=True)
    user_id: str = Field(foreign_key="users.id", index=True)
    role: str = Field(default="viewer")  # owner, admin, editor, viewer
    created_at: datetime = Field(default_factory=datetime.now)


# ---------------------------------------------------------------------------
# Organization
# ---------------------------------------------------------------------------


class FolderModel(SQLModel, table=True):
    """Nested folder hierarchy for organizing workflows."""

    __tablename__ = "folders"
    __table_args__ = (
        Index("idx_folders_team_parent", "team_id", "parent_folder_id"),
    )

    id: str = Field(primary_key=True)
    team_id: str = Field(foreign_key="teams.id", index=True)
    parent_folder_id: str | None = Field(default=None, foreign_key="folders.id")
    name: str
    created_by: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


class TagModel(SQLModel, table=True):
    """Team-scoped tags for cross-cutting workflow labels."""

    __tablename__ = "tags"
    __table_args__ = (
        Index("idx_tags_team_name", "team_id", "name", unique=True),
    )

    id: str = Field(primary_key=True)
    team_id: str = Field(foreign_key="teams.id", index=True)
    name: str
    color: str | None = Field(default=None)  # hex color for UI
    created_at: datetime = Field(default_factory=datetime.now)


class WorkflowTagModel(SQLModel, table=True):
    """Junction table: workflow <-> tag (many-to-many)."""

    __tablename__ = "workflow_tags"
    __table_args__ = (
        Index("idx_workflow_tags_pk", "workflow_id", "tag_id", unique=True),
    )

    id: int | None = Field(default=None, sa_column=Column(Integer, primary_key=True, autoincrement=True))
    workflow_id: str = Field(foreign_key="workflows.id", index=True)
    tag_id: str = Field(foreign_key="tags.id", index=True)


# ---------------------------------------------------------------------------
# Workflows
# ---------------------------------------------------------------------------


class WorkflowModel(SQLModel, table=True):
    """Workflow database model."""

    __tablename__ = "workflows"

    id: str = Field(primary_key=True)
    team_id: str = Field(default="default", foreign_key="teams.id", index=True)
    folder_id: str | None = Field(default=None, foreign_key="folders.id", index=True)
    name: str = Field(index=True)
    description: str | None = Field(default=None)
    active: bool = Field(default=False, index=True)

    # Draft: the working copy in the editor. Updated on every save.
    draft_definition: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSONB))

    # Published: points to the immutable version used for execution.
    published_version_id: int | None = Field(default=None, foreign_key="workflow_versions.id")

    # Per-workflow settings: timezone, error_workflow_id, max_execution_time, retry
    settings: dict[str, Any] | None = Field(default=None, sa_column=Column(JSONB))

    created_by: str | None = Field(default=None)
    updated_by: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


class WorkflowVersionModel(SQLModel, table=True):
    """Immutable workflow version snapshot."""

    __tablename__ = "workflow_versions"
    __table_args__ = (
        Index("idx_versions_workflow", "workflow_id", "version_number", unique=True),
    )

    id: int | None = Field(default=None, sa_column=Column(Integer, primary_key=True, autoincrement=True))
    workflow_id: str = Field(foreign_key="workflows.id", index=True)
    version_number: int
    definition: dict[str, Any] = Field(sa_column=Column(JSONB, nullable=False))
    message: str | None = Field(default=None)
    created_by: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.now)


# ---------------------------------------------------------------------------
# Apps (visual UI builder)
# ---------------------------------------------------------------------------


class AppModel(SQLModel, table=True):
    """App database model — visual UI definitions built in the app builder."""

    __tablename__ = "apps"

    id: str = Field(primary_key=True)
    team_id: str = Field(default="default", foreign_key="teams.id", index=True)
    folder_id: str | None = Field(default=None, foreign_key="folders.id", index=True)
    name: str
    description: str | None = Field(default=None)
    slug: str | None = Field(default=None, unique=True)
    active: bool = Field(default=False)
    workflow_ids: list[str] = Field(default_factory=list, sa_column=Column(JSONB, server_default="[]"))
    api_execution_ids: list[str] = Field(default_factory=list, sa_column=Column(JSONB, server_default="[]"))

    draft_definition: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSONB))
    draft_source_code: str | None = Field(default=None)
    current_version_id: int | None = Field(default=None, foreign_key="app_versions.id")
    published_version_id: int | None = Field(default=None, foreign_key="app_versions.id")
    settings: dict[str, Any] | None = Field(default=None, sa_column=Column(JSONB))

    access: str = Field(default="private")  # private, public, password
    access_password_hash: str | None = Field(default=None)
    published_at: datetime | None = Field(default=None)
    embed_enabled: bool = Field(default=False)

    created_by: str | None = Field(default=None)
    updated_by: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


class AppVersionModel(SQLModel, table=True):
    """Immutable app version snapshot."""

    __tablename__ = "app_versions"
    __table_args__ = (
        Index("idx_app_versions_app", "app_id", "version_number", unique=True),
        Index("idx_app_versions_parent", "parent_version_id"),
    )

    id: int | None = Field(default=None, sa_column=Column(Integer, primary_key=True, autoincrement=True))
    app_id: str = Field(foreign_key="apps.id", index=True)
    version_number: int
    parent_version_id: int | None = Field(default=None, foreign_key="app_versions.id")
    definition: dict[str, Any] = Field(sa_column=Column(JSONB, nullable=False))
    source_code: str = Field(sa_column=SAColumn(String, nullable=False))
    trigger: str = Field(default="ai")  # ai, manual, publish
    label: str | None = Field(default=None)
    prompt: str | None = Field(default=None)
    message: str | None = Field(default=None)
    created_by: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.now)

    # Bundle artifact (set on publish; pluggable storage backend writes here today,
    # could write to object storage in the future without schema change).
    bundle_js: str | None = Field(default=None)
    bundle_css: str | None = Field(default=None)
    bundle_hash: str | None = Field(default=None, index=True)
    bundled_at: datetime | None = Field(default=None)


class AppFileModel(SQLModel, table=True):
    """Individual file in a multi-file app version."""

    __tablename__ = "app_files"
    __table_args__ = (
        Index("idx_app_files_version", "version_id"),
        Index("idx_app_files_version_path", "version_id", "path", unique=True),
    )

    id: int | None = Field(default=None, sa_column=Column(Integer, primary_key=True, autoincrement=True))
    version_id: int = Field(foreign_key="app_versions.id", index=True)
    path: str
    content: str
    file_type: str = Field(default="tsx")
    parsed_index: dict[str, Any] | None = Field(default=None, sa_column=Column(JSONB))
    size_bytes: int = Field(default=0)
    created_at: datetime = Field(default_factory=datetime.now)


# ---------------------------------------------------------------------------
# Executions
# ---------------------------------------------------------------------------


class ExecutionModel(SQLModel, table=True):
    """Execution history database model."""

    __tablename__ = "executions"

    id: str = Field(primary_key=True)
    workflow_id: str = Field(index=True)
    workflow_version_id: int | None = Field(default=None)
    workflow_name: str
    team_id: str = Field(default="default", foreign_key="teams.id", index=True)

    status: str = Field(index=True)  # running, success, failed, cancelled, waiting
    mode: str  # manual, webhook, cron

    # Progress tracking (O(1) completion check)
    total_nodes: int | None = Field(default=None)
    completed_nodes: int = Field(default=0)

    # Subworkflow tracking
    parent_execution_id: str | None = Field(default=None)
    parent_node_name: str | None = Field(default=None)
    depth: int = Field(default=0)

    # Retry lineage
    retry_of_execution_id: str | None = Field(default=None)

    # Cancellation
    cancelled_at: datetime | None = Field(default=None)

    start_time: datetime = Field(default_factory=datetime.now, index=True)
    end_time: datetime | None = Field(default=None)
    resume_at: datetime | None = Field(default=None)
    error_count: int = Field(default=0)

    # Generic JSONB bag for extensible execution context:
    # suspension: {resume_token, resume_node, node_states}
    # trigger: {type, path, request_id}
    # labels: {env, customer}
    # billing: {llm_tokens, api_calls}
    exec_metadata: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column("metadata", JSONB, nullable=False, server_default="{}"),
    )


class NodeOutputModel(SQLModel, table=True):
    """Per-node output persisted during execution."""

    __tablename__ = "node_outputs"
    __table_args__ = (
        Index("idx_node_outputs_exec", "execution_id"),
        Index("idx_node_outputs_unique", "execution_id", "node_name", "run_index", unique=True),
    )

    id: int | None = Field(default=None, sa_column=Column(Integer, primary_key=True, autoincrement=True))
    execution_id: str = Field(index=True)
    node_name: str
    output: dict[str, Any] = Field(sa_column=Column(JSONB, nullable=False))
    metrics: dict[str, Any] | None = Field(default=None, sa_column=Column(JSONB))
    status: str  # success, error, no_output
    error: str | None = Field(default=None)
    run_index: int = Field(default=0)
    created_at: datetime = Field(default_factory=datetime.now)


class ActiveTriggerModel(SQLModel, table=True):
    """Active triggers for webhooks, crons, polling, streams, etc."""

    __tablename__ = "active_triggers"

    id: int | None = Field(default=None, sa_column=Column(Integer, primary_key=True, autoincrement=True))
    workflow_id: str = Field(foreign_key="workflows.id", index=True)
    workflow_version_id: int | None = Field(default=None)
    team_id: str = Field(default="default", foreign_key="teams.id")
    node_name: str

    type: str  # webhook, cron, interval, polling, kafka, mqtt, redis, ...
    webhook_path: str | None = Field(default=None)
    config: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSONB, nullable=False))
    state: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSONB, nullable=False))

    next_run_at: datetime | None = Field(default=None)
    last_run_at: datetime | None = Field(default=None)

    error_count: int = Field(default=0)
    last_error: str | None = Field(default=None)

    enabled: bool = Field(default=True)
    created_at: datetime = Field(default_factory=datetime.now)


class CredentialModel(SQLModel, table=True):
    """Encrypted credentials storage."""

    __tablename__ = "credentials"

    id: str = Field(primary_key=True)
    team_id: str = Field(default="default", foreign_key="teams.id", index=True)
    name: str
    type: str  # "openai", "postgres", "smtp"
    data: str  # Fernet-encrypted JSON
    created_by: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


class SharedCredentialModel(SQLModel, table=True):
    """Controls credential visibility — who can use/manage a credential."""

    __tablename__ = "shared_credentials"
    __table_args__ = (
        Index("idx_shared_creds_cred", "credential_id"),
        Index("idx_shared_creds_target", "share_type", "share_target_id"),
        Index("idx_shared_creds_unique", "credential_id", "share_type", "share_target_id", unique=True),
    )

    id: int | None = Field(default=None, sa_column=Column(Integer, primary_key=True, autoincrement=True))
    credential_id: str = Field(foreign_key="credentials.id", index=True)
    share_type: str  # "team" or "user"
    share_target_id: str  # team_id or user_id depending on share_type
    role: str = Field(default="user")  # "user" (can reference) or "manager" (can edit/rotate/delete)
    created_by: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.now)


# ---------------------------------------------------------------------------
# Variables
# ---------------------------------------------------------------------------


class VariableModel(SQLModel, table=True):
    """Team-scoped key-value variables (like env vars for workflows), scoped per environment."""

    __tablename__ = "variables"
    __table_args__ = (
        Index("idx_variables_team_env_key", "team_id", "environment", "key", unique=True),
        Index("ix_variables_team_env", "team_id", "environment"),
    )

    id: int | None = Field(default=None, sa_column=Column(Integer, primary_key=True, autoincrement=True))
    team_id: str = Field(foreign_key="teams.id", index=True)
    environment: str = Field(default="default")
    key: str
    value: str  # Fernet ciphertext when type='secret', otherwise plaintext.
    type: str = Field(default="string")  # string, secret, number
    description: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


# ---------------------------------------------------------------------------
# Data tables (user-defined structured storage)
# ---------------------------------------------------------------------------


class DataTableModel(SQLModel, table=True):
    """User-defined data table metadata."""

    __tablename__ = "data_tables"
    __table_args__ = (
        Index("idx_data_tables_team_name", "team_id", "name", unique=True),
    )

    id: str = Field(primary_key=True)
    team_id: str = Field(foreign_key="teams.id", index=True)
    name: str
    description: str | None = Field(default=None)
    # Column schema: [{"name": "email", "type": "string", "required": true}, ...]
    columns: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSONB, nullable=False))
    created_by: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


class DataTableRowModel(SQLModel, table=True):
    """Row in a user-defined data table. Data stored as JSON object."""

    __tablename__ = "data_table_rows"
    __table_args__ = (
        Index("idx_data_table_rows_table", "table_id"),
    )

    id: int | None = Field(default=None, sa_column=Column(BigInteger, primary_key=True, autoincrement=True))
    table_id: str = Field(foreign_key="data_tables.id", index=True)
    data: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSONB, nullable=False))
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


class ApiTestExecutionModel(SQLModel, table=True):
    """Captured HTTP request/response snapshot from the API Tester.

    Used as context for the app-builder LLM — the generated app reproduces
    exactly the URL, method, headers, and body shape recorded here.
    """

    __tablename__ = "api_test_executions"

    id: str = Field(primary_key=True)
    team_id: str = Field(default="default", foreign_key="teams.id", index=True)
    name: str | None = Field(default=None)
    method: str
    url: str
    request_headers: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default="{}"),
    )
    request_body_text: str | None = Field(default=None)
    # Multipart upload metadata (no bytes): list of
    # {field, filename, size, content_type}. Null when the request is not
    # multipart. Replays require the user to re-attach files.
    request_files: list[dict[str, Any]] | None = Field(
        default=None,
        sa_column=Column(JSONB, nullable=True),
    )
    response_status: int | None = Field(default=None)
    response_headers: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default="{}"),
    )
    response_content_type: str | None = Field(default=None)
    response_size: int = Field(default=0)
    response_body_b64: str | None = Field(default=None)
    response_truncated: bool = Field(default=False)
    # Pre-computed compact view of the response (schema/template/snippet)
    # for the LLM context renderer — avoids re-decoding b64 each chat turn.
    # See `services.schema_inference.summarize_response` for the shape.
    response_summary: dict[str, Any] | None = Field(
        default=None,
        sa_column=Column(JSONB, nullable=True),
    )
    latency_ms: float | None = Field(default=None)
    error: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


# ---------------------------------------------------------------------------
# Agents
#
# An agent is a DEFINITION, not an actor: it holds no run state, so N people
# run the same one concurrently. The session owns the workspace and the memory
# key; the run owns the trace. `settings` is a JSONB bag whose keys are exactly
# the AIAgent node's parameter names, so AgentRuntime can pass it straight in.
# ---------------------------------------------------------------------------


class AgentModel(SQLModel, table=True):
    """A saved, runnable agent definition."""

    __tablename__ = "agents"

    id: str = Field(primary_key=True)
    team_id: str = Field(default="default", foreign_key="teams.id", index=True)
    folder_id: str | None = Field(default=None, foreign_key="folders.id")
    name: str = Field(index=True)
    description: str | None = Field(default=None)
    role: str = Field(default="asks")  # asks | builds | watches — derived from tools
    model: str = Field(default="claude-sonnet-5")
    system_prompt: str = Field(default="")
    task_template: str | None = Field(default=None)
    settings: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSONB))
    memory: dict[str, Any] | None = Field(default=None, sa_column=Column(JSONB))
    output_schema: dict[str, Any] | None = Field(default=None, sa_column=Column(JSONB))
    version: int = Field(default=1)
    active: bool = Field(default=True)
    archived_at: datetime | None = Field(default=None)
    created_by: str | None = Field(default=None)
    updated_by: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


class AgentToolBindingModel(SQLModel, table=True):
    """Which tools an agent may use, in the order the model sees them."""

    __tablename__ = "agent_tool_bindings"
    __table_args__ = (Index("ix_atb_agent_pos", "agent_id", "position"),)

    id: int | None = Field(
        default=None, sa_column=SAColumn(Integer, primary_key=True, autoincrement=True)
    )
    agent_id: str = Field(foreign_key="agents.id", index=True)
    source: str = Field(default="builtin")  # builtin|mcp|openapi|node|promoted
    connector_id: str | None = Field(default=None)
    tool_key: str
    alias: str | None = Field(default=None)
    config: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSONB))
    requires_approval: bool = Field(default=False)
    enabled: bool = Field(default=True)
    position: int = Field(default=0)
    created_at: datetime = Field(default_factory=datetime.now)


class AgentSessionModel(SQLModel, table=True):
    """A continuous body of work with one agent. Owns the workspace + memory key."""

    __tablename__ = "agent_sessions"

    id: str = Field(primary_key=True)
    agent_id: str = Field(foreign_key="agents.id", index=True)
    team_id: str = Field(default="default", index=True)
    title: str
    status: str = Field(default="active")  # active | idle | archived
    # Pinned at creation so behaviour never shifts mid-conversation.
    agent_version: int = Field(default=1)
    agent_config: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSONB))
    app_id: str | None = Field(default=None)
    workflow_id: str | None = Field(default=None)
    memory_key: str  # never the literal "default" — that is the cross-tenant leak
    created_by: str | None = Field(default=None)
    holder_id: str | None = Field(default=None)  # single-writer lock
    holder_since: datetime | None = Field(default=None)
    run_count: int = Field(default=0)
    last_run_at: datetime | None = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


class AgentRunModel(SQLModel, table=True):
    """One TURN within a session. Immutable once terminal."""

    __tablename__ = "agent_runs"

    id: str = Field(primary_key=True)
    session_id: str = Field(foreign_key="agent_sessions.id", index=True)
    agent_id: str = Field(foreign_key="agents.id", index=True)
    team_id: str = Field(default="default")
    turn: int = Field(default=1)
    status: str = Field(default="queued")
    trigger: str = Field(default="studio")
    task: str
    input: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSONB))
    # Frozen copy of the config this turn ran under. Survives an adopt-newer-version.
    agent_snapshot: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSONB))
    response: str | None = Field(default=None)
    structured_output: dict[str, Any] | None = Field(default=None, sa_column=Column(JSONB))
    error: str | None = Field(default=None)
    iterations: int = Field(default=0)
    tool_call_count: int = Field(default=0)
    input_tokens: int = Field(default=0)
    output_tokens: int = Field(default=0)
    llm_time_ms: float = Field(default=0.0)
    event_count: int = Field(default=0)
    parent_execution_id: str | None = Field(default=None)
    created_by: str | None = Field(default=None)
    started_at: datetime = Field(default_factory=datetime.now)
    ended_at: datetime | None = Field(default=None)
    cancelled_at: datetime | None = Field(default=None)


class AgentRunEventModel(SQLModel, table=True):
    """One agent:* event. Row-per-event so a killed pod still leaves a trace."""

    __tablename__ = "agent_run_events"

    id: int | None = Field(
        default=None, sa_column=SAColumn(BigInteger, primary_key=True, autoincrement=True)
    )
    run_id: str = Field(foreign_key="agent_runs.id", index=True)
    seq: int
    type: str
    node_name: str | None = Field(default=None)
    payload: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSONB))
    truncated: bool = Field(default=False)
    created_at: datetime = Field(default_factory=datetime.now)


class AgentApprovalModel(SQLModel, table=True):
    """A tool call held pending a human decision."""

    __tablename__ = "agent_approvals"

    id: str = Field(primary_key=True)
    run_id: str = Field(foreign_key="agent_runs.id", index=True)
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSONB))
    reason: str | None = Field(default=None)
    status: str = Field(default="pending")
    scope: str | None = Field(default=None)  # once | run | always
    decided_by: str | None = Field(default=None)
    decision_note: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.now)
    decided_at: datetime | None = Field(default=None)


class ToolConnectorModel(SQLModel, table=True):
    """A registered MCP server or OpenAPI spec."""

    __tablename__ = "tool_connectors"

    id: str = Field(primary_key=True)
    team_id: str = Field(default="default", index=True)
    name: str
    kind: str  # mcp | openapi
    base_url: str
    spec_url: str | None = Field(default=None)
    tool_prefix: str = Field(default="")
    config: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSONB))
    selection: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSONB))
    headers: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSONB))
    enabled: bool = Field(default=True)
    status: str = Field(default="pending")
    last_error: str | None = Field(default=None)
    source_hash: str | None = Field(default=None)
    instructions: str | None = Field(default=None)
    last_discovered_at: datetime | None = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


class ConnectorToolModel(SQLModel, table=True):
    """One tool discovered from a connector. Selection survives re-discovery."""

    __tablename__ = "connector_tools"

    id: int | None = Field(
        default=None, sa_column=SAColumn(BigInteger, primary_key=True, autoincrement=True)
    )
    connector_id: str = Field(foreign_key="tool_connectors.id", index=True)
    remote_id: str
    tool_name: str  # PINNED at first import; never renamed by re-discovery
    description: str = Field(default="")
    input_schema: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSONB))
    optional_args: list[str] = Field(default_factory=list, sa_column=Column(JSONB))
    invoke: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSONB))
    selected: bool = Field(default=False)
    read_only: bool = Field(default=True)
    unsupported_reason: str | None = Field(default=None)
    schema_hash: str = Field(default="")
    est_tokens: int = Field(default=0)
    first_seen_at: datetime = Field(default_factory=datetime.now)
    last_seen_at: datetime = Field(default_factory=datetime.now)
    removed_at: datetime | None = Field(default=None)


class PromotedToolModel(SQLModel, table=True):
    """An accepted workflow registered as a callable tool. Pinned to ONE version."""

    __tablename__ = "promoted_tools"

    id: str = Field(primary_key=True)
    team_id: str = Field(default="default", index=True)
    workflow_id: str = Field(foreign_key="workflows.id", index=True)
    version_id: int | None = Field(default=None)
    version_number: int | None = Field(default=None)
    tool_name: str
    description: str = Field(default="")
    input_schema: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSONB))
    est_tokens: int = Field(default=0)
    call_count: int = Field(default=0)
    success_count: int = Field(default=0)
    fail_count: int = Field(default=0)
    recent_results: list[Any] = Field(default_factory=list, sa_column=Column(JSONB))
    demoted_at: datetime | None = Field(default=None)
    demoted_reason: str | None = Field(default=None)
    accepted_by: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
