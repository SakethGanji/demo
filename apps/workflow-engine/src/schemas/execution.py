"""Execution-related Pydantic schemas."""

from typing import Any, Literal

from pydantic import BaseModel, Field


class NodeTestRequest(BaseModel):
    """Request schema for testing a single node."""

    source: Literal["input", "last_run", "pinned"] = Field(
        default="input", description="Where the node's input comes from"
    )
    input_items: list[dict[str, Any]] | None = Field(
        default=None, description="Input item json objects (required when source is 'input')"
    )
    environment: str = Field(default="default", description="Which variable environment to resolve $vars against")


class RunFromRequest(BaseModel):
    """Request schema for running from a node using a past execution's outputs."""

    execution_id: str | None = Field(default=None, description="Execution to reuse (default: the latest)")
    environment: str = Field(default="default", description="Which variable environment to resolve $vars against")


class RunWorkflowRequest(BaseModel):
    """Request schema for running a workflow with input data."""

    input_data: dict[str, Any] | None = Field(
        default=None,
        description="Initial input data to pass to the workflow",
    )
    environment: str = Field(
        default="default",
        description="Which variable environment to resolve $vars against",
    )
    # Attribution (who started the run, as whom, through which agent); stored in executions.metadata.attribution
    triggered_by: str | None = Field(default=None, max_length=200)
    run_as: str | None = Field(default=None, max_length=200)
    via_agent: str | None = Field(default=None, max_length=200)


class ExecutionErrorSchema(BaseModel):
    """Schema for execution error."""

    node_name: str
    error: str
    timestamp: str
    error_code: str | None = None
    details: dict[str, Any] | None = None


class ExecutionResponse(BaseModel):
    """Response schema for workflow execution."""

    status: str = Field(..., description="Execution status: success, failed or waiting (an Approval paused the run)")
    execution_id: str = Field(..., description="Unique execution ID")
    data: dict[str, Any] = Field(..., description="Output data from each node")
    errors: list[ExecutionErrorSchema] = Field(
        default_factory=list, description="List of errors"
    )


class ExecutionListItem(BaseModel):
    """Schema for execution in list response."""

    id: str
    workflow_id: str
    workflow_name: str
    status: str
    mode: str
    start_time: str
    end_time: str | None
    error_count: int
    attribution: dict[str, Any] | None = None


class ExecutionParentRef(BaseModel):
    """The run that called this one, and the node that made the call."""

    execution_id: str
    workflow_id: str
    workflow_name: str
    node: str | None = None


class ExecutionChildRef(BaseModel):
    """One sub-workflow run started by this run."""

    execution_id: str
    node: str | None = None
    workflow_id: str
    workflow_name: str
    version_number: int | None = None  # None = the child's draft
    workflow_version_id: int | None = None
    status: str
    duration_ms: int | None = None


class ExecutionDetailResponse(BaseModel):
    """Detailed execution response."""

    id: str
    workflow_id: str
    workflow_name: str
    status: str
    mode: str
    start_time: str
    end_time: str | None
    errors: list[ExecutionErrorSchema]
    node_data: dict[str, Any]
    node_metrics: dict[str, Any] = {}
    attribution: dict[str, Any] | None = None  # executions.metadata.attribution {triggered_by, run_as, via_agent, via}
    # Per-item loop history: {Loop name: {totalItems, concurrency, onItemFailure, stopAfterFailures, passes: [{index,
    # status, items, nodes: {step: {status, ports, items, error}}}], truncated}}. Null for runs saved before it existed.
    loop_runs: dict[str, Any] | None = None
    # Repeat-until tries: {Poll name: {sessions: [{tries: [{n, at, result, value}], outcome, passes}], truncated}}.
    poll_tries: dict[str, Any] | None = None
    # Stage 8 lineage (DESIGN-STAGE8-REUSE section 11). A top-level run: parent null, its own id as root, depth 0.
    parent: ExecutionParentRef | None = None
    root_execution_id: str | None = None
    depth: int = 0
    children: list[ExecutionChildRef] = []


