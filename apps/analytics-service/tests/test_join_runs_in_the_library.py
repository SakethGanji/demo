"""A join run lives in the library, but it may not be RUN or PUBLISHED from there.

`POST /joins/execute` writes an ordinary `analytics_definitions` row with
`kind="join"` against the join's LEFT dataset, and an `analytics_runs` row
against it. Both therefore show up on the generic library routes — `GET
/datasets/{left}/analytics` lists the definition, and `repo.get_run` reports the
run's dataset as the left one, which is exactly what the publish route's
ownership check compares against.

Two things went wrong as a result:

* `POST .../analytics/{def_id}/run` looked the kind up in a dict of the four
  runnable request models and raised a bare `KeyError('join')` outside every
  handler — an opaque 500 with no code, for a Run button the UI is entitled to
  render because the definition is listed like any other.

* `POST .../analytics/runs/{run_id}/publish` accepted the join run and published
  it. That path passes no `extra_lineage`, so a two-parent derivation was
  recorded with ONE parent — the right-hand source vanished from the lineage
  graph. It also consults no relationship, so the right-hand dataset was never
  authorized; permissions are team-scoped and a relationship may cross teams, so
  left-side WRITE alone materialized another team's columns. `POST
  /joins/{run_id}/publish` does both correctly, and must be the only door.
"""

from __future__ import annotations

from conftest import (
    XLSX_MIME,
    auth,
    create_team_user,
    make_crm_workbook,
    upload_file,
)

DEFAULT_TEAM = "00000000-0000-0000-0000-000000000001"


async def _crm(client, admin_id, tmp_path, name, team_id=DEFAULT_TEAM):
    path = tmp_path / name
    make_crm_workbook(path)
    body = await upload_file(client, admin_id, path, name=name,
                             content_type=XLSX_MIME, team_id=team_id)
    return body["dataset_id"]


async def _declare(client, admin_id, left, right=None):
    payload = {"from_sheet": "Orders", "from_column": "customer_id",
               "to_sheet": "Customers", "to_column": "customer_id",
               "confirmed": True}
    if right:
        payload["to_dataset_id"] = right
    r = await client.post(f"/api/v1/datasets/{left}/relationships",
                          headers=auth(admin_id), json=payload)
    assert r.status_code == 201, r.text
    return r.json()


async def _executed_join(client, admin_id, tmp_path):
    """(left dataset, right dataset, join definition id, join run id)."""
    left = await _crm(client, admin_id, tmp_path, "left.xlsx")
    right = await _crm(client, admin_id, tmp_path, "right.xlsx")
    h = auth(admin_id)
    rel = await _declare(client, admin_id, left, right)
    run_id = (await client.post("/api/v1/joins/execute", headers=h,
                                json={"relationship_id": rel["id"]})).json()["run_id"]
    defs = (await client.get(f"/api/v1/datasets/{left}/analytics", headers=h)).json()
    definition = next(d for d in defs["items"] if d["kind"] == "join")
    return left, right, definition["id"], run_id


async def test_running_a_join_definition_from_the_library_is_400_not_500(
        client, admin_id, tmp_path):
    left, _, definition_id, _ = await _executed_join(client, admin_id, tmp_path)

    r = await client.post(f"/api/v1/datasets/{left}/analytics/{definition_id}/run",
                          headers=auth(admin_id))
    assert r.status_code == 400, r.text
    body = r.json()
    assert body["code"] == "kind-not-runnable"
    assert body["kind"] == "join"
    assert "/joins/execute" in body["detail"]


