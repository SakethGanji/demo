"""Library journeys — the saved-analytics screens, driven the way a UI drives them.

House model: ``tests/test_e2e_journeys.py``. Every step is one real HTTP call, in
the order a screen makes it, and state flows step to step — step N asserts
something that is only true *because* of step N-1.

The screens covered here:

* **Chart builder** — pick a sheet, save a definition, preview the numbers,
  save a chart, render it, re-tune the encoding, re-render.
* **Run-history table** — page through a definition's runs, including a page
  boundary and a page past the end.
* **Library permissions** — what a viewer sees that an editor does not, and what
  an outsider is not even told exists.
* **Lineage explorer** — publish a run, chain a second definition onto the
  published output, walk the DAG both directions and check the one-hop view
  agrees with it.
* **Sheet picker repair** — a definition saved against a multi-sheet workbook
  is fixed from the machine-readable error the API returned.
* **Dashboard mount** — list every saved chart, then render each one.
* **Library management** — rename collisions and retiring an entry.

Between them they call every route in ``app/features/library/api.py``.
"""

from __future__ import annotations

import json

from conftest import (
    auth,
    create_team_user,
    make_workbook,
    rid,
    upload_file,
    upload_inline,
)

PROBLEM = "application/problem+json"

#: region x quarter revenue — two dimensions so an encoding can actually be
#: re-tuned (swap the axis, promote the other dimension to a series).
SALES = [
    {"region": "EU", "quarter": "Q1", "amount": 100.0},
    {"region": "EU", "quarter": "Q2", "amount": 150.0},
    {"region": "US", "quarter": "Q1", "amount": 50.0},
    {"region": "US", "quarter": "Q2", "amount": 70.0},
]


def _problem(response) -> dict:
    """Assert the RFC7807 envelope a UI branches on, and return the body."""
    assert response.headers["content-type"].startswith(PROBLEM), response.headers
    body = response.json()
    assert body["status"] == response.status_code
    assert body["code"] and body["detail"]
    return body


async def _sales_dataset(client, user_id, *, team_id=None):
    kwargs = {"team_id": team_id} if team_id else {}
    body = await upload_inline(client, user_id, json.dumps(SALES), **kwargs)
    return body["dataset_id"]


# ---------------------------------------------------------------------------
# 1. The chart-builder screen
# ---------------------------------------------------------------------------


