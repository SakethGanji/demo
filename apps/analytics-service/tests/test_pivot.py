"""Wave 2 §11 — pivot builder: POST /pivot + the 'pivot' analytics kind.

Semantics under test: widening (distinct pivot values → columns), percentage
displays as windowed shares of the aggregated result, totals RE-AGGREGATED at
the coarser grain (so non-additive functions like mean stay correct), and the
saved-definition → run → publish → pivoted_from lineage path.
"""

from __future__ import annotations

import json

import pytest

from conftest import auth, create_team_user, upload_inline

SALES = [
    {"region": "EU", "quarter": "Q1", "amount": 100.0, "units": 1},
    {"region": "EU", "quarter": "Q2", "amount": 200.0, "units": 2},
    {"region": "US", "quarter": "Q1", "amount": 50.0, "units": 5},
    {"region": "US", "quarter": "Q2", "amount": 150.0, "units": 3},
    {"region": "APAC", "quarter": "Q1", "amount": 25.0, "units": 4},
]

BASIC = {
    "rows": ["region"],
    "columns": "quarter",
    "values": [{"column": "amount", "function": "sum", "alias": "amt"}],
}


async def _sales_dataset(client, admin_id):
    return (await upload_inline(client, admin_id, json.dumps(SALES)))["dataset_id"]


async def test_basic_pivot_widens_and_totals(client, admin_id):
    ds = await _sales_dataset(client, admin_id)
    r = await client.post("/api/v1/pivot", headers=auth(admin_id),
                          json={"dataset_id": ds, **BASIC})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["pivot_columns"] == ["Q1", "Q2"]
    assert body["columns"] == ["region", "Q1", "Q2"]  # single value → bare headers
    assert body["data"] == [
        {"region": "APAC", "Q1": 25.0, "Q2": None},
        {"region": "EU", "Q1": 100.0, "Q2": 200.0},
        {"region": "US", "Q1": 50.0, "Q2": 150.0},
    ]
    assert body["totals"] == {"amt": 525.0}
    assert body["truncated"] is False

    # The persisted parquet is a real, owned artifact.
    r = await client.get(f"/api/v1/samples/{body['result_file']}/data",
                        headers=auth(admin_id))
    assert r.status_code == 200


async def test_multi_value_headers_and_no_columns_passthrough(client, admin_id):
    ds = await _sales_dataset(client, admin_id)
    h = auth(admin_id)

    r = await client.post("/api/v1/pivot", headers=h, json={
        "dataset_id": ds, "rows": ["region"], "columns": "quarter",
        "values": [{"column": "amount", "function": "sum", "alias": "amt"},
                   {"column": "units", "function": "sum", "alias": "u"}]})
    assert r.status_code == 200, r.text
    assert r.json()["columns"] == [
        "region", "Q1_amt", "Q1_u", "Q2_amt", "Q2_u"]

    # No pivot dim → plain grouped table (long format passthrough).
    r = await client.post("/api/v1/pivot", headers=h, json={
        "dataset_id": ds, "rows": ["region"],
        "values": [{"column": "amount", "function": "sum", "alias": "amt"}]})
    assert r.status_code == 200, r.text
    assert r.json()["columns"] == ["region", "amt"]
    assert r.json()["data"][1] == {"region": "EU", "amt": 300.0}


async def test_totals_reaggregate_non_additive_mean(client, admin_id):
    ds = await _sales_dataset(client, admin_id)
    r = await client.post("/api/v1/pivot", headers=auth(admin_id), json={
        "dataset_id": ds, "rows": ["region"], "columns": "quarter",
        "values": [{"column": "amount", "function": "mean", "alias": "avg_amt"}],
        "include_row_totals": True, "include_column_totals": True})
    assert r.status_code == 200, r.text
    body = r.json()
    eu = next(row for row in body["data"] if row["region"] == "EU")
    # Row total = mean over EU's raw rows (150), NOT mean-of-means of cells.
    assert eu["total_avg_amt"] == 150.0
    # Column totals = mean over each quarter's raw rows.
    assert body["column_totals"]["Q1"] == pytest.approx(58.3333, abs=1e-3)
    assert body["column_totals"]["Q2"] == 175.0
    assert body["totals"]["avg_amt"] == 105.0  # grand mean of all 5 rows


async def test_percentage_displays(client, admin_id):
    ds = await _sales_dataset(client, admin_id)
    h = auth(admin_id)

    r = await client.post("/api/v1/pivot", headers=h, json={
        "dataset_id": ds, "rows": ["region"], "columns": "quarter",
        "values": [{"column": "amount", "function": "sum", "alias": "amt",
                    "display": "pct_of_row"}]})
    assert r.status_code == 200, r.text
    eu = next(row for row in r.json()["data"] if row["region"] == "EU")
    assert eu["Q1"] == pytest.approx(33.3333, abs=1e-3)
    assert eu["Q2"] == pytest.approx(66.6667, abs=1e-3)

    r = await client.post("/api/v1/pivot", headers=h, json={
        "dataset_id": ds, "rows": ["region"], "columns": "quarter",
        "values": [{"column": "amount", "function": "sum", "alias": "amt",
                    "display": "pct_of_grand_total"}]})
    cells = [v for row in r.json()["data"]
             for k, v in row.items() if k != "region" and v is not None]
    assert sum(cells) == pytest.approx(100.0, abs=1e-6)
    # Percentage specs are excluded from re-aggregated grand totals.
    assert r.json()["totals"] is None


