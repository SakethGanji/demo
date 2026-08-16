"""Phase 3 (reuse) — saved analytics, run history, publish, lineage.

Journey: save definitions → run them → inspect run history + artifacts →
publish an output as a new dataset and as a new version → verify lineage
both directions → the published data is a real, queryable dataset.
"""

from __future__ import annotations

from conftest import SAMPLE_CSV, auth, make_crm_workbook, upload_file, upload_inline


async def _upload_csv(client, admin_id):
    body = await upload_file(client, admin_id, SAMPLE_CSV, name="accounts.csv")
    return body["dataset_id"]


async def test_library_journey(client, admin_id):
    h = auth(admin_id)
    ds = await _upload_csv(client, admin_id)

    # ---- 1. Save a sampling definition pinned to the current version ----
    r = await client.post(f"/api/v1/datasets/{ds}/analytics", headers=h, json={
        "name": "daily-sample", "kind": "sample",
        "params": {"target_total_volume": 5,
                   "sampling_steps": [{"method": "random", "sample_size": 5}],
                   "seed": 42}})
    assert r.status_code == 201, r.text
    sample_def = r.json()["id"]

    r = await client.post(f"/api/v1/datasets/{ds}/analytics", headers=h, json={
        "name": "quick-profile", "kind": "profile",
        "params": {"include_histograms": False}})
    assert r.status_code == 201
    profile_def = r.json()["id"]

    defs = (await client.get(f"/api/v1/datasets/{ds}/analytics", headers=h)).json()
    assert defs["total"] == 2

    # Duplicate names are rejected (unique per dataset).
    r = await client.post(f"/api/v1/datasets/{ds}/analytics", headers=h, json={
        "name": "daily-sample", "kind": "profile"})
    assert r.status_code == 409

    # ---- 2. Run them; results are inline AND durably recorded ----
    r = await client.post(f"/api/v1/datasets/{ds}/analytics/{sample_def}/run", headers=h)
    assert r.status_code == 200, r.text
    run = r.json()
    assert run["status"] == "completed"
    assert run["result"]["sampled_count"] == 5
    assert run["artifact_id"]                      # output registered as artifact
    assert run["result_summary"]["sample_file"]
    run_id = run["id"]

    r = await client.post(f"/api/v1/datasets/{ds}/analytics/{profile_def}/run", headers=h)
    assert r.status_code == 200
    assert r.json()["artifact_id"] is None         # profiles produce no artifact

    history = (await client.get(f"/api/v1/datasets/{ds}/analytics/{sample_def}/runs",
                                headers=h)).json()
    assert history["total"] == 1 and history["items"][0]["status"] == "completed"

    # ---- 3. Publish the sample as a NEW DATASET ----
    r = await client.post(f"/api/v1/datasets/{ds}/analytics/runs/{run_id}/publish",
                          headers=h, json={"mode": "new_dataset", "name": "accounts-sample"})
    assert r.status_code == 200, r.text
    pub = r.json()
    child = pub["dataset_id"]
    assert pub["dataset_name"] == "accounts-sample" and pub["version_number"] == 1

    # The published dataset is a real dataset: metadata, sheets, analytics all work.
    meta = (await client.get(f"/api/v1/datasets/{child}", headers=h)).json()
    assert meta["row_count"] == 5
    sheets = (await client.get(f"/api/v1/datasets/{child}/sheets", headers=h)).json()["items"]
    assert sheets[0]["name"] == "data" and sheets[0]["schema_fingerprint"]

    # ---- 4. Publish the same run as a NEW VERSION of the source dataset ----
    r = await client.post(f"/api/v1/datasets/{ds}/analytics/runs/{run_id}/publish",
                          headers=h, json={"mode": "new_version"})
    assert r.status_code == 200 and r.json()["version_number"] == 2
    versions = (await client.get(f"/api/v1/datasets/{ds}/versions", headers=h)).json()["items"]
    assert versions[0]["row_count"] == 5  # v2 is the 5-row sample

    # ---- 5. Lineage: child points up, parent points down ----
    lin = (await client.get(f"/api/v1/datasets/{child}/lineage", headers=h)).json()
    assert len(lin["parents"]) == 1
    assert lin["parents"][0]["relation"] == "sampled_from"  # kind-specific
    assert lin["parents"][0]["parent_dataset_id"] == ds
    assert lin["parents"][0]["parent_version_number"] == 1

    lin = (await client.get(f"/api/v1/datasets/{ds}/lineage", headers=h)).json()
    child_ids = {c["child_dataset_id"] for c in lin["children"]}
    assert child in child_ids and ds in child_ids  # new_dataset + new_version children

    # ---- 6. Publishing a profile run is refused (no artifact) ----
    prof_runs = (await client.get(f"/api/v1/datasets/{ds}/analytics/{profile_def}/runs",
                                  headers=h)).json()["items"]
    r = await client.post(
        f"/api/v1/datasets/{ds}/analytics/runs/{prof_runs[0]['id']}/publish",
        headers=h, json={"mode": "new_dataset"})
    assert r.status_code == 409

    # ---- 7. Definition lifecycle: retarget to the production tag ----
    r = await client.put(f"/api/v1/datasets/{ds}/tags", headers=h,
                         json={"tag_name": "production", "version_number": 1})
    assert r.status_code == 200
    r = await client.patch(f"/api/v1/datasets/{ds}/analytics/{sample_def}", headers=h,
                           json={"version_selector": {"mode": "tag", "tag": "production"}})
    assert r.status_code == 200 and r.json()["version_selector"]["mode"] == "tag"
    r = await client.post(f"/api/v1/datasets/{ds}/analytics/{sample_def}/run", headers=h)
    assert r.status_code == 200  # runs against the tagged (original) version

    r = await client.delete(f"/api/v1/datasets/{ds}/analytics/{profile_def}", headers=h)
    assert r.status_code == 204
    defs = (await client.get(f"/api/v1/datasets/{ds}/analytics", headers=h)).json()
    assert defs["total"] == 1


