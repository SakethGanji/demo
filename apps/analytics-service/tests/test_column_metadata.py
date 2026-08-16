"""Wave 3 §15 — column-level data dictionary.

Entries are keyed on the logical sheet identity + NORMALIZED column name,
so they survive confirmed sheet renames untouched; state accumulated on a
spurious rename-created identity trips the existing 409 conflict guard.
"""

from __future__ import annotations

import json

from openpyxl import Workbook

from conftest import (
    DEFAULT_TEAM_ID,
    XLSX_MIME,
    auth,
    create_team_user,
    upload_file,
    upload_inline,
)

ROWS = [{"region": "EU", "amount": 100.0}, {"region": "US", "amount": 50.0}]


async def _dataset(client, admin_id):
    """Single-sheet inline dataset — sheet 'data', columns region/amount."""
    return (await upload_inline(client, admin_id, json.dumps(ROWS)))["dataset_id"]


def _wb_with_costs(path, sheet_name):
    wb = Workbook()
    ws = wb.active
    ws.title = "Revenue"
    ws.append(["Amount", "Region"])
    ws.append([100, "EU"])
    costs = wb.create_sheet(sheet_name)
    costs.append(["Item", "Cost"])
    costs.append(["rent", 50])
    wb.save(path)


async def _upload_wb(client, admin_id, path, *, dataset_id=None):
    return await upload_file(client, admin_id, path, name="book.xlsx",
                             content_type=XLSX_MIME, dataset_id=dataset_id)


async def test_column_metadata_crud(client, admin_id):
    ds = await _dataset(client, admin_id)
    h = auth(admin_id)
    base = f"/api/v1/datasets/{ds}/sheet-metadata/data/columns"

    r = await client.put(f"{base}/region", headers=h, json={
        "business_name": "Sales region", "semantic_type": "country_group",
        "sensitivity": "public", "allowed_values": ["EU", "US"]})
    assert r.status_code == 200, r.text
    entry = r.json()
    assert entry["column_name"] == "region" and entry["sheet_key"] == "data"
    assert entry["allowed_values"] == ["EU", "US"]

    # Upsert overwrites in place.
    r = await client.put(f"{base}/region", headers=h,
                         json={"business_name": "Region (ISO)"})
    assert r.status_code == 200, r.text
    assert r.json()["business_name"] == "Region (ISO)"
    assert r.json()["allowed_values"] is None

    r = await client.put(f"{base}/amount", headers=h, json={
        "business_name": "Order amount", "unit": "USD",
        "description": "Gross order value before refunds"})
    assert r.status_code == 200, r.text

    r = await client.get(base, headers=h)
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2
    assert [e["column_name"] for e in body["items"]] == ["amount", "region"]
    assert body["items"][1]["business_name"] == "Region (ISO)"

    r = await client.delete(f"{base}/region", headers=h)
    assert r.status_code == 204
    assert (await client.delete(f"{base}/region", headers=h)).status_code == 404
    assert (await client.get(base, headers=h)).json()["total"] == 1


async def test_unknown_sheet_and_column(client, admin_id):
    ds = await _dataset(client, admin_id)
    h = auth(admin_id)

    r = await client.put(
        f"/api/v1/datasets/{ds}/sheet-metadata/nope/columns/region",
        headers=h, json={"business_name": "x"})
    assert r.status_code == 404

    r = await client.put(
        f"/api/v1/datasets/{ds}/sheet-metadata/data/columns/nope",
        headers=h, json={"business_name": "x"})
    assert r.status_code == 400, r.text
    body = r.json()
    assert body["code"] == "unknown-column"
    assert set(body["available"]) == {"region", "amount"}


