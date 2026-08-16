"""Phase 2 (trust) — quality rules, validation runs, promotion gates.

One UI-ordered journey: define rules → validate → inspect failures → see the
promotion gate block → fix the data with a new version → validate clean →
promote through the gate.
"""

from __future__ import annotations

from conftest import auth, create_team_user, make_orders_workbook, upload_file

PROBLEM = "application/problem+json"


async def _upload(client, admin_id, path, dataset_id=None):
    body = await upload_file(client, admin_id, path, name="orders.xlsx",
                             dataset_id=dataset_id)
    return body["dataset_id"]


async def test_quality_journey(client, admin_id, tmp_path):
    h = auth(admin_id)
    dirty = tmp_path / "orders_v1.xlsx"
    clean = tmp_path / "orders_v2.xlsx"
    make_orders_workbook(dirty, clean=False)
    make_orders_workbook(clean, clean=True)
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
    # Failing rows live in the object store, not Postgres — the result carries
    # a pointer, and the rows come back through the usual /samples path.
    failures = by_name["customer-id-not-null"]
    assert "sample_failures" not in failures
    assert failures["failure_sample_file"] and failures["failure_artifact_id"]
    fetched = await client.get(
        f"/api/v1/samples/{failures['failure_sample_file']}/data", headers=h)
    assert fetched.status_code == 200, fetched.text
    rows = fetched.json()["data"] if isinstance(fetched.json(), dict) else fetched.json()
    assert len(rows) == 1

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
    h = auth(admin_id)
    wb_path = tmp_path / "wb.xlsx"
    make_orders_workbook(wb_path, clean=True)
    ds = await _upload(client, admin_id, wb_path)
    r = await client.post(f"/api/v1/datasets/{ds}/rules", headers=h, json={
        "name": "min-rows", "rule_type": "row_count_min", "sheet_selector": "orders"})
    assert r.status_code == 201

    outsider, _ = await create_team_user(client, admin_id, "viewer")
    assert (await client.get(f"/api/v1/datasets/{ds}/rules",
                             headers=auth(outsider))).status_code == 404
    assert (await client.post(f"/api/v1/datasets/{ds}/versions/1/validate",
                              headers=auth(outsider))).status_code == 404


async def _one_rule_run(client, h, ds, spec, version=1):
    """Create one rule on a fresh ruleset, validate, return its result row."""
    r = await client.post(f"/api/v1/datasets/{ds}/rules", headers=h, json=spec)
    assert r.status_code == 201, r.text
    run = (await client.post(f"/api/v1/datasets/{ds}/versions/{version}/validate",
                             headers=h)).json()
    assert run["rules_total"] == 1
    # Clean up so the next isolated case starts from zero rules.
    await client.delete(f"/api/v1/datasets/{ds}/rules/{r.json()['id']}", headers=h)
    return run["results"][0]


async def test_each_rule_type_fails_in_isolation(client, admin_id, tmp_path):
    """One rule at a time against the dirty workbook — no cross-rule noise."""
    h = auth(admin_id)
    dirty = tmp_path / "dirty.xlsx"
    make_orders_workbook(dirty, clean=False)
    ds = await _upload(client, admin_id, dirty)

    res = await _one_rule_run(client, h, ds, {
        "name": "tier-strict", "rule_type": "accepted_values",
        "sheet_selector": "customers", "column_selector": "tier",
        "parameters": {"values": ["gold", "silver"]}})
    assert res["status"] == "failed" and res["failure_count"] == 2  # bronze+copper

    res = await _one_rule_run(client, h, ds, {
        "name": "total-window", "rule_type": "range",
        "sheet_selector": "orders", "column_selector": "total",
        "parameters": {"min": 10, "max": 50}})
    assert res["status"] == "failed" and res["failure_count"] == 2  # 99.5 and 5.0

    res = await _one_rule_run(client, h, ds, {
        "name": "must-have-refunds", "rule_type": "sheet_exists",
        "sheet_selector": "refunds"})
    assert res["status"] == "failed" and res["failure_count"] == 1


async def test_validate_error_paths(client, admin_id, tmp_path):
    h = auth(admin_id)
    wb = tmp_path / "wb.xlsx"
    make_orders_workbook(wb, clean=True)
    ds = await _upload(client, admin_id, wb)

    # Zero enabled rules → explicit 400, not an empty green run.
    r = await client.post(f"/api/v1/datasets/{ds}/versions/1/validate", headers=h)
    assert r.status_code == 400 and "no enabled" in r.json()["detail"]

    r = await client.post(f"/api/v1/datasets/{ds}/rules", headers=h, json={
        "name": "min-rows", "rule_type": "row_count_min", "sheet_selector": "orders"})
    assert r.status_code == 201

    # Nonexistent version → 404.
    r = await client.post(f"/api/v1/datasets/{ds}/versions/9/validate", headers=h)
    assert r.status_code == 404


async def test_revalidation_history_is_newest_first(client, admin_id, tmp_path):
    h = auth(admin_id)
    wb = tmp_path / "wb.xlsx"
    make_orders_workbook(wb, clean=True)
    ds = await _upload(client, admin_id, wb)
    r = await client.post(f"/api/v1/datasets/{ds}/rules", headers=h, json={
        "name": "min-rows", "rule_type": "row_count_min", "sheet_selector": "orders"})
    assert r.status_code == 201

    run_ids = []
    for _ in range(3):
        run = (await client.post(f"/api/v1/datasets/{ds}/versions/1/validate",
                                 headers=h)).json()
        run_ids.append(run["id"])

    runs = (await client.get(f"/api/v1/datasets/{ds}/versions/1/validations",
                             headers=h)).json()
    assert runs["total"] == 3
    assert [x["id"] for x in runs["items"]] == list(reversed(run_ids))
