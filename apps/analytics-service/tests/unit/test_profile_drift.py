"""Unit tests for §9 profile-drift computation — pure over profile dicts."""

from __future__ import annotations

from app.features.data_accelerator.services.diffs import compute_profile_drift


def _profile(columns, row_count=5, dup=0):
    return {"row_count": row_count, "duplicate_row_count": dup, "columns": columns}


def test_numeric_and_null_rate_deltas():
    a = _profile([{"name": "score", "dtype": "numeric", "null_percent": 0.0,
                   "unique_count": 5, "mean": 10.0, "std": 2.0}])
    b = _profile([{"name": "score", "dtype": "numeric", "null_percent": 20.0,
                   "unique_count": 4, "mean": 12.5, "std": 3.0}],
                 row_count=6, dup=1)
    d = compute_profile_drift(a, b, sheet_key="data")
    assert d.sheet_key == "data"
    assert d.row_count_delta == 1 and d.duplicate_rows_delta == 1
    col = d.columns[0]
    assert col.null_percent_delta == 20.0
    assert col.unique_count_delta == -1
    assert col.mean_delta == 2.5 and col.std_delta == 1.0
    assert col.added_categories == [] and col.removed_categories == []


def test_category_adds_and_removes():
    a = _profile([{"name": "tier", "dtype": "categorical", "null_percent": 0.0,
                   "unique_count": 2,
                   "top_values": [{"value": "gold"}, {"value": "silver"}]}])
    b = _profile([{"name": "tier", "dtype": "categorical", "null_percent": 0.0,
                   "unique_count": 2,
                   "top_values": [{"value": "gold"}, {"value": "copper"}]}])
    col = compute_profile_drift(a, b).columns[0]
    assert col.added_categories == ["copper"]
    assert col.removed_categories == ["silver"]


def test_only_common_columns_compared():
    a = _profile([{"name": "gone", "dtype": "text", "null_percent": 0.0, "unique_count": 1}])
    b = _profile([{"name": "new", "dtype": "text", "null_percent": 0.0, "unique_count": 1}])
    assert compute_profile_drift(a, b).columns == []


def test_appearing_and_disappearing_columns_are_reported():
    # The §21 shape: a transformation drops a column and computes a new one, so
    # the drift has to say what left and what arrived, not just what changed.
    a = _profile([
        {"name": "city", "dtype": "text", "null_percent": 0.0, "unique_count": 3},
        {"name": "amount", "dtype": "numeric", "null_percent": 0.0, "unique_count": 4},
    ])
    b = _profile([
        {"name": "amount", "dtype": "numeric", "null_percent": 0.0, "unique_count": 4},
        {"name": "double_amount", "dtype": "numeric", "null_percent": 0.0, "unique_count": 4},
    ])
    drift = compute_profile_drift(a, b)
    assert drift.removed_columns == ["city"]
    assert drift.added_columns == ["double_amount"]
    assert [c.column for c in drift.columns] == ["amount"]


def test_identical_column_sets_report_no_adds_or_removes():
    a = _profile([{"name": "x", "dtype": "text", "null_percent": 0.0, "unique_count": 1}])
    b = _profile([{"name": "x", "dtype": "text", "null_percent": 0.0, "unique_count": 2}])
    drift = compute_profile_drift(a, b)
    assert drift.added_columns == [] and drift.removed_columns == []


def test_missing_stats_yield_none_deltas():
    a = _profile([{"name": "x", "dtype": "text", "null_percent": None, "unique_count": None}])
    b = _profile([{"name": "x", "dtype": "text", "null_percent": 5.0, "unique_count": 3}])
    col = compute_profile_drift(a, b).columns[0]
    assert col.null_percent_delta is None and col.unique_count_delta is None
    assert col.mean_delta is None
