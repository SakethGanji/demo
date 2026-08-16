"""Every publish refusal must be machine-distinguishable, not prose.

`resolve_publishable_artifact` guards every publish path in the service — the
library's own POST /analytics/runs/{id}/publish, the transform publish, and the
join publish. All four of its refusals used to be bare `HTTPException(409)`s,
which the problem+json renderer collapses to the generic `code: "conflict"` —
the very same code a *completed* run that was already published (`code:
"run-already-published"`) is distinguished by.

What breaks in production without this: a Publish button cannot tell "the run is
still executing, keep polling" from "the run failed, disable me" from "the
artifact was garbage-collected, offer a re-run" without pattern-matching English
detail strings. Prose is not a contract: rewording a message silently changes
client behaviour. These tests pin the slug and the structured fields, so the
detail string stays free to change.
"""

from __future__ import annotations

import json

from conftest import auth, upload_inline
from sqlalchemy import text

from app.infra.db.postgres import async_session_factory


async def _sql(statement: str, **binds) -> None:
    async with async_session_factory() as s:
        await s.execute(text(statement), binds)
        await s.commit()


ROWS = [{"id": i, "region": "EU" if i % 2 else "US", "amount": float(i)}
        for i in range(1, 11)]


async def _dataset(client, admin_id):
    return (await upload_inline(client, admin_id, json.dumps(ROWS)))["dataset_id"]


async def _definition(client, admin_id, ds, *, kind="aggregate", name="d"):
    params = {"aggregate": {"group_by": ["region"],
                            "aggregations": [{"column": "amount",
                                              "function": "sum"}]},
              "profile": {}}[kind]
    r = await client.post(f"/api/v1/datasets/{ds}/analytics", headers=auth(admin_id),
                          json={"name": name, "kind": kind, "params": params})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def test_publishing_a_run_that_is_not_completed_answers_a_distinct_problem_code(
        client, admin_id):
    """A UI must branch on a code, not on the word "failed" in a sentence."""
    h = auth(admin_id)
    ds = await _dataset(client, admin_id)
    def_id = await _definition(client, admin_id, ds)
    run_id = (await client.post(f"/api/v1/datasets/{ds}/analytics/{def_id}/run",
                                headers=h)).json()["id"]

    # Drive the run back to a non-terminal state the way a still-executing run
    # looks to the publish endpoint.
    await _sql("UPDATE analytics_runs SET status = 'running', artifact_id = NULL, "
               "error = NULL WHERE id = :id", id=run_id)

    r = await client.post(
        f"/api/v1/datasets/{ds}/analytics/runs/{run_id}/publish", headers=h,
        json={"mode": "new_dataset", "name": "published-from-running"})
    assert r.status_code == 409, r.text
    body = r.json()
    assert body["code"] == "run-not-completed"
    assert body["run_status"] == "running"
    assert body["status"] == 409          # problem+json's own status is intact
    assert body["run_error"] is None

    # The same slug, a different run_status, for the terminal-but-failed case.
    await _sql("UPDATE analytics_runs SET status = 'failed', error = 'boom' "
               "WHERE id = :id", id=run_id)

    r = await client.post(
        f"/api/v1/datasets/{ds}/analytics/runs/{run_id}/publish", headers=h,
        json={"mode": "new_dataset", "name": "published-from-failed"})
    assert r.status_code == 409, r.text
    body = r.json()
    assert body["code"] == "run-not-completed"
    assert body["run_status"] == "failed"
    assert body["run_error"] == "boom"


async def test_publishing_a_profile_run_says_it_produced_no_artifact_by_code(
        client, admin_id):
    """"Nothing to publish" and "not finished yet" are different screens."""
    h = auth(admin_id)
    ds = await _dataset(client, admin_id)
    def_id = await _definition(client, admin_id, ds, kind="profile", name="prof")
    run = (await client.post(f"/api/v1/datasets/{ds}/analytics/{def_id}/run",
                             headers=h)).json()
    assert run["status"] == "completed" and not run.get("artifact_id")

    r = await client.post(
        f"/api/v1/datasets/{ds}/analytics/runs/{run['id']}/publish", headers=h,
        json={"mode": "new_dataset", "name": "from-profile"})
    assert r.status_code == 409, r.text
    body = r.json()
    assert body["code"] == "run-has-no-artifact"
    assert body["kind"] == "profile"
    # It is emphatically NOT the "still working" refusal.
    assert body["code"] != "run-not-completed"


async def test_a_vanished_artifact_and_a_vanished_source_version_have_their_own_codes(
        client, admin_id):
    """The last two refusals are reachable through the shared helper only.

    ``analytics_runs.artifact_id`` is ON DELETE SET NULL, so a collected
    artifact cannot be observed as a dangling id over HTTP — deleting the row
    turns the run into the "no artifact" case instead. These two branches are
    the helper's defence against a torn state (a restored backup, a manual
    fix-up), and they are exercised where they live. Without their own codes a
    caller cannot tell "re-run it, the output was garbage-collected" from
    "this run kind never had an output".
    """
    from app.api.errors import ProblemException
    from app.features.library.service import resolve_publishable_artifact

    ds = await _dataset(client, admin_id)
    def_id = await _definition(client, admin_id, ds, name="agg")
    run = (await client.post(f"/api/v1/datasets/{ds}/analytics/{def_id}/run",
                             headers=auth(admin_id))).json()
    assert run["status"] == "completed" and run["artifact_id"]

    ghost = "00000000-0000-0000-0000-0000000000ff"
    try:
        await resolve_publishable_artifact({**run, "artifact_id": ghost})
    except ProblemException as exc:
        assert exc.status_code == 409
        assert exc.code == "run-artifact-missing"
        assert exc.extra["artifact_id"] == ghost
    else:
        raise AssertionError("a missing artifact must refuse the publish")

    try:
        await resolve_publishable_artifact({**run, "dataset_version_id": ghost})
    except ProblemException as exc:
        assert exc.status_code == 409
        assert exc.code == "run-source-version-missing"
        assert exc.extra["dataset_version_id"] == ghost
    else:
        raise AssertionError("a missing source version must refuse the publish")
