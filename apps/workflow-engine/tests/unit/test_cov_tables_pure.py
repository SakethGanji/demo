"""Data-table engine pure logic: type checks, column validation, row checks, filter compilation (no DB)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import postgresql

from src.services.data_table_service import MAX_BULK, DataTableService, _type_ok
from src.services.project_service import ProjectError

COLS = [{"name": "email", "type": "string", "required": True}, {"name": "total", "type": "number", "required": False},
        {"name": "qty", "type": "integer", "required": False}, {"name": "status", "type": "string", "required": False, "default": "open"},
        {"name": "at", "type": "datetime", "required": False}, {"name": "meta", "type": "json", "required": False},
        {"name": "ok", "type": "boolean", "required": False}]
T = SimpleNamespace(columns=COLS)
svc = DataTableService(None)  # type: ignore[arg-type]


@pytest.mark.parametrize("t,v,ok", [
    ("string", "x", True), ("string", 1, False), ("number", 1.5, True), ("number", True, False), ("number", "1", False),
    ("integer", 3, True), ("integer", 3.0, True), ("integer", 3.5, False), ("integer", True, False),
    ("boolean", False, True), ("boolean", 0, False), ("datetime", "2026-01-02T03:04:05Z", True),
    ("datetime", "2026-01-02", True), ("datetime", "yesterday", False), ("datetime", 5, False),
    ("json", {"a": [1]}, True), ("json", 5, True), ("string", None, True), ("integer", None, True),
])
def test_type_ok(t, v, ok):
    assert _type_ok(t, v) is ok


@pytest.mark.parametrize("cols", [
    "nope", [{"name": "1bad", "type": "string"}], [{"name": "x", "type": "float"}], [{"name": "x"}], ["x"],
    [{"name": "a", "type": "string"}, {"name": "a", "type": "number"}],
    [{"name": "id", "type": "string"}], [{"name": "created_at", "type": "datetime"}],
    [{"name": "n", "type": "integer", "default": "x"}], [{"name": "a-b", "type": "string"}],
])
def test_validate_columns_rejects(cols):
    with pytest.raises(ProjectError) as e:
        DataTableService._validate_columns(cols)
    assert e.value.status == 422 and e.value.code == "invalid_column"


def test_validate_columns_normalises():
    out = DataTableService._validate_columns([{"name": "n", "type": "integer", "default": 3, "junk": 1}, {"name": "s", "type": "string", "default": None}])
    assert out == [{"name": "n", "type": "integer", "required": False, "default": 3}, {"name": "s", "type": "string", "required": False}]


def test_check_row_defaults_required_unknown_and_types():
    out, errs = DataTableService._check_row(T, {"email": "a@x.io"}, 0)
    assert errs == [] and out["status"] == "open" and "total" not in out
    _, errs = DataTableService._check_row(T, {}, 3)
    assert errs == [{"row": 3, "column": "email", "error": "is required"}]
    _, errs = DataTableService._check_row(T, {"email": None}, 0)
    assert errs == [{"row": 0, "column": "email", "error": "is required"}]
    _, errs = DataTableService._check_row(T, {"email": "a", "ghost": 1, "total": "x", "qty": 1.5, "at": "bad", "ok": "yes"}, 1)
    assert {(e["column"], e["error"]) for e in errs} == {("ghost", "unknown column"), ("total", "expected number"),
                                                         ("qty", "expected integer"), ("at", "expected datetime"), ("ok", "expected boolean")}
    _, errs = DataTableService._check_row(T, "notadict", 2)
    assert errs == [{"row": 2, "column": None, "error": "row data must be an object"}]


def test_check_row_partial_skips_required_and_defaults():
    out, errs = DataTableService._check_row(T, {"total": 1}, 0, partial=True)
    assert errs == [] and out == {"total": 1}


def _sql(flt):
    return str(svc._compile(T, flt).compile(dialect=postgresql.dialect()))


def test_compile_empty_filter_is_none():
    assert svc._compile(T, None) is None and svc._compile(T, {}) is None


@pytest.mark.parametrize("flt,code", [
    ({"ghost": 1}, "unknown_column"), ({"ghost": {"$gt": 1}}, "unknown_column"),
    ({"$or": [{"ghost": 1}]}, "unknown_column"), ({"$and": "x"}, "invalid_filter"), ("str", "invalid_filter"),
    ({"total": {"$weird": 1}}, "invalid_filter"), ({"total": {"$in": 5}}, "invalid_filter"),
    ({"id": {"$contains": "x"}}, "invalid_filter"), ({"$or": [5]}, "invalid_filter"),
    ({"email'); DROP TABLE x;--": 1}, "unknown_column"),
])
def test_compile_rejects(flt, code):
    with pytest.raises(ProjectError) as e:
        svc._compile(T, flt)
    assert e.value.status == 422 and e.value.code == code


def test_compile_values_are_bound_parameters_not_inlined():
    sql = _sql({"email": {"$contains": "'; DROP TABLE x; --"}, "total": {"$gte": 5}})
    assert "DROP TABLE" not in sql and "ilike" in sql.lower()
    assert "numeric" in sql.lower()  # numeric compare casts the jsonb text
    assert "@>" in _sql({"status": "paid"})
    assert "has_key" in _sql({"meta": {"$exists": True}}) or "?" in _sql({"meta": {"$exists": True}})
    assert "NOT" in _sql({"status": {"$ne": "x"}}).upper()
    assert _sql({"id": {"$in": [1, 2]}}).lower().count("in") >= 1
    assert "OR" in _sql({"$or": [{"status": "a"}, {"status": "b"}]}).upper()


def test_compile_empty_in_matches_nothing():
    assert "false" in _sql({"status": {"$in": []}}).lower()


def test_bulk_limit_constant_and_insert_guard():
    import asyncio
    with pytest.raises(ProjectError) as e:
        asyncio.run(svc.insert_rows(SimpleNamespace(columns=COLS, id="t"), [{"email": "a"}] * (MAX_BULK + 1)))
    assert e.value.code == "too_many_rows"
