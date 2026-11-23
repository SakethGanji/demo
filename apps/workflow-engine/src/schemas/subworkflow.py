"""Schemas of the sub-workflow endpoints (DESIGN-STAGE8-REUSE section 11)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class InputField(BaseModel):
    name: str
    type: str = "any"
    required: bool = False
    description: str = ""


class SubworkflowListItem(BaseModel):
    """One row of the sub-workflow picker (``GET /projects/{p}/subworkflows``)."""

    id: str
    name: str
    description: str | None = None
    published_version: int | None = Field(None, description="version_number of the live version; null = never published")
    io_source: str = Field("draft", description="'published' when inputs/output come from the published version, else 'draft'")
    callable: bool
    reasons: list[str] = []
    inputs: list[InputField] = []
    output_fields: list[str] = []
    has_approval: bool = False
    used_by_count: int = 0


class SubworkflowIO(BaseModel):
    """``GET /workflows/{id}/io``: what the workflow takes and gives when another workflow calls it."""

    workflow_id: str
    version_number: int | None = Field(None, description="null = the draft")
    input: dict[str, Any]
    output: dict[str, Any]
    input_fields: list[InputField] = []
    output_fields: list[str] = []
    callable: bool
    reasons: list[str] = []
    findings: list[dict[str, Any]] = []


class UsedByRow(BaseModel):
    parent_workflow_id: str
    parent_name: str
    node: str
    version_spec: str
    where: str = Field(..., description="'draft' | 'published' | 'release'")
    release_ids: list[str] = []


class ExtractRequest(BaseModel):
    """``POST /workflows/{id}/extract-subworkflow``."""

    nodes: list[str] = Field(..., description="Names of the selected nodes")
    name: str = Field(..., min_length=1, max_length=200, description="Name of the new sub-workflow")
    dry_run: bool = Field(True, description="true (default): plan only, nothing is written")
    definition: dict[str, Any] | None = Field(
        None, description="The parent's unsaved definition; default: its saved draft. Saved (rewritten) when dry_run is false")


class ExtractResponse(BaseModel):
    plan: dict[str, Any]
    child_definition: dict[str, Any]
    parent_definition: dict[str, Any]
    warnings: list[dict[str, Any]] = []
    affected_tests: list[dict[str, Any]] = []
    # dry_run=false only
    child_workflow_id: str | None = None
    child_version_number: int | None = Field(None, description="1 when the new sub-workflow published; null = left as a draft")
    child_publish_errors: list[dict[str, Any]] = []
    call_version: str | None = Field(None, description="'latest' when the child published, else 'draft'")
