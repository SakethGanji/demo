"""Explorer journeys — every step in the order the Explorer screens drive it.

Five journeys against the real app + Postgres + storage backend, each one
modelled on a screen a frontend engineer is about to build:

1. **Workbook explorer** — sheet picker → sheet preview → column drawer →
   grid query with cursor paging → quality tabs → save the grid as a view →
   the views list → RBAC (viewer vs outsider).
2. **View editor** — hydrate the edit form, rename, a rejected retarget that
   must leave nothing behind, pin a version, the two 409 name collisions,
   delete, and the second delete a double-clicked button sends.
3. **Data-quality tab** — badge → profile run → report → drill into the grid,
   with every number on the screen required to agree with the others.
4. **A saved view going stale** — a new version drops the column the stored
   query sorts on; the run 400s, and the screen has to be able to recover
   without deleting (and so re-issuing the id of) the view.
5. **SQL console** — ad-hoc SELECT, the persisted result artifact, who can
   fetch it, and who can create one.

Each journey is one test function so ordering is explicit and state flows step
to step, exactly like a browser session: step N asserts something that is only
true *because* of step N-1.
"""

from __future__ import annotations

import json

from openpyxl import Workbook

from conftest import (
    DEFAULT_TEAM_ID,
    XLSX_MIME,
    auth,
    create_team_user,
    make_crm_workbook,
    upload_file,
    upload_inline,
)

PROBLEM = "application/problem+json"


