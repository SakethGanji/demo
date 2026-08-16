"""Wave 3 §18 — catalog upgrades: facets + list filters for the signals the
§17 health read-model exposes (validation status, schema drift, documentation).

The signals are computed from the SAME sources as /datasets/{id}/health, so
these tests double as a cross-check that the catalog and health agree.
"""

from __future__ import annotations

import json

from conftest import auth, create_team_user, upload_inline

V1 = [{"region": "EU", "amount": 100.5}, {"region": "US", "amount": 50.5},
      {"region": "APAC", "amount": 75.5}]
# Same rows plus a column -> a schema-fingerprint change across versions.
V1_WIDER = [{"region": "EU", "amount": 100.5, "channel": "web"}]


async def _fully_documented_validated(client, user_id):
    """A dataset that is fully documented AND has a passing validation run on
    its current version -> documentation=full, validation_status=passed."""
    h = auth(user_id)
    ds = (await upload_inline(client, user_id, json.dumps(V1)))["dataset_id"]
    assert (await client.patch(f"/api/v1/datasets/{ds}", headers=h, json={
        "description": "Orders rollup", "domain": "sales"})).status_code == 200
    assert (await client.put(f"/api/v1/datasets/{ds}/sheet-metadata/data",
                             headers=h, json={"grain": "one row per order"})
            ).status_code == 200
    for col in ("region", "amount"):
        assert (await client.put(
            f"/api/v1/datasets/{ds}/sheet-metadata/data/columns/{col}",
            headers=h, json={"business_name": col.title()})).status_code == 200
    assert (await client.post(f"/api/v1/datasets/{ds}/rules", headers=h, json={
        "name": "region-not-null", "rule_type": "not_null",
        "sheet_selector": "data", "column_selector": "region"})
            ).status_code == 201
    run = (await client.post(f"/api/v1/datasets/{ds}/versions/1/validate",
                             headers=h)).json()
    assert run["status"] == "completed" and run["error_failures"] == 0
    return ds


async def _drifted(client, user_id):
    """Two versions with different schemas -> has_schema_drift=true, undocumented."""
    ds = (await upload_inline(client, user_id, json.dumps(V1)))["dataset_id"]
    await upload_inline(client, user_id, json.dumps(V1_WIDER), dataset_id=ds)
    return ds


async def _facets(client, user_id):
    r = await client.get("/api/v1/datasets/facets", headers=auth(user_id))
    assert r.status_code == 200, r.text
    return r.json()


async def test_facets_expose_signal_buckets(client, admin_id):
    plain = (await upload_inline(client, admin_id, json.dumps(V1)))["dataset_id"]  # noqa: F841
    documented = await _fully_documented_validated(client, admin_id)  # noqa: F841
    drifted = await _drifted(client, admin_id)  # noqa: F841

    f = await _facets(client, admin_id)
    assert f["validation_status"] == {"none": 2, "passed": 1}
    assert f["has_schema_drift"] == {"false": 2, "true": 1}
    assert f["documentation"] == {"none": 2, "full": 1}


async def test_failed_validation_bucket(client, admin_id):
    h = auth(admin_id)
    ds = (await upload_inline(client, admin_id, json.dumps(
        [{"region": "EU", "amount": None}, {"region": "US", "amount": 5.0}]))
    )["dataset_id"]
    assert (await client.post(f"/api/v1/datasets/{ds}/rules", headers=h, json={
        "name": "amount-not-null", "rule_type": "not_null",
        "sheet_selector": "data", "column_selector": "amount"})
            ).status_code == 201
    run = (await client.post(f"/api/v1/datasets/{ds}/versions/1/validate",
                             headers=h)).json()
    assert run["error_failures"] > 0

    f = await _facets(client, admin_id)
    assert f["validation_status"] == {"failed": 1}


async def _list(client, user_id, **params):
    r = await client.get("/api/v1/datasets", headers=auth(user_id), params=params)
    assert r.status_code == 200, r.text
    return r.json()["items"]


