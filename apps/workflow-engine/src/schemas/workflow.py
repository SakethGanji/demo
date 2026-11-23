"""Workflow-related Pydantic schemas."""

from __future__ import annotations

import re
import uuid
from typing import Any, Literal, TYPE_CHECKING

from pydantic import BaseModel, Field, field_validator, model_validator

if TYPE_CHECKING:
    from ..engine.types import NodeDefinition, Connection, NodeData, Workflow


class PinnedDataItem(BaseModel):
    """Schema for a pinned data item (test data)."""

    json: dict[str, Any] = Field(default_factory=dict, description="JSON data")
    binary: dict[str, Any] | None = Field(None, description="Binary data")


class NodeContractOverride(BaseModel):
    """An approved, learned output contract for one node instance (replaces the node type's computed outputs)."""

    outputs: dict[str, dict[str, Any]] = Field(..., description="Output port -> Shape")
    inputs: dict[str, dict[str, Any]] | None = Field(None, description="Input port -> Shape the node's incoming items must fit (user-declared)")
    source: Literal["observed", "import_placeholder", "declared"] = Field("observed", description="Where the shapes came from")
    approved_by: str = Field(..., min_length=1, description="Who approved it (typed name; no auth yet)")
    approved_at: str | None = Field(None, description="ISO timestamp, set by the server on approve")
    from_execution: str | None = Field(None, description="Execution the shapes were learned from")


_TEST_ID = re.compile(r"^t_[0-9a-f]{8}$")
TEST_CATEGORIES = ("normal", "boundary", "missing", "wrong_type", "unusual",
                   "ext_success", "ext_error", "ext_timeout", "ext_empty")


def new_test_id() -> str:
    return "t_" + uuid.uuid4().hex[:8]


class TestCaseSchema(BaseModel):
    """One test of a workflow (CONTRACTS-STAGE3A §1). Mocks and assertions stay free-form dicts; the runner reads them."""

    __test__ = False  # not a pytest class

    id: str = Field(default_factory=new_test_id, description="t_ + 8 lowercase hex, unique per workflow")
    name: str = Field(..., min_length=1, max_length=120)
    description: str | None = None
    target: dict[str, Any] = Field(default_factory=lambda: {"kind": "workflow"})
    input: dict[str, Any] = Field(default_factory=lambda: {"items": []})
    mocks: dict[str, dict[str, Any]] = Field(default_factory=dict)
    expect: list[dict[str, Any]] = Field(default_factory=list)
    category: Literal["normal", "boundary", "missing", "wrong_type", "unusual",
                      "ext_success", "ext_error", "ext_timeout", "ext_empty"] = "normal"
    field: str | None = None
    covers: list[str] | None = None
    status: Literal["draft", "approved"] = "draft"
    locked: bool = False
    approved_by: str | None = None
    approved_at: str | None = None
    source: Literal["human", "suggested", "replay", "ai", "fuzz"] = "human"
    now: str | None = None
    seed: int | None = None
    # Stage 8 section 6.3: ExecuteWorkflow nodes that run their child for real (the child's own mocks apply) instead of being mocked.
    # Mocks and assertions may then use "<node>/<child node>" keys.
    subworkflows: dict[str, Literal["inline"]] | None = None
    # Stage 8 (convert selection to sub-workflow): the behaviour this test asserts on moved into a sub-workflow, so the test must
    # be reviewed. Set by the extract route; cleared by approve/unlock (the review); an approved stale test blocks publish.
    stale: bool | None = None
    stale_reason: str | None = None

    @field_validator("id")
    @classmethod
    def _id_shape(cls, v: str) -> str:
        if not _TEST_ID.match(v):
            raise ValueError("test id must be 't_' followed by 8 lowercase hex characters")
        return v

    @field_validator("target")
    @classmethod
    def _target_shape(cls, v: dict[str, Any]) -> dict[str, Any]:
        kind = v.get("kind")
        if kind == "workflow":
            return {"kind": "workflow"}
        if kind == "node" and isinstance(v.get("node"), str) and v["node"]:
            return {"kind": "node", "node": v["node"]}
        if kind == "range" and isinstance(v.get("from"), str) and isinstance(v.get("to"), str) and v["from"] and v["to"]:
            return {"kind": "range", "from": v["from"], "to": v["to"]}
        raise ValueError("target must be {kind: workflow} | {kind: node, node} | {kind: range, from, to}")

    @field_validator("input")
    @classmethod
    def _input_shape(cls, v: dict[str, Any]) -> dict[str, Any]:
        items = v.get("items", [])
        if not isinstance(items, list):
            raise ValueError("input.items must be a list")
        return {"items": items}

    @model_validator(mode="after")
    def _locked_means_approved(self) -> "TestCaseSchema":
        self.locked = self.status == "approved"
        return self

    def to_stored(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude_none=True)


