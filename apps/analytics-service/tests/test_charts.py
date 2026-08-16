"""Wave 2 §14 — chart definitions: thin references, no query logic."""

from __future__ import annotations

import json

from conftest import auth, create_team_user, upload_inline

ROWS = [{"region": "EU", "amount": 100.0}, {"region": "US", "amount": 50.0}]


async def _dataset_with_sources(client, admin_id):
    """A dataset with one pivot definition and one saved view."""
    h = auth(admin_id)
    ds = (await upload_inline(client, admin_id, json.dumps(ROWS)))["dataset_id"]
    definition = (await client.post(f"/api/v1/datasets/{ds}/analytics", headers=h, json={
        "name": "by-region", "kind": "pivot",
        "params": {"rows": ["region"],
                   "values": [{"column": "amount", "function": "sum"}]}})).json()
    view = (await client.post(f"/api/v1/datasets/{ds}/views", headers=h, json={
        "name": "all-rows", "sheet": "data", "query": {}})).json()
    return ds, definition["id"], view["id"]


async def test_chart_crud_over_definition_and_view(client, admin_id):
    ds, def_id, view_id = await _dataset_with_sources(client, admin_id)
    h = auth(admin_id)
    base = f"/api/v1/datasets/{ds}/charts"

    r = await client.post(base, headers=h, json={
        "name": "regions-bar", "chart_type": "bar", "definition_id": def_id,
        "config": {"x": "region", "y": "amount_sum"}})
    assert r.status_code == 201, r.text
    chart = r.json()
    assert chart["definition_id"] == def_id and chart["view_id"] is None
    assert chart["config"] == {"x": "region", "y": "amount_sum"}

    r = await client.post(base, headers=h, json={
        "name": "rows-table", "chart_type": "table", "view_id": view_id})
    assert r.status_code == 201, r.text

    r = await client.get(base, headers=h)
    assert r.json()["total"] == 2

    # Duplicate name → 409.
    r = await client.post(base, headers=h, json={
        "name": "regions-bar", "chart_type": "pie", "definition_id": def_id})
    assert r.status_code == 409

    # Retargeting to the view clears the definition reference.
    r = await client.patch(f"{base}/{chart['id']}", headers=h,
                           json={"view_id": view_id, "chart_type": "line"})
    assert r.status_code == 200, r.text
    assert r.json()["view_id"] == view_id and r.json()["definition_id"] is None

    r = await client.delete(f"{base}/{chart['id']}", headers=h)
    assert r.status_code == 204
    assert (await client.get(f"{base}/{chart['id']}", headers=h)).status_code == 404


async def test_chart_source_validation(client, admin_id):
    ds, def_id, view_id = await _dataset_with_sources(client, admin_id)
    h = auth(admin_id)
    base = f"/api/v1/datasets/{ds}/charts"

    # Exactly one source, schema-enforced.
    r = await client.post(base, headers=h, json={
        "name": "x", "chart_type": "bar"})
    assert r.status_code == 422
    r = await client.post(base, headers=h, json={
        "name": "x", "chart_type": "bar",
        "definition_id": def_id, "view_id": view_id})
    assert r.status_code == 422

    # Sources must exist on THIS dataset.
    other = (await upload_inline(client, admin_id, json.dumps(ROWS)))["dataset_id"]
    r = await client.post(f"/api/v1/datasets/{other}/charts", headers=h, json={
        "name": "x", "chart_type": "bar", "definition_id": def_id})
    assert r.status_code == 404

    # Bad chart type is schema-rejected.
    r = await client.post(base, headers=h, json={
        "name": "x", "chart_type": "hologram", "definition_id": def_id})
    assert r.status_code == 422


async def test_chart_cascade_and_authorization(client, admin_id):
    ds, def_id, _view_id = await _dataset_with_sources(client, admin_id)
    h = auth(admin_id)
    chart = (await client.post(f"/api/v1/datasets/{ds}/charts", headers=h, json={
        "name": "doomed", "chart_type": "kpi", "definition_id": def_id})).json()

    # Deleting the source definition cascades to the chart.
    r = await client.delete(f"/api/v1/datasets/{ds}/analytics/{def_id}", headers=h)
    assert r.status_code == 204
    r = await client.get(f"/api/v1/datasets/{ds}/charts/{chart['id']}", headers=h)
    assert r.status_code == 404

    outsider, _ = await create_team_user(client, admin_id, "viewer")
    r = await client.get(f"/api/v1/datasets/{ds}/charts", headers=auth(outsider))
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Rendering — a chart that can't produce data isn't a chart
# ---------------------------------------------------------------------------

CHART_ROWS = [
    {"region": "NY", "amount": 100.0}, {"region": "NY", "amount": 50.0},
    {"region": "LA", "amount": 250.0}, {"region": "SF", "amount": 75.0},
]


async def _dataset_with_aggregate(client, admin_id, name="by-region"):
    """A dataset plus a saved aggregation definition to chart."""
    import json as _json

    from conftest import upload_inline

    ds = (await upload_inline(client, admin_id, _json.dumps(CHART_ROWS)))["dataset_id"]
    r = await client.post(f"/api/v1/datasets/{ds}/analytics", headers=auth(admin_id),
                          json={"name": name, "kind": "aggregate", "sheet": "data",
                                "params": {"group_by": ["region"],
                                           "aggregations": [
                                               {"column": "amount", "function": "sum",
                                                "alias": "revenue"}]}})
    assert r.status_code == 201, r.text
    return ds, r.json()["id"]


