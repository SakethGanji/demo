"""SQLModel database models."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlmodel import Column, Field, SQLModel
from sqlalchemy import Column as SAColumn, DateTime, Index, Integer, BigInteger, String, ForeignKey, text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID


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
# Projects (BACKEND-DESIGN §2 M1)
# ---------------------------------------------------------------------------

DEFAULT_PROJECT_ID = "proj_default"


class ProjectModel(SQLModel, table=True):
    """A project: the unit that owns apps, routes, workflows, tables, secrets, releases."""

    __tablename__ = "projects"

    id: str = Field(primary_key=True)
    team_id: str = Field(default="default", foreign_key="teams.id", index=True)
    name: str
    slug: str
    description: str | None = Field(default=None)
    environments: list[dict[str, Any]] = Field(
        default_factory=lambda: [{"name": "dev", "kind": "draft"}, {"name": "prod", "kind": "release"}],
        sa_column=Column(JSONB, nullable=False),
    )
    default_environment: str = Field(default="dev")
    live_environment: str = Field(default="prod")
    settings: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSONB, nullable=False, server_default="{}"))
    created_by: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


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
    project_id: str = Field(default=DEFAULT_PROJECT_ID, foreign_key="projects.id", index=True)
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
    project_id: str = Field(default=DEFAULT_PROJECT_ID, foreign_key="projects.id", index=True)
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
    project_id: str = Field(default=DEFAULT_PROJECT_ID, foreign_key="projects.id", index=True)
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
    project_id: str | None = Field(default=None, index=True)
    environment: str | None = Field(default=None)

    status: str = Field(index=True)  # running, success, failed, cancelled, waiting
    mode: str  # manual, webhook, cron

    # Progress tracking (O(1) completion check)
    total_nodes: int | None = Field(default=None)
    completed_nodes: int = Field(default=0)

    # Subworkflow tracking
    # (Stage 8: parent_execution_id is FK fk_executions_parent ON DELETE CASCADE; root_execution_id is the top-level run)
    parent_execution_id: str | None = Field(default=None)
    parent_node_name: str | None = Field(default=None)
    depth: int = Field(default=0)
    root_execution_id: str | None = Field(default=None, index=True)

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
        Index("ix_variables_team_env", "team_id", "environment"),
    )

    id: int | None = Field(default=None, sa_column=Column(Integer, primary_key=True, autoincrement=True))
    team_id: str = Field(foreign_key="teams.id", index=True)
    # NULL = team-shared; set = project-scoped (project value wins at run time).
    project_id: str | None = Field(default=None, foreign_key="projects.id", index=True)
    allowed_hosts: list[str] | None = Field(default=None, sa_column=Column(JSONB, nullable=True))
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
        Index("idx_data_tables_project_name", "project_id", "name", unique=True),
    )

    id: str = Field(primary_key=True)
    team_id: str = Field(foreign_key="teams.id", index=True)
    project_id: str = Field(default=DEFAULT_PROJECT_ID, foreign_key="projects.id", index=True)
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


# ---------------------------------------------------------------------------
# Project datasets (files stored by the analytics service, linked to a project by name)
# ---------------------------------------------------------------------------


class ProjectDatasetModel(SQLModel, table=True):
    """A dataset imported into a project. The bytes live in the analytics service; this row is the project's link to
    them. Workflows store the link ``id`` (a UUIDv7) in a node's ``datasetId``; ``name`` is for people and expressions.

    Cross-database contract: ``project_datasets.dataset_id`` <-> analytics ``datasets.id`` and
    ``project_datasets.version_id`` <-> analytics ``dataset_versions.id`` (the version imported). The engine and the
    analytics service keep separate Postgres databases for now, so there is no foreign key: the engine keeps the two
    in step (an upload whose link insert fails deletes the analytics dataset; a delete removes the analytics dataset
    first and keeps the link if that fails), and reads report ``missing`` when the analytics side is gone. When the
    databases merge, both columns become real foreign keys.
    """

    __tablename__ = "project_datasets"
    __table_args__ = (
        Index("ux_project_datasets_project_name", "project_id", "name", unique=True),
        Index("ux_project_datasets_project_dataset", "project_id", "dataset_id", unique=True),
    )

    id: str = Field(sa_column=Column(PG_UUID(as_uuid=False), primary_key=True))
    team_id: str = Field(foreign_key="teams.id", index=True)
    project_id: str = Field(foreign_key="projects.id", index=True)
    dataset_id: str = Field(sa_column=Column(PG_UUID(as_uuid=False), nullable=False))  # analytics datasets.id
    version_id: str | None = Field(default=None, sa_column=Column(PG_UUID(as_uuid=False), nullable=True))  # analytics dataset_versions.id
    name: str
    file_name: str
    format: str  # csv | parquet | xlsx | xls
    row_count: int | None = Field(default=None)
    column_count: int | None = Field(default=None)
    size_bytes: int | None = Field(default=None, sa_column=Column(BigInteger, nullable=True))
    # [{"name": "amount", "type": "DOUBLE"}, ...] in the file's column order
    columns: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSONB, nullable=False))
    created_by: str | None = Field(default=None)
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
# Broker call log (phase 4)
# ---------------------------------------------------------------------------


class BrokerCallModel(SQLModel, table=True):
    """One node-as-function call made through the broker (parameters redacted)."""

    __tablename__ = "broker_calls"

    id: str = Field(primary_key=True)
    execution_id: str | None = Field(default=None, index=True)
    project_id: str | None = Field(default=None)
    environment: str | None = Field(default=None)
    origin: str | None = Field(default=None)
    node_type: str | None = Field(default=None)
    params_redacted: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSONB, nullable=False, server_default="{}"))
    status: str
    error_code: str | None = Field(default=None)
    error: str | None = Field(default=None)
    duration_ms: float | None = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.now)


# ---------------------------------------------------------------------------
# API section (phase 5)
# ---------------------------------------------------------------------------


class ApiRouteModel(SQLModel, table=True):
    __tablename__ = "api_routes"

    id: str = Field(primary_key=True)
    project_id: str = Field(foreign_key="projects.id", index=True)
    name: str
    method: str
    path: str
    path_shape: str
    auth: str = Field(default="none")
    description: str | None = Field(default=None)
    request_schema: dict[str, Any] | None = Field(default=None, sa_column=Column(JSONB, nullable=True))
    response_schema: dict[str, Any] | None = Field(default=None, sa_column=Column(JSONB, nullable=True))
    response_mode: str = Field(default="auto")
    validate_request: bool = Field(default=False)
    handler_type: str
    handler_workflow_id: str | None = Field(default=None, foreign_key="workflows.id")
    handler_module_id: str | None = Field(default=None, foreign_key="code_modules.id")
    handler_function: str | None = Field(default=None)
    source: str = Field(default="ui")
    timeout_s: int = Field(default=30)
    created_by: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


class ApiKeyModel(SQLModel, table=True):
    __tablename__ = "api_keys"

    id: str = Field(primary_key=True)
    project_id: str = Field(foreign_key="projects.id", index=True)
    environment: str
    name: str
    prefix: str
    key_hash: str
    created_by: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.now)
    last_used_at: datetime | None = Field(default=None)
    revoked_at: datetime | None = Field(default=None)


class ApiSpecRevisionModel(SQLModel, table=True):
    __tablename__ = "api_spec_revisions"

    id: int | None = Field(default=None, sa_column=Column(Integer, primary_key=True, autoincrement=True))
    project_id: str = Field(foreign_key="projects.id", index=True)
    revision: int
    spec_hash: str
    spec: dict[str, Any] = Field(sa_column=Column(JSONB, nullable=False))
    changes: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSONB, nullable=False, server_default="[]"))
    created_at: datetime = Field(default_factory=datetime.now)


# ---------------------------------------------------------------------------
# Python modules (phase 8)
# ---------------------------------------------------------------------------


class CodeModuleModel(SQLModel, table=True):
    __tablename__ = "code_modules"

    id: str = Field(primary_key=True)
    project_id: str = Field(foreign_key="projects.id", index=True)
    name: str
    source: str
    source_sha: str
    created_by: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


# ---------------------------------------------------------------------------
# Releases (phase 9)
# ---------------------------------------------------------------------------


class ReleaseModel(SQLModel, table=True):
    __tablename__ = "releases"

    id: str = Field(primary_key=True)
    project_id: str = Field(foreign_key="projects.id", index=True)
    environment: str
    number: int
    is_active: bool = Field(default=False)
    status: str
    snapshot: dict[str, Any] = Field(sa_column=Column(JSONB, nullable=False))
    checks: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSONB, nullable=False, server_default="[]"))
    source_release_id: str | None = Field(default=None, foreign_key="releases.id")
    notes: str | None = Field(default=None)
    created_by: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.now)


class ApiSpecModel(SQLModel, table=True):
    """A registered API (Stage 3A catalog): the normalised OpenAPI document, ``spec = {"operations": [...]}``."""

    __tablename__ = "api_specs"

    id: str = Field(primary_key=True)
    project_id: str = Field(default=DEFAULT_PROJECT_ID, foreign_key="projects.id", index=True)
    name: str
    base_url: str = Field(default="")
    spec: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSONB, nullable=False))
    source: str = Field(default="paste")  # paste | upload | url | example
    source_url: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


class ApiDriftModel(SQLModel, table=True):
    """An API reply that no longer matches what the workflow expects (Stage 3B drift alert)."""

    __tablename__ = "api_drift"
    __table_args__ = (Index("uq_api_drift_fingerprint", "workflow_id", "node_name", "fingerprint", unique=True),)

    id: str = Field(primary_key=True)  # drift_<hex>
    project_id: str = Field(default=DEFAULT_PROJECT_ID, foreign_key="projects.id", index=True)
    workflow_id: str = Field(foreign_key="workflows.id", index=True)
    node_name: str
    api_ref: dict[str, Any] | None = Field(default=None, sa_column=Column(JSONB))
    basis: str  # spec | contract | mock
    status_code: int | None = Field(default=None)
    diffs: list[Any] = Field(default_factory=list, sa_column=Column(JSONB, nullable=False))
    fingerprint: str
    first_seen: datetime = Field(default_factory=datetime.now)
    last_seen: datetime = Field(default_factory=datetime.now)
    count: int = Field(default=1)
    last_execution_id: str | None = Field(default=None)
    status: str = Field(default="open")  # open | resolved
    resolved_by: str | None = Field(default=None)
    resolved_at: datetime | None = Field(default=None)


class SmokeConfigModel(SQLModel, table=True):
    """Live smoke test settings of one workflow (Stage 3B)."""

    __tablename__ = "smoke_configs"

    workflow_id: str = Field(primary_key=True, foreign_key="workflows.id")
    enabled: bool = Field(default=False)
    test_ids: list[Any] = Field(default_factory=list, sa_column=Column(JSONB, nullable=False))
    live_nodes: list[Any] = Field(default_factory=list, sa_column=Column(JSONB, nullable=False))
    schedule: str | None = Field(default=None)
    before_release: bool = Field(default=False)
    next_run_at: datetime | None = Field(default=None)
    updated_at: datetime = Field(default_factory=datetime.now)


class SmokeRunModel(SQLModel, table=True):
    """One live smoke run (manual, scheduled or before a release)."""

    __tablename__ = "smoke_runs"

    id: str = Field(primary_key=True)  # smoke_<hex>
    workflow_id: str = Field(foreign_key="workflows.id", index=True)
    trigger: str = Field(default="manual")  # manual | schedule | release
    status: str  # passed | failed | error
    started_at: datetime = Field(default_factory=datetime.now)
    finished_at: datetime | None = Field(default=None)
    results: list[Any] = Field(default_factory=list, sa_column=Column(JSONB, nullable=False))


# ---------------------------------------------------------------------------
# Stage 5: interfaces and human tasks (DESIGN-STAGE5-UIS section 3.2). The migration is the source of truth.
# ---------------------------------------------------------------------------


def _tstz() -> Column:
    return Column(DateTime(timezone=True))


def _tstz_now() -> Column:
    return Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))


class HumanTask(SQLModel, table=True):
    """A durable human step created by an Approval node (status open until someone decides)."""

    __tablename__ = "human_tasks"
    __table_args__ = (Index("human_tasks_exec_node_idx", "execution_id", "node_name", "run_index", unique=True),)

    id: str = Field(primary_key=True)  # tsk_<uuid7>
    project_id: str = Field(foreign_key="projects.id")
    workflow_id: str
    workflow_version_id: int | None = Field(default=None)
    execution_id: str = Field(foreign_key="executions.id")
    node_name: str
    run_index: int = Field(default=0)
    kind: str = Field(default="approval")
    title: str
    context: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSONB, nullable=False, server_default="{}"))
    form: list[Any] = Field(default_factory=list, sa_column=Column(JSONB, nullable=False, server_default="[]"))
    assignees: list[Any] = Field(default_factory=list, sa_column=Column(JSONB, nullable=False, server_default="[]"))
    status: str = Field(default="open")  # open | approved | rejected | timed_out | cancelled
    due_at: datetime | None = Field(default=None, sa_column=_tstz())
    on_timeout: str = Field(default="timed_out")
    decision: dict[str, Any] | None = Field(default=None, sa_column=Column(JSONB))
    decided_by: str | None = Field(default=None)
    decided_at: datetime | None = Field(default=None, sa_column=_tstz())
    decide_key: str | None = Field(default=None)
    resume_state: str = Field(default="none")  # none | pending | running | done | failed
    resume_owner: str | None = Field(default=None)
    resume_heartbeat: datetime | None = Field(default=None, sa_column=_tstz())
    snapshot: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSONB, nullable=False))
    created_at: datetime = Field(default_factory=datetime.now, sa_column=_tstz_now())
    updated_at: datetime = Field(default_factory=datetime.now, sa_column=_tstz_now())


class InterfaceSubmission(SQLModel, table=True):
    """One form submission: dedupes by Idempotency-Key and maps a submission to its execution for anonymous polling."""

    __tablename__ = "interface_submissions"
    __table_args__ = (Index("interface_submissions_key_idx", "project_id", "slug", "idempotency_key", unique=True),)

    id: str = Field(primary_key=True)  # sub_<uuid7>
    project_id: str = Field(foreign_key="projects.id")
    workflow_id: str
    slug: str
    idempotency_key: str
    execution_id: str | None = Field(default=None, foreign_key="executions.id")
    run_token_hash: str
    mode: str  # live | preview
    created_at: datetime = Field(default_factory=datetime.now, sa_column=_tstz_now())


class ChatSession(SQLModel, table=True):
    __tablename__ = "chat_sessions"

    id: str = Field(primary_key=True)  # chs_<uuid7>
    project_id: str = Field(foreign_key="projects.id")
    workflow_id: str
    slug: str
    mode: str  # live | preview
    user_label: str | None = Field(default=None)
    user_id: str | None = Field(default=None)
    token_hash: str
    busy_execution_id: str | None = Field(default=None)
    last_seq: int = Field(default=0)
    created_at: datetime = Field(default_factory=datetime.now, sa_column=_tstz_now())
    updated_at: datetime = Field(default_factory=datetime.now, sa_column=_tstz_now())


class ChatMessage(SQLModel, table=True):
    __tablename__ = "chat_messages"
    __table_args__ = (Index("chat_messages_seq_idx", "session_id", "seq", unique=True),)

    id: str = Field(primary_key=True)  # chm_<uuid7>
    session_id: str = Field(foreign_key="chat_sessions.id")
    seq: int
    role: str  # user | assistant | error
    content: str
    execution_id: str | None = Field(default=None)
    client_message_id: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.now, sa_column=_tstz_now())


class InterfaceSlug(SQLModel, table=True):
    """Live slug index: which workflow serves /i/{project}/{slug} (written at publish)."""

    __tablename__ = "interface_slugs"

    project_id: str = Field(primary_key=True, foreign_key="projects.id")
    slug: str = Field(primary_key=True)
    workflow_id: str
    kind: str
    version_id: int
    updated_at: datetime = Field(default_factory=datetime.now, sa_column=_tstz_now())


# ---------------------------------------------------------------------------
# Stage 6: AI builder proposals (DESIGN-STAGE6-AI-BUILDER section 2). The migration is the source of truth.
# ---------------------------------------------------------------------------


def _jsonb_default(default_sql: str) -> Column:
    return Column(JSONB, nullable=False, server_default=text(default_sql))


class AIProposal(SQLModel, table=True):
    """One AI change to a workflow (new or existing), held until a human keeps or discards it."""

    __tablename__ = "ai_proposals"

    id: str = Field(primary_key=True)  # p_<12 hex>
    project_id: str = Field(foreign_key="projects.id")
    team_id: str | None = Field(default=None)
    workflow_id: str | None = Field(default=None, foreign_key="workflows.id")  # NULL = new workflow
    parent_id: str | None = Field(default=None, foreign_key="ai_proposals.id")
    kind: str  # build | modify | node_assist | chat
    status: str  # spec_draft | clarify | spec_approved | building | ready | failed | kept | undone | discarded | answered
    actor: str
    request: str
    request_sha256: str
    spec: dict[str, Any] | None = Field(default=None, sa_column=Column(JSONB))
    spec_approved_by: str | None = Field(default=None)
    spec_approved_at: datetime | None = Field(default=None, sa_column=_tstz())
    spec_fingerprint: str | None = Field(default=None)
    base_definition: dict[str, Any] | None = Field(default=None, sa_column=Column(JSONB))
    base_draft_hash: str | None = Field(default=None)
    proposed_definition: dict[str, Any] | None = Field(default=None, sa_column=Column(JSONB))
    ops_log: list[Any] = Field(default_factory=list, sa_column=_jsonb_default("'[]'::jsonb"))
    diff: dict[str, Any] | None = Field(default=None, sa_column=Column(JSONB))
    report: dict[str, Any] | None = Field(default=None, sa_column=Column(JSONB))
    test_change_requests: list[Any] = Field(default_factory=list, sa_column=_jsonb_default("'[]'::jsonb"))
    answer: str | None = Field(default=None)
    model: str
    provider: str
    region: str | None = Field(default=None)
    llm_calls: int = Field(default=0)
    input_tokens: int = Field(default=0)
    output_tokens: int = Field(default=0)
    cost_usd: float | None = Field(default=None)
    duration_ms: int = Field(default=0)
    error_code: str | None = Field(default=None)
    error_message: str | None = Field(default=None)
    kept_by: str | None = Field(default=None)
    kept_at: datetime | None = Field(default=None, sa_column=_tstz())
    kept_draft_hash: str | None = Field(default=None)
    accepted_failing: bool = Field(default=False)
    # Capability graph (migration 20261022120000): the Understand result, the owning app build and the part within it
    understanding: dict[str, Any] | None = Field(default=None, sa_column=Column(JSONB))
    app_build_id: str | None = Field(default=None, foreign_key="app_builds.id")
    part_key: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.now, sa_column=_tstz_now())
    updated_at: datetime = Field(default_factory=datetime.now, sa_column=_tstz_now())


class AIProposalEvent(SQLModel, table=True):
    """Append-only audit row of a proposal (no UPDATE or DELETE path exists in the repository)."""

    __tablename__ = "ai_proposal_events"
    __table_args__ = (Index("ai_proposal_events_seq_uq", "proposal_id", "seq", unique=True),)

    id: int | None = Field(default=None, sa_column=Column(BigInteger, primary_key=True, autoincrement=True))
    proposal_id: str = Field(foreign_key="ai_proposals.id")
    seq: int
    at: datetime = Field(default_factory=datetime.now, sa_column=_tstz_now())
    actor: str | None = Field(default=None)
    event: str
    payload: dict[str, Any] = Field(default_factory=dict, sa_column=_jsonb_default("'{}'::jsonb"))


# ---------------------------------------------------------------------------
# Stage 8: reuse (sub-workflows and templates). DESIGN-STAGE8-REUSE section 10.
# ---------------------------------------------------------------------------


class WorkflowRefModel(SQLModel, table=True):
    """One ExecuteWorkflow node of a parent workflow (draft when ``parent_version_id`` is NULL, else an immutable version)."""

    __tablename__ = "workflow_refs"

    id: int | None = Field(default=None, sa_column=Column(BigInteger, primary_key=True, autoincrement=True))
    project_id: str = Field(foreign_key="projects.id")
    parent_workflow_id: str = Field(foreign_key="workflows.id")
    parent_version_id: int | None = Field(default=None, foreign_key="workflow_versions.id")
    node_name: str
    child_workflow_id: str  # no FK: a dangling reference is a checker finding, not a database error
    version_spec: str  # 'draft' | 'latest' | '<n>'
    created_at: datetime = Field(default_factory=datetime.now, sa_column=_tstz_now())


class TemplateInstallModel(SQLModel, table=True):
    """A template installed into a project (idempotency key and old-id to new-id mapping)."""

    __tablename__ = "template_installs"

    id: str = Field(primary_key=True)  # tpi_<uuid>
    project_id: str = Field(foreign_key="projects.id")
    template_slug: str
    template_version: str
    bundle_hash: str
    install_name: str
    status: str = Field(default="installed")
    mapping: dict[str, Any] = Field(default_factory=dict, sa_column=_jsonb_default("'{}'::jsonb"))
    needs: list[Any] = Field(default_factory=list, sa_column=_jsonb_default("'[]'::jsonb"))
    depends_on: list[Any] = Field(default_factory=list, sa_column=_jsonb_default("'[]'::jsonb"))
    installed_by: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.now, sa_column=_tstz_now())
    updated_at: datetime = Field(default_factory=datetime.now, sa_column=_tstz_now())


# ---------------------------------------------------------------------------
# Semantic Capability Graph (PLAN-CAPABILITY-GRAPH section 1, CONTRACTS-CAPGRAPH.md). Migration 20261022120000.
# ---------------------------------------------------------------------------


class KgNodeModel(SQLModel, table=True):
    """A concept, capability, role, learned pattern or app. ``key`` is unique per (scope, team, project)."""

    __tablename__ = "kg_nodes"
    __table_args__ = (
        Index("ux_kg_nodes_key", "scope", text("coalesce(team_id, '')"), text("coalesce(project_id, '')"), "key", unique=True),
        Index("ix_kg_nodes_project", "project_id", "kind"),
        Index("ix_kg_nodes_kind", "kind", "status"),
    )

    id: str = Field(primary_key=True)  # kn_<12 hex>
    key: str
    kind: str  # concept | capability | role | pattern | app
    subkind: str | None = Field(default=None)
    scope: str  # global | team | project
    team_id: str | None = Field(default=None)
    project_id: str | None = Field(default=None, foreign_key="projects.id")
    visibility: str = Field(default="scope")  # scope | org (patterns only, decision D1)
    label: str
    summary: str = Field(default="")
    aliases: list[Any] = Field(default_factory=list, sa_column=_jsonb_default("'[]'::jsonb"))
    props: dict[str, Any] = Field(default_factory=dict, sa_column=_jsonb_default("'{}'::jsonb"))
    source: str  # seed | extracted | learned | human | llm_suggested
    status: str = Field(default="active")  # active | stale | proposed | rejected
    validated_count: int = Field(default=0)
    last_validated_at: datetime | None = Field(default=None, sa_column=_tstz())
    created_at: datetime = Field(default_factory=datetime.now, sa_column=_tstz_now())
    updated_at: datetime = Field(default_factory=datetime.now, sa_column=_tstz_now())


class KgEdgeModel(SQLModel, table=True):
    """A typed relation ``src --rel--> dst`` (the vocabulary is frozen in CONTRACTS-CAPGRAPH.md section 5)."""

    __tablename__ = "kg_edges"
    __table_args__ = (Index("ix_kg_edges_dst", "dst_id", "rel"), Index("kg_edges_src_id_rel_dst_id_key", "src_id", "rel", "dst_id", unique=True))

    id: int | None = Field(default=None, sa_column=Column(BigInteger, primary_key=True, autoincrement=True))
    src_id: str = Field(foreign_key="kg_nodes.id")
    rel: str
    dst_id: str = Field(foreign_key="kg_nodes.id")
    props: dict[str, Any] = Field(default_factory=dict, sa_column=_jsonb_default("'{}'::jsonb"))
    source: str
    confidence: float = Field(default=1.0)
    created_at: datetime = Field(default_factory=datetime.now, sa_column=_tstz_now())


class KgLearnEventModel(SQLModel, table=True):
    """Append-only operational memory: what a Keep, rebuild or human action taught the graph."""

    __tablename__ = "kg_learn_events"

    id: int | None = Field(default=None, sa_column=Column(BigInteger, primary_key=True, autoincrement=True))
    project_id: str | None = Field(default=None, foreign_key="projects.id")
    team_id: str | None = Field(default=None)
    at: datetime = Field(default_factory=datetime.now, sa_column=_tstz_now())
    actor: str | None = Field(default=None)
    trigger: str  # seed | rebuild | kept_workflow | kept_app | human
    proposal_id: str | None = Field(default=None)  # or the app_build id
    summary: str
    payload: dict[str, Any] = Field(default_factory=dict, sa_column=_jsonb_default("'{}'::jsonb"))


class AppBuildModel(SQLModel, table=True):
    """A multi-part application build; each part is an ordinary ``ai_proposals`` row."""

    __tablename__ = "app_builds"

    id: str = Field(primary_key=True)  # ab_<12 hex>
    project_id: str = Field(foreign_key="projects.id")
    team_id: str | None = Field(default=None)
    actor: str
    request: str
    request_sha256: str
    status: str  # clarify | planned | specs | building | ready | kept | discarded | failed
    understanding: dict[str, Any] | None = Field(default=None, sa_column=Column(JSONB))
    answers: dict[str, Any] = Field(default_factory=dict, sa_column=_jsonb_default("'{}'::jsonb"))
    plan: dict[str, Any] | None = Field(default=None, sa_column=Column(JSONB))
    bundle: dict[str, Any] | None = Field(default=None, sa_column=Column(JSONB))
    install_id: str | None = Field(default=None)
    kept_by: str | None = Field(default=None)
    kept_at: datetime | None = Field(default=None, sa_column=_tstz())
    created_at: datetime = Field(default_factory=datetime.now, sa_column=_tstz_now())
    updated_at: datetime = Field(default_factory=datetime.now, sa_column=_tstz_now())
