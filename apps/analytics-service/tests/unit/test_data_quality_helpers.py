"""§16 pure helpers — SQL builders + profile lifting, over in-memory DuckDB."""

from __future__ import annotations

import duckdb
import pandas as pd

from app.features.explorer.data_quality import (
    duplicate_groups_sql,
    duplicate_totals_sql,
    group_examples_sql,
    missing_from_profile,
    most_missing_rows_sql,
    null_counts_sql,
)


def _conn():
    df = pd.DataFrame({
        "Region": ["EU", "EU", "EU", None, None, "US"],
        "Amount": [100.0, 100.0, 250.0, 5.0, 5.0, None],
    })
    conn = duckdb.connect(":memory:")
    conn.register("df", df)
    return conn


def test_duplicate_groups_biggest_first_with_null_keys():
    conn = _conn()
    rows = conn.execute(duplicate_groups_sql(["Region", "Amount"], 10)).fetchall()
    # Two exact-duplicate groups of 2: (EU, 100.0) and (NULL, 5.0).
    assert len(rows) == 2
    assert {(r[0], r[1], r[2]) for r in rows} == {("EU", 100.0, 2), (None, 5.0, 2)}


def test_duplicate_totals_are_uncapped():
    conn = _conn()
    groups, dup_rows = conn.execute(duplicate_totals_sql(["Region", "Amount"])).fetchone()
    assert (groups, dup_rows) == (2, 4)
    # Subset grouping widens the EU group to 3 rows.
    groups, dup_rows = conn.execute(duplicate_totals_sql(["Region"])).fetchone()
    assert (groups, dup_rows) == (2, 5)


def test_group_examples_match_null_keys():
    conn = _conn()
    rows = conn.execute(group_examples_sql(["Region", "Amount"], 5),
                        [None, 5.0]).fetchall()
    assert len(rows) == 2 and all(r[0] is None and r[1] == 5.0 for r in rows)


def test_null_counts_positional():
    conn = _conn()
    total, region_nn, amount_nn = conn.execute(
        null_counts_sql(["Region", "Amount"])).fetchone()
    assert (total, total - region_nn, total - amount_nn) == (6, 2, 1)


def test_most_missing_rows_only_rows_with_nulls():
    conn = _conn()
    res = conn.execute(most_missing_rows_sql(["Region", "Amount"], 5))
    rows = res.fetchall()
    assert len(rows) == 3            # two null-Region rows + one null-Amount row
    assert all(r[-1] == 1 for r in rows)


def test_missing_from_profile_maps_names_and_rounds():
    profile = {"columns": [
        {"name": "Region", "null_count": 2, "null_percent": 33.333333},
        {"name": "Amount", "null_count": 0, "null_percent": 0.0},
    ]}
    out = missing_from_profile(profile, {"Region": "region", "Amount": "amount"})
    assert [(c.column, c.null_count, c.null_percent) for c in out] == [
        ("region", 2, 33.33), ("amount", 0, 0.0)]
