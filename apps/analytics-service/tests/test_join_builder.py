"""Wave 5 §23 — the guided join builder: pre-flight warnings, execute, publish.

The contract under test: a join is driven only by a CONFIRMED relationship, its
behaviour is measured before it runs, and publishing records BOTH parents.
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


async def crm_dataset(client, admin_id, tmp_path, name, team_id=DEFAULT_TEAM):
    path = tmp_path / name
    make_crm_workbook(path)
    body = await upload_file(client, admin_id, path, name=name,
                             content_type=XLSX_MIME, team_id=team_id)
    return body["dataset_id"]


async def declare(client, admin_id, left_ds, right_ds=None, confirmed=True):
    """Orders.customer_id → Customers.customer_id, ready to drive a join."""
    payload = {"from_sheet": "Orders", "from_column": "customer_id",
               "to_sheet": "Customers", "to_column": "customer_id",
               "confirmed": confirmed}
    if right_ds:
        payload["to_dataset_id"] = right_ds
    r = await client.post(f"/api/v1/datasets/{left_ds}/relationships",
                          headers=auth(admin_id), json=payload)
    assert r.status_code == 201, r.text
    return r.json()


# --- pre-flight ---------------------------------------------------------------

async def test_preview_measures_the_join_before_running_it(client, admin_id, tmp_path):
    ds = await crm_dataset(client, admin_id, tmp_path, "crm.xlsx")
    h = auth(admin_id)
    rel = await declare(client, admin_id, ds)

    r = await client.post("/api/v1/joins/preview", headers=h,
                          json={"relationship_id": rel["id"], "how": "inner"})
    assert r.status_code == 200, r.text
    body = r.json()
    w = body["warnings"]

    assert w["left_rows"] == 3 and w["right_rows"] == 3
    # Customer 1 has two orders, so the child side repeats its key...
    assert w["left_duplicate_keys"] == 1
    # ...but the parent key is unique, so this is 1:N, not N:N.
    assert w["right_duplicate_keys"] == 0
    assert w["many_to_many"] is False
    assert w["estimated_output_rows"] == 3
    assert w["row_expansion_factor"] == 1.0
    assert w["unmatched_left_pct"] == 0.0
    # Customer 3 never ordered — a third of the parent rows find no partner.
    assert w["unmatched_right_pct"] == 33.33
    # The join key is not a collision; nothing else shares a name.
    assert w["column_collisions"] == []

    assert body["output_columns"] == ["order_id", "customer_id", "total", "tier"]
    assert len(body["preview"]) == 3
    assert {row["tier"] for row in body["preview"]} == {"gold", "silver"}


async def test_preview_persists_nothing(client, admin_id, tmp_path):
    ds = await crm_dataset(client, admin_id, tmp_path, "crm.xlsx")
    h = auth(admin_id)
    rel = await declare(client, admin_id, ds)

    # The samples AREA is shared storage that outlives a test's DB truncation,
    # so compare before/after rather than looking for join_* files globally.
    before = (await client.get("/api/v1/samples", headers=h)).json()["total"]
    await client.post("/api/v1/joins/preview", headers=h,
                      json={"relationship_id": rel["id"]})
    after = (await client.get("/api/v1/samples", headers=h)).json()["total"]

    assert after == before                    # no artifact was written
    listing = await client.get(f"/api/v1/datasets/{ds}/analytics", headers=h)
    assert listing.json()["total"] == 0       # and no definition or run


async def test_a_left_join_reports_the_larger_output(client, admin_id, tmp_path):
    ds = await crm_dataset(client, admin_id, tmp_path, "crm.xlsx")
    h = auth(admin_id)
    # Reverse the direction so the parent (Customers) is the left side: a LEFT
    # join then keeps the customer who never ordered.
    r = await client.post(f"/api/v1/datasets/{ds}/relationships", headers=h,
                          json={"from_sheet": "Customers", "from_column": "customer_id",
                                "to_sheet": "Orders", "to_column": "customer_id"})
    rel = r.json()

    inner = (await client.post("/api/v1/joins/preview", headers=h,
                               json={"relationship_id": rel["id"], "how": "inner"})).json()
    left = (await client.post("/api/v1/joins/preview", headers=h,
                              json={"relationship_id": rel["id"], "how": "left"})).json()
    assert inner["warnings"]["estimated_output_rows"] == 3
    assert left["warnings"]["estimated_output_rows"] == 4   # + the orderless customer
    assert left["warnings"]["many_to_many"] is False


# --- the confirmation gate ----------------------------------------------------

async def test_an_unconfirmed_relationship_cannot_drive_a_join(
        client, admin_id, tmp_path):
    ds = await crm_dataset(client, admin_id, tmp_path, "crm.xlsx")
    h = auth(admin_id)
    rel = await declare(client, admin_id, ds, confirmed=False)
    assert rel["status"] == "suggested"

    # Previewing an unreviewed edge is fine — that is how you review it.
    assert (await client.post("/api/v1/joins/preview", headers=h,
                              json={"relationship_id": rel["id"]})).status_code == 200

    r = await client.post("/api/v1/joins/execute", headers=h,
                          json={"relationship_id": rel["id"]})
    assert r.status_code == 409
    assert r.json()["code"] == "relationship-not-confirmed"

    # Confirming unblocks it.
    await client.post(f"/api/v1/datasets/{ds}/relationships/{rel['id']}/confirm",
                      headers=h)
    assert (await client.post("/api/v1/joins/execute", headers=h,
                              json={"relationship_id": rel["id"]})).status_code == 200


# --- execution ----------------------------------------------------------------

async def test_execute_materializes_an_authorized_artifact(client, admin_id, tmp_path):
    ds = await crm_dataset(client, admin_id, tmp_path, "crm.xlsx")
    h = auth(admin_id)
    rel = await declare(client, admin_id, ds)

    r = await client.post("/api/v1/joins/execute", headers=h,
                          json={"relationship_id": rel["id"], "how": "inner"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["row_count"] == 3
    assert body["warnings"]["estimated_output_rows"] == 3   # the estimate held

    sample = await client.get(f"/api/v1/samples/{body['sample_file']}/data", headers=h)
    assert sample.status_code == 200
    rows = sample.json()["data"] if isinstance(sample.json(), dict) else sample.json()
    assert len(rows) == 3
    assert all("tier" in row for row in rows)

    # The run is recorded under a reusable `join` definition.
    defs = await client.get(f"/api/v1/datasets/{ds}/analytics", headers=h)
    assert defs.json()["total"] == 1
    assert defs.json()["items"][0]["kind"] == "join"


async def test_repeated_executions_share_one_definition(client, admin_id, tmp_path):
    ds = await crm_dataset(client, admin_id, tmp_path, "crm.xlsx")
    h = auth(admin_id)
    rel = await declare(client, admin_id, ds)
    for _ in range(2):
        await client.post("/api/v1/joins/execute", headers=h,
                          json={"relationship_id": rel["id"]})

    defs = (await client.get(f"/api/v1/datasets/{ds}/analytics", headers=h)).json()
    assert defs["total"] == 1        # no litter in the library
    runs = await client.get(
        f"/api/v1/datasets/{ds}/analytics/{defs['items'][0]['id']}/runs", headers=h)
    assert runs.json()["total"] == 2


async def test_select_columns_narrows_the_output(client, admin_id, tmp_path):
    ds = await crm_dataset(client, admin_id, tmp_path, "crm.xlsx")
    h = auth(admin_id)
    rel = await declare(client, admin_id, ds)

    r = await client.post("/api/v1/joins/execute", headers=h,
                          json={"relationship_id": rel["id"],
                                "select_columns": ["order_id", "tier"]})
    assert r.status_code == 200, r.text
    assert r.json()["output_columns"] == ["order_id", "tier"]


async def test_an_unknown_output_column_is_rejected(client, admin_id, tmp_path):
    ds = await crm_dataset(client, admin_id, tmp_path, "crm.xlsx")
    h = auth(admin_id)
    rel = await declare(client, admin_id, ds)
    r = await client.post("/api/v1/joins/preview", headers=h,
                          json={"relationship_id": rel["id"],
                                "select_columns": ["ghost"]})
    assert r.status_code == 400
    assert r.json()["code"] == "unknown-column"


# --- cross-dataset ------------------------------------------------------------

async def test_a_cross_dataset_join_works_within_a_team(client, admin_id, tmp_path):
    left = await crm_dataset(client, admin_id, tmp_path, "left.xlsx")
    right = await crm_dataset(client, admin_id, tmp_path, "right.xlsx")
    h = auth(admin_id)
    rel = await declare(client, admin_id, left, right_ds=right)
    assert rel["to_dataset_id"] == right

    r = await client.post("/api/v1/joins/execute", headers=h,
                          json={"relationship_id": rel["id"]})
    assert r.status_code == 200, r.text
    assert r.json()["row_count"] == 3


# --- publishing ---------------------------------------------------------------

async def test_publish_records_both_parents(client, admin_id, tmp_path):
    left = await crm_dataset(client, admin_id, tmp_path, "left.xlsx")
    right = await crm_dataset(client, admin_id, tmp_path, "right.xlsx")
    h = auth(admin_id)
    rel = await declare(client, admin_id, left, right_ds=right)
    run_id = (await client.post("/api/v1/joins/execute", headers=h,
                                json={"relationship_id": rel["id"]})).json()["run_id"]

    r = await client.post(f"/api/v1/joins/{run_id}/publish", headers=h,
                          json={"mode": "new_dataset", "name": f"joined-{run_id[:8]}"})
    assert r.status_code == 200, r.text
    published = r.json()

    lineage = (await client.get(
        f"/api/v1/datasets/{published['dataset_id']}/lineage", headers=h)).json()
    parents = lineage["parents"]
    # A join has two sources, and both are traceable.
    assert len(parents) == 2
    assert {p["relation"] for p in parents} == {"joined_from"}
    assert {p["parent_dataset_id"] for p in parents} == {left, right}

    preview = await client.get(
        f"/api/v1/datasets/{published['dataset_id']}/versions/1/preview", headers=h)
    assert preview.json()["total"] == 3


async def test_publishing_an_unknown_run_is_a_404(client, admin_id, tmp_path):
    ds = await crm_dataset(client, admin_id, tmp_path, "crm.xlsx")
    h = auth(admin_id)
    # A non-join analytics run is not publishable through the joins route.
    r = await client.post(f"/api/v1/joins/{ds}/publish", headers=h,
                          json={"mode": "new_dataset"})
    assert r.status_code == 404


# --- RBAC ---------------------------------------------------------------------

async def test_joins_are_invisible_across_teams(client, admin_id, tmp_path):
    ds = await crm_dataset(client, admin_id, tmp_path, "crm.xlsx")
    h = auth(admin_id)
    rel = await declare(client, admin_id, ds)
    run_id = (await client.post("/api/v1/joins/execute", headers=h,
                                json={"relationship_id": rel["id"]})).json()["run_id"]

    outsider, _ = await create_team_user(client, admin_id, "admin")
    oh = auth(outsider)
    assert (await client.post("/api/v1/joins/preview", headers=oh,
                              json={"relationship_id": rel["id"]})).status_code == 404
    assert (await client.post("/api/v1/joins/execute", headers=oh,
                              json={"relationship_id": rel["id"]})).status_code == 404
    assert (await client.post(f"/api/v1/joins/{run_id}/publish", headers=oh,
                              json={"mode": "new_dataset"})).status_code == 404


async def test_reading_a_join_never_leaks_the_other_side(client, admin_id, tmp_path):
    """A relationship must not become a side channel into another team's data."""
    left = await crm_dataset(client, admin_id, tmp_path, "left.xlsx")
    editor, other_team = await create_team_user(client, admin_id, "editor")
    right = await crm_dataset(client, admin_id, tmp_path, "right.xlsx",
                              team_id=other_team)
    h = auth(admin_id)
    rel = await declare(client, admin_id, left, right_ds=right)

    # Grant the editor access to the LEFT dataset's team only.
    await create_team_user(client, admin_id, "editor", team_id=DEFAULT_TEAM)
    viewer, _ = await create_team_user(client, admin_id, "viewer", team_id=DEFAULT_TEAM)

    # The left side alone is not enough to join into a dataset they can't read.
    r = await client.post("/api/v1/joins/preview", headers=auth(viewer),
                          json={"relationship_id": rel["id"]})
    assert r.status_code == 404


async def test_publishing_requires_write_on_the_target(client, admin_id, tmp_path):
    ds = await crm_dataset(client, admin_id, tmp_path, "crm.xlsx")
    h = auth(admin_id)
    rel = await declare(client, admin_id, ds)
    run_id = (await client.post("/api/v1/joins/execute", headers=h,
                                json={"relationship_id": rel["id"]})).json()["run_id"]

    viewer, _ = await create_team_user(client, admin_id, "viewer", team_id=DEFAULT_TEAM)
    r = await client.post(f"/api/v1/joins/{run_id}/publish", headers=auth(viewer),
                          json={"mode": "new_dataset", "name": "nope"})
    assert r.status_code == 403