async def test_a_refused_join_run_adds_no_run_row(client, admin_id, tmp_path):
    """The guard fires before the job and run rows open, so the refusal must not
    show up as a failed run in the definition's history."""
    left, _, definition_id, _ = await _executed_join(client, admin_id, tmp_path)
    h = auth(admin_id)
    before = (await client.get(
        f"/api/v1/datasets/{left}/analytics/{definition_id}/runs", headers=h)).json()

    await client.post(f"/api/v1/datasets/{left}/analytics/{definition_id}/run", headers=h)

    after = (await client.get(
        f"/api/v1/datasets/{left}/analytics/{definition_id}/runs", headers=h)).json()
    assert after["total"] == before["total"]


async def test_publishing_a_join_run_through_the_library_route_is_refused(
        client, admin_id, tmp_path):
    left, _, _, run_id = await _executed_join(client, admin_id, tmp_path)

    r = await client.post(f"/api/v1/datasets/{left}/analytics/runs/{run_id}/publish",
                          headers=auth(admin_id), json={"mode": "new_dataset",
                                                        "name": f"lib-{run_id[:8]}"})
    assert r.status_code == 409, r.text
    body = r.json()
    assert body["code"] == "publish-wrong-route"
    assert body["publish_path"] == f"/api/v1/joins/{run_id}/publish"


async def test_the_join_route_still_publishes_both_parents(client, admin_id, tmp_path):
    """Closing the library door must leave the correct door open and complete."""
    left, right, _, run_id = await _executed_join(client, admin_id, tmp_path)
    h = auth(admin_id)

    r = await client.post(f"/api/v1/joins/{run_id}/publish", headers=h,
                          json={"mode": "new_dataset", "name": f"joined-{run_id[:8]}"})
    assert r.status_code == 200, r.text
    published = r.json()["dataset_id"]

    lineage = (await client.get(f"/api/v1/datasets/{published}/lineage", headers=h)).json()
    assert {p["parent_dataset_id"] for p in lineage["parents"]} == {left, right}


async def test_a_left_team_editor_cannot_publish_a_cross_team_join_here(
        client, admin_id, tmp_path):
    """Permissions are team-scoped and a relationship may cross teams. The library
    route checked only the left dataset, so left-side WRITE was enough to
    materialize the right team's columns into a dataset the editor owns."""
    left = await _crm(client, admin_id, tmp_path, "left.xlsx")
    _, other_team = await create_team_user(client, admin_id, "editor")
    right = await _crm(client, admin_id, tmp_path, "right.xlsx", team_id=other_team)
    h = auth(admin_id)
    rel = await _declare(client, admin_id, left, right)
    run_id = (await client.post("/api/v1/joins/execute", headers=h,
                                json={"relationship_id": rel["id"]})).json()["run_id"]

    left_only, _ = await create_team_user(client, admin_id, "editor", team_id=DEFAULT_TEAM)
    r = await client.post(f"/api/v1/datasets/{left}/analytics/runs/{run_id}/publish",
                          headers=auth(left_only),
                          json={"mode": "new_dataset", "name": f"sneak-{run_id[:8]}"})
    assert r.status_code == 409, r.text
    assert r.json()["code"] == "publish-wrong-route"


async def test_an_ordinary_analytics_run_still_publishes_here(client, admin_id, tmp_path):
    """The guard is keyed on the run kind, not on the route."""
    ds = await _crm(client, admin_id, tmp_path, "solo.xlsx")
    h = auth(admin_id)
    definition = (await client.post(f"/api/v1/datasets/{ds}/analytics", headers=h, json={
        "name": "orders-by-customer", "kind": "aggregate", "sheet": "Orders",
        "params": {"group_by": ["customer_id"],
                   "aggregations": [{"column": "total", "function": "sum"}]}})).json()
    run = (await client.post(
        f"/api/v1/datasets/{ds}/analytics/{definition['id']}/run", headers=h)).json()
    assert run.get("status") == "completed", run

    r = await client.post(f"/api/v1/datasets/{ds}/analytics/runs/{run['id']}/publish",
                          headers=h, json={"mode": "new_dataset",
                                           "name": f"agg-{run['id'][:8]}"})
    assert r.status_code == 200, r.text