async def test_column_metadata_rbac(client, admin_id):
    ds = await _dataset(client, admin_id)
    base = f"/api/v1/datasets/{ds}/sheet-metadata/data/columns"

    outsider, _ = await create_team_user(client, admin_id, "editor")
    r = await client.get(base, headers=auth(outsider))
    assert r.status_code == 404  # cross-team: existence hidden

    viewer, _ = await create_team_user(client, admin_id, "viewer",
                                       team_id=DEFAULT_TEAM_ID)
    r = await client.put(f"{base}/region", headers=auth(viewer),
                         json={"business_name": "x"})
    assert r.status_code == 403  # in-team, but no write permission
    assert (await client.get(base, headers=auth(viewer))).status_code == 200


# ---------------------------------------------------------------------------
# PUT replaces the whole entry; PATCH merges into it
# ---------------------------------------------------------------------------

FULL_ENTRY = {"business_name": "Sales region", "description": "ISO region group",
              "semantic_type": "country_group", "unit": "n/a",
              "sensitivity": "public", "allowed_values": ["EU", "US"]}


async def _stored_entry(client, headers, ds, column="region", sheet="data"):
    listed = (await client.get(
        f"/api/v1/datasets/{ds}/sheet-metadata/{sheet}/columns",
        headers=headers)).json()
    return {e["column_name"]: e for e in listed["items"]}[column]


