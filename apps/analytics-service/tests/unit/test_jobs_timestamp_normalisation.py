"""`JobOut` timestamp normalisation, without a database.

The jobs listing gets its timestamps from Postgres as text
(``2026-08-07 10:00:00+00``); the jobs detail gets them as ``datetime``
objects. Both go through the same normaliser so the wire format is one
thing. These pin each input shape the two repo queries can produce.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.features.jobs.api import JobOut, _iso


def _job(**overrides) -> JobOut:
    fields = {"id": "j1", "job_type": "import", "status": "completed",
              "created_at": datetime(2026, 8, 7, 10, 0, tzinfo=timezone.utc)}
    fields.update(overrides)
    return JobOut(**fields)


def test_the_postgres_text_cast_shape_is_rewritten_to_iso_8601():
    """`SELECT created_at::text` yields a half-width offset and a space.

    Without this the jobs listing ships '2026-08-07 10:00:00+00' — which
    JavaScript's `new Date` and Go's time.RFC3339 both reject — for a field
    the OpenAPI schema advertises as a timestamp.
    """
    assert _iso("2026-08-07 10:00:00.123456+00") == "2026-08-07T10:00:00.123456+00:00"


def test_a_datetime_from_the_detail_query_is_rewritten_to_iso_8601():
    """`SELECT *` yields a datetime whose str() is space-separated.

    Without this the jobs detail ships '2026-08-07 10:00:00+00:00', which
    differs from the listing's spelling of the very same instant, so a client
    that caches the listing value sees a phantom change on every poll.
    """
    got = _iso(datetime(2026, 8, 7, 10, 0, tzinfo=timezone.utc))
    assert got == "2026-08-07T10:00:00+00:00"


def test_both_repo_query_shapes_normalise_to_the_identical_string():
    """The listing and the detail must agree byte for byte on one instant."""
    from_detail = _iso(datetime(2026, 8, 7, 10, 0, 0, 123456, tzinfo=timezone.utc))
    from_listing = _iso("2026-08-07 10:00:00.123456+00")
    assert from_detail == from_listing


def test_a_naive_timestamp_is_read_as_utc_rather_than_as_local_time():
    """The jobs columns are timestamptz; a naive value means the driver dropped
    the zone, not that the instant is local. Without this, a deployment in a
    non-UTC container would silently shift every job time by its offset.
    """
    assert _iso(datetime(2026, 8, 7, 10, 0)) == "2026-08-07T10:00:00+00:00"


def test_a_non_utc_offset_is_converted_to_utc_rather_than_preserved():
    """Two jobs must be comparable by string sort, which needs one zone.

    Without this a session running with a non-UTC TimeZone setting would emit
    '+05:30' rows that sort before UTC rows recorded later.
    """
    tz = timezone(timedelta(hours=5, minutes=30))
    assert _iso(datetime(2026, 8, 7, 15, 30, tzinfo=tz)) == "2026-08-07T10:00:00+00:00"


def test_an_unparseable_timestamp_is_passed_through_instead_of_raising():
    """A read-only observability endpoint must not 500 on a weird value.

    Without this, one malformed row would take down the whole jobs listing —
    exactly the page an operator opens when something is already wrong.
    """
    assert _iso("not a timestamp") == "not a timestamp"


def test_absent_optional_timestamps_stay_null():
    """A pending job has no started_at/completed_at; those must remain null,
    not become the string 'None' or the epoch."""
    job = _job()
    assert job.started_at is None and job.completed_at is None


@pytest.mark.parametrize("field", ["created_at", "started_at", "completed_at"])
def test_every_timestamp_field_on_the_model_is_normalised(field: str):
    """All three fields go through the normaliser, not just created_at.

    Without this, a UI computing a job's duration from started_at and
    completed_at would be parsing a format its date library rejects.
    """
    job = _job(**{field: "2026-08-07 10:00:00+00"})
    assert getattr(job, field) == "2026-08-07T10:00:00+00:00"
