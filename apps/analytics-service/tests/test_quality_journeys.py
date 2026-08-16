"""Quality journeys — the data-contract screens, driven the way a UI drives them.

Every step is a real HTTP call in the order a screen makes it, and each step
asserts something that is only true *because* of the step before it. The
screens modelled here:

1. **Rule authoring form** — the sheet/column pickers are populated from
   ``GET /sheets``; the values they offer must be the values ``POST /rules``
   accepts and the engine resolves.
2. **Rule editor drawer** — a rename/retarget/reparameterise round trip, and
   the next run's verdict changing because of it.
3. **Release screen** — the error/warning split: a warning is *reported as a
   failure* and still ships; an error blocks.
4. **Failure drill-down** — run detail → failing-rows table, and who may open it.
5. **Validation history table** — paging, and opening a row from the list.
6. **Gate arming** — disabling / deleting the last rule disarms the promotion
   gate; re-enabling re-arms it.
7. **RBAC** — what a viewer sees that an editor does not, and what an outsider
   cannot see at all.
8. **A version that failed to ingest** — validate refuses it and leaves no
   orphan run or job behind.

Endpoints exercised: POST/GET/PATCH/DELETE ``/datasets/{ds}/rules[/{id}]``,
POST ``/datasets/{ds}/versions/{n}/validate``,
GET ``/datasets/{ds}/versions/{n}/validations``,
GET ``/datasets/{ds}/validations/{run_id}``.
"""

from __future__ import annotations

import io

from conftest import auth, create_team_user, make_orders_workbook, upload_file

PROBLEM = "application/problem+json"


# ---------------------------------------------------------------------------
# Helpers — every one of them moves only over HTTP, exactly as a UI would.
# ---------------------------------------------------------------------------


async def _orders_dataset(client, user_id, tmp_path, *, clean, team_id,
                          filename="orders.xlsx", dataset_id=None):
    wb = tmp_path / filename
    make_orders_workbook(wb, clean=clean)
    body = await upload_file(client, user_id, wb, name="orders.xlsx",
                             team_id=team_id, dataset_id=dataset_id)
    return body["dataset_id"]


async def _rule(client, h, ds, spec):
    r = await client.post(f"/api/v1/datasets/{ds}/rules", headers=h, json=spec)
    assert r.status_code == 201, r.text
    return r.json()


