"""Guards on the relationship + join surface that only a UI-shaped flow shows.

Three contracts, each of which was wrong in a way no single-route test would
catch — you only see them by driving the screen in order and following the ids
the API handed back:

* executing a join is a WRITE on the owning dataset, not a read;
* deleting an edge that a saved join definition still names is a 409;
* a discovery run must not downgrade the provenance of a declared edge.
"""

from __future__ import annotations

from conftest import (
    DEFAULT_TEAM_ID,
    XLSX_MIME,
    auth,
    create_team_user,
    make_crm_workbook,
    upload_file,
)


async def _crm_dataset(client, admin_id, tmp_path, name="crm.xlsx"):
    path = tmp_path / name
    make_crm_workbook(path)
    body = await upload_file(client, admin_id, path, name=name,
                             content_type=XLSX_MIME)
    return body["dataset_id"]


async def _add_fk_rule(client, admin_id, ds):
    r = await client.post(f"/api/v1/datasets/{ds}/rules", headers=auth(admin_id),
                          json={"name": "orders-customer-fk",
                                "rule_type": "foreign_key",
                                "sheet_selector": "Orders",
                                "column_selector": "customer_id",
                                "parameters": {"ref_sheet": "Customers",
                                               "ref_column": "customer_id"}})
    assert r.status_code == 201, r.text
    return r.json()


async def _confirmed_edge(client, admin_id, ds):
    r = await client.post(f"/api/v1/datasets/{ds}/relationships",
                          headers=auth(admin_id),
                          json={"from_sheet": "Orders",
                                "from_column": "customer_id",
                                "to_sheet": "Customers",
                                "to_column": "customer_id",
                                "confirmed": True})
    assert r.status_code == 201, r.text
    return r.json()


async def test_a_viewer_may_preview_a_join_but_may_not_execute_one(
        client, admin_id, tmp_path):
    """POST /joins/execute writes, so it must demand DATASET_WRITE like every
    other state-changing route on this surface.

    Without this, a read-only member of the owning team can materialize a
    parquet artifact, a job, a run and a permanent `join` analytics definition
    on a dataset they were only ever granted read on — the library screen then
    shows rows a viewer created, and storage grows on a viewer's say-so. The
    preview half must stay open to viewers: it persists nothing, and the whole
    point of the read-only screen is that it can explore.
    """
    h = auth(admin_id)
    ds = await _crm_dataset(client, admin_id, tmp_path)
    edge = await _confirmed_edge(client, admin_id, ds)
    viewer, _ = await create_team_user(client, admin_id, "viewer",
                                       team_id=DEFAULT_TEAM_ID)
    vh = auth(viewer)

    # The read-only half of the wizard still works.
    preview = await client.post("/api/v1/joins/preview", headers=vh,
                                json={"relationship_id": edge["id"]})
    assert preview.status_code == 200, preview.text

    # The button that would persist something is refused — 403, not 404: the
    # viewer is IN the team, so the UI hides the control rather than the route.
    denied = await client.post("/api/v1/joins/execute", headers=vh,
                               json={"relationship_id": edge["id"]})
    assert denied.status_code == 403, denied.text

    # And nothing was written: no definition, and no artifact behind one.
    defs = await client.get(f"/api/v1/datasets/{ds}/analytics", headers=h)
    assert defs.status_code == 200, defs.text
    assert defs.json()["total"] == 0, defs.text

    # An editor on the same dataset is unaffected.
    editor, _ = await create_team_user(client, admin_id, "editor",
                                       team_id=DEFAULT_TEAM_ID)
    allowed = await client.post("/api/v1/joins/execute", headers=auth(editor),
                                json={"relationship_id": edge["id"]})
    assert allowed.status_code == 200, allowed.text
    assert (await client.get(f"/api/v1/datasets/{ds}/analytics",
                             headers=h)).json()["total"] == 1


