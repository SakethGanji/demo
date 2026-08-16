"""Validate a QuerySpec against a sheet's schema_json — before any file I/O.

Column references (projection, filters recursively, sort) resolve
normalized_name-first-then-name, the same precedence as the quality engine's
``_physical_column``. Failures raise problem+json 400s (``unknown-column``,
``operator-type-mismatch``) whose extra kwargs surface as top-level fields.
"""

from __future__ import annotations

from typing import Any

from app.api.errors import ProblemException
from app.shared.query.schemas import Filter, FilterGroup, QuerySpec

# Operators that only make sense on text columns. DuckDB happily compares
# numbers on VARCHAR (gt/lt etc. stay allowed everywhere), but string matching
# and length ops on non-text columns are a caller mistake.
STRING_OPS = frozenset({
    "contains", "icontains", "not_contains", "starts_with", "ends_with", "regex",
    "len_eq", "len_gt", "len_gte", "len_lt", "len_lte", "len_between",
})
DATE_OPS = frozenset({"date_before", "date_after", "date_between", "last_n_days"})


def _is_text(dtype: str) -> bool:
    return dtype.upper().startswith(("VARCHAR", "CHAR", "TEXT", "STRING"))


def _is_temporal(dtype: str) -> bool:
    return dtype.upper().startswith(("DATE", "TIMESTAMP"))


def _available(schema_json: list[dict]) -> list[str]:
    return [c.get("normalized_name") or c["name"]
            for c in sorted(schema_json, key=lambda c: c.get("position", 0))]


def _resolve(name: str, schema_json: list[dict]) -> dict[str, Any]:
    """Resolve a column ref to its schema row — normalized_name first, then name."""
    for c in schema_json:
        if c.get("normalized_name") == name:
            return c
    for c in schema_json:
        if c["name"] == name:
            return c
    raise ProblemException(
        400, f"Unknown column: '{name}'",
        code="unknown-column", column=name, available=_available(schema_json),
    )


def _check_filter(node: Filter | FilterGroup, schema_json: list[dict],
                  mapping: dict[str, str]) -> None:
    if isinstance(node, FilterGroup):
        for cond in node.conditions:
            _check_filter(cond, schema_json, mapping)
        return
    col = _resolve(node.column, schema_json)
    mapping[node.column] = col["name"]
    dtype = col.get("dtype") or ""
    if node.op in STRING_OPS and not _is_text(dtype):
        raise ProblemException(
            400, f"Operator '{node.op}' requires a text column; '{node.column}' is {dtype}",
            code="operator-type-mismatch", column=node.column, op=node.op, dtype=dtype,
        )
    if node.op in DATE_OPS and not _is_temporal(dtype):
        raise ProblemException(
            400, f"Operator '{node.op}' requires a DATE/TIMESTAMP column; '{node.column}' is {dtype}",
            code="operator-type-mismatch", column=node.column, op=node.op, dtype=dtype,
        )


def validate_spec(spec: QuerySpec, schema_json: list[dict]) -> dict[str, str]:
    """Validate every column ref and operator/type pairing in *spec*.

    Returns ``{requested_name -> physical_name}`` for the compiler. Raises
    ``ProblemException`` 400 (``unknown-column`` / ``operator-type-mismatch``)
    on the first violation.
    """
    mapping: dict[str, str] = {}
    for name in spec.columns or []:
        mapping[name] = _resolve(name, schema_json)["name"]
    if spec.filters is not None:
        _check_filter(spec.filters, schema_json, mapping)
    for s in spec.sort:
        mapping[s.column] = _resolve(s.column, schema_json)["name"]
    return mapping
