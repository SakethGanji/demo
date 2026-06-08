"""PromptLab schemas — stateless evaluator I/O + dataset upload (v6)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Evaluate request / response
# ---------------------------------------------------------------------------


class EvaluateRequest(BaseModel):
    """Request body for ``POST /prompt-lab/evaluate`` (JSON only).

    Stateless: every call evaluates the given prompt against ``stage``-sized
    sample of ``dataset_id`` and returns metrics. Session state and run
    persistence are the workflow's responsibility — this endpoint has no
    knowledge of either.
    """

    dataset_id: str
    prompt_system: str
    prompt_user_template: str
    intent_classes: list[str]
    stage: Literal["smoke", "quick", "full"] = "full"
    config: dict = Field(default_factory=dict)
    target_column: str | None = None
    input_columns: list[str] | None = None
    evaluation_splits: list[str] = Field(default_factory=list)
    scorer: str = "classification_exact"
    task_type: str = "classification"
    max_cost_usd: float = 1.0


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
    prompt_hash: str
    stage: Literal["smoke", "quick", "full"]
    n_rows_evaluated: int
    metrics: MetricsBlock
    failures_sample: list[FailureSampleRow] = Field(default_factory=list)
    failure_summary: FailureSummary
    tokens_in: int
    tokens_out: int
    cost_usd: float
    latency_p50_ms: float


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


