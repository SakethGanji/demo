"""Data accelerator schemas — datasets, tags, sampling, profiling, aggregation."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.shared.schemas import ColumnInfo, ColumnSummary

Classification = Literal["public", "internal", "confidential", "restricted"]


# ---------------------------------------------------------------------------
# Dataset management (moved from files schemas)
# ---------------------------------------------------------------------------

class DatasetInfo(BaseModel):
    """Summary info for a dataset in listings."""

    id: str
    name: str
    description: str | None = None
    classification: str = "internal"
    domain: str | None = None
    source_system: str | None = None
    refresh_frequency: str | None = None
    deprecated: bool = False
    is_favorite: bool = False
    current_version: int | None = None
    row_count: int | None = None
    size_bytes: int | None = None
    created_at: str
    updated_at: str


class VersionInfo(BaseModel):
    """Summary info for a dataset version.

    ``row_count`` is the TOTAL across all sheets. ``checksum`` hashes the
    canonical parquet; ``source_checksum`` the exact uploaded bytes;
    ``manifest_checksum`` the ordered per-sheet artifact checksums (the
    version's content identity).
    """

    id: str
    version_number: int
    status: str
    size_bytes: int | None = None
    row_count: int | None = None
    sheet_count: int | None = None
    checksum: str | None = None
    source_checksum: str | None = None
    manifest_checksum: str | None = None
    created_at: str
    processed_at: str | None = None
    tags: list[str] = Field(default_factory=list)


class DeleteResponse(BaseModel):
    """Response for delete operations."""

    success: bool
    message: str
    deleted_keys: list[str] = Field(default_factory=list)


class UpdateDatasetRequest(BaseModel):
    """Patch a dataset's mutable metadata. Only provided fields change."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    classification: Classification | None = Field(
        default=None, description="Data sensitivity: public, internal, confidential, or restricted",
    )
    domain: str | None = Field(default=None, max_length=255)
    source_system: str | None = Field(default=None, max_length=255)
    refresh_frequency: str | None = Field(
        default=None, max_length=100, description='e.g. "daily", "monthly", "ad-hoc"')
    deprecated: bool | None = None
    deprecation_reason: str | None = Field(default=None, max_length=2000)
    metadata: dict[str, Any] | None = Field(
        default=None, description="Free-form key/value metadata (replaces the whole object)")


class DatasetPatched(BaseModel):
    """Result of a dataset metadata patch."""

    id: str
    name: str
    description: str | None = None
    classification: str = "internal"
    domain: str | None = None
    source_system: str | None = None
    refresh_frequency: str | None = None
    deprecated: bool = False
    deprecation_reason: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str


class DatasetSearchResult(BaseModel):
    """A dataset with its versions inline, returned from search."""

    id: str
    name: str
    description: str | None = None
    current_version_id: str | None = None
    created_at: str
    updated_at: str
    versions: list[VersionInfo] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Tags
# ---------------------------------------------------------------------------

class TagInfo(BaseModel):
    """A tag pointing to a specific dataset version."""

    tag_name: str
    version_id: str
    version_number: int
    created_at: str
    updated_at: str


class SetTagRequest(BaseModel):
    """Request to create or move a tag."""

    tag_name: str = Field(..., min_length=1, max_length=128)
    version_id: str | None = Field(default=None, description="Target version UUID")
    version_number: int | None = Field(default=None, description="Target version number (alternative to version_id)")

    @field_validator("tag_name")
    @classmethod
    def validate_tag_name(cls, v: str) -> str:
        # Tags are case-insensitive slugs: 'Production' and 'production' must
        # be the same tag (enforced by a DB CHECK on the normalized form).
        v = v.strip().lower()
        if not v:
            raise ValueError("tag_name cannot be empty")
        return v


class PromoteTagRequest(BaseModel):
    """Promote a tag to a specific (ready) version, with an audit reason."""

    version_id: str | None = Field(default=None, description="Target version UUID")
    version_number: int | None = Field(default=None, description="Target version number (alternative to version_id)")
    reason: str | None = Field(default=None, max_length=2000, description="Why this version is being promoted")


