"""``start_run`` and ``worker.dispatch`` leave nothing open when they blow up.

Two rows record one transformation attempt, and they are closed by different
code. ``transformation_runs`` is opened by ``start_run`` *before* the dispatch
and closed only by the registered handler (``_handle_transform``). ``jobs`` is
opened, started and closed by ``worker.dispatch``. So the gap is everything
``dispatch`` does *around* the handler: if that fails, the handler never ran,
nobody closed the run, and the row says ``running`` forever while the caller is
handed a ``transformation-failed`` problem+json saying the opposite.

Async mode is the sharp end. With ``sync=False`` the run row is *designed* to be
left ``running`` for the worker loop to pick up — so a failure of
``jobs.create_job`` strands it with no job row in existence at all. Nothing
distinguishes "queued" from "abandoned", and unlike ``analytics_runs`` there is
no audit script for this table.

The double-close case matters as much as the strand: when the handler DID run
and recorded the real pipeline error, ``start_run``'s belt-and-braces
``fail_run`` must not overwrite it with a vaguer one. That is what the
``status = 'running'`` guard on ``repo.fail_run`` buys, and it is asserted here
against the SQL rather than trusted.

Stubs, not Postgres: which bookkeeping call fires for which failure is control
flow. The guard's SQL is checked separately, by reading it.
"""

from __future__ import annotations

import types

import pytest
from fastapi import HTTPException

from app.api.errors import ProblemException
from app.features.transform import repo as transform_repo
from app.features.transform import service
from app.shared import worker

DATASET = {"id": "11111111-1111-1111-1111-111111111111",
           "team_id": "22222222-2222-2222-2222-222222222222"}
DEFINITION = {"id": "33333333-3333-3333-3333-333333333333",
              "version_selector": None, "logical_sheet_id": "aaaa"}


class _Principal:
    user_id = "44444444-4444-4444-4444-444444444444"


@pytest.fixture
def rig(monkeypatch):
    """Stub start_run's collaborators; record every run-row write."""
    calls: list[tuple] = []

    async def resolve_version(dataset_id, **pin):
        return {"id": "55555555-5555-5555-5555-555555555555", "path": "/tmp/x.parquet",
                "status": "ready", "version_number": 2}

    async def _resolve_run_sheet(ver, logical_sheet_id):
        return {"sheet_name": "Sheet1", "schema_json": {}}

    async def create_run(**kwargs):
        return {"id": "run-1", "status": "running"}

    async def fail_run(run_id, error):
        calls.append(("fail_run", run_id, error))

    async def get_run(run_id):
        return {"id": run_id, "status": "running"}

    monkeypatch.setattr(service, "resolve_version", resolve_version)
    monkeypatch.setattr(service, "_resolve_run_sheet", _resolve_run_sheet)
    monkeypatch.setattr(service, "repo", types.SimpleNamespace(
        create_run=create_run, fail_run=fail_run, get_run=get_run))
    return types.SimpleNamespace(calls=calls)


def _dispatch_raising(exc):
    async def dispatch(job_type, **kwargs):
        raise exc
    return dispatch


async def _start(sync: bool):
    return await service.start_run(DATASET, DEFINITION, _Principal(), sync=sync)


BOOM = RuntimeError("could not enqueue job: connection reset")


@pytest.mark.parametrize("sync", [True, False], ids=["sync", "async"])
async def test_a_dispatch_failure_closes_the_run_row(rig, monkeypatch, sync):
    """Pre-fix this raised out of start_run with the row still ``running``.

    In async mode it is unambiguously permanent: the exception came from
    ``jobs.create_job``, so no job row exists and the worker loop will never see
    this transformation again.
    """
    monkeypatch.setattr(service.worker, "dispatch", _dispatch_raising(BOOM))

    with pytest.raises(ProblemException) as e:
        await _start(sync=sync)

    # Contract corrected: an infrastructure failure in the dispatch plumbing is
    # a 503 ``dispatch-failed``, not the 400 ``transformation-failed`` that
    # blamed the caller's pipeline. The full statement of that contract lives in
    # tests/unit/test_transform_dispatch_errors.py.
    assert e.value.code == "dispatch-failed"
    assert [c[0] for c in rig.calls] == ["fail_run"]
    assert rig.calls[0][1] == "run-1"
    assert str(BOOM) in rig.calls[0][2]


