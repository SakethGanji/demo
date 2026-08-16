"""Phase 4 (discovery) — rich metadata, column search, facets, favorites, usage.

Journey: enrich a dataset with catalog metadata → tag sheet semantics →
find it by column name → browse facets → star it → check usage — the way a
data-catalog UI would.
"""

from __future__ import annotations

from conftest import (
    DEFAULT_TEAM_ID, auth, create_team_user, make_holdings_workbook, rid,
    upload_file, upload_inline,
)


async def test_discovery_journey(client, admin_id, tmp_path):
    h = auth(admin_id)
    wb_path = tmp_path / "holdings.xlsx"
    make_holdings_workbook(wb_path)
    ds = (await upload_file(client, admin_id, wb_path))["dataset_id"]
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
    wb_path = tmp_path / "wb.xlsx"
    make_holdings_workbook(wb_path)
    ds = (await upload_file(client, admin_id, wb_path, name="h.xlsx"))["dataset_id"]

    outsider, _ = await create_team_user(client, admin_id, "viewer")
    r = await client.get("/api/v1/search/columns", params={"q": "cusip"},
                         headers=auth(outsider))
    assert r.status_code == 200
    assert all(hit["dataset_id"] != ds for hit in r.json()["items"])
    assert (await client.get(f"/api/v1/datasets/{ds}/usage",
                             headers=auth(outsider))).status_code == 404


async def test_dataset_search_scoped_by_team(client, admin_id, tmp_path):
    h = auth(admin_id)
    marker = f"searchable-{rid()}"
    wb_path = tmp_path / f"{marker}.xlsx"
    make_holdings_workbook(wb_path)
    ds = (await upload_file(client, admin_id, wb_path))["dataset_id"]
    r = await client.patch(f"/api/v1/datasets/{ds}", headers=h,
                           json={"description": "custody holdings extract"})
    assert r.status_code == 200

    # Matches by name...
    hits = (await client.get("/api/v1/datasets/search", params={"q": marker},
                             headers=h)).json()
    assert hits["total"] == 1 and hits["items"][0]["id"] == ds
    # ...and by description.
    hits = (await client.get("/api/v1/datasets/search",
                             params={"q": "custody holdings"}, headers=h)).json()
    assert any(d["id"] == ds for d in hits["items"])

    # Outsiders never see it, even with the exact name.
    outsider, _ = await create_team_user(client, admin_id, "viewer")
    hits = (await client.get("/api/v1/datasets/search", params={"q": marker},
                             headers=auth(outsider))).json()
    assert hits["total"] == 0

    # Empty queries are rejected, not treated as match-all.
    r = await client.get("/api/v1/datasets/search", params={"q": ""}, headers=h)
    assert r.status_code == 422


async def test_facets_across_multiple_domains(client, admin_id, tmp_path):
    h = auth(admin_id)
    domains = {}
    for i in range(2):
        wb_path = tmp_path / f"d{i}.xlsx"
        make_holdings_workbook(wb_path)
        ds = (await upload_file(client, admin_id, wb_path,
                                name=f"d{i}.xlsx"))["dataset_id"]
        domain = f"dom-{i}-{rid()}"
        domains[domain] = ds
        r = await client.patch(f"/api/v1/datasets/{ds}", headers=h,
                               json={"domain": domain})
        assert r.status_code == 200

    facets = (await client.get("/api/v1/datasets/facets", headers=h)).json()
    for domain in domains:
        assert facets["domain"].get(domain) == 1
    # Both land in the classification facet under the default class.
    assert facets["classification"].get("internal", 0) >= 2


async def test_favorite_on_cross_team_dataset_hidden(client, admin_id, tmp_path):
    wb_path = tmp_path / "wb.xlsx"
    make_holdings_workbook(wb_path)
    ds = (await upload_file(client, admin_id, wb_path))["dataset_id"]
    outsider, _ = await create_team_user(client, admin_id, "editor")
    assert (await client.put(f"/api/v1/datasets/{ds}/favorite",
                             headers=auth(outsider))).status_code == 404
    assert (await client.request("DELETE", f"/api/v1/datasets/{ds}/favorite",
                                 headers=auth(outsider))).status_code == 404


# ---------------------------------------------------------------------------
# Sheet metadata: PUT replaces the whole record, PATCH merges
# ---------------------------------------------------------------------------


async def _holdings(client, admin_id, tmp_path, name="holdings.xlsx"):
    wb_path = tmp_path / name
    make_holdings_workbook(wb_path)
    return (await upload_file(client, admin_id, wb_path, name=name))["dataset_id"]


async def _stored_sheet_meta(client, headers, ds, sheet_key="holdings"):
    listed = (await client.get(f"/api/v1/datasets/{ds}/sheet-metadata",
                               headers=headers)).json()
    return {m["sheet_key"]: m for m in listed["items"]}[sheet_key]