async def test_a_chart_author_previews_a_definition_then_builds_and_retunes_a_chart(
    client, admin_id,
):
    """SCREEN: chart builder — sheet picker → definition → preview → chart → encoding.

    The literal click path: choose a sheet, save an aggregate, run it once to see
    the numbers, save a chart over it with no encoding, render it (fields
    inferred), then re-tune the encoding and render again. A UI regression here
    means the field names offered by the picker are not the ones the chart may
    reference, or that editing the encoding silently retargets the chart.
    """
    h = auth(admin_id)
    ds = await _sales_dataset(client, admin_id)

    # ---- 1. On mount the builder asks which sheets/columns exist ----
    sheets = (await client.get(f"/api/v1/datasets/{ds}/sheets", headers=h)).json()["items"]
    assert [s["name"] for s in sheets] == ["data"]
    sheet_columns = {c["normalized_name"] for c in sheets[0]["columns"]}
    assert {"region", "quarter", "amount"} <= sheet_columns

    # ---- 2. Save a definition using ONLY names the picker offered ----
    r = await client.post(f"/api/v1/datasets/{ds}/analytics", headers=h, json={
        "name": "revenue-by-region-quarter", "kind": "aggregate", "sheet": "data",
        "params": {"group_by": ["region", "quarter"],
                   "aggregations": [{"column": "amount", "function": "sum",
                                     "alias": "revenue"}],
                   "sort_by": "region", "sort_order": "asc"}})
    assert r.status_code == 201, r.text
    definition = r.json()
    def_id = definition["id"]
    assert definition["sheet"] == "data"
    assert definition["version_selector"] == {"mode": "current"}

    # ---- 3. Preview: run it once so the author sees real numbers ----
    r = await client.post(f"/api/v1/datasets/{ds}/analytics/{def_id}/run", headers=h)
    assert r.status_code == 200, r.text
    run = r.json()
    assert run["status"] == "completed" and run["definition_id"] == def_id
    # The columns the run returns are exactly the vocabulary a chart config may
    # name — this is the contract that lets the builder populate its dropdowns.
    assert run["result"]["columns"] == ["region", "quarter", "revenue"]
    preview_totals = {(row["region"], row["quarter"]): row["revenue"]
                      for row in run["result"]["data"]}
    assert preview_totals == {("EU", "Q1"): 100.0, ("EU", "Q2"): 150.0,
                              ("US", "Q1"): 50.0, ("US", "Q2"): 70.0}
    assert run["result_summary"]["group_count"] == 4

    # ---- 4. Save a chart with NO encoding yet ----
    r = await client.post(f"/api/v1/datasets/{ds}/charts", headers=h, json={
        "name": "revenue", "chart_type": "bar", "definition_id": def_id,
        "config": {}})
    assert r.status_code == 201, r.text
    chart = r.json()
    chart_id = chart["id"]
    assert chart["definition_id"] == def_id and chart["view_id"] is None
    assert chart["config"] == {}

    # ---- 5. First render: everything is inferred from step 3's columns ----
    r = await client.post(f"/api/v1/datasets/{ds}/charts/{chart_id}/render", headers=h)
    assert r.status_code == 200, r.text
    first = r.json()
    assert first["chart_id"] == chart_id and first["chart_type"] == "bar"
    assert first["x_field"] == "region"          # first non-numeric column
    assert first["y_fields"] == ["revenue"]      # the only numeric one
    assert first["series_field"] is None
    assert [s["name"] for s in first["series"]] == ["revenue"]
    assert sorted(first["categories"]) == ["EU", "US"]
    assert first["row_count"] == 4 and first["total_rows"] == 4
    assert first["truncated"] is False
    assert first["masked_columns"] == []
    assert first["source"] == {"type": "definition", "id": def_id,
                               "name": "revenue-by-region-quarter",
                               "kind": "aggregate"}

    # ---- 6. Re-tune: quarter on the axis, region as the series ----
    r = await client.patch(f"/api/v1/datasets/{ds}/charts/{chart_id}", headers=h,
                           json={"config": {"x_field": "quarter",
                                            "y_fields": ["revenue"],
                                            "series_field": "region"}})
    assert r.status_code == 200, r.text
    edited = r.json()
    # Editing the encoding must not disturb the source or the chart type.
    assert edited["definition_id"] == def_id and edited["view_id"] is None
    assert edited["chart_type"] == "bar" and edited["name"] == "revenue"
    assert edited["config"]["series_field"] == "region"
    assert edited["updated_at"] >= chart["updated_at"]

    # ---- 7. Second render obeys the encoding, over the SAME numbers ----
    r = await client.post(f"/api/v1/datasets/{ds}/charts/{chart_id}/render", headers=h)
    assert r.status_code == 200, r.text
    second = r.json()
    assert second["x_field"] == "quarter" and second["series_field"] == "region"
    assert sorted(second["categories"]) == ["Q1", "Q2"]
    assert [s["name"] for s in second["series"]] == ["EU", "US"]
    plotted = {s["name"]: dict(zip(second["categories"], s["data"]))
               for s in second["series"]}
    assert plotted == {"EU": {"Q1": 100.0, "Q2": 150.0},
                       "US": {"Q1": 50.0, "Q2": 70.0}}
    # Same source rows as the preview in step 3, only reshaped.
    assert {(series, quarter): value
            for series, cells in plotted.items()
            for quarter, value in cells.items()} == preview_totals

    # ---- 8. Reload the chart the way a page refresh would ----
    reloaded = (await client.get(f"/api/v1/datasets/{ds}/charts/{chart_id}",
                                 headers=h)).json()
    assert reloaded["config"] == edited["config"]      # the encoding persisted
    assert reloaded["definition_id"] == def_id

    listing = (await client.get(f"/api/v1/datasets/{ds}/charts", headers=h)).json()
    assert listing["total"] == 1 and listing["limit"] == 50 and listing["offset"] == 0
    assert [c["id"] for c in listing["items"]] == [chart_id]
    assert listing["items"][0]["config"]["x_field"] == "quarter"

    # ---- 9. A stale encoding degrades, it does not break the chart ----
    r = await client.patch(f"/api/v1/datasets/{ds}/charts/{chart_id}", headers=h,
                           json={"config": {"x_field": "column_that_went_away",
                                            "y_fields": ["revenue"]}})
    assert r.status_code == 200
    degraded = (await client.post(
        f"/api/v1/datasets/{ds}/charts/{chart_id}/render", headers=h)).json()
    assert degraded["x_field"] == "region"     # ignored, re-inferred
    assert degraded["y_fields"] == ["revenue"]


# ---------------------------------------------------------------------------
# 2. The run-history table
# ---------------------------------------------------------------------------


