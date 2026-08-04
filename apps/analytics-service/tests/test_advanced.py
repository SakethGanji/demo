"""Phase 5 (advanced) — workbook reconstruction, partial ingest, artifact
reuse, copy-on-write sheet replacement, relationship joins.

Journey: partial ingest → full workbook xlsx download → COW-replace one sheet
(others reuse artifacts) → join two sheets in an aggregation.
"""

from __future__ import annotations

import io

from openpyxl import Workbook, load_workbook

from conftest import DEFAULT_TEAM_ID, auth


def _wb(path):
    wb = Workbook()
    cust = wb.active
    cust.title = "Customers"
    cust.append(["customer_id", "tier"])
    for r in ([1, "gold"], [2, "silver"], [3, "gold"]):
        cust.append(r)
    orders = wb.create_sheet("Orders")
    orders.append(["order_id", "customer_id", "total"])
    for r in ([10, 1, 100.0], [11, 2, 40.0], [12, 1, 60.0]):
        orders.append(r)
    scratch = wb.create_sheet("Scratch")
    scratch.append(["junk"])
    scratch.append(["x"])
    scratch.sheet_state = "hidden"
    wb.save(path)


def _single_sheet_csv(path):
    path.write_text("customer_id,tier\n1,platinum\n2,silver\n3,gold\n4,bronze\n")


async def test_advanced_workbook_journey(client, admin_id, tmp_path):
    h = auth(admin_id)
    wb_path = tmp_path / "crm.xlsx"
    _wb(wb_path)

    # ---- 1. Partial-workbook ingest: opt out of the Scratch sheet ----
    with open(wb_path, "rb") as f:
        r = await client.post("/api/v1/upload",
                              headers={**h, "X-Team-Id": DEFAULT_TEAM_ID},
                              files={"file": ("crm.xlsx", f, "application/octet-stream")},
                              data={"include_sheets": "Customers,Orders"})
    assert r.status_code == 200, r.text
    ds = r.json()["dataset_id"]
    sheets = (await client.get(f"/api/v1/datasets/{ds}/sheets", headers=h)).json()["items"]
    assert {s["name"] for s in sheets} == {"Customers", "Orders"}  # Scratch excluded

    # ---- 2. Reconstructed workbook download (format=xlsx, no sheet) ----
    r = await client.get(f"/api/v1/datasets/{ds}/download",
                         params={"format": "xlsx"}, headers=h)
    assert r.status_code == 200, r.text
    book = load_workbook(io.BytesIO(r.content))
    assert set(book.sheetnames) == {"Customers", "Orders"}
    assert book["Customers"].max_row == 4  # header + 3 rows

    # csv without a sheet still enforces selection on multi-sheet.
    r = await client.get(f"/api/v1/datasets/{ds}/download",
                         params={"format": "csv"}, headers=h)
    assert r.status_code == 400 and r.json()["code"] == "sheet-selection-required"

    # ---- 3. Relationship join: revenue per tier across two sheets ----
    r = await client.post("/api/v1/aggregate", headers=h, json={
        "dataset_id": ds, "sheet": "Orders",
        "join": {"sheet": "Customers", "left_on": "customer_id",
                 "right_on": "customer_id"},
        "group_by": ["tier"],
        "aggregations": [{"column": "total", "function": "sum", "alias": "revenue"}],
        "sort_by": "revenue"})
    assert r.status_code == 200, r.text
    rows = {row["tier"]: row["revenue"] for row in r.json()["data"]}
    assert rows == {"gold": 160.0, "silver": 40.0}

    # ---- 4. Copy-on-write: replace Customers, Orders reuses its artifact ----
    csv_path = tmp_path / "customers_fixed.csv"
    _single_sheet_csv(csv_path)
    with open(csv_path, "rb") as f:
        r = await client.post(f"/api/v1/datasets/{ds}/sheets/Customers/replace",
                              headers=h, files={"file": ("customers_fixed.csv", f, "text/csv")})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["version_number"] == 2
    assert body["replaced_sheet"] == "Customers" and body["reused_sheets"] == ["Orders"]
    assert body["row_count"] == 7  # 4 new customers + 3 reused orders

    # v1 vs v2: only Customers modified; Orders artifact is byte-identical.
    diff = (await client.get(f"/api/v1/datasets/{ds}/versions/1/diff/2", headers=h)).json()
    assert [m["sheet_key"] for m in diff["modified"]] == ["customers"]
    assert diff["unchanged"] == ["Orders"]

    sdiff = (await client.get(
        f"/api/v1/datasets/{ds}/versions/1/sheets/Customers/diff/2", headers=h)).json()
    assert sdiff["row_count_delta"] == 1

    # Lineage recorded the base version.
    lin = (await client.get(f"/api/v1/datasets/{ds}/lineage", headers=h)).json()
    assert any(p["relation"] == "sheet_replaced_from" and p["parent_version_number"] == 1
               for p in lin["parents"])

    # The new version actually serves the new data.
    r = await client.get(f"/api/v1/datasets/{ds}/download",
                         params={"format": "csv", "sheet": "Customers"}, headers=h)
    assert b"platinum" in r.content

    # Replacing with a multi-sheet workbook is refused.
    with open(wb_path, "rb") as f:
        r = await client.post(f"/api/v1/datasets/{ds}/sheets/Customers/replace",
                              headers=h, files={"file": ("crm.xlsx", f, "application/octet-stream")})
    assert r.status_code == 400


async def test_artifact_reuse_on_reupload(client, admin_id, tmp_path):
    """Re-uploading a workbook with one changed sheet reuses the other's artifact."""
    h = auth(admin_id)
    v1 = tmp_path / "v1.xlsx"
    _wb(v1)
    with open(v1, "rb") as f:
        r = await client.post("/api/v1/upload",
                              headers={**h, "X-Team-Id": DEFAULT_TEAM_ID},
                              files={"file": ("crm.xlsx", f, "application/octet-stream")})
    ds = r.json()["dataset_id"]

    # v2: identical workbook — every sheet checksum matches v1.
    with open(v1, "rb") as f:
        r = await client.post("/api/v1/upload", headers=h,
                              files={"file": ("crm.xlsx", f, "application/octet-stream")},
                              data={"dataset_id": ds})
    assert r.status_code == 200, r.text

    diff = (await client.get(f"/api/v1/datasets/{ds}/versions/1/diff/2", headers=h)).json()
    assert diff["modified"] == [] and diff["added"] == [] and diff["removed"] == []
    versions = (await client.get(f"/api/v1/datasets/{ds}/versions", headers=h)).json()["items"]
    assert versions[0]["manifest_checksum"] == versions[1]["manifest_checksum"]