FULL_SHEET_META = {"grain": "one row per portfolio holding",
                   "primary_key_columns": ["portfolio_id", "cusip_number"],
                   "description": "Custody holdings, end of day"}


async def test_sheet_metadata_put_clears_omitted_fields(client, admin_id, tmp_path):
    """PUT is a whole-record REPLACE — documented, and locked in here.

    Omitting a field is the only way to blank it, so this is deliberate
    behaviour, not the data-loss defect; PATCH is the non-destructive path.
    """
    ds = await _holdings(client, admin_id, tmp_path)
    h = auth(admin_id)
    url = f"/api/v1/datasets/{ds}/sheet-metadata/Holdings"

    assert (await client.put(url, headers=h, json=FULL_SHEET_META)).status_code == 200

    r = await client.put(url, headers=h, json={"grain": "one row per holding"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["grain"] == "one row per holding"
    assert body["primary_key_columns"] is None and body["description"] is None
    stored = await _stored_sheet_meta(client, h, ds)
    assert stored["primary_key_columns"] is None and stored["description"] is None


async def test_sheet_metadata_patch_merges_and_clears(client, admin_id, tmp_path):
    ds = await _holdings(client, admin_id, tmp_path)
    h = auth(admin_id)
    url = f"/api/v1/datasets/{ds}/sheet-metadata/Holdings"
    assert (await client.put(url, headers=h, json=FULL_SHEET_META)).status_code == 200

    # Omitted fields survive — the whole point of PATCH.
    r = await client.patch(url, headers=h, json={"grain": "one row per lot"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["grain"] == "one row per lot"
    assert body["primary_key_columns"] == ["portfolio_id", "cusip_number"]
    assert body["description"] == "Custody holdings, end of day"

    # An EXPLICIT null still clears — absent and null are different requests.
    r = await client.patch(url, headers=h, json={"description": None})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["description"] is None
    assert body["grain"] == "one row per lot"  # untouched
    assert body["primary_key_columns"] == ["portfolio_id", "cusip_number"]

    # Same call in one request: clear one field, set another, leave a third.
    r = await client.patch(url, headers=h, json={"primary_key_columns": None,
                                                 "description": "Rebuilt"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["primary_key_columns"] is None and body["description"] == "Rebuilt"
    assert body["grain"] == "one row per lot"

    # An empty body changes nothing.
    r = await client.patch(url, headers=h, json={})
    assert r.status_code == 200, r.text
    assert r.json()["grain"] == "one row per lot"

    stored = await _stored_sheet_meta(client, h, ds)
    assert stored["grain"] == "one row per lot"
    assert stored["description"] == "Rebuilt" and stored["primary_key_columns"] is None


async def test_sheet_metadata_patch_requires_existing_record(client, admin_id, tmp_path):
    """PATCH updates; it never creates. PUT is how a record comes into being."""
    ds = await _holdings(client, admin_id, tmp_path)
    h = auth(admin_id)

    r = await client.patch(f"/api/v1/datasets/{ds}/sheet-metadata/Holdings",
                           headers=h, json={"grain": "one row per holding"})
    assert r.status_code == 404, r.text
    assert r.json()["code"] == "not_found"
    # ...and nothing was created as a side effect.
    listed = (await client.get(f"/api/v1/datasets/{ds}/sheet-metadata",
                               headers=h)).json()
    assert listed["total"] == 0

    # Unknown sheet key: same 404, no existence leak.
    r = await client.patch(f"/api/v1/datasets/{ds}/sheet-metadata/nope",
                           headers=h, json={"grain": "x"})
    assert r.status_code == 404, r.text


async def test_sheet_metadata_patch_rbac_matches_put(client, admin_id, tmp_path):
    ds = await _holdings(client, admin_id, tmp_path)
    url = f"/api/v1/datasets/{ds}/sheet-metadata/Holdings"
    assert (await client.put(url, headers=auth(admin_id),
                             json=FULL_SHEET_META)).status_code == 200

    # In-team viewer: no write permission, for either verb.
    viewer, _ = await create_team_user(client, admin_id, "viewer",
                                       team_id=DEFAULT_TEAM_ID)
    put = await client.put(url, headers=auth(viewer), json={"grain": "x"})
    patch = await client.patch(url, headers=auth(viewer), json={"grain": "x"})
    assert put.status_code == 403
    assert patch.status_code == put.status_code, patch.text

    # Cross-team editor: existence stays hidden, for either verb.
    outsider, _ = await create_team_user(client, admin_id, "editor")
    put = await client.put(url, headers=auth(outsider), json={"grain": "x"})
    patch = await client.patch(url, headers=auth(outsider), json={"grain": "x"})
    assert put.status_code == 404
    assert patch.status_code == put.status_code, patch.text

    # Neither rejected call touched the record.
    stored = await _stored_sheet_meta(client, auth(admin_id), ds)
    assert stored["grain"] == FULL_SHEET_META["grain"]


async def test_sheet_metadata_on_hidden_sheet(client, admin_id, tmp_path):
    from conftest import make_workbook
    h = auth(admin_id)
    wb_path = tmp_path / "wb.xlsx"
    make_workbook(wb_path)  # includes hidden 'Secrets'
    ds = (await upload_file(client, admin_id, wb_path))["dataset_id"]

    r = await client.put(f"/api/v1/datasets/{ds}/sheet-metadata/Secrets", headers=h,
                         json={"grain": "one row per secret",
                               "primary_key_columns": ["K"]})
    assert r.status_code == 200, r.text
    assert r.json()["sheet_key"] == "secrets"
    listed = (await client.get(f"/api/v1/datasets/{ds}/sheet-metadata",
                               headers=h)).json()
    by_key = {m["sheet_key"]: m for m in listed["items"]}
    assert by_key["secrets"]["primary_key_columns"] == ["K"]


async def test_dataset_patch_clears_a_field_with_an_explicit_null(client, admin_id):
    """PATCH /datasets/{id} has MERGE semantics: an omitted field survives, an
    explicit null clears. It used to drop nulls before building the UPDATE, so
    a field could be set and changed but never blanked.

    Same contract and same technique as PATCH sheet-metadata / column-metadata.
    """
    h = auth(admin_id)
    ds = (await upload_inline(client, admin_id, '[{"a": 1}]'))["dataset_id"]

    full = {"description": "Nightly custody extract", "domain": "custody",
            "source_system": "custody-core", "refresh_frequency": "daily",
            "deprecated": True, "deprecation_reason": "superseded by v2",
            "metadata": {"regulatory": "sox"}}
    r = await client.patch(f"/api/v1/datasets/{ds}", headers=h, json=full)
    assert r.status_code == 200, r.text

    # Omitted fields survive.
    r = await client.patch(f"/api/v1/datasets/{ds}", headers=h,
                           json={"refresh_frequency": "hourly"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["refresh_frequency"] == "hourly"
    assert body["description"] == "Nightly custody extract"
    assert body["domain"] == "custody" and body["source_system"] == "custody-core"
    assert body["deprecation_reason"] == "superseded by v2"

    # An EXPLICIT null clears — this is what the old dict-comprehension dropped.
    r = await client.patch(f"/api/v1/datasets/{ds}", headers=h,
                           json={"description": None, "domain": None})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["description"] is None and body["domain"] is None
    assert body["source_system"] == "custody-core"  # untouched

    # Clear and set in one request.
    r = await client.patch(f"/api/v1/datasets/{ds}", headers=h, json={
        "deprecation_reason": None, "deprecated": False})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["deprecation_reason"] is None and body["deprecated"] is False

    # Empty body: no-op returning the record, not an error — matches the
    # sheet/column PATCHes, and doubles as the read-back.
    r = await client.patch(f"/api/v1/datasets/{ds}", headers=h, json={})
    assert r.status_code == 200, r.text
    stored = r.json()
    assert stored["description"] is None and stored["domain"] is None
    assert stored["deprecation_reason"] is None
    assert stored["source_system"] == "custody-core"
    assert stored["refresh_frequency"] == "hourly"
    assert stored["metadata"] == {"regulatory": "sox"}

    # Independently visible in the catalog listing, so it really is persisted.
    listing = (await client.get("/api/v1/datasets", headers=h)).json()
    entry = next(d for d in listing["items"] if d["id"] == ds)
    assert entry["description"] is None and entry["domain"] is None
    assert entry["source_system"] == "custody-core"


async def test_dataset_patch_refuses_to_null_a_not_null_column(client, admin_id):
    """`name`, `classification`, `deprecated` and `metadata` back NOT NULL
    columns. Honouring an explicit null there would be a constraint violation
    surfacing as a 500, so the request model rejects it up front."""
    h = auth(admin_id)
    ds = (await upload_inline(client, admin_id, '[{"a": 1}]'))["dataset_id"]

    for field in ("name", "classification", "deprecated", "metadata"):
        r = await client.patch(f"/api/v1/datasets/{ds}", headers=h, json={field: None})
        assert r.status_code == 422, f"{field}: {r.text}"
        assert field in r.text

    # Nothing was written by any of the rejected calls.
    stored = (await client.patch(f"/api/v1/datasets/{ds}", headers=h, json={})).json()
    assert stored["name"] and stored["classification"] == "internal"
    assert stored["deprecated"] is False and stored["metadata"] == {}


async def test_dataset_patch_on_a_missing_dataset_is_404(client, admin_id):
    """Including the empty-body no-op path, which returns the record — there is
    no record to return."""
    h = auth(admin_id)
    missing = "11111111-1111-1111-1111-111111111111"
    for body in ({"domain": "x"}, {}):
        r = await client.patch(f"/api/v1/datasets/{missing}", headers=h, json=body)
        assert r.status_code == 404, r.text
