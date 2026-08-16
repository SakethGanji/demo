"""Analytics error paths + pagination — the 4xx contracts a UI depends on.

Aggregate/profile/sample/download rejections carry actionable details, join
column collisions stay addressable, and list pagination actually pages.
"""

from __future__ import annotations

from openpyxl import Workbook

from conftest import auth, make_crm_workbook, upload_file, upload_inline


async def _crm(client, admin_id, tmp_path):
    p = tmp_path / "crm.xlsx"
    make_crm_workbook(p)
    return (await upload_file(client, admin_id, p))["dataset_id"]


async def test_aggregate_unknown_columns_and_functions(client, admin_id, tmp_path):
    ds = await _crm(client, admin_id, tmp_path)
    h = auth(admin_id)
    base = {"dataset_id": ds, "sheet": "Orders"}

    r = await client.post("/api/v1/aggregate", headers=h, json={
        **base, "group_by": ["ghost"],
        "aggregations": [{"column": "total", "function": "sum"}]})
    assert r.status_code == 400 and "ghost" in r.json()["detail"]

    r = await client.post("/api/v1/aggregate", headers=h, json={
        **base, "group_by": ["customer_id"],
        "aggregations": [{"column": "ghost", "function": "sum"}]})
    assert r.status_code == 400 and "ghost" in r.json()["detail"]

    r = await client.post("/api/v1/aggregate", headers=h, json={
        **base, "group_by": ["customer_id"],
        "aggregations": [{"column": "total", "function": "median_abs"}]})
    assert r.status_code == 400 and "Allowed" in r.json()["detail"]


async def test_the_published_schema_names_the_functions_the_400_enforces(
        client, admin_id, tmp_path):
    """The enum in the OpenAPI document and the runtime 400 are two halves of
    one contract, and the tension between them is the whole design.

    Publishing the vocabulary as a JSON Schema ``enum`` is what lets a generated
    client — or a model reading the spec — see the valid values without parsing
    the description. Doing it with a ``Literal`` instead would have moved
    rejection into pydantic, turning the message below into a 422 whose
    top-level ``detail`` is the generic "Request validation failed". So the enum
    is schema metadata only, and this test asserts both halves at once.
    """
    from app.main import app
    from app.shared.constants import AGG_FUNCTIONS

    published = app.openapi()["components"]["schemas"]["AggregationSpec"]
    assert published["properties"]["function"]["enum"] == list(AGG_FUNCTIONS)

    ds = await _crm(client, admin_id, tmp_path)
    r = await client.post("/api/v1/aggregate", headers=auth(admin_id), json={
        "dataset_id": ds, "sheet": "Orders", "group_by": ["customer_id"],
        "aggregations": [{"column": "total", "function": "median_abs"}]})
    # Still 400, still the service's own message — NOT a 422 from pydantic.
    assert r.status_code == 400, r.text
    detail = r.json()["detail"]
    assert detail.startswith("Unknown aggregation function: median_abs. Allowed: ")
    assert "errors" not in r.json()
    for function in AGG_FUNCTIONS:
        assert repr(function) in detail


async def test_aggregate_join_error_paths(client, admin_id, tmp_path):
    ds = await _crm(client, admin_id, tmp_path)
    h = auth(admin_id)
    body = {"dataset_id": ds, "sheet": "Orders",
            "group_by": ["tier"],
            "aggregations": [{"column": "total", "function": "sum"}]}

    r = await client.post("/api/v1/aggregate", headers=h, json={
        **body, "join": {"sheet": "Customers", "left_on": "ghost",
                         "right_on": "customer_id"}})
    assert r.status_code == 400 and "base sheet" in r.json()["detail"]

    r = await client.post("/api/v1/aggregate", headers=h, json={
        **body, "join": {"sheet": "Customers", "left_on": "customer_id",
                         "right_on": "ghost"}})
    assert r.status_code == 400 and "Customers" in r.json()["detail"]

    r = await client.post("/api/v1/aggregate", headers=h, json={
        **body, "join": {"sheet": "Ghost", "left_on": "customer_id",
                         "right_on": "customer_id"}})
    assert r.status_code == 404  # unknown sheet resolves like any sheet miss


