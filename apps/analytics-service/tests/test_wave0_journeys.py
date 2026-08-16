"""Wave 0 features driven the way a UI would drive them — composed journeys.

Multi-step user journeys over the public ``/api/v1`` HTTP API only, exercising
the ROADMAP Wave 0 features (§1 logical sheet identity + confirm-rename,
§3 aggregation extras, §4 corrupt-upload 400, §5 auto-key coordinated
sampling) together with the cross-cutting contracts a front-end depends on:
problem+json error shape, sheet-selection-required, cross-team 404 hiding,
in-team 403, audit trail visibility, and the pagination envelope.

Per-feature behavior is already pinned in test_logical_sheets.py,
test_coordinated_sampling.py and test_aggregation_extras.py; the value here
is the realistic sequencing (non-admin actors, state flowing step to step)
and the composed effects — e.g. the promotion gate blocking and then
re-arming across a confirmed rename.
"""

from __future__ import annotations

import io
from datetime import date

from openpyxl import Workbook

from conftest import (
    DEFAULT_TEAM_ID,
    auth,
    create_team_user,
    make_orders_workbook,
    upload_file,
)

PROBLEM = "application/problem+json"


# ---------------------------------------------------------------------------
# Workbook / dataset builders local to these journeys
# ---------------------------------------------------------------------------


def _steward_workbook(path, expenses_name="Expenses"):
    """Ledger + Expenses where Ledger.item is a *passing* FK into Expenses.

    Unlike the conftest factories, the cross-sheet FK genuinely holds
    (every ledger item exists in Expenses), so validation can go green and
    the promotion gate can open. The Expenses sheet content is identical
    across builds — only the name changes — so a rename fingerprints as a
    high-confidence candidate.
    """
    wb = Workbook()
    ledger = wb.active
    ledger.title = "Ledger"
    ledger.append(["entry_id", "item", "amount"])
    for row in ([1, "rent", 120.0], [2, "power", 45.5], [3, "rent", 80.0]):
        ledger.append(row)
    exp = wb.create_sheet(expenses_name)
    exp.append(["Item", "Cost"])
    exp.append(["rent", 50])
    exp.append(["power", 20])
    wb.save(path)


SALES_CSV = """order_date,category,channel,amount
2024-01-05,food,online,10.0
2024-01-20,gear,store,40.0
2024-02-02,food,online,25.0
2024-02-14,food,store,15.0
2024-02-28,gear,online,60.0
2024-03-03,food,online,30.0
2024-03-18,gear,store,20.0
2024-03-25,food,online,5.0
"""
# Month totals: Jan 50.0, Feb 100.0, Mar 55.0 — online: 10.0 / 85.0 / 35.0


async def _rename_dataset(client, user_id, tmp_path, *, team_id):
    """Upload steward workbook v1 (Expenses) + v2 (Operating Costs); return ds id."""
    v1, v2 = tmp_path / "steward_v1.xlsx", tmp_path / "steward_v2.xlsx"
    _steward_workbook(v1, "Expenses")
    _steward_workbook(v2, "Operating Costs")
    ds = (await upload_file(client, user_id, v1, name="steward.xlsx",
                            team_id=team_id))["dataset_id"]
    await upload_file(client, user_id, v2, name="steward.xlsx",
                      dataset_id=ds, team_id=team_id)
    return ds


# ---------------------------------------------------------------------------
# A. Data-steward rename lifecycle — an editor, not the platform admin
# ---------------------------------------------------------------------------