async def test_definition_single_get(client, admin_id):
    h = auth(admin_id)
    ds = await _upload_csv(client, admin_id)
    r = await client.post(f"/api/v1/datasets/{ds}/analytics", headers=h, json={
        "name": "weekly-profile", "kind": "profile",
        "params": {"include_histograms": False}})
    def_id = r.json()["id"]

    one = (await client.get(f"/api/v1/datasets/{ds}/analytics/{def_id}",
                            headers=h)).json()
    assert one["name"] == "weekly-profile" and one["kind"] == "profile"
    assert one["version_selector"]["mode"] == "current"

    r = await client.get(
        f"/api/v1/datasets/{ds}/analytics/11111111-1111-1111-1111-111111111111",
        headers=h)
    assert r.status_code == 404

    # A definition id from a different dataset doesn't resolve here.
    ds2 = await _upload_csv(client, admin_id)
    r = await client.get(f"/api/v1/datasets/{ds2}/analytics/{def_id}", headers=h)
    assert r.status_code == 404


async def _run_sample_def(client, h, ds):
    r = await client.post(f"/api/v1/datasets/{ds}/analytics", headers=h, json={
        "name": "pub-sample", "kind": "sample",
        "params": {"target_total_volume": 3,
                   "sampling_steps": [{"method": "random", "sample_size": 3}],
                   "seed": 1}})
    assert r.status_code == 201, r.text
    def_id = r.json()["id"]
    run = (await client.post(f"/api/v1/datasets/{ds}/analytics/{def_id}/run",
                             headers=h)).json()
    assert run["status"] == "completed"
    return def_id, run["id"]


