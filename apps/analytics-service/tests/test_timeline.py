"""Wave 2 §13 — the dataset timeline: one merged, newest-first history feed."""

from __future__ import annotations

import json

from conftest import auth, create_team_user, upload_inline

ROWS = [{"id": 1, "tier": "gold"}, {"id": 2, "tier": "silver"}]


async def test_timeline_merges_all_event_sources(client, admin_id):
    h = auth(admin_id)
    ds = (await upload_inline(client, admin_id, json.dumps(ROWS)))["dataset_id"]
    await upload_inline(client, admin_id, json.dumps(ROWS), dataset_id=ds)

    # Tag activity (set + promote), a validation run, a profile run, a publish.
    r = await client.put(f"/api/v1/datasets/{ds}/tags", headers=h,
                         json={"tag_name": "staging", "version_number": 1})
    assert r.status_code == 200
    r = await client.post(f"/api/v1/datasets/{ds}/tags/production/promote",
                          headers=h, json={"version_number": 2, "reason": "go-live"})
    assert r.status_code == 200, r.text
    r = await client.post(f"/api/v1/datasets/{ds}/rules", headers=h,
                          json={"name": "id-not-null", "rule_type": "not_null",
                                "sheet_selector": "data",
                                "column_selector": "id"})
    assert r.status_code == 201, r.text
    assert (await client.post(f"/api/v1/datasets/{ds}/versions/2/validate",
                              headers=h)).status_code == 200
    assert (await client.post(f"/api/v1/datasets/{ds}/versions/2/profile-runs",
                              headers=h)).status_code == 200

    r = await client.post(f"/api/v1/datasets/{ds}/analytics", headers=h, json={
        "name": "pub-src", "kind": "sample",
        "params": {"target_total_volume": 2,
                   "sampling_steps": [{"method": "random", "sample_size": 2}]}})
    assert r.status_code == 201, r.text
    run = (await client.post(
        f"/api/v1/datasets/{ds}/analytics/{r.json()['id']}/run", headers=h)).json()
    r = await client.post(f"/api/v1/datasets/{ds}/analytics/runs/{run['id']}/publish",
                          headers=h, json={"mode": "new_dataset", "name": "tl-child"})
    assert r.status_code == 200, r.text
    child = r.json()["dataset_id"]

    r = await client.get(f"/api/v1/datasets/{ds}/timeline", headers=h,
                        params={"limit": 100})
    assert r.status_code == 200, r.text
    body = r.json()
    events = body["items"]
    types = {e["event_type"] for e in events}
    assert {"version_created", "tag_set", "tag_promote", "validation_run",
            "profile_run", "published_to", "audit"} <= types

    # Newest first.
    stamps = [e["occurred_at"] for e in events]
    assert stamps == sorted(stamps, reverse=True)

    # Event payloads carry their specifics.
    promote = next(e for e in events if e["event_type"] == "tag_promote")
    assert promote["details"]["tag"] == "production"
    assert promote["details"]["reason"] == "go-live"
    assert promote["actor"]  # actor email recorded
    validation = next(e for e in events if e["event_type"] == "validation_run")
    assert validation["details"]["status"] == "completed"
    published = next(e for e in events if e["event_type"] == "published_to")
    assert published["details"]["child_dataset"] == "tl-child"
    assert all(e["details"]["method"] != "GET"
               for e in events if e["event_type"] == "audit")

    # The child's timeline shows where it came from.
    r = await client.get(f"/api/v1/datasets/{child}/timeline", headers=h)
    kinds = {e["event_type"] for e in r.json()["items"]}
    assert "derived_from" in kinds

    # Offset paging over the merged feed.
    total = body["total"]
    first = (await client.get(f"/api/v1/datasets/{ds}/timeline", headers=h,
                              params={"limit": 3})).json()
    assert len(first["items"]) == 3 and first["total"] == total
    second = (await client.get(f"/api/v1/datasets/{ds}/timeline", headers=h,
                               params={"limit": 3, "offset": 3})).json()
    assert first["items"] != second["items"]


async def test_timeline_cross_team_404(client, admin_id):
    ds = (await upload_inline(client, admin_id, json.dumps(ROWS)))["dataset_id"]
    outsider, _ = await create_team_user(client, admin_id, "viewer")
    r = await client.get(f"/api/v1/datasets/{ds}/timeline", headers=auth(outsider))
    assert r.status_code == 404