async def _validate(client, h, ds, version=1):
    r = await client.post(f"/api/v1/datasets/{ds}/versions/{version}/validate",
                          headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def _by_name(run):
    return {res["rule_name"]: res for res in run["results"]}


# ---------------------------------------------------------------------------
# 1. Rule authoring form
# ---------------------------------------------------------------------------


async def test_a_steward_authors_rules_from_the_pickers_the_sheets_endpoints_populate(
        client, admin_id, tmp_path):
    """SCREEN: "New quality rule" form on the dataset's Quality tab.

    The form cannot invent a ``sheet_selector`` or a ``column_selector``. It
    populates the sheet dropdown from ``GET /datasets/{ds}/sheets`` (which
    labels sheets with ``name``) and the column dropdown from the chosen
    sheet's ``columns[].normalized_name``, and it seeds numeric parameters from
    the sheet's ``row_count``. If any of those values is not something
    ``POST /rules`` accepts and the engine can resolve, every rule the form
    produces comes back from the next run as a per-rule ``error`` — the screen
    looks like it works and the contract silently validates nothing.
    """
    editor, team = await create_team_user(client, admin_id, "editor")
    h = auth(editor)
    ds = await _orders_dataset(client, editor, tmp_path, clean=False, team_id=team)

    # ---- 1. Mount: the sheet picker ----
    sheets = await client.get(f"/api/v1/datasets/{ds}/sheets", headers=h)
    assert sheets.status_code == 200, sheets.text
    page = sheets.json()
    assert page["total"] == 2 and page["offset"] == 0
    by_sheet = {s["name"]: s for s in page["items"]}
    assert set(by_sheet) == {"Customers", "Orders"}
    customers, orders = by_sheet["Customers"], by_sheet["Orders"]
    # The picker's label and its key are different strings — this is the
    # name-vs-key seam the form has to get right.
    assert customers["name"] == "Customers" and customers["sheet_key"] == "customers"

    # ---- 2. Choosing a sheet loads its column picker ----
    detail = await client.get(f"/api/v1/datasets/{ds}/sheets/{customers['name']}",
                              headers=h)
    assert detail.status_code == 200, detail.text
    cust_detail = detail.json()
    assert cust_detail["name"] == "Customers"
    columns = [c["normalized_name"] for c in cust_detail["columns"]]
    assert columns == [c["normalized_name"] for c in customers["columns"]], (
        "the detail endpoint and the list endpoint must agree about the columns, "
        "or the picker changes its options when you open it")
    assert "customer_id" in columns and "tier" in columns

    orders_detail = (await client.get(
        f"/api/v1/datasets/{ds}/sheets/{orders['name']}", headers=h)).json()
    assert orders_detail["row_count"] == 3  # 2 clean orders + the orphan

    # ---- 3. Submit the form using ONLY values the pickers handed over ----
    # Rule A: sheet chosen by its DISPLAY NAME, column by its normalized name.
    by_display = await _rule(client, h, ds, {
        "name": "customer-id-present",
        "description": "authored from the picker",
        "rule_type": "not_null",
        "sheet_selector": customers["name"],        # "Customers"
        "column_selector": "customer_id",
    })
    # The API pins the rule to the live logical sheet and normalizes the label
    # to the sheet KEY, so the form must re-render from the response, not from
    # what it sent.
    assert by_display["sheet_selector"] == customers["sheet_key"] == "customers"
    assert by_display["logical_sheet_id"] == customers["logical_sheet_id"]
    assert by_display["scope_type"] == "column" and by_display["severity"] == "error"
    assert by_display["enabled"] is True and by_display["created_by"] == editor

    # Rule B: sheet chosen by its KEY, threshold seeded from the sheet's own
    # row_count — the other half of what the pickers offer.
    by_key = await _rule(client, h, ds, {
        "name": "orders-must-grow",
        "rule_type": "row_count_min",
        "sheet_selector": orders["sheet_key"],       # "orders"
        "parameters": {"min": orders_detail["row_count"] + 1},
    })
    assert by_key["sheet_selector"] == "orders"
    assert by_key["logical_sheet_id"] == orders["logical_sheet_id"]
    assert by_key["parameters"] == {"min": 4}

    # ---- 4. The rules table re-renders from the list endpoint ----
    listing = await client.get(f"/api/v1/datasets/{ds}/rules", headers=h)
    assert listing.status_code == 200
    body = listing.json()
    assert body["total"] == 2 and body["limit"] == 2 and body["offset"] == 0
    assert [r["name"] for r in body["items"]] == [
        "customer-id-present", "orders-must-grow"]

    # ---- 5. Deep link to one rule returns exactly what create returned ----
    one = await client.get(f"/api/v1/datasets/{ds}/rules/{by_display['id']}", headers=h)
    assert one.status_code == 200 and one.json() == by_display

    # ---- 6. Run it: both picker-authored rules RESOLVE (no "not found") ----
    run = await _validate(client, h, ds)
    assert run["status"] == "completed" and run["rules_total"] == 2
    results = _by_name(run)
    assert {r["status"] for r in results.values()} == {"failed"}, (
        f"a picker-authored selector failed to resolve: "
        f"{[(r['rule_name'], r['status'], r['message']) for r in run['results']]}")
    assert results["customer-id-present"]["failure_count"] == 1   # the NULL id
    assert results["orders-must-grow"]["failure_count"] == 1      # 3 rows, min 4
    assert results["orders-must-grow"]["message"] == "Sheet has 3 rows, minimum is 4"

    # ---- 7. The form rejects a hand-typed shape the pickers cannot produce ----
    bad = await client.post(f"/api/v1/datasets/{ds}/rules", headers=h, json={
        "name": "no-column", "rule_type": "not_null",
        "sheet_selector": customers["sheet_key"]})
    assert bad.status_code == 422 and bad.headers["content-type"].startswith(PROBLEM)

    # ...and refuses a duplicate name with a 409 the form can attach to the field.
    dup = await client.post(f"/api/v1/datasets/{ds}/rules", headers=h, json={
        "name": "customer-id-present", "rule_type": "unique",
        "sheet_selector": "customers", "column_selector": "customer_id"})
    assert dup.status_code == 409 and dup.json()["code"] == "conflict"
    assert "customer-id-present" in dup.json()["detail"]
    assert (await client.get(f"/api/v1/datasets/{ds}/rules",
                             headers=h)).json()["total"] == 2


# ---------------------------------------------------------------------------
# 2. Rule editor drawer
# ---------------------------------------------------------------------------


async def test_the_rule_editor_retargets_a_rule_and_the_next_run_changes_its_verdict(
        client, admin_id, tmp_path):
    """SCREEN: "Edit rule" drawer, opened from a row of the rules table.

    The drawer sends every field the user touched — name, description,
    severity, parameters, and the sheet/column it points at — and then the
    steward re-runs validation to see the effect. Two things a UI breaks on if
    they regress: a PATCH that is accepted but does not change the next run's
    verdict (the steward tightens a rule, sees green, and ships), and a PATCH
    that rewrites the *history* (the run they already reviewed must keep the
    rule as it was when it ran, because the results table renders from that
    snapshot).
    """
    editor, team = await create_team_user(client, admin_id, "editor")
    h = auth(editor)
    ds = await _orders_dataset(client, editor, tmp_path, clean=False, team_id=team)

    rule = await _rule(client, h, ds, {
        "name": "tier-accepted", "description": "the tiers we know about",
        "rule_type": "accepted_values",
        "sheet_selector": "Customers", "column_selector": "tier",
        "parameters": {"values": ["gold", "silver", "bronze", "copper"]}})
    customers_pin = rule["logical_sheet_id"]
    assert customers_pin

    # ---- 1. First run: the rule as authored passes ----
    run1 = await _validate(client, h, ds)
    assert _by_name(run1)["tier-accepted"]["status"] == "passed"
    assert run1["rules_passed"] == 1 and run1["rules_failed"] == 0

    # ---- 2. Tighten it: rename, re-describe, downgrade, re-parameterise ----
    r = await client.patch(f"/api/v1/datasets/{ds}/rules/{rule['id']}", headers=h,
                           json={"name": "tier-must-be-gold",
                                 "description": "gold only, for now",
                                 "severity": "warning",
                                 "parameters": {"values": ["gold"]}})
    assert r.status_code == 200, r.text
    patched = r.json()
    assert patched["name"] == "tier-must-be-gold"
    assert patched["description"] == "gold only, for now"
    assert patched["severity"] == "warning"
    assert patched["parameters"] == {"values": ["gold"]}
    # Untouched fields survive a partial update, and the id never moves.
    assert patched["id"] == rule["id"] and patched["column_selector"] == "tier"
    assert patched["sheet_selector"] == "customers"
    assert patched["logical_sheet_id"] == customers_pin
    assert patched["updated_at"] >= rule["updated_at"]

    # The drawer re-reads the rule it just saved (there is no cached copy).
    assert (await client.get(f"/api/v1/datasets/{ds}/rules/{rule['id']}",
                             headers=h)).json() == patched

    # ---- 3. Re-run: the verdict changed BECAUSE of the patch ----
    run2 = await _validate(client, h, ds)
    tightened = _by_name(run2)["tier-must-be-gold"]
    assert tightened["status"] == "failed"
    assert tightened["failure_count"] == 3          # silver, bronze, copper
    assert tightened["severity"] == "warning"
    assert run2["warning_failures"] == 1 and run2["error_failures"] == 0

    # ---- 4. The run already reviewed keeps the rule as it ran ----
    old = (await client.get(f"/api/v1/datasets/{ds}/validations/{run1['id']}",
                            headers=h)).json()
    assert [res["rule_name"] for res in old["results"]] == ["tier-accepted"]
    assert old["results"][0]["status"] == "passed"
    assert old["results"][0]["severity"] == "error"
    assert old["results"][0]["rule_id"] == rule["id"], (
        "the snapshot still links back to the live rule the drawer edits")

    # ---- 5. Retarget it at another sheet + column entirely ----
    r = await client.patch(f"/api/v1/datasets/{ds}/rules/{rule['id']}", headers=h,
                           json={"sheet_selector": "Orders",
                                 "column_selector": "customer_id",
                                 "parameters": {"values": [1, 2]},
                                 "severity": "error"})
    assert r.status_code == 200, r.text
    retargeted = r.json()
    assert retargeted["sheet_selector"] == "orders"
    assert retargeted["column_selector"] == "customer_id"
    assert retargeted["logical_sheet_id"] not in (None, customers_pin), (
        "re-targeting must re-pin, or the engine keeps evaluating the old sheet")

    # ---- 6. The run now answers about the NEW target ----
    run3 = await _validate(client, h, ds)
    moved = _by_name(run3)["tier-must-be-gold"]
    assert moved["sheet_selector"] == "orders"
    assert moved["column_selector"] == "customer_id"
    assert moved["status"] == "failed" and moved["failure_count"] == 1  # 999
    assert run3["error_failures"] == 1 and run3["warning_failures"] == 0

    # ---- 7. Three runs on one version, and still exactly one rule ----
    rules = (await client.get(f"/api/v1/datasets/{ds}/rules", headers=h)).json()
    assert rules["total"] == 1 and rules["items"][0] == retargeted
    history = (await client.get(f"/api/v1/datasets/{ds}/versions/1/validations",
                                headers=h)).json()
    assert history["total"] == 3
    assert [x["id"] for x in history["items"]] == [run3["id"], run2["id"], run1["id"]]


# ---------------------------------------------------------------------------
# 3. Release screen — the error/warning split
# ---------------------------------------------------------------------------


async def test_a_warning_severity_failure_is_reported_but_still_ships(
        client, admin_id, tmp_path):
    """SCREEN: "Promote to production" on the release panel.

    Severity is the whole reason the panel can show an amber row next to a
    green "Promote" button. A warning-severity rule that genuinely fails has to
    (a) count as a failure in the report — ``rules_failed`` and
    ``warning_failures`` both move — and (b) leave the promotion gate open,
    while an error-severity failure closes it with a code the panel can branch
    on. If warnings ever counted as blockers the panel would show an
    unexplainable 409; if they stopped being reported the amber row would
    vanish and a known data problem would ship invisibly.
    """
    editor, team = await create_team_user(client, admin_id, "editor")
    h = auth(editor)
    ds = await _orders_dataset(client, editor, tmp_path, clean=False, team_id=team)

    # A warning that fails on BOTH versions, and an error that only v1 breaks.
    await _rule(client, h, ds, {
        "name": "small-orders-are-suspicious", "rule_type": "range",
        "sheet_selector": "orders", "column_selector": "total",
        "parameters": {"min": 20}, "severity": "warning"})
    await _rule(client, h, ds, {
        "name": "customer-id-unique", "rule_type": "unique",
        "sheet_selector": "customers", "column_selector": "customer_id",
        "severity": "error"})

    # ---- 1. v1: one warning failure AND one error failure ----
    run1 = await _validate(client, h, ds, version=1)
    assert run1["rules_total"] == 2 and run1["rules_failed"] == 2
    assert run1["error_failures"] == 1 and run1["warning_failures"] == 1
    r1 = _by_name(run1)
    assert r1["small-orders-are-suspicious"]["status"] == "failed"
    assert r1["small-orders-are-suspicious"]["severity"] == "warning"
    assert r1["small-orders-are-suspicious"]["failure_count"] == 2   # 15.0 and 5.0
    assert r1["customer-id-unique"]["severity"] == "error"

    # ---- 2. The gate blocks v1, and says exactly why ----
    blocked = await client.post(f"/api/v1/datasets/{ds}/tags/production/promote",
                                headers=h, json={"version_number": 1,
                                                 "reason": "ship it anyway"})
    assert blocked.status_code == 409
    assert blocked.headers["content-type"].startswith(PROBLEM)
    problem = blocked.json()
    assert problem["code"] == "validation-failed"
    assert problem["error_failures"] == 1 and problem["warning_failures"] == 1
    assert problem["validation_run_id"] == run1["id"], (
        "the panel links 'see why' straight at the run that blocked it")
    assert (await client.get(f"/api/v1/datasets/{ds}/tags",
                             headers=h)).json()["total"] == 0

    # ---- 3. Fix the error (new version), leave the warning unfixed ----
    await _orders_dataset(client, editor, tmp_path, clean=True, team_id=team,
                          filename="orders_v2.xlsx", dataset_id=ds)
    run2 = await _validate(client, h, ds, version=2)
    assert run2["rules_total"] == 2
    assert run2["error_failures"] == 0
    assert run2["warning_failures"] == 1 and run2["rules_failed"] == 1
    r2 = _by_name(run2)
    assert r2["customer-id-unique"]["status"] == "passed"
    assert r2["small-orders-are-suspicious"]["status"] == "failed"
    assert r2["small-orders-are-suspicious"]["failure_count"] == 1   # 15.0

    # ---- 4. The gate opens: a reported failure that does not block ----
    shipped = await client.post(f"/api/v1/datasets/{ds}/tags/production/promote",
                                headers=h, json={"version_number": 2,
                                                 "reason": "warning accepted"})
    assert shipped.status_code == 200, shipped.text
    assert shipped.json()["to_version_number"] == 2
    tags = (await client.get(f"/api/v1/datasets/{ds}/tags", headers=h)).json()
    assert {t["tag_name"]: t["version_number"] for t in tags["items"]} == {
        "production": 2}

    # ---- 5. The shipped version still shows its amber row ----
    detail = (await client.get(f"/api/v1/datasets/{ds}/validations/{run2['id']}",
                               headers=h)).json()
    amber = _by_name(detail)["small-orders-are-suspicious"]
    assert amber["status"] == "failed" and amber["severity"] == "warning"
    assert amber["failure_sample_file"], (
        "a warning must still point at its failing rows — that is the whole "
        "value of shipping it knowingly")


# ---------------------------------------------------------------------------
# 4. Failure drill-down
# ---------------------------------------------------------------------------


async def test_an_analyst_drills_from_a_failed_rule_into_the_failing_rows(
        client, admin_id, tmp_path):
    """SCREEN: click a red row in the run-detail results table.

    The pointer the table renders comes from ``GET /validations/{run_id}``,
    which derives ``failure_sample_file`` from the registered artifact — a
    different code path from the one that answers the POST. Both must produce
    the same filename or "view failing rows" 404s on refresh. Registering the
    artifact is also what makes the file team-scoped: a teammate can open it, a
    stranger gets 404 rather than a 403 that would confirm it exists.
    """
    editor, team = await create_team_user(client, admin_id, "editor")
    viewer, _ = await create_team_user(client, admin_id, "viewer", team_id=team)
    outsider, _ = await create_team_user(client, admin_id, "editor")
    h = auth(editor)
    ds = await _orders_dataset(client, editor, tmp_path, clean=False, team_id=team)

    await _rule(client, h, ds, {
        "name": "customer-id-present", "rule_type": "not_null",
        "sheet_selector": "customers", "column_selector": "customer_id"})

    # ---- 1. Run it, then re-open the run the way a refresh would ----
    posted = await _validate(client, h, ds)
    fetched = await client.get(f"/api/v1/datasets/{ds}/validations/{posted['id']}",
                               headers=h)
    assert fetched.status_code == 200, fetched.text
    detail = fetched.json()
    assert detail == posted, "POST and GET describe the same run, field for field"

    result = _by_name(detail)["customer-id-present"]
    assert result["status"] == "failed" and result["failure_count"] == 1
    sample = result["failure_sample_file"]
    assert sample and result["failure_artifact_id"]

    # ---- 2. The drill-down table ----
    rows = await client.get(f"/api/v1/samples/{sample}/data", headers=h)
    assert rows.status_code == 200, rows.text
    data = rows.json()["data"]
    assert len(data) == 1 and data[0]["customer_id"] is None
    assert data[0]["tier"] == "bronze", (
        "the whole failing row is offered, not just the offending column")

    # ---- 3. "Download" on the same row ----
    dl = await client.get(f"/api/v1/samples/{sample}", headers=h)
    assert dl.status_code == 200, dl.text
    assert dl.content[:4] == b"PAR1"

    # ---- 4. The file browser knows what it is and who owns it ----
    files = (await client.get("/api/v1/samples", headers=h)).json()
    entry = next(f for f in files["items"] if f["filename"] == sample)
    assert entry["file_type"] == "validation_failures"
    assert entry["dataset_id"] == ds and entry["size_bytes"] > 0

    # ---- 5. A teammate with read-only access can drill down too ----
    hv = auth(viewer)
    assert (await client.get(f"/api/v1/datasets/{ds}/validations/{posted['id']}",
                             headers=hv)).status_code == 200
    seen = await client.get(f"/api/v1/samples/{sample}/data", headers=hv)
    assert seen.status_code == 200 and len(seen.json()["data"]) == 1

    # ---- 6. Somebody from another team sees nothing, and cannot tell ----
    ho = auth(outsider)
    for url in (f"/api/v1/datasets/{ds}/validations/{posted['id']}",
                f"/api/v1/samples/{sample}",
                f"/api/v1/samples/{sample}/data"):
        r = await client.get(url, headers=ho)
        assert r.status_code == 404, f"{url} -> {r.status_code}"
        assert r.headers["content-type"].startswith(PROBLEM)
        assert r.json()["code"] == "not_found"


# ---------------------------------------------------------------------------
# 5. Validation history table
# ---------------------------------------------------------------------------


async def test_a_dashboard_pages_through_a_versions_validation_history(
        client, admin_id, tmp_path):
    """SCREEN: the "Validation history" table on a version, 2 rows per page.

    A paged table needs three things from the envelope: ``total`` stays the
    full count while ``items`` shrinks to the page, page 2 is disjoint from
    page 1, and every row carries enough to render without a second call. It
    then has to hand the row's id to the detail endpoint when the user clicks
    it. Out-of-range paging must be a 422 the table can show as "bad request",
    not a silently clamped page that repeats rows forever.
    """
    editor, team = await create_team_user(client, admin_id, "editor")
    h = auth(editor)
    ds = await _orders_dataset(client, editor, tmp_path, clean=False, team_id=team)
    await _rule(client, h, ds, {
        "name": "orders-have-rows", "rule_type": "row_count_min",
        "sheet_selector": "orders", "parameters": {"min": 1}})

    # ---- 1. Five runs, oldest first as they happened ----
    run_ids = [(await _validate(client, h, ds))["id"] for _ in range(5)]
    assert len(set(run_ids)) == 5
    newest_first = list(reversed(run_ids))

    url = f"/api/v1/datasets/{ds}/versions/1/validations"

    # ---- 2. Page 1 ----
    p1 = await client.get(url, headers=h, params={"limit": 2, "offset": 0})
    assert p1.status_code == 200, p1.text
    page1 = p1.json()
    assert page1["total"] == 5 and page1["limit"] == 2 and page1["offset"] == 0
    assert len(page1["items"]) == 2
    assert [x["id"] for x in page1["items"]] == newest_first[:2]

    # Each row renders the summary the table shows, with no follow-up call.
    row = page1["items"][0]
    assert row["status"] == "completed" and row["completed_at"]
    assert row["rules_total"] == 1 and row["rules_passed"] == 1
    assert row["rules_failed"] == 0 and row["error_failures"] == 0
    assert row["warning_failures"] == 0 and row["error"] is None
    assert row["triggered_by"] == editor and row["job_id"]
    assert row["dataset_id"] == ds and row["started_at"]

    # ---- 3. Page 2 is disjoint, total unchanged ----
    page2 = (await client.get(url, headers=h,
                              params={"limit": 2, "offset": 2})).json()
    assert page2["total"] == 5 and page2["offset"] == 2
    assert [x["id"] for x in page2["items"]] == newest_first[2:4]
    assert not ({x["id"] for x in page1["items"]}
                & {x["id"] for x in page2["items"]})

    # ---- 4. Last page is short; past the end is empty, not an error ----
    page3 = (await client.get(url, headers=h,
                              params={"limit": 2, "offset": 4})).json()
    assert [x["id"] for x in page3["items"]] == newest_first[4:]
    beyond = (await client.get(url, headers=h,
                               params={"limit": 2, "offset": 10})).json()
    assert beyond["items"] == [] and beyond["total"] == 5

    # ---- 5. Out-of-range paging is a typed 422 ----
    for params in ({"limit": 0}, {"limit": 201}, {"offset": -1},
                   {"limit": "all"}):
        r = await client.get(url, headers=h, params=params)
        assert r.status_code == 422, f"{params} -> {r.status_code}"
        assert r.headers["content-type"].startswith(PROBLEM)
        assert r.json()["errors"], "the table needs to know which field was wrong"

    # ---- 6. Clicking a row opens exactly that run ----
    clicked = page2["items"][0]["id"]
    opened = await client.get(f"/api/v1/datasets/{ds}/validations/{clicked}",
                              headers=h)
    assert opened.status_code == 200, opened.text
    assert opened.json()["id"] == clicked
    # The detail document is a superset of the list row, so the table's data
    # can be reused for the header of the page it opens.
    assert set(page2["items"][0]) <= set(opened.json())
    assert all(opened.json()[k] == v for k, v in page2["items"][0].items())
    assert len(opened.json()["results"]) == 1

    # A run id from another dataset is not openable here.
    other = await _orders_dataset(client, editor, tmp_path, clean=True,
                                  team_id=team, filename="other.xlsx")
    r = await client.get(f"/api/v1/datasets/{other}/validations/{clicked}", headers=h)
    assert r.status_code == 404 and r.headers["content-type"].startswith(PROBLEM)


# ---------------------------------------------------------------------------
# 6. Gate arming
# ---------------------------------------------------------------------------


async def test_turning_the_last_rule_off_disarms_the_gate_and_turning_it_on_rearms_it(
        client, admin_id, tmp_path):
    """FLOW: the escape hatch a steward reaches for when a release is stuck.

    The gate is armed by ``count_enabled_rules > 0``. Disabling the last rule
    has to turn a hard 409 into a successful promote, and re-enabling it has to
    put the block back — otherwise the toggle in the rules table is decorative
    and the steward's only way out is a raw tag write. The same toggle also
    flips ``POST /validate`` between 200 and "no enabled quality rules", which
    is what greys out the "Run validation" button.
    """
    editor, team = await create_team_user(client, admin_id, "editor")
    h = auth(editor)
    ds = await _orders_dataset(client, editor, tmp_path, clean=False, team_id=team)

    rule = await _rule(client, h, ds, {
        "name": "customer-id-unique", "rule_type": "unique",
        "sheet_selector": "customers", "column_selector": "customer_id"})

    # ---- 1. Armed: the failing run blocks the promote ----
    run = await _validate(client, h, ds)
    assert run["error_failures"] == 1
    r = await client.post(f"/api/v1/datasets/{ds}/tags/production/promote",
                          headers=h, json={"version_number": 1})
    assert r.status_code == 409 and r.json()["code"] == "validation-failed"

    # ---- 2. Disable the last rule → the gate is disarmed ----
    off = await client.patch(f"/api/v1/datasets/{ds}/rules/{rule['id']}",
                             headers=h, json={"enabled": False})
    assert off.status_code == 200 and off.json()["enabled"] is False
    # It is still listed — disabled, not deleted.
    listing = (await client.get(f"/api/v1/datasets/{ds}/rules", headers=h)).json()
    assert listing["total"] == 1 and listing["items"][0]["enabled"] is False

    r = await client.post(f"/api/v1/datasets/{ds}/tags/production/promote",
                          headers=h, json={"version_number": 1,
                                           "reason": "rule disabled, shipping"})
    assert r.status_code == 200, r.text
    assert r.json()["to_version_number"] == 1

    # ---- 3. With nothing enabled, validation has nothing to run ----
    r = await client.post(f"/api/v1/datasets/{ds}/versions/1/validate", headers=h)
    assert r.status_code == 400
    assert r.headers["content-type"].startswith(PROBLEM)
    assert "no enabled" in r.json()["detail"]
    # ...and it left no run behind for the history table.
    assert (await client.get(f"/api/v1/datasets/{ds}/versions/1/validations",
                             headers=h)).json()["total"] == 1

    # ---- 4. Re-enable → the block is back, from the SAME stored run ----
    on = await client.patch(f"/api/v1/datasets/{ds}/rules/{rule['id']}",
                            headers=h, json={"enabled": True})
    assert on.status_code == 200 and on.json()["enabled"] is True
    r = await client.post(f"/api/v1/datasets/{ds}/tags/production/promote",
                          headers=h, json={"version_number": 1})
    assert r.status_code == 409 and r.json()["code"] == "validation-failed"
    assert r.json()["validation_run_id"] == run["id"]
    # Validation is runnable again, and the history grows.
    again = await _validate(client, h, ds)
    assert again["rules_total"] == 1 and again["error_failures"] == 1
    assert (await client.get(f"/api/v1/datasets/{ds}/versions/1/validations",
                             headers=h)).json()["total"] == 2

    # ---- 5. Deleting the last rule disarms it permanently ----
    assert (await client.delete(f"/api/v1/datasets/{ds}/rules/{rule['id']}",
                                headers=h)).status_code == 204
    assert (await client.get(f"/api/v1/datasets/{ds}/rules",
                             headers=h)).json()["total"] == 0
    assert (await client.delete(f"/api/v1/datasets/{ds}/rules/{rule['id']}",
                                headers=h)).status_code == 404
    r = await client.post(f"/api/v1/datasets/{ds}/tags/production/promote",
                          headers=h, json={"version_number": 1,
                                           "reason": "contract withdrawn"})
    assert r.status_code == 200, r.text

    # ---- 6. The deleted rule's past results survive as a snapshot ----
    old = (await client.get(f"/api/v1/datasets/{ds}/validations/{run['id']}",
                            headers=h)).json()
    assert [x["rule_name"] for x in old["results"]] == ["customer-id-unique"]
    assert old["results"][0]["rule_id"] is None, (
        "the live rule is gone; the historical row keeps its own copy")


# ---------------------------------------------------------------------------
# 7. RBAC
# ---------------------------------------------------------------------------


async def test_an_editor_authors_rules_while_a_viewer_only_reads_and_an_outsider_sees_nothing(
        client, admin_id, tmp_path):
    """SCREEN: the Quality tab rendered for three different people.

    A viewer must see the whole tab — rules, history, run detail — with every
    control disabled; the API has to back that with 403, because a UI that
    optimistically enables a button needs the error to say "you may not", not
    "it does not exist". An outsider must get 404 everywhere, including on the
    mutations, so probing cannot confirm the dataset exists. ``validate``
    needing ``dataset:write`` rather than ``dataset:read`` is the surprising
    one: "Run validation" is a read-looking button that a viewer cannot press.
    """
    editor, team = await create_team_user(client, admin_id, "editor")
    viewer, _ = await create_team_user(client, admin_id, "viewer", team_id=team)
    outsider, _ = await create_team_user(client, admin_id, "admin")
    he, hv, ho = auth(editor), auth(viewer), auth(outsider)
    ds = await _orders_dataset(client, editor, tmp_path, clean=False, team_id=team)

    rule = await _rule(client, he, ds, {
        "name": "customer-id-present", "rule_type": "not_null",
        "sheet_selector": "customers", "column_selector": "customer_id"})
    run = await _validate(client, he, ds)

    rules_url = f"/api/v1/datasets/{ds}/rules"
    rule_url = f"{rules_url}/{rule['id']}"

    # ---- 1. The viewer's tab renders completely ----
    listed = await client.get(rules_url, headers=hv)
    assert listed.status_code == 200 and listed.json()["total"] == 1
    assert listed.json()["items"][0]["id"] == rule["id"]

    one = await client.get(rule_url, headers=hv)
    assert one.status_code == 200 and one.json()["name"] == "customer-id-present"

    history = await client.get(f"/api/v1/datasets/{ds}/versions/1/validations",
                               headers=hv)
    assert history.status_code == 200 and history.json()["total"] == 1

    detail = await client.get(f"/api/v1/datasets/{ds}/validations/{run['id']}",
                              headers=hv)
    assert detail.status_code == 200
    assert detail.json()["results"][0]["failure_count"] == 1

    # ---- 2. Every control on it is refused, and says so honestly ----
    forbidden = [
        ("POST", rules_url, {"name": "sneaky", "rule_type": "row_count_min",
                             "sheet_selector": "orders"}),
        ("PATCH", rule_url, {"enabled": False}),
        ("DELETE", rule_url, None),
        ("POST", f"/api/v1/datasets/{ds}/versions/1/validate", None),
    ]
    for method, url, body in forbidden:
        r = await client.request(method, url, headers=hv,
                                 **({"json": body} if body is not None else {}))
        assert r.status_code == 403, f"{method} {url} -> {r.status_code} {r.text}"
        assert r.headers["content-type"].startswith(PROBLEM)
        assert r.json()["code"] == "forbidden"

    # Nothing the viewer tried changed anything.
    assert (await client.get(rules_url, headers=he)).json()["total"] == 1
    assert (await client.get(rule_url, headers=he)).json()["enabled"] is True
    assert (await client.get(f"/api/v1/datasets/{ds}/versions/1/validations",
                             headers=he)).json()["total"] == 1

    # ---- 3. An outsider — a team ADMIN elsewhere — gets 404 on everything ----
    probes = [
        ("GET", rules_url, None),
        ("GET", rule_url, None),
        ("POST", rules_url, {"name": "sneaky", "rule_type": "row_count_min",
                             "sheet_selector": "orders"}),
        ("PATCH", rule_url, {"enabled": False}),
        ("DELETE", rule_url, None),
        ("POST", f"/api/v1/datasets/{ds}/versions/1/validate", None),
        ("GET", f"/api/v1/datasets/{ds}/versions/1/validations", None),
        ("GET", f"/api/v1/datasets/{ds}/validations/{run['id']}", None),
    ]
    for method, url, body in probes:
        r = await client.request(method, url, headers=ho,
                                 **({"json": body} if body is not None else {}))
        assert r.status_code == 404, f"{method} {url} -> {r.status_code} {r.text}"
        assert r.headers["content-type"].startswith(PROBLEM)
        assert r.json()["code"] == "not_found"

    # ---- 4. The editor still has every control ----
    assert (await client.patch(rule_url, headers=he,
                               json={"enabled": False})).status_code == 200
    assert (await client.delete(rule_url, headers=he)).status_code == 204


# ---------------------------------------------------------------------------
# 8. A version that never became ready
# ---------------------------------------------------------------------------


async def test_a_version_that_failed_to_ingest_cannot_be_validated_and_strands_nothing(
        client, admin_id, tmp_path):
    """FLOW: an upload fails, and the steward retries validation on it anyway.

    A version that never reached ``ready`` has no parquet to query. The refusal
    must arrive as a 409 the UI can distinguish from "no such version" (404)
    and from "no rules" (400) — three different things to say on one button.
    The second half matters more: the check runs BEFORE the job and run rows
    are created, so a rejected validate must leave the history table empty. If
    it ever moved below job creation, every retry would strand a 'running' run
    and the version would look permanently mid-validation.
    """
    editor, team = await create_team_user(client, admin_id, "editor")
    h = auth(editor)
    ds = await _orders_dataset(client, editor, tmp_path, clean=True, team_id=team)
    await _rule(client, h, ds, {
        "name": "orders-have-rows", "rule_type": "row_count_min",
        "sheet_selector": "orders", "parameters": {"min": 1}})

    good = await _validate(client, h, ds, version=1)
    assert good["status"] == "completed"
    jobs_before = (await client.get("/api/v1/jobs", headers=h,
                                    params={"job_type": "validation"})).json()
    assert jobs_before["total"] == 1

    # ---- 1. The next upload onto this dataset is unparseable ----
    bad = await client.post("/api/v1/upload",
                            headers={**h, "X-Team-Id": team},
                            files={"file": ("orders.xlsx",
                                            io.BytesIO(b"not a zip archive"),
                                            "application/octet-stream")},
                            data={"dataset_id": ds})
    assert bad.status_code == 400, bad.text
    assert bad.json()["code"] == "invalid-file"

    versions = (await client.get(f"/api/v1/datasets/{ds}/versions",
                                 headers=h)).json()
    v2 = next(v for v in versions["items"] if v["version_number"] == 2)
    assert v2["status"] != "ready", (
        "the failed ingest is visible in the versions list; the UI offers it")

    # ---- 2. Validating it is a 409, distinct from 404 and from 400 ----
    r = await client.post(f"/api/v1/datasets/{ds}/versions/2/validate", headers=h)
    assert r.status_code == 409, r.text
    assert r.headers["content-type"].startswith(PROBLEM)
    assert r.json()["code"] == "conflict"
    assert "not ready" in r.json()["detail"] and v2["status"] in r.json()["detail"]

    # A version that does not exist is still a 404 — a different message.
    missing = await client.post(f"/api/v1/datasets/{ds}/versions/99/validate",
                                headers=h)
    assert missing.status_code == 404 and missing.json()["code"] == "not_found"

    # ---- 3. Nothing was stranded: no run row, no job row ----
    hist = await client.get(f"/api/v1/datasets/{ds}/versions/2/validations",
                            headers=h)
    assert hist.status_code == 200, hist.text
    assert hist.json()["total"] == 0 and hist.json()["items"] == []

    jobs_after = (await client.get("/api/v1/jobs", headers=h,
                                   params={"job_type": "validation"})).json()
    assert jobs_after["total"] == jobs_before["total"], (
        "a rejected validate must not create a job it will never finish")
    assert all(j["status"] != "running" for j in jobs_after["items"])

    # ---- 4. v1 is untouched and still validates ----
    assert (await client.get(f"/api/v1/datasets/{ds}/versions/1/validations",
                             headers=h)).json()["total"] == 1
    retry = await _validate(client, h, ds, version=1)
    assert retry["status"] == "completed" and retry["id"] != good["id"]

    # ---- 5. Promotion refuses the same version for the same reason ----
    promote = await client.post(f"/api/v1/datasets/{ds}/tags/production/promote",
                                headers=h, json={"version_number": 2})
    assert promote.status_code == 409
    assert "Cannot promote" in promote.json()["detail"]
