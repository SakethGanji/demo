"""Unit tests — keyed row-level diff SQL builders.

The builders are pure, so they're exercised directly against in-memory DuckDB.
The contract that matters: every row lands in exactly one bucket, NULL is
treated as a value rather than a wildcard, and a non-unique key is detectable
before it can produce a wrong answer.
"""

from __future__ import annotations

import duckdb
import pytest

from app.features.data_accelerator.services.rowdiff import (
    DiffCounts,
    cell_changes_sql,
    column_change_counts_sql,
    counts_sql,
    duplicate_keys_sql,
    key_expr,
    sample_rows_sql,
)

KEYS = ["id"]
COMPARE = ["name", "amount"]
ALL_COLS = ["id", "name", "amount"]


@pytest.fixture
def conn():
    c = duckdb.connect()
    # id 1 unchanged, 2 amount changed, 3 removed, 4 amount changed, 5 added.
    c.execute("""CREATE TABLE a AS SELECT * FROM (VALUES
        (1,'Ana',10.0),(2,'Bob',20.0),(3,'Cy',30.0),(4,'Dee',40.0)
    ) t(id,name,amount)""")
    c.execute("""CREATE TABLE b AS SELECT * FROM (VALUES
        (1,'Ana',10.0),(2,'Bob',25.0),(4,'Dee',45.0),(5,'Eve',50.0)
    ) t(id,name,amount)""")
    yield c
    c.close()


def counts(conn, left="a", right="b", keys=KEYS, compare=COMPARE) -> DiffCounts:
    return DiffCounts(*conn.execute(counts_sql(left, right, keys, compare)).fetchone())


# --- bucket classification ----------------------------------------------------

def test_every_row_lands_in_exactly_one_bucket(conn):
    c = counts(conn)
    assert (c.added, c.removed, c.changed, c.unchanged) == (1, 1, 2, 1)
    # 4 rows on the left + 1 that only exists on the right.
    assert c.removed + c.changed + c.unchanged == 4
    assert c.added + c.changed + c.unchanged == 4
    assert c.total_changes == 4


def test_identical_versions_report_no_changes(conn):
    c = counts(conn, "a", "a")
    assert (c.added, c.removed, c.changed) == (0, 0, 0)
    assert c.unchanged == 4


def test_comparing_no_columns_makes_every_match_unchanged(conn):
    c = counts(conn, compare=[])
    assert c.changed == 0 and c.unchanged == 3
    assert (c.added, c.removed) == (1, 1)


def test_narrowing_the_compared_columns_narrows_the_diff(conn):
    # Only `name` is compared, and no name changed between the versions.
    c = counts(conn, compare=["name"])
    assert c.changed == 0 and c.unchanged == 3


# --- NULL semantics -----------------------------------------------------------

def test_null_to_value_is_a_change(conn):
    conn.execute("CREATE TABLE n1 AS SELECT * FROM (VALUES (1,NULL),(2,'x')) t(id,v)")
    conn.execute("CREATE TABLE n2 AS SELECT * FROM (VALUES (1,'set'),(2,'x')) t(id,v)")
    c = counts(conn, "n1", "n2", ["id"], ["v"])
    assert c.changed == 1 and c.unchanged == 1


def test_null_to_null_is_not_a_change(conn):
    conn.execute("CREATE TABLE n1 AS SELECT * FROM (VALUES (1,NULL)) t(id,v)")
    conn.execute("CREATE TABLE n2 AS SELECT * FROM (VALUES (1,NULL)) t(id,v)")
    c = counts(conn, "n1", "n2", ["id"], ["v"])
    assert c.changed == 0 and c.unchanged == 1


def test_a_null_key_still_matches_itself(conn):
    """A plain `=` would drop it and report the row as both added and removed."""
    conn.execute("CREATE TABLE n1 AS SELECT * FROM (VALUES (NULL,'a')) t(id,v)")
    conn.execute("CREATE TABLE n2 AS SELECT * FROM (VALUES (NULL,'b')) t(id,v)")
    c = counts(conn, "n1", "n2", ["id"], ["v"])
    assert (c.added, c.removed, c.changed) == (0, 0, 1)


# --- composite keys -----------------------------------------------------------

