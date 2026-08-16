"""``execute_join`` closes its run row and its job row on every failure path.

A join rides the same ``analytics_runs`` machinery as a saved definition, so it
inherits the same hazard: a row that is neither ``completed`` nor ``failed`` is
unrecoverable. Nothing retries an inline join, the job worker only claims
``pending`` jobs, and ``resolve_publishable_artifact`` rejects the run with a
409 that reads as "still working" rather than "abandoned".

``execute_definition`` was fixed for this; ``execute_join`` was not. Its guard
ended at ``_persist_table``, and the whole tail — the storage read, the
``artifacts`` insert, ``complete_run``, ``complete_job`` — ran outside it. Each
of those is a real failure: the object store can be unreachable, the artifact
insert can violate a constraint, either Postgres write can lose its connection.

The partial-completion case gets its own test because it is the one the obvious
fix gets wrong. ``library_repo.fail_run`` and ``jobs.fail_job`` are unguarded
(``WHERE id = :id``), so a blanket "on any exception, fail both" would overwrite
a run that had *already* completed — turning a truthful record into a lie in
order to fix a different row.

Driven against stubs rather than Postgres: which bookkeeping call happens for
which failure is control flow, not a database behaviour.
"""

from __future__ import annotations

import types

import pytest

from app.features.relationships import joins

DATASET_ID = "11111111-1111-1111-1111-111111111111"
TEAM_ID = "22222222-2222-2222-2222-222222222222"
VERSION_ID = "55555555-5555-5555-5555-555555555555"

RELATIONSHIP = {
    "id": "66666666-6666-6666-6666-666666666666",
    "status": "confirmed",
    "dataset_id": DATASET_ID,
    "to_dataset_id": "77777777-7777-7777-7777-777777777777",
    "from_logical_sheet_id": "aaaa", "to_logical_sheet_id": "bbbb",
    "from_column": "id", "to_column": "customer_id",
    "from_sheet": "Customers", "to_sheet": "Orders",
}


class _Principal:
    user_id = "44444444-4444-4444-4444-444444444444"


class _Spec:
    how = "inner"
    select_columns = None
    left_version = None
    right_version = None


class _Warnings:
    def model_dump(self) -> dict:
        return {"many_to_many": False}


class _Side:
    """Enough of a ``JoinSide`` for the bookkeeping tail to run."""

    def __init__(self, label: str):
        self.dataset = {"id": DATASET_ID, "team_id": TEAM_ID, "name": label}
        self.version = {"id": VERSION_ID, "version_number": 3}
        self.label = label


class _Conn:
    """Records ``close()`` so the tests can assert it still happens early."""

    def __init__(self):
        self.closed = False

    def execute(self, sql):  # only ever the COUNT(*) in execute_join
        return types.SimpleNamespace(fetchone=lambda: (5,))

    def close(self):
        self.closed = True


@pytest.fixture
def rig(monkeypatch):
    """Stub every collaborator of execute_join; record each bookkeeping write.

    Returns a namespace whose ``calls`` list is the assertion surface and whose
    ``fail_at`` dict lets a test make exactly one collaborator raise.
    """
    calls: list[tuple] = []
    fail_at: dict[str, Exception] = {}
    conn = _Conn()

    def _maybe(name):
        if name in fail_at:
            raise fail_at[name]

    async def _join_definition(relationship, how, created_by):
        return {"id": "33333333-3333-3333-3333-333333333333"}

    async def _open_sides(relationship, spec):
        return _Side("Customers.Sheet1"), _Side("Orders.Sheet1"), conn

    def build_join_sql(left, right, how, select_columns):
        return "SELECT 1", ["id", "amount"]

    def measure(conn_, left, right, how, names):
        return _Warnings()

    def _persist_table(conn_, table, prefix, layout):
        return "join_abc.parquet"

    async def create_job(*a, **k):
        return {"id": "job-1"}

    async def start_job(job_id):
        calls.append(("start_job", job_id))

    async def complete_job(job_id, result=None):
        _maybe("complete_job")
        calls.append(("complete_job", job_id))

    async def fail_job(job_id, error):
        calls.append(("fail_job", job_id, error))

    async def create_run(*a, **k):
        return {"id": "run-1", "status": "running"}

    async def complete_run(run_id, *, result_summary, artifact_id):
        _maybe("complete_run")
        calls.append(("complete_run", run_id))
        return {"id": run_id, "status": "completed", "artifact_id": artifact_id,
                "result_summary": result_summary}

    async def fail_run(run_id, error):
        calls.append(("fail_run", run_id, error))

    async def create_artifact(key, artifact_type, **k):
        _maybe("create_artifact")
        calls.append(("create_artifact", key))
        return {"id": "artifact-1"}

    def get_storage():
        def read_bytes(key):
            _maybe("read_bytes")
            return b"parquet-bytes"
        return types.SimpleNamespace(read_bytes=read_bytes)

    monkeypatch.setattr(joins, "_join_definition", _join_definition)
    monkeypatch.setattr(joins, "_open_sides", _open_sides)
    monkeypatch.setattr(joins, "build_join_sql", build_join_sql)
    monkeypatch.setattr(joins, "measure", measure)
    monkeypatch.setattr(joins, "_persist_table", _persist_table)
    monkeypatch.setattr(joins, "get_storage", get_storage)
    monkeypatch.setattr(joins, "jobs", types.SimpleNamespace(
        create_job=create_job, start_job=start_job,
        complete_job=complete_job, fail_job=fail_job))
    monkeypatch.setattr(joins, "library_repo", types.SimpleNamespace(
        create_run=create_run, complete_run=complete_run, fail_run=fail_run,
        create_artifact=create_artifact))
    return types.SimpleNamespace(calls=calls, fail_at=fail_at, conn=conn)


