"""A run that COMPLETED must never be rewritten as ``failed``.

Both ``explorer`` and ``quality`` close a run from a single ``except`` that
covers more than the work the run represents. Anything raised *after*
``complete_run`` therefore reached an unguarded ``UPDATE ... WHERE id = :id``
and demoted a row whose real output was already persisted — the profile JSON,
or the rule results, still sitting right there next to a status saying the run
failed. Not a stranded row: a closed row that lies.

Two concrete reachable paths, one per feature:

* explorer — ``_run_out`` shapes the response *inside* the try and *before*
  ``run = None``, so a pydantic error while serialising a successful run failed
  it.
* quality — ``webhooks.emit`` ran inside the try, so a notification problem
  failed the validation and handed the caller a 500 for work that succeeded.

Postgres, not stubs: the fix is the ``AND status = 'running'`` predicate, and a
SQL predicate is only really provable against SQL. ``tests/unit/`` pins the
shape; this file pins the behaviour.
"""

from __future__ import annotations

import json

from sqlalchemy import text

from app.features.explorer import repo as explorer_repo
from app.features.explorer import service as explorer_service
from app.features.quality import api as quality_api
from app.features.library import repo as library_repo
from app.features.quality import repo as quality_repo
from app.infra.db.postgres.session import engine

from conftest import auth, make_orders_workbook, upload_file, upload_inline

ROWS = [
    {"id": 1, "email": "a@x.com", "tier": "gold"},
    {"id": 2, "email": "b@x.com", "tier": "silver"},
]

BOOM = RuntimeError("late failure, after the run had already completed")


async def _row(table: str, row_id: str) -> dict:
    async with engine.begin() as conn:
        r = (await conn.execute(
            text(f"SELECT * FROM accelerator.{table} WHERE id = :id"),
            {"id": row_id},
        )).mappings().one()
        return dict(r)


async def _job_status(job_id: str) -> str:
    async with engine.begin() as conn:
        return (await conn.execute(
            text("SELECT status FROM accelerator.jobs WHERE id = :id"),
            {"id": job_id})).scalar()


# ---------------------------------------------------------------------------
# explorer — profile_runs
# ---------------------------------------------------------------------------

async def _completed_profile_run(client, admin_id) -> tuple[str, dict]:
    ds = (await upload_inline(client, admin_id, json.dumps(ROWS)))["dataset_id"]
    r = await client.post(f"/api/v1/datasets/{ds}/versions/1/profile-runs",
                          headers=auth(admin_id))
    assert r.status_code == 200, r.text
    runs = r.json()
    assert len(runs) == 1 and runs[0]["status"] == "completed"
    return ds, runs[0]


async def test_explorer_fail_run_is_a_no_op_against_a_completed_run(client, admin_id):
    """The guard itself, exercised directly — pre-fix this flipped the row.

    Called on a run that already ended, ``fail_run`` must change nothing at all:
    not the status, not the error column, and not the profile it computed.
    """
    _, run = await _completed_profile_run(client, admin_id)

    await explorer_repo.fail_run(run["id"], str(BOOM))

    after = await _row("profile_runs", run["id"])
    assert after["status"] == "completed", "a completed profile run was demoted"
    assert after["error"] is None, "a completed run must not acquire an error"
    assert after["profile"] is not None, "the profile it computed is still real"


async def test_explorer_fail_run_still_closes_a_genuinely_open_run(client, admin_id):
    """The guard must not turn ``fail_run`` into a no-op in general.

    Rewind a real run to ``running`` — the state the failure path is actually
    for — and the close has to land, error text and all.
    """
    _, run = await _completed_profile_run(client, admin_id)
    async with engine.begin() as conn:
        await conn.execute(
            text("UPDATE accelerator.profile_runs SET status = 'running', "
                 "completed_at = NULL WHERE id = :id"),
            {"id": run["id"]})

    await explorer_repo.fail_run(run["id"], str(BOOM))

    after = await _row("profile_runs", run["id"])
    assert after["status"] == "failed"
    assert str(BOOM) in after["error"]


