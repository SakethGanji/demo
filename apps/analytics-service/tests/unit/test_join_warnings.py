"""Unit tests — join pre-flight probes (ROADMAP §23).

The whole value of the guided join is that these numbers are RIGHT before the
join runs, so each cardinality shape (1:1, 1:N, N:N) is checked against a real
DuckDB join rather than against the formula that produced the estimate.
"""

from __future__ import annotations

import duckdb
import pytest

from app.features.relationships import probes


@pytest.fixture
def conn():
    c = duckdb.connect()
    yield c
    c.close()


def make(conn, name, rows, columns="k INT, v VARCHAR"):
    conn.execute(f"CREATE TABLE {name} ({columns})")
    for row in rows:
        placeholders = ", ".join("?" for _ in row)
        conn.execute(f"INSERT INTO {name} VALUES ({placeholders})", list(row))


def estimate(conn, how="inner"):
    return conn.execute(probes.expansion_sql("l", "k", "r", "k", how)).fetchone()[0]


def actual(conn, how="inner"):
    join = "LEFT JOIN" if how == "left" else "JOIN"
    return conn.execute(f"SELECT COUNT(*) FROM l {join} r ON l.k = r.k").fetchone()[0]


# --- expansion estimates match reality ----------------------------------------

def test_one_to_one(conn):
    make(conn, "l", [(1, "a"), (2, "b")])
    make(conn, "r", [(1, "x"), (2, "y")])
    assert estimate(conn) == actual(conn) == 2


def test_one_to_many(conn):
    make(conn, "l", [(1, "a"), (2, "b")])
    make(conn, "r", [(1, "x"), (1, "y"), (1, "z"), (2, "w")])
    assert estimate(conn) == actual(conn) == 4


def test_many_to_many_multiplies(conn):
    make(conn, "l", [(1, "a"), (1, "b"), (1, "c")])
    make(conn, "r", [(1, "x"), (1, "y")])
    # 3 left rows × 2 right rows for the same key.
    assert estimate(conn) == actual(conn) == 6


def test_null_keys_never_join(conn):
    make(conn, "l", [(1, "a"), (None, "b")])
    make(conn, "r", [(1, "x"), (None, "y")])
    assert estimate(conn) == actual(conn) == 1


def test_left_join_keeps_unmatched_left_rows(conn):
    make(conn, "l", [(1, "a"), (2, "b"), (None, "c")])
    make(conn, "r", [(1, "x"), (1, "y")])
    # 2 matched rows for key 1, plus the unmatched key-2 row and the NULL row.
    assert estimate(conn, "left") == actual(conn, "left") == 4


def test_inner_join_with_no_overlap_is_empty(conn):
    make(conn, "l", [(1, "a")])
    make(conn, "r", [(2, "x")])
    assert estimate(conn) == actual(conn) == 0


def test_expansion_over_a_mixed_workload(conn):
    make(conn, "l", [(1, "a"), (1, "b"), (2, "c"), (3, "d"), (None, "e")])
    make(conn, "r", [(1, "x"), (1, "y"), (2, "z")])
    # key 1: 2×2 = 4, key 2: 1×1 = 1, key 3 and NULL: unmatched.
    assert estimate(conn) == actual(conn) == 5
    assert estimate(conn, "left") == actual(conn, "left") == 7


# --- duplicate keys and cardinality classification ----------------------------

def test_duplicate_keys_counted_per_side(conn):
    make(conn, "l", [(1, "a"), (1, "b"), (2, "c"), (None, "d"), (None, "e")])
    dup_keys, dup_rows = conn.execute(
        probes.duplicate_keys_sql("l", "k")).fetchone()
    assert dup_keys == 1        # only key 1 repeats
    assert dup_rows == 2        # accounting for two rows
    # NULLs repeat too, but they cannot join, so they are not a join hazard.


def test_many_to_many_needs_both_sides_to_repeat():
    assert probes.is_many_to_many(probes.JoinShape(2, 3)) is True
    assert probes.is_many_to_many(probes.JoinShape(0, 3)) is False
    assert probes.is_many_to_many(probes.JoinShape(2, 0)) is False
    assert probes.is_many_to_many(probes.JoinShape(0, 0)) is False


def test_expansion_factor_flags_a_blow_up():
    assert probes.expansion_factor(600, 100) == 6.0
    assert probes.expansion_factor(100, 100) == 1.0
    assert probes.expansion_factor(0, 0) == 0.0     # no rows, no division


# --- unmatched rates ----------------------------------------------------------

def test_unmatched_counts_null_keys_as_unmatched(conn):
    make(conn, "l", [(1, "a"), (2, "b"), (None, "c"), (99, "d")])
    make(conn, "r", [(1, "x")])
    total, unmatched = conn.execute(
        probes.unmatched_sql("l", "k", "r", "k")).fetchone()
    assert total == 4
    assert unmatched == 3       # key 2, the NULL, and key 99
    assert probes.ratio(unmatched, total) == 0.75


def test_unmatched_is_measured_in_both_directions(conn):
    make(conn, "l", [(1, "a")])
    make(conn, "r", [(1, "x"), (2, "y"), (3, "z")])
    _, left_unmatched = conn.execute(
        probes.unmatched_sql("l", "k", "r", "k")).fetchone()
    _, right_unmatched = conn.execute(
        probes.unmatched_sql("r", "k", "l", "k")).fetchone()
    assert left_unmatched == 0
    assert right_unmatched == 2


# --- column collisions --------------------------------------------------------

def test_collisions_exclude_the_join_keys():
    collisions = probes.column_collisions(
        left_columns=["customer_id", "name", "created_at"],
        right_columns=["customer_id", "name", "tier"],
        left_key="customer_id", right_key="customer_id")
    assert collisions == ["name"]     # the key itself is not a collision


def test_no_collisions_when_names_are_distinct():
    assert probes.column_collisions(["a", "k"], ["b", "k"], "k", "k") == []


def test_collisions_are_sorted_and_deduplicated():
    assert probes.column_collisions(["z", "a", "k"], ["a", "z", "k"], "k", "k") \
        == ["a", "z"]