class TestDefaultsSchema(BaseModel):
    """Workflow-wide test defaults: one default mock per outside step, a frozen clock and a seed."""

    __test__ = False

    mocks: dict[str, dict[str, Any]] = Field(default_factory=dict)
    now: str | None = None
    seed: int | None = None

    def to_stored(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude_none=True)


def normalize_tests(raw: list[Any] | None) -> list[dict[str, Any]]:
    """Stored/incoming tests -> the canonical stored form (defaults filled, ``locked`` derived from ``status``)."""
    return [TestCaseSchema.model_validate(t).to_stored() for t in (raw or [])]


def _unique_test_ids(tests: list[TestCaseSchema] | None) -> list[TestCaseSchema] | None:
    ids = [t.id for t in tests or []]
    if len(ids) != len(set(ids)):
        raise ValueError("test ids must be unique")
    return tests


class NodeDefinitionSchema(BaseModel):
    """Schema for node definition in a workflow."""

    name: str = Field(..., description="Unique name for this node in the workflow")
    type: str = Field(..., description="Node type identifier")
    label: str | None = Field(None, description="Display label for the node")
    parameters: dict[str, Any] = Field(default_factory=dict, description="Node parameters")
    position: dict[str, float] | None = Field(None, description="UI position {x, y}")
    pinned_data: list[PinnedDataItem] | None = Field(None, description="Pinned test data")
    retry_on_fail: int = Field(0, ge=0, description="Number of retries on failure")
    retry_delay: int = Field(1000, ge=0, description="Delay between retries in ms")
    continue_on_fail: bool = Field(False, description="Legacy; use on_error='continue'")
    disabled: bool = Field(False, description="Disabled nodes pass their input straight to 'main'")
    on_error: Literal["stop", "continue", "error_output"] | None = Field(
        None, description="What to do when the node fails (default 'stop'; legacy continue_on_fail=true maps to 'continue')"
    )
    contract: NodeContractOverride | None = Field(None, description="Approved output contract (replaces computed outputs)")

    class Config:
        json_schema_extra = {
            "example": {
                "name": "http_request_1",
                "type": "HttpRequest",
                "parameters": {"url": "https://api.example.com", "method": "GET"},
                "position": {"x": 100, "y": 200},
            }
        }

    def to_dataclass(self) -> NodeDefinition:
        """Convert to engine NodeDefinition dataclass."""
        from ..engine.types import NodeDefinition as ND, NodeData as NDData
        on_error = self.on_error or ("continue" if self.continue_on_fail else "stop")
        return ND(
            name=self.name,
            type=self.type,
            parameters=self.parameters,
            position=self.position,
            label=self.label,
            pinned_data=[NDData(json=p.json) for p in self.pinned_data] if self.pinned_data else None,
            retry_on_fail=self.retry_on_fail,
            retry_delay=self.retry_delay,
            continue_on_fail=on_error == "continue",
            disabled=self.disabled,
            on_error=on_error,
            contract=self.contract.model_dump(exclude_none=True) if self.contract else None,
        )


