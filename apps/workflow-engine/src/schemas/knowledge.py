"""Request and response shapes of the Knowledge API (CONTRACTS-CAPGRAPH.md section 7)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class NodeView(BaseModel):
    id: str
    key: str
    kind: str
    subkind: str | None = None
    scope: str
    visibility: str
    team_id: str | None = None
    project_id: str | None = None
    label: str
    summary: str = ""
    aliases: list[str] = Field(default_factory=list)
    #: the graph listing leaves out the heavy pattern props (``skeleton``, ``reference_bundle``); NodeDetail has them in full
    props: dict[str, Any] = Field(default_factory=dict)
    source: str
    status: str
    validated_count: int = 0
    last_validated_at: datetime | None = None
    updated_at: datetime | None = None


class EdgeView(BaseModel):
    id: int
    src_id: str
    rel: str
    dst_id: str
    props: dict[str, Any] = Field(default_factory=dict)
    source: str


class ProjectRef(BaseModel):
    id: str
    slug: str
    name: str


class GraphResponse(BaseModel):
    nodes: list[NodeView]
    edges: list[EdgeView]
    truncated: bool = False
    #: team mode only: the team's projects that own at least one returned node (always ``[]`` in project mode)
    projects: list[ProjectRef] = Field(default_factory=list)


class NodeRef(BaseModel):
    id: str
    key: str
    label: str
    kind: str
    subkind: str | None = None


class Neighbour(BaseModel):
    direction: str  # "out": this node is the source; "in": this node is the target
    node: NodeRef
    props: dict[str, Any] = Field(default_factory=dict)
    source: str


class LearnEvent(BaseModel):
    id: int
    at: datetime
    project_id: str | None = None
    team_id: str | None = None
    actor: str | None = None
    trigger: str
    proposal_id: str | None = None
    summary: str
    payload: dict[str, Any] = Field(default_factory=dict)


class NodeDetail(BaseModel):
    node: NodeView
    #: relation -> neighbours (both directions), only nodes this project can see
    neighbours: dict[str, list[Neighbour]]
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    learn_events: list[LearnEvent] = Field(default_factory=list)


class MatrixRow(BaseModel):
    concept: NodeRef
    #: capability subkind (node_type, api_operation, workflow, subworkflow, template, ...) -> how many implement the concept
    implemented_by: dict[str, int]
    implemented_total: int
    patterns: int = 0           # validated patterns that implement it
    validated: int = 0          # the sum of their validated_count
    apps: int = 0
    teams: list[str] = Field(default_factory=list)
    roles: list[NodeRef] = Field(default_factory=list)
    gap: bool = False


class MatrixTotals(BaseModel):
    concepts: int
    implemented: int
    gaps: int
    patterns: int
    validated: int
    apps: int
    subworkflows: int


class CapabilitiesResponse(BaseModel):
    rows: list[MatrixRow]
    totals: MatrixTotals


class MemoryResponse(BaseModel):
    items: list[LearnEvent]
    next_cursor: str | None = None


#: the studio's default team (the same default the other team-scoped routes use)
DEFAULT_TEAM = "default"


class UnderstandRequest(BaseModel):
    #: absent: team mode over ``team_id``; when both are given, ``project_id`` wins
    project_id: str | None = None
    team_id: str = DEFAULT_TEAM
    request: str = Field(max_length=4000)


class RebuildRequest(BaseModel):
    #: absent: rebuild every project of ``team_id`` (after the global seed); when both are given, ``project_id`` wins
    project_id: str | None = None
    team_id: str = DEFAULT_TEAM
    llm_tagging: bool = False


class ProjectRebuild(BaseModel):
    project_id: str
    added: int
    updated: int
    staled: int


class RebuildResponse(BaseModel):
    added: int
    updated: int
    staled: int
    tagged: int
    proposed: int
    warnings: list[str] = Field(default_factory=list)
    #: team mode only: one entry per project of the team (always ``[]`` for a one-project rebuild)
    projects: list[ProjectRebuild] = Field(default_factory=list)


class DecideRequest(BaseModel):
    actor: str | None = Field(None, max_length=255)
