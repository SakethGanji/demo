"""Wave 3 §16 — duplicate + missing-data explorers (read-only)."""

from __future__ import annotations

import json

from openpyxl import Workbook

from conftest import XLSX_MIME, auth, create_team_user, upload_file, upload_inline

ROWS = [
    {"region": "EU", "quarter": "Q1", "amount": 100},
    {"region": "EU", "quarter": "Q1", "amount": 100},   # exact dup of the row above
    {"region": "EU", "quarter": "Q1", "amount": 250},   # subset dup on region+quarter
    {"region": None, "quarter": "Q3", "amount": 5},
    {"region": None, "quarter": "Q3", "amount": 5},     # exact dup with a NULL key
    {"region": "US", "quarter": "Q2", "amount": None},
    {"region": None, "quarter": None, "amount": None},  # most-missing row
]


async def _dataset(client, admin_id):
    return (await upload_inline(client, admin_id, json.dumps(ROWS)))["dataset_id"]


async def test_exact_duplicates(client, admin_id):
    ds = await _dataset(client, admin_id)
    r = await client.get(f"/api/v1/datasets/{ds}/versions/1/duplicates",
                         headers=auth(admin_id))
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["exact"] is True and d["columns"] == ["region", "quarter", "amount"]
    assert d["row_count"] == 7
    assert d["group_count"] == 2 and d["duplicate_rows"] == 4
    assert d["truncated"] is False
    by_key = {g["key"]["region"]: g for g in d["groups"]}
    assert by_key["EU"]["count"] == 2
    assert by_key["EU"]["key"] == {"region": "EU", "quarter": "Q1", "amount": 100.0}
    assert len(by_key["EU"]["examples"]) == 2
    assert by_key[None]["count"] == 2 and by_key[None]["key"]["amount"] == 5.0


async def test_subset_duplicates_and_truncation(client, admin_id):
    ds = await _dataset(client, admin_id)
    h = auth(admin_id)

    r = await client.get(
        f"/api/v1/datasets/{ds}/versions/1/duplicates?columns=region,quarter",
        headers=h)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["exact"] is False and d["columns"] == ["region", "quarter"]
    assert d["group_count"] == 2 and d["duplicate_rows"] == 5
    eu = next(g for g in d["groups"] if g["key"]["region"] == "EU")
    assert eu["count"] == 3 and len(eu["examples"]) == 3

    r = await client.get(
        f"/api/v1/datasets/{ds}/versions/1/duplicates?columns=region,quarter&limit=1",
        headers=h)
    d = r.json()
    assert len(d["groups"]) == 1 and d["truncated"] is True and d["group_count"] == 2

    r = await client.get(
        f"/api/v1/datasets/{ds}/versions/1/duplicates?columns=nope", headers=h)
    assert r.status_code == 400
    assert r.json()["code"] == "unknown-column"
    assert "region" in r.json()["available"]


async def test_missing_computed_then_profile_backed(client, admin_id):
    ds = await _dataset(client, admin_id)
    h = auth(admin_id)
    url = f"/api/v1/datasets/{ds}/versions/1/missing"

    r = await client.get(url, headers=h)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["source"] == "computed" and d["profile_run_id"] is None
    assert d["row_count"] == 7
    assert [(c["column"], c["null_count"]) for c in d["columns"]] == [
        ("region", 3), ("amount", 2), ("quarter", 1)]
    assert d["columns"][0]["null_percent"] == 42.86
    assert len(d["rows_most_missing"]) == 4
    worst = d["rows_most_missing"][0]
    assert worst["null_count"] == 3
    assert worst["row"] == {"region": None, "quarter": None, "amount": None}

    # A persisted profile run becomes the column-stat source; the numbers agree.
    r = await client.post(f"/api/v1/datasets/{ds}/versions/1/profile-runs", headers=h)
    assert r.status_code == 200, r.text
    r = await client.get(url, headers=h)
    d = r.json()
    assert d["source"] == "profile_run" and d["profile_run_id"]
    assert [(c["column"], c["null_count"]) for c in d["columns"]] == [
        ("region", 3), ("amount", 2), ("quarter", 1)]


async def test_sheet_contract_and_rbac(client, admin_id, tmp_path):
    wb_path = tmp_path / "book.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "Revenue"
    ws.append(["Amount", "Region"])
    ws.append([100, "EU"])
    ws.append([100, "EU"])
    costs = wb.create_sheet("Expenses")
    costs.append(["Item", "Cost"])
    costs.append(["rent", 50])
    wb.save(wb_path)
    ds = (await upload_file(client, admin_id, wb_path, name="book.xlsx",
                            content_type=XLSX_MIME))["dataset_id"]
    h = auth(admin_id)

    for endpoint in ("duplicates", "missing"):
        r = await client.get(f"/api/v1/datasets/{ds}/versions/1/{endpoint}", headers=h)
        assert r.status_code == 400, r.text
        assert r.json()["code"] == "sheet-selection-required"

    r = await client.get(
        f"/api/v1/datasets/{ds}/versions/1/sheets/Revenue/duplicates", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["group_count"] == 1 and r.json()["sheet_name"] == "Revenue"

    r = await client.get(
        f"/api/v1/datasets/{ds}/versions/1/sheets/Expenses/missing", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["rows_most_missing"] == []

    outsider, _ = await create_team_user(client, admin_id, "viewer")
    for endpoint in ("duplicates", "missing"):
        r = await client.get(f"/api/v1/datasets/{ds}/versions/1/{endpoint}",
                             headers=auth(outsider))
        assert r.status_code == 404  # cross-team: existence hidden