async def test_a_run_history_table_pages_through_runs_without_dropping_or_repeating_one(
    client, admin_id,
):
    """SCREEN: definition detail → run history table with paging.

    Five runs, three green and two red, then page through them two at a time.
    A history table breaks if `total` disagrees between pages, if a run appears
    on two pages (or on none), if "newest first" only holds inside a page, or if
    scrolling past the end is an error rather than an empty page.
    """
    h = auth(admin_id)
    ds = await _sales_dataset(client, admin_id)
    base = f"/api/v1/datasets/{ds}/analytics"

    r = await client.post(base, headers=h, json={
        "name": "regional-revenue", "kind": "aggregate", "sheet": "data",
        "params": {"group_by": ["region"],
                   "aggregations": [{"column": "amount", "function": "sum",
                                     "alias": "revenue"}]}})
    assert r.status_code == 201, r.text
    def_id = r.json()["id"]

    # ---- 1. Three good runs ----
    succeeded = []
    for _ in range(3):
        r = await client.post(f"{base}/{def_id}/run", headers=h)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "completed"
        succeeded.append(r.json()["id"])

    # ---- 2. The author edits the sort column onto one that isn't there ----
    r = await client.patch(f"{base}/{def_id}", headers=h, json={
        "params": {"group_by": ["region"],
                   "aggregations": [{"column": "amount", "function": "sum",
                                     "alias": "revenue"}],
                   "sort_by": "profit"}})
    assert r.status_code == 200, r.text

    for _ in range(2):
        r = await client.post(f"{base}/{def_id}/run", headers=h)
        assert r.status_code == 400, r.text
        body = _problem(r)
        assert body["code"] == "unknown-column"
        assert body["columns"] == ["profit"]
        assert "revenue" in body["available"]      # what the picker should offer

    # ---- 3. Page 1: newest first, so the two failures lead ----
    runs_url = f"{base}/{def_id}/runs"
    p0 = (await client.get(runs_url, headers=h,
                           params={"limit": 2, "offset": 0})).json()
    assert p0["total"] == 5 and p0["limit"] == 2 and p0["offset"] == 0
    assert len(p0["items"]) == 2
    assert [i["status"] for i in p0["items"]] == ["failed", "failed"]
    # A history row renders these; a failed run must say when it ended and why.
    for item in p0["items"]:
        assert item["definition_id"] == def_id
        assert item["completed_at"] is not None
        assert "profit" in (item["error"] or "")
        assert item["artifact_id"] is None

    # ---- 4. Page 2 straddles the failure/success boundary ----
    p1 = (await client.get(runs_url, headers=h,
                           params={"limit": 2, "offset": 2})).json()
    assert p1["total"] == 5 and p1["offset"] == 2
    assert [i["status"] for i in p1["items"]] == ["completed", "completed"]
    assert all(i["artifact_id"] for i in p1["items"])
    assert all(i["result_summary"]["group_count"] == 2 for i in p1["items"])

    p2 = (await client.get(runs_url, headers=h,
                           params={"limit": 2, "offset": 4})).json()
    assert p2["total"] == 5 and len(p2["items"]) == 1
    assert p2["items"][0]["status"] == "completed"

    # ---- 5. The invariants the table depends on ----
    paged = [i for page in (p0, p1, p2) for i in page["items"]]
    ids = [i["id"] for i in paged]
    assert len(ids) == len(set(ids)) == 5             # no repeats, no drops
    assert set(succeeded) <= set(ids)                 # every run we made is here
    starts = [i["started_at"] for i in paged]
    assert starts == sorted(starts, reverse=True)     # DESC across page borders

    unpaged = (await client.get(runs_url, headers=h)).json()
    assert unpaged["total"] == 5
    assert [i["id"] for i in unpaged["items"]] == ids  # one page == the pages

    # ---- 6. Scrolling past the end is an empty page, not an error ----
    past = await client.get(runs_url, headers=h, params={"limit": 2, "offset": 10})
    assert past.status_code == 200, past.text
    assert past.json()["items"] == [] and past.json()["total"] == 5

    # ---- 7. An unknown definition id is a 404, not an empty history ----
    missing = await client.get(
        f"{base}/11111111-1111-1111-1111-111111111111/runs", headers=h)
    assert missing.status_code == 404
    assert _problem(missing)["code"] == "not_found"


# ---------------------------------------------------------------------------
# 3. Library permissions — viewer vs editor vs outsider
# ---------------------------------------------------------------------------


