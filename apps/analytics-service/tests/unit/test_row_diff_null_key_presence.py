"""Unit tests — row-diff presence tests must survive a NULL key value.

``join_condition`` deliberately matches NULL to NULL (``IS NOT DISTINCT
FROM``), so a row whose key column is NULL is present on both sides and the
counts classify it as matched. But the sample and cell-change builders decided
presence with ``WHERE <other>.<first key column> IS NULL``, which is *also*
true for a row that matched on a NULL key. That row was therefore reported in
``added_sample``, in ``removed_sample`` and as a NULL -> value / value -> NULL
pair in the persisted cell diff, while ``added``/``removed`` said 0.

In production that is a self-contradicting diff on any sheet whose key is
nullable — a reviewer approving version N+1 sees a row invented and deleted at
the same time, and the ``diff_output`` artifact that feeds downstream tooling
carries the same phantom rows.
"""

from __future__ import annotations

import duckdb
import pytest

from app.features.data_accelerator.services.rowdiff import (
    DiffCounts,
    cell_changes_sql,
    counts_sql,
    sample_rows_sql,
)

KEYS = ["id"]
COMPARE = ["name"]
ALL_COLS = ["id", "name"]


@pytest.fixture
def conn():
    """Both sides hold a row whose key is NULL; only its `name` differs.

    id=1 is unchanged, id=NULL changed 'Nobody' -> 'Ghost', id=2 is genuinely
    added on the right.
    """
    c = duckdb.connect()
    c.execute("""CREATE TABLE a AS SELECT * FROM (VALUES
        (1, 'Ana'), (NULL, 'Nobody')
    ) t(id, name)""")
    c.execute("""CREATE TABLE b AS SELECT * FROM (VALUES
        (1, 'Ana'), (NULL, 'Ghost'), (2, 'Bo')
    ) t(id, name)""")
    yield c
    c.close()


def _rows(conn, sql):
    cur = conn.execute(sql)
    names = [d[0] for d in cur.description]
    return [dict(zip(names, r)) for r in cur.fetchall()]


def test_the_counts_classify_a_null_keyed_row_as_matched_not_added(conn):
    """Baseline: counts_sql already uses the sentinel, so it gets this right."""
    c = DiffCounts(*conn.execute(counts_sql("a", "b", KEYS, COMPARE)).fetchone())
    assert (c.added, c.removed, c.changed, c.unchanged) == (1, 0, 1, 1)


def test_a_row_matched_on_a_null_key_is_absent_from_the_added_sample(conn):
    added = _rows(conn, sample_rows_sql("b", "a", KEYS, 50))
    assert [r["name"] for r in added] == ["Bo"], (
        "the NULL-keyed row exists on both sides and must not be sampled as added")


def test_a_row_matched_on_a_null_key_is_absent_from_the_removed_sample(conn):
    removed = _rows(conn, sample_rows_sql("a", "b", KEYS, 50))
    assert removed == [], (
        "nothing was removed — the NULL-keyed row is present in both versions")


def test_the_cell_diff_reports_a_null_keyed_row_as_changed_only(conn):
    """The persisted artifact must agree with the counts, bucket for bucket."""
    cells = _rows(conn, cell_changes_sql("a", "b", KEYS, COMPARE, ALL_COLS))
    buckets = sorted(
        ((c["change_type"], c["column_name"], c["before_value"], c["after_value"])
         for c in cells), key=str)
    assert buckets == [
        ("added", "id", None, "2"),
        ("added", "name", None, "Bo"),
        ("changed", "name", "Nobody", "Ghost"),
    ]


def test_the_bucket_totals_in_the_cell_diff_match_the_counts(conn):
    """The invariant the module docstring claims: buckets are disjoint."""
    c = DiffCounts(*conn.execute(counts_sql("a", "b", KEYS, COMPARE)).fetchone())
    cells = _rows(conn, cell_changes_sql("a", "b", KEYS, COMPARE, ALL_COLS))
    distinct = {(x["change_type"], x["row_key"]) for x in cells}
    assert len([k for k in distinct if k[0] == "added"]) == c.added
    assert len([k for k in distinct if k[0] == "removed"]) == c.removed
    assert len([k for k in distinct if k[0] == "changed"]) == c.changed