async def test_a_response_shaping_error_does_not_demote_the_completed_run(
        client, admin_id, monkeypatch):
    """Path 1 end to end: ``_run_out`` blows up on an already-completed run.

    ``profile_version`` clears its ``run`` local only *after* ``_run_out``, so
    this exception arrives at the ``except`` with the completed run still held —
    exactly the double-close the guard exists to absorb. The 500 is correct
    here (the response genuinely cannot be built) but the demotion was not.
    """
    ds = (await upload_inline(client, admin_id, json.dumps(ROWS)))["dataset_id"]

    def exploding_run_out(run, insights, sheet_name):
        raise BOOM

    monkeypatch.setattr(explorer_service, "_run_out", exploding_run_out)

    r = await client.post(f"/api/v1/datasets/{ds}/versions/1/profile-runs",
                          headers=auth(admin_id))
    assert r.status_code == 500, r.text
    monkeypatch.undo()

    # The run row it left behind is the point of the test.
    async with engine.begin() as conn:
        rows = (await conn.execute(
            text("SELECT id::text, status, error, job_id::text FROM "
                 "accelerator.profile_runs WHERE dataset_id = :d"),
            {"d": ds})).mappings().all()
    assert len(rows) == 1
    run = dict(rows[0])
    assert run["status"] == "completed", (
        "the profiling itself succeeded and was persisted; only the response "
        "shaping failed, so the run must not be recorded as failed")
    assert run["error"] is None

    # Known, deliberate carve-out: shared/jobs.py::fail_job is unguarded on
    # purpose (execute_join's recovery path depends on that), so the job row
    # does still take the failure. Asserted so the divergence is visible rather
    # than discovered.
    assert await _job_status(run["job_id"]) == "failed"


# ---------------------------------------------------------------------------
# quality — validation_runs
# ---------------------------------------------------------------------------

async def _dataset_with_a_rule(client, admin_id, tmp_path) -> str:
    path = tmp_path / "orders.xlsx"
    make_orders_workbook(path, clean=True)
    ds = (await upload_file(client, admin_id, path, name="orders.xlsx"))["dataset_id"]
    r = await client.post(f"/api/v1/datasets/{ds}/rules", headers=auth(admin_id), json={
        "name": "customers-sheet-required", "rule_type": "sheet_exists",
        "sheet_selector": "customers"})
    assert r.status_code == 201, r.text
    return ds


async def test_quality_fail_run_is_a_no_op_against_a_completed_run(
        client, admin_id, tmp_path):
    """The guard itself, exercised directly — pre-fix this flipped the row."""
    ds = await _dataset_with_a_rule(client, admin_id, tmp_path)
    r = await client.post(f"/api/v1/datasets/{ds}/versions/1/validate",
                          headers=auth(admin_id))
    assert r.status_code == 200, r.text
    run = r.json()
    assert run["status"] == "completed"

    await quality_repo.fail_run(run["id"], str(BOOM))

    after = await _row("validation_runs", run["id"])
    assert after["status"] == "completed", "a completed validation run was demoted"
    assert after["error"] is None
    assert after["rules_total"] == 1, "the counts it computed are still real"


async def test_quality_fail_run_still_closes_a_genuinely_open_run(
        client, admin_id, tmp_path):
    ds = await _dataset_with_a_rule(client, admin_id, tmp_path)
    run = (await client.post(f"/api/v1/datasets/{ds}/versions/1/validate",
                             headers=auth(admin_id))).json()
    async with engine.begin() as conn:
        await conn.execute(
            text("UPDATE accelerator.validation_runs SET status = 'running', "
                 "completed_at = NULL WHERE id = :id"),
            {"id": run["id"]})

    await quality_repo.fail_run(run["id"], str(BOOM))

    after = await _row("validation_runs", run["id"])
    assert after["status"] == "failed"
    assert str(BOOM) in after["error"]