async def test_a_viewer_can_read_and_render_the_library_but_cannot_change_or_run_it(
    client, admin_id,
):
    """SCREEN: the library, opened by three different people.

    A UI greys buttons out from the caller's role, so the whole matrix has to be
    pinned: an in-team viewer gets a truthful 403 on every write, an outsider
    gets 404 everywhere (a 403 would confirm the dataset exists), and both
    branch on the `code` slug, never on the prose.
    """
    editor, team = await create_team_user(client, admin_id, "editor")
    viewer, _ = await create_team_user(client, admin_id, "viewer", team_id=team)
    outsider, _ = await create_team_user(client, admin_id, "admin")
    he, hv, ho = auth(editor), auth(viewer), auth(outsider)

    # ---- 1. The editor builds the library ----
    ds = await _sales_dataset(client, editor, team_id=team)
    base = f"/api/v1/datasets/{ds}"
    def_id = (await client.post(f"{base}/analytics", headers=he, json={
        "name": "revenue", "kind": "aggregate", "sheet": "data",
        "params": {"group_by": ["region"],
                   "aggregations": [{"column": "amount", "function": "sum",
                                     "alias": "revenue"}]}})).json()["id"]
    run = (await client.post(f"{base}/analytics/{def_id}/run", headers=he)).json()
    editor_totals = {row["region"]: row["revenue"] for row in run["result"]["data"]}
    chart_id = (await client.post(f"{base}/charts", headers=he, json={
        "name": "revenue-bar", "chart_type": "bar",
        "definition_id": def_id})).json()["id"]

    # ---- 2. Everything the viewer's library screen loads on mount ----
    for path in (f"{base}/analytics", f"{base}/analytics/{def_id}",
                 f"{base}/analytics/{def_id}/runs", f"{base}/charts",
                 f"{base}/charts/{chart_id}", f"{base}/lineage",
                 f"{base}/lineage/graph"):
        r = await client.get(path, headers=hv)
        assert r.status_code == 200, (path, r.text)

    defs = (await client.get(f"{base}/analytics", headers=hv)).json()
    assert defs["total"] == 1 and defs["items"][0]["name"] == "revenue"
    assert defs["items"][0]["created_by"] == editor      # attribution is visible
    history = (await client.get(f"{base}/analytics/{def_id}/runs", headers=hv)).json()
    assert history["total"] == 1 and history["items"][0]["triggered_by"] == editor

    # ---- 3. The viewer may RENDER, which computes the very numbers /run
    #         refuses to compute for them ----
    rendered = await client.post(f"{base}/charts/{chart_id}/render", headers=hv)
    assert rendered.status_code == 200, rendered.text
    body = rendered.json()
    assert dict(zip(body["categories"], body["series"][0]["data"])) == editor_totals

    # ...but running the definition that produces them is refused.
    refused = await client.post(f"{base}/analytics/{def_id}/run", headers=hv)
    assert refused.status_code == 403
    assert _problem(refused)["code"] == "forbidden"
    # ...and the refusal recorded nothing.
    assert (await client.get(f"{base}/analytics/{def_id}/runs",
                             headers=hv)).json()["total"] == 1

    # ---- 4. Every other write the viewer's buttons would trigger ----
    writes = [
        ("post", f"{base}/analytics", {"name": "nope", "kind": "profile"}),
        ("patch", f"{base}/analytics/{def_id}", {"name": "renamed"}),
        ("delete", f"{base}/analytics/{def_id}", None),
        ("post", f"{base}/analytics/runs/{run['id']}/publish",
         {"mode": "new_dataset", "name": f"v-{rid()}"}),
        ("post", f"{base}/charts",
         {"name": "nope", "chart_type": "bar", "definition_id": def_id}),
        ("patch", f"{base}/charts/{chart_id}", {"chart_type": "pie"}),
        ("delete", f"{base}/charts/{chart_id}", None),
    ]
    for method, path, payload in writes:
        kwargs = {"headers": hv}
        if payload is not None:
            kwargs["json"] = payload
        r = await getattr(client, method)(path, **kwargs)
        assert r.status_code == 403, (method, path, r.status_code, r.text)
        assert _problem(r)["code"] == "forbidden", path

    # Nothing the viewer tried changed anything.
    assert (await client.get(f"{base}/analytics", headers=hv)).json()["total"] == 1
    assert (await client.get(f"{base}/charts", headers=hv)).json()["total"] == 1
    assert (await client.get(f"{base}/analytics/{def_id}",
                             headers=hv)).json()["name"] == "revenue"

    # ---- 5. The outsider is not told any of it exists ----
    reads = [f"{base}/analytics", f"{base}/analytics/{def_id}",
             f"{base}/analytics/{def_id}/runs", f"{base}/charts",
             f"{base}/charts/{chart_id}", f"{base}/lineage", f"{base}/lineage/graph"]
    for path in reads:
        r = await client.get(path, headers=ho)
        assert r.status_code == 404, (path, r.status_code)
        assert _problem(r)["code"] == "not_found", path

    for method, path, payload in writes + [
            ("post", f"{base}/charts/{chart_id}/render", None)]:
        kwargs = {"headers": ho}
        if payload is not None:
            kwargs["json"] = payload
        r = await getattr(client, method)(path, **kwargs)
        assert r.status_code == 404, (method, path, r.status_code, r.text)
        assert _problem(r)["code"] == "not_found", path

    # ---- 6. The editor still has the full surface ----
    r = await client.post(f"{base}/analytics/{def_id}/run", headers=he)
    assert r.status_code == 200
    assert (await client.get(f"{base}/analytics/{def_id}/runs",
                             headers=he)).json()["total"] == 2


# ---------------------------------------------------------------------------
# 4. The lineage explorer
# ---------------------------------------------------------------------------


