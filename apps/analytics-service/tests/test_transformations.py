"""Wave 4 §19–§21 — transformation pipelines: CRUD, preview, run, profile, publish.

Covers the feature's own contracts (schema fold, rename-proof sheet pinning,
non-destructive publish, auto-profiling) plus the service-wide ones every new
endpoint family must honour: cross-team 404, in-team 403, sheet-selection-required.
"""

from __future__ import annotations

import json

from conftest import (
    XLSX_MIME,
    auth,
    create_team_user,
    make_workbook,
    upload_file,
    upload_inline,
)

ROWS = [
    {"id": 1, "name": "  Alice  ", "city": "NY", "amount": 10.0},
    {"id": 2, "name": "bob", "city": "ny", "amount": 20.0},
    {"id": 2, "name": "bob", "city": "ny", "amount": 20.0},
    {"id": 3, "name": "Carol", "city": "LA", "amount": 30.0},
    {"id": 4, "name": None, "city": "sf", "amount": 40.0},
]

CLEANUP_STEPS = [
    {"type": "trim", "columns": ["name"]},
    {"type": "case_normalize", "columns": ["city"], "mode": "upper"},
    {"type": "replace", "column": "name", "nulls_to": "unknown"},
    {"type": "deduplicate", "subset": ["id"], "keep": "first"},
]


def body(name="cleanup", steps=None, **extra):
    return {"name": name, "sheet": "data", "steps": steps or CLEANUP_STEPS, **extra}


async def make_dataset(client, admin_id):
    return (await upload_inline(client, admin_id, json.dumps(ROWS)))["dataset_id"]


# --- CRUD ---------------------------------------------------------------------

async def test_transformation_crud_lifecycle(client, admin_id):
    ds = await make_dataset(client, admin_id)
    h = auth(admin_id)
    base = f"/api/v1/datasets/{ds}/transformations"

    r = await client.post(base, headers=h, json=body())
    assert r.status_code == 201, r.text
    definition = r.json()
    assert definition["sheet_key"] == "data" and definition["logical_sheet_id"]
    assert definition["version_selector"] == {"mode": "current"}
    assert len(definition["steps"]) == 4
    did = definition["id"]

    # Name is unique per dataset.
    assert (await client.post(base, headers=h, json=body())).status_code == 409

    r = await client.get(base, headers=h)
    assert r.json()["total"] == 1 and r.json()["items"][0]["id"] == did

    r = await client.get(f"{base}/{did}", headers=h)
    assert r.status_code == 200 and r.json()["name"] == "cleanup"

    r = await client.patch(f"{base}/{did}", headers=h,
                           json={"description": "tidy up",
                                 "steps": [{"type": "limit", "count": 2}]})
    assert r.status_code == 200, r.text
    assert r.json()["description"] == "tidy up" and len(r.json()["steps"]) == 1

    assert (await client.delete(f"{base}/{did}", headers=h)).status_code == 204
    assert (await client.get(f"{base}/{did}", headers=h)).status_code == 404


async def test_pipeline_is_validated_before_it_is_saved(client, admin_id):
    ds = await make_dataset(client, admin_id)
    h = auth(admin_id)
    r = await client.post(f"/api/v1/datasets/{ds}/transformations", headers=h,
                          json=body(steps=[{"type": "select", "columns": ["nope"]}]))
    assert r.status_code == 400, r.text
    problem = r.json()
    assert problem["code"] == "unknown-column"
    assert "amount" in problem["available"]
    # Nothing was persisted.
    listing = await client.get(f"/api/v1/datasets/{ds}/transformations", headers=h)
    assert listing.json()["total"] == 0


async def test_a_step_referencing_a_column_an_earlier_step_dropped_is_rejected(
        client, admin_id):
    ds = await make_dataset(client, admin_id)
    h = auth(admin_id)
    r = await client.post(f"/api/v1/datasets/{ds}/transformations", headers=h,
                          json=body(steps=[
                              {"type": "drop", "columns": ["city"]},
                              {"type": "sort", "by": [{"column": "city"}]},
                          ]))
    assert r.status_code == 400
    assert r.json()["code"] == "unknown-column"


# --- preview ------------------------------------------------------------------