class RollbackTagRequest(BaseModel):
    """Roll a tag back to the version it previously pointed at."""

    reason: str | None = Field(default=None, max_length=2000, description="Why the tag is being rolled back")


class TagOpResponse(BaseModel):
    """Result of an explicit tag operation (promote / rollback)."""

    tag_name: str
    action: str
    from_version_number: int | None = None
    to_version_number: int
    reason: str | None = None


class TagHistoryEntry(BaseModel):
    """One recorded tag transition."""

    id: int
    tag_name: str
    action: str
    from_version_number: int | None = None
    to_version_number: int | None = None
    reason: str | None = None
    actor_user_id: str | None = None
    actor_email: str | None = None
    request_id: str | None = None
    created_at: str


# ---------------------------------------------------------------------------
# Datasets (metadata)
# ---------------------------------------------------------------------------

class SheetSummary(BaseModel):
    """Summary of a single sheet within a dataset."""
    name: str
    sheet_key: str | None = None
    storage_key: str | None = None
    row_count: int
    column_count: int
    is_default: bool = False
    visibility: str = "visible"
    status: str = "ready"


class SheetColumn(BaseModel):
    """One column of a sheet's captured schema."""
    name: str
    original_name: str | None = None
    normalized_name: str
    dtype: str
    nullable: bool = True
    position: int
    header_was_duplicated: bool = False
    generated_name: bool = False


class SheetMetadataResponse(BaseModel):
    """Full metadata for a single sheet (schema served from Postgres)."""
    name: str
    sheet_key: str | None = None
    visibility: str = "visible"
    status: str = "ready"
    is_default: bool = False
    row_count: int
    column_count: int
    size_bytes: int | None = None
    checksum: str | None = None
    schema_fingerprint: str | None = None
    columns: list[SheetColumn] = Field(default_factory=list)
    preview: list[dict[str, Any]] | None = None


class DatasetMetadataResponse(BaseModel):
    dataset_id: str
    file_path: str
    row_count: int
    column_count: int
    columns: list[ColumnInfo]
    preview: list[dict[str, Any]]
    sheets: list[SheetSummary] | None = None
    default_sheet: str | None = None


# ---------------------------------------------------------------------------
# Diffs (workbook-level and sheet-level)
# ---------------------------------------------------------------------------

class RenameCandidate(BaseModel):
    """A *suggested* sheet rename — never auto-declared, always advisory."""

    from_sheet: str
    to_sheet: str
    confidence: Literal["high", "medium"]
    reason: str


class ModifiedSheet(BaseModel):
    """A sheet present in both versions whose content or shape changed."""

    sheet_key: str
    from_sheet: str
    to_sheet: str
    schema_changed: bool
    row_count_delta: int | None = None
    visibility_changed: bool = False


class WorkbookDiffResponse(BaseModel):
    """Workbook-level diff between two versions of a dataset."""

    dataset_id: str
    from_version: int
    to_version: int
    added: list[SheetSummary] = Field(default_factory=list)
    removed: list[SheetSummary] = Field(default_factory=list)
    modified: list[ModifiedSheet] = Field(default_factory=list)
    unchanged: list[str] = Field(default_factory=list)
    rename_candidates: list[RenameCandidate] = Field(
        default_factory=list,
        description="Advisory only: removed+added pairs with matching schema fingerprints",
    )


class ColumnTypeChange(BaseModel):
    column: str
    from_dtype: str
    to_dtype: str


class ColumnNullabilityChange(BaseModel):
    column: str
    from_nullable: bool
    to_nullable: bool


class ColumnOrderChange(BaseModel):
    column: str
    from_position: int
    to_position: int