async def test_aggregate_join_collision_prefixing(client, admin_id, tmp_path):
    """Non-key columns sharing a name across sheets get a {sheet}_{col} alias."""
    wb = Workbook()
    a = wb.active
    a.title = "Accounts"
    a.append(["id", "name", "balance"])
    for r in ([1, "alice", 10.0], [2, "bob", 20.0], [3, "alice", 5.0]):
        a.append(r)
    b = wb.create_sheet("Managers")
    b.append(["id", "name"])          # 'name' collides with Accounts.name
    for r in ([1, "kim"], [2, "lee"], [3, "kim"]):
        b.append(r)
    p = tmp_path / "collide.xlsx"
    wb.save(p)
    ds = (await upload_file(client, admin_id, p))["dataset_id"]

    r = await client.post("/api/v1/aggregate", headers=auth(admin_id), json={
        "dataset_id": ds, "sheet": "Accounts",
        "join": {"sheet": "Managers", "left_on": "id", "right_on": "id"},
        "group_by": ["Managers_name"],
        "aggregations": [{"column": "balance", "function": "sum"}]})
    assert r.status_code == 200, r.text
    rows = {row["Managers_name"]: row["balance_sum"] for row in r.json()["data"]}
    assert rows == {"kim": 15.0, "lee": 20.0}


async def test_profile_histograms_on_numeric_columns(client, admin_id, tmp_path):
    ds = await _crm(client, admin_id, tmp_path)
    r = await client.post("/api/v1/profile", headers=auth(admin_id), json={
        "dataset_id": ds, "sheet": "Orders", "include_histograms": True})
    assert r.status_code == 200, r.text
    cols = {c["name"]: c for c in r.json()["columns"]}
    total = cols["total"]
    assert total["histogram"], "numeric column should carry histogram bins"
    bin0 = total["histogram"][0]
    assert {"bin_start", "bin_end", "count"} <= set(bin0)


async def test_sample_size_validation_and_clamping(client, admin_id):
    ds = (await upload_inline(client, admin_id,
                              '[{"a": 1}, {"a": 2}, {"a": 3}]'))["dataset_id"]
    h = auth(admin_id)

    # Nonsense sizes are schema-rejected up front (422), never a query error.
    for bad in ({"target_total_volume": 2,
                 "sampling_steps": [{"method": "random", "sample_size": -5}]},
                {"target_total_volume": 2,
                 "sampling_steps": [{"method": "random", "sample_size": 0}]},
                {"target_total_volume": -1,
                 "sampling_steps": [{"method": "random", "sample_size": 2}]}):
        r = await client.post("/api/v1/sample", headers=h,
                              json={"dataset_id": ds, **bad})
        assert r.status_code == 422, r.text

    # Oversize asks are clamped to the pool, not errored.
    r = await client.post("/api/v1/sample", headers=h, json={
        "dataset_id": ds, "target_total_volume": 500000,
        "sampling_steps": [{"method": "random", "sample_size": 500000}]})
    assert r.status_code == 200 and r.json()["sampled_count"] == 3


async def test_download_unknown_format(client, admin_id):
    ds = (await upload_inline(client, admin_id, '[{"a": 1}]'))["dataset_id"]
    r = await client.get(f"/api/v1/datasets/{ds}/download",
                         params={"format": "yaml"}, headers=auth(admin_id))
    assert r.status_code == 400 and "Unsupported format" in r.json()["detail"]


async def test_tag_listing(client, admin_id):
    ds = (await upload_inline(client, admin_id, '[{"a": 1}]'))["dataset_id"]
    h = auth(admin_id)
    for tag in ("production", "staging"):
        r = await client.put(f"/api/v1/datasets/{ds}/tags", headers=h,
                             json={"tag_name": tag, "version_number": 1})
        assert r.status_code == 200
    tags = (await client.get(f"/api/v1/datasets/{ds}/tags", headers=h)).json()
    assert {t["tag_name"] for t in tags["items"]} == {"production", "staging"}


async def test_dataset_listing_pagination_actually_pages(client, admin_id):
    h = auth(admin_id)
    ids = set()
    for i in range(3):
        ids.add((await upload_inline(client, admin_id,
                                     f'[{{"n": {i}}}]'))["dataset_id"])

    page1 = (await client.get("/api/v1/datasets", params={"limit": 2, "offset": 0},
                              headers=h)).json()
    page2 = (await client.get("/api/v1/datasets", params={"limit": 2, "offset": 2},
                              headers=h)).json()
    assert page1["total"] == 3 and page2["total"] == 3  # exact — DB is truncated
    assert len(page1["items"]) == 2 and len(page2["items"]) == 1
    assert page1["limit"] == 2 and page2["offset"] == 2

    seen = {d["id"] for d in page1["items"]} | {d["id"] for d in page2["items"]}
    assert seen == ids  # disjoint pages that cover everything

    beyond = (await client.get("/api/v1/datasets", params={"limit": 2, "offset": 4},
                               headers=h)).json()
    assert beyond["items"] == [] and beyond["total"] == 3

    r = await client.get("/api/v1/datasets", params={"limit": 0}, headers=h)
    assert r.status_code == 422  # limit must be >= 1