async def test_column_metadata_put_clears_omitted_fields(client, admin_id):
    """PUT is a whole-record REPLACE — documented, and locked in here.

    Omitting a field is the only way to blank it; PATCH is the additive path.
    """
    ds = await _dataset(client, admin_id)
    h = auth(admin_id)
    url = f"/api/v1/datasets/{ds}/sheet-metadata/data/columns/region"

    assert (await client.put(url, headers=h, json=FULL_ENTRY)).status_code == 200

    r = await client.put(url, headers=h, json={"business_name": "Region (ISO)"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["business_name"] == "Region (ISO)"
    for field in ("description", "semantic_type", "unit", "sensitivity",
                  "allowed_values"):
        assert body[field] is None, field
    stored = await _stored_entry(client, h, ds)
    assert stored["semantic_type"] is None and stored["allowed_values"] is None


async def test_column_metadata_patch_merges_and_clears(client, admin_id):
    ds = await _dataset(client, admin_id)
    h = auth(admin_id)
    url = f"/api/v1/datasets/{ds}/sheet-metadata/data/columns/region"
    assert (await client.put(url, headers=h, json=FULL_ENTRY)).status_code == 200

    # Omitted fields survive.
    r = await client.patch(url, headers=h, json={"unit": "EUR"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["unit"] == "EUR"
    assert body["business_name"] == "Sales region"
    assert body["description"] == "ISO region group"
    assert body["semantic_type"] == "country_group"
    assert body["sensitivity"] == "public"
    assert body["allowed_values"] == ["EU", "US"]

    # An EXPLICIT null clears — including the jsonb column.
    r = await client.patch(url, headers=h, json={"allowed_values": None})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["allowed_values"] is None
    assert body["business_name"] == "Sales region" and body["unit"] == "EUR"

    # Clear and set in one request.
    r = await client.patch(url, headers=h, json={"description": None,
                                                 "semantic_type": "region_code"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["description"] is None and body["semantic_type"] == "region_code"
    assert body["business_name"] == "Sales region"

    # Empty body: no-op.
    assert (await client.patch(url, headers=h, json={})).status_code == 200

    stored = await _stored_entry(client, h, ds)
    assert stored["business_name"] == "Sales region"
    assert stored["semantic_type"] == "region_code" and stored["unit"] == "EUR"
    assert stored["description"] is None and stored["allowed_values"] is None


async def test_column_metadata_patch_requires_existing_entry(client, admin_id):
    """PATCH updates; it never creates. Sheet/column resolution matches PUT."""
    ds = await _dataset(client, admin_id)
    h = auth(admin_id)
    base = f"/api/v1/datasets/{ds}/sheet-metadata/data/columns"

    r = await client.patch(f"{base}/region", headers=h,
                           json={"business_name": "Sales region"})
    assert r.status_code == 404, r.text
    assert r.json()["code"] == "not_found"
    assert (await client.get(base, headers=h)).json()["total"] == 0

    # Same error shapes as PUT for an unknown sheet / unknown column.
    r = await client.patch(
        f"/api/v1/datasets/{ds}/sheet-metadata/nope/columns/region",
        headers=h, json={"business_name": "x"})
    assert r.status_code == 404, r.text

    r = await client.patch(f"{base}/nope", headers=h, json={"business_name": "x"})
    assert r.status_code == 400, r.text
    assert r.json()["code"] == "unknown-column"


async def test_column_metadata_patch_rbac_matches_put(client, admin_id):
    ds = await _dataset(client, admin_id)
    url = f"/api/v1/datasets/{ds}/sheet-metadata/data/columns/region"
    assert (await client.put(url, headers=auth(admin_id),
                             json=FULL_ENTRY)).status_code == 200

    viewer, _ = await create_team_user(client, admin_id, "viewer",
                                       team_id=DEFAULT_TEAM_ID)
    put = await client.put(url, headers=auth(viewer), json={"business_name": "x"})
    patch = await client.patch(url, headers=auth(viewer), json={"business_name": "x"})
    assert put.status_code == 403
    assert patch.status_code == put.status_code, patch.text

    outsider, _ = await create_team_user(client, admin_id, "editor")
    put = await client.put(url, headers=auth(outsider), json={"business_name": "x"})
    patch = await client.patch(url, headers=auth(outsider), json={"business_name": "x"})
    assert put.status_code == 404  # cross-team: existence hidden
    assert patch.status_code == put.status_code, patch.text

    stored = await _stored_entry(client, auth(admin_id), ds)
    assert stored["business_name"] == "Sales region"


async def test_dictionary_survives_confirmed_rename(client, admin_id, tmp_path):
    v1, v2 = tmp_path / "v1.xlsx", tmp_path / "v2.xlsx"
    _wb_with_costs(v1, "Expenses")
    _wb_with_costs(v2, "Ops")
    ds = (await _upload_wb(client, admin_id, v1))["dataset_id"]
    h = auth(admin_id)

    r = await client.put(
        f"/api/v1/datasets/{ds}/sheet-metadata/Expenses/columns/cost",
        headers=h, json={"business_name": "Monthly cost", "unit": "EUR"})
    assert r.status_code == 200, r.text

    await _upload_wb(client, admin_id, v2, dataset_id=ds)
    r = await client.post(f"/api/v1/datasets/{ds}/versions/2/confirm-rename",
                          headers=h,
                          json={"from_sheet": "Expenses", "to_sheet": "Ops"})
    assert r.status_code == 200, r.text

    # Keyed on logical_sheet_id → the entry follows the surviving identity.
    r = await client.get(f"/api/v1/datasets/{ds}/sheet-metadata/Ops/columns",
                         headers=h)
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["business_name"] == "Monthly cost"
    assert body["items"][0]["sheet_key"] == "ops"


# ---------------------------------------------------------------------------
# One identity for a column across GET / PUT / PATCH / DELETE
# ---------------------------------------------------------------------------

def _wb_with_spaced_header(path, headers):
    wb = Workbook()
    ws = wb.active
    ws.title = "Accounts"
    ws.append(headers)
    ws.append(["Acme", "EU"][: len(headers)])
    wb.save(path)


async def test_every_verb_agrees_on_a_non_trivial_column_name(client, admin_id, tmp_path):
    """``Business Name`` and ``business_name`` are the same entry, everywhere.

    Entries are stored under the NORMALIZED name, and PUT/PATCH/GET all map the
    path segment through the schema before touching the repo. DELETE did not —
    it passed the raw path segment straight through — so a column whose URL
    form differs from its stored form could be written, listed, and patched,
    but never deleted. The existing coverage only ever deleted ``region``,
    which normalizes to itself, so the split was invisible.
    """
    p = tmp_path / "accounts.xlsx"
    _wb_with_spaced_header(p, ["Business Name", "Region"])
    ds = (await _upload_wb(client, admin_id, p))["dataset_id"]
    h = auth(admin_id)
    base = f"/api/v1/datasets/{ds}/sheet-metadata/Accounts/columns"

    # Write under the PHYSICAL name; the entry is keyed on the normalized one.
    r = await client.put(f"{base}/Business Name", headers=h,
                         json={"business_name": "Legal entity", "unit": "n/a"})
    assert r.status_code == 200, r.text
    assert r.json()["column_name"] == "business_name"

    listed = await _stored_entry(client, h, ds, column="business_name",
                                 sheet="Accounts")
    assert listed["business_name"] == "Legal entity"

    # PATCH addresses the same entry through either spelling.
    r = await client.patch(f"{base}/Business Name", headers=h,
                           json={"description": "as registered"})
    assert r.status_code == 200, r.text
    assert r.json()["business_name"] == "Legal entity"

    r = await client.patch(f"{base}/business_name", headers=h, json={"unit": None})
    assert r.status_code == 200, r.text
    assert r.json()["description"] == "as registered" and r.json()["unit"] is None

    # Still exactly one entry — neither spelling created a second row.
    assert (await client.get(base, headers=h)).json()["total"] == 1

    # ...and DELETE resolves it the same way. This was a 404.
    r = await client.delete(f"{base}/Business Name", headers=h)
    assert r.status_code == 204, r.text
    assert (await client.get(base, headers=h)).json()["total"] == 0
    assert (await client.delete(f"{base}/Business Name", headers=h)).status_code == 404


async def test_delete_still_reaches_an_entry_whose_column_is_gone(client, admin_id, tmp_path):
    """Normalizing through the CURRENT schema must not strand old entries.

    Documentation outlives the column it describes — that is exactly when you
    want to delete it. So the delete path resolves through the schema when it
    can and falls back to the name as given when the column is no longer there.
    """
    v1, v2 = tmp_path / "v1.xlsx", tmp_path / "v2.xlsx"
    _wb_with_spaced_header(v1, ["Business Name", "Region"])
    _wb_with_spaced_header(v2, ["Region"])
    ds = (await _upload_wb(client, admin_id, v1))["dataset_id"]
    h = auth(admin_id)
    base = f"/api/v1/datasets/{ds}/sheet-metadata/Accounts/columns"

    assert (await client.put(f"{base}/Business Name", headers=h,
                             json={"business_name": "Legal entity"})).status_code == 200

    await _upload_wb(client, admin_id, v2, dataset_id=ds)

    # The column is gone from v2, but the entry is still listed...
    listed = (await client.get(base, headers=h)).json()
    assert [e["column_name"] for e in listed["items"]] == ["business_name"]
    # ...and still deletable under the name GET reports.
    assert (await client.delete(f"{base}/business_name", headers=h)).status_code == 204
    assert (await client.get(base, headers=h)).json()["total"] == 0


async def test_rename_conflicts_on_spurious_column_metadata(client, admin_id, tmp_path):
    v1, v2 = tmp_path / "v1.xlsx", tmp_path / "v2.xlsx"
    _wb_with_costs(v1, "Expenses")
    _wb_with_costs(v2, "Ops")
    ds = (await _upload_wb(client, admin_id, v1))["dataset_id"]
    await _upload_wb(client, admin_id, v2, dataset_id=ds)
    h = auth(admin_id)

    # The auto-created "Ops" identity accumulates its own dictionary entry.
    r = await client.put(
        f"/api/v1/datasets/{ds}/sheet-metadata/Ops/columns/cost",
        headers=h, json={"business_name": "Ops cost"})
    assert r.status_code == 200, r.text

    r = await client.post(f"/api/v1/datasets/{ds}/versions/2/confirm-rename",
                          headers=h,
                          json={"from_sheet": "Expenses", "to_sheet": "Ops"})
    assert r.status_code == 409, r.text
    body = r.json()
    assert body["code"] == "conflicting-sheet-state"
    assert body["attached"]["column_metadata"] == 1
