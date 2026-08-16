"""The stranded-run audit against real rows (scripts/audit_stranded_analytics_runs).

``tests/unit/test_stranded_run_audit.py`` pins the verdict logic. This file
pins the two things only Postgres can answer: that the scan finds a real
stranded row with enough identifying detail for a human to sanity-check it, and
that ``--close`` leaves the run and its job agreeing about one attempt.

Stranding is *simulated*, not provoked: the code path that produced these rows
was removed. So each test runs a definition for real and then rewinds its rows
to the state the old ``execute_definition`` used to leave behind — which is
also exactly what the tool has to cope with in production, since every row it
will ever see was written by code that no longer exists.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from app.infra.db.postgres.session import engine
from scripts.audit_stranded_analytics_runs import (
    IN_FLIGHT,
    LIKELY_STRANDED,
    STRANDED,
    STRANDED_MARKER,
    close_row,
    collect,
)

from conftest import SAMPLE_CSV, auth, upload_file

HOUR = 3600


async def _run_a_definition(client, admin_id) -> tuple[str, str]:
    """Create and run a real profile definition. Returns (dataset_id, run_id)."""
    h = auth(admin_id)
    ds = (await upload_file(client, admin_id, SAMPLE_CSV, name="accounts.csv"))["dataset_id"]
    r = await client.post(f"/api/v1/datasets/{ds}/analytics", headers=h, json={
        "name": "nightly-profile", "kind": "profile",
        "params": {"include_histograms": False}})
    assert r.status_code == 201, r.text
    definition = r.json()["id"]

    r = await client.post(f"/api/v1/datasets/{ds}/analytics/{definition}/run", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "completed"
    return ds, r.json()["id"]


async def _strand(run_id: str, *, age_seconds: int, job_status: str | None = "running") -> None:
    """Rewind a completed run to the state the old failure path left behind.

    ``job_status=None`` deletes the job row, which the FK turns into
    ``analytics_runs.job_id = NULL``.
    """
    async with engine.begin() as conn:
        await conn.execute(
            text("""
                UPDATE accelerator.analytics_runs
                   SET status = 'running', completed_at = NULL, error = NULL,
                       result_summary = NULL,
                       started_at = now() - make_interval(secs => :age)
                 WHERE id = :id
            """),
            {"id": run_id, "age": age_seconds},
        )
        if job_status is None:
            await conn.execute(
                text("DELETE FROM accelerator.jobs WHERE id = "
                     "(SELECT job_id FROM accelerator.analytics_runs WHERE id = :id)"),
                {"id": run_id},
            )
        else:
            await conn.execute(
                text("""
                    UPDATE accelerator.jobs
                       SET status = :st,
                           completed_at = CASE WHEN :st IN ('pending', 'running')
                                               THEN NULL ELSE completed_at END
                     WHERE id = (SELECT job_id FROM accelerator.analytics_runs
                                  WHERE id = :id)
                """),
                {"id": run_id, "st": job_status},
            )


async def _row(run_id: str) -> dict:
    async with engine.begin() as conn:
        return dict((await conn.execute(
            text("SELECT r.status, r.error, r.completed_at, j.status AS job_status, "
                 "       j.error AS job_error, j.completed_at AS job_completed_at "
                 "  FROM accelerator.analytics_runs r "
                 "  LEFT JOIN accelerator.jobs j ON j.id = r.job_id "
                 " WHERE r.id = :id"),
            {"id": run_id})).mappings().one())


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


async def test_a_stranded_run_is_found_with_enough_detail_to_sanity_check_it(
        client, admin_id):
    """The report is the opt-in prompt, so it has to identify the row well
    enough that a human can decide without opening psql."""
    ds, run_id = await _run_a_definition(client, admin_id)
    await _strand(run_id, age_seconds=6 * HOUR)

    rows = await collect(older_than_seconds=HOUR)
    assert [r["run_id"] for r in rows] == [run_id]
    found = rows[0]
    assert found["verdict"] == LIKELY_STRANDED
    assert found["definition_name"] == "nightly-profile"
    assert found["kind"] == "profile"
    assert found["dataset_id"] == ds
    assert found["dataset_name"]
    assert found["actor_id"] == admin_id and found["actor_email"]
    assert found["job_id"] and found["job_status"] == "running"
    assert 6 * HOUR - 60 < found["age_seconds"] < 6 * HOUR + 60


async def test_a_terminal_job_beside_an_open_run_is_the_strong_verdict(client, admin_id):
    """The signature of the bug that produced these rows: the job was closed
    and the run was not."""
    _, run_id = await _run_a_definition(client, admin_id)
    await _strand(run_id, age_seconds=6 * HOUR, job_status="failed")

    rows = await collect(older_than_seconds=HOUR)
    assert rows[0]["verdict"] == STRANDED


async def test_a_run_whose_job_was_deleted_is_still_found(client, admin_id):
    _, run_id = await _run_a_definition(client, admin_id)
    await _strand(run_id, age_seconds=6 * HOUR, job_status=None)

    rows = await collect(older_than_seconds=HOUR)
    assert rows[0]["verdict"] == LIKELY_STRANDED
    assert rows[0]["job_id"] is None and rows[0]["job_status"] is None


async def test_a_recent_running_row_is_reported_but_never_actionable(client, admin_id):
    """A run started seconds ago is healthy. It is still *counted*, so the
    operator can see the threshold is hiding something, but it never becomes a
    close candidate."""
    _, run_id = await _run_a_definition(client, admin_id)
    await _strand(run_id, age_seconds=5)

    rows = await collect(older_than_seconds=HOUR)
    assert [r["verdict"] for r in rows] == [IN_FLIGHT]


async def test_completed_and_failed_runs_are_invisible_to_the_scan(client, admin_id):
    _, run_id = await _run_a_definition(client, admin_id)
    assert await collect(older_than_seconds=HOUR) == []
    await _strand(run_id, age_seconds=6 * HOUR)
    assert len(await collect(older_than_seconds=HOUR)) == 1


# ---------------------------------------------------------------------------
# Closing
# ---------------------------------------------------------------------------


async def test_closing_leaves_the_run_and_its_job_agreeing(client, admin_id):
    """The run and the job are the product and operational records of ONE
    attempt. Closing one without the other just mirrors the inconsistency the
    ``execute_definition`` fix was about."""
    _, run_id = await _run_a_definition(client, admin_id)
    await _strand(run_id, age_seconds=6 * HOUR)

    result = await close_row((await collect(older_than_seconds=HOUR))[0])
    assert result["run_closed"] and result["job_closed"]

    row = await _row(run_id)
    assert row["status"] == "failed" and row["job_status"] == "failed"
    assert row["completed_at"] is not None and row["job_completed_at"] is not None
    # Greppable afterwards: this was closed by an operator, not by the service.
    assert row["error"].startswith(STRANDED_MARKER)
    assert row["job_error"] == row["error"]
    assert "unknown" in row["error"]


async def test_a_job_that_already_recorded_an_outcome_is_not_rewritten(client, admin_id):
    """Its record is truthful and older than anything this tool knows."""
    _, run_id = await _run_a_definition(client, admin_id)
    await _strand(run_id, age_seconds=6 * HOUR, job_status="completed")

    result = await close_row((await collect(older_than_seconds=HOUR))[0])
    assert result["run_closed"] and not result["job_closed"]

    row = await _row(run_id)
    assert row["status"] == "failed"
    assert row["job_status"] == "completed"
    assert row["job_error"] is None


async def test_closing_is_idempotent_and_safe_to_re_run(client, admin_id):
    _, run_id = await _run_a_definition(client, admin_id)
    await _strand(run_id, age_seconds=6 * HOUR)
    rows = await collect(older_than_seconds=HOUR)

    first = await close_row(rows[0])
    assert first["run_closed"]
    error_after_first = (await _row(run_id))["error"]

    # A second pass finds nothing at all...
    assert await collect(older_than_seconds=HOUR) == []
    # ...and replaying the stale report row is still a no-op, so a human who
    # scrolled back and re-ran an old command cannot rewrite history.
    second = await close_row(rows[0])
    assert not second["run_closed"] and not second["job_closed"]
    assert (await _row(run_id))["error"] == error_after_first


async def test_a_run_that_finished_between_the_report_and_the_write_is_left_alone(
        client, admin_id):
    """The ``WHERE status = 'running'`` guard, stated as a race: the report is
    a snapshot, and the row may have legitimately completed since."""
    _, run_id = await _run_a_definition(client, admin_id)
    await _strand(run_id, age_seconds=6 * HOUR)
    stale = (await collect(older_than_seconds=HOUR))[0]

    async with engine.begin() as conn:
        await conn.execute(
            text("UPDATE accelerator.analytics_runs SET status = 'completed', "
                 "completed_at = now() WHERE id = :id"), {"id": run_id})

    assert not (await close_row(stale))["run_closed"]
    assert (await _row(run_id))["status"] == "completed"


@pytest.mark.parametrize("age", [5, 60])
async def test_an_in_flight_row_is_never_closed_by_the_cli(client, admin_id, age):
    """``main`` only closes CLOSEABLE verdicts. Pinned end-to-end because the
    consequence of getting it wrong is failing somebody's live run."""
    from scripts.audit_stranded_analytics_runs import CLOSEABLE

    _, run_id = await _run_a_definition(client, admin_id)
    await _strand(run_id, age_seconds=age)

    rows = await collect(older_than_seconds=HOUR)
    assert [r for r in rows if r["verdict"] in CLOSEABLE] == []
    assert (await _row(run_id))["status"] == "running"
