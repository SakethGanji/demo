"""Integration — artifact retention deadlines and the garbage-collection sweep.

Derived outputs used to accumulate without bound. Two passes reclaim them, and
each is testing a different failure the old design could not even detect:

* **expired** — an output past the deadline stamped on it at write time. The
  deadline is per-row, so editing the policy cannot retroactively delete
  something that was written under a longer one.
* **orphans** — blobs under the artifact root with no owning row, the residue
  of a crash between writing a parquet and registering it. Nothing else can
  ever reach them: the listing reads Postgres and the download resolves through
  the row, so without this pass they are permanently invisible *and*
  permanently billed.

Both run against local FS and S3/MinIO, since orphan detection depends on the
backend's last-modified semantics.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from conftest import SAMPLE_CSV, auth, create_team_user, upload_file, upload_inline
from sqlalchemy import text

from app.features.files.services import retention
from app.infra.db.postgres import async_session_factory
from app.infra.db.storage import ArtifactLayout, get_storage


async def _sample(client, user, ds):
    r = await client.post("/api/v1/sample", headers=auth(user), json={
        "dataset_id": ds, "target_total_volume": 2,
        "sampling_steps": [{"method": "random", "sample_size": 2}], "seed": 5})
    assert r.status_code == 200, r.text
    return r.json()["sample_file"]


async def _expire(filename: str) -> None:
    """Backdate an artifact's deadline so the sweep will collect it."""
    async with async_session_factory() as s:
        await s.execute(
            text("UPDATE artifacts SET expires_at = now() - interval '1 day' "
                 "WHERE filename = :fn"),
            {"fn": filename})
        await s.commit()


# ---------------------------------------------------------------------------
# Deadlines
# ---------------------------------------------------------------------------


async def test_new_artifact_gets_a_deadline_from_its_kind(client, admin_id):
    ds = (await upload_inline(
        client, admin_id, '[{"a": 1}, {"a": 2}, {"a": 3}]'))["dataset_id"]
    fname = await _sample(client, admin_id, ds)

    async with async_session_factory() as s:
        row = (await s.execute(
            text("SELECT artifact_type, expires_at FROM artifacts "
                 "WHERE filename = :fn"), {"fn": fname})).mappings().one()
    assert row["artifact_type"] == "sample_output"
    assert row["expires_at"] is not None

    days = retention.RETENTION_DAYS["sample_output"]
    expected = datetime.now(timezone.utc) + timedelta(days=days)
    assert abs((row["expires_at"] - expected).total_seconds()) < 300


