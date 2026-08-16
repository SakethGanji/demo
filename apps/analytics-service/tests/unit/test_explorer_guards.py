"""Explorer guards that are pure enough to pin without Postgres.

Three separate holes live here because they share a property: each one is a
guard that either could not fire, or fired as the wrong kind of failure.
"""

from __future__ import annotations

import duckdb
import pandas as pd
import pytest
from fastapi import HTTPException

from app.api.errors import ProblemException
from app.features.explorer import service
from app.features.explorer.data_quality import (
    duplicate_groups_sql,
    duplicate_totals_sql,
    group_examples_batch_sql,
    group_examples_sql,
)
from app.shared.query import QuerySpec

SCHEMA = [
    {"name": "id", "normalized_name": "id", "dtype": "BIGINT", "position": 0},
    {"name": "Email", "normalized_name": "email", "dtype": "VARCHAR", "position": 1},
    {"name": "Tier", "normalized_name": "tier", "dtype": "VARCHAR", "position": 2},
]
MASKED = {"Email": "email"}


# --- the SQL builders refuse an empty column list -----------------------------

@pytest.mark.parametrize("builder", [
    lambda cols: duplicate_groups_sql(cols, 10),
    lambda cols: duplicate_totals_sql(cols),
    lambda cols: group_examples_sql(cols, 5),
    lambda cols: group_examples_batch_sql(cols, 1, 5),
])
def test_the_duplicate_sql_builders_reject_an_empty_column_list(builder):
    """An empty list does not mean "no grouping" — it means invalid SQL.

    ``", ".join([])`` is ``""``, which produced ``GROUP BY  HAVING COUNT(*) > 1``
    and a DuckDB ParserException that no caller on this path catches, i.e. a
    500. Raising in the builder keeps that unreachable even if a future caller
    forgets to validate its input.
    """
    with pytest.raises(ValueError):
        builder([])


def test_the_batched_group_examples_query_caps_rows_per_group():
    """One query has to do what N per-group queries used to.

    ``df`` is a lazy view over the parquet, so the old one-query-per-group loop
    re-read the object up to 100 times on a single ``/duplicates`` request,
    on the event loop and with no timeout.
    """
    df = pd.DataFrame({
        "Region": ["EU"] * 7 + ["US"] * 2 + [None] * 3,
        "Tier": ["gold"] * 7 + ["gold"] * 2 + [None] * 3,
    })
    conn = duckdb.connect(":memory:")
    conn.register("df", df)

    sql = group_examples_batch_sql(["Region", "Tier"], 3, 5)
    rows = conn.execute(sql, ["EU", "gold", "US", "gold", None, None]).fetchall()

    per_group: dict[tuple, int] = {}
    for r in rows:
        per_group[(r[0], r[1])] = per_group.get((r[0], r[1]), 0) + 1
    # Every requested group is represented, none beyond the per-group cap, and
    # the NULL key matched null-safely rather than vanishing.
    assert per_group == {("EU", "gold"): 5, ("US", "gold"): 2, (None, None): 3}


# --- a masked column may be projected, never computed over --------------------

def test_a_spec_that_filters_on_a_masked_column_is_refused():
    """``total`` is a COUNT over the raw WHERE clause; masking never touches it.

    So filtering ``Email eq '<guess>'`` and reading the count recovers a masked
    value one answer at a time — the same bypass ``ensure_raw_access`` closes on
    ``/download``.
    """
    spec = QuerySpec(filters={"logic": "and", "conditions": [
        {"column": "email", "op": "eq", "value": "ana@example.com"}]})
    with pytest.raises(ProblemException) as exc:
        service.guard_masked_query(spec, SCHEMA, MASKED)
    assert exc.value.status_code == 400
    assert exc.value.code == "sensitive-column-not-filterable"
    assert exc.value.extra["columns"] == ["Email"]


def test_a_spec_that_sorts_or_searches_over_a_masked_column_is_refused():
    """Sorting leaks the ordering of the hidden values; search is icontains
    across every text column, the masked one included."""
    with pytest.raises(ProblemException):
        service.guard_masked_query(
            QuerySpec(sort=[{"column": "Email", "direction": "asc"}]),
            SCHEMA, MASKED)
    with pytest.raises(ProblemException):
        service.guard_masked_query(QuerySpec(search="ana@"), SCHEMA, MASKED)