async def test_journey_steward_rename_lifecycle_as_editor(client, admin_id, tmp_path):
    """A team editor curates a dataset across a sheet rename.

    Upload v1 → attach sheet metadata + quality rules (column + cross-sheet
    FK) → validate green → upload v2 with the sheet renamed → the diff offers
    the rename, validation errors, and the promotion gate *blocks* → confirm
    the rename → re-validate green → promote through the re-armed gate.
    The sheet's logical_sheet_id is asserted continuous in every GET.
    """
    # ---- 1. Admin provisions the team and its editor; editor works alone ----
    editor, tid = await create_team_user(client, admin_id, "editor")
    h = auth(editor)

    v1, v2 = tmp_path / "v1.xlsx", tmp_path / "v2.xlsx"
    _steward_workbook(v1, "Expenses")
    _steward_workbook(v2, "Operating Costs")
    ds = (await upload_file(client, editor, v1, name="steward.xlsx",
                            team_id=tid))["dataset_id"]

    # ---- 2. Browse sheets; capture the logical identity the UI will track ----
    sheets = (await client.get(f"/api/v1/datasets/{ds}/sheets", headers=h)).json()
    by_name = {s["name"]: s for s in sheets["items"]}
    assert set(by_name) == {"Ledger", "Expenses"}
    lid = by_name["Expenses"]["logical_sheet_id"]
    assert lid

    # ---- 3. Attach keyed state: sheet metadata + two quality rules ----
    r = await client.put(f"/api/v1/datasets/{ds}/sheet-metadata/Expenses",
                         headers=h, json={"grain": "one row per cost item"})
    assert r.status_code == 200, r.text
    assert r.json()["logical_sheet_id"] == lid

    r = await client.post(f"/api/v1/datasets/{ds}/rules", headers=h, json={
        "name": "cost-not-null", "scope_type": "column", "rule_type": "not_null",
        "sheet_selector": "Expenses", "column_selector": "cost"})
    assert r.status_code == 201, r.text
    assert r.json()["logical_sheet_id"] == lid

    r = await client.post(f"/api/v1/datasets/{ds}/rules", headers=h, json={
        "name": "ledger-item-fk", "scope_type": "cross_sheet",
        "rule_type": "foreign_key", "sheet_selector": "Ledger",
        "column_selector": "item",
        "parameters": {"ref_sheet": "expenses", "ref_column": "item"}})
    assert r.status_code == 201, r.text

    # ---- 4. Validate v1: both rules pass (the FK genuinely holds) ----
    r = await client.post(f"/api/v1/datasets/{ds}/versions/1/validate", headers=h)
    assert r.status_code == 200, r.text
    run = r.json()
    assert run["error_failures"] == 0
    assert {x["rule_name"]: x["status"] for x in run["results"]} == {
        "cost-not-null": "passed", "ledger-item-fk": "passed"}

    # ---- 5. v2 arrives with the sheet renamed ----
    await upload_file(client, editor, v2, name="steward.xlsx",
                      dataset_id=ds, team_id=tid)

    diff = (await client.get(f"/api/v1/datasets/{ds}/versions/1/diff/2",
                             headers=h)).json()
    cands = [(c["from_sheet"], c["to_sheet"]) for c in diff["rename_candidates"]]
    assert ("Expenses", "Operating Costs") in cands

    # ---- 6. Validation on v2 errors — and the promotion gate blocks ----
    r = await client.post(f"/api/v1/datasets/{ds}/versions/2/validate", headers=h)
    assert r.status_code == 200, r.text
    statuses = {x["rule_name"]: x["status"] for x in r.json()["results"]}
    assert statuses["cost-not-null"] == "error"      # sheet gone under old key
    assert statuses["ledger-item-fk"] == "error"     # ref_sheet gone too
    assert r.json()["error_failures"] > 0

    r = await client.post(f"/api/v1/datasets/{ds}/tags/production/promote",
                          headers=h, json={"version_number": 2, "reason": "ship"})
    assert r.status_code == 409, r.text
    assert r.headers["content-type"].startswith(PROBLEM)
    assert r.json()["code"] == "validation-failed"

    # ---- 7. Confirm the rename; identity is continuous ----
    r = await client.post(f"/api/v1/datasets/{ds}/versions/2/confirm-rename",
                          headers=h,
                          json={"from_sheet": "Expenses",
                                "to_sheet": "Operating Costs"})
    assert r.status_code == 200, r.text
    assert r.json()["logical_sheet_id"] == lid
    assert r.json()["was_candidate"] is True

    sheets = (await client.get(f"/api/v1/datasets/{ds}/sheets", headers=h)).json()
    by_name = {s["name"]: s for s in sheets["items"]}
    assert by_name["Operating Costs"]["logical_sheet_id"] == lid

    meta = (await client.get(f"/api/v1/datasets/{ds}/sheet-metadata",
                             headers=h)).json()
    rows = {m["sheet_key"]: m for m in meta["items"]}
    assert rows["operating_costs"]["logical_sheet_id"] == lid
    assert rows["operating_costs"]["grain"] == "one row per cost item"

    rules = (await client.get(f"/api/v1/datasets/{ds}/rules", headers=h)).json()
    by_rule = {x["name"]: x for x in rules["items"]}
    assert by_rule["cost-not-null"]["logical_sheet_id"] == lid
    assert by_rule["ledger-item-fk"]["parameters"]["ref_sheet"] == "operating_costs"

    # ---- 8. Re-validate green and promote through the re-armed gate ----
    r = await client.post(f"/api/v1/datasets/{ds}/versions/2/validate", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["error_failures"] == 0
    assert all(x["status"] == "passed" for x in r.json()["results"])

    r = await client.post(f"/api/v1/datasets/{ds}/tags/production/promote",
                          headers=h,
                          json={"version_number": 2, "reason": "rename confirmed"})
    assert r.status_code == 200, r.text
    assert r.json()["to_version_number"] == 2


# ---------------------------------------------------------------------------
# B. Analyst coordinated-sampling journey with auto-keys + persisted artifacts
# ---------------------------------------------------------------------------


async def test_journey_analyst_coordinated_sampling_auto_keys(
    client, admin_id, tmp_path,
):
    """An analyst samples a two-sheet workbook without spelling out join keys.

    The first naive aggregate hits sheet-selection-required (the 400 a UI
    uses to populate its sheet picker), an editor declares the FK rule once,
    and the coordinated sample borrows the keys from it. The persisted
    parquet artifacts — not just the response body — are downloaded via
    /samples/{f} and /samples/{f}/data and checked for referential
    consistency.
    """
    editor, tid = await create_team_user(client, admin_id, "editor")
    h = auth(editor)
    p = tmp_path / "orders.xlsx"
    make_orders_workbook(p, clean=True)
    ds = (await upload_file(client, editor, p, team_id=tid))["dataset_id"]

    # ---- 1. Naive first call → the sheet-picker contract ----
    r = await client.post("/api/v1/aggregate", headers=h, json={
        "dataset_id": ds, "group_by": ["customer_id"],
        "aggregations": [{"column": "total", "function": "sum"}]})
    assert r.status_code == 400, r.text
    assert r.headers["content-type"].startswith(PROBLEM)
    assert r.json()["code"] == "sheet-selection-required"
    assert set(r.json()["sheets"]) == {"Customers", "Orders"}

    # ---- 2. Declare the relationship once, as a quality rule ----
    r = await client.post(f"/api/v1/datasets/{ds}/rules", headers=h, json={
        "name": "orders-fk", "scope_type": "cross_sheet",
        "rule_type": "foreign_key", "sheet_selector": "Orders",
        "column_selector": "customer_id",
        "parameters": {"ref_sheet": "customers", "ref_column": "customer_id"}})
    assert r.status_code == 201, r.text

    # ---- 3. Coordinated sample WITHOUT keys — they default from the rule ----
    r = await client.post("/api/v1/sample/coordinated", headers=h, json={
        "dataset_id": ds, "driver_sheet": "Orders", "target_total_volume": 2,
        "sampling_steps": [{"method": "random", "sample_size": 2}],
        "seed": 7, "related": [{"sheet": "Customers"}]})
    assert r.status_code == 200, r.text
    body = r.json()
    rel = body["related"][0]
    assert rel["left_on"] == "customer_id" and rel["right_on"] == "customer_id"
    driver_file, rel_file = body["driver"]["sample_file"], rel["sample_file"]
    assert driver_file and rel_file

    # ---- 4. The persisted artifacts themselves are consistent ----
    for fname in (driver_file, rel_file):
        raw = await client.get(f"/api/v1/samples/{fname}", headers=h)
        assert raw.status_code == 200
        assert raw.content[:4] == b"PAR1"  # a real parquet file came back

    driver_rows = (await client.get(f"/api/v1/samples/{driver_file}/data",
                                    headers=h)).json()
    rel_rows = (await client.get(f"/api/v1/samples/{rel_file}/data",
                                 headers=h)).json()
    assert driver_rows["total_count"] == body["driver"]["sampled_count"]
    assert rel_rows["total_count"] == rel["sampled_count"]

    driver_keys = {row["customer_id"] for row in driver_rows["data"]}
    rel_keys = {row["customer_id"] for row in rel_rows["data"]}
    assert rel_keys == driver_keys  # the semi-join survived persistence
    assert len(driver_rows["data"]) == 2


# ---------------------------------------------------------------------------
# C. Aggregation dashboard journey — bucketed overview, then drill-down
# ---------------------------------------------------------------------------


async def test_journey_dashboard_bucketed_overview_then_drilldown(
    client, admin_id, tmp_path,
):
    """One dashboard request combines month bucketing, a conditional
    aggregate, HAVING, sort and limit; the follow-up drill-down derives
    structured filters from a bucket value in the first response and its
    totals reconcile with the bucket it drilled into."""
    editor, tid = await create_team_user(client, admin_id, "editor")
    h = auth(editor)
    p = tmp_path / "sales.csv"
    p.write_text(SALES_CSV)
    ds = (await upload_file(client, editor, p, team_id=tid))["dataset_id"]

    # ---- 1. The overview tile: everything in ONE request ----
    r = await client.post("/api/v1/aggregate", headers=h, json={
        "dataset_id": ds,
        "group_by": [{"column": "order_date", "date_trunc": "month",
                      "alias": "month"}],
        "aggregations": [
            {"column": "amount", "function": "sum", "alias": "total"},
            {"column": "amount", "function": "sum", "alias": "online_total",
             "filter": {"conditions": [
                 {"column": "channel", "op": "eq", "value": "online"}]}},
        ],
        "having": [{"column": "total", "op": "gt", "value": 50}],
        "sort_by": "total", "sort_order": "desc", "limit": 12})
    assert r.status_code == 200, r.text
    body = r.json()
    # Envelope fields a dashboard binds to (AggregateResponse contract).
    assert body["success"] is True
    assert body["truncated"] is False
    assert body["columns"] == ["month", "total", "online_total"]
    assert body["group_count"] == 2  # Jan (50.0) dropped by HAVING
    assert [(str(row["month"])[:7], row["total"], row["online_total"])
            for row in body["data"]] == [("2024-02", 100.0, 85.0),
                                         ("2024-03", 55.0, 35.0)]
    # Grand totals cover the returned rows.
    assert body["totals"]["total"] == 155.0
    assert body["totals"]["online_total"] == 120.0

    # ---- 2. Drill into the top bucket using structured filters ----
    top = body["data"][0]
    start = date.fromisoformat(str(top["month"])[:10])
    nxt = (date(start.year + 1, 1, 1) if start.month == 12
           else date(start.year, start.month + 1, 1))

    r = await client.post("/api/v1/aggregate", headers=h, json={
        "dataset_id": ds,
        "filters": {"logic": "and", "conditions": [
            {"column": "order_date", "op": "gte", "value": start.isoformat()},
            {"column": "order_date", "op": "lt", "value": nxt.isoformat()}]},
        "group_by": ["category"],
        "aggregations": [{"column": "amount", "function": "sum",
                          "alias": "total"}],
        "sort_by": "total", "sort_order": "desc"})
    assert r.status_code == 200, r.text
    drill = r.json()
    assert drill["group_count"] == 2
    assert [(row["category"], row["total"]) for row in drill["data"]] == [
        ("gear", 60.0), ("food", 40.0)]
    # The drill-down partitions exactly the bucket it came from.
    assert sum(row["total"] for row in drill["data"]) == top["total"]
    assert drill["totals"]["total"] == top["total"]


# ---------------------------------------------------------------------------
# D. Error-surface journey — the problem+json fields a UI must render
# ---------------------------------------------------------------------------


async def test_journey_error_surfaces_are_renderable_problems(
    client, admin_id, tmp_path,
):
    """Every Wave 0 failure a UI can trigger comes back as
    application/problem+json with ``detail`` and ``code`` — the two fields an
    error component needs — and RBAC failures keep their 404-hides/403-blocks
    split."""

    def _problem(r, status):
        assert r.status_code == status, r.text
        assert r.headers["content-type"].startswith(PROBLEM)
        body = r.json()
        assert body.get("detail")   # human-renderable
        assert body.get("code")     # machine-dispatchable
        return body

    # Fixtures: a rename-pending dataset and a two-sheet dataset, Default team.
    ds_rename = await _rename_dataset(client, admin_id, tmp_path,
                                      team_id=DEFAULT_TEAM_ID)
    orders = tmp_path / "orders.xlsx"
    make_orders_workbook(orders, clean=True)
    ds_orders = (await upload_file(client, admin_id, orders))["dataset_id"]

    # ---- 1. Corrupt xlsx upload (as a non-admin editor in their own team) ----
    editor, tid = await create_team_user(client, admin_id, "editor")
    r = await client.post("/api/v1/upload",
                          headers={**auth(editor), "X-Team-Id": tid},
                          files={"file": ("report.xlsx",
                                          io.BytesIO(b"definitely not a zip"),
                                          "application/octet-stream")})
    body = _problem(r, 400)
    assert body["code"] == "invalid-file"

    # ---- 2. Aggregate HAVING an unknown alias ----
    viewer, _ = await create_team_user(client, admin_id, "viewer",
                                       team_id=DEFAULT_TEAM_ID)
    r = await client.post("/api/v1/aggregate", headers=auth(viewer), json={
        "dataset_id": ds_orders, "sheet": "Orders", "group_by": ["customer_id"],
        "aggregations": [{"column": "total", "function": "sum",
                          "alias": "spend"}],
        "having": [{"column": "ghost", "op": "gt", "value": 1}]})
    body = _problem(r, 400)
    assert "ghost" in body["detail"] and "spend" in body["detail"]

    # ---- 3. Confirm-rename across teams: existence is hidden ----
    payload = {"from_sheet": "Expenses", "to_sheet": "Operating Costs"}
    url = f"/api/v1/datasets/{ds_rename}/versions/2/confirm-rename"
    outsider, _ = await create_team_user(client, admin_id, "editor")
    _problem(await client.post(url, headers=auth(outsider), json=payload), 404)

    # ---- 4. Confirm-rename in-team as viewer: visible but forbidden ----
    _problem(await client.post(url, headers=auth(viewer), json=payload), 403)

    # ---- 5. Coordinated sample, keys omitted, no FK rule to borrow from ----
    r = await client.post("/api/v1/sample/coordinated", headers=auth(viewer),
                          json={"dataset_id": ds_orders,
                                "driver_sheet": "Orders",
                                "target_total_volume": 2,
                                "sampling_steps": [{"method": "random",
                                                    "sample_size": 2}],
                                "related": [{"sheet": "Customers"}]})
    body = _problem(r, 400)
    # The detail is actionable: it names the missing rule kind.
    assert "No foreign_key quality rule" in body["detail"]


# ---------------------------------------------------------------------------
# E. Audit visibility — the rename shows up on the record, attributed
# ---------------------------------------------------------------------------


async def test_journey_confirm_rename_lands_in_audit_trail(
    client, admin_id, tmp_path,
):
    """After an editor confirms a rename, the superuser sees the exact call in
    /api/v1/audit — POST, the confirm-rename path for that dataset, the
    editor's identity, status 200 — inside the standard pagination envelope.
    (audit_log is append-only across tests, so match on the unique dataset id
    rather than any global counts.)"""
    editor, tid = await create_team_user(client, admin_id, "editor")
    ds = await _rename_dataset(client, editor, tmp_path, team_id=tid)

    r = await client.post(f"/api/v1/datasets/{ds}/versions/2/confirm-rename",
                          headers=auth(editor),
                          json={"from_sheet": "Expenses",
                                "to_sheet": "Operating Costs"})
    assert r.status_code == 200, r.text

    page = (await client.get("/api/v1/audit", params={"limit": 100},
                             headers=auth(admin_id))).json()
    # Pagination envelope contract.
    assert {"items", "total", "limit", "offset"} <= set(page)
    assert page["limit"] == 100 and page["offset"] == 0

    wanted = f"/api/v1/datasets/{ds}/versions/2/confirm-rename"
    entries = [e for e in page["items"] if e["path"] == wanted]
    assert len(entries) == 1
    entry = entries[0]
    assert entry["method"] == "POST"
    assert entry["status_code"] == 200
    assert entry["actor_user_id"] == editor
    assert entry["actor_email"] and entry["actor_email"].startswith("editor-")
    assert entry["request_id"]
