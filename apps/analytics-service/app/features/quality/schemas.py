"""Quality feature schemas — rules and validation runs."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

ScopeType = Literal["dataset", "sheet", "column", "cross_sheet"]
RuleType = Literal["sheet_exists", "row_count_min", "not_null", "unique",
                   "accepted_values", "range", "regex_match", "foreign_key"]
Severity = Literal["error", "warning"]

# Which scope each rule type belongs to (drives request validation).
_RULE_SCOPES: dict[str, str] = {
    "sheet_exists": "dataset",
    "row_count_min": "sheet",
    "not_null": "column",
    "unique": "column",
    "accepted_values": "column",
    "range": "column",
    "regex_match": "column",
    "foreign_key": "cross_sheet",
}


class RuleCreate(BaseModel):
    """Create a quality rule. Selectors use sheet_key / normalized column names."""

    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    rule_type: RuleType
    sheet_selector: str | None = Field(
        default=None, description="Target sheet_key (required for all but dataset scope … "
                                  "sheet_exists uses it as the REQUIRED sheet name)")
    column_selector: str | None = Field(
        default=None, description="Target normalized column name (column/cross_sheet scope)")
    parameters: dict[str, Any] = Field(default_factory=dict)
    severity: Severity = "error"
    enabled: bool = True

    @property
    def scope_type(self) -> str:
        return _RULE_SCOPES[self.rule_type]

    @model_validator(mode="after")
    def _check_selectors(self):
        scope = _RULE_SCOPES[self.rule_type]
        if self.sheet_selector is None:
            raise ValueError(f"{self.rule_type} requires sheet_selector")
        if scope in ("column", "cross_sheet") and not self.column_selector:
            raise ValueError(f"{self.rule_type} requires column_selector")
        if self.rule_type == "foreign_key" and not (
                self.parameters.get("ref_sheet") and self.parameters.get("ref_column")):
            raise ValueError("foreign_key requires parameters.ref_sheet and parameters.ref_column")
        if self.rule_type == "accepted_values" and not self.parameters.get("values"):
            raise ValueError("accepted_values requires parameters.values (non-empty list)")
        return self


class RuleUpdate(BaseModel):
    """Patch a rule. Only provided fields change (rule_type/scope are fixed)."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    sheet_selector: str | None = None
    column_selector: str | None = None
    parameters: dict[str, Any] | None = None
    severity: Severity | None = None
    enabled: bool | None = None


class RuleOut(BaseModel):
    id: str
    dataset_id: str
    name: str
    description: str | None = None
    scope_type: str
    sheet_selector: str | None = None
    column_selector: str | None = None
    rule_type: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    severity: str
    enabled: bool
    created_by: str | None = None
    created_at: str
    updated_at: str


class RuleResultOut(BaseModel):
    id: str | None = None
    rule_id: str | None = None
    rule_name: str
    rule_type: str
    scope_type: str
    sheet_selector: str | None = None
    column_selector: str | None = None
    severity: str
    status: str  # passed | failed | error | skipped
    failure_count: int | None = None
    message: str | None = None
    sample_failures: list[dict[str, Any]] | None = None


class ValidationRunOut(BaseModel):
    id: str
    dataset_id: str
    dataset_version_id: str
    job_id: str | None = None
    status: str
    rules_total: int | None = None
    rules_passed: int | None = None
    rules_failed: int | None = None
    error_failures: int | None = None
    warning_failures: int | None = None
    triggered_by: str | None = None
    started_at: str
    completed_at: str | None = None
    error: str | None = None


class ValidationDetail(ValidationRunOut):
    results: list[RuleResultOut] = Field(default_factory=list)
