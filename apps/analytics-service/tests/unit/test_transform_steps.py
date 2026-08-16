"""Unit tests — transformation steps: model round-trip, per-step SQL, schema fold.

No Postgres: the pipeline compiler is pure, and the executed cases run against
an in-memory DuckDB table standing in for a sheet's parquet.
"""

from __future__ import annotations

import duckdb
import pytest
from pydantic import TypeAdapter, ValidationError

from app.api.errors import ProblemException
from app.features.transform import steps as S
from app.features.transform.compile import compile_pipeline, compile_step, initial_columns
from app.features.transform.expr import ColExpr, LitExpr
from app.shared.query.schemas import Filter, FilterGroup, Sort

STEPS = TypeAdapter(list[S.TransformStep])

SCHEMA = [
    {"name": "id", "normalized_name": "id", "dtype": "INTEGER", "position": 0},
    {"name": "Full Name", "normalized_name": "full_name", "dtype": "VARCHAR", "position": 1},
    {"name": "city", "normalized_name": "city", "dtype": "VARCHAR", "position": 2},
    {"name": "amount", "normalized_name": "amount", "dtype": "DOUBLE", "position": 3},
    {"name": "when_txt", "normalized_name": "when_txt", "dtype": "VARCHAR", "position": 4},
]


@pytest.fixture
def conn():
    c = duckdb.connect()
    c.execute("""
        CREATE TABLE df AS SELECT * FROM (VALUES
            (1, '  Alice  ', 'NY', 10.0, '2024-01-05'),
            (2, 'bob',       'ny', 20.0, '2024-02-06'),
            (2, 'bob',       'ny', 20.0, '2024-02-06'),
            (3, 'Carol',     'LA', 30.0, '2024-03-07'),
            (4, NULL,        'sf', NULL, '2024-04-08')
        ) t(id, "Full Name", city, amount, when_txt)
    """)
    yield c
    c.close()


def run(conn, steps, **kwargs):
    sql, binds, cols = compile_pipeline(steps, SCHEMA, **kwargs)
    return conn.execute(sql, binds).fetchall(), cols


# --- the discriminated union --------------------------------------------------

def test_steps_round_trip_through_the_discriminated_union():
    raw = [
        {"type": "trim", "columns": ["full_name"], "mode": "both"},
        {"type": "limit", "count": 5},
    ]
    parsed = STEPS.validate_python(raw)
    assert isinstance(parsed[0], S.TrimStep)
    assert isinstance(parsed[1], S.LimitStep)
    # Dumping returns the same discriminator, so stored JSON reloads unchanged.
    assert [s.model_dump(mode="json")["type"] for s in parsed] == ["trim", "limit"]


def test_unknown_step_type_is_rejected():
    with pytest.raises(ValidationError):
        STEPS.validate_python([{"type": "teleport", "columns": ["id"]}])


def test_replace_requires_find_or_nulls_to():
    with pytest.raises(ValidationError):
        S.ReplaceStep(column="city")


# --- per-step SQL, binds, and folded columns ----------------------------------

def test_every_user_value_becomes_a_bind():
    binds: list = []
    sql, _ = compile_step(
        S.ReplaceStep(column="city", mode="substring", find="ny",
                      replace_with="NY", nulls_to="unknown"),
        initial_columns(SCHEMA), binds, "src")
    assert binds == ["ny", "NY", "unknown"]
    # None of the user's strings appear as SQL text.
    assert "'ny'" not in sql and "unknown" not in sql
    assert sql.count("?") == 3


def test_binds_are_appended_in_the_order_the_placeholders_appear():
    steps = [
        S.ReplaceStep(column="city", mode="substring", find="a", replace_with="b"),
        S.LimitStep(count=3, offset=1),
    ]
    _, binds, _ = compile_pipeline(steps, SCHEMA)
    assert binds == ["a", "b", 3, 1]


def test_select_folds_the_column_set():
    _, _, cols = compile_pipeline([S.SelectStep(columns=["id", "amount"])], SCHEMA)
    assert [c["name"] for c in cols] == ["id", "amount"]
    assert [c["position"] for c in cols] == [0, 1]


def test_rename_updates_name_and_normalized_name():
    _, _, cols = compile_pipeline(
        [S.RenameStep(renames={"full_name": "Customer Name"})], SCHEMA)
    renamed = cols[1]
    assert renamed["name"] == "Customer Name"
    assert renamed["normalized_name"] == "customer_name"


