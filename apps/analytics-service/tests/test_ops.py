"""Operational hardening — durable upload status, jobs observability."""

from __future__ import annotations

from conftest import SAMPLE_CSV, auth, create_team_user, upload_file


async def test_upload_status_survives_restart(client, admin_id):
    """Status polling answers from the DB when the in-memory cache is gone."""
    h = auth(admin_id)
    up = await upload_file(client, admin_id, SAMPLE_CSV, name="s.csv")
    vid = up["version_id"]

    # Simulate a process restart: wipe the in-memory cache.
    from app.features.files.services.processing import processing_status
    processing_status.pop(vid, None)

    r = await client.get(f"/api/v1/upload/status/{vid}", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "complete" and body["row_count"] > 0


async def test_jobs_observability(client, admin_id):
    h = auth(admin_id)
    up = await upload_file(client, admin_id, SAMPLE_CSV, name="s.csv")
    ds = up["dataset_id"]

    # The import left a completed job; it's listable and fetchable.
    listing = (await client.get("/api/v1/jobs",
                                params={"job_type": "import", "status": "completed"},
                                headers=h)).json()
    assert listing["total"] >= 1
    mine = next(j for j in listing["items"] if j["dataset_id"] == ds)
    assert mine["progress"] == 100 and mine["result"]["row_count"] > 0

    detail = (await client.get(f"/api/v1/jobs/{mine['id']}", headers=h)).json()
    assert detail["status"] == "completed" and detail["job_type"] == "import"

    # Cross-team jobs are hidden (404), and listings are team-scoped.
    outsider, _ = await create_team_user(client, admin_id, "viewer")
    assert (await client.get(f"/api/v1/jobs/{mine['id']}",
                             headers=auth(outsider))).status_code == 404
    listing = (await client.get("/api/v1/jobs", headers=auth(outsider))).json()
    assert all(j["dataset_id"] != ds for j in listing["items"])
