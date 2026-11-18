"""Response models for the API. The TypeScript mirror is src/shared/api/types.ts; keep them in step."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel


class Health(BaseModel):
    status: Literal["ok"]
    version: str
    datasets: int
    data_dir: str


class DatasetSummary(BaseModel):
    id: str
    name: str
    filename: str
    source: Literal["sample", "upload"]
    sample: bool
    note: str
    created_at: str
    size_bytes: int
    rows: int
    n_cols: int
    kind: Literal["text", "tabular"]
    target: str | None
    n_classes: int


class Dataset(DatasetSummary):
    profile: dict[str, Any]


class DatasetList(BaseModel):
    datasets: list[DatasetSummary]
