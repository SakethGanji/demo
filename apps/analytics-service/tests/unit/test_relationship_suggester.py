"""Unit tests — relationship discovery scoring and probes (ROADMAP §22).

The scorers are pure; the probe builders return SQL, so they are exercised
against an in-memory DuckDB pair of tables standing in for two sheets.
"""

from __future__ import annotations

import duckdb
import pytest

from app.features.relationships import probes


@pytest.fixture
def conn():
    c = duckdb.connect()
    # Customers is the parent (unique id); Orders is the child, with one
    # orphan key and one NULL key.
    c.execute("""CREATE TABLE customers AS SELECT * FROM (VALUES
        (1, 'gold'), (2, 'silver'), (3, 'gold')) t(customer_id, tier)""")
    c.execute("""CREATE TABLE orders AS SELECT * FROM (VALUES
        (10, 1), (11, 2), (12, 1), (13, 999), (14, NULL))
        t(order_id, customer_id)""")
    yield c
    c.close()


# --- the type-family gate -----------------------------------------------------

def test_type_families():
    assert probes.type_family("BIGINT") == "numeric"
    assert probes.type_family("DECIMAL(18,2)") == "numeric"
    assert probes.type_family("VARCHAR") == "text"
    assert probes.type_family("TIMESTAMP") == "temporal"
    assert probes.type_family("STRUCT(a INT)") == "other"


def test_numbers_never_pair_with_strings():
    assert probes.types_compatible("BIGINT", "INTEGER") is True
    assert probes.types_compatible("VARCHAR", "VARCHAR") is True
    assert probes.types_compatible("BIGINT", "VARCHAR") is False
    # An uncomparable type is rejected outright rather than probed.
    assert probes.types_compatible("STRUCT(a INT)", "STRUCT(a INT)") is False


# --- name scoring -------------------------------------------------------------

def test_identical_names_score_highest():
    assert probes.name_score("customer_id", "customer_id", "Customers") == 1.0


def test_foreign_key_convention_pointing_at_id():
    assert probes.name_score("customer_id", "id", "Customers") == 1.0


def test_sheet_named_key_convention():
    assert probes.name_score("customer_id", "pk", "Customer") == 0.9


def test_unrelated_names_score_zero():
    assert probes.name_score("total", "tier", "Customers") == 0.0


def test_a_generic_id_reference_scores_low_but_nonzero():
    # Worth probing, not worth suggesting on the name alone.
    assert 0 < probes.name_score("widget_id", "id", "Customers") < 0.6


# --- confidence ---------------------------------------------------------------

def test_confidence_combines_the_three_signals():
    assert probes.confidence(name=1.0, coverage=1.0, uniqueness=1.0) == 1.0
    assert probes.confidence(name=0.0, coverage=0.0, uniqueness=0.0) == 0.0


def test_a_name_match_alone_cannot_clear_the_threshold():
    # Evidence from the data has to carry a suggestion — otherwise every
    # `*_id` column in a workbook would be proposed as a relationship.
    assert probes.confidence(name=1.0, coverage=0.0, uniqueness=0.0) \
        < probes.SUGGESTION_THRESHOLD


def test_strong_data_evidence_clears_the_threshold():
    assert probes.confidence(name=1.0, coverage=0.8, uniqueness=1.0) \
        >= probes.SUGGESTION_THRESHOLD


def _signals(coverage, uniqueness):
    return probes.PairSignals(coverage=coverage, uniqueness=uniqueness,
                              child_distinct=10, parent_distinct=10,
                              matched_distinct=10)


def test_a_non_unique_target_is_never_suggested():
    # A repeating "parent" key is a many-to-many, not a reference — and no
    # score from the other signals may outvote that.
    signals = _signals(coverage=1.0, uniqueness=0.1)
    score = probes.score_pair(signals, name=1.0)
    assert score >= probes.SUGGESTION_THRESHOLD      # the weighted sum passes...
    assert probes.qualifies(score, signals) is False  # ...but the floor rejects it


def test_low_coverage_is_never_suggested():
    signals = _signals(coverage=0.05, uniqueness=1.0)
    assert probes.qualifies(probes.score_pair(signals, name=1.0), signals) is False


def test_a_well_evidenced_pair_qualifies():
    signals = _signals(coverage=0.9, uniqueness=1.0)
    assert probes.qualifies(probes.score_pair(signals, name=1.0), signals) is True


def test_ratio_is_safe_on_empty_input():
    assert probes.ratio(0, 0) == 0.0
    assert probes.ratio(3, 4) == 0.75


# --- probes against real data -------------------------------------------------

def test_coverage_excludes_null_keys(conn):
    child_distinct, matched = conn.execute(
        probes.coverage_sql("orders", "customer_id", "customers", "customer_id")
    ).fetchone()
    # Distinct non-NULL child keys are {1, 2, 999}; two of them exist upstream.
    assert child_distinct == 3
    assert matched == 2
    assert probes.ratio(matched, child_distinct) == 0.6667


def test_uniqueness_identifies_the_parent_side(conn):
    parent = conn.execute(probes.uniqueness_sql("customers", "customer_id")).fetchone()
    assert probes.ratio(parent[0], parent[1]) == 1.0        # a real key
    child = conn.execute(probes.uniqueness_sql("orders", "customer_id")).fetchone()
    assert probes.ratio(child[0], child[1]) < 1.0           # repeats, so not a key


def test_orientation_follows_uniqueness(conn):
    """The direction with the unique target should win."""
    def score(child_rel, child_col, parent_rel, parent_col):
        cd, matched = conn.execute(
            probes.coverage_sql(child_rel, child_col, parent_rel, parent_col)).fetchone()
        pd_, pn = conn.execute(
            probes.uniqueness_sql(parent_rel, parent_col)).fetchone()
        return probes.confidence(name=1.0, coverage=probes.ratio(matched, cd),
                                 uniqueness=probes.ratio(pd_, pn))

    orders_to_customers = score("orders", "customer_id", "customers", "customer_id")
    customers_to_orders = score("customers", "customer_id", "orders", "customer_id")
    assert orders_to_customers > customers_to_orders
