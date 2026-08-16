"""What ``start_run`` tells the caller when the *plumbing*, not the pipeline, fails.

``start_run`` has two failure surfaces and they belong to different people. The
user's SQL is compiled and executed inside the handler, and every way it can go
wrong already arrives as a problem+json — so the generic ``except Exception``
around ``worker.dispatch`` only ever catches infrastructure: ``jobs.create_job``
when Postgres is down, the job-row writes, the result serialization.

Pre-fix that branch answered ``400 transformation-failed`` with the raw
exception interpolated into ``detail``. Two things break in production. A 400
tells the client the request was bad, so a UI shows "your transformation is
invalid, fix it" for an outage and the user edits a pipeline that was fine —
and a retry-on-5xx client never retries something that would have succeeded a
second later. And the raw text of a DB driver error carries host, port, user and
sometimes credentials, which is exactly what the service's own unhandled-error
handler redacts (``app/api/errors.py``) — this path was routing around it.

Stubs, not Postgres: which status/code a given exception class produces is
control flow.
"""

from __future__ import annotations

import types

import pytest

from app.api.errors import ProblemException
from app.features.transform import service

DATASET = {"id": "11111111-1111-1111-1111-111111111111",
           "team_id": "22222222-2222-2222-2222-222222222222"}
DEFINITION = {"id": "33333333-3333-3333-3333-333333333333",
              "version_selector": None, "logical_sheet_id": "aaaa"}


class _Principal:
    user_id = "44444444-4444-4444-4444-444444444444"


class OperationalError(Exception):
    """Shaped like SQLAlchemy's: the message quotes the connection URL."""


# The kind of text a DB driver puts in an exception, and must never be echoed.
LEAKY = OperationalError(
    "connection to server at 10.4.2.7, port 5432 failed: FATAL: password "
    "authentication failed for user \"accelerator_rw\"")


@pytest.fixture
def rig(monkeypatch):
    """Stub start_run's collaborators; record every run-row write."""
    calls: list[tuple] = []

    async def resolve_version(dataset_id, **pin):
        return {"id": "55555555-5555-5555-5555-555555555555",
                "path": "/tmp/x.parquet", "status": "ready", "version_number": 2}

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


@pytest.mark.parametrize("sync", [True, False], ids=["sync", "async"])
async def test_an_infrastructure_failure_in_dispatch_is_not_reported_as_a_client_error(
        rig, monkeypatch, sync):
    async def dispatch(job_type, **kwargs):
        raise LEAKY

    monkeypatch.setattr(service.worker, "dispatch", dispatch)

    with pytest.raises(ProblemException) as e:
        await service.start_run(DATASET, DEFINITION, _Principal(), sync=sync)

    problem = e.value
    assert problem.status_code >= 500, "the caller's pipeline was not at fault"
    assert problem.code == "dispatch-failed"
    assert problem.code != "transformation-failed"

    detail = str(problem.detail)
    assert "password" not in detail and "10.4.2.7" not in detail
    assert "accelerator_rw" not in detail and "OperationalError" not in detail

    # The bookkeeping this branch exists for is untouched: the run row is still
    # closed, and it — not the response — carries the real error for an operator.
    assert rig.calls == [("fail_run", "run-1", str(LEAKY))]


async def test_the_run_row_keeps_the_detail_the_response_withholds(rig, monkeypatch):
    """Redacting the client's copy must not redact the operator's copy.

    The temptation when hiding an exception from a response is to stop passing
    it around at all; then nobody can diagnose the outage, because
    ``transformation_runs.error`` is the only durable record of it.
    """
    async def dispatch(job_type, **kwargs):
        raise LEAKY

    monkeypatch.setattr(service.worker, "dispatch", dispatch)

    with pytest.raises(ProblemException):
        await service.start_run(DATASET, DEFINITION, _Principal(), sync=True)

    assert "password authentication failed" in rig.calls[0][2]
