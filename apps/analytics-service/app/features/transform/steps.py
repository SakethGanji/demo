"""Transformation step models (ROADMAP §19) — a discriminated union on ``type``.

A pipeline is an ordered list of these. Each step is declarative data, never
SQL text: the compiler in :mod:`app.features.transform.compile` turns one step
into one CTE, resolving column references against the schema as folded by the
preceding steps.

Steps split into two families, which matters for the schema fold:

* **projection steps** (``select``/``drop``/``rename``/``reorder``/``split``/
  ``merge``/``compute``) change the column set, so later steps see the new one;
* **row steps** (``filter``/``deduplicate``/``sort``/``limit``) and in-place
  value steps (``cast``/``trim``/``case_normalize``/``replace``/``parse_dates``)
  leave the column set alone (``cast``/``parse_dates`` change only a dtype).
"""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field, model_validator

from app.shared.query.schemas import FilterGroup, Sort

from .expr import CastTarget, Expr

# A new/renamed column name. Identifiers are always emitted through
# ``quote_ident``, so this bound is about keeping outputs sane (and
# round-trippable through parquet), not about injection.
ColumnName = Annotated[str, Field(min_length=1, max_length=200)]


class SelectStep(BaseModel):
    """Keep only these columns, in this order."""

    type: Literal["select"] = "select"
    columns: list[str] = Field(..., min_length=1, description="Columns to keep, in output order")


class DropStep(BaseModel):
    """Remove these columns."""

    type: Literal["drop"] = "drop"
    columns: list[str] = Field(..., min_length=1, description="Columns to remove")


class RenameStep(BaseModel):
    """Rename columns. Keys are current names, values are the new names."""

    type: Literal["rename"] = "rename"
    renames: dict[str, ColumnName] = Field(
        ..., min_length=1, description="{current name: new name}")


class ReorderStep(BaseModel):
    """Move these columns to the front; unlisted columns keep their order."""

    type: Literal["reorder"] = "reorder"
    columns: list[str] = Field(..., min_length=1, description="Columns to place first, in order")


class CastStep(BaseModel):
    """Cast one column to a whitelisted type."""

    type: Literal["cast"] = "cast"
    column: str = Field(..., min_length=1)
    to: CastTarget = Field(..., description="Target type (whitelisted)")


class TrimStep(BaseModel):
    """Strip whitespace from text columns."""

    type: Literal["trim"] = "trim"
    columns: list[str] = Field(..., min_length=1)
    mode: Literal["both", "left", "right"] = "both"


class CaseNormalizeStep(BaseModel):
    """Normalize the case of text columns."""

    type: Literal["case_normalize"] = "case_normalize"
    columns: list[str] = Field(..., min_length=1)
    mode: Literal["lower", "upper", "title"] = "lower"


class ReplaceStep(BaseModel):
    """Replace values in one text column, and/or fill its NULLs."""

    type: Literal["replace"] = "replace"
    column: str = Field(..., min_length=1)
    mode: Literal["exact", "substring", "regex"] = "substring"
    find: str | None = Field(default=None, description="Value/substring/pattern to match")
    replace_with: str = Field(default="", description="Replacement value")
    nulls_to: str | None = Field(
        default=None, description="When set, NULLs become this (applied after the replacement)")

    @model_validator(mode="after")
    def _needs_work(self):
        if self.find is None and self.nulls_to is None:
            raise ValueError("replace requires 'find' and/or 'nulls_to'")
        return self


class ParseDatesStep(BaseModel):
    """Parse text columns into TIMESTAMPs with an explicit strptime format."""

    type: Literal["parse_dates"] = "parse_dates"
    columns: list[str] = Field(..., min_length=1)
    format: str = Field(..., min_length=1, description="strptime format, e.g. '%Y-%m-%d'")


class SplitStep(BaseModel):
    """Split a text column on a delimiter and keep one part."""

    type: Literal["split"] = "split"
    column: str = Field(..., min_length=1)
    delimiter: str = Field(..., min_length=1)
    index: int = Field(default=1, ge=1, description="1-based part to keep")
    into: ColumnName = Field(..., description="Output column (may be the source column)")


class MergeStep(BaseModel):
    """Concatenate several columns into one."""

    type: Literal["merge"] = "merge"
    columns: list[str] = Field(..., min_length=2, description="Columns to join, in order")
    into: ColumnName = Field(..., description="Output column")
    separator: str = Field(default=" ", description="Inserted between values")
    drop_sources: bool = Field(default=False, description="Remove the source columns")


class ComputeStep(BaseModel):
    """Add (or replace) a column from a typed expression — ROADMAP §20."""

    type: Literal["compute"] = "compute"
    into: ColumnName = Field(..., description="Output column")
    expression: Expr = Field(..., description="Typed expression tree")


class FilterStep(BaseModel):
    """Keep only rows matching a condition (the query DSL's FilterGroup)."""

    type: Literal["filter"] = "filter"
    where: FilterGroup = Field(..., description="Structured condition")


class DeduplicateStep(BaseModel):
    """Drop duplicate rows, by all columns or a subset."""

    type: Literal["deduplicate"] = "deduplicate"
    subset: list[str] | None = Field(
        default=None, description="Columns defining a duplicate; null means every column")
    keep: Literal["first", "last", "none"] = Field(
        default="first", description="Which duplicate survives; 'none' drops every duplicated row")
    order_by: list[Sort] = Field(
        default_factory=list, description="Makes first/last deterministic")


class SortStep(BaseModel):
    """Order rows."""

    type: Literal["sort"] = "sort"
    by: list[Sort] = Field(..., min_length=1)


class LimitStep(BaseModel):
    """Keep at most *count* rows."""

    type: Literal["limit"] = "limit"
    count: int = Field(..., ge=1, le=10_000_000)
    offset: int = Field(default=0, ge=0)


TransformStep = Annotated[
    Union[
        SelectStep, DropStep, RenameStep, ReorderStep, CastStep, TrimStep,
        CaseNormalizeStep, ReplaceStep, ParseDatesStep, SplitStep, MergeStep,
        ComputeStep, FilterStep, DeduplicateStep, SortStep, LimitStep,
    ],
    Field(discriminator="type"),
]

# Pipelines are bounded so one definition cannot compile to unbounded SQL.
MAX_PIPELINE_STEPS = 50
