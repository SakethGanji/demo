"""Rendering a chart is a raw read path, so the dictionary's masking binds here.

`POST /charts/{id}/render` returns the source's actual values: the x-field
becomes the `categories` list verbatim and each y-field becomes a series of raw
cell values. The view-backed branch called `run_view` without a principal, and
`run_view` only masks when it is given one — so a chart over a saved view
returned exactly the values `POST /views/{id}/run` withholds from the same
caller. The definition-backed branch never masked at all: a `sample` definition
returns dataset rows verbatim, and an `aggregate` group key carries the raw
values of the column it grouped on.

`app/shared/masking.py` states the rule this pins: "**This is a real control,
not a display convenience.**" A control with an unguarded second door is not a
control — and `render` only needs DATASET_READ, the weakest permission there is.
"""

from __future__ import annotations

import json

from conftest import auth, create_team_user, upload_inline

DEFAULT_TEAM = "00000000-0000-0000-0000-000000000001"

ROWS = [
    {"id": 1, "email": "ana@example.com", "amount": 100.0},
    {"id": 2, "email": "bob@example.com", "amount": 250.0},
]


async def _dataset_with_pii(client, admin_id):
    ds = (await upload_inline(client, admin_id, json.dumps(ROWS)))["dataset_id"]
    r = await client.put(
        f"/api/v1/datasets/{ds}/sheet-metadata/data/columns/email",
        headers=auth(admin_id),
        json={"business_name": "Contact email", "semantic_type": "email",
              "sensitivity": "confidential"})
    assert r.status_code == 200, r.text
    return ds


async def _analyst(client, admin_id):
    """A member of the dataset's team WITHOUT dataset:read_sensitive."""
    uid, _ = await create_team_user(client, admin_id, "editor", team_id=DEFAULT_TEAM)
    return uid


async def _chart_over_view(client, admin_id, ds):
    h = auth(admin_id)
    view = (await client.post(f"/api/v1/datasets/{ds}/views", headers=h, json={
        "name": "all", "sheet": "data", "query": {}})).json()
    return (await client.post(f"/api/v1/datasets/{ds}/charts", headers=h, json={
        "name": "emails", "chart_type": "bar", "view_id": view["id"],
        "config": {"x_field": "email", "y_fields": ["amount"]}})).json()


async def test_a_view_backed_chart_masks_the_columns_the_view_run_masks(
        client, admin_id):
    ds = await _dataset_with_pii(client, admin_id)
    chart = await _chart_over_view(client, admin_id, ds)
    uid = await _analyst(client, admin_id)

    r = await client.post(f"/api/v1/datasets/{ds}/charts/{chart['id']}/render",
                          headers=auth(uid))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["masked_columns"] == ["email"]
    assert set(body["categories"]) == {"a***@***.com", "b***@***.com"}
    assert "ana@example.com" not in r.text
    # Non-sensitive series are untouched — masking is column-scoped, not a blackout.
    assert sorted(body["series"][0]["data"]) == [100.0, 250.0]


async def test_an_elevated_caller_still_sees_the_real_categories(client, admin_id):
    """The control must not become a blanket denial: masking is about who asks."""
    ds = await _dataset_with_pii(client, admin_id)
    chart = await _chart_over_view(client, admin_id, ds)

    r = await client.post(f"/api/v1/datasets/{ds}/charts/{chart['id']}/render",
                          headers=auth(admin_id))
    assert r.status_code == 200, r.text
    assert r.json()["masked_columns"] == []
    assert set(r.json()["categories"]) == {"ana@example.com", "bob@example.com"}