async def test_preview_returns_rows_and_the_output_schema_without_persisting(
        client, admin_id):
    ds = await make_dataset(client, admin_id)
    h = auth(admin_id)
    did = (await client.post(f"/api/v1/datasets/{ds}/transformations",
                             headers=h, json=body())).json()["id"]

    r = await client.post(
        f"/api/v1/datasets/{ds}/transformations/{did}/preview?rows=10", headers=h)
    assert r.status_code == 200, r.text
    preview = r.json()
    assert preview["approximate"] is True
    assert preview["sheet_name"] == "data" and preview["version_number"] == 1
    assert {c["name"] for c in preview["output_schema"]} == {"id", "name", "city", "amount"}
    assert all(row["city"] == row["city"].upper() for row in preview["rows"])

    # A preview is a dry run: no run row, no artifact.
    runs = await client.get(f"/api/v1/datasets/{ds}/transformations/{did}/runs", headers=h)
    assert runs.json()["total"] == 0


# --- running ------------------------------------------------------------------

async def test_run_materializes_the_output_and_records_a_run(client, admin_id):
    ds = await make_dataset(client, admin_id)
    h = auth(admin_id)
    did = (await client.post(f"/api/v1/datasets/{ds}/transformations",
                             headers=h, json=body())).json()["id"]

    r = await client.post(f"/api/v1/datasets/{ds}/transformations/{did}/run", headers=h)
    assert r.status_code == 200, r.text
    run = r.json()
    assert run["status"] == "completed" and run["mode"] == "full"
    assert run["job_id"] and run["artifact_id"]

    summary = run["result_summary"]
    assert summary["source_row_count"] == 5
    assert summary["row_count"] == 4          # the duplicate id was collapsed
    assert summary["step_count"] == 4
    assert summary["sheet"] == "data"

    # The output is downloadable through the normal samples authorization path.
    sample = await client.get(f"/api/v1/samples/{summary['sample_file']}/data", headers=h)
    assert sample.status_code == 200, sample.text
    rows = sample.json()["data"] if isinstance(sample.json(), dict) else sample.json()
    assert len(rows) == 4
    assert {r_["city"] for r_ in rows} == {"NY", "LA", "SF"}
    assert "unknown" in {r_["name"] for r_ in rows}

    listing = await client.get(f"/api/v1/datasets/{ds}/transformations/{did}/runs",
                               headers=h)
    assert listing.json()["total"] == 1


async def test_async_run_is_completed_by_the_worker(client, admin_id):
    from app.shared import worker

    ds = await make_dataset(client, admin_id)
    h = auth(admin_id)
    did = (await client.post(f"/api/v1/datasets/{ds}/transformations",
                             headers=h, json=body())).json()["id"]

    r = await client.post(
        f"/api/v1/datasets/{ds}/transformations/{did}/run?sync=false", headers=h)
    assert r.status_code == 200, r.text
    run_id = r.json()["id"]
    assert r.json()["status"] == "running"       # nothing has executed yet

    # The ASGI test client does not run the lifespan loop, so drain explicitly —
    # the same handler the loop would have used.
    assert await worker.run_pending_jobs_once() >= 1

    r = await client.get(
        f"/api/v1/datasets/{ds}/transformations/runs/{run_id}", headers=h)
    assert r.status_code == 200
    assert r.json()["status"] == "completed"
    assert r.json()["result_summary"]["row_count"] == 4


async def test_a_failing_pipeline_records_the_error_on_the_run(client, admin_id):
    ds = await make_dataset(client, admin_id)
    h = auth(admin_id)
    # Valid at save time; fails at execution because the format doesn't match.
    did = (await client.post(
        f"/api/v1/datasets/{ds}/transformations", headers=h,
        json=body(steps=[{"type": "parse_dates", "columns": ["name"],
                          "format": "%Y-%m-%d"}]))).json()["id"]

    r = await client.post(f"/api/v1/datasets/{ds}/transformations/{did}/run", headers=h)
    assert r.status_code == 400, r.text
    assert r.json()["code"] == "transformation-failed"

    runs = await client.get(f"/api/v1/datasets/{ds}/transformations/{did}/runs",
                            headers=h)
    assert runs.json()["items"][0]["status"] == "failed"
    assert runs.json()["items"][0]["error"]


# --- §20 computed columns -----------------------------------------------------

