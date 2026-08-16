"""Request/response models for relationships and the join builder (§22–§23)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class RelationshipOut(BaseModel):
    """One directed edge: (from sheet, column) references (to sheet, column)."""

    id: str
    dataset_id: str
    from_logical_sheet_id: str
    from_sheet: str | None = None
    from_column: str
    to_dataset_id: str
    to_logical_sheet_id: str
    to_sheet: str | None = None
    to_column: str
    status: str
    method: str
    evidence: dict[str, Any] = Field(default_factory=dict)
    confidence: float | None = None
    algorithm_version: int = 1
    created_by: str | None = None
    reviewed_by: str | None = None
    created_at: str
    updated_at: str


class RelationshipCreate(BaseModel):
    """Declare a relationship by hand (``method='manual'``, already confirmed)."""

    from_sheet: str | None = Field(
        default=None, description="Owning-side sheet; required for multi-sheet versions")
    from_column: str = Field(..., min_length=1, description="Column on the owning side")
    to_dataset_id: str | None = Field(
        default=None, description="Target dataset; defaults to this dataset")
    to_sheet: str | None = Field(default=None, description="Target sheet")
    to_column: str = Field(..., min_length=1, description="Column on the target side")
    confirmed: bool = Field(
        default=True, description="Manual edges are confirmed unless you say otherwise")


class SeedResponse(BaseModel):
    """Result of seeding edges from the dataset's foreign_key quality rules."""

    created: int
    relationships: list[RelationshipOut] = Field(default_factory=list)


class SuggestResponse(BaseModel):
    """Result of a statistical discovery run."""

    job_id: str | None = Field(
        default=None,
        description="The discovery job; poll GET /jobs/{id} when sync=false")
    pairs_examined: int = 0
    suggested: int = 0
    skipped: int = Field(
        default=0,
        description="Candidate pairs the cap kept from being probed — a "
                    "non-zero value means this run was not exhaustive")
    relationships: list[RelationshipOut] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# §23 join builder
# ---------------------------------------------------------------------------

class JoinBuildSpec(BaseModel):
    """A join driven by a CONFIRMED relationship — the only cross-dataset join."""

    relationship_id: str = Field(
        ..., description="Must be confirmed; supplies both sides and the keys")
    how: Literal["inner", "left"] = "inner"
    left_version: int | None = Field(
        default=None, description="Version of the owning side; defaults to current")
    right_version: int | None = Field(
        default=None, description="Version of the target side; defaults to current")
    select_columns: list[str] | None = Field(
        default=None, description="Projection over the joined result; null keeps everything")


class JoinWarnings(BaseModel):
    """What the join will actually do, measured before it runs.

    The point of the join builder: a user sees the row expansion and the
    unmatched rate BEFORE producing a result they would otherwise have to
    debug afterwards.
    """

    left_rows: int
    right_rows: int
    left_duplicate_keys: int = Field(
        default=0, description="Distinct key values repeated on the left")
    right_duplicate_keys: int = Field(default=0)
    many_to_many: bool = Field(
        default=False, description="Both sides repeat their key — output grows multiplicatively")
    estimated_output_rows: int = 0
    row_expansion_factor: float = Field(
        default=0.0, description="Output rows per left input row")
    unmatched_left_pct: float = 0.0
    unmatched_right_pct: float = 0.0
    column_collisions: list[str] = Field(
        default_factory=list, description="Non-key column names present on both sides")


class JoinPreview(BaseModel):
    """Pre-flight warnings plus a small sample of the joined rows."""

    warnings: JoinWarnings
    output_columns: list[str] = Field(default_factory=list)
    preview: list[dict[str, Any]] = Field(default_factory=list)
    relationship: RelationshipOut


class JoinExecuteResponse(BaseModel):
    """A materialized join output, registered as a `join_output` artifact."""

    run_id: str
    sample_file: str
    row_count: int
    warnings: JoinWarnings
    output_columns: list[str] = Field(default_factory=list)
    relationship: RelationshipOut


class JoinPublishRequest(BaseModel):
    mode: Literal["new_dataset", "new_version"] = "new_dataset"
    name: str | None = Field(default=None, max_length=255)
    dataset_id: str | None = Field(
        default=None,
        description="Target dataset for new_version mode; defaults to the join's left side")
