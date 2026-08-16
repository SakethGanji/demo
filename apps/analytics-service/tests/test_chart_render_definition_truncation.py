"""A definition-backed chart render must not throw away the source's own cut.

`tests/test_chart_render_truncation.py` fixed the *view* arm of
`POST /charts/{id}/render`. The *definition* arm had the same hole from the
other direction: `compute_definition` called `run_pivot` / `run_aggregation`,
which return a `truncated` flag when their row cap clipped the output, and then
returned only `(columns, rows, masked_columns)` — a 3-tuple with no room for the
flag. `render_chart` left `source_truncated = False`, so the only truncation
signal left was `build_series`' 1000-category cap.

What breaks in production without this: a saved pivot definition holding
`params.limit: 10` over 60 pivot rows renders the first 10 and answers
`truncated: false, total_rows == row_count`. That is a chart asserting
completeness for a set the server cut — "revenue by region" showing a sixth of
the regions with nothing on screen saying so. Whoever reads that chart draws a
conclusion about data they were never shown.
"""

from __future__ import annotations

import json

from conftest import auth, upload_inline

# 60 distinct buckets: comfortably under build_series' MAX_CATEGORIES (1000),
# so any truncation reported here can only have come from the source.
WIDE_ROWS = [{"id": i, "bucket": f"b{i:03d}", "region": "EU" if i % 2 else "US",
              "amount": float(i)} for i in range(1, 61)]


async def _chart_over_definition(client, admin_id, rows, kind, params):
    h = auth(admin_id)
    ds = (await upload_inline(client, admin_id, json.dumps(rows)))["dataset_id"]
    d = await client.post(f"/api/v1/datasets/{ds}/analytics", headers=h, json={
        "name": f"{kind}-def", "kind": kind, "params": params})
    assert d.status_code == 201, d.text
    definition = d.json()
    c = await client.post(f"/api/v1/datasets/{ds}/charts", headers=h, json={
        "name": "c", "chart_type": "bar", "definition_id": definition["id"]})
    assert c.status_code == 201, c.text
    return ds, c.json()["id"]


async def _render(client, admin_id, ds, chart_id):
    r = await client.post(f"/api/v1/datasets/{ds}/charts/{chart_id}/render",
                          headers=auth(admin_id))
    assert r.status_code == 200, r.text
    return r.json()


async def test_a_definition_backed_chart_reports_truncation_when_the_source_clipped_it(
        client, admin_id):
    """A pivot the server cut must render as incomplete, not as the whole story."""
    ds, chart_id = await _chart_over_definition(
        client, admin_id, WIDE_ROWS, "pivot",
        {"rows": ["bucket"], "limit": 10,
         "values": [{"column": "amount", "function": "sum"}]})

    body = await _render(client, admin_id, ds, chart_id)
    assert body["row_count"] == 10                  # what the pivot handed back
    assert body["truncated"] is True                # ...out of 60 pivot rows
    # The pivot response knows there are *more* rows, not how many, so the
    # honest answer is "unknown" rather than a total equal to row_count — which
    # is exactly the claim of completeness this test exists to prevent.
    assert body["total_rows"] is None
    assert len(body["categories"]) == 10


async def test_a_definition_backed_chart_that_was_not_clipped_stays_untruncated(
        client, admin_id):
    """`truncated` has to stay meaningful, or the banner fires on every chart."""
    ds, chart_id = await _chart_over_definition(
        client, admin_id, WIDE_ROWS, "pivot",
        {"rows": ["region"],
         "values": [{"column": "amount", "function": "sum"}]})

    body = await _render(client, admin_id, ds, chart_id)
    assert body["row_count"] == 2                   # EU and US, nothing cut
    assert body["truncated"] is False
    assert body["total_rows"] == 2


async def test_an_aggregate_definitions_own_top_n_limit_is_not_reported_as_truncation(
        client, admin_id):
    """A definition that *asked* for the top N got what it asked for.

    `aggregation._is_truncated` deliberately reports truncation only when the
    server-side cap did the cutting, not when the caller set a smaller limit —
    a documented choice, and the same reasoning that keeps a `sample`
    definition untruncated. Pinning it here so propagating the flag does not
    quietly turn every top-N chart into a "this is incomplete" banner.
    """
    ds, chart_id = await _chart_over_definition(
        client, admin_id, WIDE_ROWS, "aggregate",
        {"group_by": ["bucket"], "limit": 5, "sort_by": "amount_sum",
         "sort_order": "desc",
         "aggregations": [{"column": "amount", "function": "sum"}]})

    body = await _render(client, admin_id, ds, chart_id)
    assert body["row_count"] == 5
    assert body["truncated"] is False
    assert body["total_rows"] == 5