class SheetDiffResponse(BaseModel):
    """Column-level schema diff for one sheet across two versions."""

    dataset_id: str
    sheet_key: str
    from_sheet: str
    to_sheet: str
    from_version: int
    to_version: int
    identical: bool
    added_columns: list[SheetColumn] = Field(default_factory=list)
    removed_columns: list[SheetColumn] = Field(default_factory=list)
    type_changes: list[ColumnTypeChange] = Field(default_factory=list)
    nullability_changes: list[ColumnNullabilityChange] = Field(default_factory=list)
    order_changes: list[ColumnOrderChange] = Field(default_factory=list)
    from_row_count: int | None = None
    to_row_count: int | None = None
    row_count_delta: int | None = None


# ---------------------------------------------------------------------------
# Sampling
# ---------------------------------------------------------------------------

class DistributionGoals(BaseModel):
    """Target distribution constraints for the final sample."""

    column: str = Field(..., description="Column to apply distribution constraints on")
    class_minimums: dict[str, int] | None = Field(
        default=None, description="Min samples per class value, e.g. {'urgent': 50, 'pending': 30}",
    )
    target_distribution: dict[str, float] | None = Field(
        default=None, description="Target percentage per class (must sum to ~1.0), e.g. {'completed': 0.7, 'pending': 0.3}",
    )


class SamplingStep(BaseModel):
    """A single step in a sampling pipeline."""

    method: str = Field(
        ..., description="Sampling method: random, stratified, systematic, cluster, weighted, time_stratified, llm_semantic, deduplicate",
    )
    sample_size: int | None = Field(default=None, description="Number of rows to sample in this step")
    sample_fraction: float | None = Field(default=None, description="Fraction of remaining pool to sample")
    replace: bool = Field(default=False, description="Sample with replacement (allows duplicates)")
    rounds: int = Field(default=1, description="Number of rounds to repeat this step (each round draws from remaining pool, or full pool if replace=True)")
    filters: list[dict[str, Any]] | None = Field(
        default=None,
        description="Structured filters applied before sampling. Each is a Filter (column/op/value) or FilterGroup (logic/conditions).",
    )
    filter_expr: str | None = Field(default=None, description="Raw SQL WHERE filter (fallback), e.g. \"region = 'US'\"")
    # Stratified
    stratify_column: str | None = Field(default=None, description="Column for stratified sampling")
    class_targets: dict[str, int] | None = Field(
        default=None, description="Per-class sample counts for stratified, e.g. {'urgent': 50, 'normal': 100}",
    )
    # Cluster
    cluster_column: str | None = Field(default=None, description="Column for cluster sampling")
    num_clusters: int | None = Field(default=None, description="Number of clusters to select")
    # Weighted
    weight_column: str | None = Field(default=None, description="Column with numeric weights for weighted sampling (higher = more likely)")
    # Time-stratified
    time_column: str | None = Field(default=None, description="Date/datetime column for time-stratified sampling")
    time_bins: int = Field(default=10, description="Number of equal time bins to stratify across")
    # LLM semantic
    text_column: str | None = Field(default=None, description="Column containing text for LLM semantic sampling")
    strategy: str = Field(default="diverse", description="LLM sampling strategy: 'diverse', 'edge_cases', or 'similarity_search'")
    llm_provider: str = Field(default="openai", description="Embedding provider: 'openai' or 'gemini'")
    llm_api_url: str | None = Field(default=None, description="Embeddings API URL (OpenAI-compatible, overrides provider)")
    llm_api_key: str | None = Field(default=None, description="API key for embeddings endpoint (overrides provider)")
    llm_model: str | None = Field(default=None, description="Embedding model name")
    llm_query: str | None = Field(default=None, description="Query text for similarity_search strategy")
    # Deduplicate
    deduplicate_columns: list[str] | None = Field(default=None, description="Columns to deduplicate on (all columns if not specified)")


class StepResult(BaseModel):
    """Summary of one sampling step's execution."""

    step_index: int
    method: str
    rows_selected: int
    pool_before: int
    pool_after: int
    rounds_completed: int = 1
    per_round_counts: list[int] | None = None
    class_counts: dict[str, int] | None = None
    filter_applied: str | None = None
    filter_matched: int | None = None
    warnings: list[str] = Field(default_factory=list)


