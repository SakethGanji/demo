"""PromptLab schemas — sessions, runs, evaluator, dataset upload (v5)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Evaluate request / response
# ---------------------------------------------------------------------------


class EvaluateRequest(BaseModel):
    """Request body for ``POST /prompt-lab/evaluate`` (JSON only).

    The dataset must be pre-registered via ``POST /prompt-lab/datasets``;
    pass its ``dataset_id`` here. ``session_id`` is optional — when supplied,
    runs are grouped under it and budget aggregates across calls (the
    session doc is lazily upserted). ``target_column`` and ``input_columns``
    fall back to the dataset's sidecar metadata.
    """

    dataset_id: str
    prompt_system: str
    prompt_user_template: str
    intent_classes: list[str]
    config: dict = Field(default_factory=dict)
    session_id: str | None = None
    target_column: str | None = None
    input_columns: list[str] | None = None
    evaluation_splits: list[str] = Field(default_factory=list)
    scorer: str = "classification_exact"
    task_type: str = "classification"
    parent_run_id: str | None = None
    strategy: str | None = None
    # Caching / cost controls.
    use_cache: bool = True
    max_cost_usd: float = 1.0
    failure_threshold: float = 0.5


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


class PerClassMetric(BaseModel):
    precision: float
    recall: float
    f1: float
    support: int


class SplitMetrics(BaseModel):
    accuracy: float
    macro_f1: float
    n_rows: int
    n_failures: int
    per_class: dict[str, PerClassMetric] = Field(default_factory=dict)
    confusion_matrix: dict[str, dict[str, int]] = Field(default_factory=dict)


class MetricsBlock(BaseModel):
    overall: SplitMetrics
    by_split: dict[str, SplitMetrics] = Field(default_factory=dict)


class FailureSampleRow(BaseModel):
    row_id: str
    split: str
    inputs: dict
    gt: str
    predicted: str
    confusion_pair: str


class FailureSummary(BaseModel):
    total: int
    by_confusion_pair: dict[str, int] = Field(default_factory=dict)
    by_split: dict[str, int] = Field(default_factory=dict)
    top_confusion_pairs: list[str] = Field(default_factory=list)


class EvaluateResponse(BaseModel):
    dataset_id: str
    session_id: str | None = None
    run_id: str | None = None
    prompt_hash: str
    metrics: MetricsBlock
    failures_sample: list[FailureSampleRow] = Field(default_factory=list)
    failure_summary: FailureSummary
    tokens_in: int
    tokens_out: int
    cost_usd: float
    latency_p50_ms: float
    cached: bool = False
    budget_spent_usd: float = 0.0
    budget_max_usd: float = 0.0


# ---------------------------------------------------------------------------
# Dataset upload
# ---------------------------------------------------------------------------


class DatasetColumnInfo(BaseModel):
    name: str
    dtype: str
    n_unique: int
    samples: list[Any] = Field(default_factory=list)


class DatasetEntry(BaseModel):
    """Response of ``POST /prompt-lab/datasets``."""

    dataset_id: str
    name: str
    description: str = ""
    n_rows: int
    target_column: str
    input_columns: list[str] = Field(default_factory=list)
    detected_split_column: str | None = None
    detected_classes: list[str] = Field(default_factory=list)
    detected_splits: list[str] = Field(default_factory=list)
    columns: list[DatasetColumnInfo] = Field(default_factory=list)
    sha256: str
    file_size_bytes: int
    storage_path: str
    format_original: str
    created_at: str


