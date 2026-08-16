"""Request/response models for the transformation feature (ROADMAP §19–§21)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.features.library.schemas import VersionSelector

from .steps import MAX_PIPELINE_STEPS, TransformStep

DEFAULT_PREVIEW_ROWS_DOC = (
    "Rows to sample from the source before applying the pipeline — the preview "
    "is approximate by construction and never scans the whole sheet")

# Preview reads a bounded sample, never the whole sheet. They live here, not in
# the service, because the request models bound on them too.
DEFAULT_PREVIEW_ROWS = 50
MAX_PREVIEW_ROWS = 500


class TransformationCreate(BaseModel):
    """A new saved pipeline over one logical sheet."""

    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    sheet: str | None = Field(
        default=None,
        description="Sheet the pipeline reads; pinned by logical identity, so renames "
                    "don't break it. Required when the version has several sheets")
    version_selector: VersionSelector = Field(default_factory=VersionSelector)
    steps: list[TransformStep] = Field(
        default_factory=list, max_length=MAX_PIPELINE_STEPS,
        description="Ordered pipeline; validated against the sheet schema before it is saved")


class TransformationUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    sheet: str | None = Field(default=None, min_length=1)
    version_selector: VersionSelector | None = None
    steps: list[TransformStep] | None = Field(default=None, max_length=MAX_PIPELINE_STEPS)


class TransformationCompile(BaseModel):
    """An UNSAVED pipeline, compiled (and optionally sampled) without persisting.

    Same shape as :class:`TransformationCreate` minus ``name`` — a builder UI
    asks this question on every keystroke, long before the user has decided what
    to call the thing, and must not have to create a throwaway definition (and
    trip the per-dataset name uniqueness) to find out whether step 3 is valid.
    """

    sheet: str | None = Field(
        default=None,
        description="Sheet the pipeline reads. Required when the version has several")
    version_selector: VersionSelector = Field(default_factory=VersionSelector)
    steps: list[TransformStep] = Field(
        default_factory=list, max_length=MAX_PIPELINE_STEPS)
    rows: int | None = Field(
        default=None, ge=1, le=MAX_PREVIEW_ROWS,
        description="When set, also return this many sampled result rows. "
                    "Omit to validate and fold the schema without touching data")


class TransformationOut(BaseModel):
    id: str
    dataset_id: str
    logical_sheet_id: str
    sheet_key: str | None = None
    name: str
    description: str | None = None
    version_selector: dict[str, Any] = Field(default_factory=dict)
    steps: list[dict[str, Any]] = Field(default_factory=list)
    created_by: str | None = None
    created_at: str
    updated_at: str


class OutputColumn(BaseModel):
    """One column of a pipeline's output schema, as folded by the compiler."""

    name: str
    normalized_name: str
    dtype: str
    position: int


class TransformPreview(BaseModel):
    """A sampled dry run — no job, no artifact, nothing persisted."""

    columns: list[str]
    rows: list[dict[str, Any]]
    approximate: bool = Field(
        default=True, description="Rows come from USING SAMPLE, not the whole sheet")
    output_schema: list[OutputColumn] = Field(default_factory=list)
    version_number: int
    sheet_name: str


class TransformationCompileResult(BaseModel):
    """The folded output schema of an unsaved pipeline, plus optional rows."""

    output_schema: list[OutputColumn] = Field(default_factory=list)
    step_schemas: list[list[OutputColumn]] = Field(
        default_factory=list,
        description="Columns as each step leaves them, in step order — what a "
                    "column picker for step N+1 must offer")
    columns: list[str] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    sampled: bool = Field(
        default=False, description="False when only the schema was compiled")
    version_number: int
    sheet_name: str


class TransformationRunOut(BaseModel):
    id: str
    definition_id: str
    dataset_version_id: str | None = None
    job_id: str | None = None
    status: str
    mode: str
    result_summary: dict[str, Any] | None = None
    artifact_id: str | None = None
    triggered_by: str | None = None
    started_at: str
    completed_at: str | None = None
    error: str | None = None
    # On the LIST model, not just the detail one: a run list without this can't
    # distinguish an already-published run, so the UI re-offers Publish and the
    # second attempt 409s.
    published_version_id: str | None = Field(
        default=None,
        description="Version this run was published as; null until it is published. "
                    "Publishing a run twice is a 409 — this is how a UI knows to "
                    "offer 'view published version' instead of 'publish'")
    published_dataset_id: str | None = None
    published_version_number: int | None = None


class TransformationRunDetail(TransformationRunOut):
    """A run plus its §21 auto-profile and drift-vs-source."""

    output_profile: dict[str, Any] | None = None
    source_drift: dict[str, Any] | None = None


class TransformPublishRequest(BaseModel):
    mode: Literal["new_dataset", "new_version"]
    name: str | None = Field(
        default=None, max_length=255,
        description="Name for the new dataset (new_dataset mode only)")