async def test_publishing_a_run_produces_a_dataset_whose_provenance_traces_both_ways(
    client, admin_id,
):
    """SCREEN: lineage explorer, walking a published-analytics chain.

    Publish an aggregate, chain a second aggregate onto the published output,
    publish that too, then walk the DAG. The explorer breaks if the one-hop
    `/lineage` panel and the `/lineage/graph` canvas disagree about the same
    edge, if the relation is not the kind-specific one the legend renders, or if
    `truncated` lies about whether "expand" would show more.
    """
    h = auth(admin_id)
    root = await _sales_dataset(client, admin_id)

    async def aggregate_and_publish(source, def_name, value_column, out_name):
        r = await client.post(f"/api/v1/datasets/{source}/analytics", headers=h, json={
            "name": def_name, "kind": "aggregate", "sheet": "data",
            "params": {"group_by": ["region"],
                       "aggregations": [{"column": value_column, "function": "sum",
                                         "alias": "revenue"}]}})
        assert r.status_code == 201, r.text
        def_id = r.json()["id"]
        r = await client.post(
            f"/api/v1/datasets/{source}/analytics/{def_id}/run", headers=h)
        assert r.status_code == 200, r.text
        run_id = r.json()["id"]
        r = await client.post(
            f"/api/v1/datasets/{source}/analytics/runs/{run_id}/publish",
            headers=h, json={"mode": "new_dataset", "name": out_name})
        assert r.status_code == 200, r.text
        return def_id, run_id, r.json()

    # ---- 1. Publish the first aggregate as a new dataset ----
    suffix = rid()
    _, root_run, published = await aggregate_and_publish(
        root, "by-region", "amount", f"revenue-{suffix}")
    child = published["dataset_id"]
    assert published["mode"] == "new_dataset"
    assert published["dataset_name"] == f"revenue-{suffix}"
    assert published["version_number"] == 1 and published["version_id"]

    # ---- 2. The published id resolves as an ordinary dataset ----
    meta = await client.get(f"/api/v1/datasets/{child}", headers=h)
    assert meta.status_code == 200, meta.text
    assert meta.json()["dataset_id"] == child
    assert meta.json()["row_count"] == 2            # EU + US
    assert {c["name"] for c in meta.json()["columns"]} == {"region", "revenue"}
    # The detail response carries no `name`, so the breadcrumb has to come from
    # the publish response or a second call to the catalog listing.
    catalog = (await client.get("/api/v1/datasets", headers=h)).json()["items"]
    assert {d["id"]: d["name"] for d in catalog}[child] == f"revenue-{suffix}"

    # ---- 3. One-hop lineage: the child points back at the run's source ----
    lin = (await client.get(f"/api/v1/datasets/{child}/lineage", headers=h)).json()
    assert lin["dataset_id"] == child and lin["children"] == []
    assert len(lin["parents"]) == 1
    parent = lin["parents"][0]
    assert parent["relation"] == "aggregated_from"
    assert parent["parent_visible"] is True
    assert parent["parent_dataset_id"] == root
    assert parent["parent_version_number"] == 1
    assert parent["version_number"] == 1

    # ---- 4. The DAG agrees with the panel on that same edge ----
    graph = (await client.get(f"/api/v1/datasets/{child}/lineage/graph",
                              headers=h)).json()
    assert graph["max_depth"] == 10 and graph["hidden_nodes"] == 0
    assert graph["truncated"] is False
    assert {n["id"] for n in graph["nodes"]} == {root, child}
    assert next(n for n in graph["nodes"] if n["id"] == child)["is_root"] is True
    assert next(n for n in graph["nodes"] if n["id"] == root)["is_root"] is False
    assert [(e["child_id"], e["parent_id"], e["relation"], e["depth"])
            for e in graph["edges"]] == [(child, root, "aggregated_from", 1)]
    # The two endpoints must not disagree about the same derivation.
    assert {p["parent_dataset_id"] for p in lin["parents"]} == {
        e["parent_id"] for e in graph["edges"] if e["child_id"] == child}

    # ---- 5. Looking from the root, the same edge points downstream ----
    root_lin = (await client.get(f"/api/v1/datasets/{root}/lineage",
                                 headers=h)).json()
    assert root_lin["parents"] == []
    assert [(c["child_dataset_id"], c["relation"], c["child_visible"])
            for c in root_lin["children"]] == [(child, "aggregated_from", True)]
    assert root_lin["children"][0]["child_dataset_name"] == f"revenue-{suffix}"

    root_graph = (await client.get(f"/api/v1/datasets/{root}/lineage/graph",
                                   headers=h)).json()
    assert {n["id"] for n in root_graph["nodes"]} == {root, child}
    assert next(n for n in root_graph["nodes"] if n["id"] == root)["is_root"] is True

    # ---- 6. The published output is itself a first-class analytics source ----
    columns = (await client.get(f"/api/v1/datasets/{child}/sheets",
                                headers=h)).json()["items"][0]["columns"]
    assert {c["normalized_name"] for c in columns} == {"region", "revenue"}

    _, _, republished = await aggregate_and_publish(
        child, "rollup", "revenue", f"rollup-{suffix}")
    grandchild = republished["dataset_id"]

    # ---- 7. Two aggregated_from hops, walked in one call ----
    deep = (await client.get(f"/api/v1/datasets/{grandchild}/lineage/graph",
                             headers=h)).json()
    assert {n["id"] for n in deep["nodes"]} == {root, child, grandchild}
    edges = {(e["child_id"], e["parent_id"]): e for e in deep["edges"]}
    assert edges[(grandchild, child)]["depth"] == 1
    assert edges[(child, root)]["depth"] == 2
    assert {e["relation"] for e in deep["edges"]} == {"aggregated_from"}
    assert deep["truncated"] is False

    # ---- 8. Collapsing the canvas to one hop says there IS more to expand ----
    shallow = (await client.get(f"/api/v1/datasets/{grandchild}/lineage/graph",
                                headers=h, params={"max_depth": 1})).json()
    assert shallow["max_depth"] == 1 and shallow["truncated"] is True
    assert {n["id"] for n in shallow["nodes"]} == {child, grandchild}
    assert root not in {n["id"] for n in shallow["nodes"]}

    # ---- 9. Publishing the same run again as a NEW VERSION of the source ----
    r = await client.post(
        f"/api/v1/datasets/{root}/analytics/runs/{root_run}/publish",
        headers=h, json={"mode": "new_version"})
    assert r.status_code == 200, r.text
    assert r.json()["dataset_id"] == root and r.json()["version_number"] == 2
    versions = (await client.get(f"/api/v1/datasets/{root}/versions",
                                 headers=h)).json()["items"]
    assert [v["version_number"] for v in versions] == [2, 1]
    assert versions[0]["row_count"] == 2

    # The self-derivation shows in the one-hop panel but is not a graph edge.
    root_lin = (await client.get(f"/api/v1/datasets/{root}/lineage",
                                 headers=h)).json()
    assert root in {c["child_dataset_id"] for c in root_lin["children"]}
    root_graph = (await client.get(f"/api/v1/datasets/{root}/lineage/graph",
                                   headers=h)).json()
    assert all(e["child_id"] != e["parent_id"] for e in root_graph["edges"])

    # ---- 10. A run id from another dataset does not publish here ----
    r = await client.post(
        f"/api/v1/datasets/{child}/analytics/runs/{root_run}/publish",
        headers=h, json={"mode": "new_version"})
    assert r.status_code == 404
    assert _problem(r)["code"] == "not_found"


