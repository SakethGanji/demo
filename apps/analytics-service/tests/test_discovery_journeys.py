"""Discovery journeys — the catalog, dictionary and dataset-detail screens.

Every step is an HTTP call in the order a UI would make it, and each step
asserts something that is only true *because* of the step before it. The
screens modelled here:

- **Search results → Data dictionary editor.** A hit carries ``sheet_key`` and
  ``normalized_name``; the editor uses both as path segments with no
  translation step, because the UI has nothing else to translate with.
- **Catalog with facet rail.** The counters must move as a dataset is
  documented, and the filtered listing must move with them.
- **Activity feed.** The one screen that pages; paging to exhaustion has to
  yield every event exactly once.
- **Governance / column sensitivity.** Declaring a column PII is an access
  control; taking the declaration back has to restore access.
- **Dictionary editor over a schema that moved on.** Documented-but-gone rows
  are what the steward is there to clean up.
- **Dataset detail for a half-ingested dataset.** Every panel must render.
- **Star toggle in a shared team catalog.** Per-user, idempotent.
- **Read-only (viewer) and outsider views of the whole discovery surface.**

Every route in ``app/features/discovery/api.py`` is called at least once.
"""

from __future__ import annotations

import base64
import json

from openpyxl import Workbook

from conftest import (
    DEFAULT_TEAM_ID, XLSX_MIME, auth, create_team_user, rid, upload_file,
    upload_inline,
)

PROBLEM = "application/problem+json"

ROWS = [{"region": "EU", "amount": 100.0}, {"region": "US", "amount": 50.0}]


def _securities_workbook(path):
    """Two sheets, both with a CUSIP-ish column spelled differently.

    The physical headers are deliberately NOT already normalized, so a search
    hit's ``normalized_name`` differs from the header the user typed and the
    "use the hit verbatim" contract has teeth.
    """
    wb = Workbook()
    ws = wb.active
    ws.title = "Holdings"
    ws.append(["Portfolio Id", "CUSIP Number", "Market Value"])
    ws.append([1, "037833100", 1000.5])
    ws.append([2, "17275R102", 250.0])
    trades = wb.create_sheet("Trade Blotter")
    trades.append(["trade_id", "cusip"])
    trades.append([10, "037833100"])
    wb.save(path)


async def _upload_wb(client, user_id, path, *, dataset_id=None,
                     team_id=DEFAULT_TEAM_ID):
    return await upload_file(client, user_id, path, name="book.xlsx",
                             content_type=XLSX_MIME, dataset_id=dataset_id,
                             team_id=team_id)


# ---------------------------------------------------------------------------
# 1. Search results → data dictionary editor
# ---------------------------------------------------------------------------


