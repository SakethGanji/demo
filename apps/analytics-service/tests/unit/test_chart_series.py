"""Unit tests — shaping tabular results into chart series.

The contract: a chart saved with an empty config still renders something
sensible, config wins wherever it is set, a stale config degrades instead of
breaking, and every series is aligned to one shared category axis so consumers
can zip them positionally.
"""

from __future__ import annotations

from app.features.library.charts import (
    MAX_CATEGORIES,
    build_series,
    infer_fields,
    numeric_columns,
)

COLUMNS = ["region", "revenue", "orders"]
ROWS = [
    {"region": "NY", "revenue": 100.0, "orders": 3},
    {"region": "LA", "revenue": 250.0, "orders": 5},
    {"region": "SF", "revenue": 75.0, "orders": 2},
]


# --- column typing ------------------------------------------------------------

def test_numeric_columns_detected_from_values():
    assert numeric_columns(COLUMNS, ROWS) == ["revenue", "orders"]


def test_a_column_with_any_non_numeric_value_is_not_numeric():
    rows = [{"a": 1}, {"a": "oops"}]
    assert numeric_columns(["a"], rows) == []


def test_nulls_do_not_disqualify_a_numeric_column():
    rows = [{"a": 1}, {"a": None}, {"a": 3}]
    assert numeric_columns(["a"], rows) == ["a"]


def test_an_all_null_column_is_not_numeric():
    assert numeric_columns(["a"], [{"a": None}]) == []


def test_booleans_are_not_treated_as_numbers():
    assert numeric_columns(["flag"], [{"flag": True}]) == []


# --- field inference ----------------------------------------------------------

def test_fields_are_inferred_when_config_is_empty():
    """A chart saved with no config must still render."""
    x, y, series = infer_fields(COLUMNS, ROWS, {})
    assert x == "region"                      # first non-numeric column
    assert y == ["revenue", "orders"]         # every numeric column
    assert series is None


def test_config_overrides_inference():
    x, y, series = infer_fields(COLUMNS, ROWS,
                                {"x_field": "revenue", "y_fields": ["orders"]})
    assert (x, y, series) == ("revenue", ["orders"], None)


def test_a_single_y_field_may_be_a_bare_string():
    _, y, _ = infer_fields(COLUMNS, ROWS, {"y": "revenue"})
    assert y == ["revenue"]


def test_short_config_keys_are_accepted():
    x, y, series = infer_fields(COLUMNS, ROWS,
                                {"x": "region", "y": ["revenue"], "series": "orders"})
    assert (x, y, series) == ("region", ["revenue"], "orders")


def test_a_stale_config_degrades_instead_of_breaking():
    """A field that no longer exists is ignored, not raised."""
    x, y, series = infer_fields(COLUMNS, ROWS,
                                {"x_field": "deleted", "y_fields": ["gone", "revenue"],
                                 "series_field": "vanished"})
    assert x == "region"          # fell back to inference
    assert y == ["revenue"]       # kept only what still exists
    assert series is None


def test_the_series_field_is_excluded_from_inferred_y_fields():
    _, y, series = infer_fields(COLUMNS, ROWS, {"series_field": "orders"})
    assert series == "orders" and "orders" not in y


def test_an_all_numeric_result_still_gets_an_x_field():
    columns, rows = ["a", "b"], [{"a": 1, "b": 2}]
    x, y, _ = infer_fields(columns, rows, {})
    assert x == "a" and y == ["b"]


# --- wide shape (no series field) --------------------------------------------

def test_one_series_per_y_field_aligned_to_categories():
    data = build_series(ROWS, COLUMNS, {})
    assert data.categories == ["NY", "LA", "SF"]
    assert [s["name"] for s in data.series] == ["revenue", "orders"]
    assert data.series[0]["data"] == [100.0, 250.0, 75.0]
    assert data.series[1]["data"] == [3, 5, 2]
    # Every series is the same length as the category axis.
    assert all(len(s["data"]) == len(data.categories) for s in data.series)


def test_missing_combinations_are_padded_with_none():
    rows = [{"region": "NY", "revenue": 1.0}, {"region": "LA"}]
    data = build_series(rows, ["region", "revenue"], {})
    assert data.categories == ["NY", "LA"]
    assert data.series[0]["data"] == [1.0, None]


def test_categories_keep_first_seen_order_and_deduplicate():
    rows = [{"k": "b", "v": 1}, {"k": "a", "v": 2}, {"k": "b", "v": 3}]
    data = build_series(rows, ["k", "v"], {})
    assert data.categories == ["b", "a"]


def test_null_category_labels_become_empty_strings():
    rows = [{"k": None, "v": 1}]
    data = build_series(rows, ["k", "v"], {})
    assert data.categories == [""]


# --- long shape (series field) ------------------------------------------------

def test_a_series_field_pivots_rows_into_one_series_each():
    """The shape a stacked bar or multi-line chart needs."""
    rows = [
        {"month": "Jan", "country": "US", "revenue": 10.0},
        {"month": "Jan", "country": "UK", "revenue": 5.0},
        {"month": "Feb", "country": "US", "revenue": 20.0},
    ]
    data = build_series(rows, ["month", "country", "revenue"],
                        {"x_field": "month", "series_field": "country",
                         "y_fields": ["revenue"]})
    assert data.categories == ["Jan", "Feb"]
    assert [s["name"] for s in data.series] == ["UK", "US"]      # sorted
    uk = next(s for s in data.series if s["name"] == "UK")
    us = next(s for s in data.series if s["name"] == "US")
    assert us["data"] == [10.0, 20.0]
    assert uk["data"] == [5.0, None]        # UK has no February row


def test_a_series_field_with_no_value_field_yields_no_series():
    rows = [{"k": "a", "s": "x"}]
    data = build_series(rows, ["k", "s"], {"x_field": "k", "series_field": "s"})
    assert data.categories == ["a"] and data.series == []


# --- edges --------------------------------------------------------------------

def test_empty_results_render_empty():
    data = build_series([], [], {})
    assert data.categories == [] and data.series == []


def test_the_category_axis_is_capped():
    rows = [{"k": f"c{i}", "v": i} for i in range(MAX_CATEGORIES + 50)]
    data = build_series(rows, ["k", "v"], {})
    assert len(data.categories) == MAX_CATEGORIES
    assert data.truncated is True
    assert all(len(s["data"]) == MAX_CATEGORIES for s in data.series)


def test_a_small_result_is_not_marked_truncated():
    assert build_series(ROWS, COLUMNS, {}).truncated is False
