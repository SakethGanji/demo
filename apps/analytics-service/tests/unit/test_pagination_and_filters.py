"""Unit tests — Page envelope math and the structured-filter compiler."""

from __future__ import annotations

import duckdb
import pytest
from fastapi import HTTPException

from app.api.pagination import Page, PageParams
from app.shared.filters import apply_filters, compile_filter


# --- Page envelope -----------------------------------------------------------

def test_page_of_builds_envelope():
    page = Page.of(["a", "b"], total=10, params=PageParams(limit=2, offset=4))
    assert page.model_dump() == {"items": ["a", "b"], "total": 10,
                                 "limit": 2, "offset": 4}


def test_page_total_is_independent_of_items_len():
    page = Page.of([], total=7, params=PageParams(limit=50, offset=100))
    assert page.items == [] and page.total == 7


# --- compile_filter ----------------------------------------------------------

def test_compile_eq_parameterizes_value():
    params = []
    sql = compile_filter({"column": "region", "op": "eq", "value": "EU"}, params)
    assert sql == '"region" = ?' and params == ["EU"]


def test_compile_in_and_between():
    params = []
    sql = compile_filter({"column": "n", "op": "in", "value": [1, 2, 3]}, params)
    assert sql == '"n" IN (?, ?, ?)' and params == [1, 2, 3]
    params = []
    sql = compile_filter({"column": "n", "op": "between", "value": [5, 9]}, params)
    assert sql == '"n" BETWEEN ? AND ?' and params == [5, 9]


def test_compile_group_nests_with_logic():
    params = []
    sql = compile_filter({"logic": "or", "conditions": [
        {"column": "a", "op": "is_null"},
        {"column": "b", "op": "gt", "value": 3},
    ]}, params)
    assert sql == '("a" IS NULL OR "b" > ?)' and params == [3]


def test_compile_rejects_bad_input():
    for bad in ({"column": "a", "op": "no_such_op", "value": 1},
                {"op": "eq", "value": 1},                       # missing column
                {"column": "a", "op": "in", "value": []},        # empty list
                {"column": "a", "op": "between", "value": [1]},  # not a pair
                {"logic": "xor", "conditions": [{"column": "a", "op": "is_null"}]}):
        with pytest.raises(HTTPException) as e:
            compile_filter(bad, [])
        assert e.value.status_code == 400, bad


def test_compile_quotes_hostile_column_names():
    params = []
    sql = compile_filter({"column": 'x"; DROP TABLE y; --', "op": "eq",
                          "value": 1}, params)
    assert sql.startswith('"x""; DROP TABLE y; --"')  # stays inside the ident


# --- apply_filters end-to-end on in-memory DuckDB ----------------------------

@pytest.fixture()
def conn():
    c = duckdb.connect()
    c.execute("""
        CREATE TABLE t AS SELECT * FROM (VALUES
            (1, 'EU', 10.0), (2, 'US', 20.0), (3, 'EU', 30.0), (4, 'APAC', NULL)
        ) AS v(id, region, amount)
    """)
    yield c
    c.close()


def test_apply_structured_filters(conn):
    table, matched, desc = apply_filters(
        conn, "t", [{"column": "region", "op": "eq", "value": "EU"},
                    {"column": "amount", "op": "gte", "value": 20}])
    assert matched == 1 and "structured" in desc
    assert conn.execute(f"SELECT id FROM {table}").fetchone()[0] == 3


def test_apply_filter_expr_and_combined(conn):
    table, matched, _ = apply_filters(conn, "t", None, "amount > 5")
    assert matched == 3
    table, matched, _ = apply_filters(
        conn, "t", [{"column": "region", "op": "neq", "value": "US"}],
        "amount > 5")
    assert matched == 2


def test_apply_no_filters_is_passthrough(conn):
    table, matched, desc = apply_filters(conn, "t", None, None)
    assert table == "t" and matched is None and desc is None


def test_apply_filter_expr_blocks_injection(conn):
    with pytest.raises(HTTPException):
        apply_filters(conn, "t", None, "1=1; DROP TABLE t")


def test_legacy_dict_filters_still_400_on_a_bad_operator(conn):
    """The sampling path takes ``list[dict]`` straight to ``compile_filter``.

    It never went through the typed DSL, so it always rejected an unknown
    operator — tightening the typed union must not have moved that behaviour,
    and must not have started swallowing it either.
    """
    with pytest.raises(HTTPException) as e:
        apply_filters(conn, "t", [{"column": "region", "op": "greater_than",
                                   "value": "EU"}])
    assert e.value.status_code == 400
    assert "greater_than" in e.value.detail

    # A well-formed legacy filter is unaffected.
    _, matched, _ = apply_filters(conn, "t", [{"column": "region", "op": "eq",
                                               "value": "EU"}])
    assert matched == 2


def test_top_n_uses_filter_src_alias(conn):
    table, matched, _ = apply_filters(
        conn, "t", [{"column": "amount", "op": "top_n", "value": 2}])
    assert matched == 2
    vals = {r[0] for r in conn.execute(f"SELECT amount FROM {table}").fetchall()}
    assert vals == {20.0, 30.0}
