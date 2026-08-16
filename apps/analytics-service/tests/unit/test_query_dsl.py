"""Unit tests — typed query DSL: schemas, validation, compile + execute, cursors."""

from __future__ import annotations

import base64
import json

import duckdb
import pytest
from pydantic import ValidationError

from app.api.errors import ProblemException
from app.shared.data_io import build_sheet_schema
from app.shared.query import (
    Filter,
    FilterGroup,
    QuerySpec,
    Sort,
    compile_query,
    decode_cursor,
    encode_cursor,
    execute_query,
    spec_hash,
    validate_spec,
)

VERSION = "ver-123"


# --- fixtures ----------------------------------------------------------------

@pytest.fixture()
def sheet(tmp_path):
    """(conn, read_parquet expr, schema_json) over a small tmp parquet.

    Physical column ``Order ID`` normalizes to ``order_id`` — exercises the
    normalized-name-first resolution path.
    """
    path = str(tmp_path / "sheet.parquet")
    conn = duckdb.connect()
    conn.execute("""
        CREATE TABLE src AS SELECT * FROM (VALUES
            (1, 'EU',   'alpha widget', 10.0, DATE '2024-01-05'),
            (2, 'US',   'beta gadget',  20.0, DATE '2024-02-10'),
            (3, 'EU',   NULL,           30.0, DATE '2024-03-15'),
            (4, 'APAC', 'Gamma Widget', NULL, DATE '2024-04-20'),
            (5, 'US',   'delta',        50.0, DATE '2024-05-25')
        ) AS v("Order ID", region, description, amount, created)
    """)
    conn.execute(f"COPY src TO '{path}' (FORMAT PARQUET)")
    src = f"read_parquet('{path}')"
    described = conn.execute(f"DESCRIBE SELECT * FROM {src}").fetchall()
    schema_json, _ = build_sheet_schema(described)
    yield conn, src, schema_json
    conn.close()


def run(sheet, spec):
    conn, src, schema = sheet
    return execute_query(conn, src, spec, schema, version_id=VERSION)


# --- typed models accept the existing dict shapes ----------------------------

def test_filter_accepts_existing_dict_shape():
    f = Filter.model_validate({"column": "x", "op": "gt", "value": 5})
    assert f.column == "x" and f.op == "gt" and f.value == 5 and f.case_sensitive


def test_filtergroup_accepts_nested_dicts():
    g = FilterGroup.model_validate({"logic": "or", "conditions": [
        {"column": "a", "op": "is_null"},
        {"logic": "and", "conditions": [
            {"column": "b", "op": "in", "value": [1, 2]},
            {"column": "c", "op": "between", "value": [0, 9]},
        ]},
    ]})
    assert g.logic == "or"
    assert isinstance(g.conditions[0], Filter)
    assert isinstance(g.conditions[1], FilterGroup)
    assert g.conditions[1].conditions[0].op == "in"


def test_queryspec_defaults_and_limits():
    spec = QuerySpec()
    assert spec.columns is None and spec.filters is None
    assert spec.sort == [] and spec.limit == 100
    with pytest.raises(ValidationError):
        QuerySpec(limit=0)
    with pytest.raises(ValidationError):
        QuerySpec(limit=1001)


def test_value_arity_validation():
    bad = [
        {"column": "a", "op": "is_null", "value": 1},        # no-value op given a value
        {"column": "a", "op": "in", "value": 5},             # not a list
        {"column": "a", "op": "in", "value": []},            # empty list
        {"column": "a", "op": "between", "value": [1]},      # not a pair
        {"column": "a", "op": "date_between", "value": [1, 2, 3]},
        {"column": "a", "op": "eq"},                         # scalar op missing value
    ]
    for payload in bad:
        with pytest.raises(ValidationError):
            Filter.model_validate(payload)
    # No-value ops are fine without a value; pairs and lists validate.
    Filter.model_validate({"column": "a", "op": "is_duplicate"})
    Filter.model_validate({"column": "a", "op": "not_between", "value": [1, 9]})
    Filter.model_validate({"column": "a", "op": "not_in", "value": ["x"]})