async def test_filters_and_bucketed_rows(client, admin_id):
    ds = await _sales_dataset(client, admin_id)
    h = auth(admin_id)

    r = await client.post("/api/v1/pivot", headers=h, json={
        "dataset_id": ds, **BASIC,
        "filters": {"conditions": [
            {"column": "amount", "op": "gt", "value": 30}]}})
    assert r.status_code == 200, r.text
    assert [row["region"] for row in r.json()["data"]] == ["EU", "US"]  # APAC filtered

    r = await client.post("/api/v1/pivot", headers=h, json={
        "dataset_id": ds,
        "rows": [{"column": "amount", "bin_width": 100, "alias": "band"}],
        "columns": "quarter",
        "values": [{"column": "units", "function": "sum", "alias": "u"}]})
    assert r.status_code == 200, r.text
    assert [row["band"] for row in r.json()["data"]] == [0.0, 100.0, 200.0]


async def test_pivot_validation_errors(client, admin_id):
    ds = await _sales_dataset(client, admin_id)
    h = auth(admin_id)
    base = {"dataset_id": ds}

    r = await client.post("/api/v1/pivot", headers=h, json={
        **base, "rows": ["region"],
        "values": [{"column": "nope", "function": "sum"}]})
    assert r.status_code == 400 and "nope" in r.json()["detail"]

    r = await client.post("/api/v1/pivot", headers=h, json={
        **base, "rows": ["region"],
        "values": [{"column": "amount", "function": "explode"}]})
    assert r.status_code == 400 and "explode" in r.json()["detail"]

    r = await client.post("/api/v1/pivot", headers=h, json={
        **base, "rows": ["quarter"], "columns": "quarter",
        "values": [{"column": "amount", "function": "sum"}]})
    assert r.status_code == 400 and "also a row dimension" in r.json()["detail"]

    r = await client.post("/api/v1/pivot", headers=h, json={
        **base, "rows": ["region"],
        "values": [{"column": "amount", "function": "sum",
                    "display": "pct_of_row"}]})
    assert r.status_code == 400 and "requires a pivot" in r.json()["detail"]

    r = await client.post("/api/v1/pivot", headers=h, json={
        **base, "rows": ["region"], "columns": "quarter",
        "values": [{"column": "amount", "function": "sum"}],
        "sort_by": "amount_sum"})
    assert r.status_code == 400 and "row dimension" in r.json()["detail"]


async def test_too_many_pivot_columns_guard(client, admin_id, monkeypatch):
    from app.features.data_accelerator.services import pivot as pivot_service

    monkeypatch.setattr(pivot_service, "MAX_PIVOT_COLUMNS", 1)
    ds = await _sales_dataset(client, admin_id)
    r = await client.post("/api/v1/pivot", headers=auth(admin_id),
                          json={"dataset_id": ds, **BASIC})
    assert r.status_code == 400, r.text
    assert r.json()["code"] == "too-many-pivot-columns"


async def test_pivot_definition_run_publish_lineage(client, admin_id):
    ds = await _sales_dataset(client, admin_id)
    h = auth(admin_id)

    r = await client.post(f"/api/v1/datasets/{ds}/analytics", headers=h, json={
        "name": "quarterly-by-region", "kind": "pivot",
        "params": {k: v for k, v in BASIC.items()}})
    assert r.status_code == 201, r.text
    def_id = r.json()["id"]

    r = await client.post(f"/api/v1/datasets/{ds}/analytics/{def_id}/run", headers=h)
    assert r.status_code == 200, r.text
    run = r.json()
    assert run["status"] == "completed"
    assert run["result_summary"]["result_file"].startswith("pivot_")
    assert run["artifact_id"]
    assert run["result"]["pivot_columns"] == ["Q1", "Q2"]

    r = await client.post(f"/api/v1/datasets/{ds}/analytics/runs/{run['id']}/publish",
                          headers=h, json={"mode": "new_dataset",
                                           "name": "quarterly-pivot"})
    assert r.status_code == 200, r.text
    child = r.json()["dataset_id"]

    lin = (await client.get(f"/api/v1/datasets/{child}/lineage", headers=h)).json()
    assert lin["parents"][0]["relation"] == "pivoted_from"

    # The published pivot is a real dataset: widened columns are queryable.
    r = await client.post(f"/api/v1/datasets/{child}/versions/1/query",
                          headers=h, json={"sort": [{"column": "region"}]})
    assert r.status_code == 200, r.text
    assert {"region", "Q1", "Q2"} <= set(r.json()["items"][0])


async def test_pivot_cross_team_404(client, admin_id):
    ds = await _sales_dataset(client, admin_id)
    outsider, _ = await create_team_user(client, admin_id, "viewer")
    r = await client.post("/api/v1/pivot", headers=auth(outsider),
                          json={"dataset_id": ds, **BASIC})
    assert r.status_code == 404
