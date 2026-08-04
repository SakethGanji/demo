"""Phase 4 (discovery) — rich metadata, column search, facets, favorites, usage.

Journey: enrich a dataset with catalog metadata → tag sheet semantics →
find it by column name → browse facets → star it → check usage — the way a
data-catalog UI would.
"""

from __future__ import annotations

from openpyxl import Workbook

from conftest import DEFAULT_TEAM_ID, auth, rid


def _wb(path):
    wb = Workbook()
    ws = wb.active
    ws.title = "Holdings"
    ws.append(["portfolio_id", "cusip_number", "market_value"])
    ws.append([1, "037833100", 1000.5])
    ws.append([2, "17275R102", 250.0])
    wb.save(path)


async def test_discovery_journey(client, admin_id, tmp_path):
    h = auth(admin_id)
    wb_path = tmp_path / "holdings.xlsx"
    _wb(wb_path)
    with open(wb_path, "rb") as f:
        r = await client.post("/api/v1/upload",
                              headers={**h, "X-Team-Id": DEFAULT_TEAM_ID},
                              files={"file": ("holdings.xlsx", f, "application/octet-stream")})
    assert r.status_code == 200, r.text
    ds = r.json()["dataset_id"]
    marker = f"disc-{rid()}"

    # ---- 1. Enrich the dataset with catalog metadata ----
    r = await client.patch(f"/api/v1/datasets/{ds}", headers=h, json={
        "domain": marker, "source_system": "custody-core",
        "refresh_frequency": "daily", "metadata": {"regulatory": "sox"}})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["domain"] == marker and body["metadata"] == {"regulatory": "sox"}

    # ---- 2. Sheet-level semantics: grain + primary key ----
    r = await client.put(f"/api/v1/datasets/{ds}/sheet-metadata/Holdings", headers=h,
                         json={"grain": "one row per portfolio holding",
                               "primary_key_columns": ["portfolio_id", "cusip_number"]})
    assert r.status_code == 200
    assert r.json()["sheet_key"] == "holdings"  # normalized
    listed = (await client.get(f"/api/v1/datasets/{ds}/sheet-metadata", headers=h)).json()
    assert listed["items"][0]["primary_key_columns"] == ["portfolio_id", "cusip_number"]

    # ---- 3. Column search finds it (Phase 1 schemas, no file I/O) ----
    r = await client.get("/api/v1/search/columns", params={"q": "cusip"}, headers=h)
    assert r.status_code == 200
    hits = r.json()["items"]
    assert any(hit["dataset_id"] == ds and hit["normalized_name"] == "cusip_number"
               for hit in hits)

    # ---- 4. Facets + filtered listing ----
    facets = (await client.get("/api/v1/datasets/facets", headers=h)).json()
    assert facets["domain"].get(marker) == 1
    listing = (await client.get("/api/v1/datasets", params={"domain": marker},
                                headers=h)).json()
    assert listing["total"] == 1 and listing["items"][0]["id"] == ds

    # ---- 5. Favorites: star, filter, unstar ----
    assert (await client.put(f"/api/v1/datasets/{ds}/favorite", headers=h)).status_code == 204
    listing = (await client.get("/api/v1/datasets", params={"favorites": "true"},
                                headers=h)).json()
    assert any(d["id"] == ds and d["is_favorite"] for d in listing["items"])
    assert (await client.delete(f"/api/v1/datasets/{ds}/favorite", headers=h)).status_code == 204
    listing = (await client.get("/api/v1/datasets", params={"favorites": "true"},
                                headers=h)).json()
    assert all(d["id"] != ds for d in listing["items"])

    # ---- 6. Deprecation hides from default browse when asked ----
    r = await client.patch(f"/api/v1/datasets/{ds}", headers=h,
                           json={"deprecated": True, "deprecation_reason": "superseded"})
    assert r.status_code == 200 and r.json()["deprecated"] is True
    listing = (await client.get("/api/v1/datasets",
                                params={"domain": marker, "include_deprecated": "false"},
                                headers=h)).json()
    assert listing["total"] == 0

    # ---- 7. Usage metrics reflect the audit trail ----
    await client.get(f"/api/v1/datasets/{ds}/download",
                     params={"format": "csv", "sheet": "Holdings"}, headers=h)
    usage = (await client.get(f"/api/v1/datasets/{ds}/usage", headers=h)).json()
    assert usage["downloads"] >= 1 and usage["writes"] >= 1
    assert usage["last_activity_at"]


async def test_discovery_rbac(client, admin_id, tmp_path):
    """Column search is team-scoped: outsiders never see other teams' columns."""
    h = auth(admin_id)
    wb_path = tmp_path / "wb.xlsx"
    _wb(wb_path)
    with open(wb_path, "rb") as f:
        r = await client.post("/api/v1/upload",
                              headers={**h, "X-Team-Id": DEFAULT_TEAM_ID},
                              files={"file": ("h.xlsx", f, "application/octet-stream")})
    ds = r.json()["dataset_id"]

    team = (await client.post("/api/v1/teams", headers=h, json={"name": f"d-{rid()}"})).json()
    outsider = (await client.post("/api/v1/auth/users", headers=h,
                                  json={"email": f"o-{rid()}@bank.com", "name": "O",
                                        "team_id": team["id"]})).json()["id"]
    r = await client.get("/api/v1/search/columns", params={"q": "cusip"},
                         headers=auth(outsider))
    assert r.status_code == 200
    assert all(hit["dataset_id"] != ds for hit in r.json()["items"])
    assert (await client.get(f"/api/v1/datasets/{ds}/usage",
                             headers=auth(outsider))).status_code == 404