async def test_publish_name_collision_409(client, admin_id):
    h = auth(admin_id)
    ds = await _upload_csv(client, admin_id)
    _, run_id = await _run_sample_def(client, h, ds)
    publish_url = f"/api/v1/datasets/{ds}/analytics/runs/{run_id}/publish"

    r = await client.post(publish_url, headers=h,
                          json={"mode": "new_dataset", "name": "golden-sample"})
    assert r.status_code == 200, r.text

    # Same explicit name again → refused, nothing shadowed.
    r = await client.post(publish_url, headers=h,
                          json={"mode": "new_dataset", "name": "golden-sample"})
    assert r.status_code == 409 and "golden-sample" in r.json()["detail"]

    # Default-name republish collides with itself the second time too.
    r = await client.post(publish_url, headers=h, json={"mode": "new_dataset"})
    assert r.status_code == 200, r.text
    r = await client.post(publish_url, headers=h, json={"mode": "new_dataset"})
    assert r.status_code == 409

    # new_version mode has no name to collide — still allowed.
    r = await client.post(publish_url, headers=h, json={"mode": "new_version"})
    assert r.status_code == 200, r.text


async def test_retarget_definition_to_pinned_version(client, admin_id):
    h = auth(admin_id)
    ds = await _upload_csv(client, admin_id)
    def_id, run_id = await _run_sample_def(client, h, ds)

    # Publish the sample as v2 — current moves to the 3-row version.
    r = await client.post(f"/api/v1/datasets/{ds}/analytics/runs/{run_id}/publish",
                          headers=h, json={"mode": "new_version"})
    assert r.status_code == 200 and r.json()["version_number"] == 2

    # Pin the definition to v1 and run: it samples the original big version.
    r = await client.patch(f"/api/v1/datasets/{ds}/analytics/{def_id}", headers=h,
                           json={"version_selector": {"mode": "version",
                                                      "version_number": 1}})
    assert r.status_code == 200
    selector = r.json()["version_selector"]
    assert selector["mode"] == "version" and selector["version_number"] == 1
    run = (await client.post(f"/api/v1/datasets/{ds}/analytics/{def_id}/run",
                             headers=h)).json()
    assert run["status"] == "completed"
    assert run["result"]["original_count"] > 3  # read v1, not the 3-row v2


async def test_dataset_delete_cleans_up_run_artifacts(client, admin_id):
    """Run outputs registered as artifacts die with the dataset — rows via FK
    cascade, blobs via the delete service (they live in the samples area,
    outside the dataset's version prefixes)."""
    from sqlalchemy import text
    from app.infra.db.postgres.session import engine

    h = auth(admin_id)
    ds = await _upload_csv(client, admin_id)
    _, run_id = await _run_sample_def(client, h, ds)

    runs = (await client.get(f"/api/v1/datasets/{ds}/analytics", headers=h)).json()
    sample_file = None
    for d in runs["items"]:
        hist = (await client.get(
            f"/api/v1/datasets/{ds}/analytics/{d['id']}/runs", headers=h)).json()
        for r in hist["items"]:
            sample_file = (r.get("result_summary") or {}).get("sample_file")
    assert sample_file, "run should have produced a stored sample artifact"
    assert (await client.get(f"/api/v1/samples/{sample_file}",
                             headers=h)).status_code == 200

    r = await client.delete(f"/api/v1/datasets/{ds}", headers=h)
    assert r.status_code == 200 and "artifact" in r.json()["message"]

    # Blob is gone from storage, and no artifact rows survive the cascade.
    assert (await client.get(f"/api/v1/samples/{sample_file}",
                             headers=h)).status_code == 404
    async with engine.connect() as conn:
        count = (await conn.execute(
            text("SELECT COUNT(*) FROM accelerator.artifacts"))).scalar()
    assert count == 0


