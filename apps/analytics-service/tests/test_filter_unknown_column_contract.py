"""A filter on a column that does not exist is a 400, not a 500.

``tests/test_sampling_column_contract.py`` closed this for every column a
sample request names *except* the two that are compiled rather than read:
a step's ``filters`` and its raw ``filter_expr``. Those went straight to
DuckDB, whose ``BinderException`` nothing catches — so the caller got
"An unexpected error occurred." with no column name, no valid options, and
(with debug on) the generated SQL echoed back in the detail.

The check has to live in ``app.shared.filters``, not in the sampling service:
``filters`` is a recursive structure, and a caller-side scan of the top-level
list silently misses a condition nested inside a FilterGroup.
"""

from __future__ import annotations

import duckdb
import pytest

from app.api.errors import ProblemException
from app.shared.filters import apply_filters
from conftest import auth, upload_inline

ROWS = ('[{"a": 1, "grp": "x"}, {"a": 2, "grp": "y"}, {"a": 3, "grp": "x"}]')


@pytest.fixture
def conn():
    c = duckdb.connect()
    c.execute("CREATE TABLE t AS SELECT * FROM (VALUES (1, 'x'), (2, 'y')) v(a, grp)")
    return c


def test_a_filter_on_a_missing_column_raises_the_unknown_column_problem(conn):
    """The 400 has to name the column and list the valid ones — that is the
    difference between a UI highlighting a field and a user guessing."""
    with pytest.raises(ProblemException) as exc:
        apply_filters(conn, "t", [{"column": "ghost", "op": "eq", "value": 1}])

    assert exc.value.status_code == 400
    assert exc.value.code == "unknown-column"
    assert exc.value.extra["columns"] == ["ghost"]
    assert exc.value.extra["available"] == ["a", "grp"]


def test_a_missing_column_nested_in_a_filter_group_is_caught_too(conn):
    """Filters are a tree. A guard that only walks the top level lets exactly
    the interesting case — one bad leaf inside an OR — through to the binder."""
    with pytest.raises(ProblemException) as exc:
        apply_filters(conn, "t", [{"logic": "or", "conditions": [
            {"column": "a", "op": "eq", "value": 1},
            {"logic": "and", "conditions": [
                {"column": "ghost", "op": "gt", "value": 0}]},
        ]}])

    assert exc.value.code == "unknown-column"
    assert exc.value.extra["columns"] == ["ghost"]


def test_a_raw_filter_expr_on_a_missing_column_is_also_a_request_error(conn):
    """``filter_expr`` is raw SQL and cannot be checked before execution, so
    the binder error it produces must be translated rather than escape as a
    500 — the WHERE clause is entirely the caller's."""
    with pytest.raises(ProblemException) as exc:
        apply_filters(conn, "t", None, "ghost > 1")

    assert exc.value.status_code == 400
    assert exc.value.code == "unknown-column"
    assert exc.value.extra["columns"] == ["ghost"]
    # The generated SQL is never echoed back.
    assert "SELECT" not in exc.value.detail


def test_a_valid_filter_still_filters_and_leaves_no_scratch_view(conn):
    """The guard must not change the accepted path, and the temporary
    ``_filter_src`` view must be dropped even when a filter is rejected."""
    table, matched, _ = apply_filters(conn, "t", [{"column": "grp", "op": "eq",
                                                   "value": "x"}])
    assert (table, matched) == ("_filtered_view", 1)

    with pytest.raises(ProblemException):
        apply_filters(conn, "t", None, "ghost > 1")
    left = conn.execute(
        "SELECT COUNT(*) FROM duckdb_views() WHERE view_name = '_filter_src'"
    ).fetchone()[0]
    assert left == 0


async def test_post_sample_answers_400_for_a_step_filter_on_an_unknown_column(
        client, admin_id):
    """The end-to-end contract: the sampling endpoint's per-step ``filters``
    used to 500 on a typo'd column name."""
    ds = (await upload_inline(client, admin_id, ROWS))["dataset_id"]
    r = await client.post("/api/v1/sample", headers=auth(admin_id), json={
        "dataset_id": ds, "target_total_volume": 2,
        "sampling_steps": [{"method": "random", "sample_size": 2,
                            "filters": [{"column": "ghost", "op": "eq",
                                         "value": "x"}]}]})

    assert r.status_code == 400, r.text
    body = r.json()
    assert body["code"] == "unknown-column"
    assert body["columns"] == ["ghost"]
    assert body["available"] == ["a", "grp"]


async def test_post_sample_answers_400_for_a_step_filter_expr_on_an_unknown_column(
        client, admin_id):
    """Same for the raw-expression form of the same mistake."""
    ds = (await upload_inline(client, admin_id, ROWS))["dataset_id"]
    r = await client.post("/api/v1/sample", headers=auth(admin_id), json={
        "dataset_id": ds, "target_total_volume": 2,
        "sampling_steps": [{"method": "random", "sample_size": 2,
                            "filter_expr": "ghost > 1"}]})

    assert r.status_code == 400, r.text
    assert r.json()["code"] == "unknown-column"


async def test_post_sample_still_applies_a_valid_step_filter(client, admin_id):
    """The rejection must not be the only reason a filter "works"."""
    ds = (await upload_inline(client, admin_id, ROWS))["dataset_id"]
    r = await client.post("/api/v1/sample", headers=auth(admin_id), json={
        "dataset_id": ds, "target_total_volume": 2,
        "sampling_steps": [{"method": "random", "sample_size": 2,
                            "filters": [{"column": "grp", "op": "eq",
                                         "value": "x"}]}]})

    assert r.status_code == 200, r.text
    assert r.json()["steps_summary"][0]["filter_matched"] == 2