async def test_a_definition_backed_chart_masks_its_group_key(client, admin_id):
    """An aggregate's group key keeps its source column name and its raw values,
    so grouping by a declared-PII column publishes that column as the axis."""
    ds = await _dataset_with_pii(client, admin_id)
    h = auth(admin_id)
    definition = (await client.post(f"/api/v1/datasets/{ds}/analytics", headers=h, json={
        "name": "by-email", "kind": "aggregate",
        "params": {"group_by": ["email"],
                   "aggregations": [{"column": "amount", "function": "sum"}]}})).json()
    chart = (await client.post(f"/api/v1/datasets/{ds}/charts", headers=h, json={
        "name": "spend-by-person", "chart_type": "bar",
        "definition_id": definition["id"],
        "config": {"x_field": "email", "y_fields": ["amount_sum"]}})).json()
    uid = await _analyst(client, admin_id)

    r = await client.post(f"/api/v1/datasets/{ds}/charts/{chart['id']}/render",
                          headers=auth(uid))
    assert r.status_code == 200, r.text
    assert r.json()["masked_columns"] == ["email"]
    assert set(r.json()["categories"]) == {"a***@***.com", "b***@***.com"}
    assert "ana@example.com" not in r.text


async def test_a_sample_definition_chart_masks_raw_rows(client, admin_id):
    """A `sample` definition returns dataset rows verbatim — the most direct leak."""
    ds = await _dataset_with_pii(client, admin_id)
    h = auth(admin_id)
    definition = (await client.post(f"/api/v1/datasets/{ds}/analytics", headers=h, json={
        "name": "rows", "kind": "sample",
        "params": {"target_total_volume": 2,
                   "sampling_steps": [{"method": "random", "sample_size": 2}],
                   "seed": 42}})).json()
    chart = (await client.post(f"/api/v1/datasets/{ds}/charts", headers=h, json={
        "name": "raw-rows", "chart_type": "table", "definition_id": definition["id"],
        "config": {"x_field": "email", "y_fields": ["amount"]}})).json()
    uid = await _analyst(client, admin_id)

    r = await client.post(f"/api/v1/datasets/{ds}/charts/{chart['id']}/render",
                          headers=auth(uid))
    assert r.status_code == 200, r.text
    assert r.json()["masked_columns"] == ["email"]
    assert "ana@example.com" not in r.text and "bob@example.com" not in r.text


async def test_a_pivot_on_a_sensitive_dimension_is_refused_not_half_masked(
        client, admin_id):
    """A pivot widens the distinct values of its column dimension into output
    COLUMN NAMES. Masking cell values cannot reach a header, so masking alone
    would publish the exact values it is meant to withhold."""
    ds = await _dataset_with_pii(client, admin_id)
    h = auth(admin_id)
    definition = (await client.post(f"/api/v1/datasets/{ds}/analytics", headers=h, json={
        "name": "pivot-on-email", "kind": "pivot",
        "params": {"rows": ["id"], "columns": "email",
                   "values": [{"column": "amount", "function": "sum"}]}})).json()
    chart = (await client.post(f"/api/v1/datasets/{ds}/charts", headers=h, json={
        "name": "wide", "chart_type": "table",
        "definition_id": definition["id"]})).json()
    uid = await _analyst(client, admin_id)

    r = await client.post(f"/api/v1/datasets/{ds}/charts/{chart['id']}/render",
                          headers=auth(uid))
    assert r.status_code == 403, r.text
    assert r.json()["code"] == "sensitive-data-restricted"
    assert "ana@example.com" not in r.text

    # The elevated caller still gets the pivot.
    ok = await client.post(f"/api/v1/datasets/{ds}/charts/{chart['id']}/render",
                           headers=h)
    assert ok.status_code == 200, ok.text


async def test_a_dataset_with_no_declared_sensitivity_is_unaffected(client, admin_id):
    ds = (await upload_inline(client, admin_id, json.dumps(ROWS)))["dataset_id"]
    chart = await _chart_over_view(client, admin_id, ds)
    uid = await _analyst(client, admin_id)

    r = await client.post(f"/api/v1/datasets/{ds}/charts/{chart['id']}/render",
                          headers=auth(uid))
    assert r.status_code == 200, r.text
    assert r.json()["masked_columns"] == []
    assert set(r.json()["categories"]) == {"ana@example.com", "bob@example.com"}