def test_rename_onto_an_existing_name_is_rejected():
    with pytest.raises(ProblemException) as e:
        compile_pipeline([S.RenameStep(renames={"full_name": "city"})], SCHEMA)
    assert e.value.code == "duplicate-column"


def test_drop_every_column_is_rejected():
    with pytest.raises(ProblemException) as e:
        compile_pipeline(
            [S.DropStep(columns=["id", "full_name", "city", "amount", "when_txt"])],
            SCHEMA)
    assert e.value.code == "empty-projection"


def test_cast_uses_the_type_whitelist_and_updates_the_dtype():
    sql, _, cols = compile_pipeline([S.CastStep(column="amount", to="integer")], SCHEMA)
    assert "CAST(\"amount\" AS INTEGER)" in sql
    assert cols[3]["dtype"] == "INTEGER"


def test_cast_to_an_unlisted_type_is_rejected_at_validation():
    with pytest.raises(ValidationError):
        S.CastStep(column="amount", to="; DROP TABLE users")


def test_reorder_moves_named_columns_to_the_front():
    _, _, cols = compile_pipeline([S.ReorderStep(columns=["amount", "city"])], SCHEMA)
    assert [c["name"] for c in cols][:2] == ["amount", "city"]
    assert [c["name"] for c in cols][2:] == ["id", "Full Name", "when_txt"]


def test_deduplicate_keep_first_and_last_use_opposite_orderings():
    first, _, _ = compile_pipeline(
        [S.DeduplicateStep(subset=["id"], keep="first",
                           order_by=[Sort(column="amount", direction="asc")])], SCHEMA)
    last, _, _ = compile_pipeline(
        [S.DeduplicateStep(subset=["id"], keep="last",
                           order_by=[Sort(column="amount", direction="asc")])], SCHEMA)
    assert "row_number()" in first and "ORDER BY \"amount\" ASC" in first
    assert "ORDER BY \"amount\" DESC" in last


def test_deduplicate_keep_none_counts_instead_of_ranking():
    sql, _, _ = compile_pipeline([S.DeduplicateStep(subset=["id"], keep="none")], SCHEMA)
    assert "COUNT(*) OVER (PARTITION BY \"id\") = 1" in sql


def test_pipeline_length_is_bounded():
    too_many = [S.LimitStep(count=1)] * (S.MAX_PIPELINE_STEPS + 1)
    with pytest.raises(ProblemException) as e:
        compile_pipeline(too_many, SCHEMA)
    assert e.value.code == "too-many-steps"


# --- the schema fold ----------------------------------------------------------

def test_filtering_on_a_dropped_column_is_an_unknown_column_error():
    steps = [
        S.DropStep(columns=["city"]),
        S.FilterStep(where=FilterGroup(
            conditions=[Filter(column="city", op="eq", value="NY")])),
    ]
    with pytest.raises(ProblemException) as e:
        compile_pipeline(steps, SCHEMA)
    assert e.value.code == "unknown-column"
    assert e.value.extra["column"] == "city"
    # The error tells the caller what IS available at that point in the chain.
    assert "city" not in e.value.extra["available"]
    assert "amount" in e.value.extra["available"]


def test_a_renamed_column_is_addressable_by_its_new_name_downstream():
    steps = [
        S.RenameStep(renames={"amount": "total"}),
        S.FilterStep(where=FilterGroup(
            conditions=[Filter(column="total", op="gt", value=5)])),
    ]
    sql, binds, cols = compile_pipeline(steps, SCHEMA)
    assert binds == [5]
    assert "total" in [c["name"] for c in cols]


def test_a_computed_column_is_addressable_downstream():
    steps = [
        S.ComputeStep(into="doubled", expression=LitExpr(value=1)),
        S.SelectStep(columns=["doubled"]),
    ]
    _, _, cols = compile_pipeline(steps, SCHEMA)
    assert [c["name"] for c in cols] == ["doubled"]


def test_referencing_a_column_before_it_exists_fails():
    with pytest.raises(ProblemException) as e:
        compile_pipeline([S.SelectStep(columns=["doubled"])], SCHEMA)
    assert e.value.code == "unknown-column"