def test_unknown_operator_is_a_problem_naming_op_and_column():
    # An operator outside the vocabulary is reported like an unknown *column*:
    # 400 problem+json with a specific code, not a generic schema error.
    with pytest.raises(ProblemException) as e:
        Filter.model_validate({"column": "a", "op": "greater_than", "value": 5})
    assert e.value.status_code == 400 and e.value.code == "unknown-operator"
    assert e.value.extra["op"] == "greater_than"
    assert e.value.extra["column"] == "a"
    assert "gt" in e.value.extra["available"]
    assert "greater_than" in e.value.detail and "'a'" in e.value.detail
    # The vocabulary is case-sensitive and exact — no near-miss is guessed at.
    for op in ("GT", "gt ", "equals"):
        with pytest.raises(ProblemException):
            Filter(column="a", op=op, value=1)


# --- malformed conditions are rejected, never silently dropped ---------------
#
# Regression: ``conditions`` used to be a plain ``Filter | FilterGroup`` union.
# Every FilterGroup field has a default, so a condition that failed ``Filter``
# matched ``FilterGroup`` instead and became an empty group — its column/op/
# value discarded, its WHERE clause empty, the whole unfiltered table returned.

def test_bad_condition_never_degrades_into_an_empty_group():
    for bad in ({"column": "a", "op": "greater_than", "value": 5},  # unknown op
                {"column": "a", "op": "in", "value": []},           # bad arity
                {"op": "eq", "value": 1},                           # no column
                {"col": "a", "operator": "gt"}):                    # misspelt keys
        with pytest.raises((ProblemException, ValidationError)) as e:
            FilterGroup.model_validate({"logic": "and", "conditions": [bad]})
        # Whatever the flavour, it is a 4xx contract violation and never a
        # quietly-accepted no-op.
        if isinstance(e.value, ProblemException):
            assert 400 <= e.value.status_code < 500, bad


def test_unknown_operator_reported_at_any_nesting_depth():
    with pytest.raises(ProblemException) as e:
        FilterGroup.model_validate({"logic": "or", "conditions": [
            {"column": "ok", "op": "is_null"},
            {"logic": "and", "conditions": [
                {"logic": "or", "conditions": [
                    {"column": "deep", "op": "greater_than", "value": 1}]}]},
        ]})
    assert e.value.code == "unknown-operator"
    assert e.value.extra["column"] == "deep"


def test_condition_matching_neither_model_is_rejected():
    # Keys belonging to neither model: honouring this as a group would discard
    # the caller's evident intent.
    with pytest.raises(ProblemException) as e:
        FilterGroup.model_validate({"conditions": [{"col": "a", "operator": "gt"}]})
    assert e.value.status_code == 400 and e.value.code == "invalid-filter"
    assert e.value.extra["keys"] == ["col", "operator"]

    # Filter fields mixed with group fields — ambiguous, so neither is assumed.
    with pytest.raises(ProblemException) as e:
        FilterGroup.model_validate({"conditions": [
            {"column": "a", "op": "eq", "value": 1, "conditions": []}]})
    assert e.value.code == "invalid-filter"

    # Not an object at all.
    with pytest.raises(ProblemException) as e:
        FilterGroup.model_validate({"conditions": ["region = 'EU'"]})
    assert e.value.code == "invalid-filter"


def test_deliberate_empty_groups_remain_legal():
    # The line is discarded information: {} says nothing and loses nothing, so
    # it stays a legitimate no-op. Anything filter-shaped must parse as one.
    assert FilterGroup().conditions == []
    assert FilterGroup.model_validate({}).conditions == []
    assert FilterGroup.model_validate({"logic": "or"}).conditions == []
    assert FilterGroup.model_validate({"conditions": []}).conditions == []

    nested = FilterGroup.model_validate({"conditions": [{}, {"logic": "or"}]})
    assert [type(c) for c in nested.conditions] == [FilterGroup, FilterGroup]
    assert all(c.conditions == [] for c in nested.conditions)


def test_group_and_filter_shapes_still_round_trip_through_model_dump():
    # compile.py re-dumps the model to the dict shape compile_filter consumes,
    # and explorer saved views persist that dump — both must re-validate.
    original = FilterGroup.model_validate({"logic": "or", "conditions": [
        {"column": "a", "op": "is_null"},
        {"logic": "and", "conditions": []},
        {"logic": "and", "conditions": [{"column": "b", "op": "in", "value": [1]}]},
    ]})
    assert FilterGroup.model_validate(original.model_dump()) == original


# --- validate_spec -----------------------------------------------------------