async def _execute():
    return await joins.execute_join(RELATIONSHIP, _Spec(), _Principal())


BOOM = RuntimeError("object store unreachable")


async def test_the_happy_path_completes_both_records(rig):
    result = await _execute()

    assert [c[0] for c in rig.calls] == [
        "start_job", "create_artifact", "complete_run", "complete_job"]
    assert result["run"]["status"] == "completed"
    assert result["summary"]["row_count"] == 5
    assert result["summary"]["sample_file"] == "join_abc.parquet"
    assert rig.conn.closed


@pytest.mark.parametrize("where", ["read_bytes", "create_artifact", "complete_run"])
async def test_a_failure_in_the_tail_closes_the_run_and_the_job(rig, where):
    """Every step before the run is closed must still close it.

    Pre-fix these three raised straight out of ``execute_join`` with no
    bookkeeping at all, leaving BOTH rows ``running`` forever — the exact state
    ``scripts/audit_stranded_analytics_runs.py`` exists to mop up.
    """
    rig.fail_at[where] = BOOM

    with pytest.raises(RuntimeError):
        await _execute()

    kinds = [c[0] for c in rig.calls]
    assert "complete_run" not in kinds and "complete_job" not in kinds
    assert kinds[-2:] == ["fail_run", "fail_job"], kinds
    # Run before job: `audit_stranded_analytics_runs` treats an open run beside
    # a terminal job as PROOF of stranding, which only holds while no live path
    # closes the job first.
    assert rig.calls[-2][2] == rig.calls[-1][2] == str(BOOM)
    # The DuckDB connection is released before any of this, not held across it.
    assert rig.conn.closed


async def test_a_completed_run_is_not_clobbered_when_only_the_job_fails(rig):
    """The partial-completion case.

    ``complete_run`` succeeded, so the run is legitimately ``completed`` and the
    artifact it points at exists. Only the job is still open. ``fail_run`` must
    not be called here at all — the Python ``run_closed`` flag is what prevents
    it, and that is the guarantee this test pins. (``fail_run`` also carries an
    ``AND status = 'running'`` guard now, so a regression here would be caught
    twice, but belt-and-braces is not a reason to stop checking the belt.)
    Only the job, which really is still open, gets failed.
    """
    rig.fail_at["complete_job"] = BOOM

    with pytest.raises(RuntimeError):
        await _execute()

    kinds = [c[0] for c in rig.calls]
    assert "fail_run" not in kinds, "a completed run must not be re-failed"
    assert kinds == ["start_job", "create_artifact", "complete_run", "fail_job"]


async def test_a_failure_inside_the_query_still_closes_both(rig):
    """The pre-existing guard is not collateral damage of the second block."""
    def _persist_table(conn_, table, prefix, layout):
        raise BOOM

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(joins, "_persist_table", _persist_table)
        with pytest.raises(RuntimeError):
            await _execute()

    assert [c[0] for c in rig.calls] == ["start_job", "fail_run", "fail_job"]
    assert rig.conn.closed


async def test_an_unconfirmed_relationship_opens_no_records(rig):
    """Rejected before any row exists — nothing to strand."""
    with pytest.raises(Exception):
        await joins.execute_join({**RELATIONSHIP, "status": "suggested"},
                                 _Spec(), _Principal())

    assert rig.calls == []
