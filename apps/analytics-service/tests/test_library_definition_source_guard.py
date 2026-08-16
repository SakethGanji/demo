"""A saved definition reads its own dataset — never a path it names itself.

`POST /datasets/{id}/analytics` stores `params` as free-form JSON and checks
only DATASET_WRITE on the dataset the definition hangs off. The underlying
request models accept `file_path`, and every executor prefers `file_path` over
`dataset_id`, and `load_data` opens that path straight off the server
filesystem. The direct `/sample` and `/aggregate` endpoints gate `file_path`
behind a platform-administrator check for exactly this reason ("otherwise any
member could point it at another team's parquet files"); the library run and
chart-render paths never applied it.

So without this guard, an editor on any dataset they own could save a definition
whose params name another team's parquet — or any readable file on the box — run
it, read the rows back inline, and register the output as an artifact in their
OWN team. These tests go through the HTTP routes because the point is that both
the durable run path and the read-only chart-render path are covered, and that
the file's contents never reach the response.
"""

from __future__ import annotations

import json

from conftest import auth, upload_inline

OWN_ROWS = [{"region": "EU", "amount": 100.0}, {"region": "US", "amount": 50.0}]
SECRET_ROWS = [{"region": "PAYROLL", "amount": 999999.0}]


def _secret_csv(tmp_path):
    path = tmp_path / "other_teams_export.csv"
    path.write_text("region,amount\nPAYROLL,999999.0\n")
    return str(path)


async def _definition(client, admin_id, ds, name, params):
    return await client.post(f"/api/v1/datasets/{ds}/analytics", headers=auth(admin_id),
                             json={"name": name, "kind": "aggregate", "params": params})


AGG = {"group_by": ["region"],
       "aggregations": [{"column": "amount", "function": "sum"}]}


async def test_running_a_definition_whose_params_name_a_file_path_is_refused(
        client, admin_id, tmp_path):
    ds = (await upload_inline(client, admin_id, json.dumps(OWN_ROWS)))["dataset_id"]
    created = await _definition(client, admin_id, ds, "poisoned",
                                {**AGG, "file_path": _secret_csv(tmp_path)})
    assert created.status_code == 201, created.text

    r = await client.post(
        f"/api/v1/datasets/{ds}/analytics/{created.json()['id']}/run",
        headers=auth(admin_id))
    assert r.status_code == 400, r.text
    body = r.json()
    assert body["code"] == "definition-source-not-allowed"
    assert body["params"] == ["file_path"]
    assert "PAYROLL" not in r.text


async def test_a_refused_definition_leaves_no_run_behind(client, admin_id, tmp_path):
    """The guard fires before the job and analytics_runs rows are opened, so a
    definition that never started must not litter the run history."""
    ds = (await upload_inline(client, admin_id, json.dumps(OWN_ROWS)))["dataset_id"]
    definition = (await _definition(client, admin_id, ds, "poisoned",
                                    {**AGG, "file_path": _secret_csv(tmp_path)})).json()

    await client.post(f"/api/v1/datasets/{ds}/analytics/{definition['id']}/run",
                      headers=auth(admin_id))

    runs = await client.get(f"/api/v1/datasets/{ds}/analytics/{definition['id']}/runs",
                            headers=auth(admin_id))
    assert runs.json()["total"] == 0


async def test_rendering_a_chart_over_such_a_definition_is_refused_too(
        client, admin_id, tmp_path):
    """Chart render only needs DATASET_READ, and it returns the rows as chart
    categories — the cheapest way to read the file back out."""
    ds = (await upload_inline(client, admin_id, json.dumps(OWN_ROWS)))["dataset_id"]
    h = auth(admin_id)
    definition = (await _definition(client, admin_id, ds, "poisoned",
                                    {**AGG, "file_path": _secret_csv(tmp_path)})).json()
    chart = (await client.post(f"/api/v1/datasets/{ds}/charts", headers=h, json={
        "name": "leak", "chart_type": "bar", "definition_id": definition["id"],
        "config": {"x_field": "region", "y_fields": ["amount_sum"]}})).json()

    r = await client.post(f"/api/v1/datasets/{ds}/charts/{chart['id']}/render", headers=h)
    assert r.status_code == 400, r.text
    assert r.json()["code"] == "definition-source-not-allowed"
    assert "PAYROLL" not in r.text


async def test_inline_data_in_params_is_refused(client, admin_id):
    """`data` outranks `dataset_id` the same way `file_path` does, so a chart
    over such a definition plots numbers that are in no dataset at all."""
    ds = (await upload_inline(client, admin_id, json.dumps(OWN_ROWS)))["dataset_id"]
    definition = (await _definition(client, admin_id, ds, "fabricated",
                                    {**AGG, "data": SECRET_ROWS})).json()

    r = await client.post(
        f"/api/v1/datasets/{ds}/analytics/{definition['id']}/run", headers=auth(admin_id))
    assert r.status_code == 400, r.text
    assert r.json()["code"] == "definition-source-not-allowed"


async def test_an_ordinary_definition_still_runs(client, admin_id):
    """The guard must not cost the normal path anything."""
    ds = (await upload_inline(client, admin_id, json.dumps(OWN_ROWS)))["dataset_id"]
    definition = (await _definition(client, admin_id, ds, "clean", AGG)).json()

    r = await client.post(
        f"/api/v1/datasets/{ds}/analytics/{definition['id']}/run", headers=auth(admin_id))
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "completed"


async def test_stored_version_keys_do_not_beat_the_version_selector(client, admin_id):
    """A definition pinned to `current` that also carries `version_number: 1` used
    to run against v1 while recording v2 as the run's version — so the numbers on
    screen, the published parent and the lineage chain disagreed about which
    version produced them."""
    ds = (await upload_inline(client, admin_id, json.dumps(OWN_ROWS)))["dataset_id"]
    v2 = await upload_inline(client, admin_id,
                             json.dumps(OWN_ROWS + [{"region": "APAC", "amount": 7.0}]),
                             dataset_id=ds)
    h = auth(admin_id)
    definition = (await _definition(client, admin_id, ds, "pinned-by-params",
                                    {**AGG, "version_number": 1})).json()

    run = (await client.post(
        f"/api/v1/datasets/{ds}/analytics/{definition['id']}/run", headers=h)).json()
    assert run["status"] == "completed", run
    # v2 has three regions; v1 has two. The selector says "current" = v2.
    assert run["result_summary"]["group_count"] == 3
    assert run["dataset_version_id"] == v2["version_id"]
