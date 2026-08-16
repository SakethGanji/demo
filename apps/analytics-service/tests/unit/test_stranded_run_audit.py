"""The stranded-run audit's verdict logic (scripts/audit_stranded_analytics_runs).

Read-only reporting by default, so these tests pin the two things that decide
whether a row is *touched at all*: the age gate, and how strongly the job row
corroborates abandonment. The gate is what stops the tool killing live work —
a run started seconds ago looks exactly like one abandoned six weeks ago except
for its age, so nothing else may override it.

Pure: ``classify`` takes a mapping and a threshold and returns a verdict. No
DB, no clock — the age is measured by Postgres and handed in.
"""

from __future__ import annotations

import pytest

from scripts.audit_stranded_analytics_runs import (
    CLOSEABLE,
    DEFAULT_OLDER_THAN_MINUTES,
    IN_FLIGHT,
    LIKELY_STRANDED,
    OPEN_JOB_STATUSES,
    STRANDED,
    STRANDED_MARKER,
    TERMINAL_JOB_STATUSES,
    classify,
    close_reason,
    humanize_age,
)

HOUR = 3600.0
THRESHOLD = DEFAULT_OLDER_THAN_MINUTES * 60


def _row(**over):
    row = {"age_seconds": 6 * HOUR, "job_id": "job-1", "job_status": "running"}
    row.update(over)
    return row


def _verdict(**over):
    return classify(_row(**over), older_than_seconds=THRESHOLD)["verdict"]


# ---------------------------------------------------------------------------
# The age gate — the only thing standing between the tool and live work
# ---------------------------------------------------------------------------


def test_a_run_that_just_started_is_in_flight_not_stranded():
    """The whole premise: 'running' is the healthy state of a live run."""
    assert _verdict(age_seconds=2.0) == IN_FLIGHT


@pytest.mark.parametrize("job_status", sorted(TERMINAL_JOB_STATUSES))
def test_the_age_gate_outranks_every_other_signal(job_status):
    """A terminal job beside an open run is proof of *inconsistency*, but a
    two-second-old row is still the likeliest thing to be mid-write. Age is the
    single safety knob; no verdict may bypass it, or a bug in the corroborating
    logic becomes a bug that fails somebody's in-flight run."""
    assert _verdict(age_seconds=2.0, job_status=job_status) == IN_FLIGHT


def test_in_flight_is_never_closeable():
    assert IN_FLIGHT not in CLOSEABLE
    assert set(CLOSEABLE) == {STRANDED, LIKELY_STRANDED}


def test_the_boundary_is_inclusive_of_the_threshold():
    assert _verdict(age_seconds=THRESHOLD - 1) == IN_FLIGHT
    assert _verdict(age_seconds=THRESHOLD) == LIKELY_STRANDED


def test_lowering_the_threshold_promotes_a_young_row():
    """Operators who know their fleet is idle can narrow the window — but only
    explicitly, per invocation."""
    row = _row(age_seconds=90.0)
    assert classify(row, older_than_seconds=THRESHOLD)["verdict"] == IN_FLIGHT
    assert classify(row, older_than_seconds=60)["verdict"] == LIKELY_STRANDED


# ---------------------------------------------------------------------------
# How strongly the job row corroborates
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("job_status", sorted(TERMINAL_JOB_STATUSES))
def test_a_terminal_job_beside_an_open_run_is_proof_not_a_heuristic(job_status):
    """Every close path writes the run BEFORE the job — ``fail_run`` then
    ``fail_job``, ``complete_run`` then ``complete_job``. So there is no window
    in which a healthy attempt has a terminal job and a running run."""
    assert _verdict(job_status=job_status) == STRANDED


@pytest.mark.parametrize("job_status", sorted(OPEN_JOB_STATUSES))
def test_an_old_run_whose_job_is_also_open_is_only_likely(job_status):
    """Self-consistent rows are the ordinary stranding shape: both writes were
    skipped together. Downgraded to LIKELY because 'old and open' is also what
    a genuinely long-lived run would look like — the argument that none can
    exist is about inline execution, not about the row."""
    assert _verdict(job_status=job_status) == LIKELY_STRANDED


def test_a_run_whose_job_row_is_gone_is_still_reported():
    """``analytics_runs.job_id`` is ON DELETE SET NULL, so a deleted job leaves
    the run with nothing tracking it at all — the opposite of a reason to skip."""
    verdict = classify(_row(job_id=None, job_status=None), older_than_seconds=THRESHOLD)
    assert verdict["verdict"] == LIKELY_STRANDED
    assert "no job row" in verdict["why"]


def test_every_verdict_carries_its_own_justification():
    """The report is read by a human deciding whether to opt in, so the reason
    has to travel with the row rather than live in the docstring."""
    for row in (_row(age_seconds=2.0), _row(), _row(job_status="failed")):
        why = classify(row, older_than_seconds=THRESHOLD)["why"]
        assert why and why[0].islower() and len(why) > 30


def test_a_missing_age_is_treated_as_brand_new():
    """Degrade toward doing nothing: an unmeasurable age must not become a
    licence to close."""
    assert classify({"job_id": None, "job_status": None},
                    older_than_seconds=THRESHOLD)["verdict"] == IN_FLIGHT


# ---------------------------------------------------------------------------
# What gets written, and how it reads afterwards
# ---------------------------------------------------------------------------


def test_the_recorded_reason_is_greppable_and_honest():
    reason = close_reason(_row(age_seconds=50 * HOUR))
    # Greppable: tells a later reader this row was closed by an operator, not
    # by the service failing the work.
    assert reason.startswith(STRANDED_MARKER)
    assert "2d 2h" in reason
    # Honest: 'failed' is a claim about the record, not about the computation.
    assert "unknown" in reason


def test_the_default_threshold_is_far_past_any_real_request():
    """An analytics run never leaves the request that started it —
    ``execute_definition`` has no ``worker.dispatch``. An hour is orders of
    magnitude past that, which is the point."""
    assert DEFAULT_OLDER_THAN_MINUTES == 60


@pytest.mark.parametrize("seconds,expected", [
    (0, "0s"), (45, "45s"), (90, "1m 30s"), (3600, "1h 0m"),
    (5400, "1h 30m"), (86400, "1d 0h"), (200000, "2d 7h"), (None, "unknown"),
])
def test_ages_render_for_a_human_sanity_check(seconds, expected):
    assert humanize_age(seconds) == expected


def test_a_negative_age_does_not_render_as_a_negative_duration():
    """Clock skew between rows is possible; a '-3s' in the report is noise that
    reads like a bug in the tool rather than in the data."""
    assert humanize_age(-5) == "0s"
