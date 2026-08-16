"""``execute_definition`` closes its run row on every failure path.

An ``analytics_runs`` row that is neither ``completed`` nor ``failed`` is
unrecoverable: nothing retries it, nothing reaps it, and every occurrence
accumulates. So the three exception classes that can escape a run —
``ProblemException``, FastAPI's ``HTTPException``, and anything else — must all
do the SAME bookkeeping, and differ only in what the caller is told.

The regression this pins: ``ProblemException`` subclasses STARLETTE's
``HTTPException``, while the old guard caught FASTAPI's. Those are siblings,
not parent and child, so the guard matched neither the errors it was written
for (actionable 4xx, reported as 500s) nor left the ones it did match in a
consistent state (no bookkeeping at all, hence ``running`` forever).

Driven against stubs rather than Postgres: the whole point is *which*
bookkeeping calls happen for *which* exception, and that is not a database
behaviour.
"""

from __future__ import annotations

import types

import pytest
from fastapi import HTTPException

from app.api.errors import ProblemException
from app.features.library import service

DATASET = {"id": "11111111-1111-1111-1111-111111111111",
           "team_id": "22222222-2222-2222-2222-222222222222"}
#: 'profile' is the one kind that touches neither storage nor the artifact
#: table, so the run/job bookkeeping is all that is left to observe.
DEFINITION = {"id": "33333333-3333-3333-3333-333333333333", "kind": "profile",
              "sheet": None, "params": {}, "version_selector": None}


class _Principal:
    user_id = "44444444-4444-4444-4444-444444444444"


class _ProfileResult:
    row_count, column_count = 7, 3

    def model_dump(self) -> dict:
        return {"row_count": self.row_count, "column_count": self.column_count}


@pytest.fixture
def calls(monkeypatch):
    """Stub the version/job/run side of execute_definition; record every write."""
    recorded: list[tuple] = []

    async def resolve_version(dataset_id, **pin):
        return {"id": "55555555-5555-5555-5555-555555555555"}

    async def create_job(*args, **kwargs):
        return {"id": "job-1"}

    async def start_job(job_id):
        recorded.append(("start_job", job_id))

    async def complete_job(job_id, result=None):
        recorded.append(("complete_job", job_id))

    async def fail_job(job_id, error):
        recorded.append(("fail_job", job_id, error))

    async def create_run(*args, **kwargs):
        return {"id": "run-1"}

    async def complete_run(run_id, **kwargs):
        recorded.append(("complete_run", run_id))
        return {"id": run_id, "status": "completed"}

    async def fail_run(run_id, error):
        recorded.append(("fail_run", run_id, error))

    monkeypatch.setattr(service, "resolve_version", resolve_version)
    monkeypatch.setattr(service, "jobs", types.SimpleNamespace(
        create_job=create_job, start_job=start_job,
        complete_job=complete_job, fail_job=fail_job))
    monkeypatch.setattr(service, "repo", types.SimpleNamespace(
        create_run=create_run, complete_run=complete_run, fail_run=fail_run))
    return recorded


def _raising(exc: Exception):
    async def _run_profiling(request):
        raise exc
    return _run_profiling


async def _execute():
    return await service.execute_definition(DATASET, DEFINITION, _Principal())


UNKNOWN_COLUMN = ProblemException(
    400, "Unknown column: 'ghost'", code="unknown-column", available=["region"])
NOT_FOUND = HTTPException(404, "Sheet not found: Ghost")
BOOM = RuntimeError("duckdb exploded")


@pytest.mark.parametrize("exc", [UNKNOWN_COLUMN, NOT_FOUND, BOOM],
                         ids=["problem", "fastapi-http", "generic"])
async def test_every_exception_class_closes_the_run_and_the_job(calls, monkeypatch, exc):
    monkeypatch.setattr(service, "run_profiling", _raising(exc))

    with pytest.raises(Exception):
        await _execute()

    kinds = [c[0] for c in calls]
    assert kinds == ["start_job", "fail_run", "fail_job"], kinds
    # Both records carry the same error text — the operational record and the
    # product record describe one attempt and must not disagree.
    assert calls[1][2] == calls[2][2] == str(exc)


async def test_a_problem_exception_reaches_the_caller_intact(calls, monkeypatch):
    """Status, code and extra fields survive; they are the actionable part."""
    monkeypatch.setattr(service, "run_profiling", _raising(UNKNOWN_COLUMN))

    with pytest.raises(ProblemException) as e:
        await _execute()

    assert e.value is UNKNOWN_COLUMN
    assert e.value.status_code == 400
    assert e.value.code == "unknown-column"
    assert e.value.extra["available"] == ["region"]
    # Not re-wrapped: the old path reported this as 500 "Analytics run failed".
    assert "Analytics run failed" not in str(e.value.detail)


async def test_a_fastapi_http_exception_reaches_the_caller_intact(calls, monkeypatch):
    monkeypatch.setattr(service, "run_profiling", _raising(NOT_FOUND))

    with pytest.raises(HTTPException) as e:
        await _execute()

    assert e.value is NOT_FOUND and e.value.status_code == 404


async def test_an_unexpected_error_is_still_a_500_that_hides_nothing_useful(
    calls, monkeypatch
):
    """Only genuinely unexpected failures become a 500 — that part is unchanged."""
    monkeypatch.setattr(service, "run_profiling", _raising(BOOM))

    with pytest.raises(HTTPException) as e:
        await _execute()

    assert e.value.status_code == 500
    assert e.value.detail == "Analytics run failed: duckdb exploded"
    assert e.value.__cause__ is BOOM


async def test_a_successful_run_completes_both_records(calls, monkeypatch):
    """The happy path is not collateral damage of the widened except clause."""
    async def _run_profiling(request):
        return _ProfileResult()

    monkeypatch.setattr(service, "run_profiling", _run_profiling)

    run, result = await _execute()

    assert [c[0] for c in calls] == ["start_job", "complete_run", "complete_job"]
    assert run["status"] == "completed"
    assert result == {"row_count": 7, "column_count": 3}
