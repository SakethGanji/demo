"""Sampling validates column names against the SOURCE, not the sampled frame.

The original ``sort_by`` guard read ``if request.sort_by in combined.columns``,
gated behind ``not combined.empty``. Two consequences fall out of that shape and
both are pinned here, because neither is reachable from the HTTP layer without
contriving a zero-row pipeline:

* a run that selects no rows never inspected ``sort_by`` at all, so a typo on an
  empty result was accepted outright;
* validation has to happen before any DuckDB work, otherwise the first thing a
  bad column name meets is a BinderException (a 500) rather than the 400.

Pure DuckDB, no Postgres — this is request/schema logic, so it belongs in unit.
"""

from __future__ import annotations

import duckdb
import pytest

from app.api.errors import ProblemException
from app.features.data_accelerator.schemas import SampleRequest
from app.features.data_accelerator.services.sampling import (
    validate_request_columns,
)


@pytest.fixture
def conn():
    c = duckdb.connect()
    c.execute("CREATE VIEW df AS SELECT * FROM (VALUES (1, 'x'), (2, 'y')) t(a, grp)")
    yield c
    c.close()


def _request(**kwargs) -> SampleRequest:
    return SampleRequest(
        target_total_volume=1,
        sampling_steps=[{"method": "random", "sample_size": 1}],
        **kwargs,
    )


def test_an_unknown_sort_by_is_rejected_even_though_no_rows_were_sampled_yet(conn):
    with pytest.raises(ProblemException) as exc:
        validate_request_columns(conn, "df", _request(sort_by="ghost"))
    assert exc.value.status_code == 400
    assert exc.value.code == "unknown-column"
    assert exc.value.extra["available"] == ["a", "grp"]


def test_a_known_sort_by_passes_untouched(conn):
    validate_request_columns(conn, "df", _request(sort_by="grp"))


def test_every_offending_field_is_reported_at_once_not_just_the_first(conn):
    """A caller fixing a request body one 400 at a time is a bad loop; the
    response names every bad column it can see in one pass."""
    request = _request(
        sort_by="ghost",
        distribution_goals={"column": "phantom", "class_minimums": {"x": 1}},
    )
    with pytest.raises(ProblemException) as exc:
        validate_request_columns(conn, "df", request)
    assert exc.value.extra["columns"] == ["ghost", "phantom"]
    assert exc.value.extra["fields"] == ["distribution_goals.column", "sort_by"]
