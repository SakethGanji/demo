"""Unit tests — aggregation grand totals (no Postgres, no storage, no app).

`_grand_totals` replaced a `SELECT SUM(alias) FROM agg_result` that was wrong
twice over: it summed every alias regardless of the aggregation's function (a
`max` footer read 75000 when the true maximum was 25000), and it summed the
LIMITed page rather than every group, so even `sum`/`count` went silently
partial as soon as the result was truncated.

The rules under test: only additive functions get a total, everything else is
reported in the omission map with a reason, and the total always spans the
whole filtered source — never the returned page.
"""

from __future__ import annotations

import duckdb
import pytest

from app.features.data_accelerator.schemas import AggregateRequest
from app.features.data_accelerator.services.aggregation import (
    TOTAL_OMITTED_NON_ADDITIVE,
    TOTALABLE_FUNCTIONS,
    _compile_group_entries,
    _compile_having,
    _compile_select_aggs,
    _compile_where,
    _grand_totals,
    _split_totalable,
)

# 6 groups, one row each; g5's amount is NULL.
# sum = 75000, COUNT(amount) = 5, true max = 25000, mean = 15000.
# Summing the per-group maxima gives 75000 — the exact number the defect
# report saw in a `max` footer.
_ROWS = "('g0', 5000), ('g1', 10000), ('g2', 15000), ('g3', 20000), " \
        "('g4', 25000), ('g5', NULL)"


@pytest.fixture
def conn():
    c = duckdb.connect()
    c.execute(f"CREATE TABLE df AS SELECT * FROM (VALUES {_ROWS}) t(grp, amount)")
    yield c
    c.close()


def _totals(conn, request: AggregateRequest):
    """Compile a request the way `run_aggregation` does, then take its totals."""
    available = {r[0] for r in conn.execute("DESCRIBE df").fetchall()}
    where_sql, where_binds, needs_src = _compile_where(
        request.filters, request.filter_expr, available)
    from_target = "_agg_src" if where_sql else "df"
    group_entries = _compile_group_entries(request.group_by, from_target)
    _parts, aliases, _binds, agg_exprs, aggs_need_src = _compile_select_aggs(
        request.aggregations, available)
    having_sql, having_binds = _compile_having(request.having, agg_exprs, aliases)
    if needs_src or aggs_need_src:
        conn.execute("CREATE OR REPLACE VIEW _filter_src AS SELECT * FROM df")
    return _grand_totals(conn, request, available, "df", from_target, where_sql,
                         where_binds, group_entries, having_sql, having_binds)


def _req(**over) -> AggregateRequest:
    body = {"group_by": ["grp"],
            "aggregations": [{"column": "amount", "function": "sum",
                              "alias": "total"}]}
    body.update(over)
    return AggregateRequest(**body)


# --- which functions get a total ---------------------------------------------

def test_only_sum_and_count_are_totalable():
    assert TOTALABLE_FUNCTIONS == frozenset({"sum", "count"})


def test_split_totalable_separates_specs_and_names_the_reason():
    request = _req(aggregations=[
        {"column": "amount", "function": "sum", "alias": "total"},
        {"column": "amount", "function": "count", "alias": "n"},
        {"column": "amount", "function": "max", "alias": "peak"},
        {"column": "grp", "function": "nunique"},          # default alias
    ])
    totalable, omitted = _split_totalable(request.aggregations)
    assert [s.alias for s in totalable] == ["total", "n"]
    assert omitted == {"peak": TOTAL_OMITTED_NON_ADDITIVE,
                       "grp_nunique": TOTAL_OMITTED_NON_ADDITIVE}


@pytest.mark.parametrize(
    "function", ["max", "min", "mean", "median", "std", "nunique", "first", "last"])
def test_non_additive_functions_are_omitted_with_a_reason_not_summed(conn, function):
    """A `max` footer must not read 75000 when the maximum is 25000."""
    totals, omitted = _totals(conn, _req(aggregations=[
        {"column": "amount", "function": function, "alias": "v"}]))
    assert totals == {}
    assert omitted == {"v": TOTAL_OMITTED_NON_ADDITIVE}


def test_mixed_request_totals_the_additive_aliases_and_omits_the_rest(conn):
    totals, omitted = _totals(conn, _req(aggregations=[
        {"column": "amount", "function": "sum", "alias": "total"},
        {"column": "amount", "function": "count", "alias": "n"},
        {"column": "amount", "function": "max", "alias": "peak"},
        {"column": "amount", "function": "mean", "alias": "avg"},
    ]))
    assert totals == {"total": 75000, "n": 5}   # count = non-null rows
    assert omitted == {"peak": TOTAL_OMITTED_NON_ADDITIVE,
                       "avg": TOTAL_OMITTED_NON_ADDITIVE}
    # The omitted aliases are absent from totals, not present-and-zero.
    assert "peak" not in totals and "avg" not in totals


