"""Shared constants, type sets, and mappings."""

from __future__ import annotations

ALLOWED_EXTENSIONS = {".csv", ".parquet", ".xlsx", ".xls"}

_NUMERIC_DUCKDB_TYPES = frozenset({
    "TINYINT", "SMALLINT", "INTEGER", "BIGINT", "HUGEINT",
    "UTINYINT", "USMALLINT", "UINTEGER", "UBIGINT",
    "FLOAT", "DOUBLE", "REAL",
})

_DATETIME_DUCKDB_TYPES = frozenset({
    "DATE", "TIMESTAMP", "TIMESTAMPTZ", "TIMESTAMP_S",
    "TIMESTAMP_MS", "TIMESTAMP_NS", "TIME", "TIMETZ",
    "TIMESTAMP WITH TIME ZONE",
})

# TUS protocol constants
TUS_VERSION = "1.0.0"
TUS_EXTENSIONS = "creation,termination,checksum"
TUS_MAX_SIZE = 100 * 1024 * 1024 * 1024  # 100GB
TUS_UPLOAD_EXPIRY_SECONDS = 7 * 24 * 3600  # 7 days

# Server-side cap on aggregation output rows (groups); the effective LIMIT is
# min(user limit, cap) and responses flag `truncated` when the cap cut results.
MAX_AGGREGATION_ROWS = 100_000

# ---------------------------------------------------------------------------
# Aggregation functions — the single source of truth
# ---------------------------------------------------------------------------
# This list used to exist three times: here, in the ``AggregationSpec.function``
# field description, and again as a hand-written string in the MCP compute
# tools. Nothing kept them in sync, so adding a function meant remembering all
# three and a caller could be told a different vocabulary than the one the
# validator enforces. Everything below is derived; add a function HERE only.
#
# A tuple, not a set, because the derived prose has to read in a deliberate
# order (arithmetic, then extremes, then positional). Runtime validation is set
# membership and the runtime error message sorts, so this order is
# presentation-only and cannot change what is accepted.
AGG_FUNCTIONS: tuple[str, ...] = (
    "sum", "mean", "median", "count", "min", "max", "std", "nunique", "first", "last",
)

#: Membership test for validation. Frozen so a caller cannot mutate the
#: vocabulary of every other module by accident.
ALLOWED_AGG_FUNCTIONS = frozenset(AGG_FUNCTIONS)

#: The vocabulary as prose, for field descriptions and tool help text.
#: Deliberately NOT used to build a ``Literal``: ``AggregationSpec.function``
#: stays a bare ``str`` so an unknown function is rejected by the service with
#: "Unknown aggregation function: X. Allowed: [...]" rather than by pydantic
#: with a generic 422 that never names the offending value.
AGG_FUNCTIONS_TEXT = ", ".join(AGG_FUNCTIONS)

#: DuckDB expression per function. ``nunique`` is absent on purpose — it
#: compiles to COUNT(DISTINCT …), which is not a bare ``f(col)`` call; see
#: ``tests/unit/test_agg_function_registry.py``, which pins that this map and
#: ``AGG_FUNCTIONS`` cannot drift apart silently.
AGG_SQL_MAP = {
    "sum": "SUM",
    "mean": "AVG",
    "median": "MEDIAN",
    "count": "COUNT",
    "min": "MIN",
    "max": "MAX",
    "std": "STDDEV_SAMP",
    "first": "FIRST",
    "last": "LAST",
}


def is_numeric_duckdb_type(type_str: str) -> bool:
    base = type_str.upper().split("(")[0].strip()
    return base in _NUMERIC_DUCKDB_TYPES or base.startswith("DECIMAL") or base.startswith("NUMERIC")


def is_datetime_duckdb_type(type_str: str) -> bool:
    base = type_str.upper().split("(")[0].strip()
    return base in _DATETIME_DUCKDB_TYPES


def is_boolean_duckdb_type(type_str: str) -> bool:
    return type_str.upper().strip() in {"BOOLEAN", "BOOL"}