def test_unknown_column_problem(sheet):
    _, _, schema = sheet
    with pytest.raises(ProblemException) as e:
        validate_spec(QuerySpec(columns=["nope"]), schema)
    assert e.value.status_code == 400 and e.value.code == "unknown-column"
    assert e.value.extra["column"] == "nope"
    assert "region" in e.value.extra["available"]
    assert "order_id" in e.value.extra["available"]


def test_unknown_column_in_filters_and_sort(sheet):
    _, _, schema = sheet
    filters = FilterGroup(conditions=[
        FilterGroup(logic="or", conditions=[Filter(column="ghost", op="is_null")])])
    with pytest.raises(ProblemException) as e:
        validate_spec(QuerySpec(filters=filters), schema)
    assert e.value.code == "unknown-column"
    with pytest.raises(ProblemException) as e:
        validate_spec(QuerySpec(sort=[Sort(column="ghost")]), schema)
    assert e.value.code == "unknown-column"


def test_operator_type_mismatch(sheet):
    _, _, schema = sheet
    cases = [
        Filter(column="amount", op="contains", value="1"),       # string op on DOUBLE
        Filter(column="order_id", op="len_gt", value=2),         # len op on INTEGER
        Filter(column="amount", op="regex", value="^1"),
        Filter(column="region", op="date_before", value="2024-01-01"),  # date op on VARCHAR
        Filter(column="order_id", op="last_n_days", value=7),
    ]
    for f in cases:
        with pytest.raises(ProblemException) as e:
            validate_spec(QuerySpec(filters=FilterGroup(conditions=[f])), schema)
        assert e.value.status_code == 400, f.op
        assert e.value.code == "operator-type-mismatch", f.op
        assert e.value.extra["op"] == f.op


def test_type_compatible_ops_pass(sheet):
    _, _, schema = sheet
    ok = QuerySpec(filters=FilterGroup(conditions=[
        Filter(column="amount", op="gt", value=5),           # comparisons allowed anywhere
        Filter(column="region", op="gt", value="A"),
        Filter(column="description", op="contains", value="w"),
        Filter(column="created", op="date_after", value="2024-01-01"),
    ]))
    mapping = validate_spec(ok, schema)
    assert mapping["amount"] == "amount"


def test_normalized_name_precedence(sheet):
    _, _, schema = sheet
    mapping = validate_spec(QuerySpec(columns=["order_id", "region"]), schema)
    assert mapping == {"order_id": "Order ID", "region": "region"}
    # Physical name still resolves when no normalized name matches.
    mapping = validate_spec(QuerySpec(columns=["Order ID"]), schema)
    assert mapping == {"Order ID": "Order ID"}


# --- compile + execute -------------------------------------------------------

def test_projection_aliases_requested_names(sheet):
    page = run(sheet, QuerySpec(columns=["order_id", "region"],
                                sort=[Sort(column="order_id")]))
    assert page.total == 5 and page.next_cursor is None
    assert page.items[0] == {"order_id": 1, "region": "EU"}
    assert set(page.items[0]) == {"order_id", "region"}


def test_star_projection_when_columns_omitted(sheet):
    page = run(sheet, QuerySpec(limit=1))
    assert set(page.items[0]) == {"Order ID", "region", "description", "amount", "created"}


def test_and_or_nesting(sheet):
    spec = QuerySpec(
        columns=["order_id"],
        filters=FilterGroup(logic="or", conditions=[
            Filter(column="region", op="eq", value="EU"),
            FilterGroup(conditions=[
                Filter(column="amount", op="gte", value=45),
                Filter(column="region", op="eq", value="US"),
            ]),
        ]),
        sort=[Sort(column="order_id")],
    )
    page = run(sheet, spec)
    assert [r["order_id"] for r in page.items] == [1, 3, 5] and page.total == 3


def test_multi_sort(sheet):
    page = run(sheet, QuerySpec(columns=["order_id"], sort=[
        Sort(column="region"), Sort(column="amount", direction="desc")]))
    assert [r["order_id"] for r in page.items] == [4, 3, 1, 5, 2]


def test_search_is_case_insensitive_over_text_columns(sheet):
    page = run(sheet, QuerySpec(columns=["order_id"], search="widget",
                                sort=[Sort(column="order_id")]))
    assert [r["order_id"] for r in page.items] == [1, 4] and page.total == 2
    # Matches any text column, not just description.
    page = run(sheet, QuerySpec(columns=["order_id"], search="apac"))
    assert page.total == 1