async def test_rendering_a_chart_runs_its_definition(client, admin_id):
    ds, definition_id = await _dataset_with_aggregate(client, admin_id)
    h = auth(admin_id)
    chart = (await client.post(f"/api/v1/datasets/{ds}/charts", headers=h, json={
        "name": "revenue by region", "chart_type": "bar",
        "definition_id": definition_id})).json()

    r = await client.post(f"/api/v1/datasets/{ds}/charts/{chart['id']}/render",
                          headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["chart_type"] == "bar"
    assert sorted(body["categories"]) == ["LA", "NY", "SF"]
    assert body["x_field"] == "region"
    assert [s["name"] for s in body["series"]] == ["revenue"]
    assert body["source"] == {"type": "definition", "id": definition_id,
                              "name": "by-region", "kind": "aggregate"}

    by_region = dict(zip(body["categories"], body["series"][0]["data"]))
    assert by_region["NY"] == 150.0 and by_region["LA"] == 250.0


async def test_an_empty_config_still_renders(client, admin_id):
    """Fields are inferred, so a chart saved without encoding is still useful."""
    ds, definition_id = await _dataset_with_aggregate(client, admin_id)
    h = auth(admin_id)
    chart = (await client.post(f"/api/v1/datasets/{ds}/charts", headers=h, json={
        "name": "bare", "chart_type": "bar", "definition_id": definition_id,
        "config": {}})).json()

    body = (await client.post(f"/api/v1/datasets/{ds}/charts/{chart['id']}/render",
                              headers=h)).json()
    assert body["x_field"] and body["series"]


async def test_config_selects_the_fields(client, admin_id):
    ds, definition_id = await _dataset_with_aggregate(client, admin_id)
    h = auth(admin_id)
    chart = (await client.post(f"/api/v1/datasets/{ds}/charts", headers=h, json={
        "name": "explicit", "chart_type": "line", "definition_id": definition_id,
        "config": {"x_field": "region", "y_fields": ["revenue"]}})).json()

    body = (await client.post(f"/api/v1/datasets/{ds}/charts/{chart['id']}/render",
                              headers=h)).json()
    assert body["x_field"] == "region" and body["y_fields"] == ["revenue"]


async def test_rendering_a_view_backed_chart(client, admin_id):
    import json as _json

    from conftest import upload_inline

    ds = (await upload_inline(client, admin_id,
                              _json.dumps(CHART_ROWS)))["dataset_id"]
    h = auth(admin_id)
    view = (await client.post(f"/api/v1/datasets/{ds}/views", headers=h, json={
        "name": "all rows", "sheet": "data",
        "query": {"sort": [{"column": "amount", "direction": "desc"}]}})).json()
    chart = (await client.post(f"/api/v1/datasets/{ds}/charts", headers=h, json={
        "name": "from view", "chart_type": "bar", "view_id": view["id"],
        "config": {"x_field": "region", "y_fields": ["amount"]}})).json()

    r = await client.post(f"/api/v1/datasets/{ds}/charts/{chart['id']}/render",
                          headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["source"]["type"] == "view"
    assert r.json()["row_count"] == 4


async def test_rendering_persists_nothing(client, admin_id):
    """Rendering is a read — no run row, unlike executing the definition."""
    ds, definition_id = await _dataset_with_aggregate(client, admin_id)
    h = auth(admin_id)
    chart = (await client.post(f"/api/v1/datasets/{ds}/charts", headers=h, json={
        "name": "c", "chart_type": "bar", "definition_id": definition_id})).json()

    await client.post(f"/api/v1/datasets/{ds}/charts/{chart['id']}/render", headers=h)

    runs = await client.get(
        f"/api/v1/datasets/{ds}/analytics/{definition_id}/runs", headers=h)
    assert runs.json()["total"] == 0


async def test_rendering_a_chart_whose_source_is_gone_is_a_409(client, admin_id):
    ds, definition_id = await _dataset_with_aggregate(client, admin_id)
    h = auth(admin_id)
    chart = (await client.post(f"/api/v1/datasets/{ds}/charts", headers=h, json={
        "name": "orphan", "chart_type": "bar", "definition_id": definition_id})).json()
    await client.delete(f"/api/v1/datasets/{ds}/analytics/{definition_id}", headers=h)

    r = await client.post(f"/api/v1/datasets/{ds}/charts/{chart['id']}/render",
                          headers=h)
    assert r.status_code in (404, 409), r.text


async def test_rendering_is_hidden_across_teams(client, admin_id):
    from conftest import create_team_user

    ds, definition_id = await _dataset_with_aggregate(client, admin_id)
    h = auth(admin_id)
    chart = (await client.post(f"/api/v1/datasets/{ds}/charts", headers=h, json={
        "name": "c", "chart_type": "bar", "definition_id": definition_id})).json()

    outsider, _ = await create_team_user(client, admin_id, "admin")
    r = await client.post(f"/api/v1/datasets/{ds}/charts/{chart['id']}/render",
                          headers=auth(outsider))
    assert r.status_code == 404