async def test_filters_narrow_the_listing(client, admin_id):
    plain = (await upload_inline(client, admin_id, json.dumps(V1)))["dataset_id"]
    documented = await _fully_documented_validated(client, admin_id)
    drifted = await _drifted(client, admin_id)

    ids = lambda items: {d["id"] for d in items}
    assert ids(await _list(client, admin_id, validation_status="passed")) == {documented}
    assert ids(await _list(client, admin_id, has_schema_drift=True)) == {drifted}
    assert ids(await _list(client, admin_id, documentation="full")) == {documented}
    assert ids(await _list(client, admin_id, documentation="none")) == {plain, drifted}


async def test_listing_rows_carry_the_signals(client, admin_id):
    documented = await _fully_documented_validated(client, admin_id)
    (row,) = [d for d in await _list(client, admin_id) if d["id"] == documented]
    assert row["validation_status"] == "passed"
    assert row["has_schema_drift"] is False
    assert row["documentation"] == "full"


async def test_facets_are_team_scoped(client, admin_id):
    # Admin (superuser) datasets live in the Default team.
    await upload_inline(client, admin_id, json.dumps(V1))
    member, member_team = await create_team_user(client, admin_id, "editor")
    # The member's own team starts empty.
    f = await _facets(client, member)
    assert f["validation_status"] == {}
    assert f["documentation"] == {}

    ds = (await upload_inline(client, member, json.dumps(V1),  # noqa: F841
                              team_id=member_team))["dataset_id"]
    f = await _facets(client, member)
    assert f["validation_status"] == {"none": 1}
    assert f["documentation"] == {"none": 1}
    assert f["has_schema_drift"] == {"false": 1}


async def _document(client, uid, ds, *, desc=None, domain=None, grain=None, cols=()):
    h = auth(uid)
    patch = {}
    if desc is not None:
        patch["description"] = desc
    if domain is not None:
        patch["domain"] = domain
    if patch:
        assert (await client.patch(f"/api/v1/datasets/{ds}", headers=h,
                                   json=patch)).status_code == 200
    if grain is not None:
        assert (await client.put(f"/api/v1/datasets/{ds}/sheet-metadata/data",
                                 headers=h, json={"grain": grain})).status_code == 200
    for col in cols:
        assert (await client.put(
            f"/api/v1/datasets/{ds}/sheet-metadata/data/columns/{col}",
            headers=h, json={"business_name": col.title()})).status_code == 200


async def test_catalog_documentation_matches_health(client, admin_id):
    """The catalog documentation bucket must equal the §17 health bucket for the
    SAME dataset across the threshold matrix. The facet SQL re-expresses
    ``evaluate_documentation`` in SQL rather than calling it, so this pins the
    two implementations together (incl. the 50%-column-coverage boundary)."""
    states = {
        "none": {},
        "desc_only": {"desc": "orders"},
        "sheet_cols_no_meta": {"grain": "one per order", "cols": ("region", "amount")},
        # 1 of 2 columns documented == DOC_COLUMN_COVERAGE (0.5) boundary -> full.
        "half_cols_full": {"desc": "orders", "domain": "sales",
                           "grain": "one per order", "cols": ("region",)},
        "full": {"desc": "orders", "domain": "sales",
                 "grain": "one per order", "cols": ("region", "amount")},
    }
    ids = {}
    for label, kw in states.items():
        ds = (await upload_inline(client, admin_id, json.dumps(V1)))["dataset_id"]
        await _document(client, admin_id, ds, **kw)
        ids[ds] = label

    health_bucket = {}
    for ds in ids:
        r = await client.get(f"/api/v1/datasets/{ds}/health", headers=auth(admin_id))
        assert r.status_code == 200, r.text
        health_bucket[ds] = r.json()["dimensions"]["documentation"]["evidence"]["bucket"]

    rows = await _list(client, admin_id, limit=200)
    catalog_bucket = {r["id"]: r["documentation"] for r in rows if r["id"] in ids}
    # Catalog == health, dataset for dataset.
    assert catalog_bucket == health_bucket
    # And the boundary cases landed where the thresholds say they should.
    assert {ids[ds]: b for ds, b in health_bucket.items()} == {
        "none": "none", "desc_only": "partial", "sheet_cols_no_meta": "partial",
        "half_cols_full": "full", "full": "full"}
