"""Phase 3 (reuse) — saved analytics, run history, publish, lineage.

Journey: save definitions → run them → inspect run history + artifacts →
publish an output as a new dataset and as a new version → verify lineage
both directions → the published data is a real, queryable dataset.
"""

from __future__ import annotations

from conftest import DEFAULT_TEAM_ID, SAMPLE_CSV, auth


async def _upload_csv(client, admin_id):
    with open(SAMPLE_CSV, "rb") as f:
        r = await client.post("/api/v1/upload",
                              headers={**auth(admin_id), "X-Team-Id": DEFAULT_TEAM_ID},
                              files={"file": ("accounts.csv", f, "text/csv")})
    assert r.status_code == 200, r.text
    return r.json()["dataset_id"]


async def test_library_journey(client, admin_id):
    h = auth(admin_id)
    ds = await _upload_csv(client, admin_id)

    # ---- 1. Save a sampling definition pinned to the current version ----
    r = await client.post(f"/api/v1/datasets/{ds}/analytics", headers=h, json={
        "name": "daily-sample", "kind": "sample",
        "params": {"target_total_volume": 5,
                   "sampling_steps": [{"method": "random", "sample_size": 5}],
                   "seed": 42}})
    assert r.status_code == 201, r.text
    sample_def = r.json()["id"]

    r = await client.post(f"/api/v1/datasets/{ds}/analytics", headers=h, json={
        "name": "quick-profile", "kind": "profile",
        "params": {"include_histograms": False}})
    assert r.status_code == 201
    profile_def = r.json()["id"]

    defs = (await client.get(f"/api/v1/datasets/{ds}/analytics", headers=h)).json()
    assert defs["total"] == 2

    # Duplicate names are rejected (unique per dataset).
    r = await client.post(f"/api/v1/datasets/{ds}/analytics", headers=h, json={
        "name": "daily-sample", "kind": "profile"})
    assert r.status_code == 409

    # ---- 2. Run them; results are inline AND durably recorded ----
    r = await client.post(f"/api/v1/datasets/{ds}/analytics/{sample_def}/run", headers=h)
    assert r.status_code == 200, r.text
    run = r.json()
    assert run["status"] == "completed"
    assert run["result"]["sampled_count"] == 5
    assert run["artifact_id"]                      # output registered as artifact
    assert run["result_summary"]["sample_file"]
    run_id = run["id"]

    r = await client.post(f"/api/v1/datasets/{ds}/analytics/{profile_def}/run", headers=h)
    assert r.status_code == 200
    assert r.json()["artifact_id"] is None         # profiles produce no artifact

    history = (await client.get(f"/api/v1/datasets/{ds}/analytics/{sample_def}/runs",
                                headers=h)).json()
    assert history["total"] == 1 and history["items"][0]["status"] == "completed"

    # ---- 3. Publish the sample as a NEW DATASET ----
    r = await client.post(f"/api/v1/datasets/{ds}/analytics/runs/{run_id}/publish",
                          headers=h, json={"mode": "new_dataset", "name": "accounts-sample"})
    assert r.status_code == 200, r.text
    pub = r.json()
    child = pub["dataset_id"]
    assert pub["dataset_name"] == "accounts-sample" and pub["version_number"] == 1

    # The published dataset is a real dataset: metadata, sheets, analytics all work.
    meta = (await client.get(f"/api/v1/datasets/{child}", headers=h)).json()
    assert meta["row_count"] == 5
    sheets = (await client.get(f"/api/v1/datasets/{child}/sheets", headers=h)).json()["items"]
    assert sheets[0]["name"] == "data" and sheets[0]["schema_fingerprint"]

    # ---- 4. Publish the same run as a NEW VERSION of the source dataset ----
    r = await client.post(f"/api/v1/datasets/{ds}/analytics/runs/{run_id}/publish",
                          headers=h, json={"mode": "new_version"})
    assert r.status_code == 200 and r.json()["version_number"] == 2
    versions = (await client.get(f"/api/v1/datasets/{ds}/versions", headers=h)).json()["items"]
    assert versions[0]["row_count"] == 5  # v2 is the 5-row sample

    # ---- 5. Lineage: child points up, parent points down ----
    lin = (await client.get(f"/api/v1/datasets/{child}/lineage", headers=h)).json()
    assert len(lin["parents"]) == 1
    assert lin["parents"][0]["relation"] == "published_from"
    assert lin["parents"][0]["parent_dataset_id"] == ds
    assert lin["parents"][0]["parent_version_number"] == 1

    lin = (await client.get(f"/api/v1/datasets/{ds}/lineage", headers=h)).json()
    child_ids = {c["child_dataset_id"] for c in lin["children"]}
    assert child in child_ids and ds in child_ids  # new_dataset + new_version children

    # ---- 6. Publishing a profile run is refused (no artifact) ----
    prof_runs = (await client.get(f"/api/v1/datasets/{ds}/analytics/{profile_def}/runs",
                                  headers=h)).json()["items"]
    r = await client.post(
        f"/api/v1/datasets/{ds}/analytics/runs/{prof_runs[0]['id']}/publish",
        headers=h, json={"mode": "new_dataset"})
    assert r.status_code == 409

    # ---- 7. Definition lifecycle: retarget to the production tag ----
    r = await client.put(f"/api/v1/datasets/{ds}/tags", headers=h,
                         json={"tag_name": "production", "version_number": 1})
    assert r.status_code == 200
    r = await client.patch(f"/api/v1/datasets/{ds}/analytics/{sample_def}", headers=h,
                           json={"version_selector": {"mode": "tag", "tag": "production"}})
    assert r.status_code == 200 and r.json()["version_selector"]["mode"] == "tag"
    r = await client.post(f"/api/v1/datasets/{ds}/analytics/{sample_def}/run", headers=h)
    assert r.status_code == 200  # runs against the tagged (original) version

    r = await client.delete(f"/api/v1/datasets/{ds}/analytics/{profile_def}", headers=h)
    assert r.status_code == 204
    defs = (await client.get(f"/api/v1/datasets/{ds}/analytics", headers=h)).json()
    assert defs["total"] == 1
