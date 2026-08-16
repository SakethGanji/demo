"""Publish contract codes, and chart-axis masking.

- A publish name collision carries its own code (dataset-name-taken) so a "pick
  another name" dialog can branch on it; a name supplied in new_version mode is
  rejected rather than silently dropped.
- A chart whose category/series axis is a masked column that COLLAPSES to one
  token would silently drop groups; it is refused, mirroring the pivot-dimension
  guard. A distinctness-preserving mask (email) still renders.

(Publishing one run to multiple targets — a new dataset and a new version of the
source — is deliberate; see test_library_journeys step 9. Not guarded here.)
"""

from __future__ import annotations

import json

from conftest import auth, rid, upload_inline, poll_status, create_team_user

DEFAULT_TEAM = "00000000-0000-0000-0000-000000000001"


async def _ds(client, uid, rows, team_id=None):
    kw = {"team_id": team_id} if team_id else {}
    body = await upload_inline(client, uid, json.dumps(rows), **kw)
    await poll_status(client, uid, body["version_id"])
    return body["dataset_id"]


async def _aggregate_def(client, uid, ds, group_by, col="amount", alias="s"):
    r = await client.post(f"/api/v1/datasets/{ds}/analytics", headers=auth(uid), json={
        "name": f"a-{rid()}", "kind": "aggregate",
        "params": {"group_by": group_by,
                   "aggregations": [{"column": col, "function": "sum", "alias": alias}]}})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _run(client, uid, ds, defn):
    return (await client.post(f"/api/v1/datasets/{ds}/analytics/{defn}/run",
                              headers=auth(uid))).json()["id"]


async def test_publish_name_collision_has_its_own_code(client, admin_id):
    h = auth(admin_id)
    ds = await _ds(client, admin_id, [{"region": "EU", "amount": 1}])
    defn = await _aggregate_def(client, admin_id, ds, ["region"])
    name = f"dup-{rid()}"
    await client.post(f"/api/v1/datasets/{ds}/analytics/runs/{await _run(client, admin_id, ds, defn)}/publish",
                      headers=h, json={"mode": "new_dataset", "name": name})
    coll = await client.post(
        f"/api/v1/datasets/{ds}/analytics/runs/{await _run(client, admin_id, ds, defn)}/publish",
        headers=h, json={"mode": "new_dataset", "name": name})
    assert coll.status_code == 409, coll.text
    assert coll.json()["code"] == "dataset-name-taken"


async def test_new_version_publish_rejects_a_name(client, admin_id):
    h = auth(admin_id)
    ds = await _ds(client, admin_id, [{"region": "EU", "amount": 1}])
    defn = await _aggregate_def(client, admin_id, ds, ["region"])
    run_id = await _run(client, admin_id, ds, defn)
    r = await client.post(f"/api/v1/datasets/{ds}/analytics/runs/{run_id}/publish",
                          headers=h, json={"mode": "new_version", "name": "nope"})
    assert r.status_code == 422, r.text


async def test_chart_with_a_collapsing_masked_axis_is_refused(client, admin_id):
    editor, team = await create_team_user(client, admin_id, "editor")
    he = auth(editor)
    ds = await _ds(client, editor, [{"dept": "cardiology", "cost": 100},
                                    {"dept": "oncology", "cost": 200},
                                    {"dept": "neurology", "cost": 300}], team_id=team)
    # 'dept' is sensitive with no semantic_type → masks every value to "***".
    await client.put(f"/api/v1/datasets/{ds}/sheet-metadata/data/columns/dept",
                     headers=auth(admin_id), json={"sensitivity": "confidential"})
    defn = await _aggregate_def(client, editor, ds, ["dept"], col="cost", alias="cost_sum")
    c = await client.post(f"/api/v1/datasets/{ds}/charts", headers=he, json={
        "name": f"c-{rid()}", "chart_type": "bar", "definition_id": defn,
        "config": {"x_field": "dept", "y_fields": ["cost_sum"]}})
    chart = c.json()["id"]

    # The non-elevated caller is refused rather than shown a data-dropping chart.
    masked = await client.post(f"/api/v1/datasets/{ds}/charts/{chart}/render", headers=he)
    assert masked.status_code == 403, masked.text
    assert masked.json()["code"] == "sensitive-data-restricted"
    assert "cardiology" not in masked.text

    # The elevated caller still sees all three real groups.
    truth = await client.post(f"/api/v1/datasets/{ds}/charts/{chart}/render", headers=auth(admin_id))
    assert truth.status_code == 200, truth.text
    assert len(truth.json()["categories"]) == 3