# ---------------------------------------------------------------------------
# 5. Repairing a definition from the error the API returned
# ---------------------------------------------------------------------------


async def test_a_definition_saved_without_a_sheet_is_repaired_from_the_error_body(
    client, admin_id, tmp_path,
):
    """FLOW: run fails with sheet-selection-required → sheet picker → re-run.

    The `sheets` field on that 400 exists so a UI can render a picker. This
    proves a value taken straight from it, fed back through PATCH, actually
    fixes the definition — and that the run history then shows the mixed
    failed-then-succeeded state a history screen has to render.
    """
    h = auth(admin_id)
    path = tmp_path / "payments.xlsx"
    make_workbook(path)
    ds = (await upload_file(client, admin_id, path,
                            content_type="application/octet-stream"))["dataset_id"]
    base = f"/api/v1/datasets/{ds}"

    # ---- 1. Saved without naming a sheet — accepted, because saving is not running ----
    r = await client.post(f"{base}/analytics", headers=h, json={
        "name": "cost-by-item", "kind": "aggregate",
        "params": {"group_by": ["Item"],
                   "aggregations": [{"column": "Cost", "function": "sum",
                                     "alias": "total_cost"}]}})
    assert r.status_code == 201, r.text
    def_id = r.json()["id"]
    assert r.json()["sheet"] is None

    # ---- 2. Running it is where the workbook's ambiguity bites ----
    r = await client.post(f"{base}/analytics/{def_id}/run", headers=h)
    assert r.status_code == 400, r.text
    problem = _problem(r)
    assert problem["code"] == "sheet-selection-required"
    offered = problem["sheets"]
    assert set(offered) == {"Revenue", "Expenses", "Secrets"}

    # ---- 3. The failure is recorded, so the history screen can show it ----
    hist = (await client.get(f"{base}/analytics/{def_id}/runs", headers=h)).json()
    assert hist["total"] == 1 and hist["items"][0]["status"] == "failed"
    failed_run = hist["items"][0]["id"]
    assert hist["items"][0]["artifact_id"] is None

    # ---- 4. Pick a sheet FROM THE ERROR BODY and repair the definition ----
    chosen = next(name for name in offered if name == "Expenses")
    r = await client.patch(f"{base}/analytics/{def_id}", headers=h,
                           json={"sheet": chosen})
    assert r.status_code == 200, r.text
    assert r.json()["sheet"] == chosen
    assert r.json()["params"]["group_by"] == ["Item"]   # the edit was surgical
    assert (await client.get(f"{base}/analytics/{def_id}",
                             headers=h)).json()["sheet"] == chosen

    # ---- 5. Same definition, same button, now it runs ----
    r = await client.post(f"{base}/analytics/{def_id}/run", headers=h)
    assert r.status_code == 200, r.text
    ok = r.json()
    assert ok["status"] == "completed" and ok["artifact_id"]
    assert {row["Item"]: row["total_cost"] for row in ok["result"]["data"]} == {
        "rent": 50, "power": 20}

    # ---- 6. The history now carries both outcomes, newest first ----
    hist = (await client.get(f"{base}/analytics/{def_id}/runs", headers=h)).json()
    assert hist["total"] == 2
    assert [i["status"] for i in hist["items"]] == ["completed", "failed"]
    assert hist["items"][1]["id"] == failed_run
    assert "sheet" in (hist["items"][1]["error"] or "").lower()

    # ---- 7. A chart over the repaired definition renders ----
    chart_id = (await client.post(f"{base}/charts", headers=h, json={
        "name": "costs", "chart_type": "bar", "definition_id": def_id})).json()["id"]
    r = await client.post(f"{base}/charts/{chart_id}/render", headers=h)
    assert r.status_code == 200, r.text
    rendered = r.json()
    assert rendered["x_field"] == "Item"
    assert sorted(rendered["categories"]) == ["power", "rent"]
    assert dict(zip(rendered["categories"],
                    rendered["series"][0]["data"])) == {"rent": 50, "power": 20}
    assert rendered["source"]["kind"] == "aggregate"

    # ---- 8. Re-pointing the definition at the other sheet re-points the chart ----
    r = await client.patch(f"{base}/analytics/{def_id}", headers=h, json={
        "sheet": "Revenue",
        "params": {"group_by": ["Region"],
                   "aggregations": [{"column": "Amount", "function": "sum",
                                     "alias": "total_cost"}]}})
    assert r.status_code == 200, r.text
    rendered = (await client.post(f"{base}/charts/{chart_id}/render",
                                  headers=h)).json()
    assert rendered["x_field"] == "Region"
    assert sorted(rendered["categories"]) == ["APAC", "EU", "US"]


# ---------------------------------------------------------------------------
# 6. Dashboard mount — list, then render each tile
# ---------------------------------------------------------------------------


