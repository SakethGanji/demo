"""Data accelerator schemas — datasets, tags, sampling, profiling, aggregation."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.shared.constants import AGG_FUNCTIONS, AGG_FUNCTIONS_TEXT
from app.shared.query import FilterGroup
from app.shared.schemas import ColumnInfo, ColumnSummary

Classification = Literal["public", "internal", "confidential", "restricted"]

#: The catalog signal vocabularies, exactly as
#: ``app.features.discovery.repo.signals_lateral`` computes them. Pinned here
#: so the facet counts, the ``GET /datasets`` filters and any MCP wrapper share
#: one definition instead of drifting into three bare ``str`` parameters that
#: silently match nothing.
ValidationStatus = Literal["passed", "failed", "none"]
DocumentationLevel = Literal["full", "partial", "none"]


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
    # §18 catalog signals (shared with /datasets/{id}/health).
    validation_status: str | None = None
    has_schema_drift: bool | None = None
    documentation: str | None = None
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


#: Columns of ``datasets`` that are NOT NULL. Sending one as an explicit null
#: is a client error, not a clear — the alternative is a constraint violation
#: surfacing as a 500.
_NON_NULLABLE_DATASET_FIELDS = ("name", "classification", "deprecated", "metadata")


class UpdateDatasetRequest(BaseModel):
    """Partial dataset-metadata update — the PATCH body, MERGE semantics.

    Only the fields **present** in the request body are written; anything you
    omit keeps its stored value. Sending a nullable field as an explicit
    ``null`` clears it — that is the only way to blank a ``description``,
    ``domain``, ``source_system``, ``refresh_frequency`` or
    ``deprecation_reason``. An empty body is a no-op that returns the record
    unchanged.

    ``name``, ``classification``, ``deprecated`` and ``metadata`` back NOT NULL
    columns, so an explicit ``null`` for those is rejected (422) rather than
    attempted. They are still typed optional because omitting them is the
    normal case.
    """

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

    @model_validator(mode="before")
    @classmethod
    def _reject_null_on_non_nullable(cls, data: Any) -> Any:
        """Explicit ``null`` on a NOT NULL column → 422, not a failed UPDATE.

        PATCH honours explicit nulls as clears, so these four have to be
        stopped here; there is nowhere later that can tell them apart from an
        omission.
        """
        if isinstance(data, dict):
            offenders = [f for f in _NON_NULLABLE_DATASET_FIELDS
                         if f in data and data[f] is None]
            if offenders:
                raise ValueError(
                    f"cannot be cleared (null): {', '.join(offenders)} — "
                    "omit the field to leave it unchanged, or send a new value")
        return data


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
        # Every other tag route takes the name as a PATH segment, and Starlette
        # decodes %2F before routing — so a tag containing '/' (or a control
        # char) could be created and then never read, promoted, rolled back, or
        # deleted. Refuse at creation rather than mint an unreachable tag.
        if "/" in v or "\\" in v or any(ch.isspace() for ch in v):
            raise ValueError(
                "tag_name cannot contain '/', '\\', or whitespace — tags are used "
                "as URL path segments")
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
    logical_sheet_id: str | None = None
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
    logical_sheet_id: str | None = None
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
    masked_columns: list[str] = Field(
        default_factory=list,
        description="Columns whose preview values were masked for this caller "
                    "because the data dictionary marks them sensitive.")


class DatasetMetadataResponse(BaseModel):
    dataset_id: str
    file_path: str
    row_count: int
    column_count: int
    columns: list[ColumnInfo]
    preview: list[dict[str, Any]]
    sheets: list[SheetSummary] | None = None
    default_sheet: str | None = None
    masked_columns: list[str] = Field(
        default_factory=list,
        description="Columns whose preview values were masked for this caller "
                    "because the data dictionary marks them sensitive.")


# ---------------------------------------------------------------------------
# Diffs (workbook-level and sheet-level)
# ---------------------------------------------------------------------------

class RenameCandidate(BaseModel):
    """A *suggested* sheet rename — never auto-declared, always advisory."""

    from_sheet: str
    to_sheet: str
    confidence: Literal["high", "medium"]
    reason: str


class RenamedSheet(BaseModel):
    """A rename the caller already CONFIRMED — one logical sheet, two keys.

    The physical ``sheet_key`` genuinely differs between the two versions, so
    both sides still appear in ``added``/``removed``; this entry is how the
    caller learns those two entries are the same sheet and must not be drawn
    as a disappearance plus an arrival.
    """

    logical_sheet_id: str
    from_sheet: str
    to_sheet: str
    from_sheet_key: str
    to_sheet_key: str


class ModifiedSheet(BaseModel):
    """A sheet present in both versions whose content or shape changed."""

    sheet_key: str
    from_sheet: str
    to_sheet: str
    schema_changed: bool
    row_count_delta: int | None = None
    visibility_changed: bool = False


class ColumnDrift(BaseModel):
    """Profile deltas for one column present in both versions (§9)."""

    column: str = Field(description="Physical column name, as profiled")
    from_null_percent: float | None = None
    to_null_percent: float | None = None
    null_percent_delta: float | None = None
    from_unique_count: int | None = None
    to_unique_count: int | None = None
    unique_count_delta: int | None = None
    mean_delta: float | None = Field(default=None, description="Numeric columns only")
    std_delta: float | None = Field(default=None, description="Numeric columns only")
    added_categories: list[str] = Field(
        default_factory=list, description="Top values new in the to-version")
    removed_categories: list[str] = Field(
        default_factory=list, description="Top values gone from the from-version")


class SheetProfileDrift(BaseModel):
    """Profile drift between two versions of one sheet, from persisted runs."""

    sheet_key: str | None = None
    from_row_count: int | None = None
    to_row_count: int | None = None
    row_count_delta: int | None = None
    from_duplicate_rows: int | None = None
    to_duplicate_rows: int | None = None
    duplicate_rows_delta: int | None = None
    columns: list[ColumnDrift] = Field(
        default_factory=list, description="Deltas for columns profiled in both versions")
    added_columns: list[str] = Field(
        default_factory=list, description="Columns profiled on the 'to' side only")
    removed_columns: list[str] = Field(
        default_factory=list, description="Columns profiled on the 'from' side only")


class RowDiffRequest(BaseModel):
    """Configuration for a keyed row-level diff."""

    key: list[str] | None = Field(
        default=None,
        description="Columns matching rows across versions. Defaults to the "
                    "sheet's declared primary key (see sheet-metadata).")
    columns: list[str] | None = Field(
        default=None,
        description="Columns to compare; defaults to every column present in "
                    "both versions except the key.")
    sample_limit: int = Field(
        default=20, ge=1, le=200,
        description="Rows per inline sample. The full diff is always written "
                    "to an artifact regardless of this.")


class ColumnChangeCount(BaseModel):
    column: str
    changed_rows: int


class RowDiffResponse(BaseModel):
    """Which rows changed between two versions, and to what.

    The inline samples are a preview; the complete cell-level diff is the
    `diff_file` artifact — one row per changed cell
    (change_type, row_key, column_name, before_value, after_value), fetchable
    via GET /samples/{diff_file}/data.
    """

    dataset_id: str
    from_version: int
    to_version: int
    sheet: str
    key: list[str]
    compared_columns: list[str] = Field(default_factory=list)

    added: int
    removed: int
    changed: int
    unchanged: int
    column_changes: list[ColumnChangeCount] = Field(
        default_factory=list,
        description="Matched rows whose value changed, per column, busiest first")

    added_sample: list[dict[str, Any]] = Field(default_factory=list)
    removed_sample: list[dict[str, Any]] = Field(default_factory=list)
    changed_sample: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Cell-level changes: row_key, column_name, before, after")

    diff_file: str | None = Field(
        default=None, description="Full cell-level diff; null when nothing changed")
    diff_artifact_id: str | None = None
    masked_columns: list[str] = Field(
        default_factory=list,
        description="Sensitive columns withheld from the inline samples")


class WorkbookDiffResponse(BaseModel):
    """Workbook-level diff between two versions of a dataset."""

    dataset_id: str
    from_version: int
    to_version: int
    added: list[SheetSummary] = Field(default_factory=list)
    removed: list[SheetSummary] = Field(default_factory=list)
    modified: list[ModifiedSheet] = Field(default_factory=list)
    unchanged: list[str] = Field(default_factory=list)
    renamed: list[RenamedSheet] = Field(
        default_factory=list,
        description=("Removed+added pairs already relinked by confirm-rename: "
                     "one logical sheet under two physical keys"),
    )
    rename_candidates: list[RenameCandidate] = Field(
        default_factory=list,
        description=("Advisory only: removed+added pairs with matching schema "
                     "fingerprints that are NOT already a confirmed rename"),
    )
    profile_drift: list[SheetProfileDrift] | None = Field(
        default=None,
        description="Per-sheet profile deltas (include=profile; sheets with runs on both sides)")
    profile_missing: list[str] | None = Field(
        default=None,
        description="Common sheet keys lacking a completed profile run on either side")


class ConfirmRenameRequest(BaseModel):
    """Confirm that *to_sheet* in this version is *from_sheet* renamed."""

    from_sheet: str = Field(description="Old sheet name (or sheet_key) being renamed away")
    to_sheet: str = Field(description="New sheet name (or sheet_key) in this version")
    force: bool = Field(
        default=False,
        description="Confirm even when the pair is not a diff rename candidate "
                    "(schema fingerprints differ, e.g. rename + schema change)",
    )


class ConfirmRenameResponse(BaseModel):
    dataset_id: str
    logical_sheet_id: str
    from_sheet: str
    to_sheet: str
    sheet_key: str
    was_candidate: bool
    forced: bool
    versions_relinked: int


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
    profile_drift: SheetProfileDrift | None = Field(
        default=None, description="Profile deltas (include=profile)")


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
    sample_size: int | None = Field(default=None, gt=0, description="Number of rows to sample in this step")
    sample_fraction: float | None = Field(default=None, gt=0, description="Fraction of remaining pool to sample")
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
    num_clusters: int | None = Field(
        default=None, gt=0,
        description="Number of clusters to select",
    )
    # Weighted
    weight_column: str | None = Field(default=None, description="Column with numeric weights for weighted sampling (higher = more likely)")
    # Time-stratified
    time_column: str | None = Field(default=None, description="Date/datetime column for time-stratified sampling")
    time_bins: int = Field(
        default=10, gt=0,
        description="Number of equal time bins to stratify across",
    )
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
    target_total_volume: int = Field(..., gt=0, description="Target total number of rows in the final sample")
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
# Coordinated cross-sheet sampling
# ---------------------------------------------------------------------------

class RelatedSheetLink(BaseModel):
    """One related sheet to keep referentially consistent with the sample.

    After the driver sheet is sampled, this sheet is filtered (semi-join) to
    only the rows whose ``right_on`` value appears in the parent's sampled
    ``left_on`` values. Keys are declared explicitly (same shape as ``JoinSpec``);
    there is no auto-discovered relationship graph.
    """

    sheet: str = Field(..., description="Related sheet (name or sheet_key) to filter by key")
    left_on: str | None = Field(
        default=None,
        description="Key column on the parent (driver or upstream related) sampled output. "
                    "Omit BOTH keys to resolve them from `relationship_id`, or failing that "
                    "from an enabled foreign_key quality rule linking this sheet to its parent.",
    )
    right_on: str | None = Field(
        default=None, description="Key column on this related sheet (omit with left_on)")
    parent_sheet: str | None = Field(
        default=None,
        description="Sheet whose sampled keys drive this filter; defaults to the driver sheet. "
                    "Set it to another related sheet's name to chain filters.",
    )
    relationship_id: str | None = Field(
        default=None,
        description="A CONFIRMED relationship (§22) supplying the keys, and the parent sheet "
                    "when `parent_sheet` is omitted. Explicit left_on/right_on win over it.",
    )
    # §24 sub-sampling: without these a related sheet keeps every referenced row
    # (v1 behaviour); with them the key-filtered set is sampled further.
    sampling_steps: list[SamplingStep] | None = Field(
        default=None,
        description="Sample the key-filtered rows further. Omit to keep every referenced row.")
    target_total_volume: int | None = Field(
        default=None, gt=0,
        description="Target rows for this sheet's sub-sample (requires sampling_steps)")

    @model_validator(mode="after")
    def _keys_together(self) -> "RelatedSheetLink":
        if (self.left_on is None) != (self.right_on is None):
            raise ValueError("left_on and right_on must be provided together (or both omitted)")
        if self.target_total_volume is not None and not self.sampling_steps:
            raise ValueError("target_total_volume requires sampling_steps")
        return self


class CoordinatedSampleRequest(BaseModel):
    """Sample a driver sheet, then filter related sheets by key.

    Produces a referentially-consistent mini-workbook: the driver sheet is
    sampled with the normal pipeline; each related sheet is filtered down to the
    rows referenced by the sample. All sheets are read from a single resolved
    version (``version_id > version_number > tag > current``).
    """

    dataset_id: str = Field(..., description="Dataset to sample (all sheets come from one version)")
    version_id: str | None = Field(default=None, description="Target a specific version by UUID")
    version_number: int | None = Field(default=None, description="Target a specific version by number")
    tag: str | None = Field(default=None, description="Target a version by tag name (e.g. 'production')")
    driver_sheet: str = Field(..., description="Sheet to sample; its keys drive related-sheet filtering")
    target_total_volume: int = Field(..., gt=0, description="Target total rows in the driver sample")
    sampling_steps: list[SamplingStep] = Field(..., description="Ordered sampling steps for the driver sheet")
    distribution_goals: DistributionGoals | None = Field(default=None, description="Distribution constraints for the driver sample")
    seed: int | None = Field(default=None, description="Random seed — makes the driver sample (and thus every filtered sheet) reproducible")
    return_data: bool = Field(default=True, description="Include sampled rows in the response (driver and related)")
    # Post-processing (applied to the driver sample only)
    deduplicate: bool = Field(default=False, description="Remove duplicate rows from the driver sample")
    deduplicate_columns: list[str] | None = Field(default=None, description="Columns to deduplicate the driver sample on")
    shuffle: bool = Field(default=False, description="Randomly shuffle the driver output rows")
    sort_by: str | None = Field(default=None, description="Column to sort the driver output by")
    sort_descending: bool = Field(default=False, description="Sort the driver output in descending order")
    related: list[RelatedSheetLink] = Field(default_factory=list, description="Related sheets to filter by key")


class RelatedSheetSample(BaseModel):
    """The filtered result for one related sheet."""

    sheet: str
    parent_sheet: str
    left_on: str
    right_on: str
    original_count: int
    sampled_count: int
    relationship_id: str | None = Field(
        default=None, description="Set when the keys came from a confirmed relationship (§22)")
    key_source: str = Field(
        default="explicit",
        description="How the keys were resolved: explicit | relationship | fk_rule")
    referenced_count: int | None = Field(
        default=None,
        description="Rows referenced by the parent BEFORE sub-sampling (§24); "
                    "equals sampled_count when no sub-sampling was requested")
    columns: list[ColumnSummary] = []
    preview: list[dict[str, Any]] = []
    sample_file: str | None = Field(default=None, description="Saved sample filename — fetch via GET /api/v1/samples/{filename}")
    data: list[dict[str, Any]] | None = None


class CoordinatedSampleResponse(BaseModel):
    """Response model for coordinated cross-sheet sampling."""

    success: bool
    dataset_id: str
    driver_sheet: str
    driver: SampleResponse
    related: list[RelatedSheetSample] = []


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
    count: int = Field(
        description="TOTAL rows in the sheet — the same value for every column, "
                    "nulls included. It is NOT the SQL COUNT(column) of non-null "
                    "values; that is `non_null_count`. Consumers derive non-null "
                    "as count - null_count, so this must stay the row count")
    non_null_count: int | None = Field(
        default=None,
        description="Rows where this column is not null (SQL COUNT(column)); "
                    "equals count - null_count")
    null_count: int = Field(description="Rows where this column is null")
    null_percent: float = Field(description="null_count / count, as a percentage")
    unique_count: int = Field(
        description="Distinct non-null values (SQL COUNT(DISTINCT column))")
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
    top_n: int = Field(
        default=10, ge=0,
        description="Number of top values to return per column",
    )


class ProfileResponse(BaseModel):
    """Response model for profiling endpoint."""

    success: bool
    row_count: int
    column_count: int
    columns: list[ColumnProfile]
    # Inner value is nullable: CORR() is undefined (NULL) for a zero-variance
    # column (e.g. a constant flag) or a pair with fewer than two co-varying
    # non-null rows. That is a legitimate "no correlation", not a server error —
    # the insight layer already skips None pairs. Typing it float (non-null)
    # made ProfileResponse validation raise, surfacing as a 500 on the very
    # common constant-numeric-column case.
    correlations: dict[str, dict[str, float | None]] | None = None
    memory_usage_bytes: int
    duplicate_row_count: int


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

class AggregationSpec(BaseModel):
    """Specification for a single aggregation."""

    column: str
    #: A bare ``str``, not a ``Literal``, on purpose. The service validates it
    #: and answers "Unknown aggregation function: X. Allowed: [...]", naming
    #: both what was sent and the whole vocabulary; a Literal would turn that
    #: into a generic 422. Only the *description* is derived from the constant,
    #: so the prose can never advertise a function the validator rejects.
    #:
    #: ``json_schema_extra`` publishes the same vocabulary as a JSON Schema
    #: ``enum`` so OpenAPI consumers and LLMs reading the generated tool schema
    #: can *see* the valid values instead of parsing them out of the prose.
    #: It is schema metadata only: pydantic does not enforce ``json_schema_extra``,
    #: so the annotation stays ``str``, nothing is validated at parse time, and
    #: the runtime 400 above is still the only thing that rejects a bad value.
    function: str = Field(
        description=f"Aggregation function: {AGG_FUNCTIONS_TEXT}",
        json_schema_extra={"enum": list(AGG_FUNCTIONS)},
    )
    alias: str | None = Field(default=None, description="Output column name (defaults to column_function)")
    filter: FilterGroup | None = Field(
        default=None,
        description="Conditional aggregate: only rows matching this filter feed the "
                    "aggregate — compiled to agg(...) FILTER (WHERE ...)")


class GroupByBucket(BaseModel):
    """Bucketed group-by entry — exactly one of date_trunc/bin_width/bin_count.

    The output column is named ``alias`` (default ``{column}_bucket``) and can
    be referenced by ``sort_by``.
    """

    column: str = Field(..., min_length=1, description="Source column to bucket")
    date_trunc: Literal["year", "quarter", "month", "week", "day", "hour"] | None = Field(
        default=None, description="Truncate a date/timestamp column to this unit")
    bin_width: float | None = Field(
        default=None, gt=0, description="Fixed-width numeric bins: FLOOR(col / w) * w")
    bin_count: int | None = Field(
        default=None, ge=1, description="Equal-width numeric bins (1..N) over the column's min/max")
    alias: str | None = Field(default=None, description="Output column name (defaults to {column}_bucket)")

    @model_validator(mode="after")
    def _exactly_one_bucket_kind(self):
        given = [k for k in ("date_trunc", "bin_width", "bin_count") if getattr(self, k) is not None]
        if len(given) != 1:
            raise ValueError(
                f"exactly one of date_trunc, bin_width, bin_count is required, got: {given or 'none'}")
        return self


class HavingCondition(BaseModel):
    """Post-aggregation condition over one aggregation alias (HAVING)."""

    column: str = Field(..., min_length=1, description="An aggregation alias from this request's aggregations")
    op: Literal["eq", "neq", "gt", "gte", "lt", "lte"]
    value: float | int


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
    group_by: list[str | GroupByBucket] = Field(
        description="Columns to group by — plain column names and/or bucketed entries")
    aggregations: list[AggregationSpec] = Field(description="Aggregation specifications")
    having: list[HavingCondition] = Field(
        default_factory=list,
        description="Post-aggregation conditions over aggregation aliases (ANDed)")
    sort_by: str | None = Field(
        default=None,
        description="Column to sort results by — any group-by output name (incl. bucket aliases) or aggregation alias")
    sort_order: Literal["asc", "desc"] = Field(
        default="desc",
        description="Sort order — exactly 'asc' or 'desc' (lowercase). Anything "
                    "else is a 422: it used to be coerced to 'desc', which "
                    "silently inverted requests like 'ASC' or 'ascending'.")
    limit: int | None = Field(default=None, description="Maximum number of groups to return (server-capped)")
    filters: FilterGroup | None = Field(
        default=None,
        description="Structured pre-aggregation filter (WHERE), compiled with bound parameters")
    filter_expr: str | None = Field(
        default=None,
        description="DEPRECATED — use `filters`. Raw SQL WHERE clause expression to filter data "
                    "before aggregation (e.g. 'sales >= 100'); ANDed with `filters` if both given",
    )
    return_data: bool = Field(default=True, description="Include aggregated rows in response (set False for large results)")


class AggregateResponse(BaseModel):
    """Response model for aggregation endpoint."""

    success: bool
    original_count: int
    group_count: int
    columns: list[str]
    data: list[dict[str, Any]] | None = None
    totals: dict[str, Any] | None = Field(
        default=None,
        description="Grand total per aggregation alias, re-aggregated over EVERY "
                    "filtered row (not just the returned page). Only additive "
                    "functions (sum, count) get one; see `totals_omitted` for the "
                    "aliases that were left out and why")
    totals_omitted: dict[str, Any] | None = Field(
        default=None,
        description="alias -> reason for every aggregation with no entry in "
                    "`totals`, so a client can tell 'there is no total' from "
                    "'the total is zero'. Reason: 'non-additive' — the function "
                    "(max/min/mean/median/std/nunique/first/last) has no "
                    "meaningful grand total; re-run at the grain you need")
    truncated: bool = Field(
        default=False,
        description="True when the server-side row cap (MAX_AGGREGATION_ROWS) cut "
                    "the results. `totals` stay correct when this is set — they "
                    "are computed over all groups, not the returned page")
    result_file: str | None = Field(default=None, description="Saved result filename — fetch via GET /api/v1/samples/{filename}")


# ---------------------------------------------------------------------------
# Pivot (Wave 2 §11)
# ---------------------------------------------------------------------------

PivotDisplay = Literal["value", "pct_of_row", "pct_of_column", "pct_of_grand_total"]


class PivotValue(AggregationSpec):
    """One value cell of a pivot: an aggregation plus how to display it."""

    display: PivotDisplay = Field(
        default="value",
        description="Render the aggregate raw, or as a percentage of its row / "
                    "pivot column / grand total (window functions over the "
                    "aggregated result)")


class PivotRequest(BaseModel):
    """Classic pivot over one sheet: row dims × one column dim × value aggs.

    Compiled onto the aggregation engine: a long-format GROUP BY over
    rows + column, percentage displays via window functions, then widened so
    each distinct column value becomes an output column.
    """

    file_path: str | None = Field(default=None, description="Path to data file")
    dataset_id: str | None = Field(default=None, description="Reference to a previously uploaded dataset")
    version_id: str | None = Field(default=None, description="Target a specific version by UUID")
    version_number: int | None = Field(default=None, description="Target a specific version by number")
    tag: str | None = Field(default=None, description="Target a version by tag name")
    sheet: str | None = Field(default=None, description="Sheet name for multi-sheet datasets")
    data: list[dict[str, Any]] | None = Field(default=None, description="Inline JSON array of data")

    rows: list[str | GroupByBucket] = Field(
        min_length=1, description="Row dimensions — plain columns and/or bucketed entries")
    columns: str | GroupByBucket | None = Field(
        default=None,
        description="Pivot dimension whose distinct values become output columns; "
                    "omit for a plain grouped table")
    values: list[PivotValue] = Field(min_length=1, description="Value aggregations")
    filters: FilterGroup | None = Field(
        default=None, description="Structured pre-aggregation filter (WHERE)")
    include_row_totals: bool = Field(
        default=False,
        description="Add a total_{alias} column per value, re-aggregated over the "
                    "row dims (correct for non-additive functions too)")
    include_column_totals: bool = Field(
        default=False,
        description="Return column_totals — one totals entry per output column, "
                    "re-aggregated over the pivot dim")
    sort_by: str | None = Field(
        default=None, description="Row-dimension output name to sort by (default: all row dims asc)")
    sort_order: Literal["asc", "desc"] = "asc"
    limit: int | None = Field(default=None, ge=1, description="Maximum output rows (server-capped)")
    return_data: bool = Field(default=True, description="Include pivoted rows in the response")


class PivotResponse(BaseModel):
    success: bool
    original_count: int
    row_count: int
    columns: list[str] = Field(description="Output columns, in order")
    pivot_columns: list[str] = Field(
        default_factory=list, description="Distinct pivot-dimension values that became columns")
    data: list[dict[str, Any]] | None = None
    totals: dict[str, Any] | None = Field(
        default=None, description="Grand totals per value alias (display=value specs only)")
    column_totals: dict[str, Any] | None = Field(
        default=None, description="Per-output-column totals (include_column_totals)")
    truncated: bool = False
    result_file: str | None = Field(
        default=None, description="Saved result filename — fetch via GET /api/v1/samples/{filename}")
