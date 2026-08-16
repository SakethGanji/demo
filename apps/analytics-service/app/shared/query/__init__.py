"""Typed filter/query DSL — schemas, validation, compilation, cursor paging."""

from __future__ import annotations

from app.shared.query.compile import (
    CompiledQuery,
    compile_query,
    decode_cursor,
    encode_cursor,
    execute_query,
    spec_hash,
)
from app.shared.query.schemas import (
    Filter,
    FilterGroup,
    QueryPage,
    QuerySpec,
    Sort,
)
from app.shared.query.validate import validate_spec

__all__ = [
    "CompiledQuery",
    "Filter",
    "FilterGroup",
    "QueryPage",
    "QuerySpec",
    "Sort",
    "compile_query",
    "decode_cursor",
    "encode_cursor",
    "execute_query",
    "spec_hash",
    "validate_spec",
]