class GoalValidationResult(BaseModel):
    """Result of validating the final sample against distribution goals."""

    met: bool = Field(..., description="Whether all goals were met")
    target_total_volume: int | None = None
    actual_total: int = 0
    class_minimum_results: dict[str, dict[str, Any]] | None = None
    distribution_results: dict[str, dict[str, Any]] | None = None
    warnings: list[str] = Field(default_factory=list)


class ReproducibilityInfo(BaseModel):
    """Metadata to reproduce the exact same sampling run."""

    seed: int | None = None
    target_total_volume: int = 0
    steps_config: list[dict[str, Any]] = Field(default_factory=list)
    distribution_goals: dict[str, Any] | None = None
    post_processing: dict[str, Any] = Field(default_factory=dict)
    timestamp: str = ""


class SampleRequest(BaseModel):
    """Request model for sampling endpoint."""

    file_path: str | None = Field(default=None, description="Path to CSV or Parquet file")
    dataset_id: str | None = Field(default=None, description="Reference to a previously uploaded dataset")
    version_id: str | None = Field(default=None, description="Target a specific version by UUID")
    version_number: int | None = Field(default=None, description="Target a specific version by number")
    tag: str | None = Field(default=None, description="Target a version by tag name (e.g. 'production')")
    sheet: str | None = Field(default=None, description="Sheet name for multi-sheet datasets")
    data: list[dict[str, Any]] | None = Field(default=None, description="Inline JSON array of data")
    target_total_volume: int = Field(..., description="Target total number of rows in the final sample")
    sampling_steps: list[SamplingStep] = Field(..., description="Ordered list of sampling steps to execute sequentially")
    distribution_goals: DistributionGoals | None = Field(default=None, description="Distribution constraints for the final sample")
    seed: int | None = Field(default=None, description="Random seed for reproducibility")
    return_data: bool = Field(default=True, description="Include sampled rows in response (set False for large datasets)")
    # Post-processing
    deduplicate: bool = Field(default=False, description="Remove duplicate rows from final sample")
    deduplicate_columns: list[str] | None = Field(default=None, description="Columns to deduplicate on (all columns if not specified)")
    shuffle: bool = Field(default=False, description="Randomly shuffle the final output rows")
    sort_by: str | None = Field(default=None, description="Column to sort the final output by")
    sort_descending: bool = Field(default=False, description="Sort in descending order")


class SampleResponse(BaseModel):
    """Response model for sampling endpoint."""

    success: bool
    original_count: int
    sampled_count: int
    columns: list[ColumnSummary] = []
    preview: list[dict[str, Any]] = []
    sample_file: str | None = Field(default=None, description="Saved sample filename — fetch via GET /api/v1/samples/{filename}")
    data: list[dict[str, Any]] | None = None
    steps_summary: list[StepResult] = []
    goal_validation: GoalValidationResult | None = None
    reproducibility: ReproducibilityInfo | None = None


# ---------------------------------------------------------------------------
# Profiling
# ---------------------------------------------------------------------------

class TopValue(BaseModel):
    """A frequent value in a column."""

    value: Any
    count: int
    percent: float


class HistogramBin(BaseModel):
    """A histogram bin."""

    bin_start: float
    bin_end: float
    count: int


class ColumnProfile(BaseModel):
    """Profile statistics for a single column."""

    name: str
    dtype: str  # numeric, categorical, datetime, boolean, text
    count: int
    null_count: int
    null_percent: float
    unique_count: int
    top_values: list[TopValue]
    # Numeric-only
    mean: float | None = None
    median: float | None = None
    std: float | None = None
    min: float | None = None
    max: float | None = None
    q25: float | None = None
    q75: float | None = None
    # Datetime-only
    min_date: str | None = None
    max_date: str | None = None
    # Text-only
    avg_length: float | None = None
    min_length: int | None = None
    max_length: int | None = None
    # Histogram
    histogram: list[HistogramBin] | None = None


