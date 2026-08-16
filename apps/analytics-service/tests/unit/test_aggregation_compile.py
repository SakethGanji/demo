"""Unit tests — aggregation compile helpers and request-schema validators.

Only the pieces callable without storage/DB: ``_bucket_expr`` /
``_filter_columns`` from the aggregation service (compiled SQL is also run
through an in-memory DuckDB table to pin bucket semantics), and the pydantic
validators on GroupByBucket / HavingCondition / RelatedSheetLink.
"""

from __future__ import annotations

import duckdb
import pytest
from pydantic import ValidationError

from app.features.data_accelerator.schemas import (
    GroupByBucket,
    HavingCondition,
    RelatedSheetLink,
)
from app.features.data_accelerator.services.aggregation import (
    _bucket_expr,
    _filter_columns,
)


# --- _bucket_expr: compiled SQL text -----------------------------------------

def test_date_trunc_casts_for_varchar_iso_dates():
    expr = _bucket_expr(GroupByBucket(column="created", date_trunc="month"), "df")
    assert expr == "date_trunc('month', CAST(\"created\" AS TIMESTAMP))"


def test_bin_width_floors_to_width_multiples():
    expr = _bucket_expr(GroupByBucket(column="total", bin_width=10), "df")
    assert expr == 'FLOOR("total" / 10.0) * 10.0'


def test_bucket_column_identifier_is_quoted():
    expr = _bucket_expr(GroupByBucket(column='evil"; DROP', bin_width=1), "df")
    assert '"evil""; DROP"' in expr  # doubled quote, no injection


def test_bin_count_expr_targets_the_given_source():
    expr = _bucket_expr(GroupByBucket(column="total", bin_count=4), "_agg_src")
    assert "FROM _agg_src" in expr and "FROM df" not in expr


# --- _bucket_expr: semantics, executed in DuckDB -----------------------------

def _bins(expr, values):
    conn = duckdb.connect()
    vals = ", ".join(f"({v!r})" if v is not None else "(NULL)" for v in values)
    conn.execute(f'CREATE TABLE df AS SELECT * FROM (VALUES {vals}) AS t("total")')
    rows = conn.execute(f'SELECT {expr} FROM df ORDER BY "total" NULLS FIRST').fetchall()
    conn.close()
    return [r[0] for r in rows]


def test_bin_count_covers_range_and_top_value_lands_in_bin_n():
    expr = _bucket_expr(GroupByBucket(column="total", bin_count=4), "df")
    # min=0, max=100 → width 25; the max itself must land in bin 4, not 5.
    assert _bins(expr, [0, 24, 25, 99, 100]) == [1, 1, 2, 4, 4]


def test_bin_count_null_and_constant_column():
    expr = _bucket_expr(GroupByBucket(column="total", bin_count=3), "df")
    assert _bins(expr, [None, 7, 7]) == [None, 1, 1]  # hi == lo → everything bin 1


def test_bin_width_semantics_include_negatives():
    expr = _bucket_expr(GroupByBucket(column="total", bin_width=10), "df")
    assert _bins(expr, [-5, 0, 9, 10, 19]) == [-10.0, 0.0, 0.0, 10.0, 10.0]


# --- _filter_columns ---------------------------------------------------------

def test_filter_columns_recurses_nested_groups():
    group = {"conditions": [
        {"column": "a", "op": "eq", "value": 1},
        {"logic": "or", "conditions": [
            {"column": "b", "op": "gt", "value": 2},
            {"conditions": [{"column": "c", "op": "is_null"}]},
        ]},
        {"op": "eq", "value": 3},  # no column key → ignored
    ]}
    assert _filter_columns(group) == {"a", "b", "c"}
    assert _filter_columns({"conditions": []}) == set()


# --- GroupByBucket validators ------------------------------------------------

def test_bucket_requires_exactly_one_kind():
    with pytest.raises(ValidationError, match="exactly one"):
        GroupByBucket(column="x")
    with pytest.raises(ValidationError, match="exactly one"):
        GroupByBucket(column="x", date_trunc="month", bin_width=5)
    with pytest.raises(ValidationError, match="exactly one"):
        GroupByBucket(column="x", bin_width=5, bin_count=3)


def test_bucket_bounds_bin_width_positive_bin_count_at_least_one():
    with pytest.raises(ValidationError):
        GroupByBucket(column="x", bin_width=0)
    with pytest.raises(ValidationError):
        GroupByBucket(column="x", bin_width=-2)
    with pytest.raises(ValidationError):
        GroupByBucket(column="x", bin_count=0)
    assert GroupByBucket(column="x", bin_count=1).bin_count == 1


def test_bucket_rejects_unknown_date_trunc_unit():
    with pytest.raises(ValidationError):
        GroupByBucket(column="x", date_trunc="fortnight")
    assert GroupByBucket(column="x", date_trunc="quarter").alias is None


# --- HavingCondition ---------------------------------------------------------

def test_having_op_is_whitelisted_and_value_numeric():
    assert HavingCondition(column="total_sum", op="gte", value=5).op == "gte"
    with pytest.raises(ValidationError):
        HavingCondition(column="total_sum", op="like", value=5)
    with pytest.raises(ValidationError):
        HavingCondition(column="total_sum", op="eq", value="high")
    with pytest.raises(ValidationError):
        HavingCondition(column="", op="eq", value=1)  # alias must be non-empty


# --- RelatedSheetLink --------------------------------------------------------

def test_related_link_keys_both_or_neither():
    both = RelatedSheetLink(sheet="orders", left_on="id", right_on="cust_id")
    assert (both.left_on, both.right_on) == ("id", "cust_id")
    neither = RelatedSheetLink(sheet="orders")  # defaults from FK rules later
    assert neither.left_on is None and neither.right_on is None
    with pytest.raises(ValidationError, match="together"):
        RelatedSheetLink(sheet="orders", left_on="id")
    with pytest.raises(ValidationError, match="together"):
        RelatedSheetLink(sheet="orders", right_on="cust_id")
