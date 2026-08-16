"""Enqueued discovery must be pollable by the caller that enqueued it.

``POST /relationships/suggest?sync=false`` returns before any work happens, so
the response body is the caller's ONLY link to the run. Without a job id the
only way to find out how it went is to list every
``relationship_discovery`` job in the team and guess which one is yours —
``GET /jobs`` has no dataset filter, so two discovery runs started at the same
time are indistinguishable, and a UI showing "discovery finished, 0 found"
could be reporting somebody else's dataset.
"""

from __future__ import annotations

from conftest import XLSX_MIME, auth, make_crm_workbook, upload_file


async def test_enqueued_discovery_returns_a_job_id_that_can_be_polled_to_completion(
        client, admin_id, tmp_path):
    from app.shared import worker

    path = tmp_path / "crm.xlsx"
    make_crm_workbook(path)
    ds = (await upload_file(client, admin_id, path, name="crm.xlsx",
                            content_type=XLSX_MIME))["dataset_id"]
    h = auth(admin_id)

    r = await client.post(
        f"/api/v1/datasets/{ds}/relationships/suggest?sync=false", headers=h)
    assert r.status_code == 200, r.text
    job_id = r.json()["job_id"]
    assert job_id, "async discovery must hand back the job it queued"

    # The handle resolves to the queued row, before anything has run it.
    queued = await client.get(f"/api/v1/jobs/{job_id}", headers=h)
    assert queued.status_code == 200, queued.text
    assert queued.json()["status"] == "pending"
    assert queued.json()["job_type"] == "relationship_discovery"
    assert queued.json()["dataset_id"] == ds

    assert await worker.run_pending_jobs_once() >= 1

    # The SAME id is how the caller learns the run finished, and what it found.
    done = await client.get(f"/api/v1/jobs/{job_id}", headers=h)
    assert done.status_code == 200
    assert done.json()["status"] == "completed"
    assert done.json()["result"]["suggested"] >= 1


async def test_the_inline_mode_still_returns_a_job_id(client, admin_id, tmp_path):
    """The default sync path keeps its handle — one contract, two modes."""
    path = tmp_path / "crm.xlsx"
    make_crm_workbook(path)
    ds = (await upload_file(client, admin_id, path, name="crm.xlsx",
                            content_type=XLSX_MIME))["dataset_id"]
    h = auth(admin_id)

    body = (await client.post(
        f"/api/v1/datasets/{ds}/relationships/suggest", headers=h)).json()

    assert body["job_id"]
    assert body["skipped"] == 0        # a small workbook is never capped
    job = await client.get(f"/api/v1/jobs/{body['job_id']}", headers=h)
    assert job.json()["status"] == "completed"
