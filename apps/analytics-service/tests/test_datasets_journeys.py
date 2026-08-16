"""Datasets area (app/features/data_accelerator) driven the way a UI drives it.

Every journey below is one test function so the ordering is explicit and state
flows step to step, exactly like a browser session: the id step 3 needs is the
id step 1 handed back, and step N asserts something that is only true *because*
of step N-1.

The screens modelled here:

* **Dataset detail, first paint** — the four calls a detail page fires on mount
  and whether they agree with each other about sheets, default sheet and
  current version.
* **Promotion review** — schema diff → sheet diff → declare the key → row diff
  → fetch the cell-level artifact → promote, resolve, history.
* **Rename banner** — a sheet renamed upstream, confirmed, and what every
  downstream view says afterwards.
* **Quality-gated release** — the two 409 gate codes, the fix, the promote and
  the emergency rollback, read back as one coherent history.
* **Pivot builder over a big sheet** — `return_data=false` plus artifact paging,
  which is the only shape a real pivot screen can take.
* **Aggregate footer drill-down** — click a row, re-query at a finer grain, and
  the numbers must reconcile.
* **Coordinated sample hand-off** — the mini-workbook a tester actually
  receives, re-read from the persisted artifacts rather than the JSON body.
* **Curation and retire** — patch, facet, search, then the RBAC matrix a screen
  has to grey buttons out from.

Errors are asserted on the machine-readable ``code`` slug, never the prose: a
UI has to branch on the code.
"""

from __future__ import annotations

import json

from openpyxl import Workbook

from conftest import (
    auth,
    create_team_user,
    make_crm_workbook,
    rid,
    upload_file,
    upload_inline,
)

PROBLEM = "application/problem+json"


# ---------------------------------------------------------------------------
# Fixtures-as-builders (local to this file)
# ---------------------------------------------------------------------------