async def test_a_problem_exception_from_the_dispatch_also_closes_it(rig, monkeypatch):
    """The 4xx branch re-raises untouched — but must still do the bookkeeping.

    This is the branch that mattered in ``library/service.py``: a
    ``ProblemException`` is a *Starlette* HTTPException, it takes the re-raise
    path, and the re-raise path used to skip the run row entirely.
    """
    exc = ProblemException(400, "Step 3 references an unknown column",
                           code="unknown-column")
    monkeypatch.setattr(service.worker, "dispatch", _dispatch_raising(exc))

    with pytest.raises(ProblemException) as e:
        await _start(sync=True)

    assert e.value is exc, "the actionable 4xx must reach the caller unwrapped"
    assert [c[0] for c in rig.calls] == ["fail_run"]
    assert "unknown column" in rig.calls[0][2]


async def test_a_fastapi_http_exception_takes_the_same_branch(rig, monkeypatch):
    exc = HTTPException(404, "Sheet not found")
    monkeypatch.setattr(service.worker, "dispatch", _dispatch_raising(exc))

    with pytest.raises(HTTPException) as e:
        await _start(sync=True)

    assert e.value is exc
    assert [c[0] for c in rig.calls] == ["fail_run"]


async def test_a_successful_dispatch_writes_nothing_extra(rig, monkeypatch):
    """The happy path — and async mode's deliberately-open row — are untouched."""
    async def dispatch(job_type, **kwargs):
        return None

    monkeypatch.setattr(service.worker, "dispatch", dispatch)

    run = await _start(sync=False)

    assert rig.calls == []
    assert run["status"] == "running", "an async run stays open for the worker loop"


def test_fail_run_is_guarded_so_the_double_close_cannot_clobber():
    """``start_run`` fails the run unconditionally, so the SQL must be the guard.

    When the handler ran and recorded the precise pipeline error, or when the
    run genuinely COMPLETED and only ``complete_job`` tripped, ``start_run``'s
    later ``fail_run`` has to be a no-op. An unguarded ``WHERE id = :id`` would
    overwrite the better message, or demote a completed run to failed.
    """
    sql = transform_repo.fail_run.__doc__
    assert "still open" in sql

    import inspect

    source = inspect.getsource(transform_repo.fail_run)
    assert "status = 'running'" in source
    assert "WHERE id = :id AND status = 'running'" in source


# ---------------------------------------------------------------------------
# worker.dispatch — the job row
# ---------------------------------------------------------------------------

async def test_an_unserializable_handler_result_does_not_strand_the_job(monkeypatch):
    """``complete_job`` JSON-encodes the result, so it can fail on its own.

    A handler that returns something ``json.dumps`` cannot encode succeeds, and
    then ``complete_job`` raises — with the job already ``running``. Pre-fix
    that call sat outside the guard, so the row stayed ``running`` forever;
    ``_run_one`` (the background loop's entry point) had it inside all along, so
    whether a job could strand depended on *which* entry point ran it.
    """
    calls: list[tuple] = []

    async def create_job(job_type, **kwargs):
        return {"id": "job-9"}

    async def start_job(job_id):
        calls.append(("start_job", job_id))

    async def complete_job(job_id, result=None):
        # what json.dumps(result) does with a set
        raise TypeError("Object of type set is not JSON serializable")

    async def fail_job(job_id, error=None):
        calls.append(("fail_job", job_id, error))

    async def handler(job):
        return {"columns": {"a", "b"}}

    monkeypatch.setattr(worker, "jobs", types.SimpleNamespace(
        create_job=create_job, start_job=start_job,
        complete_job=complete_job, fail_job=fail_job))
    monkeypatch.setitem(worker._HANDLERS, "widget", handler)

    with pytest.raises(TypeError):
        await worker.dispatch("widget", params={}, inline=True)

    assert [c[0] for c in calls] == ["start_job", "fail_job"]
    assert "not JSON serializable" in calls[1][2]


async def test_the_inline_happy_path_still_completes_the_job(monkeypatch):
    calls: list[tuple] = []

    async def create_job(job_type, **kwargs):
        return {"id": "job-9"}

    async def start_job(job_id):
        calls.append(("start_job", job_id))

    async def complete_job(job_id, result=None):
        calls.append(("complete_job", job_id, result))

    async def fail_job(job_id, error=None):
        calls.append(("fail_job", job_id, error))

    async def handler(job):
        return {"rows": 5}

    monkeypatch.setattr(worker, "jobs", types.SimpleNamespace(
        create_job=create_job, start_job=start_job,
        complete_job=complete_job, fail_job=fail_job))
    monkeypatch.setitem(worker._HANDLERS, "widget", handler)

    result = await worker.dispatch("widget", params={}, inline=True)

    assert result == {"rows": 5}
    assert [c[0] for c in calls] == ["start_job", "complete_job"]
