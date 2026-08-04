"""Phase 2 (trust) — quality rules, validation runs, promotion gates.

One UI-ordered journey: define rules → validate → inspect failures → see the
promotion gate block → fix the data with a new version → validate clean →
promote through the gate.
"""

from __future__ import annotations

from openpyxl import Workbook

from conftest import DEFAULT_TEAM_ID, auth

PROBLEM = "application/problem+json"


def _orders_workbook(path, *, clean: bool):
    """Customers + Orders. The dirty variant has a NULL id, a dup id, and an orphan FK."""
    wb = Workbook()
    cust = wb.active
    cust.title = "Customers"
    cust.append(["customer_id", "tier"])
    rows = [[1, "gold"], [2, "silver"], [3, "gold"]]
    if not clean:
        rows += [[None, "bronze"], [2, "copper"]]  # NULL id + duplicate id
    for r in rows:
        cust.append(r)
    orders = wb.create_sheet("Orders")
    orders.append(["order_id", "customer_id", "total"])
    order_rows = [[10, 1, 99.5], [11, 2, 15.0]]
    if not clean:
        order_rows.append([12, 999, 5.0])  # orphan customer_id
    for r in order_rows:
        orders.append(r)
    wb.save(path)


async def _upload(client, admin_id, path, dataset_id=None):
    data = {"dataset_id": dataset_id} if dataset_id else {}
    with open(path, "rb") as f:
        r = await client.post("/api/v1/upload",
                              headers={**auth(admin_id), "X-Team-Id": DEFAULT_TEAM_ID},
                              files={"file": ("orders.xlsx", f, "application/octet-stream")},
                              data=data)
    assert r.status_code == 200, r.text
    return r.json()["dataset_id"]