def test_composite_key_parts_cannot_run_together(conn):
    """('a','bc') and ('ab','c') must stay distinct rows."""
    conn.execute("CREATE TABLE k1 AS SELECT * FROM (VALUES ('a','bc',1),('ab','c',2)) t(p,q,v)")
    conn.execute("CREATE TABLE k2 AS SELECT * FROM (VALUES ('a','bc',9),('ab','c',2)) t(p,q,v)")
    c = counts(conn, "k1", "k2", ["p", "q"], ["v"])
    assert (c.added, c.removed, c.changed, c.unchanged) == (0, 0, 1, 1)

    keys = conn.execute(
        f"SELECT DISTINCT {key_expr('l', ['p', 'q'])} FROM k1 l").fetchall()
    assert len({k[0] for k in keys}) == 2


# --- per-column summary -------------------------------------------------------

def test_column_change_counts_rank_the_busiest_column(conn):
    rows = conn.execute(column_change_counts_sql("a", "b", KEYS, COMPARE)).fetchall()
    assert rows == [("amount", 2)]      # `name` never changed, so it's omitted


def test_column_change_counts_are_empty_when_nothing_changed(conn):
    assert conn.execute(
        column_change_counts_sql("a", "a", KEYS, COMPARE)).fetchall() == []


# --- the cell-level artifact shape --------------------------------------------

def test_cell_changes_cover_all_three_buckets_uniformly(conn):
    rows = conn.execute(
        cell_changes_sql("a", "b", KEYS, COMPARE, ALL_COLS)).fetchall()
    by_type: dict[str, list] = {}
    for change_type, key, column, before, after in rows:
        by_type.setdefault(change_type, []).append((key, column, before, after))

    assert sorted(by_type["changed"]) == [
        ("2", "amount", "20.0", "25.0"), ("4", "amount", "40.0", "45.0")]
    # An added row is every cell going NULL -> value.
    assert sorted(by_type["added"]) == [
        ("5", "amount", None, "50.0"), ("5", "id", None, "5"),
        ("5", "name", None, "Eve")]
    # A removed row is every cell going value -> NULL.
    assert sorted(by_type["removed"]) == [
        ("3", "amount", "30.0", None), ("3", "id", "3", None),
        ("3", "name", "Cy", None)]


def test_cell_changes_are_empty_between_identical_versions(conn):
    assert conn.execute(
        cell_changes_sql("a", "a", KEYS, COMPARE, ALL_COLS)).fetchall() == []


# --- samples ------------------------------------------------------------------

def test_added_and_removed_samples_return_whole_rows(conn):
    added = conn.execute(sample_rows_sql("b", "a", KEYS, 10)).fetchall()
    removed = conn.execute(sample_rows_sql("a", "b", KEYS, 10)).fetchall()
    assert added == [(5, "Eve", 50.0)] or added[0][0] == 5
    assert removed[0][0] == 3


def test_samples_respect_the_limit(conn):
    assert len(conn.execute(sample_rows_sql("b", "a", KEYS, 1)).fetchall()) <= 1


# --- key-quality guard --------------------------------------------------------

def test_duplicate_keys_are_detectable(conn):
    conn.execute("CREATE TABLE dup AS SELECT * FROM (VALUES (1,'a'),(1,'b'),(2,'c')) t(id,v)")
    assert conn.execute(duplicate_keys_sql("dup", ["id"])).fetchone()[0] == 1
    assert conn.execute(duplicate_keys_sql("a", ["id"])).fetchone()[0] == 0


def test_a_duplicated_key_would_double_count_if_not_refused(conn):
    """Why the guard exists: the join fans out and the counts stop summing."""
    conn.execute("CREATE TABLE d1 AS SELECT * FROM (VALUES (1,'a'),(1,'b')) t(id,v)")
    conn.execute("CREATE TABLE d2 AS SELECT * FROM (VALUES (1,'a')) t(id,v)")
    c = counts(conn, "d1", "d2", ["id"], ["v"])
    assert c.changed + c.unchanged > 1        # 2 left rows fan out against 1 right
    assert conn.execute(duplicate_keys_sql("d1", ["id"])).fetchone()[0] == 1
