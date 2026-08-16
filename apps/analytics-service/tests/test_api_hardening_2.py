"""Second hardening pass — input a UI can realistically send must not 500,
and pivot percentage row-totals must not mix units. From the pre-UI audit."""

from __future__ import annotations

import json

from conftest import auth, upload_inline

ROWS = [{"region": "EU", "amount": 100, "note": "alpha"},
        {"region": "US", "amount": 250, "note": "beta"},
        {"region": "APAC", "amount": 5, "note": "gamma"}]


def _agg_body(**over):
    body = {"data": ROWS, "group_by": ["region"],
            "aggregations": [{"column": "amount", "function": "sum", "alias": "total"}]}
    body.update(over)
    return body


async def test_numeric_filter_op_with_non_numeric_value_is_400_not_500(client, admin_id):
    h = auth(admin_id)
    for op in ["len_gt", "top_n", "top_pct", "last_n_days"]:
        body = _agg_body(filters={"logic": "and", "conditions": [{"column": "note" if op.startswith("len") else "amount", "op": op, "value": "abc"}]})
        r = await client.post("/api/v1/aggregate", headers=h, json=body)
        assert r.status_code == 400, (op, r.status_code, r.text)
        assert r.json()["code"] == "invalid-filter-value", (op, r.json())


async def test_aggregate_negative_limit_does_not_500(client, admin_id):
    r = await client.post("/api/v1/aggregate", headers=auth(admin_id), json=_agg_body(limit=-1))
    assert r.status_code == 200, r.text


async def test_query_with_bad_regex_is_400_not_500(client, admin_id):
    h = auth(admin_id)
    ds = (await upload_inline(client, admin_id, json.dumps(ROWS)))["dataset_id"]
    r = await client.post(f"/api/v1/datasets/{ds}/versions/1/sheets/data/query", headers=h,
                          json={"filters": {"logic": "and", "conditions": [{"column": "region", "op": "regex", "value": "([a-"}]}})
    assert r.status_code == 400, r.text
    assert r.headers.get("content-type", "").startswith("application/problem+json")


async def test_pivot_percentage_display_omits_raw_row_total(client, admin_id):
    h = auth(admin_id)
    data = [{"r": "a", "p": "x", "v": 1.0}, {"r": "a", "p": "y", "v": 3.0}]
    # pct_of_row display + include_row_totals: a raw sum beside percentages would
    # mix units, so the row total is omitted (like grand/column totals).
    pct = await client.post("/api/v1/pivot", headers=h, json={
        "data": data, "rows": ["r"], "columns": "p", "include_row_totals": True,
        "values": [{"column": "v", "function": "sum", "display": "pct_of_row", "alias": "v"}]})
    assert pct.status_code == 200, pct.text
    row = pct.json()["data"][0]
    assert row["x"] == 25.0 and row["y"] == 75.0
    assert "total_v" not in row, row

    # A value-display pivot still gets its row total.
    val = await client.post("/api/v1/pivot", headers=h, json={
        "data": data, "rows": ["r"], "columns": "p", "include_row_totals": True,
        "values": [{"column": "v", "function": "sum", "display": "value", "alias": "v"}]})
    assert val.json()["data"][0]["total_v"] == 4.0


async def test_empty_collection_lists_report_limit_at_least_one(client, admin_id):
    h = auth(admin_id)
    ds = (await upload_inline(client, admin_id, json.dumps(ROWS)))["dataset_id"]
    tags = await client.get(f"/api/v1/datasets/{ds}/tags", headers=h)
    assert tags.status_code == 200
    assert tags.json()["limit"] >= 1


async def test_sample_download_uses_recorded_media_type(client, admin_id):
    h = auth(admin_id)
    ds = (await upload_inline(client, admin_id, json.dumps(ROWS)))["dataset_id"]
    agg = await client.post("/api/v1/aggregate", headers=h, json={
        "dataset_id": ds, "sheet": "data", "group_by": ["region"],
        "aggregations": [{"column": "amount", "function": "sum", "alias": "total"}]})
    result_file = agg.json()["result_file"]
    exp = await client.post(f"/api/v1/samples/{result_file}/export", headers=h, params={"format": "csv"})
    assert exp.status_code == 200, exp.text
    export_file = exp.json()["export_file"]
    dl = await client.get(f"/api/v1/samples/{export_file}", headers=h)
    assert dl.status_code == 200
    assert "text/csv" in dl.headers.get("content-type", ""), dl.headers.get("content-type")
