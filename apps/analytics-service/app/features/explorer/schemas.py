"""Explorer schemas — raw-SQL escape hatch, column explorer, saved views."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from app.features.data_accelerator.schemas import ColumnProfile, TopValue
from app.shared.query import QueryPage, QuerySpec


class SqlQueryRequest(BaseModel):
    """One ad-hoc SELECT over the sheets of a version."""

    sql: str = Field(..., min_length=1, max_length=100_000,
                     description="A single SELECT; sheets are tables named by sheet_key")


class SqlQueryResponse(BaseModel):
    """Result rows plus the persisted parquet in the artifact area."""

    columns: list[str] = Field(description="Result column names, in order")
    items: list[dict[str, Any]] = Field(description="Result rows (row-capped)")
    row_count: int = Field(description="Rows returned (after the cap)")
    truncated: bool = Field(description="True when the row cap cut the result")
    tables: list[str] = Field(description="Sheet tables that were queryable")
    result_file: str = Field(description="Persisted parquet filename under /samples")


class VersionSelector(BaseModel):
    """Which version a saved view runs against (same shape as the library's)."""

    mode: Literal["current", "tag", "version"] = "current"
    tag: str | None = None
    version_number: int | None = None

    @model_validator(mode="after")
    def _check_target(self):
        if self.mode == "tag" and not self.tag:
            raise ValueError("mode 'tag' requires a tag")
        if self.mode == "version" and self.version_number is None:
            raise ValueError("mode 'version' requires a version_number")
        return self


class DatasetViewIn(BaseModel):
    """Create a saved view over one (logical) sheet."""

    name: str = Field(..., min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    sheet: str = Field(..., min_length=1,
                       description="Sheet name or sheet_key (resolved to its logical sheet)")
    version_selector: VersionSelector = Field(default_factory=VersionSelector)
    query: QuerySpec = Field(default_factory=QuerySpec)


class DatasetViewUpdate(BaseModel):
    """Partial update; omitted fields keep their value."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    sheet: str | None = Field(default=None, min_length=1)
    version_selector: VersionSelector | None = None
    query: QuerySpec | None = None


class DatasetViewOut(BaseModel):
    id: str
    dataset_id: str
    logical_sheet_id: str
    sheet_key: str | None = None
    sheet_name: str | None = None
    name: str
    description: str | None = None
    version_selector: dict[str, Any]
    query: dict[str, Any]
    created_by: str | None = None
    created_at: str
    updated_at: str


class RunViewRequest(BaseModel):
    """Paging overrides for running a saved view."""

    cursor: str | None = None
    limit: int | None = Field(default=None, ge=1, le=1000)


class ViewRunResponse(BaseModel):
    """A saved view executed against its selector-pinned version."""

    view_id: str
    version_number: int
    sheet_name: str
    result: QueryPage


class InsightOut(BaseModel):
    """One deterministic finding over a sheet's profile."""

    rule: str
    severity: str
    column_name: str | None = None
    message: str
    evidence: dict[str, Any] = Field(default_factory=dict)


class ProfileRunOut(BaseModel):
    """A persisted per-sheet profile run (profile JSON omitted; see detail)."""

    id: str
    dataset_id: str
    dataset_version_id: str
    logical_sheet_id: str
    sheet_name: str | None = None
    job_id: str | None = None
    status: str
    algorithm_version: int
    error: str | None = None
    started_at: str
    completed_at: str | None = None
    insights: list[InsightOut] = Field(default_factory=list)


class ProfileRunDetail(ProfileRunOut):
    """Run plus the full persisted profile JSON."""

    profile: dict[str, Any] | None = None


class DuplicateGroup(BaseModel):
    """One set of rows sharing the same values on the grouped columns."""

    key: dict[str, Any] = Field(description="Normalized column -> shared value")
    count: int
    examples: list[dict[str, Any]] = Field(
        default_factory=list, description="A few full rows from the group")


class DuplicatesResponse(BaseModel):
    """Duplicate-row groups for one sheet (§16); read-only, no remediation."""

    sheet_name: str
    columns: list[str] = Field(description="Normalized columns grouped on")
    exact: bool = Field(description="True when grouped on every column")
    row_count: int
    group_count: int = Field(description="Total duplicate groups (before cap)")
    duplicate_rows: int = Field(description="Total rows inside duplicate groups")
    groups: list[DuplicateGroup] = Field(default_factory=list)
    truncated: bool = False
    masked_columns: list[str] = Field(
        default_factory=list,
        description="Normalized columns whose values are masked for this caller; "
                    "in group keys they are replaced by a stable pseudonym so "
                    "distinct groups stay distinguishable")


class ColumnMissing(BaseModel):
    """Missingness of one column."""

    column: str = Field(description="Normalized column name")
    null_count: int
    null_percent: float


class MissingRow(BaseModel):
    """A row ranked by how many of its values are null."""

    null_count: int
    row: dict[str, Any]


class MissingResponse(BaseModel):
    """Per-column missingness + rows-most-missing probe for one sheet (§16)."""

    sheet_name: str
    source: str = Field(description='"profile_run" or "computed"')
    profile_run_id: str | None = None
    row_count: int
    columns: list[ColumnMissing] = Field(
        default_factory=list, description="All columns, worst null rate first")
    rows_most_missing: list[MissingRow] = Field(
        default_factory=list, description="Rows with nulls, most nulls first")
    masked_columns: list[str] = Field(
        default_factory=list,
        description="Normalized columns whose values are masked for this caller")


class ColumnExplorerResponse(ColumnProfile):
    """Full per-column statistics (§7) — the profile plus explorer extras."""

    normalized_name: str = Field(description="Normalized (snake_case) column name")
    sheet_name: str = Field(description="Sheet the column belongs to")
    uniqueness: float | None = Field(
        default=None, description="unique_count / non-null count; None when all null")
    is_candidate_key: bool = Field(
        description="True when non-null and unique across every row")
    rare_values: list[TopValue] = Field(
        default_factory=list, description="Least frequent non-null values")
    examples: list[Any] = Field(
        default_factory=list, description="A few distinct non-null example values")
    masked_columns: list[str] = Field(
        default_factory=list,
        description="[this column] when the caller may only see it masked; the "
                    "value-bearing statistics (min/max/quantiles/histogram) are "
                    "then withheld rather than masked")