def test_a_nested_filter_group_cannot_hide_a_masked_column():
    """The check has to walk the whole condition tree, not just its top level."""
    spec = QuerySpec(filters={"logic": "or", "conditions": [
        {"column": "tier", "op": "eq", "value": "gold"},
        {"logic": "and", "conditions": [
            {"column": "email", "op": "starts_with", "value": "a"}]},
    ]})
    with pytest.raises(ProblemException) as exc:
        service.guard_masked_query(spec, SCHEMA, MASKED)
    assert exc.value.code == "sensitive-column-not-filterable"


def test_projecting_a_masked_column_and_filtering_others_stays_allowed():
    """The guard must not make masked datasets unusable.

    Projection is fine — those values come back masked. Only predicates and
    ordering, which leak through which rows *match*, are refused.
    """
    service.guard_masked_query(
        QuerySpec(columns=["email", "tier"],
                  sort=[{"column": "id", "direction": "desc"}],
                  filters={"logic": "and", "conditions": [
                      {"column": "id", "op": "gt", "value": 1}]}),
        SCHEMA, MASKED)
    # And with nothing masked, nothing is refused.
    service.guard_masked_query(
        QuerySpec(search="ana@", sort=[{"column": "email", "direction": "asc"}]),
        SCHEMA, {})


def test_search_is_allowed_when_the_masked_column_is_not_text():
    """``search`` only reaches text columns, so a numeric masked column is not
    an oracle and blocking search there would be a gratuitous regression."""
    schema = [
        {"name": "ssn", "normalized_name": "ssn", "dtype": "BIGINT", "position": 0},
        {"name": "Tier", "normalized_name": "tier", "dtype": "VARCHAR", "position": 1},
    ]
    service.guard_masked_query(QuerySpec(search="gold"), schema, {"ssn": "identifier"})


# --- cursors are scoped to a sheet, not just a version ------------------------

def test_two_sheets_of_one_version_get_different_cursor_scopes():
    """Cursor identity was the version id alone.

    Two sheets of one workbook share it, and an empty spec hashes identically
    on both, so a cursor minted on sheet A was accepted verbatim on sheet B and
    the caller silently resumed inside a different sheet.
    """
    ver = {"id": "11111111-1111-1111-1111-111111111111"}
    a = service._query_scope(ver, {"logical_sheet_id": "aaaa", "sheet_key": "revenue"})
    b = service._query_scope(ver, {"logical_sheet_id": "bbbb", "sheet_key": "expenses"})
    assert a != b
    assert a == service._query_scope(ver, {"logical_sheet_id": "aaaa",
                                           "sheet_key": "revenue"})
    # Legacy sheets carry no logical id; the key still separates them.
    assert (service._query_scope(ver, {"sheet_key": "revenue"})
            != service._query_scope(ver, {"sheet_key": "expenses"}))


# --- a view deleted mid-update is a 404, not a 500 ----------------------------

async def test_updating_a_view_that_vanished_mid_request_is_a_404(monkeypatch):
    """The route's existence check and the UPDATE are not one transaction.

    A concurrent DELETE between them makes ``repo.update_view`` return None,
    which ``DatasetViewOut(**None)`` turned into a TypeError and the catch-all
    handler turned into a 500. Two people editing the same dataset is ordinary,
    and the honest answer is that the view is gone.
    """
    from app.features.explorer import repo

    async def _gone(dataset_id, view_id, fields):
        return None

    monkeypatch.setattr(repo, "update_view", _gone)
    ds = {"id": "11111111-1111-1111-1111-111111111111"}
    view = {"id": "22222222-2222-2222-2222-222222222222",
            "version_selector": {"mode": "current"}, "query": {}}
    from app.features.explorer.schemas import DatasetViewUpdate

    with pytest.raises(HTTPException) as exc:
        await service.update_view(ds, view, DatasetViewUpdate(name="renamed"))
    assert exc.value.status_code == 404