async def test_a_steward_searches_for_a_column_and_documents_it_where_the_hit_points(
    client, admin_id, tmp_path,
):
    """SCREEN: global column search → "Document this column" → dictionary editor.

    The search result row is the ONLY thing the editor screen has: it navigates
    to ``/datasets/{dataset_id}/sheet-metadata/{sheet_key}/columns/{normalized_name}``
    built straight out of the hit. ``sheet_key`` comes from
    ``dataset_version_sheets`` while the dictionary routes resolve it against
    ``dataset_sheets.current_sheet_key``, and ``normalized_name`` is re-resolved
    through the current schema — two independent identifier pipelines. If either
    stops being usable verbatim, every "search then document" flow 404s and the
    user has no way to reach the editor at all.
    """
    h = auth(admin_id)
    marker = rid()
    wb = tmp_path / "securities.xlsx"
    _securities_workbook(wb)
    ds = (await _upload_wb(client, admin_id, wb))["dataset_id"]
    assert (await client.patch(f"/api/v1/datasets/{ds}", headers=h,
                               json={"domain": f"securities-{marker}"})
            ).status_code == 200

    # ---- 1. The user types "cusip" into the global search box ----
    r = await client.get("/api/v1/search/columns", params={"q": "cusip"}, headers=h)
    assert r.status_code == 200, r.text
    page = r.json()
    hits = [hit for hit in page["items"] if hit["dataset_id"] == ds]
    assert page["total"] == 2 and page["limit"] == 50 and page["offset"] == 0
    assert len(hits) == 2

    by_sheet = {hit["sheet_key"]: hit for hit in hits}
    assert set(by_sheet) == {"holdings", "trade_blotter"}
    holdings = by_sheet["holdings"]
    # Everything the result row renders, and everything it navigates with.
    assert holdings["sheet_name"] == "Holdings"
    assert holdings["column_name"] == "CUSIP Number"      # the header as shown
    assert holdings["normalized_name"] == "cusip_number"  # the addressable id
    assert holdings["domain"] == f"securities-{marker}"
    assert holdings["dtype"] and holdings["position"] == 1

    # ---- 2. Clicking the hit opens the sheet's dictionary — still empty ----
    base = (f"/api/v1/datasets/{holdings['dataset_id']}"
            f"/sheet-metadata/{holdings['sheet_key']}/columns")
    r = await client.get(base, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["total"] == 0 and r.json()["items"] == []

    # ---- 3. The steward saves a definition, addressed entirely by the hit ----
    r = await client.put(f"{base}/{holdings['normalized_name']}", headers=h,
                         json={"business_name": "CUSIP",
                               "description": "North American security id",
                               "semantic_type": "identifier"})
    assert r.status_code == 200, r.text
    saved = r.json()
    assert saved["column_name"] == holdings["normalized_name"]
    assert saved["sheet_key"] == holdings["sheet_key"]   # round-trips verbatim
    assert saved["logical_sheet_id"] and saved["updated_by"] == admin_id

    # ---- 4. Re-running the same search still points at the same place ----
    r = await client.get("/api/v1/search/columns", params={"q": "cusip"}, headers=h)
    again = next(hit for hit in r.json()["items"]
                 if hit["dataset_id"] == ds and hit["sheet_key"] == "holdings")
    assert (again["sheet_key"], again["normalized_name"]) == (
        holdings["sheet_key"], holdings["normalized_name"])

    # ---- 5. The dictionary the editor reloads now shows exactly that row ----
    listed = (await client.get(base, headers=h)).json()
    assert listed["total"] == 1
    assert listed["items"][0]["business_name"] == "CUSIP"
    assert listed["items"][0]["semantic_type"] == "identifier"

    # The OTHER hit's sheet is untouched — the write went where the hit pointed.
    other = by_sheet["trade_blotter"]
    other_base = (f"/api/v1/datasets/{ds}"
                  f"/sheet-metadata/{other['sheet_key']}/columns")
    assert (await client.get(other_base, headers=h)).json()["total"] == 0

    # ---- 6. Result paging, the way an infinite-scroll list drives it ----
    first = (await client.get("/api/v1/search/columns",
                              params={"q": "cusip", "limit": 1},
                              headers=h)).json()
    assert first["total"] == 2 and len(first["items"]) == 1 and first["limit"] == 1
    second = (await client.get("/api/v1/search/columns",
                               params={"q": "cusip", "limit": 1, "offset": 1},
                               headers=h)).json()
    assert second["offset"] == 1 and len(second["items"]) == 1
    seen = {(hit["sheet_key"], hit["normalized_name"])
            for hit in first["items"] + second["items"]}
    assert seen == {("holdings", "cusip_number"), ("trade_blotter", "cusip")}

    # ---- 7. Error branches the search box must render, not crash on ----
    empty = (await client.get("/api/v1/search/columns",
                              params={"q": f"nothing-{marker}"}, headers=h)).json()
    assert empty["total"] == 0 and empty["items"] == []   # "No results", not an error
    r = await client.get("/api/v1/search/columns", params={"q": ""}, headers=h)
    assert r.status_code == 422       # an empty box must not mean "match all"


# ---------------------------------------------------------------------------
# 2. Catalog facet rail + filtered listing
# ---------------------------------------------------------------------------


async def test_documenting_a_dataset_moves_it_between_facet_buckets_and_listings(
    client, admin_id,
):
    """SCREEN: catalog with a facet rail, and the dataset's Health tab.

    The whole feedback loop of a catalog is "the counter moves when I finish
    documenting". The facet SQL (``signals_lateral``) is a hand-rolled
    re-expression of ``evaluate_documentation``, so the two can only be trusted
    if the same dataset is read through both as it transitions. A UI would show
    a stuck "0 documented" rail, and a dataset that never leaves the
    "Needs documentation" filter, if they diverged.
    """
    h = auth(admin_id)
    ds = (await upload_inline(client, admin_id, json.dumps(ROWS)))["dataset_id"]

    async def facets():
        r = await client.get("/api/v1/datasets/facets", headers=h)
        assert r.status_code == 200, r.text
        return r.json()

    async def listing(**params):
        r = await client.get("/api/v1/datasets", headers=h, params=params)
        assert r.status_code == 200, r.text
        return [d["id"] for d in r.json()["items"]]

    async def doc_dimension():
        r = await client.get(f"/api/v1/datasets/{ds}/health", headers=h)
        assert r.status_code == 200, r.text
        return r.json()["dimensions"]["documentation"]

    # ---- 1. Fresh upload: undocumented in the rail, the filter and health ----
    f = await facets()
    assert f["documentation"].get("none") == 1
    assert "full" not in f["documentation"]
    assert f["validation_status"] == {"none": 1}
    assert f["has_schema_drift"] == {"false": 1}
    assert ds in await listing(documentation="none")
    assert ds not in await listing(documentation="full")
    dim = await doc_dimension()
    assert dim["evidence"]["bucket"] == "none" and dim["status"] == "attention"
    assert dim["evidence"]["total_columns"] == 2

    # ---- 2. Step one of the wizard: describe + classify the dataset ----
    r = await client.patch(f"/api/v1/datasets/{ds}", headers=h,
                           json={"description": "Regional order rollup",
                                 "domain": "sales"})
    assert r.status_code == 200, r.text
    f = await facets()
    assert f["documentation"] == {"partial": 1}   # moved out of "none"
    assert ds not in await listing(documentation="none")
    assert ds in await listing(documentation="partial")
    assert (await doc_dimension())["evidence"]["bucket"] == "partial"

    # ---- 3. Step two: the sheet's grain and primary key ----
    r = await client.put(f"/api/v1/datasets/{ds}/sheet-metadata/data", headers=h,
                         json={"grain": "one row per region",
                               "primary_key_columns": ["region"]})
    assert r.status_code == 200, r.text
    assert r.json()["sheet_key"] == "data" and r.json()["logical_sheet_id"]

    # The sheet panel reloads from the list and the item route alike.
    sm = (await client.get(f"/api/v1/datasets/{ds}/sheet-metadata", headers=h)).json()
    assert sm["total"] == 1 and sm["items"][0]["grain"] == "one row per region"
    one = (await client.get(f"/api/v1/datasets/{ds}/sheet-metadata/data",
                            headers=h)).json()
    assert one == sm["items"][0]
    assert one["primary_key_columns"] == ["region"]

    doc = await doc_dimension()
    assert doc["evidence"]["documented_sheets"] == 1
    assert doc["evidence"]["total_sheets"] == 1
    assert doc["evidence"]["bucket"] == "partial"   # columns still undocumented

    # ---- 4. Step three: one column definition tips coverage over the line ----
    r = await client.put(f"/api/v1/datasets/{ds}/sheet-metadata/data/columns/region",
                         headers=h, json={"business_name": "Sales region",
                                          "allowed_values": ["EU", "US"]})
    assert r.status_code == 200, r.text
    assert r.json()["allowed_values"] == ["EU", "US"]

    doc = await doc_dimension()
    assert doc["evidence"]["documented_columns"] == 1
    assert doc["evidence"]["total_columns"] == 2   # 1/2 >= the 0.5 threshold
    assert doc["evidence"]["bucket"] == "full" and doc["status"] == "ok"
    assert doc["summary"] == "Fully documented"

    # ---- 5. The rail and the filtered listing agree with health ----
    f = await facets()
    assert f["documentation"] == {"full": 1}
    assert f["domain"] == {"sales": 1}
    assert ds in await listing(documentation="full")
    assert ds not in await listing(documentation="partial")

    # ---- 6. Classifying it fills the rail's remaining buckets ----
    r = await client.patch(f"/api/v1/datasets/{ds}", headers=h,
                           json={"classification": "internal",
                                 "source_system": "sap"})
    assert r.status_code == 200, r.text
    f = await facets()
    assert f["classification"] == {"internal": 1}
    assert f["source_system"] == {"sap": 1}
    assert f["deprecated"] == {"false": 1}
    assert set(f) == {"classification", "domain", "source_system", "deprecated",
                      "validation_status", "has_schema_drift", "documentation"}

    # Retiring it moves the deprecated bucket, and the listing can hide it.
    assert (await client.patch(f"/api/v1/datasets/{ds}", headers=h,
                               json={"deprecated": True})).status_code == 200
    assert (await facets())["deprecated"] == {"true": 1}
    assert ds not in await listing(include_deprecated="false")
    assert ds in await listing(include_deprecated="true")

    # ---- 7. A partial edit must not undo the rest of the wizard ----
    r = await client.patch(f"/api/v1/datasets/{ds}/sheet-metadata/data", headers=h,
                           json={"description": "loaded nightly"})
    assert r.status_code == 200, r.text
    merged = r.json()
    assert merged["description"] == "loaded nightly"
    assert merged["grain"] == "one row per region"          # untouched
    assert merged["primary_key_columns"] == ["region"]      # untouched
    assert (await facets())["documentation"] == {"full": 1}

    # ---- 8. The rail must reject a facet value it never emits ----
    r = await client.get("/api/v1/datasets", headers=h,
                         params={"documentation": "mostly"})
    assert r.status_code == 422, r.text


# ---------------------------------------------------------------------------
# 3. Activity feed — paging + the usage counters beside it
# ---------------------------------------------------------------------------


async def test_an_activity_feed_paged_to_exhaustion_shows_every_event_once(
    client, admin_id,
):
    """SCREEN: dataset Activity tab — a merged feed plus usage counters.

    This is the only discovery screen that pages, and it pages over a UNION of
    seven sources. A UI that scrolls it must be able to concatenate the pages
    and get the feed: no event may be shown twice and none may be skipped, and
    the running ``total`` has to agree with what exhaustion actually yields.
    Duplicated/dropped rows across a page boundary are invisible to a test that
    only checks "page 1 != page 2".
    """
    h = auth(admin_id)
    ds = (await upload_inline(client, admin_id, json.dumps(ROWS)))["dataset_id"]
    await upload_inline(client, admin_id, json.dumps(ROWS), dataset_id=ds)

    # A realistic mix of history: a tag, a promotion, a validation run.
    assert (await client.put(f"/api/v1/datasets/{ds}/tags", headers=h,
                             json={"tag_name": "staging", "version_number": 1})
            ).status_code == 200
    r = await client.post(f"/api/v1/datasets/{ds}/tags/staging/promote", headers=h,
                          json={"version_number": 2, "reason": "v2 approved"})
    assert r.status_code == 200 and r.json()["from_version_number"] == 1
    assert (await client.post(f"/api/v1/datasets/{ds}/rules", headers=h,
                              json={"name": "region-not-null",
                                    "rule_type": "not_null",
                                    "sheet_selector": "data",
                                    "column_selector": "region"})
            ).status_code == 201
    assert (await client.post(f"/api/v1/datasets/{ds}/versions/2/validate",
                              headers=h)).status_code == 200

    # ---- 1. The feed's first page tells the UI how many there are ----
    first = (await client.get(f"/api/v1/datasets/{ds}/timeline", headers=h,
                              params={"limit": 2})).json()
    total = first["total"]
    assert total > 6 and first["limit"] == 2 and first["offset"] == 0
    assert len(first["items"]) == 2

    # ---- 2. Scroll to the bottom, two at a time, the way the list does ----
    collected, offset = [], 0
    while True:
        r = await client.get(f"/api/v1/datasets/{ds}/timeline", headers=h,
                             params={"limit": 2, "offset": offset})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["total"] == total    # the count must not drift mid-scroll
        assert body["offset"] == offset
        collected.extend(body["items"])
        if len(body["items"]) < 2:
            break
        offset += 2
        assert offset <= total + 2, "paging never terminated"

    assert len(collected) == total
    # Newest first, all the way down — the feed's only ordering promise.
    stamps = [e["occurred_at"] for e in collected]
    assert stamps == sorted(stamps, reverse=True)

    # ---- 3. Exhaustion must equal one big read: no dupes, nothing dropped ----
    whole = (await client.get(f"/api/v1/datasets/{ds}/timeline", headers=h,
                              params={"limit": 200})).json()
    assert whole["total"] == total and len(whole["items"]) == total

    def key(e):
        return json.dumps([e["event_type"], e["occurred_at"], e["actor"],
                           e["details"]], sort_keys=True)

    paged_keys = sorted(key(e) for e in collected)
    whole_keys = sorted(key(e) for e in whole["items"])
    assert len(set(paged_keys)) == len(paged_keys), "an event was shown twice"
    assert paged_keys == whole_keys, "paging dropped or duplicated an event"

    # ---- 4. What the feed rows actually render ----
    types = {e["event_type"] for e in whole["items"]}
    assert {"version_created", "tag_set", "tag_promote", "validation_run",
            "audit"} <= types
    promote = next(e for e in whole["items"] if e["event_type"] == "tag_promote")
    assert promote["details"]["tag"] == "staging"
    assert promote["details"]["reason"] == "v2 approved"
    assert promote["details"]["from_version"] == 1
    assert promote["actor"]

    # ---- 5. The counters beside the feed come from the same activity ----
    usage = (await client.get(f"/api/v1/datasets/{ds}/usage", headers=h)).json()
    assert usage["dataset_id"] == ds
    assert usage["downloads"] == 0
    audited_writes = sum(1 for e in whole["items"] if e["event_type"] == "audit")
    assert usage["writes"] == audited_writes
    assert usage["total_events"] == audited_writes
    assert usage["last_activity_at"]

    # A download moves the download counter, and only that counter.
    r = await client.get(f"/api/v1/datasets/{ds}/download", headers=h,
                         params={"format": "csv"})
    assert r.status_code == 200, r.text
    usage2 = (await client.get(f"/api/v1/datasets/{ds}/usage", headers=h)).json()
    assert usage2["downloads"] == 1
    assert usage2["writes"] == usage["writes"]
    assert usage2["total_events"] == usage["total_events"] + 1


# ---------------------------------------------------------------------------
# 4. Governance — declare a column sensitive, then take it back
# ---------------------------------------------------------------------------


async def test_a_governance_officer_can_declare_a_column_pii_and_then_undo_it(
    client, admin_id,
):
    """SCREEN: dictionary editor's Sensitivity control, seen from an analyst's tab.

    Setting ``sensitivity`` is an access-control write, not a label: it masks
    the column everywhere rows are returned and closes the raw download. That
    makes the *undo* path safety-critical — a steward who mislabels a column
    must be able to put it back. Two ways exist (an explicit-null PATCH and a
    DELETE of the entry) and both are the fragile kind: an explicit null that a
    repo reads as "unchanged" leaves the column masked forever with no route
    back.
    """
    h = auth(admin_id)
    analyst, _ = await create_team_user(client, admin_id, "editor",
                                        team_id=DEFAULT_TEAM_ID)
    ha = auth(analyst)
    rows = [{"id": 1, "email": "ana@example.com", "amount": 100.0},
            {"id": 2, "email": "bob@example.com", "amount": 250.0}]
    ds = (await upload_inline(client, admin_id, json.dumps(rows)))["dataset_id"]
    col = f"/api/v1/datasets/{ds}/sheet-metadata/data/columns/email"

    async def preview():
        r = await client.get(f"/api/v1/datasets/{ds}/versions/1/preview", headers=ha)
        assert r.status_code == 200, r.text
        return r.json()

    # ---- 1. Before the declaration the analyst sees real values ----
    body = await preview()
    assert body["masked_columns"] == []
    assert {row["email"] for row in body["items"]} == {"ana@example.com",
                                                       "bob@example.com"}

    # ---- 2. The steward declares it PII ----
    r = await client.put(col, headers=h, json={"business_name": "Contact email",
                                               "semantic_type": "email",
                                               "sensitivity": "pii"})
    assert r.status_code == 200, r.text
    assert r.json()["sensitivity"] == "pii"

    # The dictionary panel renders the badge from the list route too.
    listed = (await client.get(f"/api/v1/datasets/{ds}/sheet-metadata/data/columns",
                               headers=h)).json()
    assert listed["total"] == 1 and listed["items"][0]["sensitivity"] == "pii"

    # ---- 3. The analyst's tab is now masked, and the raw file is closed ----
    body = await preview()
    assert body["masked_columns"] == ["email"]
    assert {row["email"] for row in body["items"]} == {"a***@***.com",
                                                       "b***@***.com"}
    assert {row["amount"] for row in body["items"]} == {100.0, 250.0}
    r = await client.get(f"/api/v1/datasets/{ds}/download", headers=ha,
                         params={"format": "csv"})
    assert r.status_code == 403, r.text
    assert r.headers["content-type"].startswith(PROBLEM)
    assert r.json()["code"] == "sensitive-data-restricted"

    # ---- 4. Undo #1: clear the field with an explicit null, keep the rest ----
    r = await client.patch(col, headers=h, json={"sensitivity": None})
    assert r.status_code == 200, r.text
    cleared = r.json()
    assert cleared["sensitivity"] is None
    assert cleared["business_name"] == "Contact email"   # merge, not replace
    assert cleared["semantic_type"] == "email"

    # The item GET the editor reloads with agrees.
    r = await client.get(col, headers=h)
    assert r.status_code == 200 and r.json()["sensitivity"] is None

    body = await preview()
    assert body["masked_columns"] == []
    assert {row["email"] for row in body["items"]} == {"ana@example.com",
                                                       "bob@example.com"}
    assert (await client.get(f"/api/v1/datasets/{ds}/download", headers=ha,
                             params={"format": "csv"})).status_code == 200

    # ---- 5. Re-declare, then undo #2: delete the whole entry ----
    r = await client.put(col, headers=h, json={"sensitivity": "confidential",
                                               "semantic_type": "email"})
    assert r.status_code == 200 and r.json()["sensitivity"] == "confidential"
    assert (await preview())["masked_columns"] == ["email"]

    r = await client.delete(col, headers=h)
    assert r.status_code == 204, r.text
    body = await preview()
    assert body["masked_columns"] == []
    assert {row["email"] for row in body["items"]} == {"ana@example.com",
                                                       "bob@example.com"}

    # The entry is gone from the panel, and deleting again is a clean 404.
    assert (await client.get(f"/api/v1/datasets/{ds}/sheet-metadata/data/columns",
                             headers=h)).json()["total"] == 0
    r = await client.get(col, headers=h)
    assert r.status_code == 404 and r.headers["content-type"].startswith(PROBLEM)
    assert (await client.delete(col, headers=h)).status_code == 404


# ---------------------------------------------------------------------------
# 5. Dictionary editor over a schema that moved on
# ---------------------------------------------------------------------------


async def test_a_dictionary_screen_can_show_and_clean_up_columns_that_no_longer_exist(
    client, admin_id,
):
    """SCREEN: data dictionary editor after a new version drops a column.

    The editor renders "documented but gone" rows so a steward can retire them.
    Nothing in ``ColumnMetadataOut`` says a row is orphaned, so the UI has to
    fetch the live schema (``/sheets``) and the dictionary and diff them —
    and the three write verbs disagree about those exact rows: PUT/PATCH 400
    with ``unknown-column`` while GET/DELETE still resolve them. Both halves
    have to hold or the row is either unrenderable or unremovable.
    """
    h = auth(admin_id)
    v1 = [{"account_id": 1, "legacy_code": "X", "region": "EU"}]
    v2 = [{"account_id": 2, "region": "US"}]          # legacy_code dropped
    ds = (await upload_inline(client, admin_id, json.dumps(v1)))["dataset_id"]
    base = f"/api/v1/datasets/{ds}/sheet-metadata/data/columns"

    # ---- 1. Document two of v1's three columns ----
    for name, business in (("account_id", "Account"), ("legacy_code", "Legacy code")):
        r = await client.put(f"{base}/{name}", headers=h,
                             json={"business_name": business})
        assert r.status_code == 200, r.text
    assert (await client.get(base, headers=h)).json()["total"] == 2

    # ---- 2. A new version arrives without legacy_code ----
    await upload_inline(client, admin_id, json.dumps(v2), dataset_id=ds)

    # ---- 3. The screen's two fetches: live schema, then documented entries ----
    sheets = (await client.get(f"/api/v1/datasets/{ds}/sheets", headers=h)).json()
    live = {c["normalized_name"] for c in sheets["items"][0]["columns"]}
    assert live == {"account_id", "region"}

    documented = (await client.get(base, headers=h)).json()
    assert documented["total"] == 2
    names = {e["column_name"] for e in documented["items"]}
    assert names == {"account_id", "legacy_code"}
    # The diff the UI has to compute itself, because no field carries it.
    orphans = names - live
    assert orphans == {"legacy_code"}

    # ---- 4. The orphan row is still readable, so the screen can show it ----
    r = await client.get(f"{base}/legacy_code", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["business_name"] == "Legacy code"

    # ---- 5. ...but its Edit control cannot be used: PUT/PATCH refuse ----
    r = await client.patch(f"{base}/legacy_code", headers=h,
                           json={"description": "retired 2026"})
    assert r.status_code == 400, r.text
    assert r.headers["content-type"].startswith(PROBLEM)
    body = r.json()
    assert body["code"] == "unknown-column" and body["column"] == "legacy_code"
    assert set(body["available"]) >= live

    r = await client.put(f"{base}/legacy_code", headers=h,
                         json={"business_name": "Legacy code"})
    assert r.status_code == 400 and r.json()["code"] == "unknown-column"

    # ---- 6. Only the Delete control works — which is the cleanup ----
    assert (await client.delete(f"{base}/legacy_code", headers=h)).status_code == 204
    remaining = (await client.get(base, headers=h)).json()
    assert remaining["total"] == 1
    assert [e["column_name"] for e in remaining["items"]] == ["account_id"]
    assert {e["column_name"] for e in remaining["items"]} <= live

    # ---- 7. A column that arrived in v2 documents normally ----
    r = await client.put(f"{base}/region", headers=h,
                         json={"business_name": "Region"})
    assert r.status_code == 200, r.text
    assert (await client.get(base, headers=h)).json()["total"] == 2


# ---------------------------------------------------------------------------
# 6. Dataset detail for a dataset that never finished ingesting
# ---------------------------------------------------------------------------


async def test_every_discovery_panel_renders_for_a_dataset_that_never_finished_ingesting(
    client, admin_id,
):
    """SCREEN: dataset detail, opened on an abandoned resumable upload.

    A tus upload that is created and never completed leaves a real dataset row
    with no ready version — the state a user reaches on their very first upload
    mistake, and the one they immediately click into. Every discovery panel has
    a distinct no-current-version branch (health reports a null version and
    "No ready version", the dictionary routes 404 through the sheet lookup,
    facets still counts the row). If any of them 500s the detail page is a blank
    error and the user cannot even find the retry button.
    """
    h = auth(admin_id)

    # ---- 1. Start a resumable upload and walk away ----
    meta = base64.b64encode(b"orders.csv").decode()
    r = await client.post("/api/v1/tus/", headers={
        **h, "X-Team-Id": DEFAULT_TEAM_ID, "Upload-Length": "4096",
        "Upload-Metadata": f"filename {meta}", "Tus-Resumable": "1.0.0"})
    assert r.status_code == 201, r.text
    status = (await client.get(f"{r.headers['location']}/status", headers=h)).json()
    assert status["status"] == "uploading"
    ds = status["dataset_id"]
    assert ds

    # ---- 2. Catalog rail: the row is counted, not skipped or crashed on ----
    f = (await client.get("/api/v1/datasets/facets", headers=h)).json()
    assert sum(f["documentation"].values()) == 1
    assert f["documentation"] == {"none": 1}
    assert f["validation_status"] == {"none": 1}
    assert f["has_schema_drift"] == {"false": 1}

    # ---- 3. Health panel: every dimension present, none of them fabricated ----
    r = await client.get(f"/api/v1/datasets/{ds}/health", headers=h)
    assert r.status_code == 200, r.text
    health = r.json()
    assert health["dataset_id"] == ds
    assert health["current_version_number"] is None
    dims = health["dimensions"]
    assert set(dims) == {"schema_stability", "validation", "missing_data",
                         "duplicates", "drift", "freshness", "documentation"}
    assert dims["freshness"]["status"] == "attention"
    assert dims["freshness"]["summary"] == "No ready version"
    assert dims["schema_stability"]["status"] == "unknown"
    assert dims["validation"]["status"] == "unknown"
    assert dims["missing_data"]["status"] == "unknown"
    assert dims["duplicates"]["status"] == "unknown"
    assert dims["drift"]["status"] == "unknown"
    assert dims["documentation"]["evidence"]["total_columns"] == 0
    assert dims["documentation"]["evidence"]["total_sheets"] == 0

    # ---- 4. Activity feed: the pending version is the only event ----
    tl = (await client.get(f"/api/v1/datasets/{ds}/timeline", headers=h)).json()
    assert tl["total"] == 1
    assert [e["event_type"] for e in tl["items"]] == ["version_created"]
    assert tl["items"][0]["details"]["status"] == "uploading"
    assert tl["items"][0]["details"]["version_number"] == 1

    # ---- 5. Usage counters: honest zeroes, not nulls the UI must special-case ----
    usage = (await client.get(f"/api/v1/datasets/{ds}/usage", headers=h)).json()
    assert usage["dataset_id"] == ds
    assert (usage["downloads"], usage["writes"], usage["total_events"]) == (0, 0, 0)
    assert usage["last_activity_at"] is None

    # ---- 6. Documentation panels: an empty list, and a typed 404 per sheet ----
    sm = (await client.get(f"/api/v1/datasets/{ds}/sheet-metadata", headers=h)).json()
    assert sm == {"items": [], "total": 0, "limit": 50, "offset": 0}

    r = await client.get(f"/api/v1/datasets/{ds}/sheet-metadata/data/columns",
                         headers=h)
    assert r.status_code == 404, r.text
    assert r.headers["content-type"].startswith(PROBLEM)
    assert "data" in r.json()["detail"]

    r = await client.get(f"/api/v1/datasets/{ds}/sheet-metadata/data", headers=h)
    assert r.status_code == 404, r.text

    # ---- 7. And the write controls refuse for the same, stated reason ----
    r = await client.put(f"/api/v1/datasets/{ds}/sheet-metadata/data", headers=h,
                         json={"grain": "one row per order"})
    assert r.status_code == 404, r.text

    # ---- 8. Search finds nothing in it — there is no captured schema yet ----
    hits = (await client.get("/api/v1/search/columns", params={"q": "order"},
                             headers=h)).json()
    assert [hit for hit in hits["items"] if hit["dataset_id"] == ds] == []

    # ---- 9. It can still be starred from the catalog while it is stuck ----
    assert (await client.put(f"/api/v1/datasets/{ds}/favorite",
                             headers=h)).status_code == 204
    starred = (await client.get("/api/v1/datasets", headers=h,
                                params={"favorites": "true"})).json()
    assert [d["id"] for d in starred["items"]] == [ds]


# ---------------------------------------------------------------------------
# 7. The star toggle in a shared team catalog
# ---------------------------------------------------------------------------


async def test_favorites_are_private_to_the_user_who_set_them(client, admin_id):
    """SCREEN: catalog star toggle + the "My favorites" filter, shared team.

    The star is a per-user row in a two-column PK table, so four behaviours the
    toggle depends on all live here: one user's star must be invisible to their
    colleague, a read-only viewer must be able to star at all (it is a personal
    bookmark, not a write to the dataset), re-clicking must be idempotent rather
    than a duplicate-key 500, and un-starring something that was never starred
    must be the 404 the toggle uses to resync its own state.
    """
    ds = (await upload_inline(client, admin_id, json.dumps(ROWS)))["dataset_id"]
    viewer, _ = await create_team_user(client, admin_id, "viewer",
                                       team_id=DEFAULT_TEAM_ID)
    colleague, _ = await create_team_user(client, admin_id, "editor",
                                          team_id=DEFAULT_TEAM_ID)
    hv, hc = auth(viewer), auth(colleague)

    async def starred(headers):
        r = await client.get("/api/v1/datasets", headers=headers,
                             params={"favorites": "true"})
        assert r.status_code == 200, r.text
        return r.json()

    # ---- 1. Both users can see the dataset; neither has starred it ----
    assert ds in [d["id"] for d in
                  (await client.get("/api/v1/datasets", headers=hv)).json()["items"]]
    assert (await starred(hv))["total"] == 0
    assert (await starred(hc))["total"] == 0

    # ---- 2. The viewer stars it — a read-only role may bookmark ----
    r = await client.put(f"/api/v1/datasets/{ds}/favorite", headers=hv)
    assert r.status_code == 204, r.text

    mine = await starred(hv)
    assert mine["total"] == 1 and [d["id"] for d in mine["items"]] == [ds]
    # ...and the colleague's rail is untouched.
    assert (await starred(hc))["total"] == 0

    # ---- 3. Double-click is idempotent, not a duplicate row or a 500 ----
    assert (await client.put(f"/api/v1/datasets/{ds}/favorite",
                             headers=hv)).status_code == 204
    assert (await starred(hv))["total"] == 1

    # ---- 4. The colleague cannot un-star what they never starred ----
    r = await client.delete(f"/api/v1/datasets/{ds}/favorite", headers=hc)
    assert r.status_code == 404, r.text
    assert r.headers["content-type"].startswith(PROBLEM)
    assert (await starred(hv))["total"] == 1   # ...and the viewer keeps theirs

    # ---- 5. The viewer un-stars; the toggle's own second click 404s ----
    assert (await client.delete(f"/api/v1/datasets/{ds}/favorite",
                                headers=hv)).status_code == 204
    assert (await starred(hv))["total"] == 0
    assert (await client.delete(f"/api/v1/datasets/{ds}/favorite",
                                headers=hv)).status_code == 404

    # ---- 6. An outsider's star attempt is a 404, not a 403 ----
    outsider, _ = await create_team_user(client, admin_id, "editor")
    ho = auth(outsider)
    assert (await client.put(f"/api/v1/datasets/{ds}/favorite",
                             headers=ho)).status_code == 404
    assert (await client.delete(f"/api/v1/datasets/{ds}/favorite",
                                headers=ho)).status_code == 404
    assert (await starred(ho))["total"] == 0


# ---------------------------------------------------------------------------
# 8. What each role sees of the discovery surface
# ---------------------------------------------------------------------------


async def test_a_viewer_gets_a_read_only_catalog_and_an_outsider_gets_nothing(
    client, admin_id, tmp_path,
):
    """SCREEN: the same catalog/dictionary screens rendered for three roles.

    RBAC is screen behaviour, not just a security property: the dictionary
    editor renders its Save/Delete controls from what the role may do, and the
    UI must branch on 403 (in your team, wrong role — show "read only") versus
    404 (not your team — the resource must not be shown to exist at all). Every
    discovery route is exercised from a viewer and from an outsider here, so a
    route that gains or loses an auth dependency is caught by the screen it
    breaks.
    """
    editor, tid = await create_team_user(client, admin_id, "editor")
    viewer, _ = await create_team_user(client, admin_id, "viewer", team_id=tid)
    outsider, _ = await create_team_user(client, admin_id, "editor")
    he, hv, ho = auth(editor), auth(viewer), auth(outsider)

    wb = tmp_path / "securities.xlsx"
    _securities_workbook(wb)
    ds = (await _upload_wb(client, editor, wb, team_id=tid))["dataset_id"]
    sm = f"/api/v1/datasets/{ds}/sheet-metadata"
    col = f"{sm}/holdings/columns/cusip_number"

    # ---- 1. The editor documents it, so there is something to read ----
    assert (await client.put(f"{sm}/holdings", headers=he,
                             json={"grain": "one row per holding"})).status_code == 200
    assert (await client.put(col, headers=he,
                             json={"business_name": "CUSIP"})).status_code == 200

    # ---- 2. The viewer's screens all render ----
    for path, params in ((f"{sm}", None),
                         (f"{sm}/holdings", None),
                         (f"{sm}/holdings/columns", None),
                         (col, None),
                         (f"/api/v1/datasets/{ds}/timeline", None),
                         (f"/api/v1/datasets/{ds}/health", None),
                         (f"/api/v1/datasets/{ds}/usage", None),
                         ("/api/v1/datasets/facets", None),
                         ("/api/v1/search/columns", {"q": "cusip"})):
        r = await client.get(path, headers=hv, params=params)
        assert r.status_code == 200, f"{path}: {r.status_code} {r.text}"

    assert (await client.get(col, headers=hv)).json()["business_name"] == "CUSIP"
    vf = (await client.get("/api/v1/datasets/facets", headers=hv)).json()
    assert sum(vf["documentation"].values()) == 1     # scoped to their team
    vhits = (await client.get("/api/v1/search/columns", params={"q": "cusip"},
                              headers=hv)).json()
    assert {hit["dataset_id"] for hit in vhits["items"]} == {ds}

    # ---- 3. ...but every editing control is refused with 403, in-team ----
    for method, path, body in (
        ("put", f"{sm}/holdings", {"grain": "changed"}),
        ("patch", f"{sm}/holdings", {"grain": "changed"}),
        ("put", col, {"business_name": "changed"}),
        ("patch", col, {"business_name": "changed"}),
    ):
        r = await getattr(client, method)(path, headers=hv, json=body)
        assert r.status_code == 403, f"{method} {path}: {r.status_code} {r.text}"
        assert r.headers["content-type"].startswith(PROBLEM)
    r = await client.delete(col, headers=hv)
    assert r.status_code == 403, r.text

    # Nothing the viewer attempted changed anything.
    assert (await client.get(f"{sm}/holdings",
                             headers=hv)).json()["grain"] == "one row per holding"
    assert (await client.get(col, headers=hv)).json()["business_name"] == "CUSIP"

    # ---- 4. The outsider is told the dataset does not exist, everywhere ----
    for path in (f"{sm}", f"{sm}/holdings", f"{sm}/holdings/columns", col,
                 f"/api/v1/datasets/{ds}/timeline",
                 f"/api/v1/datasets/{ds}/health",
                 f"/api/v1/datasets/{ds}/usage"):
        r = await client.get(path, headers=ho)
        assert r.status_code == 404, f"{path}: {r.status_code} {r.text}"
        assert r.headers["content-type"].startswith(PROBLEM)

    for method, path, body in (
        ("put", f"{sm}/holdings", {"grain": "x"}),
        ("patch", f"{sm}/holdings", {"grain": "x"}),
        ("put", col, {"business_name": "x"}),
        ("patch", col, {"business_name": "x"}),
    ):
        r = await getattr(client, method)(path, headers=ho, json=body)
        assert r.status_code == 404, f"{method} {path}: {r.status_code} {r.text}"
    assert (await client.delete(col, headers=ho)).status_code == 404

    # ---- 5. ...and the outsider's own catalog does not mention it ----
    ohits = (await client.get("/api/v1/search/columns", params={"q": "cusip"},
                              headers=ho)).json()
    assert ohits["total"] == 0 and ohits["items"] == []
    of = (await client.get("/api/v1/datasets/facets", headers=ho)).json()
    assert sum(of["documentation"].values()) == 0

    # ---- 6. The editor's own view is unchanged by any of it ----
    assert (await client.get(f"{sm}", headers=he)).json()["total"] == 1
    assert (await client.get(f"{sm}/holdings/columns",
                             headers=he)).json()["total"] == 1
