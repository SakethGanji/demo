"""Wave 3 §17 — GET /datasets/{id}/health: multi-dimension read-model."""

from __future__ import annotations

import json

from conftest import auth, create_team_user, upload_inline

# Float amounts so the column stays DOUBLE when v2 introduces a null —
# an int column would flip BIGINT -> DOUBLE and (correctly) flag schema churn.
V1 = [{"region": "EU", "amount": 100.5}, {"region": "US", "amount": 50.5},
      {"region": "APAC", "amount": 75.5}]
# One amount null in five rows (20% -> missing "warning"), a new LATAM
# category + null spike on amount (drift), no duplicate rows.
V2 = [{"region": "EU", "amount": 100.5}, {"region": "LATAM", "amount": None},
      {"region": "US", "amount": 200.5}, {"region": "APAC", "amount": 50.5},
      {"region": "EU", "amount": 75.5}]


async def _health(client, user_id, ds):
    r = await client.get(f"/api/v1/datasets/{ds}/health", headers=auth(user_id))
    assert r.status_code == 200, r.text
    return r.json()


async def test_fresh_dataset_is_mostly_unknown(client, admin_id):
    ds = (await upload_inline(client, admin_id, json.dumps(V1)))["dataset_id"]
    d = await _health(client, admin_id, ds)
    assert d["current_version_number"] == 1
    dims = d["dimensions"]
    assert set(dims) == {"schema_stability", "validation", "missing_data",
                         "duplicates", "drift", "freshness", "documentation"}
    assert dims["schema_stability"]["status"] == "ok"
    for name in ("validation", "missing_data", "duplicates", "drift", "freshness"):
        assert dims[name]["status"] == "unknown", name
    assert dims["documentation"]["status"] == "attention"
    assert dims["documentation"]["evidence"]["bucket"] == "none"


async def test_dimensions_light_up_with_signals(client, admin_id):
    h = auth(admin_id)
    ds = (await upload_inline(client, admin_id, json.dumps(V1)))["dataset_id"]

    # Documentation: description + domain + sheet grain + both columns.
    r = await client.patch(f"/api/v1/datasets/{ds}", headers=h, json={
        "description": "Orders rollup", "domain": "sales",
        "refresh_frequency": "daily"})
    assert r.status_code == 200, r.text
    r = await client.put(f"/api/v1/datasets/{ds}/sheet-metadata/data", headers=h,
                         json={"grain": "one row per order"})
    assert r.status_code == 200, r.text
    for col in ("region", "amount"):
        r = await client.put(
            f"/api/v1/datasets/{ds}/sheet-metadata/data/columns/{col}",
            headers=h, json={"business_name": col.title()})
        assert r.status_code == 200, r.text

    # Profile v1, upload v2 (drift + missing), profile v2, validate v2.
    assert (await client.post(f"/api/v1/datasets/{ds}/versions/1/profile-runs",
                              headers=h)).status_code == 200
    await upload_inline(client, admin_id, json.dumps(V2), dataset_id=ds)
    assert (await client.post(f"/api/v1/datasets/{ds}/versions/2/profile-runs",
                              headers=h)).status_code == 200
    r = await client.post(f"/api/v1/datasets/{ds}/rules", headers=h, json={
        "name": "region-not-null", "rule_type": "not_null",
        "sheet_selector": "data", "column_selector": "region"})
    assert r.status_code == 201, r.text
    run = (await client.post(f"/api/v1/datasets/{ds}/versions/2/validate",
                             headers=h)).json()
    assert run["status"] == "completed" and run["error_failures"] == 0

    d = await _health(client, admin_id, ds)
    assert d["current_version_number"] == 2
    dims = d["dimensions"]

    assert dims["schema_stability"]["status"] == "ok"
    assert dims["schema_stability"]["evidence"]["versions_considered"] == [1, 2]

    assert dims["validation"]["status"] == "ok"
    assert dims["validation"]["evidence"]["run_id"] == run["id"]
    assert dims["validation"]["evidence"]["version_number"] == 2

    assert dims["missing_data"]["status"] == "warning"
    worst = dims["missing_data"]["evidence"]["worst"]
    assert worst["column"] == "amount" and worst["null_percent"] == 20.0
    assert worst["version_number"] == 2

    assert dims["duplicates"]["status"] == "ok"

    assert dims["drift"]["status"] == "warning"
    assert dims["drift"]["evidence"]["compared"] == [
        {"sheet": "data", "from_version": 1, "to_version": 2}]
    assert "amount" in dims["drift"]["evidence"]["notable"][0]["columns"]

    assert dims["freshness"]["status"] == "ok"
    assert dims["freshness"]["evidence"]["refresh_frequency"] == "daily"

    assert dims["documentation"]["status"] == "ok"
    assert dims["documentation"]["evidence"] == {
        "bucket": "full", "has_description": True, "has_domain": True,
        "documented_sheets": 1, "total_sheets": 1,
        "documented_columns": 2, "total_columns": 2}


async def test_schema_change_flags_stability(client, admin_id):
    h = auth(admin_id)
    ds = (await upload_inline(client, admin_id, json.dumps(V1)))["dataset_id"]
    await upload_inline(
        client, admin_id,
        json.dumps([{"region": "EU", "amount": 1, "channel": "web"}]),
        dataset_id=ds)
    d = await _health(client, admin_id, ds)
    dim = d["dimensions"]["schema_stability"]
    assert dim["status"] == "warning"
    assert dim["evidence"]["changes"] == [
        {"sheet": "data", "from_version": 1, "to_version": 2}]


async def test_validation_failures_surface(client, admin_id):
    h = auth(admin_id)
    ds = (await upload_inline(client, admin_id, json.dumps(V2)))["dataset_id"]
    r = await client.post(f"/api/v1/datasets/{ds}/rules", headers=h, json={
        "name": "amount-not-null", "rule_type": "not_null",
        "sheet_selector": "data", "column_selector": "amount"})
    assert r.status_code == 201, r.text
    run = (await client.post(f"/api/v1/datasets/{ds}/versions/1/validate",
                             headers=h)).json()
    assert run["error_failures"] > 0

    d = await _health(client, admin_id, ds)
    assert d["dimensions"]["validation"]["status"] == "attention"


async def test_health_hidden_cross_team(client, admin_id):
    ds = (await upload_inline(client, admin_id, json.dumps(V1)))["dataset_id"]
    outsider, _ = await create_team_user(client, admin_id, "viewer")
    r = await client.get(f"/api/v1/datasets/{ds}/health", headers=auth(outsider))
    assert r.status_code == 404
