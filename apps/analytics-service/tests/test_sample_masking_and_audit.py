"""Derived-artifact reads honor masking and are audited; teams-list envelope.

A sensitive column is masked (or the raw file blocked) on the explorer, download,
SQL, and analytics endpoints. An elevated caller's aggregate/sql/pivot output is
a team-owned parquet holding the raw values, so the artifact read must apply the
same raw-file gate — otherwise a DATASET_READ teammate reads the sensitive
values straight out of the saved output. And that egress must leave an audit
trail.
"""

from __future__ import annotations

import json

from conftest import auth, create_team_user, upload_inline

DEFAULT_TEAM = "00000000-0000-0000-0000-000000000001"

_PII = [
    {"id": 1, "email": "ana@example.com", "amount": 100.0},
    {"id": 2, "email": "bob@example.com", "amount": 250.0},
    {"id": 3, "email": "cy@secret.org", "amount": 5.0},
]


async def _pii_dataset(client, admin_id):
    ds = (await upload_inline(client, admin_id, json.dumps(_PII)))["dataset_id"]
    r = await client.put(
        f"/api/v1/datasets/{ds}/sheet-metadata/data/columns/email",
        headers=auth(admin_id),
        json={"business_name": "Contact email", "semantic_type": "email",
              "sensitivity": "confidential"})
    assert r.status_code == 200, r.text
    return ds


async def _admin_aggregate_on_email(client, admin_id, ds):
    r = await client.post("/api/v1/aggregate", headers=auth(admin_id), json={
        "dataset_id": ds, "sheet": "data", "group_by": ["email"],
        "aggregations": [{"column": "amount", "function": "sum", "alias": "total"}]})
    assert r.status_code == 200, r.text
    return r.json()["result_file"]


async def test_teammate_cannot_read_sensitive_values_from_a_derived_artifact(client, admin_id):
    ds = await _pii_dataset(client, admin_id)
    editor, _ = await create_team_user(client, admin_id, "editor", team_id=DEFAULT_TEAM)
    result_file = await _admin_aggregate_on_email(client, admin_id, ds)

    # The editor has DATASET_READ (same team) but not read_sensitive.
    data = await client.get(f"/api/v1/samples/{result_file}/data", headers=auth(editor))
    assert data.status_code == 403, data.text
    assert data.json().get("code") == "sensitive-data-restricted"
    # The raw-bytes download route is gated the same way.
    dl = await client.get(f"/api/v1/samples/{result_file}", headers=auth(editor))
    assert dl.status_code == 403, dl.text

    # The elevated creator (superuser) still reads it.
    assert (await client.get(f"/api/v1/samples/{result_file}/data",
                             headers=auth(admin_id))).status_code == 200


async def test_non_sensitive_artifact_is_still_readable_by_a_teammate(client, admin_id):
    # No sensitive column declared → the gate must not over-restrict.
    rows = [{"region": "NA", "amount": 10.0}, {"region": "EU", "amount": 20.0}]
    ds = (await upload_inline(client, admin_id, json.dumps(rows)))["dataset_id"]
    editor, _ = await create_team_user(client, admin_id, "editor", team_id=DEFAULT_TEAM)
    r = await client.post("/api/v1/aggregate", headers=auth(admin_id), json={
        "dataset_id": ds, "sheet": "data", "group_by": ["region"],
        "aggregations": [{"column": "amount", "function": "sum", "alias": "total"}]})
    result_file = r.json()["result_file"]
    assert (await client.get(f"/api/v1/samples/{result_file}/data",
                             headers=auth(editor))).status_code == 200


async def test_sample_egress_is_audited(client, admin_id):
    ds = await _pii_dataset(client, admin_id)
    result_file = await _admin_aggregate_on_email(client, admin_id, ds)
    await client.get(f"/api/v1/samples/{result_file}/data", headers=auth(admin_id))
    await client.get(f"/api/v1/samples/{result_file}", headers=auth(admin_id))

    audit = (await client.get("/api/v1/audit?limit=100", headers=auth(admin_id))).json()
    sample_paths = [r["path"] for r in audit["items"] if "/samples/" in r["path"]]
    assert any(p.endswith("/data") for p in sample_paths), sample_paths
    assert any(p.endswith(result_file) for p in sample_paths), sample_paths


async def test_empty_teams_list_reports_a_page_size_the_envelope_allows(client, admin_id):
    uid, team = await create_team_user(client, admin_id, "viewer")
    await client.delete(f"/api/v1/teams/{team}/members/{uid}", headers=auth(admin_id))
    r = await client.get("/api/v1/teams", headers=auth(uid))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 0
    assert body["limit"] >= 1
