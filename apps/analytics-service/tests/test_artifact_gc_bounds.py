"""Integration — the garbage-collection sweep stays inside its own bound.

``POST /api/v1/storage/gc`` documents itself as "bounded per call, so a large
backlog clears over several runs rather than one long request". The orphan pass
did not honour that: it iterated every unreferenced key under the artifact root
and issued blocking storage round trips for each one, on the event loop, inside
the request. A deployment with a real residue therefore got a GC call that ran
for minutes and stalled every other request in the process while it did.

The truncation itself then needs to be *reportable*. Without a signal, a sweep
that stopped at its limit is indistinguishable from a sweep that found nothing,
so an operator watching ``orphans_deleted`` has no way to know the root is still
dirty and the job should run again.

Runs against local FS and S3/MinIO, like the rest of the retention suite.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from conftest import auth

from app.features.files.services import retention
from app.infra.db.storage import ArtifactLayout, get_storage


def _past_grace() -> datetime:
    return datetime.now(timezone.utc) + timedelta(
        hours=retention.ORPHAN_GRACE_HOURS + 1)


async def _drain() -> None:
    """Clear residue other tests left under the artifact root.

    ``_db_cleanup`` deletes artifact rows between tests but not their blobs, so
    every earlier test in the session contributes orphans. These tests count
    deletions, so they have to start from a known-empty root.
    """
    while True:
        result = await retention.sweep_orphans(now=_past_grace(), limit=1000)
        if not result["more_remaining"]:
            return


def _orphan_keys(count: int, *, tag: str) -> list[str]:
    """Write *count* unreferenced blobs under the artifact root."""
    storage = get_storage()
    keys = []
    for i in range(count):
        key = ArtifactLayout("query_output", team_id=f"t-{tag}",
                             dataset_id=f"d-{tag}").key(f"{tag}-{i:03d}.parquet")
        storage.write_bytes(key, b"PAR1" + bytes([i % 251]) * 10)
        keys.append(key)
    return keys


async def test_the_orphan_sweep_deletes_no_more_than_its_limit_per_call(client):
    """Pins the bound the endpoint's own docstring promises.

    Without it one GC request deletes every orphan in the deployment in a
    single pass of blocking storage calls on the event loop: the request never
    returns on a large residue, and every other request served by that worker
    waits behind it.
    """
    await _drain()
    keys = _orphan_keys(5, tag="bound")
    storage = get_storage()

    result = await retention.sweep_orphans(now=_past_grace(), limit=2)

    assert result["orphans_deleted"] == 2, result
    survivors = [k for k in keys if storage.exists(k)]
    assert len(survivors) == 3, survivors


async def test_a_truncated_orphan_sweep_reports_that_more_remain(client):
    """A sweep that stopped early must not look like a clean sweep.

    ``more_remaining`` is the only difference between "nothing left to
    collect" and "I hit my limit and gave up"; without it the scheduler and
    the admin both read a truncated pass as done, and the residue is billed
    forever.
    """
    await _drain()
    _orphan_keys(4, tag="hint")

    truncated = await retention.sweep_orphans(now=_past_grace(), limit=2)
    assert truncated["orphans_deleted"] == 2, truncated
    assert truncated["more_remaining"] is True, truncated

    # Draining the rest flips the signal back off, so it means what it says.
    drained = await retention.sweep_orphans(now=_past_grace(), limit=50)
    assert drained["orphans_deleted"] == 2, drained
    assert drained["more_remaining"] is False, drained


async def test_a_finished_orphan_sweep_reports_nothing_remaining(client):
    """The no-op path has to answer the same shape the loop does.

    ``run_gc`` reads ``more_remaining`` off both passes unconditionally, so a
    branch that omits it is a KeyError in the GC endpoint rather than a
    report.
    """
    await _drain()

    result = await retention.sweep_orphans(now=_past_grace())

    assert result["orphans_deleted"] == 0, result
    assert result["more_remaining"] is False, result


async def test_the_gc_endpoint_surfaces_the_more_remaining_flag(client, admin_id):
    """The bound is worthless if the HTTP caller cannot see it was applied.

    The job scheduler and the admin UI both drive GC through this route, so
    the truncation signal has to survive the response model, not stop at the
    service boundary.
    """
    await _drain()

    r = await client.post("/api/v1/storage/gc", headers=auth(admin_id))

    assert r.status_code == 200, r.text
    assert r.json()["more_remaining"] is False, r.json()


async def test_the_orphan_sweep_asks_postgres_for_known_keys_in_batches(
        client, monkeypatch):
    """One ANY() over every key in the bucket is a single unbounded parameter.

    The lookup is "which of these keys has a row?"; sending them all as one
    bound array makes both the statement and its result set scale with the
    whole deployment, which is the shape that turns a housekeeping job into a
    database incident.
    """
    await _drain()
    _orphan_keys(3, tag="chunk")
    monkeypatch.setattr(retention, "KEY_LOOKUP_CHUNK", 1)

    seen: list[int] = []
    real = retention.library_repo.known_artifact_keys

    async def spy(storage_keys):
        seen.append(len(storage_keys))
        return await real(storage_keys)

    monkeypatch.setattr(retention.library_repo, "known_artifact_keys", spy)
    await retention.sweep_orphans(now=_past_grace(), limit=0)

    assert seen, "known_artifact_keys was never consulted"
    assert max(seen) <= 1, seen