async def test_saved_definition_with_stale_params_is_a_400_not_a_500(client, admin_id):
    """`params` is stored as free-form JSON, so a definition can outlive the
    schema it was saved against. `sort_order: "ASC"` was legal when the field
    was a bare `str` (and ran as *descending*); it is now Literal["asc","desc"].

    That must report which param is wrong, not "Analytics run failed" with a
    500 — the request was fine, the stored definition is not.
    """
    h = auth(admin_id)
    ds = (await upload_inline(
        client, admin_id,
        '[{"region": "US", "amount": 3}, {"region": "EU", "amount": 5}]'))["dataset_id"]

    agg = {"group_by": ["region"],
           "aggregations": [{"column": "amount", "function": "sum", "alias": "total"}],
           "sort_by": "total"}
    r = await client.post(f"/api/v1/datasets/{ds}/analytics", headers=h, json={
        "name": "legacy-sort", "kind": "aggregate",
        "params": {**agg, "sort_order": "ASC"}})
    assert r.status_code == 201, r.text
    def_id = r.json()["id"]

    r = await client.post(f"/api/v1/datasets/{ds}/analytics/{def_id}/run", headers=h)
    assert r.status_code == 400, r.text
    assert r.headers["content-type"].startswith("application/problem+json")
    body = r.json()
    assert body["code"] == "invalid-definition"
    assert "sort_order" in body["detail"]
    assert body["errors"] == [{"param": "sort_order",
                              "reason": "Input should be 'asc' or 'desc'",
                              "value": "ASC"}]

    # Nothing ran, so nothing was recorded: no run row, no job, no failed run.
    hist = (await client.get(f"/api/v1/datasets/{ds}/analytics/{def_id}/runs",
                             headers=h)).json()
    assert hist["total"] == 0

    # A definition missing a required param is the same 400 (this one 500'd
    # before the Literal change too).
    r = await client.post(f"/api/v1/datasets/{ds}/analytics", headers=h, json={
        "name": "no-group-by", "kind": "aggregate",
        "params": {"aggregations": agg["aggregations"]}})
    broken = r.json()["id"]
    r = await client.post(f"/api/v1/datasets/{ds}/analytics/{broken}/run", headers=h)
    assert r.status_code == 400 and r.json()["code"] == "invalid-definition"
    assert "group_by" in r.json()["detail"]

    # Fix the stored params and the same definition runs.
    r = await client.patch(f"/api/v1/datasets/{ds}/analytics/{def_id}", headers=h,
                           json={"params": {**agg, "sort_order": "asc"}})
    assert r.status_code == 200
    r = await client.post(f"/api/v1/datasets/{ds}/analytics/{def_id}/run", headers=h)
    assert r.status_code == 200, r.text
    assert [row["total"] for row in r.json()["result"]["data"]] == [3, 5]


async def test_chart_render_on_a_stale_definition_is_also_a_400(client, admin_id):
    """The chart path recomputes the definition without recording a run; it has
    no blanket `except Exception` at all, so the same ValidationError surfaced
    there as a bare 500."""
    h = auth(admin_id)
    ds = (await upload_inline(
        client, admin_id,
        '[{"region": "US", "amount": 3}, {"region": "EU", "amount": 5}]'))["dataset_id"]

    r = await client.post(f"/api/v1/datasets/{ds}/analytics", headers=h, json={
        "name": "legacy-chart-source", "kind": "aggregate",
        "params": {"group_by": ["region"],
                   "aggregations": [{"column": "amount", "function": "sum",
                                     "alias": "total"}],
                   "sort_by": "total", "sort_order": "DESC"}})
    def_id = r.json()["id"]

    r = await client.post(f"/api/v1/datasets/{ds}/charts", headers=h, json={
        "name": "by-region", "chart_type": "bar", "definition_id": def_id})
    assert r.status_code == 201, r.text
    chart_id = r.json()["id"]

    r = await client.post(f"/api/v1/datasets/{ds}/charts/{chart_id}/render", headers=h)
    assert r.status_code == 400, r.text
    assert r.json()["code"] == "invalid-definition"
    assert "sort_order" in r.json()["detail"]


# ---------------------------------------------------------------------------
# Failures DURING a run: the client keeps the real error, the row is closed
# ---------------------------------------------------------------------------

async def _runs(client, h, ds, def_id):
    return (await client.get(f"/api/v1/datasets/{ds}/analytics/{def_id}/runs",
                             headers=h)).json()["items"]