class ProfileRequest(BaseModel):
    """Request model for profiling endpoint."""

    file_path: str | None = Field(default=None, description="Path to data file")
    dataset_id: str | None = Field(default=None, description="Reference to a previously uploaded dataset")
    version_id: str | None = Field(default=None, description="Target a specific version by UUID")
    version_number: int | None = Field(default=None, description="Target a specific version by number")
    tag: str | None = Field(default=None, description="Target a version by tag name (e.g. 'production')")
    sheet: str | None = Field(default=None, description="Sheet name for multi-sheet datasets")
    data: list[dict[str, Any]] | None = Field(default=None, description="Inline JSON array of data")
    columns: list[str] | None = Field(default=None, description="Columns to profile (None = all)")
    include_histograms: bool = Field(default=True, description="Include histograms for numeric columns")
    include_correlations: bool = Field(default=False, description="Include correlation matrix")
    include_duplicates: bool = Field(default=True, description="Count duplicate rows (expensive on large datasets)")
    top_n: int = Field(default=10, description="Number of top values to return per column")


class ProfileResponse(BaseModel):
    """Response model for profiling endpoint."""

    success: bool
    row_count: int
    column_count: int
    columns: list[ColumnProfile]
    correlations: dict[str, dict[str, float]] | None = None
    memory_usage_bytes: int
    duplicate_row_count: int


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

class AggregationSpec(BaseModel):
    """Specification for a single aggregation."""

    column: str
    function: str = Field(
        description="Aggregation function: sum, mean, median, count, min, max, std, nunique, first, last"
    )
    alias: str | None = Field(default=None, description="Output column name (defaults to column_function)")


class JoinSpec(BaseModel):
    """Relationship-based join with another sheet of the same version.

    Not general SQL: one equi-join, keyed by declared columns. Colliding
    non-key columns from the joined sheet get a ``{sheet}_`` prefix.
    """

    sheet: str = Field(..., description="Sheet (name or sheet_key) to join with")
    left_on: str = Field(..., description="Join column on the base sheet")
    right_on: str = Field(..., description="Join column on the joined sheet")
    how: Literal["inner", "left"] = "inner"


class AggregateRequest(BaseModel):
    """Request model for aggregation endpoint."""

    file_path: str | None = Field(default=None, description="Path to data file")
    dataset_id: str | None = Field(default=None, description="Reference to a previously uploaded dataset")
    version_id: str | None = Field(default=None, description="Target a specific version by UUID")
    version_number: int | None = Field(default=None, description="Target a specific version by number")
    tag: str | None = Field(default=None, description="Target a version by tag name (e.g. 'production')")
    sheet: str | None = Field(default=None, description="Sheet name for multi-sheet datasets")
    data: list[dict[str, Any]] | None = Field(default=None, description="Inline JSON array of data")
    join: JoinSpec | None = Field(
        default=None,
        description="Join another sheet of the same dataset version before aggregating")
    group_by: list[str] = Field(description="Columns to group by")
    aggregations: list[AggregationSpec] = Field(description="Aggregation specifications")
    sort_by: str | None = Field(default=None, description="Column to sort results by")
    sort_order: str = Field(default="desc", description="Sort order: asc or desc")
    limit: int | None = Field(default=None, description="Maximum number of groups to return")
    filter_expr: str | None = Field(
        default=None,
        description="SQL WHERE clause expression to filter data before aggregation (e.g. 'sales >= 100')",
    )
    return_data: bool = Field(default=True, description="Include aggregated rows in response (set False for large results)")


class AggregateResponse(BaseModel):
    """Response model for aggregation endpoint."""

    success: bool
    original_count: int
    group_count: int
    columns: list[str]
    data: list[dict[str, Any]] | None = None
    totals: dict[str, Any] | None = None
    result_file: str | None = Field(default=None, description="Saved result filename — fetch via GET /api/v1/samples/{filename}")
