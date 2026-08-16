"""Shared Pydantic schemas used across multiple features."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class Filter(BaseModel):
    """A single filter condition.

    Operators:
      Comparison:  eq, neq, gt, gte, lt, lte
      Set:         in, not_in (value must be a list)
      String:      contains, not_contains, starts_with, ends_with, regex, icontains
      Null/Empty:  is_null, is_not_null, is_empty, is_not_empty
      Range:       between, not_between (value must be [low, high])
      Top/Bottom:  top_n, bottom_n (value is N, by column's own values)
      Percentile:  top_pct, bottom_pct (value is 0-1 fraction, e.g. 0.1 = top 10%)
      Length:      len_eq, len_gt, len_gte, len_lt, len_lte, len_between (string/text length)
      Date:        date_before, date_after, date_between (value is ISO date string or [start, end])
                   last_n_days (value is int days from today)
      Duplicates:  is_duplicate, is_unique (rows where column value appears more/exactly once)
    """

    column: str = Field(..., description="Column name to filter on")
    op: str = Field(..., description="Filter operator")
    value: Any = Field(default=None, description="Value(s) to compare against")
    case_sensitive: bool = Field(default=True, description="Case sensitivity for string ops")


class FilterGroup(BaseModel):
    """A group of filters combined with AND or OR logic.

    Supports nesting: conditions can contain Filter or FilterGroup objects.
    """

    logic: str = Field(default="and", description="Combine conditions with 'and' or 'or'")
    conditions: list[dict[str, Any]] = Field(..., description="List of Filter or FilterGroup objects")


class ColumnInfo(BaseModel):
    name: str
    dtype: str


class ColumnSummary(BaseModel):
    """Quick stats for one column of the sampled data."""

    name: str
    dtype: str
    nulls: int
    unique: int
    top_values: list[Any] | None = None
    min: Any | None = None
    max: Any | None = None
    mean: float | None = None