async def test_actionable_error_during_a_run_keeps_its_status_and_code(
    client, admin_id, tmp_path
):
    """A ProblemException raised mid-run must reach the client intact.

    ``ProblemException`` subclasses STARLETTE's ``HTTPException``; the guard
    here caught FASTAPI's, which is a sibling, not a parent. So every
    actionable 4xx raised while a run executed missed the re-raise, fell into
    the blanket ``except Exception``, and was reported as a 500 "Analytics run
    failed" — losing the status, the ``code`` and the extra fields that make it
    actionable.
    """
    h = auth(admin_id)
    p = tmp_path / "crm.xlsx"
    make_crm_workbook(p)
    ds = (await upload_file(client, admin_id, p))["dataset_id"]

    # Multi-sheet dataset + a definition that names no sheet: the aggregation
    # refuses to guess and raises sheet-selection-required while running.
    r = await client.post(f"/api/v1/datasets/{ds}/analytics", headers=h, json={
        "name": "no-sheet", "kind": "aggregate",
        "params": {"group_by": ["tier"],
                   "aggregations": [{"column": "total", "function": "sum"}]}})
    assert r.status_code == 201, r.text
    def_id = r.json()["id"]

    r = await client.post(f"/api/v1/datasets/{ds}/analytics/{def_id}/run", headers=h)
    assert r.status_code == 400, r.text
    assert r.headers["content-type"].startswith("application/problem+json")
    body = r.json()
    assert body["code"] == "sheet-selection-required"
    # The extra fields survive too — this is the whole point of the code.
    assert "Orders" in body["sheets"] and "Customers" in body["sheets"]
    assert "Analytics run failed" not in body["detail"]

    # It DID start, so it is recorded — as failed, never left running.
    runs = await _runs(client, h, ds, def_id)
    assert [r["status"] for r in runs] == ["failed"]
    assert "sheet" in runs[0]["error"]


async def test_no_failure_path_leaves_a_run_stuck_in_running(client, admin_id, tmp_path):
    """Failure bookkeeping is uniform across all three exception classes.

    A plain FastAPI ``HTTPException`` used to be re-raised with NO bookkeeping
    at all: the client got its 4xx, but the ``analytics_runs`` row (and its
    job) stayed ``running`` forever. Nothing retries or reaps that state, so it
    only ever accumulates. Both classes now close the row before propagating.
    """
    h = auth(admin_id)
    p = tmp_path / "crm.xlsx"
    make_crm_workbook(p)
    ds = (await upload_file(client, admin_id, p))["dataset_id"]

    # Binds cleanly (group_by is list[str]) but the column is not in the sheet,
    # so the aggregation service raises a bare fastapi.HTTPException mid-run.
    r = await client.post(f"/api/v1/datasets/{ds}/analytics", headers=h, json={
        "name": "ghost-group", "kind": "aggregate", "sheet": "Orders",
        "params": {"group_by": ["ghost"],
                   "aggregations": [{"column": "total", "function": "sum"}]}})
    def_id = r.json()["id"]

    r = await client.post(f"/api/v1/datasets/{ds}/analytics/{def_id}/run", headers=h)
    assert r.status_code == 400, r.text
    assert "ghost" in r.json()["detail"]

    runs = await _runs(client, h, ds, def_id)
    assert [r["status"] for r in runs] == ["failed"]
    assert runs[0]["completed_at"] is not None

    # The job the run opened is closed too — they are the operational and the
    # product record of the SAME attempt and must not disagree.
    jobs = (await client.get("/api/v1/jobs", headers=h,
                             params={"job_type": "analytics"})).json()
    statuses = {j["status"] for j in jobs["items"]}
    assert statuses == {"failed"}, jobs["items"]

    # Nothing anywhere is still running.
    all_runs = await _runs(client, h, ds, def_id)
    assert not [r for r in all_runs if r["status"] == "running"]