class ConnectionSchema(BaseModel):
    """Schema for connection between nodes."""

    source_node: str = Field(..., description="Source node name")
    target_node: str = Field(..., description="Target node name")
    source_output: str = Field("main", description="Source output name")
    target_input: str = Field("main", description="Target input name")
    waypoints: list[dict[str, float]] | None = Field(None, description="Manual edge routing waypoints [{x, y}]")

    class Config:
        json_schema_extra = {
            "example": {
                "source_node": "start",
                "target_node": "http_request_1",
                "source_output": "main",
                "target_input": "main",
            }
        }

    def to_dataclass(self) -> Connection:
        """Convert to engine Connection dataclass."""
        from ..engine.types import Connection as Conn
        return Conn(
            source_node=self.source_node,
            target_node=self.target_node,
            source_output=self.source_output,
            target_input=self.target_input,
            waypoints=self.waypoints,
        )


class WorkflowCreateRequest(BaseModel):
    """Request schema for creating a workflow."""

    name: str = Field(..., min_length=1, max_length=255, description="Workflow name")
    nodes: list[NodeDefinitionSchema] = Field(..., min_length=1, description="List of nodes")
    connections: list[ConnectionSchema] = Field(
        default_factory=list, description="List of connections"
    )
    description: str | None = Field(None, max_length=1000, description="Workflow description")
    settings: dict[str, Any] = Field(default_factory=dict, description="Workflow settings")
    tests: list[TestCaseSchema] = Field(default_factory=list, description="Test cases")
    test_defaults: TestDefaultsSchema = Field(default_factory=TestDefaultsSchema, description="Default mocks, clock and seed of the tests")
    folder_id: str | None = Field(None, description="Folder to organize this workflow in")
    # For ad-hoc execution with input
    input_data: dict[str, Any] | None = Field(None, description="Input data for ad-hoc execution")
    environment: str = Field("default", description="Which variable environment to resolve $vars against")
    project_id: str | None = Field(None, description="Owning project (id or slug); default: Default project")

    def to_workflow(self) -> Workflow:
        """Convert to engine Workflow dataclass."""
        from ..engine.types import Workflow as WF
        return WF(
            name=self.name,
            nodes=[n.to_dataclass() for n in self.nodes],
            connections=[c.to_dataclass() for c in self.connections],
            description=self.description,
            settings=self.settings,
            tests=[t.to_stored() for t in self.tests],
            test_defaults=self.test_defaults.to_stored(),
        )

    @field_validator("tests")
    @classmethod
    def _tests_unique(cls, v: list[TestCaseSchema]) -> list[TestCaseSchema]:
        return _unique_test_ids(v) or []


class WorkflowUpdateRequest(BaseModel):
    """Request schema for updating a workflow."""

    name: str | None = Field(None, min_length=1, max_length=255, description="Workflow name")
    nodes: list[NodeDefinitionSchema] | None = Field(None, description="List of nodes")
    connections: list[ConnectionSchema] | None = Field(None, description="List of connections")
    description: str | None = Field(None, max_length=1000, description="Workflow description")
    settings: dict[str, Any] | None = Field(None, description="Workflow settings")
    tests: list[TestCaseSchema] | None = Field(None, description="Test cases (omit to keep the stored ones)")
    test_defaults: TestDefaultsSchema | None = Field(None, description="Default mocks, clock and seed (omit to keep)")
    node_renames: dict[str, str] | None = Field(None, description="Nodes renamed since the last save {old: new}; applied to the stored tests before the lock check")
    folder_id: str | None = Field(None, description="Folder ID, or null to move to root")
    project_id: str | None = Field(None, description="Move the workflow to this project (id or slug)")

    @field_validator("tests")
    @classmethod
    def _tests_unique(cls, v: list[TestCaseSchema] | None) -> list[TestCaseSchema] | None:
        return _unique_test_ids(v)


class ContractLearnRequest(BaseModel):
    """Body of the learn endpoint."""

    execution_id: str | None = Field(None, description="Execution to learn from; default: latest with a saved output for the node")