def _make_ledger_workbook(path):
    """Revenue (duplicate headers, nulls, one exact duplicate row) + Expenses +
    a hidden Secrets sheet — one workbook that drives every explorer surface."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Revenue"
    ws.append(["Region", "Amount", "Amount", "Notes"])  # dup header -> amount_2
    for row in (["EU", 100, 1, "q1"],
                ["EU", 200, 2, None],
                ["US", 250, 3, "q2"],
                ["APAC", 90, 4, None],
                ["EU", 200, 2, None]):        # exact duplicate of row 2
        ws.append(row)
    costs = wb.create_sheet("Expenses")
    costs.append(["Item", "Cost"])
    costs.append(["rent", 50])
    costs.append(["power", 20])
    secrets = wb.create_sheet("Secrets")
    secrets.append(["K", "V"])
    secrets.append(["k1", "v1"])
    secrets.sheet_state = "hidden"
    wb.save(path)


# ---------------------------------------------------------------------------
# 1. The workbook explorer screen
# ---------------------------------------------------------------------------

async def test_journey_an_analyst_explores_a_workbook_sheet_by_sheet_and_saves_the_grid_as_a_view(
        client, admin_id, tmp_path):
    """SCREEN: Explorer, opened on a multi-sheet workbook.

    On mount the screen has a dataset id and nothing else. It must discover the
    sheets, discover that it may not query without picking one, render a grid,
    page it, open the column drawer, read the two quality tabs, and finally
    persist the grid as a saved view that another user can open by id.

    If this regresses the explorer is unusable on any workbook: either the
    picker cannot be built (no sheet list / no normalized column names), or the
    grid pages into the wrong rows, or the saved view cannot be reopened.
    """
    editor, team = await create_team_user(client, admin_id, "editor")
    h = auth(editor)
    path = tmp_path / "ledger.xlsx"
    _make_ledger_workbook(path)
    ds = (await upload_file(client, editor, path, name="ledger.xlsx",
                            content_type=XLSX_MIME, team_id=team))["dataset_id"]

    # ---- 1. Mount: what sheets are there, and which is the default tab? ----
    meta = (await client.get(f"/api/v1/datasets/{ds}", headers=h)).json()
    assert meta["default_sheet"] == "Revenue"
    assert {s["name"] for s in meta["sheets"]} == {"Revenue", "Expenses", "Secrets"}

    # ---- 2. The grid asks for the version's rows without naming a sheet ----
    # This is the call a single-table UI makes; on a workbook it is the signal
    # to render the picker, and `sheets` is what fills it.
    r = await client.get(f"/api/v1/datasets/{ds}/versions/1/preview", headers=h)
    assert r.status_code == 400, r.text
    assert r.headers["content-type"].startswith(PROBLEM)
    assert r.json()["code"] == "sheet-selection-required"
    assert set(r.json()["sheets"]) == {"Revenue", "Expenses", "Secrets"}

    # ---- 3. Picker: the version-scoped sheet list carries the column names ----
    picker = (await client.get(f"/api/v1/datasets/{ds}/versions/1/sheets",
                               headers=h)).json()
    assert picker["total"] == 3
    revenue = next(s for s in picker["items"] if s["name"] == "Revenue")
    assert revenue["row_count"] == 5
    by_norm = {c["normalized_name"]: c for c in revenue["columns"]}
    assert set(by_norm) == {"region", "amount", "amount_2", "notes"}
    # The duplicated header is disambiguated for the UI, and the physical name
    # it must send back for a projection is right there beside it.
    assert by_norm["amount_2"]["name"] == "Amount.1"
    revenue_logical_id = revenue["logical_sheet_id"]
    assert revenue_logical_id

    # ---- 4. Pick Revenue: the grid's first page ----
    r = await client.get(
        f"/api/v1/datasets/{ds}/versions/1/sheets/Revenue/preview",
        params={"limit": 3}, headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 5 and len(body["items"]) == 3
    # Rows come back keyed by PHYSICAL names, not the normalized ones the
    # picker just handed over — the grid has to map header -> key itself.
    assert set(body["items"][0]) == {"Region", "Amount", "Amount.1", "Notes"}
    assert body["masked_columns"] == []

    # ---- 5. Column drawer on the deduplicated header ----
    r = await client.get(
        f"/api/v1/datasets/{ds}/versions/1/sheets/Revenue/columns/amount_2",
        headers=h)
    assert r.status_code == 200, r.text
    col = r.json()
    assert col["normalized_name"] == "amount_2" and col["name"] == "Amount.1"
    assert col["sheet_name"] == "Revenue"
    assert col["count"] == 5 and col["null_count"] == 0
    assert col["unique_count"] == 4 and col["uniqueness"] == 0.8
    assert col["is_candidate_key"] is False   # row 5 repeats line 2
    assert col["min"] == 1 and col["max"] == 4

    # ---- 6. Filter + sort the grid, then page it with the cursor ----
    spec = {"columns": ["region", "amount"],
            "filters": {"conditions": [
                {"column": "region", "op": "eq", "value": "EU"}]},
            "sort": [{"column": "amount", "direction": "desc"}],
            "limit": 2}
    url = f"/api/v1/datasets/{ds}/versions/1/sheets/Revenue/query"
    page1 = (await client.post(url, headers=h, json=spec)).json()
    assert page1["total"] == 3               # 3 EU rows of the 5
    assert [i["amount"] for i in page1["items"]] == [200, 200]
    assert set(page1["items"][0]) == {"region", "amount"}  # projection aliases
    assert page1["next_cursor"]

    page2 = (await client.post(url, headers=h,
                               json={**spec, "cursor": page1["next_cursor"]})).json()
    assert [i["amount"] for i in page2["items"]] == [100]
    assert page2["next_cursor"] is None      # the grid stops fetching here
    assert page2["total"] == 3               # the footer count does not drift

    # ---- 7. Quality tabs for the sheet on screen ----
    dups = (await client.get(
        f"/api/v1/datasets/{ds}/versions/1/sheets/Revenue/duplicates",
        headers=h)).json()
    assert dups["sheet_name"] == "Revenue" and dups["exact"] is True
    assert dups["columns"] == ["region", "amount", "amount_2", "notes"]
    assert dups["row_count"] == 5
    assert dups["group_count"] == 1 and dups["duplicate_rows"] == 2
    assert dups["truncated"] is False
    group = dups["groups"][0]
    assert group["count"] == 2 and group["key"]["region"] == "EU"
    assert group["key"]["amount"] == 200 and group["key"]["notes"] is None
    assert len(group["examples"]) == 2

    miss = (await client.get(
        f"/api/v1/datasets/{ds}/versions/1/sheets/Revenue/missing",
        headers=h)).json()
    assert miss["sheet_name"] == "Revenue"
    assert miss["source"] == "computed" and miss["profile_run_id"] is None
    assert miss["row_count"] == 5
    assert miss["columns"][0] == {"column": "notes", "null_count": 3,
                                  "null_percent": 60.0}
    assert all(c["null_count"] == 0 for c in miss["columns"][1:])
    assert [row["null_count"] for row in miss["rows_most_missing"]] == [1, 1, 1]

    # The other tab of the same workbook has nothing to report — the empty
    # state the screen must render rather than a spinner.
    other = (await client.get(
        f"/api/v1/datasets/{ds}/versions/1/sheets/Expenses/missing",
        headers=h)).json()
    assert other["row_count"] == 2 and other["rows_most_missing"] == []

    # ---- 8. Save the grid exactly as configured ----
    r = await client.post(f"/api/v1/datasets/{ds}/views", headers=h,
                          json={"name": "eu-ledger",
                                "description": "EU rows, biggest first",
                                "sheet": "Revenue",
                                "query": spec})
    assert r.status_code == 201, r.text
    view = r.json()
    view_id = view["id"]
    assert view["sheet_name"] == "Revenue" and view["sheet_key"] == "revenue"
    # The view is keyed on the same logical sheet the picker named, so the UI
    # can highlight the right tab when it reopens the view.
    assert view["logical_sheet_id"] == revenue_logical_id
    assert view["version_selector"] == {"mode": "current"}
    assert "cursor" not in view["query"]     # per-run paging is never stored
    assert view["created_by"] == editor

    listed = (await client.get(f"/api/v1/datasets/{ds}/views", headers=h)).json()
    assert listed["total"] == 1 and listed["items"][0]["id"] == view_id
    assert listed["limit"] == 50 and listed["offset"] == 0

    # ---- 9. Reopen it by id and re-run it: the grid comes back identical ----
    fetched = (await client.get(f"/api/v1/datasets/{ds}/views/{view_id}",
                                headers=h)).json()
    assert fetched["query"]["columns"] == ["region", "amount"]
    assert fetched["query"]["limit"] == 2
    assert fetched["description"] == "EU rows, biggest first"

    run = (await client.post(f"/api/v1/datasets/{ds}/views/{view_id}/run",
                             headers=h)).json()
    assert run["view_id"] == view_id
    assert run["version_number"] == 1 and run["sheet_name"] == "Revenue"
    assert run["result"]["items"] == page1["items"]   # byte-for-byte the grid
    assert run["result"]["total"] == 3

    # A "show all" button overrides the stored page size without editing it.
    run_all = (await client.post(f"/api/v1/datasets/{ds}/views/{view_id}/run",
                                 headers=h, json={"limit": 10})).json()
    assert [i["amount"] for i in run_all["result"]["items"]] == [200, 200, 100]
    assert run_all["result"]["next_cursor"] is None
    assert (await client.get(f"/api/v1/datasets/{ds}/views/{view_id}",
                             headers=h)).json()["query"]["limit"] == 2

    # ---- 10. RBAC the screen has to reflect ----
    viewer, _ = await create_team_user(client, admin_id, "viewer", team_id=team)
    hv = auth(viewer)
    # A viewer explores and runs saved views, but the Save button must be off.
    assert (await client.get(
        f"/api/v1/datasets/{ds}/versions/1/sheets/Revenue/preview",
        headers=hv)).status_code == 200
    assert (await client.get(f"/api/v1/datasets/{ds}/views/{view_id}",
                             headers=hv)).status_code == 200
    assert (await client.post(f"/api/v1/datasets/{ds}/views/{view_id}/run",
                              headers=hv)).status_code == 200
    r = await client.post(f"/api/v1/datasets/{ds}/views", headers=hv,
                          json={"name": "nope", "sheet": "Revenue", "query": {}})
    assert r.status_code == 403                        # in-team: truthful 403
    assert (await client.patch(f"/api/v1/datasets/{ds}/views/{view_id}",
                               headers=hv, json={"name": "x"})).status_code == 403
    assert (await client.delete(f"/api/v1/datasets/{ds}/views/{view_id}",
                                headers=hv)).status_code == 403

    outsider, _ = await create_team_user(client, admin_id, "editor")
    ho = auth(outsider)
    for method, path_, kw in (
        ("get", f"/api/v1/datasets/{ds}/versions/1/sheets/Revenue/preview", {}),
        ("get", f"/api/v1/datasets/{ds}/versions/1/sheets/Revenue/columns/amount", {}),
        ("get", f"/api/v1/datasets/{ds}/versions/1/sheets/Revenue/duplicates", {}),
        ("get", f"/api/v1/datasets/{ds}/versions/1/sheets/Revenue/missing", {}),
        ("get", f"/api/v1/datasets/{ds}/views", {}),
        ("get", f"/api/v1/datasets/{ds}/views/{view_id}", {}),
        ("post", f"/api/v1/datasets/{ds}/views/{view_id}/run", {}),
        ("post", f"/api/v1/datasets/{ds}/versions/1/sheets/Revenue/query",
         {"json": {}}),
    ):
        r = await getattr(client, method)(path_, headers=ho, **kw)
        assert r.status_code == 404, (path_, r.status_code)  # existence hidden

    # ---- 11. A sheet that isn't there is a 404, not an empty grid ----
    r = await client.get(f"/api/v1/datasets/{ds}/versions/1/sheets/Ghost/preview",
                         headers=h)
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# 2. The saved-view editor screen
# ---------------------------------------------------------------------------

EDITOR_ROWS = [
    {"id": 1, "name": "alpha", "score": 10.5},
    {"id": 2, "name": "beta", "score": 20.0},
    {"id": 3, "name": "gamma", "score": 30.25},
    {"id": 4, "name": "delta", "score": 40.0},
]


async def test_journey_a_saved_view_is_edited_field_by_field_and_a_rejected_edit_leaves_nothing_behind(
        client, admin_id):
    """SCREEN: the saved-view edit dialog (rename / retarget / delete).

    Every field on the dialog is its own PATCH, and the dialog stays open after
    a failure, so a rejected save must not have half-applied. The two distinct
    409s (create-with-a-taken-name, rename-onto-a-taken-name) are the ones the
    dialog renders inline against the name field; the second DELETE is what a
    double-clicked button sends.

    Regressions here corrupt saved state silently: a partially applied PATCH,
    or a 500 instead of a 409, both leave the user's list of views wrong.
    """
    h = auth(admin_id)
    ds = (await upload_inline(client, admin_id,
                              json.dumps(EDITOR_ROWS)))["dataset_id"]
    base = f"/api/v1/datasets/{ds}/views"
    original_query = {
        "filters": {"conditions": [{"column": "score", "op": "gt", "value": 15}]},
        "sort": [{"column": "score", "direction": "desc"}],
        "limit": 2,
    }

    # ---- 1. Two views exist; the second one owns a name we will collide with ----
    a = (await client.post(base, headers=h,
                           json={"name": "high-scores", "description": "top rows",
                                 "sheet": "data", "query": original_query})).json()
    b = (await client.post(base, headers=h,
                           json={"name": "everything", "sheet": "data",
                                 "query": {}})).json()
    vid = a["id"]
    assert (await client.get(base, headers=h)).json()["total"] == 2

    # ---- 2. Hydrate the edit form from the view, not from a cached list ----
    form = (await client.get(f"{base}/{vid}", headers=h)).json()
    assert form["name"] == "high-scores" and form["description"] == "top rows"
    # The stored query is the normalized form of what was sent — every default
    # made explicit — so the dialog can bind it to its widgets without guessing.
    assert form["query"]["sort"] == original_query["sort"]
    assert form["query"]["limit"] == 2
    assert form["query"]["columns"] is None and form["query"]["search"] is None
    assert form["query"]["filters"]["logic"] == "and"
    assert form["query"]["filters"]["conditions"] == [
        {"column": "score", "op": "gt", "value": 15, "case_sensitive": True}]
    assert form["version_selector"] == {"mode": "current"}
    assert form["created_at"] and form["updated_at"]

    # ---- 3. Rename only: the stored query must not be touched ----
    r = await client.patch(f"{base}/{vid}", headers=h, json={"name": "top-scores"})
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "top-scores"
    assert r.json()["query"] == form["query"]
    assert r.json()["description"] == "top rows"

    # ---- 4. A retarget the screen builds wrong is refused, atomically ----
    r = await client.patch(f"{base}/{vid}", headers=h,
                           json={"name": "renamed-too",
                                 "query": {"columns": ["nope"]}})
    assert r.status_code == 400, r.text
    assert r.headers["content-type"].startswith(PROBLEM)
    assert r.json()["code"] == "unknown-column" and r.json()["column"] == "nope"
    assert "score" in r.json()["available"]   # what the field picker should offer

    after = (await client.get(f"{base}/{vid}", headers=h)).json()
    assert after["name"] == "top-scores"      # the name in the SAME patch body
    assert after["query"] == form["query"]    # ...and the query: nothing landed

    # ---- 5. Pin the view to v1, then prove the pin by shipping a v2 ----
    await upload_inline(client, admin_id,
                        json.dumps(EDITOR_ROWS + [{"id": 5, "name": "epsilon",
                                                   "score": 99.0}]),
                        dataset_id=ds)
    r = await client.patch(f"{base}/{vid}", headers=h,
                           json={"version_selector": {"mode": "version",
                                                      "version_number": 1}})
    assert r.status_code == 200, r.text
    assert r.json()["version_selector"] == {"mode": "version", "version_number": 1}

    pinned_run = (await client.post(f"{base}/{vid}/run", headers=h)).json()
    assert pinned_run["version_number"] == 1
    assert pinned_run["result"]["total"] == 3          # v1 rows over 15
    following_run = (await client.post(f"{base}/{b['id']}/run", headers=h)).json()
    assert following_run["version_number"] == 2        # mode 'current' moved
    assert following_run["result"]["total"] == 5

    # ---- 6. Both name collisions come back as 409, inline on the field ----
    r = await client.post(base, headers=h,
                          json={"name": "top-scores", "sheet": "data", "query": {}})
    assert r.status_code == 409, r.text
    r = await client.patch(f"{base}/{vid}", headers=h, json={"name": b["name"]})
    assert r.status_code == 409, r.text
    assert (await client.get(f"{base}/{vid}", headers=h)).json()["name"] == "top-scores"
    assert (await client.get(base, headers=h)).json()["total"] == 2  # no ghost row

    # ---- 7. Delete, and the delete the double-clicked button repeats ----
    assert (await client.delete(f"{base}/{vid}", headers=h)).status_code == 204
    r = await client.delete(f"{base}/{vid}", headers=h)
    assert r.status_code == 404, r.text
    assert (await client.get(f"{base}/{vid}", headers=h)).status_code == 404
    r = await client.post(f"{base}/{vid}/run", headers=h)
    assert r.status_code == 404
    r = await client.patch(f"{base}/{vid}", headers=h, json={"name": "zombie"})
    assert r.status_code == 404

    remaining = (await client.get(base, headers=h)).json()
    assert remaining["total"] == 1 and remaining["items"][0]["id"] == b["id"]

    # ---- 8. The freed name is immediately reusable ----
    r = await client.post(base, headers=h,
                          json={"name": "top-scores", "sheet": "data",
                                "query": original_query})
    assert r.status_code == 201, r.text
    assert r.json()["id"] != vid              # a new resource, a new id

    # ---- 9. A stale bookmark carrying a non-uuid id is a 404, never a 500 ----
    for method, kwargs in (("get", {}), ("delete", {}),
                           ("patch", {"json": {"name": "x"}})):
        r = await getattr(client, method)(f"{base}/not-a-uuid", headers=h, **kwargs)
        assert r.status_code == 404, (method, r.status_code, r.text)
    assert (await client.post(f"{base}/not-a-uuid/run",
                              headers=h)).status_code == 404

    # ...and creating a view against a sheet the dialog offered but that is not
    # on this dataset fails before anything is stored.
    r = await client.post(base, headers=h,
                          json={"name": "ghost-sheet", "sheet": "ghost",
                                "query": {}})
    assert r.status_code == 404, r.text
    # ...and it says which of the screen's two 404s this is: "the sheet picker
    # is stale, refresh it" rather than the generic `not_found` the dialog also
    # gets when the dataset itself is gone and it must navigate away.
    assert r.json()["code"] == "sheet-not-in-version", r.json()
    assert r.json()["available"] == ["data"]     # what the picker should offer
    assert (await client.get(base, headers=h)).json()["total"] == 2


# ---------------------------------------------------------------------------
# 3. The data-quality tab
# ---------------------------------------------------------------------------

QUALITY_ROWS = [
    {"account": "A1", "region": "EU", "balance": 100.0},
    {"account": "A1", "region": "EU", "balance": 100.0},   # exact duplicate
    {"account": "A2", "region": "US", "balance": None},
    {"account": "A3", "region": None, "balance": None},
    {"account": "A4", "region": "EU", "balance": 50.0},
]


async def test_journey_the_quality_tab_profiles_a_version_then_drills_from_the_badge_into_the_grid(
        client, admin_id):
    """SCREEN: the Data Quality tab — badge → profile run → report → grid.

    The tab shows several numbers computed by different code paths on the same
    version (the live missing probe, the persisted profile run, the duplicate
    report, and the grid's own total). A user who clicks through them must see
    them agree; if they diverge, the screen shows two contradictory counts and
    nobody can tell which is right.

    This journey pins the joins between those surfaces: the run id the missing
    report cites is the run the tab actually created, and filtering the grid to
    a reported duplicate group returns exactly the rows that group claimed.
    """
    h = auth(admin_id)
    ds = (await upload_inline(client, admin_id,
                              json.dumps(QUALITY_ROWS)))["dataset_id"]

    # ---- 1. Before any profiling, the tab computes the report live ----
    missing_url = f"/api/v1/datasets/{ds}/versions/1/missing"
    live = (await client.get(missing_url, headers=h)).json()
    assert live["source"] == "computed" and live["profile_run_id"] is None
    assert live["sheet_name"] == "data" and live["row_count"] == 5
    assert [(c["column"], c["null_count"]) for c in live["columns"]] == [
        ("balance", 2), ("region", 1), ("account", 0)]
    assert live["columns"][0]["null_percent"] == 40.0

    # ---- 2. "Run profiling" — one run per sheet, insights attached ----
    r = await client.post(f"/api/v1/datasets/{ds}/versions/1/profile-runs",
                          headers=h)
    assert r.status_code == 200, r.text
    created = r.json()
    assert len(created) == 1
    run_id = created[0]["id"]
    assert created[0]["status"] == "completed"
    assert created[0]["sheet_name"] == "data"
    assert created[0]["algorithm_version"] == 1
    assert created[0]["completed_at"] and created[0]["error"] is None
    rules = {i["rule"]: i for i in created[0]["insights"]}
    assert "duplicate-rows" in rules
    assert rules["duplicate-rows"]["evidence"]["row_count"] == 5

    # ---- 3. The runs list is what the tab polls / re-renders from ----
    listed = (await client.get(f"/api/v1/datasets/{ds}/versions/1/profile-runs",
                               headers=h)).json()
    assert listed["total"] == 1
    assert [x["id"] for x in listed["items"]] == [run_id]
    assert listed["items"][0]["insights"]      # the badge counts live here
    only_completed = (await client.get(
        f"/api/v1/datasets/{ds}/versions/1/profile-runs",
        params={"status": "completed"}, headers=h)).json()
    assert only_completed["total"] == 1

    # ---- 4. "View full profile" — the run detail carries the profile JSON ----
    detail = (await client.get(f"/api/v1/datasets/{ds}/profile-runs/{run_id}",
                               headers=h)).json()
    assert detail["id"] == run_id and detail["status"] == "completed"
    profile = detail["profile"]
    assert profile["row_count"] == 5 and profile["column_count"] == 3
    prof_cols = {c["name"]: c for c in profile["columns"]}
    # The full profile agrees with the live report the tab showed in step 1.
    assert prof_cols["balance"]["null_count"] == 2
    assert prof_cols["region"]["null_count"] == 1

    # ---- 5. The missing report now cites THAT run, by id ----
    backed = (await client.get(missing_url, headers=h)).json()
    assert backed["source"] == "profile_run"
    assert backed["profile_run_id"] == run_id        # not merely truthy
    assert [(c["column"], c["null_count"]) for c in backed["columns"]] == [
        (c["column"], c["null_count"]) for c in live["columns"]]
    assert backed["row_count"] == live["row_count"]

    # ---- 6. The duplicates tab, and how it relates to the run's insight ----
    dups = (await client.get(f"/api/v1/datasets/{ds}/versions/1/duplicates",
                             headers=h)).json()
    assert dups["exact"] is True and dups["row_count"] == 5
    assert dups["group_count"] == 1 and dups["duplicate_rows"] == 2
    group = dups["groups"][0]
    assert group["count"] == 2
    assert group["key"] == {"account": "A1", "region": "EU", "balance": 100.0}
    # The profiler counts REDUNDANT rows (rows - distinct); the duplicates tab
    # counts rows INSIDE duplicate groups. Same words, different numbers — the
    # screen must not print them under one label.
    assert (rules["duplicate-rows"]["evidence"]["duplicate_row_count"]
            == dups["duplicate_rows"] - dups["group_count"] == 1)

    # ---- 7. Group on a subset, the way the tab's column chips do ----
    by_region = (await client.get(f"/api/v1/datasets/{ds}/versions/1/duplicates",
                                  params={"columns": "region"}, headers=h)).json()
    assert by_region["exact"] is False and by_region["columns"] == ["region"]
    eu = next(g for g in by_region["groups"] if g["key"]["region"] == "EU")
    assert eu["count"] == 3

    # ---- 8. Drill down: filter the grid to the reported group ----
    r = await client.post(f"/api/v1/datasets/{ds}/versions/1/query", headers=h,
                          json={"filters": {"conditions": [
                              {"column": "account", "op": "eq", "value": "A1"},
                              {"column": "region", "op": "eq", "value": "EU"},
                              {"column": "balance", "op": "eq", "value": 100.0}]}})
    assert r.status_code == 200, r.text
    assert r.json()["total"] == group["count"] == 2
    assert len(r.json()["items"]) == 2

    # ...and to the column the missing report flagged worst.
    r = await client.post(f"/api/v1/datasets/{ds}/versions/1/query", headers=h,
                          json={"filters": {"conditions": [
                              {"column": "balance", "op": "is_null"}]}})
    assert r.json()["total"] == live["columns"][0]["null_count"] == 2

    # ---- 9. The column drawer agrees with the report it was opened from ----
    col = (await client.get(f"/api/v1/datasets/{ds}/versions/1/columns/balance",
                            headers=h)).json()
    assert col["null_count"] == 2 and col["count"] == 5
    assert col["sheet_name"] == "data"
    assert col["is_candidate_key"] is False

    # ---- 10. Re-profiling from the tab replaces, never accumulates ----
    again = (await client.post(f"/api/v1/datasets/{ds}/versions/1/profile-runs",
                               headers=h)).json()
    assert len(again) == 1 and again[0]["id"] == run_id
    assert (await client.get(f"/api/v1/datasets/{ds}/versions/1/profile-runs",
                             headers=h)).json()["total"] == 1

    # ---- 11. Error branches the tab must distinguish ----
    r = await client.get(f"/api/v1/datasets/{ds}/versions/1/columns/ghost",
                         headers=h)
    assert r.status_code == 400 and r.json()["code"] == "unknown-column"
    r = await client.get(f"/api/v1/datasets/{ds}/versions/99/missing", headers=h)
    assert r.status_code == 404 and r.json()["code"] == "not_found"
    r = await client.get(f"/api/v1/datasets/{ds}/profile-runs/"
                         "00000000-0000-0000-0000-0000000000ff", headers=h)
    assert r.status_code == 404

    outsider, _ = await create_team_user(client, admin_id, "editor")
    for path_ in (f"/api/v1/datasets/{ds}/versions/1/profile-runs",
                  f"/api/v1/datasets/{ds}/profile-runs/{run_id}",
                  f"/api/v1/datasets/{ds}/versions/1/columns/balance"):
        assert (await client.get(path_, headers=auth(outsider))).status_code == 404


# ---------------------------------------------------------------------------
# 4. A saved view goes stale when the schema moves under it
# ---------------------------------------------------------------------------

async def test_journey_a_saved_view_goes_stale_when_a_new_version_drops_its_column_and_the_ui_recovers(
        client, admin_id):
    """FLOW: the saved-views list after an upstream schema change.

    A ``mode: current`` view is validated when it is created and re-validated
    against whatever is current every time it runs. Ship a version without the
    column its query sorts on and every run 400s — which is the error state the
    views list has to render, and the repair path (re-point the query, keep the
    id) it has to offer. Deleting and recreating is not a repair: it changes
    the id and breaks every bookmark and dashboard tile pointing at the view.
    """
    editor, team = await create_team_user(client, admin_id, "editor")
    h = auth(editor)
    v1 = [{"id": 1, "name": "alpha", "score": 10.0},
          {"id": 2, "name": "beta", "score": 30.0},
          {"id": 3, "name": "gamma", "score": 20.0}]
    ds = (await upload_inline(client, editor, json.dumps(v1),
                              team_id=team))["dataset_id"]
    base = f"/api/v1/datasets/{ds}/views"

    # ---- 1. A view over the current version, sorting on `score` ----
    stale_query = {"filters": {"conditions": [
                       {"column": "score", "op": "gte", "value": 20}]},
                   "sort": [{"column": "score", "direction": "desc"}]}
    view = (await client.post(base, headers=h,
                              json={"name": "top-scores", "sheet": "data",
                                    "query": stale_query})).json()
    vid = view["id"]
    first = (await client.post(f"{base}/{vid}/run", headers=h)).json()
    assert first["version_number"] == 1
    assert [i["name"] for i in first["result"]["items"]] == ["beta", "gamma"]

    # ---- 2. v2 lands without `score` ----
    await upload_inline(client, editor,
                        json.dumps([{"id": 1, "name": "alpha"},
                                    {"id": 2, "name": "beta"}]),
                        dataset_id=ds, team_id=team)

    # ---- 3. The tile now fails, with enough detail to explain itself ----
    r = await client.post(f"{base}/{vid}/run", headers=h)
    assert r.status_code == 400, r.text
    assert r.headers["content-type"].startswith(PROBLEM)
    body = r.json()
    assert body["code"] == "unknown-column" and body["column"] == "score"
    assert body["available"] == ["id", "name"]   # v2's columns, for the picker

    # The stored view itself is untouched and still readable — the list can
    # render the broken tile with its name and its (now invalid) query.
    still = (await client.get(f"{base}/{vid}", headers=h)).json()
    assert still["name"] == "top-scores"
    assert still["query"]["sort"] == [{"column": "score", "direction": "desc"}]

    # ---- 4. Rebuild the field picker from the version that is actually current ----
    sheets = (await client.get(f"/api/v1/datasets/{ds}/versions/2/sheets",
                               headers=h)).json()
    fields = [c["normalized_name"] for c in sheets["items"][0]["columns"]]
    assert fields == ["id", "name"] and "score" not in fields

    # ---- 5. Repair in place: same id, valid query ----
    r = await client.patch(f"{base}/{vid}", headers=h, json={"query": {
        "sort": [{"column": "id", "direction": "desc"}], "limit": 10}})
    assert r.status_code == 200, r.text
    assert r.json()["id"] == vid                 # bookmarks survive the repair
    repaired = (await client.post(f"{base}/{vid}/run", headers=h)).json()
    assert repaired["version_number"] == 2
    assert [i["id"] for i in repaired["result"]["items"]] == [2, 1]

    # ---- 6. The other repair a UI can offer: pin the view to the old version ----
    r = await client.post(base, headers=h,
                          json={"name": "top-scores-v1", "sheet": "data",
                                "version_selector": {"mode": "version",
                                                     "version_number": 1},
                                "query": stale_query})
    assert r.status_code == 201, r.text
    pinned_run = (await client.post(f"{base}/{r.json()['id']}/run",
                                    headers=h)).json()
    assert pinned_run["version_number"] == 1
    assert [i["name"] for i in pinned_run["result"]["items"]] == ["beta", "gamma"]

    # A pin at create time is validated against the version it pins, so the
    # same query that is now invalid against v2 is accepted against v1 — and
    # trying to pin at a version that never existed fails before saving.
    r = await client.post(base, headers=h,
                          json={"name": "top-scores-v9", "sheet": "data",
                                "version_selector": {"mode": "version",
                                                     "version_number": 9},
                                "query": stale_query})
    assert r.status_code == 404, r.text

    # ---- 7. Old versions stay explorable while the view follows current ----
    r = await client.get(f"/api/v1/datasets/{ds}/versions/1/preview", headers=h)
    assert r.status_code == 200 and r.json()["total"] == 3
    assert "score" in r.json()["items"][0]


# ---------------------------------------------------------------------------
# 5. The SQL console
# ---------------------------------------------------------------------------

async def test_journey_the_sql_console_persists_every_result_and_scopes_it_to_the_team(
        client, admin_id, tmp_path):
    """SCREEN: the ad-hoc SQL console and its results drawer.

    Each execution returns rows for the grid *and* a `result_file` the drawer
    turns into a download link, so the screen needs the id it gets back to be
    fetchable, team-scoped, and distinct per run. It also needs the error
    branches to be typed, because "your SQL is wrong" and "you may not see this
    dataset" are different dialogs.
    """
    editor, team = await create_team_user(client, admin_id, "editor")
    h = auth(editor)
    path = tmp_path / "crm.xlsx"
    make_crm_workbook(path)     # Customers / Orders / hidden Scratch
    ds = (await upload_file(client, editor, path, name="crm.xlsx",
                            content_type=XLSX_MIME, team_id=team))["dataset_id"]
    sql_url = f"/api/v1/datasets/{ds}/versions/1/sql"

    # ---- 1. The console needs the table names before anyone can type ----
    picker = (await client.get(f"/api/v1/datasets/{ds}/versions/1/sheets",
                               headers=h)).json()
    assert {s["sheet_key"] for s in picker["items"]} == {
        "customers", "orders", "scratch"}

    # ---- 2. Run a query; the tables list confirms what was in scope ----
    r = await client.post(sql_url, headers=h, json={
        "sql": "SELECT c.tier, SUM(o.total) AS spend FROM orders o "
               "JOIN customers c USING (customer_id) GROUP BY c.tier "
               "ORDER BY spend DESC"})
    assert r.status_code == 200, r.text
    first = r.json()
    assert first["columns"] == ["tier", "spend"]
    assert first["items"] == [{"tier": "gold", "spend": 160.0},
                              {"tier": "silver", "spend": 40.0}]
    assert first["row_count"] == 2 and first["truncated"] is False
    assert set(first["tables"]) == {"customers", "orders", "scratch"}
    file1 = first["result_file"]
    assert file1.endswith(".parquet")

    # ---- 3. A second, different query gets its own artifact ----
    second = (await client.post(sql_url, headers=h, json={
        "sql": "SELECT COUNT(*) AS n FROM orders"})).json()
    file2 = second["result_file"]
    assert second["items"] == [{"n": 3}]
    assert file2 != file1

    # ---- 4. Re-running the SAME query mints yet another artifact ----
    # No dedupe and no cap: the console writes one blob per execution.
    third = (await client.post(sql_url, headers=h, json={
        "sql": "SELECT COUNT(*) AS n FROM orders"})).json()
    assert third["items"] == second["items"]
    assert third["result_file"] not in {file1, file2}

    # ---- 5. The results drawer: read it back, and download it ----
    data = (await client.get(f"/api/v1/samples/{file1}/data", headers=h)).json()
    assert data["filename"] == file1
    assert data["total_count"] == 2 and data["filtered_count"] == 2
    assert [c["name"] for c in data["columns"]] == ["tier", "spend"]
    assert data["data"] == first["items"]        # the grid and the file agree

    blob = await client.get(f"/api/v1/samples/{file1}", headers=h)
    assert blob.status_code == 200
    assert blob.content[:4] == b"PAR1"

    paged = (await client.get(f"/api/v1/samples/{file1}/data",
                              params={"limit": 1, "offset": 1,
                                      "sort_by": "spend", "sort_order": "desc"},
                              headers=h)).json()
    assert paged["data"] == [{"tier": "silver", "spend": 40.0}]
    assert paged["total_count"] == 2             # the footer count is unpaged

    assert (await client.get(f"/api/v1/samples/{file2}/data",
                             headers=h)).json()["data"] == [{"n": 3}]

    # ---- 6. An in-team VIEWER can read the console and write results ----
    viewer, _ = await create_team_user(client, admin_id, "viewer", team_id=team)
    hv = auth(viewer)
    assert (await client.get(f"/api/v1/samples/{file1}", headers=hv)).status_code == 200
    assert (await client.get(f"/api/v1/samples/{file1}/data",
                             headers=hv)).status_code == 200
    r = await client.post(sql_url, headers=hv, json={
        "sql": "SELECT tier FROM customers ORDER BY tier"})
    assert r.status_code == 200, r.text          # dataset:read is enough...
    viewer_file = r.json()["result_file"]
    assert viewer_file not in {file1, file2}
    # ...so a read-only member has created a durable blob, and can read it back.
    assert (await client.get(f"/api/v1/samples/{viewer_file}",
                             headers=hv)).status_code == 200
    # ...and so can the editor: the artifact belongs to the dataset, not the user.
    assert (await client.get(f"/api/v1/samples/{viewer_file}",
                             headers=h)).status_code == 200

    # ---- 7. Outsiders see none of it ----
    outsider, _ = await create_team_user(client, admin_id, "editor")
    ho = auth(outsider)
    assert (await client.post(sql_url, headers=ho,
                              json={"sql": "SELECT 1"})).status_code == 404
    assert (await client.get(f"/api/v1/samples/{file1}", headers=ho)).status_code == 404
    assert (await client.get(f"/api/v1/samples/{file1}/data",
                             headers=ho)).status_code == 404

    # ---- 8. Typed error branches, so the console can mark the editor ----
    r = await client.post(sql_url, headers=h, json={"sql": "SELECT * FROM ghost"})
    assert r.status_code == 400, r.text
    assert r.headers["content-type"].startswith(PROBLEM)
    assert r.json()["code"] == "sql-error"

    r = await client.post(sql_url, headers=h, json={
        "sql": "DELETE FROM orders"})
    assert r.status_code == 400 and r.json()["code"] == "select-only"

    r = await client.post(sql_url, headers=h, json={"sql": ""})
    assert r.status_code == 422                  # empty editor, client-side gate

    r = await client.post(f"/api/v1/datasets/{ds}/versions/9/sql", headers=h,
                          json={"sql": "SELECT 1"})
    assert r.status_code == 404 and r.json()["code"] == "not_found"

    assert (await client.get("/api/v1/samples/query_deadbeef.parquet",
                             headers=h)).status_code == 404


# ---------------------------------------------------------------------------
# 6. Cross-cutting: the explorer opened on the Default team's shared dataset
# ---------------------------------------------------------------------------

async def test_journey_a_single_table_dataset_needs_no_sheet_anywhere_in_the_explorer(
        client, admin_id):
    """FLOW: every explorer surface, driven without ever naming a sheet.

    A CSV/inline dataset has exactly one logical sheet, and a UI built for the
    common case never renders a picker. Each version-scoped route has to
    auto-resolve that sheet — if any single one of them starts demanding
    `sheet-selection-required`, the screen dead-ends on a dataset that has no
    picker to show.
    """
    h = auth(admin_id)
    rows = [{"sku": "a", "qty": 1}, {"sku": "b", "qty": 2},
            {"sku": "b", "qty": 2}, {"sku": "c", "qty": None}]
    ds = (await upload_inline(client, admin_id, json.dumps(rows),
                              team_id=DEFAULT_TEAM_ID))["dataset_id"]
    v = f"/api/v1/datasets/{ds}/versions/1"

    preview = (await client.get(f"{v}/preview", headers=h)).json()
    assert preview["total"] == 4 and set(preview["items"][0]) == {"sku", "qty"}

    query = (await client.post(f"{v}/query", headers=h,
                               json={"search": "b"})).json()
    assert query["total"] == 2

    column = (await client.get(f"{v}/columns/sku", headers=h)).json()
    assert column["sheet_name"] == "data" and column["unique_count"] == 3

    dups = (await client.get(f"{v}/duplicates", headers=h)).json()
    assert dups["sheet_name"] == "data" and dups["group_count"] == 1

    miss = (await client.get(f"{v}/missing", headers=h)).json()
    assert miss["sheet_name"] == "data" and miss["columns"][0]["column"] == "qty"

    runs = (await client.post(f"{v}/profile-runs", headers=h)).json()
    assert len(runs) == 1 and runs[0]["sheet_name"] == "data"

    sql = (await client.post(f"{v}/sql", headers=h,
                             json={"sql": "SELECT COUNT(*) AS n FROM data"})).json()
    assert sql["tables"] == ["data"] and sql["items"] == [{"n": 4}]

    # The explicit sheet name works everywhere the implicit one does, so a UI
    # may address it either way without branching on sheet count.
    assert (await client.get(f"{v}/sheets/data/preview",
                             headers=h)).json()["total"] == 4
    assert (await client.get(f"{v}/sheets/data/duplicates",
                             headers=h)).json()["group_count"] == 1
    assert (await client.get(f"{v}/sheets/data/missing",
                             headers=h)).json()["row_count"] == 4
    assert (await client.get(f"{v}/sheets/data/columns/qty",
                             headers=h)).json()["null_count"] == 1
    assert (await client.post(f"{v}/sheets/data/query", headers=h,
                              json={"search": "b"})).json()["total"] == 2
