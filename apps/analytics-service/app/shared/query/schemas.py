"""Typed filter/query DSL schemas — formalizing the dict shapes ``app.shared.filters`` consumes.

Existing sampling-step payloads (plain dicts like ``{"column": "x", "op": "gt",
"value": 5}`` and ``{"logic": "or", "conditions": [...]}``) validate unchanged.
The operator vocabulary mirrors ``compile_filter`` exactly; a model validator
enforces value arity per operator class before anything reaches SQL.

``FilterGroup.conditions`` is a *shape-discriminated* union, not a plain
``Filter | FilterGroup`` one. Every ``FilterGroup`` field has a default, so a
plain union let any dict that failed ``Filter`` (an unknown operator, a bad
value arity) fall through and match ``FilterGroup`` instead — producing an
empty group whose ``column``/``op``/``value`` were silently discarded, an
empty WHERE clause, and the whole unfiltered table in the response. Each
condition is now routed by shape to the model it was clearly meant to be, so
it fails against *that* model instead of quietly becoming a no-op.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, get_args

from pydantic import BaseModel, Discriminator, Field, Tag, model_validator

from app.api.errors import ProblemException

FilterOp = Literal[
    # Null/empty checks (no value)
    "is_null", "is_not_null", "is_empty", "is_not_empty",
    # Comparison
    "eq", "neq", "gt", "gte", "lt", "lte",
    # Set membership (non-empty list value)
    "in", "not_in",
    # Range ([low, high] pair value)
    "between", "not_between",
    # String
    "contains", "icontains", "not_contains", "starts_with", "ends_with", "regex",
    # String length
    "len_eq", "len_gt", "len_gte", "len_lt", "len_lte", "len_between",
    # Top/bottom by value
    "top_n", "bottom_n", "top_pct", "bottom_pct",
    # Date
    "date_before", "date_after", "date_between", "last_n_days",
    # Duplicate/unique (no value)
    "is_duplicate", "is_unique",
]

Logic = Literal["and", "or"]
SortDirection = Literal["asc", "desc"]

# Operator classes by value arity — everything not listed takes a single scalar.
NO_VALUE_OPS = frozenset({
    "is_null", "is_not_null", "is_empty", "is_not_empty", "is_duplicate", "is_unique",
})
LIST_OPS = frozenset({"in", "not_in"})
PAIR_OPS = frozenset({"between", "not_between", "len_between", "date_between"})

# The operator vocabulary as a set, for the friendlier unknown-operator error.
KNOWN_OPS: frozenset[str] = frozenset(get_args(FilterOp))

# Field names that identify which side of the conditions union a dict belongs
# to. A condition carrying any FILTER_KEY was meant to be a Filter, and must be
# judged as one — never re-read as an (always-valid, always-empty) FilterGroup.
FILTER_KEYS = frozenset({"column", "op", "value", "case_sensitive"})
GROUP_KEYS = frozenset({"logic", "conditions"})


class Filter(BaseModel):
    """One condition against a single column, compiled by ``compile_filter``."""

    column: str = Field(..., min_length=1, description="Column reference (normalized or physical name)")
    op: FilterOp = Field(..., description="Operator — same vocabulary as app.shared.filters.compile_filter")
    value: Any = Field(default=None, description="Operand: absent for null-checks, [low, high] for ranges, non-empty list for in/not_in, scalar otherwise")
    case_sensitive: bool = Field(default=True, description="String operators only; ignored elsewhere")

    @model_validator(mode="before")
    @classmethod
    def _reject_unknown_op(cls, data: Any) -> Any:
        """Unknown operator → problem+json 400 ``unknown-operator``.

        The ``FilterOp`` Literal already rejects it, but only as a pydantic
        ValidationError whose message is a 40-operator "Input should be ..."
        wall that never names the column. Clients branch on the problem
        ``code``, so an unknown *operator* is reported exactly like an unknown
        *column* (``validate.py::_resolve``): 400, a specific code, and the
        offending value plus the accepted vocabulary as top-level fields.
        """
        if isinstance(data, dict):
            op = data.get("op")
            if isinstance(op, str) and op not in KNOWN_OPS:
                column = data.get("column")
                where = f" on column '{column}'" if isinstance(column, str) and column else ""
                raise ProblemException(
                    400, f"Unknown filter operator: '{op}'{where}",
                    code="unknown-operator", op=op, column=column,
                    available=sorted(KNOWN_OPS),
                )
        return data

    @model_validator(mode="after")
    def _check_value_arity(self):
        if self.op in NO_VALUE_OPS:
            if self.value is not None:
                raise ValueError(f"op '{self.op}' takes no value, got: {self.value!r}")
        elif self.op in LIST_OPS:
            if not isinstance(self.value, list) or len(self.value) == 0:
                raise ValueError(f"op '{self.op}' requires a non-empty list, got: {self.value!r}")
        elif self.op in PAIR_OPS:
            if not isinstance(self.value, list) or len(self.value) != 2:
                raise ValueError(f"op '{self.op}' requires a [low, high] pair, got: {self.value!r}")
        elif self.value is None:
            raise ValueError(f"op '{self.op}' requires a value")
        return self


def _condition_kind(value: Any) -> str:
    """Tag one ``conditions`` entry as a ``Filter`` or a ``FilterGroup`` by shape.

    The line between "deliberately empty" and "malformed" is *discarded
    information*: an entry is malformed when honouring it as a group would
    throw away fields the caller clearly meant as a filter. So ``{}`` — which
    says nothing and loses nothing — stays a legitimate empty (no-op) group,
    while anything carrying filter fields, or fields belonging to neither
    model, is rejected rather than silently reinterpreted.
    """
    if isinstance(value, Filter):
        return "filter"
    if isinstance(value, FilterGroup):
        return "group"
    if isinstance(value, dict):
        keys = sorted(str(k) for k in value)
        filter_keys = [k for k in keys if k in FILTER_KEYS]
        group_keys = [k for k in keys if k in GROUP_KEYS]
        if filter_keys and not group_keys:
            return "filter"
        if group_keys and not filter_keys:
            return "group"
        if not keys:
            return "group"  # {} — an explicit empty group; nothing is discarded
        if filter_keys and group_keys:
            raise ProblemException(
                400,
                f"Malformed filter condition: mixes Filter fields {filter_keys} "
                f"with FilterGroup fields {group_keys} — send one or the other",
                code="invalid-filter", keys=keys,
            )
        raise ProblemException(
            400,
            f"Malformed filter condition: {keys} matches neither a Filter "
            f"(needs 'column' and 'op') nor a FilterGroup (needs 'conditions')",
            code="invalid-filter", keys=keys,
        )
    raise ProblemException(
        400,
        f"Malformed filter condition: expected an object, got {type(value).__name__}",
        code="invalid-filter",
    )


#: One entry of ``FilterGroup.conditions``, routed by shape (see
#: ``_condition_kind``) so a condition is validated against the model it was
#: meant to be instead of falling through to a vacuously-valid empty group.
FilterCondition = Annotated[
    Annotated[Filter, Tag("filter")] | Annotated["FilterGroup", Tag("group")],
    Discriminator(_condition_kind),
]


class FilterGroup(BaseModel):
    """Boolean combination of filters; nests recursively.

    An empty ``conditions`` list is legal and means "no filtering" — call
    paths build one to represent an intentional no-op. It can no longer arise
    by accident: a condition that fails to parse raises instead of collapsing
    into one (see the module docstring).
    """

    logic: Logic = Field(default="and", description="How conditions combine")
    conditions: list[FilterCondition] = Field(
        default_factory=list, description="Filters and/or nested groups")


FilterGroup.model_rebuild()


class Sort(BaseModel):
    """One ORDER BY term."""

    column: str = Field(..., min_length=1, description="Column reference (normalized or physical name)")
    direction: SortDirection = Field(default="asc")


class QuerySpec(BaseModel):
    """A complete query over one sheet's parquet: projection, filters, search, sort, paging."""

    columns: list[str] | None = Field(
        default=None, description="Projection; None selects every column")
    filters: FilterGroup | None = Field(default=None, description="Structured WHERE clause")
    sort: list[Sort] = Field(default_factory=list, description="Multi-column ORDER BY")
    search: str | None = Field(
        default=None, description="Case-insensitive substring match OR'd over all text columns")
    cursor: str | None = Field(
        default=None, description="Opaque cursor from a previous page's next_cursor")
    limit: int = Field(default=100, ge=1, le=1000, description="Page size")


class QueryPage(BaseModel):
    """Query response envelope — cursor-paged, unlike offset-based ``Page[T]``."""

    items: list[dict[str, Any]] = Field(description="Result rows")
    next_cursor: str | None = Field(
        default=None, description="Cursor for the next page; None on the last page")
    total: int | None = Field(default=None, description="Total rows matching the spec's filters")
    masked_columns: list[str] = Field(
        default_factory=list,
        description="Columns whose values were masked for this caller because "
                    "the data dictionary marks them sensitive")
