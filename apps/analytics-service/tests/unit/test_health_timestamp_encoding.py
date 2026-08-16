"""§17 health evidence — one timestamp encoding, and never the string "None".

The health read-model merges signals that reach it in two shapes: ``datetime``
objects from the version row and Postgres ``::text`` strings from the run
repos. Before ``iso_or_none`` the validation dimension emitted whatever
``str()`` produced and the freshness dimension emitted ``.isoformat()``, so a
single response carried two encodings of the same concept — and a missing
completion time became the literal four-character string ``"None"``.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.features.discovery.health import (
    evaluate_freshness,
    evaluate_validation,
    iso_or_none,
)

RUN = {"id": "run-1", "rules_total": 4, "rules_passed": 4, "rules_failed": 0,
       "error_failures": 0, "warning_failures": 0}


def test_a_missing_completion_time_is_reported_as_null_not_the_string_none():
    """``str(None)`` is ``"None"`` — a value that is neither a timestamp nor
    null, and that every client date parser accepts as a string and then fails
    on. A run row whose ``completed_at`` is NULL (a run recorded as completed
    without a stamped finish time, e.g. one closed by the stranded-run sweep)
    must surface as JSON ``null`` so a consumer can branch on "not known".
    """
    d = evaluate_validation({**RUN, "completed_at": None}, 3)
    assert d.evidence["completed_at"] is None


def test_validation_and_freshness_evidence_agree_on_one_timestamp_encoding():
    """Both dimensions describe an instant, so both must encode it the same
    way. The run repos hand health a Postgres ``::text`` timestamp — a SPACE
    between date and time — while the version row hands it a ``datetime``.
    Emitting each verbatim shipped ``"2026-08-05 12:00:00+00"`` next to
    ``"2026-08-05T12:00:00+00:00"`` in one response, so a client that parsed
    strict ISO-8601 read one dimension and broke on the other.
    """
    val = evaluate_validation({**RUN, "completed_at": "2026-08-05 12:00:00+00"}, 3)
    fresh = evaluate_freshness(
        datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc), "daily",
        version_number=3, now=datetime(2026, 8, 5, 18, 0, tzinfo=timezone.utc))

    assert val.evidence["completed_at"] == fresh.evidence["created_at"]
    assert "T" in val.evidence["completed_at"]


def test_an_unparseable_timestamp_is_passed_through_rather_than_raised():
    """Evidence is diagnostic: an odd value in one field must not 500 the whole
    health response, which is the read a caller reaches for precisely when a
    dataset is misbehaving.
    """
    assert iso_or_none("not-a-timestamp") == "not-a-timestamp"


def test_a_naive_timestamp_is_read_as_utc_like_every_other_stored_instant():
    """The service stores UTC. Leaving a naive stamp unqualified would let a
    client localize it to its own zone and misreport the age of a run by hours.
    """
    assert iso_or_none(datetime(2026, 8, 5, 12, 0)) == "2026-08-05T12:00:00+00:00"