async def test_a_compute_step_adds_a_derived_column(client, admin_id):
    ds = await make_dataset(client, admin_id)
    h = auth(admin_id)
    steps = [
        {"type": "compute", "into": "amount_with_tax",
         "expression": {"op": "round", "digits": 2, "value": {
             "op": "arith", "fn": "mul",
             "left": {"op": "col", "name": "amount"},
             "right": {"op": "lit", "value": 1.2}}}},
        {"type": "compute", "into": "band",
         "expression": {"op": "if",
                        "cases": [{"when": {"conditions": [
                            {"column": "amount", "op": "gte", "value": 30}]},
                            "then": {"op": "lit", "value": "high"}}],
                        "else": {"op": "lit", "value": "low"}}},
    ]
    did = (await client.post(f"/api/v1/datasets/{ds}/transformations", headers=h,
                             json=body(steps=steps))).json()["id"]

    r = await client.post(
        f"/api/v1/datasets/{ds}/transformations/{did}/preview?rows=50", headers=h)
    assert r.status_code == 200, r.text
    rows = sorted(r.json()["rows"], key=lambda x: (x["id"], x["amount"]))
    assert rows[0]["amount_with_tax"] == 12.0
    assert rows[0]["band"] == "low"
    assert rows[-1]["band"] == "high"
    assert [c["name"] for c in r.json()["output_schema"]][-2:] \
        == ["amount_with_tax", "band"]


async def test_a_computed_column_referencing_an_unknown_column_is_rejected(
        client, admin_id):
    ds = await make_dataset(client, admin_id)
    h = auth(admin_id)
    r = await client.post(f"/api/v1/datasets/{ds}/transformations", headers=h,
                          json=body(steps=[{
                              "type": "compute", "into": "x",
                              "expression": {"op": "col", "name": "ghost"}}]))
    assert r.status_code == 400
    assert r.json()["code"] == "unknown-column"


async def test_a_computed_column_with_a_type_mismatch_is_rejected(client, admin_id):
    ds = await make_dataset(client, admin_id)
    h = auth(admin_id)
    r = await client.post(f"/api/v1/datasets/{ds}/transformations", headers=h,
                          json=body(steps=[{
                              "type": "compute", "into": "x",
                              "expression": {"op": "date_extract", "part": "year",
                                             "value": {"op": "col", "name": "name"}}}]))
    assert r.status_code == 400
    assert r.json()["code"] == "operator-type-mismatch"


# --- §21 auto-profiling -------------------------------------------------------

async def test_run_detail_carries_the_output_profile_and_drift(client, admin_id):
    ds = await make_dataset(client, admin_id)
    h = auth(admin_id)
    did = (await client.post(
        f"/api/v1/datasets/{ds}/transformations", headers=h,
        json=body(steps=[
            {"type": "drop", "columns": ["city"]},
            {"type": "compute", "into": "double_amount",
             "expression": {"op": "arith", "fn": "mul",
                            "left": {"op": "col", "name": "amount"},
                            "right": {"op": "lit", "value": 2}}},
            {"type": "deduplicate", "subset": ["id"], "keep": "first"},
        ]))).json()["id"]

    run_id = (await client.post(
        f"/api/v1/datasets/{ds}/transformations/{did}/run", headers=h)).json()["id"]
    r = await client.get(
        f"/api/v1/datasets/{ds}/transformations/runs/{run_id}", headers=h)
    assert r.status_code == 200, r.text
    detail = r.json()

    profile = detail["output_profile"]
    assert profile["row_count"] == 4                 # fewer than the 5 source rows
    assert {c["name"] for c in profile["columns"]} == {"id", "name", "amount",
                                                      "double_amount"}

    drift = detail["source_drift"]
    assert drift["source"] == "ad_hoc"               # no persisted profile run yet
    assert "city" in drift["removed_columns"]
    assert "double_amount" in drift["added_columns"]
    assert drift["row_count_delta"] == -1


async def test_drift_prefers_the_persisted_source_profile(client, admin_id):
    ds = await make_dataset(client, admin_id)
    h = auth(admin_id)
    # Persist a profile run for the source version first.
    r = await client.post(f"/api/v1/datasets/{ds}/versions/1/profile-runs", headers=h)
    assert r.status_code == 200, r.text

    did = (await client.post(f"/api/v1/datasets/{ds}/transformations",
                             headers=h, json=body())).json()["id"]
    run_id = (await client.post(
        f"/api/v1/datasets/{ds}/transformations/{did}/run", headers=h)).json()["id"]
    detail = (await client.get(
        f"/api/v1/datasets/{ds}/transformations/runs/{run_id}", headers=h)).json()
    assert detail["source_drift"]["source"] == "profile_run"


# --- publishing ---------------------------------------------------------------