def _ledger_workbook(path, *, costs_sheet="Expenses", extra_revenue_row=False):
    """Revenue + a costs sheet. The costs sheet's *content* never changes, so
    renaming it fingerprints as a high-confidence rename candidate."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Revenue"
    ws.append(["amount", "region"])
    rows = [[100, "EU"], [200, "US"], [300, "APAC"]]
    if extra_revenue_row:
        rows.append([400, "EU"])
    for r in rows:
        ws.append(r)
    costs = wb.create_sheet(costs_sheet)
    costs.append(["item", "cost"])
    costs.append(["rent", 50])
    costs.append(["power", 20])
    wb.save(path)


SALES_V1 = [
    {"order_id": 1, "region": "EU", "quarter": "Q1", "amount": 100.0},
    {"order_id": 2, "region": "EU", "quarter": "Q2", "amount": 200.0},
    {"order_id": 3, "region": "US", "quarter": "Q1", "amount": 50.0},
    {"order_id": 4, "region": "US", "quarter": "Q2", "amount": 150.0},
    {"order_id": 5, "region": "APAC", "quarter": "Q1", "amount": 25.0},
    {"order_id": 6, "region": "LATAM", "quarter": "Q2", "amount": 75.0},
]
# 1 unchanged, 2 and 4 repriced, 5 gone, 7 new.
SALES_V2 = [
    {"order_id": 1, "region": "EU", "quarter": "Q1", "amount": 100.0},
    {"order_id": 2, "region": "EU", "quarter": "Q2", "amount": 250.0},
    {"order_id": 3, "region": "US", "quarter": "Q1", "amount": 50.0},
    {"order_id": 4, "region": "US", "quarter": "Q2", "amount": 175.0},
    {"order_id": 6, "region": "LATAM", "quarter": "Q2", "amount": 75.0},
    {"order_id": 7, "region": "LATAM", "quarter": "Q1", "amount": 10.0},
]


# ---------------------------------------------------------------------------
# 1. Dataset detail page, first paint
# ---------------------------------------------------------------------------


async def test_the_dataset_detail_page_first_paint_agrees_with_itself(
    client, admin_id, tmp_path,
):
    """SCREEN: /datasets/{id} — the four calls a detail page fires on mount.

    Four endpoints independently report the sheet list, the default sheet and
    the current version, and a fifth (analytics) independently resolves the
    default sheet from nothing but a dataset_id. If they ever disagree the page
    renders one sheet's schema while the chart queries another, and nothing
    about the response tells the user.
    """
    editor, team = await create_team_user(client, admin_id, "editor")
    h = auth(editor)

    v1 = tmp_path / "ledger_v1.xlsx"
    _ledger_workbook(v1)
    up = await upload_file(client, editor, v1, name="ledger.xlsx", team_id=team)
    ds = up["dataset_id"]

    # ---- 1. The list row the user clicked ----
    listing = (await client.get("/api/v1/datasets", headers=h)).json()
    row = next(d for d in listing["items"] if d["id"] == ds)
    assert row["current_version"] == 1
    assert listing["total"] >= 1 and listing["limit"] >= 1  # Page envelope

    # ---- 2. Detail header ----
    meta = (await client.get(f"/api/v1/datasets/{ds}", headers=h)).json()
    assert meta["dataset_id"] == ds
    assert meta["default_sheet"] == "Revenue"
    assert meta["masked_columns"] == []          # nothing declared sensitive
    assert meta["preview"], "the header renders a preview"

    # ---- 3. Sheet rail: the same keys, and the same default ----
    sheets = (await client.get(f"/api/v1/datasets/{ds}/sheets", headers=h)).json()
    assert [s["name"] for s in sheets["items"]] == [s["name"] for s in meta["sheets"]]
    default_from_rail = [s["name"] for s in sheets["items"] if s["is_default"]]
    assert default_from_rail == [meta["default_sheet"]]
    assert [s["sheet_key"] for s in sheets["items"]] == ["revenue", "expenses"]

    # ---- 4. Version dropdown: newest first, and it is the version described ----
    versions = (await client.get(f"/api/v1/datasets/{ds}/versions", headers=h)).json()
    assert [v["version_number"] for v in versions["items"]] == [1]
    assert versions["items"][0]["version_number"] == row["current_version"]
    assert versions["items"][0]["sheet_count"] == len(sheets["items"])

    # ---- 5. Tag chips: none yet, and the envelope still parses ----
    # limit is floored at 1 even when empty (the Page envelope rejects limit:0
    # and a pager divides by it) — see _collection.
    tags = (await client.get(f"/api/v1/datasets/{ds}/tags", headers=h)).json()
    assert tags == {"items": [], "total": 0, "limit": 1, "offset": 0}

    # ---- 6. Clicking the default sheet loads its columns + preview ----
    rev = (await client.get(f"/api/v1/datasets/{ds}/sheets/Revenue", headers=h)).json()
    assert rev["is_default"] is True and rev["row_count"] == 3
    assert [c["normalized_name"] for c in rev["columns"]] == ["amount", "region"]
    assert rev["preview"] and set(rev["preview"][0]) == {"amount", "region"}
    # ...and the rail's summary of that sheet matches the detail read.
    rail_rev = next(s for s in sheets["items"] if s["name"] == "Revenue")
    assert (rail_rev["row_count"], rail_rev["column_count"]) == (
        rev["row_count"], rev["column_count"])
    assert rail_rev["schema_fingerprint"] == rev["schema_fingerprint"]

    # A typo'd sheet is a 404 the screen can show as "no such tab".
    r = await client.get(f"/api/v1/datasets/{ds}/sheets/Nope", headers=h)
    assert r.status_code == 404 and r.headers["content-type"].startswith(PROBLEM)

    # ---- 7. Analytics on a multi-sheet dataset must be told which sheet, and
    #         the 400 carries the exact list the rail is showing ----
    r = await client.post("/api/v1/sample", headers=h, json={
        "dataset_id": ds, "target_total_volume": 2,
        "sampling_steps": [{"method": "random", "sample_size": 2}]})
    assert r.status_code == 400 and r.json()["code"] == "sheet-selection-required"
    assert r.json()["sheets"] == [s["name"] for s in sheets["items"]]

    r = await client.post("/api/v1/sample", headers=h, json={
        "dataset_id": ds, "sheet": meta["default_sheet"], "target_total_volume": 2,
        "sampling_steps": [{"method": "random", "sample_size": 2}], "seed": 11})
    assert r.status_code == 200
    body = r.json()
    # Naming the default sheet reaches the sheet the rail marked default.
    assert body["original_count"] == rev["row_count"]
    assert {c["name"] for c in body["columns"]} == {"amount", "region"}

    # ---- 8. A second version: the version-scoped sheet list must not answer
    #         for the current version ----
    v2 = tmp_path / "ledger_v2.xlsx"
    _ledger_workbook(v2, extra_revenue_row=True)
    await upload_file(client, editor, v2, name="ledger.xlsx",
                      dataset_id=ds, team_id=team)

    versions = (await client.get(f"/api/v1/datasets/{ds}/versions", headers=h)).json()
    assert [v["version_number"] for v in versions["items"]] == [2, 1]

    v1_sheets = (await client.get(
        f"/api/v1/datasets/{ds}/versions/1/sheets", headers=h)).json()["items"]
    v2_sheets = (await client.get(
        f"/api/v1/datasets/{ds}/versions/2/sheets", headers=h)).json()["items"]
    now_sheets = (await client.get(
        f"/api/v1/datasets/{ds}/sheets", headers=h)).json()["items"]
    v1_rev = next(s for s in v1_sheets if s["sheet_key"] == "revenue")
    v2_rev = next(s for s in v2_sheets if s["sheet_key"] == "revenue")
    assert (v1_rev["row_count"], v2_rev["row_count"]) == (3, 4)
    # The unversioned rail answers for the CURRENT version, which is now v2.
    assert next(s for s in now_sheets
                if s["sheet_key"] == "revenue")["row_count"] == v2_rev["row_count"]

    r = await client.get(f"/api/v1/datasets/{ds}/versions/9/sheets", headers=h)
    assert r.status_code == 404 and r.headers["content-type"].startswith(PROBLEM)


# ---------------------------------------------------------------------------
# 2. Promotion review
# ---------------------------------------------------------------------------


async def test_a_reviewer_drills_from_schema_diff_to_row_diff_then_promotes(
    client, admin_id,
):
    """FLOW: the promotion-review screen — "what changed, exactly, before I ship it".

    Every hop's id comes from the hop before: the sheet name comes out of the
    workbook diff, the sheet_key it yields addresses sheet-metadata, the primary
    key declared there is the one the row diff defaults to, and `diff_file` has
    to be fetchable by the same caller under /samples authorization. If any seam
    breaks the reviewer sees "something changed" and no way to find out what.
    """
    editor, team = await create_team_user(client, admin_id, "editor")
    h = auth(editor)
    ds = (await upload_inline(client, editor, json.dumps(SALES_V1),
                              team_id=team))["dataset_id"]
    await upload_inline(client, editor, json.dumps(SALES_V2),
                        dataset_id=ds, team_id=team)

    # ---- 1. Two versions, newest first ----
    versions = (await client.get(f"/api/v1/datasets/{ds}/versions", headers=h)).json()["items"]
    assert [v["version_number"] for v in versions] == [2, 1]
    old, new = versions[1]["version_number"], versions[0]["version_number"]
    # Same row count, different content — only a manifest checksum shows that.
    assert versions[0]["row_count"] == versions[1]["row_count"] == 6
    assert versions[0]["manifest_checksum"] != versions[1]["manifest_checksum"]

    # ---- 2. Workbook diff: no sheet moved, and the one sheet reads as
    #         "unchanged" because its SCHEMA is unchanged. This is precisely why
    #         the reviewer has to keep going — the workbook diff cannot tell
    #         them two rows were repriced. ----
    diff = (await client.get(
        f"/api/v1/datasets/{ds}/versions/{old}/diff/{new}", headers=h)).json()
    assert diff["added"] == [] and diff["removed"] == []
    assert diff["modified"] == [] and diff["rename_candidates"] == []
    assert diff["unchanged"] == ["data"]
    sheet = diff["unchanged"][0]                  # <- the id the next hop needs

    # An unknown include section is a 400 the screen can surface verbatim.
    r = await client.get(
        f"/api/v1/datasets/{ds}/versions/{old}/diff/{new}",
        params={"include": "bogus"}, headers=h)
    assert r.status_code == 400 and r.headers["content-type"].startswith(PROBLEM)

    # include=profile with no profile runs reports which sheets it could not
    # cover instead of failing the whole diff.
    prof = (await client.get(f"/api/v1/datasets/{ds}/versions/{old}/diff/{new}",
                             params={"include": "profile"}, headers=h)).json()
    assert prof["profile_drift"] == [] and prof["profile_missing"] == ["data"]

    # ---- 3. Drill into that sheet: columns are identical, so the schema diff
    #         alone cannot explain the change ----
    sdiff = (await client.get(
        f"/api/v1/datasets/{ds}/versions/{old}/sheets/{sheet}/diff/{new}",
        headers=h)).json()
    assert sdiff["sheet_key"] == "data" and sdiff["from_sheet"] == sheet
    assert sdiff["added_columns"] == [] and sdiff["removed_columns"] == []
    assert sdiff["row_count_delta"] == 0 and sdiff["identical"] is True

    # ...and on this dataset the profile-drift section is a hard 400 with a slug,
    # so the screen knows to offer "run a profile" rather than show an error.
    r = await client.get(
        f"/api/v1/datasets/{ds}/versions/{old}/sheets/{sheet}/diff/{new}",
        params={"include": "profile"}, headers=h)
    assert r.status_code == 400 and r.json()["code"] == "profile-required"
    assert set(r.json()["missing"]) == {"from", "to"}

    # ---- 4. Row diff needs a key, and says so in a way the UI can act on ----
    row_diff_url = (f"/api/v1/datasets/{ds}/versions/{old}"
                    f"/sheets/{sheet}/row-diff/{new}")
    r = await client.post(row_diff_url, headers=h, json={})
    assert r.status_code == 400 and r.json()["code"] == "diff-key-required"
    assert r.json()["sheet"] == sheet

    # ---- 5. Declare the key on the sheet_key the diff just named ----
    r = await client.put(f"/api/v1/datasets/{ds}/sheet-metadata/{sdiff['sheet_key']}",
                         headers=h, json={"primary_key_columns": ["order_id"],
                                          "grain": "one row per order"})
    assert r.status_code == 200 and r.json()["primary_key_columns"] == ["order_id"]

    # ---- 6. The row diff now defaults to it — no `key` in the body ----
    rd = (await client.post(row_diff_url, headers=h, json={})).json()
    assert rd["key"] == ["order_id"], "the key just declared is the default"
    assert (rd["added"], rd["removed"], rd["changed"], rd["unchanged"]) == (1, 1, 2, 3)
    assert rd["added"] + rd["changed"] + rd["unchanged"] == len(SALES_V2)
    assert rd["removed"] + rd["changed"] + rd["unchanged"] == len(SALES_V1)
    assert rd["column_changes"] == [{"column": "amount", "changed_rows": 2}]
    assert [r_["order_id"] for r_ in rd["added_sample"]] == [7]
    assert [r_["order_id"] for r_ in rd["removed_sample"]] == [5]

    # ---- 7. Download the complete cell-level diff, as the same caller ----
    assert rd["diff_file"] and rd["diff_artifact_id"]
    page = (await client.get(f"/api/v1/samples/{rd['diff_file']}/data",
                             params={"limit": 100}, headers=h)).json()
    by_type: dict[str, list] = {}
    for cell in page["data"]:
        by_type.setdefault(cell["change_type"], []).append(cell)
    # The artifact carries exactly the cells the summary counted.
    assert len(by_type["changed"]) == rd["changed"]
    assert {c["column_name"] for c in by_type["changed"]} == {"amount"}
    assert {c["row_key"] for c in by_type["added"]} == {"7"}
    assert {c["row_key"] for c in by_type["removed"]} == {"5"}
    assert page["total_count"] == len(page["data"])

    # A caller from another team cannot fetch that artifact by filename.
    outsider, _ = await create_team_user(client, admin_id, "editor")
    r = await client.get(f"/api/v1/samples/{rd['diff_file']}/data",
                         headers=auth(outsider))
    assert r.status_code == 404

    # ---- 8. Before promoting, the reviewer pins the version they reviewed
    #         FROM with a scratch pointer (the raw, ungated PUT) ----
    r = await client.put(f"/api/v1/datasets/{ds}/tags", headers=h,
                         json={"tag_name": "Last-Known-Good", "version_number": old})
    assert r.status_code == 200
    assert r.json()["tag_name"] == "last-known-good"     # normalized
    assert r.json()["version_number"] == old
    # The raw PUT needs a target; omitting both is a 400, not a silent no-op.
    r = await client.put(f"/api/v1/datasets/{ds}/tags", headers=h,
                         json={"tag_name": "orphan"})
    assert r.status_code == 400 and r.headers["content-type"].startswith(PROBLEM)

    # ---- 9. Review complete → promote, and the review is on the record ----
    r = await client.post(f"/api/v1/datasets/{ds}/tags/Production/promote", headers=h,
                          json={"version_number": new, "reason": "row diff reviewed"})
    assert r.status_code == 200
    assert r.json() == {"tag_name": "production", "action": "promote",
                        "from_version_number": None, "to_version_number": new,
                        "reason": "row diff reviewed"}

    # ---- 10. The tag now resolves, and resolves to the SAME record the
    #          versions list describes — field for field ----
    resolved = (await client.get(f"/api/v1/datasets/{ds}/tags/production",
                                 headers=h)).json()
    listed = next(v for v in (await client.get(
        f"/api/v1/datasets/{ds}/versions", headers=h)).json()["items"]
        if v["version_number"] == new)
    for field in ("id", "version_number", "status", "row_count", "sheet_count",
                  "checksum", "source_checksum", "manifest_checksum"):
        assert resolved[field] == listed[field], field
    assert resolved["tags"] == ["production"] == listed["tags"]

    # The scratch pointer set in step 8 did NOT move: promoting one tag must
    # never drag another along, or "last known good" stops meaning anything.
    lkg = (await client.get(f"/api/v1/datasets/{ds}/tags/last-known-good",
                            headers=h)).json()
    assert lkg["version_number"] == old
    assert lkg["tags"] == ["last-known-good"]

    # ---- 11. The chip list and the history both show it ----
    tags = (await client.get(f"/api/v1/datasets/{ds}/tags", headers=h)).json()
    assert {t["tag_name"]: t["version_number"] for t in tags["items"]} == {
        "production": new, "last-known-good": old}
    assert tags["total"] == 2

    hist = (await client.get(f"/api/v1/datasets/{ds}/tags/production/history",
                             headers=h)).json()
    assert hist["total"] == 1
    entry = hist["items"][0]
    assert entry["action"] == "promote" and entry["reason"] == "row diff reviewed"
    assert entry["to_version_number"] == new and entry["from_version_number"] is None
    assert entry["actor_email"].startswith("editor-") and entry["request_id"]

    # A tag that was never touched has no history at all — 404, not empty page.
    r = await client.get(f"/api/v1/datasets/{ds}/tags/staging/history", headers=h)
    assert r.status_code == 404 and r.headers["content-type"].startswith(PROBLEM)


# ---------------------------------------------------------------------------
# 3. Rename banner
# ---------------------------------------------------------------------------


async def test_a_renamed_sheet_keeps_its_identity_across_every_downstream_view(
    client, admin_id, tmp_path,
):
    """SCREEN: the "did this sheet get renamed?" banner on the version compare page.

    The UI shows the diff's rename candidate, the user confirms it, and every
    downstream view has to keep working. What must hold afterwards: the sheet
    rail shows the new name under the OLD logical identity, the version-scoped
    rail still shows the old name for the old version, and everything keyed to
    the logical sheet (sheet-metadata) followed the rename rather than
    detaching.
    """
    editor, team = await create_team_user(client, admin_id, "editor")
    h = auth(editor)

    v1 = tmp_path / "ledger_v1.xlsx"
    _ledger_workbook(v1)
    ds = (await upload_file(client, editor, v1, name="ledger.xlsx",
                            team_id=team))["dataset_id"]

    # The costs sheet is documented BEFORE the rename — this is the state that
    # must survive it.
    r = await client.put(f"/api/v1/datasets/{ds}/sheet-metadata/expenses", headers=h,
                         json={"grain": "one row per cost line",
                               "primary_key_columns": ["item"]})
    assert r.status_code == 200
    logical_before = r.json()["logical_sheet_id"]

    # ---- 1. Upstream renames the sheet; v2 lands ----
    v2 = tmp_path / "ledger_v2.xlsx"
    _ledger_workbook(v2, costs_sheet="Spending")
    await upload_file(client, editor, v2, name="ledger.xlsx",
                      dataset_id=ds, team_id=team)

    # ---- 2. The banner: the diff proposes the rename ----
    diff = (await client.get(f"/api/v1/datasets/{ds}/versions/1/diff/2", headers=h)).json()
    assert [s["name"] for s in diff["removed"]] == ["Expenses"]
    assert [s["name"] for s in diff["added"]] == ["Spending"]
    cand = diff["rename_candidates"]
    assert len(cand) == 1
    assert (cand[0]["from_sheet"], cand[0]["to_sheet"]) == ("Expenses", "Spending")
    assert cand[0]["confidence"] == "high"
    # Before confirming, the two names are two different logical sheets.
    removed_lsid = diff["removed"][0]["logical_sheet_id"]
    added_lsid = diff["added"][0]["logical_sheet_id"]
    assert removed_lsid == logical_before and added_lsid != logical_before

    # ---- 3. The two ways a UI can get this wrong, each with its own slug ----
    # (a) the "old" sheet is still present in v2, so nothing was renamed
    r = await client.post(f"/api/v1/datasets/{ds}/versions/2/confirm-rename", headers=h,
                          json={"from_sheet": "Revenue", "to_sheet": "Spending"})
    assert r.status_code == 400 and r.json()["code"] == "not-a-rename"
    # (b) a pair the diff never proposed — different schema, needs force
    r = await client.post(f"/api/v1/datasets/{ds}/versions/2/confirm-rename", headers=h,
                          json={"from_sheet": "Expenses", "to_sheet": "Revenue"})
    assert r.status_code == 400 and r.json()["code"] == "rename-not-candidate"

    # ---- 4. Confirm the proposed one, straight from the banner's fields ----
    r = await client.post(f"/api/v1/datasets/{ds}/versions/2/confirm-rename", headers=h,
                          json={"from_sheet": cand[0]["from_sheet"],
                                "to_sheet": cand[0]["to_sheet"]})
    assert r.status_code == 200, r.text
    confirmed = r.json()
    assert confirmed["was_candidate"] is True and confirmed["forced"] is False
    assert confirmed["sheet_key"] == "spending"
    assert confirmed["versions_relinked"] == 1
    # The surviving identity is the OLD one — that is the whole point.
    assert confirmed["logical_sheet_id"] == logical_before

    # ---- 5. Confirming twice is a distinguishable 400, not a silent success ----
    r = await client.post(f"/api/v1/datasets/{ds}/versions/2/confirm-rename", headers=h,
                          json={"from_sheet": "Expenses", "to_sheet": "Spending"})
    assert r.status_code == 404 and r.headers["content-type"].startswith(PROBLEM)

    # ---- 6. The sheet rail now shows the new name under the old identity ----
    sheets = (await client.get(f"/api/v1/datasets/{ds}/sheets", headers=h)).json()["items"]
    spending = next(s for s in sheets if s["name"] == "Spending")
    assert spending["logical_sheet_id"] == logical_before
    assert "Expenses" not in [s["name"] for s in sheets]

    # ---- 7. The version-scoped rail still tells the truth about v1 ----
    v1_sheets = (await client.get(
        f"/api/v1/datasets/{ds}/versions/1/sheets", headers=h)).json()["items"]
    old_row = next(s for s in v1_sheets if s["name"] == "Expenses")
    assert old_row["logical_sheet_id"] == logical_before   # same identity, old name
    assert old_row["sheet_key"] == "expenses"

    # ---- 8. The documentation followed the sheet instead of detaching ----
    got = (await client.get(f"/api/v1/datasets/{ds}/sheet-metadata/spending",
                            headers=h)).json()
    assert got["logical_sheet_id"] == logical_before
    assert got["grain"] == "one row per cost line"
    assert got["primary_key_columns"] == ["item"]
    # ...and it is no longer addressable under the retired key.
    r = await client.get(f"/api/v1/datasets/{ds}/sheet-metadata/expenses", headers=h)
    assert r.status_code == 404

    # ---- 9. The unaffected sheet is still just "unchanged" ----
    diff_after = (await client.get(
        f"/api/v1/datasets/{ds}/versions/1/diff/2", headers=h)).json()
    assert diff_after["unchanged"] == ["Revenue"]
    # Both names now resolve to one logical sheet, whichever side you read from.
    assert diff_after["removed"][0]["logical_sheet_id"] == \
        diff_after["added"][0]["logical_sheet_id"] == logical_before
    # ...and the banner is DONE. The fingerprints still match, so a suggestion
    # keyed only on them would reappear on every diff and re-offer a confirm
    # that now 404s. The settled rename is reported as settled instead.
    assert diff_after["rename_candidates"] == []
    assert len(diff_after["renamed"]) == 1
    assert diff_after["renamed"][0] == {
        "logical_sheet_id": logical_before,
        "from_sheet": "Expenses", "to_sheet": "Spending",
        "from_sheet_key": "expenses", "to_sheet_key": "spending",
    }


# ---------------------------------------------------------------------------
# 4. Quality-gated release
# ---------------------------------------------------------------------------


async def test_quality_rules_block_a_promote_until_fixed_then_rollback_is_ungated(
    client, admin_id, tmp_path,
):
    """FLOW: the release screen for a dataset that has quality rules.

    The gate has two distinct 409 codes and a UI must render two different
    things for them: "validate first" is a button, "validation failed" is a
    report. Then the emergency path: rollback must stay ungated even though the
    forward promote was gated, and the history has to read back as one coherent
    story the release page can render as a timeline.
    """
    editor, team = await create_team_user(client, admin_id, "editor")
    h = auth(editor)

    v1 = tmp_path / "ledger_v1.xlsx"
    _ledger_workbook(v1)
    ds = (await upload_file(client, editor, v1, name="ledger.xlsx",
                            team_id=team))["dataset_id"]

    # ---- 1. Before any rules exist, promotion is ungated ----
    r = await client.post(f"/api/v1/datasets/{ds}/tags/production/promote", headers=h,
                          json={"version_number": 1, "reason": "initial go-live"})
    assert r.status_code == 200 and r.json()["to_version_number"] == 1

    # ---- 2. Governance is switched on: a rule the next version will break ----
    r = await client.post(f"/api/v1/datasets/{ds}/rules", headers=h, json={
        "name": "revenue-has-rows", "rule_type": "row_count_min",
        "sheet_selector": "revenue", "parameters": {"min": 10},
        "severity": "error"})
    assert r.status_code == 201, r.text
    rule_id = r.json()["id"]

    v2 = tmp_path / "ledger_v2.xlsx"
    _ledger_workbook(v2, extra_revenue_row=True)
    await upload_file(client, editor, v2, name="ledger.xlsx",
                      dataset_id=ds, team_id=team)

    # ---- 3. Promote is blocked: nothing has been validated yet ----
    r = await client.post(f"/api/v1/datasets/{ds}/tags/production/promote", headers=h,
                          json={"version_number": 2})
    assert r.status_code == 409 and r.headers["content-type"].startswith(PROBLEM)
    assert r.json()["code"] == "validation-required"
    # A blocked promote must leave no trace — the timeline still shows one entry.
    hist = (await client.get(f"/api/v1/datasets/{ds}/tags/production/history",
                             headers=h)).json()
    assert hist["total"] == 1 and hist["items"][0]["action"] == "promote"
    # ...and the tag has NOT moved.
    assert (await client.get(f"/api/v1/datasets/{ds}/tags/production",
                             headers=h)).json()["version_number"] == 1

    # ---- 4. Validate → it fails, as designed ----
    run = (await client.post(f"/api/v1/datasets/{ds}/versions/2/validate",
                             headers=h)).json()
    assert run["status"] == "completed" and run["error_failures"] == 1
    failed = next(x for x in run["results"] if x["status"] == "failed")
    assert failed["rule_id"] == rule_id

    # ---- 5. Promote is blocked again, with the OTHER code, and the payload
    #         links straight to the run the report screen must open ----
    r = await client.post(f"/api/v1/datasets/{ds}/tags/production/promote", headers=h,
                          json={"version_number": 2})
    assert r.status_code == 409 and r.json()["code"] == "validation-failed"
    assert r.json()["validation_run_id"] == run["id"]
    assert r.json()["error_failures"] == 1

    # ---- 6. The reviewer fixes the rule (the threshold was wrong) ----
    r = await client.patch(f"/api/v1/datasets/{ds}/rules/{rule_id}", headers=h,
                           json={"parameters": {"min": 4}})
    assert r.status_code == 200 and r.json()["parameters"] == {"min": 4}

    # The stale run still says failed — a re-validate is genuinely required.
    r = await client.post(f"/api/v1/datasets/{ds}/tags/production/promote", headers=h,
                          json={"version_number": 2})
    assert r.status_code == 409 and r.json()["code"] == "validation-failed"

    run2 = (await client.post(f"/api/v1/datasets/{ds}/versions/2/validate",
                              headers=h)).json()
    assert run2["id"] != run["id"] and run2["error_failures"] == 0

    # ---- 7. Now the gate opens ----
    r = await client.post(f"/api/v1/datasets/{ds}/tags/production/promote", headers=h,
                          json={"version_number": 2, "reason": "gate green"})
    assert r.status_code == 200
    assert (r.json()["from_version_number"], r.json()["to_version_number"]) == (1, 2)

    # ---- 8. Production breaks. Rollback is the emergency path and must not be
    #         gated by the same rules that just gated the promote ----
    r = await client.post(f"/api/v1/datasets/{ds}/tags/production/rollback", headers=h,
                          json={"reason": "bad numbers in prod"})
    assert r.status_code == 200
    assert r.json() == {"tag_name": "production", "action": "rollback",
                        "from_version_number": 2, "to_version_number": 1,
                        "reason": "bad numbers in prod"}
    assert (await client.get(f"/api/v1/datasets/{ds}/tags/production",
                             headers=h)).json()["version_number"] == 1

    # ---- 9. The timeline reads as one story: exactly the three transitions
    #         that actually happened, newest first ----
    hist = (await client.get(f"/api/v1/datasets/{ds}/tags/production/history",
                             headers=h)).json()
    assert hist["total"] == 3
    assert [(e["action"], e["from_version_number"], e["to_version_number"])
            for e in hist["items"]] == [
        ("rollback", 2, 1), ("promote", 1, 2), ("promote", None, 1)]
    assert [e["reason"] for e in hist["items"]] == [
        "bad numbers in prod", "gate green", "initial go-live"]
    assert all(e["actor_email"].startswith("editor-") for e in hist["items"])

    # The page's second tab is paginated off the same envelope.
    page2 = (await client.get(f"/api/v1/datasets/{ds}/tags/production/history",
                              params={"limit": 2, "offset": 2}, headers=h)).json()
    assert page2["total"] == 3 and len(page2["items"]) == 1
    assert page2["items"][0]["action"] == "promote"

    # ---- 10. Retiring the tag keeps the audit trail ----
    r = await client.delete(f"/api/v1/datasets/{ds}/tags/production", headers=h)
    assert r.status_code == 200 and r.json()["success"] is True
    assert (await client.get(f"/api/v1/datasets/{ds}/tags",
                             headers=h)).json()["items"] == []
    hist = (await client.get(f"/api/v1/datasets/{ds}/tags/production/history",
                             headers=h)).json()
    assert hist["total"] == 4 and hist["items"][0]["action"] == "delete"
    assert hist["items"][0]["from_version_number"] == 1


# ---------------------------------------------------------------------------
# 5. Pivot builder over a sheet too big to download
# ---------------------------------------------------------------------------


async def test_an_analyst_pivots_without_downloading_rows_then_pages_and_exports(
    client, admin_id,
):
    """SCREEN: the pivot builder.

    The pivot response envelope has no pagination, so the only viable shape for
    a real dataset is `return_data=false` plus paging the stored artifact. That
    path is what a production UI takes, and it only works if `result_file` is
    still produced (and still fetchable) when the rows are withheld.
    """
    editor, team = await create_team_user(client, admin_id, "editor")
    h = auth(editor)
    ds = (await upload_inline(client, editor, json.dumps(SALES_V1),
                              team_id=team))["dataset_id"]

    # ---- 1. The builder's dimension picker reads the sheet rail ----
    sheets = (await client.get(f"/api/v1/datasets/{ds}/sheets", headers=h)).json()["items"]
    assert len(sheets) == 1
    sheet = sheets[0]["name"]
    columns = {c["normalized_name"] for c in sheets[0]["columns"]}
    assert {"region", "quarter", "amount"} <= columns

    # ---- 2. Before pivoting, check the pivot dimension's cardinality — a UI
    #         refuses to widen on a high-cardinality column ----
    prof = (await client.post("/api/v1/profile", headers=h, json={
        "dataset_id": ds, "sheet": sheet, "columns": ["quarter", "region"],
        "include_histograms": False})).json()
    by_name = {c["name"]: c for c in prof["columns"]}
    assert prof["row_count"] == len(SALES_V1)
    assert by_name["quarter"]["unique_count"] == 2      # safe to widen on
    assert by_name["region"]["unique_count"] == 4       # the row dimension
    assert by_name["quarter"]["null_count"] == 0

    # ---- 3. Pivot with the rows withheld ----
    piv = (await client.post("/api/v1/pivot", headers=h, json={
        "dataset_id": ds, "sheet": sheet, "rows": ["region"], "columns": "quarter",
        "values": [{"column": "amount", "function": "sum", "alias": "amt"}],
        "sort_by": "region", "sort_order": "asc", "return_data": False})).json()
    assert piv["data"] is None, "return_data=false withholds the rows"
    assert piv["result_file"], "but the artifact is still produced"
    assert piv["pivot_columns"] == ["Q1", "Q2"]         # from the profile above
    assert piv["columns"] == ["region", "Q1", "Q2"]
    assert piv["row_count"] == by_name["region"]["unique_count"]
    assert piv["totals"] == {"amt": sum(r["amount"] for r in SALES_V1)}

    # ---- 4. Page the artifact the way the grid scrolls ----
    result = piv["result_file"]
    p1 = (await client.get(f"/api/v1/samples/{result}/data",
                           params={"limit": 3, "offset": 0}, headers=h)).json()
    p2 = (await client.get(f"/api/v1/samples/{result}/data",
                           params={"limit": 3, "offset": 3}, headers=h)).json()
    assert p1["total_count"] == p2["total_count"] == piv["row_count"]
    assert [c["name"] for c in p1["columns"]] == piv["columns"]
    assert len(p1["data"]) == 3 and len(p2["data"]) == 1
    # No row is served twice and every row is served once.
    all_rows = p1["data"] + p2["data"]
    assert [r_["region"] for r_ in all_rows] == ["APAC", "EU", "LATAM", "US"]
    # The stored cells are the pivot's cells: EU is 100 in Q1 and 200 in Q2.
    eu = next(r_ for r_ in all_rows if r_["region"] == "EU")
    assert (eu["Q1"], eu["Q2"]) == (100.0, 200.0)
    # A column the artifact does not have is a slug, not a stack trace.
    r = await client.get(f"/api/v1/samples/{result}/data",
                         params={"columns": "Q3"}, headers=h)
    assert r.status_code == 400 and r.json()["code"] == "unknown-column"

    # ---- 5. "Export to Excel" on the same stored result ----
    exp = (await client.post(f"/api/v1/samples/{result}/export",
                             params={"format": "xlsx"}, headers=h)).json()
    assert exp["source_file"] == result and exp["format"] == "xlsx"
    assert exp["size_bytes"] > 0

    dl = await client.get(f"/api/v1/samples/{exp['export_file']}", headers=h)
    assert dl.status_code == 200
    assert dl.content[:2] == b"PK"                      # a real xlsx
    assert exp["export_file"] in dl.headers["content-disposition"]
    assert int(dl.headers["content-length"]) == exp["size_bytes"]

    # ---- 6. The export inherited the source's ownership, so it is hidden from
    #         another team exactly as the pivot result is ----
    outsider, _ = await create_team_user(client, admin_id, "editor")
    assert (await client.get(f"/api/v1/samples/{exp['export_file']}",
                             headers=auth(outsider))).status_code == 404
    assert (await client.get(f"/api/v1/samples/{result}/data",
                             headers=auth(outsider))).status_code == 404


# ---------------------------------------------------------------------------
# 6. Aggregate footer drill-down
# ---------------------------------------------------------------------------


async def test_clicking_an_aggregate_row_drills_down_and_the_numbers_reconcile(
    client, admin_id,
):
    """SCREEN: a grouped table with a totals footer, and the click-through.

    Every analytics UI does this: render groups with a grand-total footer, click
    a row, re-query at a finer grain. The property a user notices breaking is
    that the drill-down's grand total no longer equals the parent row's cell.
    """
    editor, team = await create_team_user(client, admin_id, "editor")
    h = auth(editor)
    ds = (await upload_inline(client, editor, json.dumps(SALES_V1),
                              team_id=team))["dataset_id"]

    aggregations = [
        {"column": "amount", "function": "sum", "alias": "amt"},
        {"column": "order_id", "function": "count", "alias": "orders"},
        {"column": "amount", "function": "max", "alias": "biggest"},
    ]

    # ---- 1. The table: top groups by revenue, plus the footer ----
    top = (await client.post("/api/v1/aggregate", headers=h, json={
        "dataset_id": ds, "group_by": ["region"], "aggregations": aggregations,
        "sort_by": "amt", "sort_order": "desc", "limit": 2})).json()
    assert [r_["region"] for r_ in top["data"]] == ["EU", "US"]
    assert top["group_count"] == 2
    # `truncated` means the SERVER cap cut the result, not the caller's limit —
    # a UI must not read it as "there are more groups".
    assert top["truncated"] is False
    # The footer is over EVERY row, not the two returned — that is the contract
    # that makes a footer trustworthy next to a limited table.
    assert top["totals"] == {"amt": 600.0, "orders": 6}
    assert top["original_count"] == len(SALES_V1)
    # The stored artifact holds exactly the returned page, so paging it cannot
    # recover the groups the limit cut.
    stored_top = (await client.get(f"/api/v1/samples/{top['result_file']}/data",
                                   headers=h)).json()
    assert stored_top["total_count"] == top["group_count"] == 2
    # ...and a non-additive aggregate says "no total" rather than a wrong one.
    assert top["totals_omitted"] == {"biggest": "non-additive"}
    assert "biggest" not in top["totals"]

    parent = next(r_ for r_ in top["data"] if r_["region"] == "EU")
    assert (parent["amt"], parent["orders"], parent["biggest"]) == (300.0, 2, 200.0)

    # ---- 2. The user clicks EU: same aggregations, no grouping, filtered to
    #         that one value — and the rows are withheld because the drill-down
    #         panel pages the artifact ----
    drill = (await client.post("/api/v1/aggregate", headers=h, json={
        "dataset_id": ds, "group_by": [], "aggregations": aggregations,
        "filters": {"conditions": [
            {"column": "region", "op": "eq", "value": parent["region"]}]},
        "return_data": False})).json()
    assert drill["data"] is None and drill["result_file"]
    assert drill["group_count"] == 1

    # THE reconciliation: the drill-down's grand total is the parent's cell.
    assert drill["totals"]["amt"] == parent["amt"]
    assert drill["totals"]["orders"] == parent["orders"]

    # ---- 3. The panel reads the stored result, and it agrees too ----
    page = (await client.get(f"/api/v1/samples/{drill['result_file']}/data",
                             headers=h)).json()
    assert page["total_count"] == 1
    cell = page["data"][0]
    assert cell["amt"] == parent["amt"] and cell["orders"] == parent["orders"]
    # The non-additive one has no grand total but IS in the stored row.
    assert cell["biggest"] == parent["biggest"]
    assert [c["name"] for c in page["columns"]] == drill["columns"]

    # ---- 4. A second drill on a different group reconciles independently ----
    us = next(r_ for r_ in top["data"] if r_["region"] == "US")
    drill_us = (await client.post("/api/v1/aggregate", headers=h, json={
        "dataset_id": ds, "group_by": [], "aggregations": aggregations,
        "filters": {"conditions": [
            {"column": "region", "op": "eq", "value": "US"}]}})).json()
    assert drill_us["totals"]["amt"] == us["amt"]
    assert drill_us["data"][0]["biggest"] == us["biggest"]
    # And the two groups' subtotals add up to the parent footer.
    assert drill["totals"]["amt"] + drill_us["totals"]["amt"] <= top["totals"]["amt"]

    # ---- 5. Error branches the builder must handle ----
    # A bad sort column publishes the machine-readable `unknown-column`
    # contract, including the vocabulary the picker should have offered.
    r = await client.post("/api/v1/aggregate", headers=h, json={
        "dataset_id": ds, "group_by": ["region"], "aggregations": aggregations,
        "sort_by": "not_an_alias"})
    assert r.status_code == 400 and r.json()["code"] == "unknown-column"
    assert r.json()["columns"] == ["not_an_alias"]
    assert "amt" in r.json()["available"]

    # A bad filter column, a bad group-by column and a bad aggregation column
    # all publish the SAME slug as sort_by, so one error renderer handles the
    # whole builder and can always repopulate the picker from `available`.
    for bad in ({"filters": {"conditions": [
                    {"column": "nope", "op": "eq", "value": "EU"}]}},
                {"group_by": ["nope"]},
                {"aggregations": [{"column": "nope", "function": "sum",
                                   "alias": "m"}]}):
        r = await client.post("/api/v1/aggregate", headers=h, json={
            "dataset_id": ds, "group_by": ["region"],
            "aggregations": aggregations, **bad})
        assert r.status_code == 400
        assert r.headers["content-type"].startswith(PROBLEM)
        assert "nope" in r.json()["detail"]
        assert r.json()["code"] == "unknown-column", r.json()
        assert r.json()["columns"] == ["nope"]
        assert "region" in r.json()["available"]

    r = await client.post("/api/v1/aggregate", headers=h, json={
        "dataset_id": ds, "group_by": ["region"],
        "aggregations": [{"column": "amount", "function": "wat", "alias": "m"}]})
    assert r.status_code == 400 and "wat" in r.json()["detail"]
    # A bad FUNCTION is a different failure from a bad column and gets its own
    # slug, so the builder highlights the function dropdown, not the picker.
    assert r.json()["code"] == "unknown-aggregation-function"
    assert "sum" in r.json()["available"]

    r = await client.post("/api/v1/aggregate", headers=h, json={
        "dataset_id": ds, "group_by": ["region"],
        "aggregations": [{"column": "amount", "function": "median", "alias": "m"}],
        "sort_order": "DESC"})
    assert r.status_code == 422, "sort_order is validated, never coerced"


# ---------------------------------------------------------------------------
# 7. Coordinated sample handed off as files
# ---------------------------------------------------------------------------


async def test_a_coordinated_sample_is_a_referentially_consistent_set_of_files(
    client, admin_id, tmp_path,
):
    """FLOW: "give me a small consistent slice I can hand to a tester".

    The product promise is a mini-workbook whose sheets still join. What a
    downstream consumer actually receives is the persisted artifacts, not the
    JSON body — so this journey withholds the rows entirely and re-checks the
    key relationship on what was written to the object store.
    """
    editor, team = await create_team_user(client, admin_id, "editor")
    h = auth(editor)
    wb = tmp_path / "crm.xlsx"
    make_crm_workbook(wb)
    ds = (await upload_file(client, editor, wb, name="crm.xlsx",
                            team_id=team))["dataset_id"]

    # ---- 1. The screen offers the sheets it can drive from ----
    sheets = (await client.get(f"/api/v1/datasets/{ds}/sheets", headers=h)).json()["items"]
    assert {s["name"] for s in sheets} >= {"Orders", "Customers"}

    # ---- 2. Sample the driver, filter the related sheet, withhold every row ----
    resp = (await client.post("/api/v1/sample/coordinated", headers=h, json={
        "dataset_id": ds, "driver_sheet": "Orders", "target_total_volume": 2,
        "sampling_steps": [{"method": "random", "sample_size": 2}],
        "seed": 5, "return_data": False,
        "related": [{"sheet": "Customers", "left_on": "customer_id",
                     "right_on": "customer_id"}]})).json()
    assert resp["success"] and resp["driver_sheet"] == "Orders"
    assert resp["driver"]["data"] is None
    assert resp["driver"]["sampled_count"] == 2
    driver_file = resp["driver"]["sample_file"]
    assert driver_file

    rel = resp["related"][0]
    assert rel["data"] is None and rel["sample_file"]
    assert rel["sheet"] == "Customers" and rel["parent_sheet"] == "Orders"
    assert (rel["left_on"], rel["right_on"]) == ("customer_id", "customer_id")
    assert rel["original_count"] == 3
    assert rel["sampled_count"] < rel["original_count"], "the semi-join reduced it"

    # ---- 3. Re-read the PERSISTED artifacts — this is the hand-off ----
    driver_rows = (await client.get(f"/api/v1/samples/{driver_file}/data",
                                    headers=h)).json()
    related_rows = (await client.get(f"/api/v1/samples/{rel['sample_file']}/data",
                                     headers=h)).json()
    assert driver_rows["total_count"] == resp["driver"]["sampled_count"]
    assert related_rows["total_count"] == rel["sampled_count"]

    # THE promise: every customer_id in the stored driver file exists in the
    # stored related file, and the related file carries nothing else.
    driver_keys = {r_["customer_id"] for r_ in driver_rows["data"]}
    related_keys = {r_["customer_id"] for r_ in related_rows["data"]}
    assert related_keys == driver_keys
    # The driver's own columns survived the round trip.
    assert [c["name"] for c in driver_rows["columns"]] == \
        [c["name"] for c in resp["driver"]["columns"]]

    # ---- 4. Hand it over as a spreadsheet ----
    exp = (await client.post(f"/api/v1/samples/{driver_file}/export",
                             params={"format": "xlsx"}, headers=h)).json()
    assert exp["source_file"] == driver_file
    dl = await client.get(f"/api/v1/samples/{exp['export_file']}", headers=h)
    assert dl.status_code == 200 and dl.content[:2] == b"PK"

    # An unsupported format is a 400, not a corrupt file.
    r = await client.post(f"/api/v1/samples/{driver_file}/export",
                          params={"format": "pdf"}, headers=h)
    assert r.status_code == 400 and r.headers["content-type"].startswith(PROBLEM)

    # ---- 5. A related sheet that does not exist names the failure ----
    r = await client.post("/api/v1/sample/coordinated", headers=h, json={
        "dataset_id": ds, "driver_sheet": "Orders", "target_total_volume": 2,
        "sampling_steps": [{"method": "random", "sample_size": 2}],
        "related": [{"sheet": "Ghosts", "left_on": "customer_id",
                     "right_on": "customer_id"}]})
    assert r.status_code == 404 and r.headers["content-type"].startswith(PROBLEM)


# ---------------------------------------------------------------------------
# 8. Curation, discovery and retirement — plus the RBAC matrix
# ---------------------------------------------------------------------------


async def test_a_steward_curates_finds_and_retires_a_dataset_within_rbac(
    client, admin_id, tmp_path,
):
    """SCREEN: the catalog admin page — edit metadata, find it again, retire it.

    Every button on this screen is role-gated, and the screen has to grey the
    right ones out per role. It also has to distinguish "you can't do that"
    (403, in your team) from "there is no such dataset" (404, someone else's) —
    they are different pieces of UI.
    """
    editor, team = await create_team_user(client, admin_id, "editor")
    viewer, _ = await create_team_user(client, admin_id, "viewer", team_id=team)
    team_admin, _ = await create_team_user(client, admin_id, "admin", team_id=team)
    outsider, _ = await create_team_user(client, admin_id, "editor")
    h, h_viewer, h_admin, h_out = (auth(editor), auth(viewer),
                                   auth(team_admin), auth(outsider))

    wb = tmp_path / "ledger.xlsx"
    _ledger_workbook(wb)
    ds = (await upload_file(client, editor, wb, name="ledger.xlsx",
                            team_id=team))["dataset_id"]

    # ---- 1. Curate: a partial PATCH writes only what was sent ----
    marker = rid()
    patched = (await client.patch(f"/api/v1/datasets/{ds}", headers=h, json={
        "name": f"quarterly-ledger-{marker}",
        "description": f"General ledger extract {marker}",
        "domain": "finance", "classification": "confidential",
        "source_system": "SAP", "refresh_frequency": "monthly"})).json()
    assert patched["name"] == f"quarterly-ledger-{marker}"
    assert patched["domain"] == "finance" and patched["classification"] == "confidential"
    assert patched["deprecated"] is False and patched["metadata"] == {}

    # An empty body is a documented no-op that returns the record unchanged.
    assert (await client.patch(f"/api/v1/datasets/{ds}", headers=h,
                               json={})).json() == patched

    # A single-field PATCH must not clear the other fields.
    again = (await client.patch(f"/api/v1/datasets/{ds}", headers=h,
                                json={"source_system": "SAP S/4"})).json()
    assert again["source_system"] == "SAP S/4"
    assert again["domain"] == "finance"          # untouched
    assert again["description"] == patched["description"]
    assert again["updated_at"] >= patched["updated_at"]

    # ...and an explicit null still clears one.
    cleared = (await client.patch(f"/api/v1/datasets/{ds}", headers=h,
                                  json={"source_system": None})).json()
    assert cleared["source_system"] is None and cleared["domain"] == "finance"

    # ---- 2. Find it again by the name that was just written ----
    hits = (await client.get("/api/v1/datasets/search",
                             params={"q": marker}, headers=h)).json()
    assert hits["total"] == 1
    hit = hits["items"][0]
    assert hit["id"] == ds and hit["name"] == cleared["name"]
    assert [v["version_number"] for v in hit["versions"]] == [1]
    assert hit["current_version_id"] == hit["versions"][0]["id"]

    # An empty query is rejected up front, not silently "match everything".
    assert (await client.get("/api/v1/datasets/search",
                             params={"q": ""}, headers=h)).status_code == 422

    # ---- 3. Facet filters the catalog sidebar drives ----
    by_domain = (await client.get("/api/v1/datasets",
                                  params={"domain": "finance"}, headers=h)).json()
    assert [d["id"] for d in by_domain["items"]] == [ds]
    assert by_domain["items"][0]["current_version"] == 1

    assert (await client.get("/api/v1/datasets",
                             params={"domain": "nope"}, headers=h)).json()["total"] == 0

    # A mistyped facet is a 422 naming the vocabulary, not an empty 200 the user
    # would read as "you can't see anything".
    r = await client.get("/api/v1/datasets",
                         params={"validation_status": "pass"}, headers=h)
    assert r.status_code == 422
    assert any(e["loc"][-1] == "validation_status" for e in r.json()["errors"])

    # ---- 4. Deprecate it; the default listing still shows it, the filtered
    #         one does not ----
    dep = (await client.patch(f"/api/v1/datasets/{ds}", headers=h, json={
        "deprecated": True, "deprecation_reason": "superseded by the warehouse"})).json()
    assert dep["deprecated"] is True
    assert dep["deprecation_reason"] == "superseded by the warehouse"
    shown = (await client.get("/api/v1/datasets", headers=h)).json()
    assert ds in [d["id"] for d in shown["items"]]
    assert next(d for d in shown["items"] if d["id"] == ds)["deprecated"] is True
    hidden = (await client.get("/api/v1/datasets",
                               params={"include_deprecated": False}, headers=h)).json()
    assert ds not in [d["id"] for d in hidden["items"]]

    # ---- 5. RBAC: what each role's screen may do ----
    # A viewer reads everything and writes nothing.
    assert (await client.get(f"/api/v1/datasets/{ds}", headers=h_viewer)).status_code == 200
    assert (await client.get(f"/api/v1/datasets/{ds}/sheets",
                             headers=h_viewer)).status_code == 200
    assert (await client.get(f"/api/v1/datasets/{ds}/versions",
                             headers=h_viewer)).status_code == 200
    for call in (
        client.patch(f"/api/v1/datasets/{ds}", headers=h_viewer, json={"domain": "x"}),
        client.put(f"/api/v1/datasets/{ds}/tags", headers=h_viewer,
                   json={"tag_name": "prod", "version_number": 1}),
        client.post(f"/api/v1/datasets/{ds}/tags/prod/promote", headers=h_viewer,
                    json={"version_number": 1}),
        client.post(f"/api/v1/datasets/{ds}/versions/1/confirm-rename",
                    headers=h_viewer, json={"from_sheet": "Expenses",
                                            "to_sheet": "Revenue"}),
        client.delete(f"/api/v1/datasets/{ds}", headers=h_viewer),
    ):
        r = await call
        assert r.status_code == 403, r.text
        assert r.headers["content-type"].startswith(PROBLEM)

    # An editor may write but may not delete.
    assert (await client.delete(f"/api/v1/datasets/{ds}", headers=h)).status_code == 403

    # An outsider gets 404 on every one of those — existence is hidden, so the
    # UI must render "not found", never "forbidden".
    for path in (f"/api/v1/datasets/{ds}",
                 f"/api/v1/datasets/{ds}/sheets",
                 f"/api/v1/datasets/{ds}/sheets/Revenue",
                 f"/api/v1/datasets/{ds}/versions",
                 f"/api/v1/datasets/{ds}/versions/1/sheets",
                 f"/api/v1/datasets/{ds}/tags"):
        r = await client.get(path, headers=h_out)
        assert r.status_code == 404, path
    r = await client.post("/api/v1/aggregate", headers=h_out, json={
        "dataset_id": ds, "sheet": "Revenue", "group_by": ["region"],
        "aggregations": [{"column": "amount", "function": "sum"}]})
    assert r.status_code == 404
    assert (await client.delete(f"/api/v1/datasets/{ds}", headers=h_out)).status_code == 404
    # ...and it is genuinely still there.
    assert (await client.get(f"/api/v1/datasets/{ds}", headers=h)).status_code == 200

    # ---- 6. Retire: only the team admin, and the whole tree goes ----
    r = await client.delete(f"/api/v1/datasets/{ds}", headers=h_admin)
    assert r.status_code == 200 and r.json()["success"] is True
    assert r.json()["deleted_keys"], "the storage objects are named in the receipt"

    # Every surface now answers 404, including for the admin who deleted it.
    for path in (f"/api/v1/datasets/{ds}",
                 f"/api/v1/datasets/{ds}/sheets",
                 f"/api/v1/datasets/{ds}/versions",
                 f"/api/v1/datasets/{ds}/tags"):
        assert (await client.get(path, headers=h_admin)).status_code == 404, path
    assert (await client.get("/api/v1/datasets/search",
                             params={"q": marker}, headers=h)).json()["total"] == 0
    assert ds not in [d["id"] for d in
                      (await client.get("/api/v1/datasets", headers=h)).json()["items"]]