async def test_publishing_survives_collection_of_its_source(client, admin_id):
    """A published dataset must not lose its data when the artifact expires.

    Publishing copies the blob into the dataset's own version storage rather
    than pointing at the artifact key. That copy is what makes retention safe
    to apply to every derived kind: collecting a sample_output can never strand
    a dataset version, because the version never depended on it.
    """
    h = auth(admin_id)
    ds = (await upload_inline(
        client, admin_id, '[{"a": 1}, {"a": 2}, {"a": 3}]'))["dataset_id"]

    r = await client.post(f"/api/v1/datasets/{ds}/analytics", headers=h, json={
        "name": "keep-me", "kind": "sample",
        "params": {"target_total_volume": 2,
                   "sampling_steps": [{"method": "random", "sample_size": 2}],
                   "seed": 42}})
    assert r.status_code == 201, r.text
    definition = r.json()["id"]

    r = await client.post(f"/api/v1/datasets/{ds}/analytics/{definition}/run",
                          headers=h)
    assert r.status_code == 200, r.text
    run_id = r.json()["id"]
    source_file = r.json()["result_summary"]["sample_file"]

    r = await client.post(
        f"/api/v1/datasets/{ds}/analytics/runs/{run_id}/publish", headers=h,
        json={"mode": "new_dataset", "name": "published-keep"})
    assert r.status_code == 200, r.text
    child = r.json()["dataset_id"]

    await _expire(source_file)
    r = await client.post("/api/v1/storage/gc", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["expired_deleted"] >= 1

    # The source artifact is gone…
    assert (await client.get(f"/api/v1/samples/{source_file}",
                             headers=h)).status_code == 404
    # …and the published dataset is still fully readable.
    r = await client.post(f"/api/v1/datasets/{child}/versions/1/sheets/data/query",
                          headers=h, json={})
    assert r.status_code == 200, r.text
    assert r.json()["total"] == 2


# ---------------------------------------------------------------------------
# Sweeps
# ---------------------------------------------------------------------------


async def test_gc_deletes_expired_artifacts_and_their_blobs(client, admin_id):
    editor, team = await create_team_user(client, admin_id, "editor")
    ds = (await upload_file(client, editor, SAMPLE_CSV, name="s.csv",
                            team_id=team))["dataset_id"]
    fname = await _sample(client, editor, ds)
    key = ArtifactLayout("sample_output", team_id=team, dataset_id=ds).key(fname)
    assert get_storage().exists(key)

    await _expire(fname)

    r = await client.post("/api/v1/storage/gc", headers=auth(admin_id))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["expired_deleted"] >= 1
    assert body["by_type"].get("sample_output", 0) >= 1

    assert not get_storage().exists(key)
    # The row goes with the blob, so the file stops being addressable at all.
    r = await client.get(f"/api/v1/samples/{fname}", headers=auth(editor))
    assert r.status_code == 404


async def test_gc_leaves_unexpired_artifacts_alone(client, admin_id):
    ds = (await upload_inline(
        client, admin_id, '[{"a": 1}, {"a": 2}, {"a": 3}]'))["dataset_id"]
    fname = await _sample(client, admin_id, ds)

    r = await client.post("/api/v1/storage/gc", headers=auth(admin_id))
    assert r.status_code == 200, r.text

    r = await client.get(f"/api/v1/samples/{fname}", headers=auth(admin_id))
    assert r.status_code == 200


async def test_gc_reclaims_orphan_blobs_past_the_grace_period(client, admin_id):
    """A blob with no row is unreachable; only this pass can free it."""
    storage = get_storage()
    key = ArtifactLayout("query_output", team_id="t-orphan",
                         dataset_id="d-orphan").key("orphan.parquet")
    storage.write_bytes(key, b"PAR1orphanPAR1")

    # Inside the grace window it is treated as a possible in-flight write.
    kept = await retention.sweep_orphans()
    assert storage.exists(key), kept

    # Past the window it is garbage. Rather than wait, move the cutoff.
    later = datetime.now(timezone.utc) + timedelta(
        hours=retention.ORPHAN_GRACE_HOURS + 1)
    result = await retention.sweep_orphans(now=later)
    assert result["orphans_deleted"] >= 1
    assert result["bytes_freed"] > 0
    assert not storage.exists(key)


async def test_gc_never_reclaims_a_registered_blob(client, admin_id):
    """The orphan pass keys off the artifacts table — a real output is safe."""
    editor, team = await create_team_user(client, admin_id, "editor")
    ds = (await upload_file(client, editor, SAMPLE_CSV, name="s.csv",
                            team_id=team))["dataset_id"]
    fname = await _sample(client, editor, ds)
    key = ArtifactLayout("sample_output", team_id=team, dataset_id=ds).key(fname)

    later = datetime.now(timezone.utc) + timedelta(days=365)
    await retention.sweep_orphans(now=later)
    assert get_storage().exists(key)


# ---------------------------------------------------------------------------
# Access control
# ---------------------------------------------------------------------------


async def test_retention_endpoints_are_platform_admin_only(client, admin_id):
    """These span every team, so team-scoped RBAC cannot express the answer."""
    editor, _team = await create_team_user(client, admin_id, "editor")

    for method, path in (("get", "/api/v1/storage/retention"),
                         ("post", "/api/v1/storage/gc")):
        r = await getattr(client, method)(path, headers=auth(editor))
        assert r.status_code == 403, (path, r.text)
        r = await getattr(client, method)(path, headers=auth(admin_id))
        assert r.status_code == 200, (path, r.text)


async def test_retention_policy_reports_rules_and_backlog(client, admin_id):
    ds = (await upload_inline(
        client, admin_id, '[{"a": 1}, {"a": 2}, {"a": 3}]'))["dataset_id"]
    fname = await _sample(client, admin_id, ds)
    await _expire(fname)

    body = (await client.get("/api/v1/storage/retention",
                             headers=auth(admin_id))).json()
    rules = {r["artifact_type"]: r["retention_days"] for r in body["rules"]}
    assert rules["published_source"] is None
    assert rules["query_output"] < rules["sample_output"]
    assert body["expired_pending"] >= 1
    assert body["orphan_grace_hours"] == retention.ORPHAN_GRACE_HOURS


async def test_gc_is_available_as_a_job(client, admin_id):
    """The sweep runs identically from the scheduler and from the endpoint."""
    from app.shared import worker

    assert "artifact_gc" in worker.registered_types()
    result = await worker.dispatch("artifact_gc", inline=True)
    assert result is not None