def test_trim_on_a_numeric_column_is_a_type_mismatch():
    with pytest.raises(ProblemException) as e:
        compile_pipeline([S.TrimStep(columns=["amount"])], SCHEMA)
    assert e.value.code == "operator-type-mismatch"


def test_naming_the_same_column_twice_is_rejected():
    with pytest.raises(ProblemException) as e:
        compile_pipeline([S.SelectStep(columns=["id", "id"])], SCHEMA)
    assert e.value.code == "duplicate-column"


# --- executed pipelines -------------------------------------------------------

def test_trim_and_case_normalize_execute(conn):
    rows, _ = run(conn, [S.TrimStep(columns=["full_name"]),
                         S.CaseNormalizeStep(columns=["city"], mode="upper")])
    assert rows[0][1] == "Alice"
    assert {r[2] for r in rows} == {"NY", "LA", "SF"}


def test_deduplicate_removes_the_repeated_row(conn):
    rows, _ = run(conn, [S.DeduplicateStep(subset=["id"], keep="first")])
    assert sorted(r[0] for r in rows) == [1, 2, 3, 4]


def test_deduplicate_keep_none_drops_both_copies(conn):
    rows, _ = run(conn, [S.DeduplicateStep(subset=["id"], keep="none")])
    assert sorted(r[0] for r in rows) == [1, 3, 4]


def test_replace_and_null_fill_execute(conn):
    rows, _ = run(conn, [S.ReplaceStep(column="full_name", mode="substring",
                                       find="bob", replace_with="BOB",
                                       nulls_to="unknown")])
    names = [r[1] for r in rows]
    assert "BOB" in names
    assert "unknown" in names  # the NULL row was filled
    assert None not in names


def test_a_multi_step_pipeline_executes_in_order(conn):
    rows, cols = run(conn, [
        S.TrimStep(columns=["full_name"]),
        S.CaseNormalizeStep(columns=["city"], mode="upper"),
        S.DeduplicateStep(subset=["id"], keep="first"),
        S.FilterStep(where=FilterGroup(
            conditions=[Filter(column="amount", op="is_not_null")])),
        S.SortStep(by=[Sort(column="amount", direction="desc")]),
    ])
    assert [r[0] for r in rows] == [3, 2, 1]
    assert [c["name"] for c in cols] == [c["name"] for c in initial_columns(SCHEMA)]


def test_parse_dates_produces_timestamps(conn):
    rows, cols = run(conn, [S.ParseDatesStep(columns=["when_txt"], format="%Y-%m-%d")])
    assert cols[4]["dtype"] == "TIMESTAMP"
    assert rows[0][4].year == 2024


def test_split_adds_a_column_without_touching_the_source(conn):
    rows, cols = run(conn, [S.SplitStep(column="when_txt", delimiter="-",
                                        index=2, into="month")])
    assert [c["name"] for c in cols][-1] == "month"
    assert [r[-1] for r in rows] == ["01", "02", "02", "03", "04"]


def test_merge_can_drop_its_sources(conn):
    rows, cols = run(conn, [S.MergeStep(columns=["full_name", "city"], into="who",
                                        separator="@", drop_sources=True)])
    names = [c["name"] for c in cols]
    assert "who" in names and "Full Name" not in names and "city" not in names
    assert rows[1][-1] == "bob@ny"


def test_limit_and_offset_execute(conn):
    rows, _ = run(conn, [S.SortStep(by=[Sort(column="id")]),
                         S.LimitStep(count=2, offset=1)])
    assert [r[0] for r in rows] == [2, 2]


def test_set_relative_filter_operators_target_the_step_input(conn):
    # `is_duplicate` compiles to a subquery over the whole set; inside a CTE
    # chain that set is the preceding step, not a global view.
    rows, _ = run(conn, [S.FilterStep(where=FilterGroup(
        conditions=[Filter(column="id", op="is_duplicate")]))])
    assert [r[0] for r in rows] == [2, 2]


def test_preview_sampling_bounds_the_scan(conn):
    rows, _ = run(conn, [], sample_rows=2)
    assert len(rows) == 2


def test_compute_step_executes(conn):
    rows, cols = run(conn, [S.ComputeStep(
        into="flag", expression=ColExpr(name="city"))])
    assert [c["name"] for c in cols][-1] == "flag"
    assert rows[0][-1] == "NY"