def test_search_with_no_text_columns_matches_nothing(sheet):
    conn, src, schema = sheet
    numeric_schema = [c for c in schema if not c["dtype"].upper().startswith("VARCHAR")]
    page = execute_query(conn, src, QuerySpec(columns=["order_id"], search="widget"),
                         numeric_schema, version_id=VERSION)
    assert page.items == [] and page.total == 0


def test_filter_ops_end_to_end(sheet):
    def ids(*conds):
        page = run(sheet, QuerySpec(columns=["order_id"],
                                    filters=FilterGroup(conditions=list(conds)),
                                    sort=[Sort(column="order_id")]))
        return [r["order_id"] for r in page.items]

    assert ids(Filter(column="amount", op="is_null")) == [4]
    assert ids(Filter(column="description", op="is_not_null")) == [1, 2, 4, 5]
    assert ids(Filter(column="region", op="in", value=["EU", "APAC"])) == [1, 3, 4]
    assert ids(Filter(column="amount", op="between", value=[15, 35])) == [2, 3]
    assert ids(Filter(column="description", op="contains", value="widget")) == [1]
    assert ids(Filter(column="description", op="contains", value="widget",
                      case_sensitive=False)) == [1, 4]
    assert ids(Filter(column="created", op="date_between",
                      value=["2024-02-01", "2024-04-01"])) == [2, 3]
    assert ids(Filter(column="created", op="date_before", value="2024-02-01")) == [1]
    assert ids(Filter(column="description", op="starts_with", value="beta")) == [2]


def test_top_n_uses_filter_src_view(sheet):
    page = run(sheet, QuerySpec(columns=["order_id"],
                                filters=FilterGroup(conditions=[
                                    Filter(column="amount", op="top_n", value=2)]),
                                sort=[Sort(column="order_id")]))
    assert [r["order_id"] for r in page.items] == [3, 5]


# --- cursor paging -----------------------------------------------------------

def test_cursor_round_trip_to_last_page(sheet):
    spec = QuerySpec(columns=["order_id"], sort=[Sort(column="order_id")], limit=2)
    p1 = run(sheet, spec)
    assert [r["order_id"] for r in p1.items] == [1, 2]
    assert p1.next_cursor is not None and p1.total == 5

    p2 = run(sheet, spec.model_copy(update={"cursor": p1.next_cursor}))
    assert [r["order_id"] for r in p2.items] == [3, 4] and p2.next_cursor is not None

    p3 = run(sheet, spec.model_copy(update={"cursor": p2.next_cursor}))
    assert [r["order_id"] for r in p3.items] == [5]
    assert p3.next_cursor is None  # limit+1 probe found no further rows


def test_cursor_limit_change_does_not_invalidate(sheet):
    # limit is excluded from the spec hash — clients may resize pages mid-scan.
    spec = QuerySpec(columns=["order_id"], sort=[Sort(column="order_id")], limit=2)
    p1 = run(sheet, spec)
    p2 = run(sheet, spec.model_copy(update={"cursor": p1.next_cursor, "limit": 3}))
    assert [r["order_id"] for r in p2.items] == [3, 4, 5] and p2.next_cursor is None


def test_cursor_tamper_and_garbage_rejected(sheet):
    for bad in ("not-base64!!!",
                base64.urlsafe_b64encode(b"not json").decode(),
                base64.urlsafe_b64encode(b'{"v":"x","h":"y","o":-1}').decode(),
                base64.urlsafe_b64encode(b'{"v":"x","h":"y","o":"2"}').decode()):
        with pytest.raises(ProblemException) as e:
            run(sheet, QuerySpec(cursor=bad))
        assert e.value.status_code == 400 and e.value.code == "invalid-cursor", bad


def test_cursor_spec_or_version_mismatch_rejected(sheet):
    spec = QuerySpec(columns=["order_id"], sort=[Sort(column="order_id")], limit=2)
    cursor = run(sheet, spec).next_cursor

    # Same cursor, different filters → spec hash mismatch.
    changed = spec.model_copy(update={
        "cursor": cursor,
        "filters": FilterGroup(conditions=[Filter(column="region", op="eq", value="EU")]),
    })
    with pytest.raises(ProblemException) as e:
        run(sheet, changed)
    assert e.value.code == "invalid-cursor"

    # Same spec, different version → version mismatch.
    conn, src, schema = sheet
    with pytest.raises(ProblemException) as e:
        execute_query(conn, src, spec.model_copy(update={"cursor": cursor}),
                      schema, version_id="other-version")
    assert e.value.code == "invalid-cursor"


