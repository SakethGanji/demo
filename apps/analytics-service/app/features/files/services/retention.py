"""Artifact retention — how long a derived output lives, and the sweep.

Derived outputs used to accumulate without bound. That was tolerable only
because nothing could tell them apart: with every kind in one flat prefix,
"expire scratch, keep published output" had no way to be expressed. The kind
is a path segment now, so the policy below is a real, enforceable statement.

Two ideas keep this honest:

* **Retention is decided once, at write time.** :func:`expires_at` is stamped
  on the artifact row. Editing the policy later does not retroactively shorten
  the life of something already written, so nothing disappears earlier than the
  deadline it was created with.
* **The row is the record, the blob follows it.** The sweep deletes the object
  first and the row second; a crash in between leaves an unreferenced blob that
  the orphan pass reclaims, which is recoverable. The reverse order would leave
  a row pointing at nothing, which reads as corruption.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from app.features.library import repo as library_repo
from app.infra.db.storage import ARTIFACT_ROOT, get_storage
from app.shared import worker

logger = logging.getLogger("analytics.retention")

# Days to keep, by artifact kind. None = keep forever.
#
# The split is by whether the output is a *record* or a *step*. A published
# source is referenced by a dataset version and must outlive everything. A
# validation failure file is evidence attached to a run someone will work
# through. Everything else is a step the user can simply repeat — the
# definition that produced it is saved, so the cheap thing is to re-run it,
# not to store the result indefinitely.
RETENTION_DAYS: dict[str, int | None] = {
    "published_source": None,
    "validation_failures": 90,
    "transform_output": 30,
    "join_output": 30,
    "sample_output": 30,
    "aggregation_output": 30,
    "pivot_output": 30,
    "diff_output": 14,
    "export": 7,
    "query_output": 7,
}

# An unknown kind is new code, not a licence to keep bytes forever; give it the
# conservative middle of the range rather than silently exempting it.
DEFAULT_RETENTION_DAYS = 30

# Orphan blobs are only swept once they are old enough that no in-flight write
# could still be racing us — a job writes the parquet before it registers the
# row, so a brand-new unreferenced key is normal, not garbage.
ORPHAN_GRACE_HOURS = 24

# Bound one sweep so a backlog degrades into several runs rather than one very
# long transaction holding a connection.
GC_BATCH = 500

# How many storage keys to hand Postgres in one ``storage_key = ANY(...)``
# lookup. The orphan pass has to ask "which of these have a row?" about every
# key under the artifact root, and a deployment can hold far more keys than
# belong in a single bound array parameter.
KEY_LOOKUP_CHUNK = 1000


def expires_at(artifact_type: str, *, now: datetime) -> datetime | None:
    """Deadline for an artifact of *artifact_type*, or None to keep forever."""
    days = RETENTION_DAYS.get(artifact_type, DEFAULT_RETENTION_DAYS)
    return None if days is None else now + timedelta(days=days)


def policy() -> list[dict]:
    """The policy as data, for the admin endpoint."""
    return [{"artifact_type": k, "retention_days": v}
            for k, v in sorted(RETENTION_DAYS.items())]


async def sweep_expired(*, limit: int = GC_BATCH) -> dict:
    """Delete artifacts past their deadline. Returns a per-kind tally."""
    storage = get_storage()
    rows = await library_repo.list_expired_artifacts(limit=limit)
    deleted: dict[str, int] = {}
    freed = 0
    done: list[str] = []
    for row in rows:
        try:
            storage.delete(row["storage_key"])
        except Exception as e:  # already gone, or backend hiccup — row still goes
            logger.warning("retention: could not delete %s: %s",
                           row["storage_key"], e)
        done.append(row["id"])
        deleted[row["artifact_type"]] = deleted.get(row["artifact_type"], 0) + 1
        freed += row["size_bytes"] or 0
    if done:
        await library_repo.delete_artifacts(done)
    return {"expired_deleted": len(done), "by_type": deleted,
            "bytes_freed": freed,
            # The repo query is itself LIMITed, so a full page is the signal
            # that the backlog outran this call.
            "more_remaining": len(rows) >= limit}


async def _known_keys(keys: list[str]) -> set[str]:
    """Which of *keys* have an owning artifact row, asked in bounded batches.

    One ``ANY(:sks)`` over every key in the deployment is a single enormous
    bound parameter and a single enormous result set; chunking keeps each
    round trip a size Postgres and the event loop can both absorb.
    """
    known: set[str] = set()
    for start in range(0, len(keys), KEY_LOOKUP_CHUNK):
        known |= await library_repo.known_artifact_keys(
            keys[start:start + KEY_LOOKUP_CHUNK])
    return known


async def sweep_orphans(*, now: datetime | None = None,
                        limit: int = GC_BATCH) -> dict:
    """Delete blobs under the artifact root with no owning row.

    These are the residue of a crash between writing a parquet and recording
    it. Without this pass they are unreachable forever: no listing shows them
    (the listing reads Postgres) and no authorization can ever succeed.

    Bounded at *limit* deletions per call, the same way :func:`sweep_expired`
    is. Every deletion here is a blocking backend round trip on the event
    loop, so an unbounded pass over a large residue is a request that never
    returns and a service that stops answering while it runs. Stopping early
    sets ``more_remaining``, which is the caller's cue to run the sweep again
    rather than to conclude the root is clean.
    """
    now = now or datetime.now(timezone.utc)
    storage = get_storage()
    # (key, size) in ONE traversal: sizing each candidate with ``size()`` cost
    # a second HEAD per object on S3, on top of the one ``modified_at`` needs.
    sized = storage.list_sizes(ARTIFACT_ROOT)
    if not sized:
        return {"orphans_deleted": 0, "bytes_freed": 0, "more_remaining": False}

    known = await _known_keys([k for k, _ in sized])
    cutoff = now - timedelta(hours=ORPHAN_GRACE_HOURS)
    deleted = 0
    freed = 0
    more_remaining = False
    for key, size in sized:
        if key in known:
            continue
        if deleted >= limit:
            # At least one more unreferenced key exists that we never looked
            # at; say so instead of reporting a clean sweep.
            more_remaining = True
            break
        try:
            if storage.modified_at(key) > cutoff:
                continue  # too young to be sure it is not an in-flight write
            storage.delete(key)
        except Exception as e:  # vanished or unreadable — nothing to reclaim
            logger.warning("retention: could not reclaim %s: %s", key, e)
            continue
        deleted += 1
        freed += size
    return {"orphans_deleted": deleted, "bytes_freed": freed,
            "more_remaining": more_remaining}


async def run_gc(*, limit: int = GC_BATCH) -> dict:
    """Both passes, as one reportable result."""
    expired = await sweep_expired(limit=limit)
    orphans = await sweep_orphans(limit=limit)
    return {
        "expired_deleted": expired["expired_deleted"],
        "by_type": expired["by_type"],
        "orphans_deleted": orphans["orphans_deleted"],
        "bytes_freed": expired["bytes_freed"] + orphans["bytes_freed"],
        "more_remaining": expired["more_remaining"] or orphans["more_remaining"],
    }


async def _handle_gc(params: dict, job_id: str | None = None) -> dict:
    """Job handler — the same body whether triggered by an admin or a schedule."""
    return await run_gc()


worker.register_handler("artifact_gc", _handle_gc)
