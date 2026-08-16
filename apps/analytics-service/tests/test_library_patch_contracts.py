"""PATCH on a definition or a chart answers with a contract, not a 500.

Two gaps, both on the update path only — the create path handles both cases
correctly, which is what makes these oversights rather than choices:

* `(dataset_id, name)` is UNIQUE on both `analytics_definitions` and
  `chart_definitions`. POST absorbs the collision with `ON CONFLICT DO NOTHING`
  and answers 409. PATCH issued a bare UPDATE, so renaming onto a sibling's name
  raised IntegrityError into the catch-all handler: an opaque 500 that a rename
  dialog cannot turn into "that name is taken". The explorer's view PATCH
  already does this correctly, so this is the repo's own house style.

* `chart_definitions` has `CHECK ((definition_id IS NULL) <> (view_id IS NULL))`
  — exactly one source. `ChartCreate` enforces it with a model validator;
  `ChartUpdate` has none, and the repo writes on key presence, so
  `{"definition_id": null}` emitted `definition_id = NULL`, tripped the CHECK
  and returned a 500 for what is a plain caller mistake.
"""

from __future__ import annotations

import json

from conftest import auth, upload_inline

ROWS = [{"region": "EU", "amount": 100.0}, {"region": "US", "amount": 50.0}]
AGG = {"group_by": ["region"],
       "aggregations": [{"column": "amount", "function": "sum"}]}


async def _fixture(client, admin_id):
    """A dataset with two definitions, a view, and two charts."""
    h = auth(admin_id)
    ds = (await upload_inline(client, admin_id, json.dumps(ROWS)))["dataset_id"]
    defs = []
    for name in ("first", "second"):
        r = await client.post(f"/api/v1/datasets/{ds}/analytics", headers=h,
                              json={"name": name, "kind": "aggregate", "params": AGG})
        assert r.status_code == 201, r.text
        defs.append(r.json()["id"])
    view = (await client.post(f"/api/v1/datasets/{ds}/views", headers=h, json={
        "name": "all", "sheet": "data", "query": {}})).json()
    charts = []
    for name in ("chart-a", "chart-b"):
        r = await client.post(f"/api/v1/datasets/{ds}/charts", headers=h, json={
            "name": name, "chart_type": "bar", "definition_id": defs[0]})
        assert r.status_code == 201, r.text
        charts.append(r.json()["id"])
    return ds, defs, view["id"], charts


async def test_renaming_a_definition_onto_a_sibling_name_is_409_not_500(
        client, admin_id):
    ds, defs, _, _ = await _fixture(client, admin_id)

    r = await client.patch(f"/api/v1/datasets/{ds}/analytics/{defs[1]}",
                           headers=auth(admin_id), json={"name": "first"})
    assert r.status_code == 409, r.text
    assert r.json()["code"] == "definition-name-taken"
    # The rename did not half-apply.
    still = await client.get(f"/api/v1/datasets/{ds}/analytics/{defs[1]}",
                             headers=auth(admin_id))
    assert still.json()["name"] == "second"


async def test_renaming_a_chart_onto_a_sibling_name_is_409_not_500(client, admin_id):
    ds, _, _, charts = await _fixture(client, admin_id)

    r = await client.patch(f"/api/v1/datasets/{ds}/charts/{charts[1]}",
                           headers=auth(admin_id), json={"name": "chart-a"})
    assert r.status_code == 409, r.text
    assert r.json()["code"] == "chart-name-taken"


async def test_renaming_to_a_free_name_still_works(client, admin_id):
    ds, defs, _, charts = await _fixture(client, admin_id)
    h = auth(admin_id)

    r = await client.patch(f"/api/v1/datasets/{ds}/analytics/{defs[1]}",
                           headers=h, json={"name": "third"})
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "third"

    r = await client.patch(f"/api/v1/datasets/{ds}/charts/{charts[1]}",
                           headers=h, json={"name": "chart-c"})
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "chart-c"


async def test_a_chart_cannot_be_left_with_no_source(client, admin_id):
    ds, _, _, charts = await _fixture(client, admin_id)
    h = auth(admin_id)

    r = await client.patch(f"/api/v1/datasets/{ds}/charts/{charts[0]}", headers=h,
                           json={"definition_id": None, "view_id": None})
    assert r.status_code == 400, r.text
    assert r.json()["code"] == "chart-source-required"


async def test_nulling_the_only_populated_source_is_refused(client, admin_id):
    """One key is enough: nulling `definition_id` on a definition-backed chart
    empties the only populated column and trips the same CHECK."""
    ds, _, _, charts = await _fixture(client, admin_id)
    h = auth(admin_id)

    r = await client.patch(f"/api/v1/datasets/{ds}/charts/{charts[0]}", headers=h,
                           json={"definition_id": None})
    assert r.status_code == 400, r.text
    assert r.json()["code"] == "chart-source-required"

    unchanged = await client.get(f"/api/v1/datasets/{ds}/charts/{charts[0]}", headers=h)
    assert unchanged.json()["definition_id"] is not None


async def test_retargeting_a_chart_to_the_other_source_still_works(client, admin_id):
    """The guard must not block the legitimate swap, which clears the old side."""
    ds, _, view_id, charts = await _fixture(client, admin_id)

    r = await client.patch(f"/api/v1/datasets/{ds}/charts/{charts[0]}",
                           headers=auth(admin_id), json={"view_id": view_id})
    assert r.status_code == 200, r.text
    assert r.json()["view_id"] == view_id and r.json()["definition_id"] is None


async def test_editing_only_the_encoding_leaves_the_source_alone(client, admin_id):
    """A PATCH that omits both source keys is untouched by the guard."""
    ds, _, _, charts = await _fixture(client, admin_id)

    r = await client.patch(f"/api/v1/datasets/{ds}/charts/{charts[0]}",
                           headers=auth(admin_id),
                           json={"config": {"x_field": "region"}})
    assert r.status_code == 200, r.text
    assert r.json()["definition_id"] is not None
    assert r.json()["config"] == {"x_field": "region"}
