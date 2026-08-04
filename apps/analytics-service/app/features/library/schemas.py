"""Library feature schemas — saved analytics, runs, publish, lineage."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

Kind = Literal["sample", "aggregate", "profile"]


class VersionSelector(BaseModel):
    """Which version a saved definition runs against."""

    mode: Literal["current", "tag", "version"] = "current"
    tag: str | None = None
    version_number: int | None = None

    @model_validator(mode="after")
    def _check(self):
        if self.mode == "tag" and not self.tag:
            raise ValueError("mode=tag requires tag")
        if self.mode == "version" and self.version_number is None:
            raise ValueError("mode=version requires version_number")
        return self


class DefinitionCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    kind: Kind
    version_selector: VersionSelector = Field(default_factory=VersionSelector)
    sheet: str | None = Field(default=None, description="Sheet for multi-sheet datasets")
    params: dict[str, Any] = Field(
        default_factory=dict,
        description="Body of the underlying /sample, /aggregate, or /profile request "
                    "(minus dataset/version/sheet, which come from the definition)")


class DefinitionUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    version_selector: VersionSelector | None = None
    sheet: str | None = None
    params: dict[str, Any] | None = None


class DefinitionOut(BaseModel):
    id: str
    dataset_id: str
    name: str
    description: str | None = None
    kind: str
    version_selector: dict[str, Any]
    sheet: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    created_by: str | None = None
    created_at: str
    updated_at: str


class AnalyticsRunOut(BaseModel):
    id: str
    definition_id: str
    dataset_version_id: str | None = None
    job_id: str | None = None
    status: str
    result_summary: dict[str, Any] | None = None
    artifact_id: str | None = None
    triggered_by: str | None = None
    started_at: str
    completed_at: str | None = None
    error: str | None = None


class RunResponse(AnalyticsRunOut):
    """A completed run plus the inline result of the underlying operation."""

    result: dict[str, Any] | None = None


class PublishRequest(BaseModel):
    mode: Literal["new_dataset", "new_version"]
    name: str | None = Field(
        default=None, max_length=255,
        description="Name for the new dataset (new_dataset mode only)")


class PublishResponse(BaseModel):
    dataset_id: str
    dataset_name: str
    version_id: str
    version_number: int
    mode: str


class LineageResponse(BaseModel):
    dataset_id: str
    parents: list[dict[str, Any]] = Field(default_factory=list)
    children: list[dict[str, Any]] = Field(default_factory=list)
