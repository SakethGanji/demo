"""`GET /analytics` and `GET /charts` page like every other list route.

Both used to read the whole table and synthesize an envelope from what they
happened to return: `limit = len(items)`, `offset = 0`, `total = len(items)`.
That envelope is unusable — `limit` reported the response size rather than the
page size (0 for an empty dataset), `?limit=`/`?offset=` were silently ignored,
and there was no way to tell "one full page" from "everything there is".

The same route family also documents lineage as `list[dict]`, so nothing pinned
the fields a client reads or the `parent_visible`/`child_visible` flags that
carry the cross-team 404 contract.
"""

from __future__ import annotations

from conftest import auth, upload_inline

ROWS = '[{"a": 1, "b": "x"}, {"a": 2, "b": "y"}, {"a": 3, "b": "x"}]'


async def _dataset(client, admin_id):
    return (await upload_inline(client, admin_id, ROWS))["dataset_id"]


async def _make_definitions(client, h, ds, count):
    ids = []
    for i in range(count):
        r = await client.post(f"/api/v1/datasets/{ds}/analytics", headers=h, json={
            "name": f"def-{i:02d}", "kind": "aggregate",
            "params": {"group_by": ["b"], "aggregations": [
                {"column": "a", "function": "sum"}]}})
        assert r.status_code == 201, r.text
        ids.append(r.json()["id"])
    return ids


async def test_listing_saved_definitions_honours_limit_and_offset(client, admin_id):
    """A page of definitions must describe the page the caller asked for.

    Production impact: a dataset with hundreds of saved definitions returned all
    of them in one unbounded response, and the envelope's `limit` echoed the row
    count, so a client could neither request the next page nor detect that it
    had been handed the whole table.
    """
    h = auth(admin_id)
    ds = await _dataset(client, admin_id)
    await _make_definitions(client, h, ds, 5)

    r = await client.get(f"/api/v1/datasets/{ds}/analytics", headers=h,
                         params={"limit": 2, "offset": 0})
    assert r.status_code == 200, r.text
    page = r.json()
    assert page["limit"] == 2 and page["offset"] == 0
    assert page["total"] == 5
    assert [d["name"] for d in page["items"]] == ["def-00", "def-01"]

    r = await client.get(f"/api/v1/datasets/{ds}/analytics", headers=h,
                         params={"limit": 2, "offset": 4})
    page = r.json()
    assert page["limit"] == 2 and page["offset"] == 4
    assert page["total"] == 5
    assert [d["name"] for d in page["items"]] == ["def-04"]


async def test_an_empty_definition_list_reports_the_requested_limit_not_zero(
        client, admin_id):
    """An empty page must still describe the page size that was asked for.

    Production impact: `limit: 0` is not a legal page size — a client that
    echoes the envelope back as its next request gets a 422.
    """
    h = auth(admin_id)
    ds = await _dataset(client, admin_id)

    page = (await client.get(f"/api/v1/datasets/{ds}/analytics", headers=h,
                             params={"limit": 25})).json()
    assert page["items"] == []
    assert page["total"] == 0
    assert page["limit"] == 25
    assert page["offset"] == 0


async def test_listing_saved_charts_honours_limit_and_offset(client, admin_id):
    """Charts page on the same envelope as definitions.

    Production impact: identical to the definitions route — an unbounded read
    with a fabricated `limit`, so paging a chart gallery was impossible.
    """
    h = auth(admin_id)
    ds = await _dataset(client, admin_id)
    definitions = await _make_definitions(client, h, ds, 1)
    for i in range(4):
        r = await client.post(f"/api/v1/datasets/{ds}/charts", headers=h, json={
            "name": f"chart-{i:02d}", "chart_type": "bar",
            "definition_id": definitions[0], "config": {}})
        assert r.status_code == 201, r.text

    page = (await client.get(f"/api/v1/datasets/{ds}/charts", headers=h,
                             params={"limit": 3, "offset": 1})).json()
    assert page["limit"] == 3 and page["offset"] == 1
    assert page["total"] == 4
    assert [c["name"] for c in page["items"]] == ["chart-01", "chart-02", "chart-03"]

    empty = (await client.get(f"/api/v1/datasets/{ds}/charts", headers=h,
                              params={"limit": 10, "offset": 99})).json()
    assert empty["items"] == []
    assert empty["total"] == 4 and empty["limit"] == 10 and empty["offset"] == 99


async def test_the_definition_list_rejects_an_out_of_range_limit(client, admin_id):
    """`limit` is validated, not silently ignored.

    Production impact: the route accepted any `limit` and ignored it, so a
    caller asking for 1_000_000 got a success and no signal that the parameter
    meant nothing.
    """
    h = auth(admin_id)
    ds = await _dataset(client, admin_id)
    r = await client.get(f"/api/v1/datasets/{ds}/analytics", headers=h,
                         params={"limit": 1000})
    assert r.status_code == 422, r.text


async def test_the_lineage_response_declares_its_parent_and_child_fields(
        client, admin_id):
    """Lineage entries are a typed contract, not opaque dicts.

    Production impact: `list[dict[str, Any]]` documented nothing in the OpenAPI
    schema, so the `parent_visible`/`child_visible` flags that carry the
    cross-team 404 contract were invisible to every generated client — a
    consumer had no way to know a blanked parent means "withheld" rather than
    "no parent".
    """
    h = auth(admin_id)
    ds = await _dataset(client, admin_id)
    definitions = await _make_definitions(client, h, ds, 1)
    run = (await client.post(
        f"/api/v1/datasets/{ds}/analytics/{definitions[0]}/run", headers=h)).json()
    assert run["status"] == "completed", run
    published = await client.post(
        f"/api/v1/datasets/{ds}/analytics/runs/{run['id']}/publish", headers=h,
        json={"mode": "new_dataset", "name": f"derived-{run['id'][:8]}"})
    assert published.status_code == 200, published.text
    derived = published.json()["dataset_id"]

    parents = (await client.get(f"/api/v1/datasets/{derived}/lineage",
                                headers=h)).json()["parents"]
    assert len(parents) == 1
    parent = parents[0]
    assert parent["parent_visible"] is True
    assert parent["parent_dataset_id"] == ds
    assert parent["relation"] == "aggregated_from"
    assert isinstance(parent["version_number"], int)

    children = (await client.get(f"/api/v1/datasets/{ds}/lineage",
                                 headers=h)).json()["children"]
    assert len(children) == 1
    child = children[0]
    assert child["child_visible"] is True
    assert child["child_dataset_id"] == derived

    # The declared schema is what makes the flags discoverable.
    schema = (await client.get("/openapi.json")).json()["components"]["schemas"]
    assert "parent_visible" in schema["LineageParent"]["properties"]
    assert "child_visible" in schema["LineageChild"]["properties"]
    lineage = schema["LineageResponse"]["properties"]
    assert lineage["parents"]["items"]["$ref"].endswith("/LineageParent")
    assert lineage["children"]["items"]["$ref"].endswith("/LineageChild")
