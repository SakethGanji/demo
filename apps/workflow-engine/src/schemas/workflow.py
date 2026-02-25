"""Workflow-related Pydantic schemas."""

from __future__ import annotations

from typing import Any, Literal, TYPE_CHECKING

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from ..engine.types import NodeDefinition, Connection, NodeData, Workflow


class PinnedDataItem(BaseModel):
    """Schema for a pinned data item (test data)."""

    json: dict[str, Any] = Field(default_factory=dict, description="JSON data")
    binary: dict[str, Any] | None = Field(None, description="Binary data")


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
    continue_on_fail: bool = Field(False, description="Continue execution on failure")

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
        return ND(
            name=self.name,
            type=self.type,
            parameters=self.parameters,
            position=self.position,
            label=self.label,
            pinned_data=[NDData(json=p.json) for p in self.pinned_data] if self.pinned_data else None,
            retry_on_fail=self.retry_on_fail,
            retry_delay=self.retry_delay,
            continue_on_fail=self.continue_on_fail,
        )


class ConnectionSchema(BaseModel):
    """Schema for connection between nodes."""

    source_node: str = Field(..., description="Source node name")
    target_node: str = Field(..., description="Target node name")
    source_output: str = Field("main", description="Source output name")
    target_input: str = Field("main", description="Target input name")
    connection_type: Literal["normal", "subnode"] = Field(
        "normal", description="Connection type: normal for data flow, subnode for configuration"
    )
    slot_name: str | None = Field(None, description="Slot name for subnode connections")
    waypoints: list[dict[str, float]] | None = Field(None, description="Manual edge routing waypoints [{x, y}]")

    class Config:
        json_schema_extra = {
            "example": {
                "source_node": "start",
                "target_node": "http_request_1",
                "source_output": "main",
                "target_input": "main",
                "connection_type": "normal",
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
            connection_type=self.connection_type,
            slot_name=self.slot_name,
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
    # For ad-hoc execution with input
    input_data: dict[str, Any] | None = Field(None, description="Input data for ad-hoc execution")

    def to_workflow(self) -> Workflow:
        """Convert to engine Workflow dataclass."""
        from ..engine.types import Workflow as WF
        return WF(
            name=self.name,
            nodes=[n.to_dataclass() for n in self.nodes],
            connections=[c.to_dataclass() for c in self.connections],
            description=self.description,
            settings=self.settings,
        )


class WorkflowUpdateRequest(BaseModel):
    """Request schema for updating a workflow."""

    name: str | None = Field(None, min_length=1, max_length=255, description="Workflow name")
    nodes: list[NodeDefinitionSchema] | None = Field(None, description="List of nodes")
    connections: list[ConnectionSchema] | None = Field(None, description="List of connections")
    description: str | None = Field(None, max_length=1000, description="Workflow description")
    settings: dict[str, Any] | None = Field(None, description="Workflow settings")


class ActiveToggleRequest(BaseModel):
    """Request schema for toggling workflow active state."""

    active: bool = Field(..., description="Whether the workflow should be active")


class WorkflowResponse(BaseModel):
    """Response schema for workflow creation."""

    id: str
    name: str
    active: bool
    webhook_url: str
    created_at: str


class WorkflowListItem(BaseModel):
    """Schema for workflow in list response."""

    id: str
    name: str
    active: bool
    webhook_url: str
    node_count: int
    created_at: str
    updated_at: str


class WorkflowDetailResponse(BaseModel):
    """Detailed workflow response."""

    id: str
    name: str
    active: bool
    webhook_url: str
    definition: dict[str, Any]
    created_at: str
    updated_at: str


class WorkflowActiveResponse(BaseModel):
    """Response for active toggle."""

    id: str
    active: bool
