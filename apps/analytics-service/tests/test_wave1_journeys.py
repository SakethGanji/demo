"""Wave 1 journey — the Dataset Exploration Workspace end-to-end.

Modeled on test_wave0_journeys.py: multi-step user journeys over the public
/api/v1 HTTP API only, driven the way a UI would sequence calls, with
non-admin actors and state flowing step to step.
"""

from __future__ import annotations

import json

from conftest import auth, create_team_user, upload_inline

V1 = [
    {"order_id": 1, "region": "EU", "amount": 100.0},
    {"order_id": 2, "region": "EU", "amount": 250.0},
    {"order_id": 3, "region": "US", "amount": 75.0},
    {"order_id": 4, "region": "US", "amount": 130.0},
    {"order_id": 5, "region": "APAC", "amount": 90.0},
]
V2 = [
    {"order_id": 1, "region": "EU", "amount": 100.0},
    {"order_id": 2, "region": "EU", "amount": 250.0},
    {"order_id": 3, "region": "US", "amount": None},
    {"order_id": 4, "region": "LATAM", "amount": None},
    {"order_id": 5, "region": "LATAM", "amount": None},
    {"order_id": 6, "region": "LATAM", "amount": 500.0},
]


async def test_journey_analyst_explores_saves_and_reviews_drift(client, admin_id):
    # ---- 1. An editor provisions data in their own team ----
    editor, team = await create_team_user(client, admin_id, "editor")
    h = auth(editor)
    ds = (await upload_inline(client, editor, json.dumps(V1),
                              team_id=team))["dataset_id"]

    # ---- 2. First look: preview, then a column deep-dive ----
    r = await client.get(f"/api/v1/datasets/{ds}/versions/1/preview",
                        params={"limit": 3}, headers=h)
    assert r.status_code == 200 and len(r.json()["items"]) == 3

    r = await client.get(f"/api/v1/datasets/{ds}/versions/1/columns/amount",
                        headers=h)
    assert r.status_code == 200
    assert r.json()["dtype"] == "numeric" and r.json()["null_count"] == 0

    # ---- 3. Narrow it down with a structured query ----
    r = await client.post(
        f"/api/v1/datasets/{ds}/versions/1/query", headers=h,
        json={"filters": {"conditions": [
                  {"column": "region", "op": "eq", "value": "EU"}]},
              "sort": [{"column": "amount", "direction": "desc"}]})
    assert r.status_code == 200
    assert [i["order_id"] for i in r.json()["items"]] == [2, 1]

    # ---- 4. Worth keeping: save it as a view ----
    r = await client.post(
        f"/api/v1/datasets/{ds}/views", headers=h,
        json={"name": "eu-orders",
              "sheet": "data",
              "query": {"filters": {"conditions": [
                            {"column": "region", "op": "eq", "value": "EU"}]},
                        "sort": [{"column": "amount", "direction": "desc"}]}})
    assert r.status_code == 201, r.text
    view_id = r.json()["id"]

    # ---- 5. Baseline profile for v1 ----
    r = await client.post(f"/api/v1/datasets/{ds}/versions/1/profile-runs",
                          headers=h)
    assert r.status_code == 200 and r.json()[0]["status"] == "completed"

    # ---- 6. A worse v2 arrives; profile it too ----
    await upload_inline(client, editor, json.dumps(V2), dataset_id=ds,
                        team_id=team)
    r = await client.post(f"/api/v1/datasets/{ds}/versions/2/profile-runs",
                          headers=h)
    assert r.status_code == 200
    rules = {i["rule"] for i in r.json()[0]["insights"]}
    assert "null-rate-spike" in rules and "new-categories" in rules

    # ---- 7. Review the release: schema diff + profile drift in one call ----
    r = await client.get(f"/api/v1/datasets/{ds}/versions/1/sheets/data/diff/2",
                        params={"include": "profile"}, headers=h)
    assert r.status_code == 200, r.text
    drift = r.json()["profile_drift"]
    assert drift["row_count_delta"] == 1
    amount = next(c for c in drift["columns"] if c["column"] == "amount")
    assert amount["null_percent_delta"] == 50.0
    region = next(c for c in drift["columns"] if c["column"] == "region")
    assert "LATAM" in region["added_categories"]

    # ---- 8. The saved view follows current and reflects v2 ----
    r = await client.post(f"/api/v1/datasets/{ds}/views/{view_id}/run", headers=h)
    assert r.status_code == 200
    assert r.json()["version_number"] == 2
    assert r.json()["result"]["total"] == 2  # EU rows unchanged in v2

    # ---- 9. Power move: ad-hoc SQL, result persisted and downloadable ----
    r = await client.post(
        f"/api/v1/datasets/{ds}/versions/2/sql", headers=h,
        json={"sql": "SELECT region, COUNT(*) AS n, SUM(amount) AS total "
                     "FROM data GROUP BY region ORDER BY n DESC"})
    assert r.status_code == 200, r.text
    assert r.json()["items"][0]["region"] == "LATAM"
    fname = r.json()["result_file"]
    r = await client.get(f"/api/v1/samples/{fname}/data", headers=h)
    assert r.status_code == 200

    # ---- 10. Everything landed in the audit trail ----
    entries = (await client.get("/api/v1/audit", params={"limit": 50},
                                headers=auth(admin_id))).json()["items"]
    paths = {e["path"] for e in entries}
    assert any(p.endswith("/sql") for p in paths)
    assert any("/views/" in p and p.endswith("/run") for p in paths)
    assert any(p.endswith("/profile-runs") for p in paths)