async def test_publish_creates_a_new_dataset_and_leaves_the_source_untouched(
        client, admin_id):
    ds = await make_dataset(client, admin_id)
    h = auth(admin_id)
    did = (await client.post(f"/api/v1/datasets/{ds}/transformations",
                             headers=h, json=body())).json()["id"]
    run_id = (await client.post(
        f"/api/v1/datasets/{ds}/transformations/{did}/run", headers=h)).json()["id"]

    r = await client.post(
        f"/api/v1/datasets/{ds}/transformations/runs/{run_id}/publish", headers=h,
        json={"mode": "new_dataset", "name": f"cleaned-{did[:8]}"})
    assert r.status_code == 200, r.text
    published = r.json()
    assert published["mode"] == "new_dataset" and published["version_number"] == 1

    # The new dataset holds the transformed rows.
    r = await client.get(
        f"/api/v1/datasets/{published['dataset_id']}/versions/1/preview", headers=h)
    assert r.status_code == 200
    assert r.json()["total"] == 4

    # Lineage says HOW it was derived, in both directions.
    lineage = (await client.get(
        f"/api/v1/datasets/{published['dataset_id']}/lineage", headers=h)).json()
    assert lineage["parents"][0]["relation"] == "transformed_from"
    assert lineage["parents"][0]["parent_dataset_id"] == ds
    source_lineage = (await client.get(f"/api/v1/datasets/{ds}/lineage", headers=h)).json()
    assert source_lineage["children"][0]["relation"] == "transformed_from"

    # The source is immutable: still one version, still five rows.
    versions = (await client.get(f"/api/v1/datasets/{ds}/versions", headers=h)).json()
    assert versions["total"] == 1
    src = await client.get(f"/api/v1/datasets/{ds}/versions/1/preview", headers=h)
    assert src.json()["total"] == 5


async def test_publish_as_a_new_version_of_the_same_dataset(client, admin_id):
    ds = await make_dataset(client, admin_id)
    h = auth(admin_id)
    did = (await client.post(f"/api/v1/datasets/{ds}/transformations",
                             headers=h, json=body())).json()["id"]
    run_id = (await client.post(
        f"/api/v1/datasets/{ds}/transformations/{did}/run", headers=h)).json()["id"]

    r = await client.post(
        f"/api/v1/datasets/{ds}/transformations/runs/{run_id}/publish", headers=h,
        json={"mode": "new_version"})
    assert r.status_code == 200, r.text
    assert r.json()["dataset_id"] == ds and r.json()["version_number"] == 2
    # Version 1 is untouched — publishing never rewrites history.
    src = await client.get(f"/api/v1/datasets/{ds}/versions/1/preview", headers=h)
    assert src.json()["total"] == 5


async def test_publishing_a_run_that_has_not_completed_is_a_409(client, admin_id):
    ds = await make_dataset(client, admin_id)
    h = auth(admin_id)
    did = (await client.post(f"/api/v1/datasets/{ds}/transformations",
                             headers=h, json=body())).json()["id"]
    run_id = (await client.post(
        f"/api/v1/datasets/{ds}/transformations/{did}/run?sync=false",
        headers=h)).json()["id"]

    r = await client.post(
        f"/api/v1/datasets/{ds}/transformations/runs/{run_id}/publish", headers=h,
        json={"mode": "new_version"})
    assert r.status_code == 409


# --- the timeline -------------------------------------------------------------

async def test_a_run_shows_up_on_the_dataset_timeline(client, admin_id):
    ds = await make_dataset(client, admin_id)
    h = auth(admin_id)
    did = (await client.post(f"/api/v1/datasets/{ds}/transformations",
                             headers=h, json=body())).json()["id"]
    await client.post(f"/api/v1/datasets/{ds}/transformations/{did}/run", headers=h)

    timeline = (await client.get(f"/api/v1/datasets/{ds}/timeline", headers=h)).json()
    events = [e for e in timeline["items"] if e["event_type"] == "transformation_run"]
    assert len(events) == 1
    assert events[0]["details"]["transformation"] == "cleanup"
    assert events[0]["details"]["status"] == "completed"


# --- rename-proofing (the §1 payoff) ------------------------------------------