async def test_a_dashboard_loads_every_saved_chart_and_finds_the_one_that_cannot_render(
    client, admin_id,
):
    """SCREEN: dashboard mount — GET /charts, then render every id it returned.

    A dashboard has nothing but the list response to build its tiles from. This
    drives all four chart sources (aggregate, pivot, sample, saved view) through
    render, and pins the fact that a profile-backed chart lists exactly like the
    others but answers 400 on render, so the dashboard must handle a tile that
    can never load.
    """
    h = auth(admin_id)
    ds = await _sales_dataset(client, admin_id)
    base = f"/api/v1/datasets/{ds}"

    async def definition(name, kind, params):
        r = await client.post(f"{base}/analytics", headers=h, json={
            "name": name, "kind": kind, "sheet": "data", "params": params})
        assert r.status_code == 201, r.text
        return r.json()["id"]

    agg = await definition("a-aggregate", "aggregate", {
        "group_by": ["region"],
        "aggregations": [{"column": "amount", "function": "sum",
                          "alias": "revenue"}]})
    piv = await definition("b-pivot", "pivot", {
        "rows": ["region"], "columns": "quarter",
        "values": [{"column": "amount", "function": "sum", "alias": "revenue"}]})
    smp = await definition("c-sample", "sample", {
        "target_total_volume": 4,
        "sampling_steps": [{"method": "random", "sample_size": 4}], "seed": 7})
    prof = await definition("d-profile", "profile", {"include_histograms": False})

    r = await client.post(f"{base}/views", headers=h, json={
        "name": "e-view", "sheet": "data",
        "query": {"sort": [{"column": "amount", "direction": "desc"}]}})
    assert r.status_code == 201, r.text
    view_id = r.json()["id"]

    # ---- 1. Five definitions/views, five saved charts ----
    tiles = [("agg-tile", "bar", {"definition_id": agg}),
             ("pivot-tile", "bar", {"definition_id": piv}),
             ("sample-tile", "table", {"definition_id": smp}),
             ("profile-tile", "kpi", {"definition_id": prof}),
             ("view-tile", "line", {"view_id": view_id})]
    for name, chart_type, source in tiles:
        r = await client.post(f"{base}/charts", headers=h, json={
            "name": name, "chart_type": chart_type, **source})
        assert r.status_code == 201, (name, r.text)

    defs = (await client.get(f"{base}/analytics", headers=h)).json()
    assert defs["total"] == 4
    assert [d["name"] for d in defs["items"]] == [
        "a-aggregate", "b-pivot", "c-sample", "d-profile"]     # name-ordered

    # ---- 2. Mount: one list call is all the dashboard gets ----
    listing = (await client.get(f"{base}/charts", headers=h)).json()
    assert listing["total"] == 5
    assert [c["name"] for c in listing["items"]] == [
        "agg-tile", "pivot-tile", "profile-tile", "sample-tile", "view-tile"]
    by_name = {c["name"]: c for c in listing["items"]}
    # Everything a tile needs to render itself is on the list row.
    for chart in listing["items"]:
        assert (chart["definition_id"] is None) != (chart["view_id"] is None)
        assert chart["chart_type"] and chart["id"]

    # ---- 3. Render every id the list returned ----
    async def render(name):
        return await client.post(f"{base}/charts/{by_name[name]['id']}/render",
                                 headers=h)

    r = await render("agg-tile")
    assert r.status_code == 200, r.text
    assert dict(zip(r.json()["categories"],
                    r.json()["series"][0]["data"])) == {"EU": 250.0, "US": 120.0}

    r = await render("pivot-tile")
    assert r.status_code == 200, r.text
    pivoted = r.json()
    assert pivoted["source"] == {"type": "definition", "id": piv,
                                 "name": "b-pivot", "kind": "pivot"}
    assert pivoted["x_field"] == "region"
    assert sorted(pivoted["categories"]) == ["EU", "US"]
    # A pivot widens the quarter dimension into output columns, so the series
    # names are data values — the dashboard legend cannot be built from config.
    assert sorted(s["name"] for s in pivoted["series"]) == ["Q1", "Q2"]
    cells = {s["name"]: dict(zip(pivoted["categories"], s["data"]))
             for s in pivoted["series"]}
    assert cells == {"Q1": {"EU": 100.0, "US": 50.0},
                     "Q2": {"EU": 150.0, "US": 70.0}}

    r = await render("sample-tile")
    assert r.status_code == 200, r.text
    sampled = r.json()
    assert sampled["source"]["kind"] == "sample"
    assert sampled["row_count"] == 4 and sampled["chart_type"] == "table"
    assert sampled["x_field"] in {"region", "quarter"}

    r = await render("view-tile")
    assert r.status_code == 200, r.text
    viewed = r.json()
    assert viewed["source"] == {"type": "view", "id": view_id, "name": "e-view"}
    assert viewed["row_count"] == 4 and viewed["total_rows"] == 4
    assert viewed["truncated"] is False

    # ---- 4. The tile that can never load, from a list row that looked fine ----
    r = await render("profile-tile")
    assert r.status_code == 400, r.text
    problem = _problem(r)
    assert problem["code"] == "kind-not-chartable" and problem["kind"] == "profile"
    # Nothing on the list row predicted it: the source kind is not carried there.
    assert "kind" not in by_name["profile-tile"]

    # ---- 5. The author retires the broken tile ----
    r = await client.delete(f"{base}/charts/{by_name['profile-tile']['id']}",
                            headers=h)
    assert r.status_code == 204
    after = (await client.get(f"{base}/charts", headers=h)).json()
    assert after["total"] == 4
    assert "profile-tile" not in {c["name"] for c in after["items"]}
    gone = await client.get(f"{base}/charts/{by_name['profile-tile']['id']}",
                            headers=h)
    assert gone.status_code == 404 and _problem(gone)["code"] == "not_found"
    # Deleting the chart left its definition alone.
    assert (await client.get(f"{base}/analytics/{prof}", headers=h)).status_code == 200

    # ---- 6. Paging the dashboard list keeps the true total ----
    page = (await client.get(f"{base}/charts", headers=h,
                             params={"limit": 2, "offset": 2})).json()
    assert page["total"] == 4 and page["limit"] == 2 and page["offset"] == 2
    assert [c["name"] for c in page["items"]] == ["sample-tile", "view-tile"]


