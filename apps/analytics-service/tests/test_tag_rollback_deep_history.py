"""Rollback must find the previous version however long the tag sat still.

Nothing dedupes a no-op promote: promoting ``production`` to the version it
already points at appends another history row naming that same version. A
CI job that re-promotes on every green build therefore grows a run of
identical entries at the head of the history.

Rollback used to fetch the newest 100 rows and scan *that page* for the first
differing target. Once the run of same-target entries got longer than the page,
the scan found nothing and the endpoint answered 409 "No previous version in
history" — for a tag that demonstrably has one. That is the emergency path,
refusing to work at exactly the moment a team is trying to undo a bad release.

The rollback target is now resolved by a SQL predicate, so the depth of the
run does not matter. This test drives the run past the old page size.
"""

from __future__ import annotations

import pytest_asyncio

from conftest import auth, upload_inline

OLD_HISTORY_PAGE = 100


@pytest_asyncio.fixture
async def two_version_dataset(client, admin_id):
    ds = (await upload_inline(client, admin_id, '[{"a": 1}]'))["dataset_id"]
    await upload_inline(client, admin_id, '[{"a": 2}]', dataset_id=ds)
    return ds


async def test_rollback_finds_the_previous_version_beyond_the_old_history_page(
        client, admin_id, two_version_dataset):
    from app.features.data_accelerator import repo

    ds = two_version_dataset
    h = auth(admin_id)

    r = await client.post(f"/api/v1/datasets/{ds}/tags/production/promote",
                          headers=h, json={"version_number": 1})
    assert r.status_code == 200, r.text
    r = await client.post(f"/api/v1/datasets/{ds}/tags/production/promote",
                          headers=h, json={"version_number": 2})
    assert r.status_code == 200, r.text

    # Re-promote to the version already tagged, past the old 100-row window.
    v2 = await repo.get_version_by_number(ds, 2)
    for _ in range(OLD_HISTORY_PAGE + 20):
        await repo.set_tag(ds, str(v2["id"]), "production", action="promote",
                           version_number=2, reason="ci re-promote")

    history, total = await repo.list_tag_history(ds, "production", limit=1)
    assert total > OLD_HISTORY_PAGE + 1, "precondition: history must exceed one page"

    r = await client.post(f"/api/v1/datasets/{ds}/tags/production/rollback",
                          headers=h, json={"reason": "bad release"})
    assert r.status_code == 200, r.text
    assert r.json()["to_version_number"] == 1
    assert r.json()["from_version_number"] == 2

    r = await client.get(f"/api/v1/datasets/{ds}/tags/production", headers=h)
    assert r.status_code == 200 and r.json()["version_number"] == 1


async def test_rollback_still_refuses_a_tag_that_only_ever_saw_one_version(
        client, admin_id, two_version_dataset):
    """The SQL predicate replaced a Python scan, so the negative case has to be
    re-pinned: 'no earlier target exists' must stay a 409, not become a crash
    or a rollback onto the current version."""
    ds = two_version_dataset
    h = auth(admin_id)
    r = await client.post(f"/api/v1/datasets/{ds}/tags/staging/promote",
                          headers=h, json={"version_number": 2})
    assert r.status_code == 200, r.text
    for _ in range(3):
        r = await client.post(f"/api/v1/datasets/{ds}/tags/staging/promote",
                              headers=h, json={"version_number": 2})
        assert r.status_code == 200, r.text
    r = await client.post(f"/api/v1/datasets/{ds}/tags/staging/rollback",
                          headers=h, json={})
    assert r.status_code == 409, r.text


async def test_rollback_skips_delete_entries_when_choosing_the_previous_version(
        client, admin_id, two_version_dataset):
    """A delete row carries a NULL target. The old scan skipped it by checking
    ``action != 'delete'``; the SQL predicate skips it via ``IS NOT NULL``.
    Pin that the two are equivalent, so a delete/re-set cycle still rolls back
    to a real version rather than blowing up on a NULL version number."""
    ds = two_version_dataset
    h = auth(admin_id)
    assert (await client.put(f"/api/v1/datasets/{ds}/tags", headers=h, json={
        "tag_name": "edge", "version_number": 1})).status_code == 200
    assert (await client.delete(f"/api/v1/datasets/{ds}/tags/edge",
                                headers=h)).status_code == 200
    assert (await client.put(f"/api/v1/datasets/{ds}/tags", headers=h, json={
        "tag_name": "edge", "version_number": 2})).status_code == 200

    r = await client.post(f"/api/v1/datasets/{ds}/tags/edge/rollback",
                          headers=h, json={})
    assert r.status_code == 200, r.text
    assert r.json()["to_version_number"] == 1