def test_cursor_helpers_round_trip():
    h = spec_hash(QuerySpec(search="x"))
    cur = encode_cursor("v1", h, 40)
    assert decode_cursor(cur) == {"v": "v1", "h": h, "o": 40}
    # Hash ignores cursor/limit but not everything else.
    assert spec_hash(QuerySpec(search="x", limit=5, cursor=cur)) == h
    assert spec_hash(QuerySpec(search="y")) != h


# --- compile_query fragments -------------------------------------------------

def test_compile_query_fragments_and_binds(sheet):
    _, _, schema = sheet
    spec = QuerySpec(
        columns=["order_id"],
        filters=FilterGroup(conditions=[Filter(column="region", op="eq", value="EU")]),
        sort=[Sort(column="order_id", direction="desc")],
    )
    compiled, binds = compile_query(spec, schema)
    assert compiled.select_list == '"Order ID" AS "order_id"'
    assert compiled.where == '("region" = ?)' and binds == ["EU"]
    # The user's sort leads; every remaining column is appended ASC so the
    # ORDER BY is a total order and OFFSET paging can't skip/duplicate ties.
    assert compiled.order_by == (
        '"Order ID" DESC, "region" ASC, "description" ASC, '
        '"amount" ASC, "created" ASC')


def test_compile_query_empty_spec_orders_by_every_column_for_stable_paging(sheet):
    _, _, schema = sheet
    compiled, binds = compile_query(QuerySpec(), schema)
    # No user sort → the ORDER BY is still a deterministic total order over all
    # columns, so paging an unsorted query returns each row exactly once.
    assert compiled.select_list == "*" and compiled.where == ""
    assert compiled.order_by == (
        '"Order ID" ASC, "region" ASC, "description" ASC, '
        '"amount" ASC, "created" ASC')
    assert binds == []


# --- the headline regression, end to end -------------------------------------

def test_malformed_filter_cannot_return_the_unfiltered_table(sheet):
    """A typo'd operator used to widen the result set to every row."""
    good = QuerySpec(columns=["order_id"], filters=FilterGroup(conditions=[
        Filter(column="amount", op="gt", value=25)]))
    assert run(sheet, good).total == 2                     # the intended answer
    assert run(sheet, QuerySpec()).total == 5              # the whole table

    with pytest.raises(ProblemException) as e:
        run(sheet, QuerySpec.model_validate(
            {"columns": ["order_id"],
             "filters": {"logic": "and", "conditions": [
                 {"column": "amount", "op": "greater_than", "value": 25}]}}))
    assert e.value.status_code == 400 and e.value.code == "unknown-operator"


def test_unknown_column_inside_a_filter_fails_the_query(sheet):
    # Same 4xx as an unknown projection column — the check is no longer skipped
    # by a bogus condition having collapsed to an empty group before validation.
    with pytest.raises(ProblemException) as e:
        run(sheet, QuerySpec(filters=FilterGroup(logic="or", conditions=[
            FilterGroup(conditions=[Filter(column="ghost", op="eq", value=1)])])))
    assert e.value.status_code == 400 and e.value.code == "unknown-column"
    assert e.value.extra["column"] == "ghost"


def test_empty_filter_group_is_a_working_no_op(sheet):
    # Explicitly-empty groups are a legitimate "no filtering" and must survive
    # the stricter parsing — at the top level and nested.
    for filters in (FilterGroup(),
                    FilterGroup(logic="or", conditions=[]),
                    FilterGroup(conditions=[FilterGroup(), FilterGroup(logic="or")])):
        compiled, binds = compile_query(QuerySpec(filters=filters), sheet[2])
        assert compiled.where == "" and binds == []
        assert run(sheet, QuerySpec(columns=["order_id"], filters=filters)).total == 5


def test_empty_group_beside_a_real_condition_still_filters(sheet):
    spec = QuerySpec(columns=["order_id"], filters=FilterGroup(logic="and", conditions=[
        Filter(column="region", op="eq", value="EU"),
        FilterGroup(),  # no-op arm
    ]), sort=[Sort(column="order_id")])
    page = run(sheet, spec)
    assert [r["order_id"] for r in page.items] == [1, 3] and page.total == 2
