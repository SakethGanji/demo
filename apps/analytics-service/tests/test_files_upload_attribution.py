"""Upload bookkeeping that only a real HTTP journey exposes.

Two things an upload leaves behind besides the dataset: a ``jobs`` row that the
job console reads, and staged bytes that the storage-admin screen totals. Both
are read through *different* endpoints than the one that created them, so a
mis-attributed job or an unscanned staging directory is invisible from the
upload response itself.
"""

from __future__ import annotations

from conftest import (
    DEFAULT_TEAM_ID, auth, create_team_user, rid, upload_file,
)

CSV = b"customer_id,tier\n1,gold\n2,silver\n3,gold\n"


async def test_an_import_job_is_owned_by_the_uploading_team_and_hidden_from_the_default_team(
        client, admin_id, tmp_path):
    """The import job an upload creates must carry the dataset's team.

    ``/api/v1/jobs`` is team-scoped (``team_id = ANY(:tids)``) and the detail
    route 404s cross-team. The upload path used to create its job without a
    team, so the row silently inherited ``create_job``'s default — the seeded
    Default team. In production that means the team that uploaded the file sees
    no import in its job console (an upload that appears to have never run),
    while every member of the unrelated Default team sees another tenant's
    dataset id and version id in theirs.
    """
    uploader, team = await create_team_user(client, admin_id, "editor")
    outsider, _ = await create_team_user(client, admin_id, "viewer",
                                         team_id=DEFAULT_TEAM_ID)

    src = tmp_path / f"orders_{rid()}.csv"
    src.write_bytes(CSV)
    up = await upload_file(client, uploader, src, team_id=team)
    dataset_id = up["dataset_id"]

    # ---- 1. The uploading team's job console shows its own import.
    r = await client.get("/api/v1/jobs", params={"job_type": "import"},
                         headers=auth(uploader))
    assert r.status_code == 200, r.text
    mine = [j for j in r.json()["items"] if j["dataset_id"] == dataset_id]
    assert len(mine) == 1, r.json()
    assert mine[0]["team_id"] == team
    assert mine[0]["dataset_version_id"] == up["version_id"]
    job_id = mine[0]["id"]

    # ---- 2. An unrelated tenant (the seeded Default team) sees nothing, and
    #         cannot resolve the id even if it learns it.
    r = await client.get("/api/v1/jobs", params={"job_type": "import"},
                         headers=auth(outsider))
    assert r.status_code == 200, r.text
    assert [j for j in r.json()["items"] if j["dataset_id"] == dataset_id] == []

    r = await client.get(f"/api/v1/jobs/{job_id}", headers=auth(outsider))
    assert r.status_code == 404, r.text

    # ---- 3. The owner can open it, and it agrees with the listing.
    r = await client.get(f"/api/v1/jobs/{job_id}", headers=auth(uploader))
    assert r.status_code == 200, r.text
    assert r.json()["team_id"] == team


async def test_storage_usage_counts_bytes_staged_in_the_uploads_staging_subdirectory(
        client, admin_id):
    """``uploads_bytes`` must include the simple-upload staging subdirectory.

    Multipart uploads stream into ``uploads_dir()/_staging`` before conversion,
    but the usage scan walked only the top level, so ``f.is_file()`` was False
    for the staging directory and every byte inside it counted as zero. An
    admin looking at the storage screen while a large upload is in flight — or
    after a crash left staged files behind — saw an uploads total of 0 and no
    way to account for the disk the volume had actually consumed.
    """
    from app.infra.db.storage import uploads_dir

    h = auth(admin_id)
    staging = uploads_dir() / "_staging"
    staging.mkdir(parents=True, exist_ok=True)

    before = (await client.get("/api/v1/storage/usage", headers=h)).json()

    probe = staging / f"probe_{rid()}_raw.csv"
    payload = b"x" * 4096
    probe.write_bytes(payload)
    try:
        after = (await client.get("/api/v1/storage/usage", headers=h)).json()
        assert after["uploads_bytes"] == before["uploads_bytes"] + len(payload)
        assert after["total_bytes"] == before["total_bytes"] + len(payload)
    finally:
        probe.unlink(missing_ok=True)