async def test_a_definition_survives_a_confirmed_sheet_rename(client, admin_id, tmp_path):
    v1, v2 = tmp_path / "v1.xlsx", tmp_path / "v2.xlsx"
    make_workbook(v1)
    make_workbook(v2, second_sheet="Spending")   # Expenses renamed, same schema
    ds = (await upload_file(client, admin_id, v1, name="book.xlsx",
                            content_type=XLSX_MIME))["dataset_id"]
    h = auth(admin_id)

    did = (await client.post(
        f"/api/v1/datasets/{ds}/transformations", headers=h,
        json={"name": "costs", "sheet": "Expenses",
              "steps": [{"type": "sort", "by": [{"column": "cost",
                                                 "direction": "desc"}]}]})).json()["id"]

    await upload_file(client, admin_id, v2, name="book.xlsx",
                      content_type=XLSX_MIME, dataset_id=ds)
    r = await client.post(f"/api/v1/datasets/{ds}/versions/2/confirm-rename",
                          headers=h,
                          json={"from_sheet": "Expenses", "to_sheet": "Spending"})
    assert r.status_code == 200, r.text

    # No definition rewriting happened — logical identity carries it to the new name.
    r = await client.post(f"/api/v1/datasets/{ds}/transformations/{did}/run", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["result_summary"]["sheet"] == "Spending"
    assert r.json()["result_summary"]["version_number"] == 2


# --- cross-cutting contracts --------------------------------------------------

async def test_multi_sheet_version_requires_a_sheet(client, admin_id, tmp_path):
    path = tmp_path / "book.xlsx"
    make_workbook(path)
    ds = (await upload_file(client, admin_id, path, name="book.xlsx",
                            content_type=XLSX_MIME))["dataset_id"]
    r = await client.post(f"/api/v1/datasets/{ds}/transformations",
                          headers=auth(admin_id),
                          json={"name": "t", "steps": [{"type": "limit", "count": 1}]})
    assert r.status_code == 400, r.text
    assert r.json()["code"] == "sheet-selection-required"
    assert "Revenue" in r.json()["sheets"]


async def test_another_teams_transformations_are_invisible(client, admin_id):
    ds = await make_dataset(client, admin_id)
    h = auth(admin_id)
    did = (await client.post(f"/api/v1/datasets/{ds}/transformations",
                             headers=h, json=body())).json()["id"]
    run_id = (await client.post(
        f"/api/v1/datasets/{ds}/transformations/{did}/run", headers=h)).json()["id"]
    sample_file = (await client.get(
        f"/api/v1/datasets/{ds}/transformations/runs/{run_id}",
        headers=h)).json()["result_summary"]["sample_file"]

    outsider, _ = await create_team_user(client, admin_id, "admin")
    oh = auth(outsider)
    base = f"/api/v1/datasets/{ds}/transformations"
    # Existence is hidden, not merely refused.
    assert (await client.get(base, headers=oh)).status_code == 404
    assert (await client.post(base, headers=oh, json=body("x"))).status_code == 404
    assert (await client.get(f"{base}/{did}", headers=oh)).status_code == 404
    assert (await client.post(f"{base}/{did}/preview", headers=oh)).status_code == 404
    assert (await client.post(f"{base}/{did}/run", headers=oh)).status_code == 404
    assert (await client.get(f"{base}/runs/{run_id}", headers=oh)).status_code == 404
    assert (await client.post(f"{base}/runs/{run_id}/publish", headers=oh,
                              json={"mode": "new_version"})).status_code == 404
    # The output artifact is not reachable either.
    assert (await client.get(f"/api/v1/samples/{sample_file}",
                             headers=oh)).status_code == 404


async def test_viewers_can_read_but_not_write_or_run(client, admin_id):
    ds = await make_dataset(client, admin_id)
    h = auth(admin_id)
    did = (await client.post(f"/api/v1/datasets/{ds}/transformations",
                             headers=h, json=body())).json()["id"]

    viewer, _ = await create_team_user(client, admin_id, "viewer",
                                       team_id="00000000-0000-0000-0000-000000000001")
    vh = auth(viewer)
    base = f"/api/v1/datasets/{ds}/transformations"

    assert (await client.get(base, headers=vh)).status_code == 200
    assert (await client.get(f"{base}/{did}", headers=vh)).status_code == 200
    assert (await client.post(f"{base}/{did}/preview", headers=vh)).status_code == 200

    assert (await client.post(base, headers=vh, json=body("v"))).status_code == 403
    assert (await client.patch(f"{base}/{did}", headers=vh,
                               json={"description": "x"})).status_code == 403
    assert (await client.post(f"{base}/{did}/run", headers=vh)).status_code == 403
    assert (await client.delete(f"{base}/{did}", headers=vh)).status_code == 403
