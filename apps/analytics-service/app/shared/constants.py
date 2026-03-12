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

# Aggregation function mappings
ALLOWED_AGG_FUNCTIONS = {
    "sum", "mean", "median", "count", "min", "max", "std", "nunique", "first", "last",
}

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
