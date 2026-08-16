"""ROADMAP §1 — logical sheet identity + confirm-rename.

A sheet rename used to silently detach sheet metadata and quality rules
(both keyed on name-derived sheet_key), disarming the promotion gate for
that sheet. These tests pin the fix: identity lives in ``dataset_sheets``,
ingest links same-key sheets to the same logical sheet, and confirming a
rename re-points the identity so keyed state follows.
"""

from __future__ import annotations

from openpyxl import Workbook

from conftest import (
    DEFAULT_TEAM_ID,
    XLSX_MIME,
    auth,
    create_team_user,
    make_workbook,
    upload_file,
)

PROBLEM = "application/problem+json"


async def _upload_wb(client, admin_id, path, *, dataset_id=None):
    return await upload_file(client, admin_id, path, name="book.xlsx",
                             content_type=XLSX_MIME, dataset_id=dataset_id)


def _wb_with_costs(path, sheet_name, *, extra_col=False):
    """Revenue + one costs sheet; extra_col changes the costs schema."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Revenue"
    ws.append(["Amount", "Region"])
    ws.append([100, "EU"])
    costs = wb.create_sheet(sheet_name)
    costs.append(["Item", "Cost"] + (["Currency"] if extra_col else []))
    costs.append(["rent", 50] + (["EUR"] if extra_col else []))
    wb.save(path)


# --- identity assignment on ingest -------------------------------------------

async def test_same_key_sheets_share_logical_identity_across_versions(
    client, admin_id, tmp_path,
):
    v1, v2 = tmp_path / "v1.xlsx", tmp_path / "v2.xlsx"
    make_workbook(v1)
    make_workbook(v2, extra_revenue_row=True)
    ds = (await _upload_wb(client, admin_id, v1))["dataset_id"]
    await _upload_wb(client, admin_id, v2, dataset_id=ds)
    h = auth(admin_id)

    r = await client.get(f"/api/v1/datasets/{ds}/sheets", headers=h)
    assert r.status_code == 200, r.text
    v2_sheets = {s["name"]: s for s in r.json()["items"]}
    assert all(s["logical_sheet_id"] for s in v2_sheets.values())

    # v1's Revenue row carries the SAME logical id as v2's (same sheet_key).
    r = await client.get(f"/api/v1/datasets/{ds}/versions/1/diff/2", headers=h)
    assert r.status_code == 200
    assert r.json()["rename_candidates"] == []


# --- the headline flow: rename detaches nothing once confirmed ----------------

async def test_confirm_rename_keeps_metadata_and_rules_attached(
    client, admin_id, tmp_path,
):
    v1, v2 = tmp_path / "v1.xlsx", tmp_path / "v2.xlsx"
    make_workbook(v1, second_sheet="Expenses")
    make_workbook(v2, second_sheet="Operating Costs")
    ds = (await _upload_wb(client, admin_id, v1))["dataset_id"]
    h = auth(admin_id)

    # Keyed state on the sheet under its old name.
    r = await client.put(f"/api/v1/datasets/{ds}/sheet-metadata/Expenses",
                         headers=h, json={"grain": "one row per cost item"})
    assert r.status_code == 200, r.text
    meta = r.json()
    assert meta["logical_sheet_id"]

    r = await client.post(f"/api/v1/datasets/{ds}/rules", headers=h, json={
        "name": "cost-not-null", "scope_type": "column", "rule_type": "not_null",
        "sheet_selector": "Expenses", "column_selector": "cost",
    })
    assert r.status_code == 201, r.text
    rule = r.json()
    assert rule["logical_sheet_id"] == meta["logical_sheet_id"]
    assert rule["sheet_selector"] == "expenses"  # pinned to the live key

    await _upload_wb(client, admin_id, v2, dataset_id=ds)

    # The diff suggests the rename (identical fingerprint + rows).
    r = await client.get(f"/api/v1/datasets/{ds}/versions/1/diff/2", headers=h)
    cands = [(c["from_sheet"], c["to_sheet"]) for c in r.json()["rename_candidates"]]
    assert ("Expenses", "Operating Costs") in cands

    # Before confirmation the rule cannot find its sheet in v2 — the old bug.
    r = await client.post(f"/api/v1/datasets/{ds}/versions/2/validate", headers=h)
    assert r.status_code == 200, r.text
    by_name = {x["rule_name"]: x for x in r.json()["results"]}
    assert by_name["cost-not-null"]["status"] == "error"

    r = await client.post(f"/api/v1/datasets/{ds}/versions/2/confirm-rename",
                          headers=h,
                          json={"from_sheet": "Expenses", "to_sheet": "Operating Costs"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["logical_sheet_id"] == meta["logical_sheet_id"]
    assert body["was_candidate"] is True and body["forced"] is False
    assert body["versions_relinked"] == 1
    assert body["sheet_key"] == "operating_costs"

    # Identity is continuous: the renamed sheet carries the original logical id.
    r = await client.get(f"/api/v1/datasets/{ds}/sheets", headers=h)
    sheets = {s["name"]: s for s in r.json()["items"]}
    assert sheets["Operating Costs"]["logical_sheet_id"] == meta["logical_sheet_id"]

    # Sheet metadata followed: now keyed (and readable) under the new key.
    r = await client.get(f"/api/v1/datasets/{ds}/sheet-metadata", headers=h)
    rows = {m["sheet_key"]: m for m in r.json()["items"]}
    assert "expenses" not in rows
    assert rows["operating_costs"]["grain"] == "one row per cost item"
    assert rows["operating_costs"]["logical_sheet_id"] == meta["logical_sheet_id"]

    # The rule followed too — selector rewritten, validation finds the sheet.
    r = await client.get(f"/api/v1/datasets/{ds}/rules", headers=h)
    rule2 = {x["name"]: x for x in r.json()["items"]}["cost-not-null"]
    assert rule2["sheet_selector"] == "operating_costs"
    assert rule2["logical_sheet_id"] == meta["logical_sheet_id"]

    r = await client.post(f"/api/v1/datasets/{ds}/versions/2/validate", headers=h)
    assert r.status_code == 200, r.text
    by_name = {x["rule_name"]: x for x in r.json()["results"]}
    assert by_name["cost-not-null"]["status"] == "passed"


async def test_confirm_rename_rewrites_cross_sheet_ref_params(
    client, admin_id, tmp_path,
):
    v1, v2 = tmp_path / "v1.xlsx", tmp_path / "v2.xlsx"
    make_workbook(v1, second_sheet="Expenses")
    make_workbook(v2, second_sheet="Operating Costs")
    ds = (await _upload_wb(client, admin_id, v1))["dataset_id"]
    h = auth(admin_id)

    r = await client.post(f"/api/v1/datasets/{ds}/rules", headers=h, json={
        "name": "rev-fk", "scope_type": "cross_sheet", "rule_type": "foreign_key",
        "sheet_selector": "Revenue", "column_selector": "region",
        "parameters": {"ref_sheet": "expenses", "ref_column": "item"},
    })
    assert r.status_code == 201, r.text

    await _upload_wb(client, admin_id, v2, dataset_id=ds)
    r = await client.post(f"/api/v1/datasets/{ds}/versions/2/confirm-rename",
                          headers=h,
                          json={"from_sheet": "Expenses", "to_sheet": "Operating Costs"})
    assert r.status_code == 200, r.text

    r = await client.get(f"/api/v1/datasets/{ds}/rules", headers=h)
    fk = {x["name"]: x for x in r.json()["items"]}["rev-fk"]
    assert fk["parameters"]["ref_sheet"] == "operating_costs"


# --- candidate gating + input validation --------------------------------------

async def test_non_candidate_rename_needs_force(client, admin_id, tmp_path):
    v1, v2 = tmp_path / "v1.xlsx", tmp_path / "v2.xlsx"
    _wb_with_costs(v1, "Expenses")
    _wb_with_costs(v2, "Ops", extra_col=True)  # renamed AND schema changed
    ds = (await _upload_wb(client, admin_id, v1))["dataset_id"]
    await _upload_wb(client, admin_id, v2, dataset_id=ds)
    h = auth(admin_id)
    url = f"/api/v1/datasets/{ds}/versions/2/confirm-rename"

    r = await client.post(url, headers=h,
                          json={"from_sheet": "Expenses", "to_sheet": "Ops"})
    assert r.status_code == 400, r.text
    assert r.headers["content-type"].startswith(PROBLEM)
    assert r.json()["code"] == "rename-not-candidate"

    r = await client.post(url, headers=h, json={
        "from_sheet": "Expenses", "to_sheet": "Ops", "force": True})
    assert r.status_code == 200, r.text
    assert r.json()["forced"] is True and r.json()["was_candidate"] is False


async def test_confirm_rename_rejects_bad_pairs(client, admin_id, tmp_path):
    v1, v2 = tmp_path / "v1.xlsx", tmp_path / "v2.xlsx"
    _wb_with_costs(v1, "Expenses")
    _wb_with_costs(v2, "Ops")
    ds = (await _upload_wb(client, admin_id, v1))["dataset_id"]
    await _upload_wb(client, admin_id, v2, dataset_id=ds)
    h = auth(admin_id)
    url = f"/api/v1/datasets/{ds}/versions/2/confirm-rename"

    r = await client.post(url, headers=h,
                          json={"from_sheet": "Expenses", "to_sheet": "Nope"})
    assert r.status_code == 404

    # from_sheet still present in the target version → not a rename.
    r = await client.post(url, headers=h,
                          json={"from_sheet": "Revenue", "to_sheet": "Ops"})
    assert r.status_code == 400
    assert r.json()["code"] == "not-a-rename"


async def test_confirm_rename_conflicting_state_409(client, admin_id, tmp_path):
    v1, v2 = tmp_path / "v1.xlsx", tmp_path / "v2.xlsx"
    _wb_with_costs(v1, "Expenses")
    _wb_with_costs(v2, "Ops")
    ds = (await _upload_wb(client, admin_id, v1))["dataset_id"]
    await _upload_wb(client, admin_id, v2, dataset_id=ds)
    h = auth(admin_id)

    # The auto-created identity for "Ops" accumulates its own state first.
    r = await client.put(f"/api/v1/datasets/{ds}/sheet-metadata/Ops",
                         headers=h, json={"grain": "per op"})
    assert r.status_code == 200, r.text

    r = await client.post(f"/api/v1/datasets/{ds}/versions/2/confirm-rename",
                          headers=h,
                          json={"from_sheet": "Expenses", "to_sheet": "Ops"})
    assert r.status_code == 409, r.text
    body = r.json()
    assert body["code"] == "conflicting-sheet-state"
    assert body["attached"]["sheet_metadata"] == 1


# --- RBAC contracts -----------------------------------------------------------

async def test_confirm_rename_rbac(client, admin_id, tmp_path):
    v1, v2 = tmp_path / "v1.xlsx", tmp_path / "v2.xlsx"
    _wb_with_costs(v1, "Expenses")
    _wb_with_costs(v2, "Ops")
    ds = (await _upload_wb(client, admin_id, v1))["dataset_id"]
    await _upload_wb(client, admin_id, v2, dataset_id=ds)
    payload = {"from_sheet": "Expenses", "to_sheet": "Ops"}
    url = f"/api/v1/datasets/{ds}/versions/2/confirm-rename"

    outsider, _ = await create_team_user(client, admin_id, "editor")
    r = await client.post(url, headers=auth(outsider), json=payload)
    assert r.status_code == 404  # cross-team: existence hidden

    viewer, _ = await create_team_user(client, admin_id, "viewer",
                                       team_id=DEFAULT_TEAM_ID)
    r = await client.post(url, headers=auth(viewer), json=payload)
    assert r.status_code == 403  # in-team, but no write permission