class ContractLearnResponse(BaseModel):
    outputs: dict[str, dict[str, Any]] = Field(..., description="Output port -> inferred Shape")
    from_execution: str
    sample_count: int


class ContractApproveRequest(BaseModel):
    """Body of the approve endpoint (``approved_by`` is a typed name; there is no auth yet)."""

    outputs: dict[str, dict[str, Any]] = Field(..., description="Output port -> Shape")
    from_execution: str | None = Field(None, description="Execution the shapes were learned from")
    approved_by: str = Field(..., min_length=1, max_length=255)


class TestApproveRequest(BaseModel):
    """Body of POST /workflows/{id}/tests/{test_id}/approve (a typed name; there is no auth yet)."""

    __test__ = False

    approved_by: str = Field(..., min_length=1, max_length=255)


class TestUnlockRequest(BaseModel):
    """Body of POST /workflows/{id}/tests/{test_id}/unlock."""

    __test__ = False

    by: str = Field(..., min_length=1, max_length=255)


class ScoreRequest(BaseModel):
    """Body of POST /workflows/{id}/score."""

    definition: dict[str, Any] | None = None
    mutations: bool = True
    repeat: int = Field(0, ge=0, le=20)


class ApplyOpsRequest(BaseModel):
    """Body of POST /workflows/{id}/apply-ops: toolkit ops ``{op, args}``; ``dry_run`` doesn't save."""

    ops: list[dict[str, Any]]
    dry_run: bool = False


class TestRunRequest(BaseModel):
    """Body of POST /workflows/{id}/tests/run: run some or all tests, optionally against an unsaved draft."""

    __test__ = False

    ids: list[str] | None = None
    definition: dict[str, Any] | None = None


class PublishRequest(BaseModel):
    """Optional request body for publishing a workflow version."""

    message: str | None = Field(None, max_length=500, description="Version message / changelog")


class CheckRequest(BaseModel):
    """Optional body of POST /workflows/{id}/check: check this definition instead of the stored draft."""

    definition: dict[str, Any] | None = None
    environment: str | None = None


class WorkflowResponse(BaseModel):
    """Response schema for workflow creation."""

    id: str
    name: str
    active: bool
    project_id: str = "proj_default"
    webhook_url: str
    created_at: str


class WorkflowListItem(BaseModel):
    """Schema for workflow in list response."""

    id: str
    name: str
    active: bool
    project_id: str = "proj_default"
    webhook_url: str
    node_count: int
    created_at: str
    updated_at: str
    description: str | None = None
    handler_for: list[dict[str, Any]] = []


class WorkflowDetailResponse(BaseModel):
    """Detailed workflow response."""

    id: str
    name: str
    active: bool
    project_id: str = "proj_default"
    webhook_url: str
    definition: dict[str, Any]
    created_at: str
    updated_at: str


class WorkflowPublishResponse(BaseModel):
    """Response for publish/unpublish.

    ``impact`` (publish only, Stage 8 4.3): the published parents that follow this workflow's ``latest`` and may now break,
    ``[{parent_workflow_id, parent_name, node, version_spec, in_release, breaking: [WF024 findings]}]``. Never blocks.
    """

    id: str
    active: bool
    version_id: int | None


class WorkflowPublishResult(WorkflowPublishResponse):
    """Response of publish only (unpublish keeps the base shape).

    ``impact`` (Stage 8 4.3): the published parents that follow this workflow's ``latest`` and may now break,
    ``[{parent_workflow_id, parent_name, node, version_spec, in_release, breaking: [WF024 findings]}]``. Never blocks.
    """

    version_number: int | None = None  # the per-workflow number (v1, v2, ...); version_id is the global row id
    impact: list[dict[str, Any]] = []


class VersionListItem(BaseModel):
    """Schema for version in list response."""

    id: int
    version_number: int
    message: str | None
    created_by: str | None
    created_at: str


class VersionDetailResponse(VersionListItem):
    """Version detail with full definition."""

    definition: dict[str, Any]
