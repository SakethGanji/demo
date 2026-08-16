"""Wave 5 §22 — relationships: seeding, statistical discovery, review, RBAC.

The CRM workbook (Customers 1:N Orders, plus a hidden Scratch sheet) is the
fixture: Orders.customer_id references Customers.customer_id, which is exactly
the shape both the FK seeder and the statistical suggester should find.
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


async def crm_dataset(client, admin_id, tmp_path, name="crm.xlsx"):
    path = tmp_path / name
    make_crm_workbook(path)
    body = await upload_file(client, admin_id, path, name=name,
                             content_type=XLSX_MIME)
    return body["dataset_id"]


async def add_fk_rule(client, admin_id, ds):
    r = await client.post(f"/api/v1/datasets/{ds}/rules", headers=auth(admin_id),
                          json={"name": "orders-customer-fk",
                                "rule_type": "foreign_key",
                                "sheet_selector": "Orders",
                                "column_selector": "customer_id",
                                "parameters": {"ref_sheet": "Customers",
                                               "ref_column": "customer_id"}})
    assert r.status_code == 201, r.text
    return r.json()


# --- seeding from quality rules -----------------------------------------------

async def test_seed_projects_foreign_key_rules_onto_edges(client, admin_id, tmp_path):
    ds = await crm_dataset(client, admin_id, tmp_path)
    h = auth(admin_id)
    rule = await add_fk_rule(client, admin_id, ds)

    r = await client.post(f"/api/v1/datasets/{ds}/relationships/seed", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["created"] == 1
    edge = r.json()["relationships"][0]

    # The rule states the direction: it lives on the child and names the parent.
    assert edge["from_sheet"] == "orders" and edge["from_column"] == "customer_id"
    assert edge["to_sheet"] == "customers" and edge["to_column"] == "customer_id"
    assert edge["method"] == "fk_rule" and edge["status"] == "suggested"
    assert edge["confidence"] == 1.0
    assert edge["evidence"]["rule_name"] == rule["name"]
    assert edge["to_dataset_id"] == ds       # within-workbook edges self-reference


async def test_seeding_is_idempotent(client, admin_id, tmp_path):
    ds = await crm_dataset(client, admin_id, tmp_path)
    h = auth(admin_id)
    await add_fk_rule(client, admin_id, ds)

    await client.post(f"/api/v1/datasets/{ds}/relationships/seed", headers=h)
    await client.post(f"/api/v1/datasets/{ds}/relationships/seed", headers=h)

    listing = await client.get(f"/api/v1/datasets/{ds}/relationships", headers=h)
    assert listing.json()["total"] == 1     # the UNIQUE tuple absorbed the re-run


async def test_reseeding_never_clobbers_a_human_verdict(client, admin_id, tmp_path):
    ds = await crm_dataset(client, admin_id, tmp_path)
    h = auth(admin_id)
    await add_fk_rule(client, admin_id, ds)
    edge = (await client.post(f"/api/v1/datasets/{ds}/relationships/seed",
                              headers=h)).json()["relationships"][0]
    await client.post(
        f"/api/v1/datasets/{ds}/relationships/{edge['id']}/reject", headers=h)

    await client.post(f"/api/v1/datasets/{ds}/relationships/seed", headers=h)

    r = await client.get(f"/api/v1/datasets/{ds}/relationships/{edge['id']}", headers=h)
    assert r.json()["status"] == "rejected"   # a rejected edge stays rejected


# --- statistical discovery ----------------------------------------------------

async def test_discovery_finds_the_undeclared_relationship(client, admin_id, tmp_path):
    ds = await crm_dataset(client, admin_id, tmp_path)
    h = auth(admin_id)

    r = await client.post(f"/api/v1/datasets/{ds}/relationships/suggest", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["job_id"]
    assert r.json()["suggested"] >= 1

    edges = r.json()["relationships"]
    edge = next(e for e in edges if e["from_column"] == "customer_id")
    # Orientation: Orders is the child, Customers (unique key) is the parent.
    assert edge["from_sheet"] == "orders"
    assert edge["to_sheet"] == "customers"
    assert edge["method"] == "statistical" and edge["status"] == "suggested"
    assert edge["confidence"] >= 0.6

    # The evidence explains WHY, so a reviewer isn't asked to trust a number.
    evidence = edge["evidence"]
    assert evidence["coverage"] == 1.0          # every order's customer exists
    assert evidence["target_uniqueness"] == 1.0  # Customers.customer_id is a key
    assert evidence["name_score"] > 0


async def test_discovery_runs_as_a_job_the_worker_can_claim(client, admin_id, tmp_path):
    from app.shared import worker

    ds = await crm_dataset(client, admin_id, tmp_path)
    h = auth(admin_id)

    r = await client.post(
        f"/api/v1/datasets/{ds}/relationships/suggest?sync=false", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["suggested"] == 0        # nothing has run yet

    assert await worker.run_pending_jobs_once() >= 1

    listing = await client.get(f"/api/v1/datasets/{ds}/relationships", headers=h)
    assert listing.json()["total"] >= 1


async def test_discovery_does_not_resurrect_rejected_edges(client, admin_id, tmp_path):
    ds = await crm_dataset(client, admin_id, tmp_path)
    h = auth(admin_id)
    edge = (await client.post(f"/api/v1/datasets/{ds}/relationships/suggest",
                              headers=h)).json()["relationships"][0]
    await client.post(
        f"/api/v1/datasets/{ds}/relationships/{edge['id']}/reject", headers=h)

    await client.post(f"/api/v1/datasets/{ds}/relationships/suggest", headers=h)

    r = await client.get(f"/api/v1/datasets/{ds}/relationships/{edge['id']}", headers=h)
    assert r.json()["status"] == "rejected"


async def test_a_single_sheet_dataset_has_nothing_to_discover(client, admin_id):
    from conftest import SAMPLE_CSV

    ds = (await upload_file(client, admin_id, SAMPLE_CSV, name="flat.csv"))["dataset_id"]
    r = await client.post(f"/api/v1/datasets/{ds}/relationships/suggest",
                          headers=auth(admin_id))
    assert r.status_code == 200
    assert r.json()["suggested"] == 0 and r.json()["pairs_examined"] == 0


# --- manual declaration + review ---------------------------------------------

async def test_manual_relationships_are_confirmed_on_arrival(client, admin_id, tmp_path):
    ds = await crm_dataset(client, admin_id, tmp_path)
    h = auth(admin_id)

    r = await client.post(f"/api/v1/datasets/{ds}/relationships", headers=h,
                          json={"from_sheet": "Orders", "from_column": "customer_id",
                                "to_sheet": "Customers", "to_column": "customer_id"})
    assert r.status_code == 201, r.text
    assert r.json()["method"] == "manual" and r.json()["status"] == "confirmed"


async def test_manual_relationships_validate_both_columns(client, admin_id, tmp_path):
    ds = await crm_dataset(client, admin_id, tmp_path)
    r = await client.post(f"/api/v1/datasets/{ds}/relationships", headers=auth(admin_id),
                          json={"from_sheet": "Orders", "from_column": "ghost",
                                "to_sheet": "Customers", "to_column": "customer_id"})
    assert r.status_code == 400
    assert r.json()["code"] == "unknown-column"


async def test_review_transitions_and_the_illegal_one(client, admin_id, tmp_path):
    ds = await crm_dataset(client, admin_id, tmp_path)
    h = auth(admin_id)
    await add_fk_rule(client, admin_id, ds)
    edge = (await client.post(f"/api/v1/datasets/{ds}/relationships/seed",
                              headers=h)).json()["relationships"][0]
    base = f"/api/v1/datasets/{ds}/relationships/{edge['id']}"

    r = await client.post(f"{base}/confirm", headers=h)
    assert r.status_code == 200 and r.json()["status"] == "confirmed"
    assert r.json()["reviewed_by"] == admin_id

    # Confirming an already-confirmed edge is not a legal transition.
    r = await client.post(f"{base}/confirm", headers=h)
    assert r.status_code == 409
    assert r.json()["code"] == "invalid-relationship-transition"

    # Changing your mind is fine.
    assert (await client.post(f"{base}/reject", headers=h)).status_code == 200


async def test_filtering_by_status(client, admin_id, tmp_path):
    ds = await crm_dataset(client, admin_id, tmp_path)
    h = auth(admin_id)
    edge = (await client.post(f"/api/v1/datasets/{ds}/relationships/suggest",
                              headers=h)).json()["relationships"][0]
    await client.post(
        f"/api/v1/datasets/{ds}/relationships/{edge['id']}/confirm", headers=h)

    r = await client.get(f"/api/v1/datasets/{ds}/relationships?status=confirmed",
                         headers=h)
    assert r.json()["total"] == 1
    r = await client.get(f"/api/v1/datasets/{ds}/relationships?status=rejected",
                         headers=h)
    assert r.json()["total"] == 0


async def test_delete_removes_the_edge(client, admin_id, tmp_path):
    ds = await crm_dataset(client, admin_id, tmp_path)
    h = auth(admin_id)
    edge = (await client.post(f"/api/v1/datasets/{ds}/relationships/suggest",
                              headers=h)).json()["relationships"][0]
    base = f"/api/v1/datasets/{ds}/relationships/{edge['id']}"
    assert (await client.delete(base, headers=h)).status_code == 204
    assert (await client.get(base, headers=h)).status_code == 404


# --- cross-dataset + RBAC -----------------------------------------------------

async def test_a_cross_dataset_relationship_needs_access_to_both_sides(
        client, admin_id, tmp_path):
    left = await crm_dataset(client, admin_id, tmp_path, name="left.xlsx")
    h = auth(admin_id)

    # A second dataset in ANOTHER team the admin can still see (superuser).
    outsider, other_team = await create_team_user(client, admin_id, "admin")
    right_path = tmp_path / "right.xlsx"
    make_crm_workbook(right_path)
    right = (await upload_file(client, admin_id, right_path, name="right.xlsx",
                               content_type=XLSX_MIME, team_id=other_team))["dataset_id"]

    r = await client.post(f"/api/v1/datasets/{left}/relationships", headers=h,
                          json={"from_sheet": "Orders", "from_column": "customer_id",
                                "to_dataset_id": right, "to_sheet": "Customers",
                                "to_column": "customer_id"})
    assert r.status_code == 201, r.text
    assert r.json()["to_dataset_id"] == right

    # The other team's member cannot reach the owning dataset at all.
    assert (await client.get(f"/api/v1/datasets/{left}/relationships",
                             headers=auth(outsider))).status_code == 404


async def test_another_teams_relationships_are_invisible(client, admin_id, tmp_path):
    ds = await crm_dataset(client, admin_id, tmp_path)
    h = auth(admin_id)
    edge = (await client.post(f"/api/v1/datasets/{ds}/relationships/suggest",
                              headers=h)).json()["relationships"][0]

    outsider, _ = await create_team_user(client, admin_id, "admin")
    oh = auth(outsider)
    base = f"/api/v1/datasets/{ds}/relationships"
    assert (await client.get(base, headers=oh)).status_code == 404
    assert (await client.get(f"{base}/{edge['id']}", headers=oh)).status_code == 404
    assert (await client.post(f"{base}/seed", headers=oh)).status_code == 404
    assert (await client.post(f"{base}/suggest", headers=oh)).status_code == 404
    assert (await client.post(f"{base}/{edge['id']}/confirm",
                              headers=oh)).status_code == 404
    assert (await client.delete(f"{base}/{edge['id']}", headers=oh)).status_code == 404


async def test_viewers_can_read_but_not_review(client, admin_id, tmp_path):
    ds = await crm_dataset(client, admin_id, tmp_path)
    h = auth(admin_id)
    edge = (await client.post(f"/api/v1/datasets/{ds}/relationships/suggest",
                              headers=h)).json()["relationships"][0]

    viewer, _ = await create_team_user(client, admin_id, "viewer", team_id=DEFAULT_TEAM)
    vh = auth(viewer)
    base = f"/api/v1/datasets/{ds}/relationships"

    assert (await client.get(base, headers=vh)).status_code == 200
    assert (await client.get(f"{base}/{edge['id']}", headers=vh)).status_code == 200
    assert (await client.post(f"{base}/seed", headers=vh)).status_code == 403
    assert (await client.post(f"{base}/suggest", headers=vh)).status_code == 403
    assert (await client.post(f"{base}/{edge['id']}/confirm",
                              headers=vh)).status_code == 403