async def test_a_webhook_failure_neither_fails_nor_500s_a_good_validation(
        client, admin_id, tmp_path, monkeypatch):
    """Path 2 end to end — and the reason the guard alone was not enough.

    The rules ran, passed, and were persisted. A notification that could not be
    queued says nothing about any of that, so the caller gets their result and
    every row still says ``completed``. Pre-fix this was a 500 over a run
    rewritten as failed.
    """
    ds = await _dataset_with_a_rule(client, admin_id, tmp_path)

    async def exploding_emit(*a, **kw):
        raise BOOM

    monkeypatch.setattr(quality_api.webhooks, "emit", exploding_emit)

    r = await client.post(f"/api/v1/datasets/{ds}/versions/1/validate",
                          headers=auth(admin_id))
    monkeypatch.undo()

    assert r.status_code == 200, (
        "a validation that succeeded must not be reported to its caller as a "
        "server error because a webhook could not be queued")
    run = r.json()
    assert run["status"] == "completed"
    assert run["rules_total"] == 1 and run["error_failures"] == 0

    after = await _row("validation_runs", run["id"])
    assert after["status"] == "completed"
    assert after["error"] is None
    # Unlike the explorer path, the job row agrees: complete_job already ran
    # and nothing after it can now reach fail_job.
    assert await _job_status(str(after["job_id"])) == "completed"


async def test_a_real_validation_failure_still_fails_the_run(
        client, admin_id, tmp_path, monkeypatch):
    """Isolating the emit must not swallow failures of the validation itself."""
    ds = await _dataset_with_a_rule(client, admin_id, tmp_path)

    def exploding_rule(*a, **kw):
        raise BOOM

    monkeypatch.setattr(quality_api, "evaluate_rule", exploding_rule)

    r = await client.post(f"/api/v1/datasets/{ds}/versions/1/validate",
                          headers=auth(admin_id))
    monkeypatch.undo()
    assert r.status_code == 500, r.text

    async with engine.begin() as conn:
        row = (await conn.execute(
            text("SELECT status, error FROM accelerator.validation_runs "
                 "WHERE dataset_id = :d"),
            {"d": ds})).mappings().one()
    assert row["status"] == "failed"
    assert str(BOOM) in row["error"]


# ---------------------------------------------------------------------------
# analytics_runs — the third and last instance of the same pattern.
#
# execute_definition calls jobs.complete_job AFTER library_repo.complete_run.
# A failure in that gap hit the unguarded UPDATE and demoted a run whose
# result_summary was already persisted. Found while fixing the two above.
# ---------------------------------------------------------------------------

async def _completed_analytics_run(client, admin_id):
    """A genuinely completed analytics_runs row, via the real HTTP path."""
    h = auth(admin_id)
    ds = (await upload_inline(client, admin_id, json.dumps(ROWS)))["dataset_id"]
    r = await client.post(f"/api/v1/datasets/{ds}/analytics", headers=h, json={
        "name": "demote-probe", "kind": "sample",
        "params": {"target_total_volume": 2,
                   "sampling_steps": [{"method": "random", "sample_size": 2}],
                   "seed": 1}})
    assert r.status_code == 201, r.text
    run = (await client.post(
        f"/api/v1/datasets/{ds}/analytics/{r.json()['id']}/run",
        headers=h)).json()
    assert run["status"] == "completed", run
    return run


async def test_library_fail_run_is_a_no_op_against_a_completed_run(client, admin_id):
    """Pre-fix this demoted a successful run and discarded nothing else --
    leaving a result_summary sitting beside a status saying it failed."""
    run = await _completed_analytics_run(client, admin_id)

    await library_repo.fail_run(run["id"], str(BOOM))

    after = await _row("analytics_runs", run["id"])
    assert after["status"] == "completed", "a completed analytics run was demoted"
    assert after["error"] is None, "a completed run must not acquire an error"
    assert after["result_summary"] is not None, "its real output is still there"


async def test_library_fail_run_still_closes_a_genuinely_open_run(client, admin_id):
    """The guard must not make fail_run a no-op in general -- execute_definition
    and execute_join both depend on it actually closing an open row."""
    run = await _completed_analytics_run(client, admin_id)
    async with engine.begin() as conn:
        await conn.execute(
            text("UPDATE accelerator.analytics_runs SET status = 'running', "
                 "completed_at = NULL WHERE id = :id"),
            {"id": run["id"]})

    await library_repo.fail_run(run["id"], str(BOOM))

    after = await _row("analytics_runs", run["id"])
    assert after["status"] == "failed"
    assert str(BOOM) in after["error"]
