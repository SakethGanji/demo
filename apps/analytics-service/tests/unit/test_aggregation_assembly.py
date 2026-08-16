"""Unit tests — aggregation SQL-assembly helpers (no storage, no DB, no app).

The pure pieces `run_aggregation` composes: join select-list building,
WHERE/FILTER/HAVING compilation with their binds, full-statement assembly,
and the limit-cap/truncation decision. `_bucket_expr` / `_filter_columns`
and the pydantic validators are covered in test_aggregation_compile.py.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.api.errors import ProblemException
from app.features.data_accelerator.schemas import (
    AggregationSpec,
    GroupByBucket,
    HavingCondition,
    JoinSpec,
)
from app.features.data_accelerator.services.aggregation import (
    _assemble_sql,
    _build_join_select,
    _compile_group_entries,
    _compile_having,
    _compile_select_aggs,
    _compile_where,
    _effective_limit,
    _is_truncated,
)
from app.shared.query import Filter, FilterGroup


def _fg(*conditions: Filter) -> FilterGroup:
    return FilterGroup(logic="and", conditions=list(conditions))


# --- _build_join_select ------------------------------------------------------

def test_join_select_skips_duplicate_key_and_prefixes_collisions():
    join = JoinSpec(sheet="orders", left_on="id", right_on="cust_id")
    parts = _build_join_select(
        ["id", "name", "amount"], ["cust_id", "name", "region"], join)
    assert parts == [
        'df."id"', 'df."name"', 'df."amount"',
        'df_join."name" AS "orders_name"',   # collides with base → sheet-prefixed
        'df_join."region" AS "region"',      # unique → kept as-is
    ]
    # The join key never appears from the right side.
    assert not any(p.startswith('df_join."cust_id"') for p in parts)


def test_join_select_prefix_only_applies_to_colliding_columns():
    join = JoinSpec(sheet="s2", left_on="k", right_on="k2")
    parts = _build_join_select(["k"], ["k2", "extra"], join)
    assert parts == ['df."k"', 'df_join."extra" AS "extra"']


# --- _compile_where ----------------------------------------------------------

def test_where_combines_structured_filters_and_filter_expr():
    where_sql, binds, uses_src = _compile_where(
        _fg(Filter(column="region", op="eq", value="EU")),
        "amount > 5", {"region", "amount"})
    assert where_sql == '("region" = ?) AND (amount > 5)'
    assert binds == ["EU"]
    assert uses_src is True


def test_where_structured_filters_alone():
    where_sql, binds, uses_src = _compile_where(
        _fg(Filter(column="status", op="in", value=["a", "b"])), None, {"status"})
    assert where_sql == '("status" IN (?, ?))'
    assert binds == ["a", "b"]
    assert uses_src is True


def test_where_filter_expr_alone_binds_nothing_and_needs_no_filter_src():
    where_sql, binds, uses_src = _compile_where(None, "amount > 5", {"amount"})
    assert where_sql == "(amount > 5)"
    assert binds == []
    assert uses_src is False


def test_where_empty_when_neither_given():
    assert _compile_where(None, None, {"a"}) == ("", [], False)


def test_where_unknown_filter_columns_are_a_400():
    """Same `unknown-column` contract as sort_by and the shared filter compiler:
    a filter rail has to know which control to highlight and what to offer."""
    with pytest.raises(ProblemException) as e:
        _compile_where(_fg(Filter(column="bogus", op="eq", value=1)), None, {"a"})
    assert e.value.status_code == 400
    assert e.value.detail.startswith("Filter columns not found: ['bogus']")
    assert e.value.code == "unknown-column"
    assert e.value.extra["columns"] == ["bogus"]
    assert e.value.extra["available"] == ["a"]


# --- _compile_group_entries --------------------------------------------------

def test_group_entries_mix_plain_columns_and_buckets():
    entries = _compile_group_entries(
        ["region", GroupByBucket(column="total", bin_width=10, alias="band")], "_agg_src")
    assert entries == [
        ("region", '"region"'),
        ("band", 'FLOOR("total" / 10.0) * 10.0'),
    ]
    # Default bucket name is {column}_bucket.
    (name, _), = _compile_group_entries([GroupByBucket(column="total", bin_width=10)], "df")
    assert name == "total_bucket"


# --- _compile_select_aggs ----------------------------------------------------

def test_select_aggs_fragments_aliases_and_nunique():
    parts, aliases, binds, exprs, uses_src = _compile_select_aggs(
        [AggregationSpec(column="amount", function="sum"),
         AggregationSpec(column="user", function="nunique", alias="users")],
        {"amount", "user"})
    assert parts == ['SUM("amount") AS "amount_sum"', 'COUNT(DISTINCT "user") AS "users"']
    assert aliases == ["amount_sum", "users"]
    assert binds == [] and uses_src is False
    assert exprs == {"amount_sum": ('SUM("amount")', []),
                     "users": ('COUNT(DISTINCT "user")', [])}


def test_select_aggs_filter_clause_binds_keep_select_order():
    parts, aliases, binds, exprs, uses_src = _compile_select_aggs(
        [AggregationSpec(column="amount", function="sum",
                         filter=_fg(Filter(column="region", op="eq", value="EU"))),
         AggregationSpec(column="id", function="count",
                         filter=_fg(Filter(column="status", op="in", value=["a", "b"])))],
        {"amount", "id", "region", "status"})
    assert parts == [
        'SUM("amount") FILTER (WHERE ("region" = ?)) AS "amount_sum"',
        'COUNT("id") FILTER (WHERE ("status" IN (?, ?))) AS "id_count"',
    ]
    assert binds == ["EU", "a", "b"]  # spec order, per-spec left to right
    assert uses_src is True
    # alias map carries each expression's own binds for HAVING re-expansion.
    assert exprs["amount_sum"] == ('SUM("amount") FILTER (WHERE ("region" = ?))', ["EU"])
    assert exprs["id_count"][1] == ["a", "b"]


def test_select_aggs_unknown_filter_columns_are_a_400():
    """A conditional measure's own filter is the fourth place on /aggregate a
    dead column can be named, and it publishes the same slug as the other three."""
    with pytest.raises(ProblemException) as e:
        _compile_select_aggs(
            [AggregationSpec(column="amount", function="sum",
                             filter=_fg(Filter(column="nope", op="eq", value=1)))],
            {"amount"})
    assert e.value.status_code == 400
    assert e.value.detail.startswith("Aggregation filter columns not found: ['nope']")
    assert e.value.code == "unknown-column"
    assert e.value.extra["columns"] == ["nope"]
    assert e.value.extra["available"] == ["amount"]


# --- _compile_having ---------------------------------------------------------

def test_having_unknown_alias_keeps_exact_detail_text():
    exprs = {"amount_sum": ('SUM("amount")', []), "id_count": ('COUNT("id")', [])}
    with pytest.raises(HTTPException) as e:
        _compile_having([HavingCondition(column="nope", op="gt", value=1)],
                        exprs, ["amount_sum", "id_count"])
    assert e.value.status_code == 400
    assert e.value.detail == ("HAVING column is not an aggregation alias: nope. "
                              "Aliases: ['amount_sum', 'id_count']")


def test_having_re_expands_expressions_and_orders_binds():
    exprs = {
        "amount_sum": ('SUM("amount") FILTER (WHERE ("region" = ?))', ["EU"]),
        "id_count": ('COUNT("id")', []),
    }
    having_sql, binds = _compile_having(
        [HavingCondition(column="amount_sum", op="gte", value=100),
         HavingCondition(column="id_count", op="lt", value=5)],
        exprs, ["amount_sum", "id_count"])
    assert having_sql == ('SUM("amount") FILTER (WHERE ("region" = ?)) >= ? '
                          'AND COUNT("id") < ?')
    # Per condition: the expression's own binds, then the comparison value.
    assert binds == ["EU", 100, 5]


def test_having_empty_conditions_compile_to_nothing():
    assert _compile_having([], {}, []) == ("", [])


# --- _assemble_sql -----------------------------------------------------------

def test_assemble_wraps_where_in_cte_and_groups_by_ordinals():
    sql = _assemble_sql("df", "_agg_src", '"region" = ?',
                        [("region", '"region"'), ("band", 'FLOOR("total" / 10.0) * 10.0')],
                        ['SUM("amount") AS "amount_sum"'], ["amount_sum"],
                        "", None, "desc", 100)
    assert sql == ('WITH _agg_src AS (SELECT * FROM df WHERE "region" = ?) '
                   'SELECT "region" AS "region", FLOOR("total" / 10.0) * 10.0 AS "band", '
                   'SUM("amount") AS "amount_sum" FROM _agg_src '
                   "GROUP BY 1, 2 LIMIT 101")


def test_assemble_without_where_selects_source_directly():
    sql = _assemble_sql("df", "df", "", [("region", '"region"')],
                        ['COUNT("id") AS "id_count"'], ["id_count"],
                        "", None, "desc", 50)
    assert sql.startswith("SELECT ") and "_agg_src" not in sql
    assert sql.endswith("FROM df GROUP BY 1 LIMIT 51")


def test_assemble_sorts_only_by_known_names_and_honours_order():
    args = ("df", "df", "", [("region", '"region"')],
            ['SUM("amount") AS "amount_sum"'], ["amount_sum"], "")
    assert ' ORDER BY "amount_sum" DESC ' in _assemble_sql(*args, "amount_sum", "desc", 10)
    assert ' ORDER BY "region" ASC ' in _assemble_sql(*args, "region", "asc", 10)


def test_assemble_rejects_unknown_sort_by_naming_the_valid_options():
    """An unknown sort_by used to drop the ORDER BY and return arbitrary order."""
    args = ("df", "df", "", [("region", '"region"')],
            ['SUM("amount") AS "amount_sum"'], ["amount_sum"], "")
    with pytest.raises(ProblemException) as e:
        _assemble_sql(*args, "not_a_column", "asc", 10)
    assert e.value.status_code == 400
    assert e.value.code == "unknown-column"
    assert e.value.detail == ("sort_by column not found: not_a_column. "
                              "Valid options: ['amount_sum', 'region']")
    # Follows the service's unknown-column convention: what was asked for, and
    # what was available.
    assert e.value.extra == {"columns": ["not_a_column"],
                             "available": ["amount_sum", "region"]}


def test_assemble_rejects_unrecognised_sort_order_instead_of_defaulting_to_desc():
    """Defence in depth behind the schema Literal: never guess a direction."""
    args = ("df", "df", "", [("region", '"region"')],
            ['SUM("amount") AS "amount_sum"'], ["amount_sum"], "")
    for bad in ("ASC", "Asc", "ascending", "descending", "garbage", ""):
        with pytest.raises(ProblemException) as e:
            _assemble_sql(*args, "region", bad, 10)
        assert e.value.status_code == 400
        assert e.value.code == "invalid-sort-order"
        assert repr(bad) in e.value.detail and "['asc', 'desc']" in e.value.detail


def test_assemble_ignores_sort_order_when_there_is_no_sort_by():
    """Guards pivot: `_aggregate_into` always passes sort_by=None, so the new
    sort validation must not fire on the direction it hardcodes."""
    sql = _assemble_sql("df", "df", "", [("region", '"region"')],
                        ['SUM("amount") AS "amount_sum"'], ["amount_sum"],
                        "", None, "asc", 100)
    assert "ORDER BY" not in sql
    assert sql.endswith("GROUP BY 1 LIMIT 101")


def test_assemble_omits_limit_entirely_when_limit_is_none():
    """The grand-total pass must span every group, so it takes no LIMIT."""
    sql = _assemble_sql("df", "df", "", [], ['SUM("amount") AS "amount_sum"'],
                        ["amount_sum"], "", None, "asc", None)
    assert sql == 'SELECT SUM("amount") AS "amount_sum" FROM df'
    assert "LIMIT" not in sql


def test_assemble_omits_group_by_when_no_group_entries():
    # Grand-total-only aggregation: no bare "GROUP BY " parser error.
    sql = _assemble_sql("df", "df", "", [],
                        ['SUM("amount") AS "amount_sum"'], ["amount_sum"],
                        "", None, "desc", 100)
    assert sql == 'SELECT SUM("amount") AS "amount_sum" FROM df LIMIT 101'
    assert "GROUP BY" not in sql
    # sort_by still validates against agg aliases alone; HAVING still appends.
    sorted_sql = _assemble_sql("df", "df", "", [],
                               ['SUM("amount") AS "amount_sum"'], ["amount_sum"],
                               'SUM("amount") > ?', "amount_sum", "asc", 100)
    assert sorted_sql.endswith(
        'FROM df HAVING SUM("amount") > ? ORDER BY "amount_sum" ASC LIMIT 101')


def test_assemble_appends_having_after_group_by():
    sql = _assemble_sql("df", "df", "", [("region", '"region"')],
                        ['SUM("amount") AS "amount_sum"'], ["amount_sum"],
                        'SUM("amount") > ?', None, "desc", 10)
    assert sql.endswith('GROUP BY 1 HAVING SUM("amount") > ? LIMIT 11')


# --- limit cap & truncation --------------------------------------------------

def test_effective_limit_matrix():
    cap = 100
    assert _effective_limit(None, cap) == 100        # no user limit → cap
    assert _effective_limit(10, cap) == 10           # under cap → user's
    assert _effective_limit(500, cap) == 100         # over cap → cap
    assert _effective_limit(100, cap) == 100         # exactly cap


def test_truncated_only_when_server_cap_did_the_cutting():
    cap = 100
    # No user limit: the probe's extra row means the cap cut results.
    assert _is_truncated(101, None, cap) is True
    assert _is_truncated(100, None, cap) is False
    # User limit under the cap: any cutting is the caller's own doing.
    assert _is_truncated(11, 10, cap) is False
    # User limit over the cap: the cap governs, so it is server truncation.
    assert _is_truncated(101, 500, cap) is True
    assert _is_truncated(100, 500, cap) is False
    # User limit exactly the cap counts as the server cap's doing.
    assert _is_truncated(101, 100, cap) is True


# --- composed: bind order matches placeholder order --------------------------

def test_full_bind_order_matches_placeholder_order_in_assembled_sql():
    available = {"region", "flag", "amount", "id", "status"}
    where_sql, where_binds, _ = _compile_where(
        _fg(Filter(column="region", op="in", value=["EU", "US"])), "flag = 1", available)
    from_target = "_agg_src" if where_sql else "df"
    entries = _compile_group_entries(["region"], from_target)
    agg_parts, aliases, select_binds, exprs, _ = _compile_select_aggs(
        [AggregationSpec(column="amount", function="sum",
                         filter=_fg(Filter(column="status", op="eq", value="ok"))),
         AggregationSpec(column="id", function="count",
                         filter=_fg(Filter(column="status", op="neq", value="void")))],
        available)
    having_sql, having_binds = _compile_having(
        [HavingCondition(column="amount_sum", op="gt", value=10)], exprs, aliases)

    sql = _assemble_sql("df", from_target, where_sql, entries, agg_parts, aliases,
                        having_sql, "amount_sum", "desc", 100)
    binds = where_binds + select_binds + having_binds

    # binds: WHERE(EU, US) then FILTERs(ok, void) then HAVING(ok's re-expansion, 10)
    assert binds == ["EU", "US", "ok", "void", "ok", 10]
    assert sql.count("?") == len(binds)
    # WHERE placeholders all live in the CTE prefix, before the SELECT list.
    cte_end = sql.index(") SELECT")
    assert sql[:cte_end].count("?") == len(where_binds)
    # FILTER placeholders sit between the CTE and HAVING; HAVING's come last.
    having_at = sql.index(" HAVING ")
    assert sql[:having_at].count("?") == len(where_binds) + len(select_binds)
    assert sql[having_at:].count("?") == len(having_binds)
