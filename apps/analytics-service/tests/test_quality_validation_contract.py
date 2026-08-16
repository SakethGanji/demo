"""What POST /versions/{n}/validate promises about its response and its failures.

Three separate promises, all of them things a UI builds on: the status code it
gets when a run cannot proceed, the shape of the document it gets when the run
succeeds, and the reachability of the failing rows the run points at.
"""

from __future__ import annotations

from sqlalchemy import text

from app.infra.db.postgres import async_session_factory
from conftest import auth, make_orders_workbook, upload_file

PROBLEM = "application/problem+json"


async def _orders_dataset(client, admin_id, tmp_path, *, clean, filename="orders.xlsx"):
    wb = tmp_path / filename
    make_orders_workbook(wb, clean=clean)
    body = await upload_file(client, admin_id, wb, name=filename)
    return body["dataset_id"]


async def _create_rule(client, h, ds, spec):
    r = await client.post(f"/api/v1/datasets/{ds}/rules", headers=h, json=spec)
    assert r.status_code == 201, r.text
    return r.json()


async def _sheet_storage_keys(dataset_id: str) -> list[str]:
    async with async_session_factory() as s:
        return [r[0] for r in (await s.execute(
            text("""SELECT s.storage_key FROM dataset_version_sheets s
                    JOIN dataset_versions v ON v.id = s.dataset_version_id
                    WHERE v.dataset_id = :d"""), {"d": dataset_id})).all()]


async def _forget_sheet_schemas(dataset_id: str) -> None:
    async with async_session_factory() as s:
        await s.execute(
            text("""UPDATE dataset_version_sheets SET schema_json = NULL
                    WHERE dataset_version_id IN (
                        SELECT id FROM dataset_versions WHERE dataset_id = :d)"""),
            {"d": dataset_id})
        await s.commit()


async def test_validate_surfaces_an_unreadable_sheet_as_404_not_500(
        client, admin_id, tmp_path):
    """A precise error raised mid-run must reach the caller with its own status.

    ``ensure_sheet_schema`` answers 404 "Sheet data unreadable" when a version's
    parquet has gone — a real state after a bucket lifecycle rule or a botched
    restore. Collapsing that into 500 "Validation run failed" tells the operator
    to file a bug against the service when the truthful answer names the missing
    data, and it makes every genuine crash and every missing blob look alike in
    the logs. The run and its job must still be closed as failed either way, so
    nothing is left stranded in 'running'.
    """
    from app.infra.db.storage import get_storage

    h = auth(admin_id)
    ds = await _orders_dataset(client, admin_id, tmp_path, clean=True)
    await _create_rule(client, h, ds, {
        "name": "rows-present", "rule_type": "row_count_min",
        "sheet_selector": "orders", "parameters": {"min": 1}})

    # Make the sheets unreadable: no cached schema, and no parquet to describe.
    await _forget_sheet_schemas(ds)
    storage = get_storage()
    for key in await _sheet_storage_keys(ds):
        storage.delete(key)

    r = await client.post(f"/api/v1/datasets/{ds}/versions/1/validate", headers=h)
    assert r.status_code == 404, r.text
    assert r.headers["content-type"].startswith(PROBLEM)
    assert "unreadable" in r.json()["detail"]

    runs = (await client.get(f"/api/v1/datasets/{ds}/versions/1/validations",
                             headers=h)).json()
    assert runs["total"] == 1
    run = runs["items"][0]
    assert run["status"] == "failed" and run["completed_at"]
    job = (await client.get(f"/api/v1/jobs/{run['job_id']}", headers=h)).json()
    assert job["status"] == "failed"


async def test_validate_returns_the_same_document_the_run_detail_returns(
        client, admin_id, tmp_path):
    """POST /validate and GET /validations/{id} must not disagree about one run.

    They describe the identical run, so a UI that renders the POST response and
    then re-fetches on refresh has to see the same rows in the same order. The
    POST used to hand back a locally-built list with ``id: null`` on every
    result — nothing to link to, nothing to key a list on — ordered by rule
    creation while the GET ordered by rule name, so the two views of one run
    disagreed on both identity and order.
    """
    h = auth(admin_id)
    ds = await _orders_dataset(client, admin_id, tmp_path, clean=False)
    # Names deliberately out of creation order, so name-order != created order.
    await _create_rule(client, h, ds, {
        "name": "z-orders-rows", "rule_type": "row_count_min",
        "sheet_selector": "orders", "parameters": {"min": 1}})
    await _create_rule(client, h, ds, {
        "name": "a-customer-id-not-null", "rule_type": "not_null",
        "sheet_selector": "customers", "column_selector": "customer_id"})
    await _create_rule(client, h, ds, {
        "name": "m-tier-accepted", "rule_type": "accepted_values",
        "sheet_selector": "customers", "column_selector": "tier",
        "parameters": {"values": ["gold", "silver", "bronze", "copper"]}})

    posted = (await client.post(f"/api/v1/datasets/{ds}/versions/1/validate",
                                headers=h)).json()
    fetched = (await client.get(f"/api/v1/datasets/{ds}/validations/{posted['id']}",
                                headers=h)).json()

    assert posted == fetched
    assert [r["rule_name"] for r in posted["results"]] == [
        "a-customer-id-not-null", "m-tier-accepted", "z-orders-rows"]
    assert all(r["id"] for r in posted["results"]), (
        "every result is a persisted row and must carry its id")
    failed = next(r for r in posted["results"] if r["rule_name"] == "a-customer-id-not-null")
    assert failed["status"] == "failed" and failed["failure_sample_file"]


async def test_failure_rows_stay_reachable_when_the_url_uuid_is_upper_case(
        client, admin_id, tmp_path):
    """The failure parquet must be written under the key it is registered at.

    UUIDs are case-insensitive to Postgres, so ``/datasets/{DS_UPPER}/...``
    authorizes and validates exactly like the lower-case spelling — but the
    storage key is a plain string. Writing under the raw path parameter while
    registering under the canonical id produces an artifact row pointing at a
    key that holds nothing, and the file it does hold is unreachable forever:
    the artifact row is the only way to resolve a blob. The steward clicks
    through to "the 1 row that failed" and gets an error.
    """
    h = auth(admin_id)
    ds = await _orders_dataset(client, admin_id, tmp_path, clean=False)
    await _create_rule(client, h, ds, {
        "name": "customer-id-not-null", "rule_type": "not_null",
        "sheet_selector": "customers", "column_selector": "customer_id"})

    r = await client.post(f"/api/v1/datasets/{ds.upper()}/versions/1/validate", headers=h)
    assert r.status_code == 200, r.text
    result = r.json()["results"][0]
    assert result["status"] == "failed" and result["failure_sample_file"]

    fetched = await client.get(
        f"/api/v1/samples/{result['failure_sample_file']}/data", headers=h)
    assert fetched.status_code == 200, fetched.text
    body = fetched.json()
    rows = body["data"] if isinstance(body, dict) else body
    assert len(rows) == 1

    # A registered artifact whose key resolves also has a size; None means the
    # sizing probe failed, which is the same mismatch seen from the other end.
    async with async_session_factory() as s:
        size = (await s.execute(
            text("SELECT size_bytes FROM artifacts WHERE filename = :f"),
            {"f": result["failure_sample_file"]})).scalar()
    assert size, "the registered key must point at the parquet that was written"
