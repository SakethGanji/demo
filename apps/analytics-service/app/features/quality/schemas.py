"""Quality feature schemas — rules and validation runs."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationInfo, field_validator, model_validator

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


def check_rule_shape(rule_type: str, sheet_selector: str | None,
                     column_selector: str | None,
                     parameters: dict[str, Any] | None) -> None:
    """Raise ``ValueError`` if this rule shape can never evaluate meaningfully.

    Lives at module level rather than inside ``RuleCreate`` because PATCH has to
    apply exactly the same invariants: a rule whose parameters are edited to
    ``{}`` after creation is just as broken as one created that way, and the
    only difference the caller sees is *when* it turns into a per-rule error.
    """
    scope = _RULE_SCOPES[rule_type]
    if sheet_selector is None:
        raise ValueError(f"{rule_type} requires sheet_selector")
    if scope in ("column", "cross_sheet") and not column_selector:
        raise ValueError(f"{rule_type} requires column_selector")
    params = parameters or {}
    if rule_type == "foreign_key" and not (
            params.get("ref_sheet") and params.get("ref_column")):
        raise ValueError("foreign_key requires parameters.ref_sheet and parameters.ref_column")
    if rule_type == "accepted_values" and not params.get("values"):
        raise ValueError("accepted_values requires parameters.values (non-empty list)")


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
        check_rule_shape(self.rule_type, self.sheet_selector,
                         self.column_selector, self.parameters)
        return self


class RuleUpdate(BaseModel):
    """Patch a rule. Only provided fields change (rule_type/scope are fixed).

    Every field is typed ``| None`` because ``None`` is how "not sent" is
    spelled, not because every column is nullable. ``name``/``severity``/
    ``enabled`` are ``NOT NULL`` in the schema, so an explicitly-sent ``null``
    has to be rejected HERE — ``exclude_unset`` keeps the key, the repo builds
    the SET list from key presence, and the resulting NotNullViolation reaches
    the route as the very ``IntegrityError`` class the ``(dataset_id, name)``
    unique index raises. Left to the route it becomes a fabricated 409 blaming
    a field the caller never sent. Nullable fields (description, the selectors,
    parameters) are deliberately NOT covered: clearing them is a real edit.
    """

    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    sheet_selector: str | None = None
    column_selector: str | None = None
    parameters: dict[str, Any] | None = None
    severity: Severity | None = None
    enabled: bool | None = None

    @field_validator("name", "severity", "enabled", mode="before")
    @classmethod
    def _reject_explicit_null(cls, v: Any, info: ValidationInfo) -> Any:
        """Reject ``{"field": null}`` while still allowing the field to be omitted.

        Field validators do not run for defaults (``validate_default`` is off),
        so this fires only when the key is actually present in the request body.
        """
        if v is None:
            raise ValueError(
                f"{info.field_name} cannot be null; omit it to leave it unchanged")
        return v


class RuleOut(BaseModel):
    id: str
    dataset_id: str
    name: str
    description: str | None = None
    scope_type: str
    sheet_selector: str | None = None
    logical_sheet_id: str | None = None
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
    # Failing rows are dataset content, so they live in the object store, not
    # in Postgres. Fetch them via GET /samples/{failure_sample_file}/data —
    # the usual /samples authorization applies.
    failure_sample_file: str | None = None
    failure_artifact_id: str | None = None


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