async def test_quality_journey(client, admin_id, tmp_path):
    h = auth(admin_id)
    dirty = tmp_path / "orders_v1.xlsx"
    clean = tmp_path / "orders_v2.xlsx"
    _orders_workbook(dirty, clean=False)
    _orders_workbook(clean, clean=True)
    ds = await _upload(client, admin_id, dirty)

    # ---- 1. Define the contract ----
    rules = [
        {"name": "customers-sheet-required", "rule_type": "sheet_exists",
         "sheet_selector": "customers"},
        {"name": "customer-id-not-null", "rule_type": "not_null",
         "sheet_selector": "customers", "column_selector": "customer_id"},
        {"name": "customer-id-unique", "rule_type": "unique",
         "sheet_selector": "customers", "column_selector": "customer_id"},
        {"name": "tier-accepted", "rule_type": "accepted_values",
         "sheet_selector": "customers", "column_selector": "tier",
         "parameters": {"values": ["gold", "silver", "bronze", "copper"]}},
        {"name": "orders-min-rows", "rule_type": "row_count_min",
         "sheet_selector": "orders", "parameters": {"min": 1}},
        {"name": "order-total-positive", "rule_type": "range",
         "sheet_selector": "orders", "column_selector": "total",
         "parameters": {"min": 0}, "severity": "warning"},
        {"name": "orders-customer-fk", "rule_type": "foreign_key",
         "sheet_selector": "orders", "column_selector": "customer_id",
         "parameters": {"ref_sheet": "customers", "ref_column": "customer_id"}},
    ]
    rule_ids = {}
    for spec in rules:
        r = await client.post(f"/api/v1/datasets/{ds}/rules", headers=h, json=spec)
        assert r.status_code == 201, r.text
        rule_ids[spec["name"]] = r.json()["id"]

    listing = (await client.get(f"/api/v1/datasets/{ds}/rules", headers=h)).json()
    assert listing["total"] == 7

    # Bad rule definitions are rejected up front.
    r = await client.post(f"/api/v1/datasets/{ds}/rules", headers=h, json={
        "name": "bad", "rule_type": "not_null", "sheet_selector": "customers"})
    assert r.status_code == 422  # missing column_selector

    # ---- 2. Validate the dirty version ----
    r = await client.post(f"/api/v1/datasets/{ds}/versions/1/validate", headers=h)
    assert r.status_code == 200, r.text
    run = r.json()
    assert run["status"] == "completed" and run["rules_total"] == 7
    by_name = {res["rule_name"]: res for res in run["results"]}
    assert by_name["customers-sheet-required"]["status"] == "passed"
    assert by_name["customer-id-not-null"]["status"] == "failed"
    assert by_name["customer-id-unique"]["status"] == "failed"
    assert by_name["orders-customer-fk"]["status"] == "failed"
    assert by_name["orders-customer-fk"]["failure_count"] == 1
    assert by_name["order-total-positive"]["status"] == "passed"
    assert run["error_failures"] == 3 and run["warning_failures"] == 0
    assert by_name["customer-id-not-null"]["sample_failures"]  # evidence included

    # Runs are persisted and retrievable.
    runs = (await client.get(f"/api/v1/datasets/{ds}/versions/1/validations", headers=h)).json()
    assert runs["total"] == 1
    detail = (await client.get(f"/api/v1/datasets/{ds}/validations/{run['id']}",
                               headers=h)).json()
    assert len(detail["results"]) == 7

    # ---- 3. The promotion gate blocks the dirty version ----
    r = await client.post(f"/api/v1/datasets/{ds}/tags/production/promote", headers=h,
                          json={"version_number": 1, "reason": "try to ship it"})
    assert r.status_code == 409
    body = r.json()
    assert body["code"] == "validation-failed" and body["error_failures"] == 3

    # An unvalidated version is blocked too (upload v2, promote before validating).
    await _upload(client, admin_id, clean, dataset_id=ds)
    r = await client.post(f"/api/v1/datasets/{ds}/tags/production/promote", headers=h,
                          json={"version_number": 2})
    assert r.status_code == 409 and r.json()["code"] == "validation-required"

    # ---- 4. Validate the clean version and promote through the gate ----
    r = await client.post(f"/api/v1/datasets/{ds}/versions/2/validate", headers=h)
    assert r.status_code == 200
    assert r.json()["error_failures"] == 0

    r = await client.post(f"/api/v1/datasets/{ds}/tags/production/promote", headers=h,
                          json={"version_number": 2, "reason": "validated clean"})
    assert r.status_code == 200 and r.json()["to_version_number"] == 2

    # Raw PUT stays ungated (documented escape hatch).
    r = await client.put(f"/api/v1/datasets/{ds}/tags", headers=h,
                         json={"tag_name": "scratch", "version_number": 1})
    assert r.status_code == 200

    # ---- 5. Rule lifecycle: disable a rule and it stops counting ----
    r = await client.patch(f"/api/v1/datasets/{ds}/rules/{rule_ids['orders-customer-fk']}",
                           headers=h, json={"enabled": False})
    assert r.status_code == 200 and r.json()["enabled"] is False
    r = await client.post(f"/api/v1/datasets/{ds}/versions/1/validate", headers=h)
    assert r.json()["rules_total"] == 6 and r.json()["error_failures"] == 2

    r = await client.delete(f"/api/v1/datasets/{ds}/rules/{rule_ids['order-total-positive']}",
                            headers=h)
    assert r.status_code == 204
    # Past results keep the deleted rule's snapshot.
    detail = (await client.get(f"/api/v1/datasets/{ds}/validations/{run['id']}",
                               headers=h)).json()
    assert any(res["rule_name"] == "order-total-positive" for res in detail["results"])


async def test_quality_rbac(client, admin_id, tmp_path):
    """Viewers can read rules/results but not mutate or validate; outsiders 404."""
    from conftest import rid
    h = auth(admin_id)
    wb_path = tmp_path / "wb.xlsx"
    _orders_workbook(wb_path, clean=True)
    ds = await _upload(client, admin_id, wb_path)
    r = await client.post(f"/api/v1/datasets/{ds}/rules", headers=h, json={
        "name": "min-rows", "rule_type": "row_count_min", "sheet_selector": "orders"})
    assert r.status_code == 201

    team = (await client.post("/api/v1/teams", headers=h, json={"name": f"q-{rid()}"})).json()
    outsider = (await client.post("/api/v1/auth/users", headers=h,
                                  json={"email": f"o-{rid()}@bank.com", "name": "O",
                                        "team_id": team["id"]})).json()["id"]
    assert (await client.get(f"/api/v1/datasets/{ds}/rules",
                             headers=auth(outsider))).status_code == 404
    assert (await client.post(f"/api/v1/datasets/{ds}/versions/1/validate",
                              headers=auth(outsider))).status_code == 404