# ---------------------------------------------------------------------------
# 7. Library management — renames and retirement
# ---------------------------------------------------------------------------


async def test_a_librarian_hits_the_same_name_collision_creating_and_renaming(
    client, admin_id,
):
    """SCREEN: library list with inline rename and a delete confirmation.

    Rename is the most ordinary edit the library offers, and a UI has to show
    the same "that name is taken" for a collision whether it happened on create
    or on rename. Retiring a definition must also take its charts with it, so
    the dashboard cannot keep a tile whose source is gone.
    """
    h = auth(admin_id)
    ds = await _sales_dataset(client, admin_id)
    base = f"/api/v1/datasets/{ds}"
    params = {"group_by": ["region"],
              "aggregations": [{"column": "amount", "function": "sum",
                                "alias": "revenue"}]}

    # ---- 1. Two definitions ----
    a = (await client.post(f"{base}/analytics", headers=h, json={
        "name": "alpha", "kind": "aggregate", "sheet": "data",
        "params": params})).json()["id"]
    r = await client.post(f"{base}/analytics", headers=h, json={
        "name": "beta", "kind": "aggregate", "sheet": "data", "params": params})
    assert r.status_code == 201, r.text
    b = r.json()["id"]

    # ---- 2. Creating a third under a taken name ----
    dup = await client.post(f"{base}/analytics", headers=h, json={
        "name": "alpha", "kind": "profile"})
    assert dup.status_code == 409
    create_problem = _problem(dup)
    assert "alpha" in create_problem["detail"]

    # ---- 3. Renaming onto the same taken name — the same user-visible outcome ----
    ren = await client.patch(f"{base}/analytics/{b}", headers=h,
                             json={"name": "alpha"})
    assert ren.status_code == 409, ren.text
    rename_problem = _problem(ren)
    assert rename_problem["code"] == "definition-name-taken"
    assert rename_problem["name"] == "alpha"
    # The refused rename left the row alone.
    assert (await client.get(f"{base}/analytics/{b}", headers=h)).json()["name"] == "beta"

    # ---- 4. A free name works, and the name-ordered list reorders ----
    r = await client.patch(f"{base}/analytics/{b}", headers=h,
                           json={"name": "aardvark"})
    assert r.status_code == 200, r.text
    listing = (await client.get(f"{base}/analytics", headers=h)).json()
    assert [d["name"] for d in listing["items"]] == ["aardvark", "alpha"]
    assert listing["total"] == 2

    # ---- 5. Charts collide the same way ----
    c1 = (await client.post(f"{base}/charts", headers=h, json={
        "name": "c-one", "chart_type": "bar", "definition_id": a})).json()["id"]
    c2 = (await client.post(f"{base}/charts", headers=h, json={
        "name": "c-two", "chart_type": "bar", "definition_id": b})).json()["id"]

    dup = await client.post(f"{base}/charts", headers=h, json={
        "name": "c-one", "chart_type": "pie", "definition_id": a})
    assert dup.status_code == 409 and "c-one" in _problem(dup)["detail"]

    ren = await client.patch(f"{base}/charts/{c2}", headers=h, json={"name": "c-one"})
    assert ren.status_code == 409, ren.text
    assert _problem(ren)["code"] == "chart-name-taken"
    assert (await client.get(f"{base}/charts/{c2}", headers=h)).json()["name"] == "c-two"

    # ---- 6. A chart cannot be left pointing at nothing ----
    r = await client.patch(f"{base}/charts/{c2}", headers=h,
                           json={"definition_id": None})
    assert r.status_code == 400
    assert _problem(r)["code"] == "chart-source-required"
    assert (await client.get(f"{base}/charts/{c2}",
                             headers=h)).json()["definition_id"] == b

    # ---- 7. Retiring a definition takes its chart with it ----
    r = await client.delete(f"{base}/analytics/{a}", headers=h)
    assert r.status_code == 204
    assert (await client.get(f"{base}/analytics/{a}", headers=h)).status_code == 404
    assert (await client.get(f"{base}/charts/{c1}", headers=h)).status_code == 404
    remaining = (await client.get(f"{base}/charts", headers=h)).json()
    assert [c["id"] for c in remaining["items"]] == [c2]
    assert remaining["total"] == 1

    # ---- 8. Deleting it again is a 404, so a double-click is not a 500 ----
    again = await client.delete(f"{base}/analytics/{a}", headers=h)
    assert again.status_code == 404 and _problem(again)["code"] == "not_found"
    again = await client.delete(f"{base}/charts/{c1}", headers=h)
    assert again.status_code == 404 and _problem(again)["code"] == "not_found"

    # ---- 9. The freed name can now be reused ----
    r = await client.post(f"{base}/analytics", headers=h, json={
        "name": "alpha", "kind": "aggregate", "sheet": "data", "params": params})
    assert r.status_code == 201, r.text