# --- the total spans every group, never the page -----------------------------

def test_totals_cover_all_groups_regardless_of_the_caller_limit(conn):
    """`limit` bounds the returned page; it must not bound the total."""
    for limit in (None, 1, 2, 6, 100):
        totals, _ = _totals(conn, _req(limit=limit, sort_by="total",
                                       sort_order="desc"))
        assert totals == {"total": 75000}
    # The top-2 page alone would have read 45000 under the old page-scoped SUM.
    page = conn.execute(
        'SELECT SUM(s) FROM (SELECT SUM(amount) AS s FROM df GROUP BY grp '
        "ORDER BY s DESC LIMIT 2)").fetchone()[0]
    assert page == 45000


def test_totals_respect_filters_but_not_the_page(conn):
    totals, omitted = _totals(conn, _req(
        filters={"logic": "and",
                 "conditions": [{"column": "amount", "op": "gte", "value": 15000}]},
        limit=1))
    assert totals == {"total": 60000}   # 15000 + 20000 + 25000
    assert omitted == {}


def test_totals_respect_the_deprecated_filter_expr(conn):
    totals, _ = _totals(conn, _req(filter_expr="amount < 20000", limit=1))
    assert totals == {"total": 30000}   # 5000 + 10000 + 15000


def test_conditional_aggregate_totals_keep_their_filter(conn):
    """A FILTER (WHERE ...) aggregate totals over the rows it actually feeds on."""
    totals, _ = _totals(conn, _req(aggregations=[
        {"column": "amount", "function": "sum", "alias": "total"},
        {"column": "amount", "function": "sum", "alias": "big",
         "filter": {"conditions": [
             {"column": "amount", "op": "gt", "value": 12000}]}},
    ]))
    assert totals == {"total": 75000, "big": 60000}


def test_where_and_filter_binds_stay_in_order_in_the_totals_query(conn):
    """WHERE binds first, then the per-aggregate FILTER binds — same rule as
    the main statement, exercised through a real execute."""
    totals, _ = _totals(conn, _req(
        filters={"conditions": [{"column": "grp", "op": "neq", "value": "g0"}]},
        aggregations=[
            {"column": "amount", "function": "sum", "alias": "total"},
            {"column": "amount", "function": "sum", "alias": "big",
             "filter": {"conditions": [
                 {"column": "amount", "op": "gt", "value": 12000}]}},
        ]))
    assert totals == {"total": 70000, "big": 60000}


# --- HAVING: total the groups the caller can actually see --------------------

def test_totals_cover_the_groups_that_passed_having(conn):
    """HAVING removes groups from the table entirely, so they must not be in
    the footer either — and the LIMIT must still not bound it."""
    totals, _ = _totals(conn, _req(
        having=[{"column": "total", "op": "gte", "value": 15000}], limit=1))
    assert totals == {"total": 60000}   # g2 + g3 + g4; g0/g1/g5 dropped


def test_having_totals_also_honour_where_and_count(conn):
    totals, omitted = _totals(conn, _req(
        filters={"conditions": [{"column": "amount", "op": "lte", "value": 20000}]},
        aggregations=[
            {"column": "amount", "function": "sum", "alias": "total"},
            {"column": "amount", "function": "count", "alias": "n"},
            {"column": "amount", "function": "max", "alias": "peak"},
        ],
        having=[{"column": "total", "op": "gt", "value": 5000}]))
    # WHERE keeps g0..g3 (g5's NULL amount fails `amount <= 20000`);
    # HAVING then drops g0.
    assert totals == {"total": 45000, "n": 3}
    assert omitted == {"peak": TOTAL_OMITTED_NON_ADDITIVE}


# --- degenerate shapes -------------------------------------------------------

def test_no_group_by_totals_match_the_single_returned_row(conn):
    totals, _ = _totals(conn, _req(group_by=[]))
    assert totals == {"total": 75000}
    assert conn.execute("SELECT SUM(amount) FROM df").fetchone()[0] == 75000


def test_all_non_additive_returns_no_totals_at_all(conn):
    totals, omitted = _totals(conn, _req(aggregations=[
        {"column": "amount", "function": "max", "alias": "peak"},
        {"column": "amount", "function": "mean", "alias": "avg"}]))
    assert totals == {}
    assert omitted == {"peak": TOTAL_OMITTED_NON_ADDITIVE,
                       "avg": TOTAL_OMITTED_NON_ADDITIVE}


def test_a_filtered_out_dataset_totals_to_null_not_to_a_missing_key(conn):
    """Nothing matched: the alias is still present with a null total, which is
    what `totals_omitted` exists to be distinguishable from."""
    totals, omitted = _totals(conn, _req(filter_expr="amount > 999999"))
    assert totals == {"total": None}
    assert omitted == {}