async def test_deleting_a_relationship_with_a_saved_join_definition_is_refused(
        client, admin_id, tmp_path):
    """DELETE must not strand the `join` definition that POST /joins/execute
    created, and must name the dependents it is refusing over.

    Nothing links the two rows — the definition holds the relationship id
    inside a JSONB `params` blob, so there is no foreign key and nothing
    cascades. Deleting the edge left a library row that still lists, still
    counts towards `total` and still shows its run history, while every button
    on it (re-run, preview, publish) answered 404. `attached.join_definitions`
    is what lets the UI say WHICH saved joins are in the way.
    """
    h = auth(admin_id)
    ds = await _crm_dataset(client, admin_id, tmp_path)
    edge = await _confirmed_edge(client, admin_id, ds)

    executed = await client.post("/api/v1/joins/execute", headers=h,
                                 json={"relationship_id": edge["id"]})
    assert executed.status_code == 200, executed.text

    defs = await client.get(f"/api/v1/datasets/{ds}/analytics", headers=h)
    definition = next(d for d in defs.json()["items"] if d["kind"] == "join")
    assert definition["params"]["relationship_id"] == edge["id"]

    refused = await client.delete(
        f"/api/v1/datasets/{ds}/relationships/{edge['id']}", headers=h)
    assert refused.status_code == 409, refused.text
    body = refused.json()
    assert body["code"] == "relationship-has-dependents", body
    assert [d["id"] for d in body["attached"]["join_definitions"]] == [
        definition["id"]], body
    assert body["attached"]["join_definitions"][0]["name"] == definition["name"]

    # The refusal was total: the edge is untouched and still drives the join.
    still = await client.get(f"/api/v1/datasets/{ds}/relationships/{edge['id']}",
                             headers=h)
    assert still.status_code == 200, still.text
    assert still.json()["status"] == "confirmed"
    assert (await client.post("/api/v1/joins/preview", headers=h,
                              json={"relationship_id": edge["id"]})
            ).status_code == 200

    # Clearing the dependent is the documented way out, and then it deletes.
    dropped = await client.delete(
        f"/api/v1/datasets/{ds}/analytics/{definition['id']}", headers=h)
    assert dropped.status_code == 204, dropped.text
    gone = await client.delete(
        f"/api/v1/datasets/{ds}/relationships/{edge['id']}", headers=h)
    assert gone.status_code == 204, gone.text
    assert (await client.get(f"/api/v1/datasets/{ds}/relationships",
                             headers=h)).json()["total"] == 0

    # A relationship nobody ever joined on still deletes with no ceremony.
    plain = await _confirmed_edge(client, admin_id, ds)
    assert (await client.delete(
        f"/api/v1/datasets/{ds}/relationships/{plain['id']}",
        headers=h)).status_code == 204


async def test_discovery_does_not_downgrade_the_provenance_of_an_fk_seeded_edge(
        client, admin_id, tmp_path):
    """A statistical run must not overwrite the method, confidence and evidence
    of an edge that a foreign_key rule declared.

    The upsert conflict key is the directed (sheet, column) pair with no method
    component, so the seeded edge and the discovered one are the same row.
    Last-writer-wins meant the reviewer's inbox re-labelled a declared edge as
    `statistical`, dropped its confidence from 1.0 to a score (re-sorting the
    inbox mid-review) and deleted `evidence.rule_id`/`rule_name` — the only
    link back to the rule that declared it. The measurement is still worth
    keeping, so it lands under `evidence.statistical` as corroboration.
    """
    h = auth(admin_id)
    ds = await _crm_dataset(client, admin_id, tmp_path)
    rule = await _add_fk_rule(client, admin_id, ds)

    seeded = await client.post(f"/api/v1/datasets/{ds}/relationships/seed", headers=h)
    assert seeded.status_code == 200, seeded.text
    edge = seeded.json()["relationships"][0]
    assert edge["method"] == "fk_rule" and edge["confidence"] == 1.0

    discovered = await client.post(
        f"/api/v1/datasets/{ds}/relationships/suggest", headers=h)
    assert discovered.status_code == 200, discovered.text
    assert discovered.json()["suggested"] >= 1

    # Same row — discovery re-derived the pair the rule already declared.
    same = await client.get(
        f"/api/v1/datasets/{ds}/relationships/{edge['id']}", headers=h)
    assert same.status_code == 200, same.text
    body = same.json()
    assert body["status"] == "suggested"
    assert body["method"] == "fk_rule", "a measurement never outranks a rule"
    assert body["confidence"] == 1.0
    assert body["evidence"]["rule_name"] == rule["name"]
    assert body["evidence"]["rule_id"] == rule["id"]

    # ...and the measurement is kept alongside, not thrown away.
    assert body["evidence"]["statistical"]["coverage"] == 1.0
    assert body["evidence"]["statistical"]["target_uniqueness"] == 1.0

    # An edge discovery found on its own is untouched by any of this.
    others = [e for e in (await client.get(
        f"/api/v1/datasets/{ds}/relationships", headers=h)).json()["items"]
        if e["id"] != edge["id"]]
    for other in others:
        assert other["method"] == "statistical"
        assert "coverage" in other["evidence"]

    # A human declaring the same pair by hand still wins outright.
    declared = await client.post(f"/api/v1/datasets/{ds}/relationships", headers=h,
                                 json={"from_sheet": "Orders",
                                       "from_column": "customer_id",
                                       "to_sheet": "Customers",
                                       "to_column": "customer_id"})
    assert declared.status_code == 201, declared.text
    assert declared.json()["id"] == edge["id"]
    assert declared.json()["method"] == "manual"
    assert declared.json()["evidence"]["declared_by"] == admin_id
