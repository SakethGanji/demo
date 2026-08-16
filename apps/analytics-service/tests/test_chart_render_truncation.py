"""A chart render that read only the first page must say so.

The view-backed branch of `POST /charts/{id}/render` asks `run_view` for
`limit=1000` — which is also the maximum `RunViewRequest.limit` allows — and
then keeps only `result.result.items`, discarding `total` and `next_cursor`. The
only truncation signal in the response came from `build_series`, which sets
`truncated` when the distinct category count exceeds `MAX_CATEGORIES`. That
constant is *also* 1000, and categories are drawn from the rows, so
`len(categories) <= len(rows) <= 1000` — the flag was structurally unreachable
on this path.

The result: a chart over a 5,000-row view rendered the first 1,000 rows and
reported `truncated: false`, i.e. asserted completeness for data it had silently
clipped. A bar chart of "revenue by region" built that way is wrong, not
partial, and nothing on screen says so.
"""

from __future__ import annotations

import json

from conftest import auth, upload_inline

WIDE_ROWS = [{"id": i, "bucket": f"b{i}", "amount": float(i)} for i in range(1, 1206)]
SMALL_ROWS = [{"id": i, "bucket": f"b{i}", "amount": float(i)} for i in range(1, 5)]


async def _chart_over_view(client, admin_id, rows):
    h = auth(admin_id)
    ds = (await upload_inline(client, admin_id, json.dumps(rows)))["dataset_id"]
    view = (await client.post(f"/api/v1/datasets/{ds}/views", headers=h, json={
        "name": "all", "sheet": "data", "query": {}})).json()
    chart = (await client.post(f"/api/v1/datasets/{ds}/charts", headers=h, json={
        "name": "buckets", "chart_type": "bar", "view_id": view["id"],
        "config": {"x_field": "bucket", "y_fields": ["amount"]}})).json()
    return ds, chart["id"]


async def test_a_view_backed_chart_reports_truncation_when_it_reads_one_page(
        client, admin_id):
    ds, chart_id = await _chart_over_view(client, admin_id, WIDE_ROWS)

    r = await client.post(f"/api/v1/datasets/{ds}/charts/{chart_id}/render",
                          headers=auth(admin_id))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["row_count"] == 1000            # one page, the schema's maximum
    assert body["total_rows"] == len(WIDE_ROWS)  # what the view actually matches
    assert body["total_rows"] > body["row_count"]
    assert body["truncated"] is True


async def test_a_chart_that_fits_in_one_page_is_not_marked_truncated(
        client, admin_id):
    """`truncated` has to stay meaningful, or a UI banner fires on every chart."""
    ds, chart_id = await _chart_over_view(client, admin_id, SMALL_ROWS)

    r = await client.post(f"/api/v1/datasets/{ds}/charts/{chart_id}/render",
                          headers=auth(admin_id))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["row_count"] == len(SMALL_ROWS)
    assert body["total_rows"] == len(SMALL_ROWS)
    assert body["truncated"] is False


async def test_a_definition_backed_chart_reports_its_row_total_too(client, admin_id):
    """Both arms of the same endpoint must describe their result the same way."""
    h = auth(admin_id)
    ds = (await upload_inline(client, admin_id, json.dumps(SMALL_ROWS)))["dataset_id"]
    definition = (await client.post(f"/api/v1/datasets/{ds}/analytics", headers=h, json={
        "name": "by-bucket", "kind": "aggregate",
        "params": {"group_by": ["bucket"],
                   "aggregations": [{"column": "amount", "function": "sum"}]}})).json()
    chart = (await client.post(f"/api/v1/datasets/{ds}/charts", headers=h, json={
        "name": "agg", "chart_type": "bar", "definition_id": definition["id"]})).json()

    r = await client.post(f"/api/v1/datasets/{ds}/charts/{chart['id']}/render", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["total_rows"] == r.json()["row_count"] == len(SMALL_ROWS)
    assert r.json()["truncated"] is False
