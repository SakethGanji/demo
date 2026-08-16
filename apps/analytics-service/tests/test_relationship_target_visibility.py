"""A relationship must not reveal a dataset the reader cannot open.

A relationship row carries the TARGET side's dataset id, logical sheet id,
current sheet key and column name. The join routes have always checked both
endpoints for exactly this reason (``_authorized_relationship``), but the plain
read routes checked only the owning dataset — so anyone with read on the left
dataset learned that a particular other team holds a dataset, what its sheet is
called, and which column is the key, purely by listing edges. Cross-team edges
arise the normal way: a superuser (or someone with access to both at the time)
links them, and later a plain member of the left team lists relationships.

RBAC needs real teams and memberships, so this is an integration test.
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


async def _cross_team_edge(client, admin_id, tmp_path):
    """(left dataset in the default team, right dataset in another, the edge)."""
    left = await _crm(client, admin_id, tmp_path, "left.xlsx")
    _outsider, other_team = await create_team_user(client, admin_id, "admin")
    right = await _crm(client, admin_id, tmp_path, "right.xlsx", team_id=other_team)

    r = await client.post(f"/api/v1/datasets/{left}/relationships",
                          headers=auth(admin_id),
                          json={"from_sheet": "Orders", "from_column": "customer_id",
                                "to_dataset_id": right, "to_sheet": "Customers",
                                "to_column": "customer_id"})
    assert r.status_code == 201, r.text
    return left, right, r.json()


async def test_a_reader_of_only_the_owning_dataset_never_sees_the_target_side(
        client, admin_id, tmp_path):
    left, right, edge = await _cross_team_edge(client, admin_id, tmp_path)
    viewer, _ = await create_team_user(client, admin_id, "viewer",
                                       team_id=DEFAULT_TEAM)
    vh = auth(viewer)

    listing = await client.get(f"/api/v1/datasets/{left}/relationships", headers=vh)
    assert listing.status_code == 200, listing.text
    body = listing.text
    assert right not in body, "the target dataset's id leaked into the listing"
    assert edge["id"] not in body

    # ...and `total` describes the page the caller actually got.
    assert listing.json()["total"] == len(listing.json()["items"]) == 0

    single = await client.get(
        f"/api/v1/datasets/{left}/relationships/{edge['id']}", headers=vh)
    assert single.status_code == 404
    assert right not in single.text     # not even in the error detail


async def test_reviewing_an_edge_you_cannot_see_both_sides_of_is_a_404(
        client, admin_id, tmp_path):
    """Confirm/reject return the whole row, so they leak the same way a GET does."""
    left, right, edge = await _cross_team_edge(client, admin_id, tmp_path)
    editor, _ = await create_team_user(client, admin_id, "editor",
                                       team_id=DEFAULT_TEAM)
    eh = auth(editor)
    base = f"/api/v1/datasets/{left}/relationships/{edge['id']}"

    for verb in ("confirm", "reject"):
        r = await client.post(f"{base}/{verb}", headers=eh)
        assert r.status_code == 404, (verb, r.text)
        assert right not in r.text


async def test_same_dataset_edges_are_unaffected_for_an_ordinary_member(
        client, admin_id, tmp_path):
    """The filter must not hide the ordinary within-workbook relationships."""
    ds = await _crm(client, admin_id, tmp_path, "crm.xlsx")
    suggested = (await client.post(
        f"/api/v1/datasets/{ds}/relationships/suggest",
        headers=auth(admin_id))).json()["relationships"]
    assert suggested

    viewer, _ = await create_team_user(client, admin_id, "viewer",
                                       team_id=DEFAULT_TEAM)
    vh = auth(viewer)

    listing = await client.get(f"/api/v1/datasets/{ds}/relationships", headers=vh)
    assert listing.json()["total"] == len(suggested)
    assert (await client.get(f"/api/v1/datasets/{ds}/relationships/"
                             f"{suggested[0]['id']}", headers=vh)).status_code == 200


async def test_someone_with_access_to_both_sides_still_sees_the_edge(
        client, admin_id, tmp_path):
    """The rule is 'can you read the target', not 'is it cross-dataset'."""
    left, right, edge = await _cross_team_edge(client, admin_id, tmp_path)
    h = auth(admin_id)      # the seeded superuser can read both

    listing = await client.get(f"/api/v1/datasets/{left}/relationships", headers=h)
    assert listing.json()["total"] == 1
    assert listing.json()["items"][0]["to_dataset_id"] == right
    assert (await client.get(f"/api/v1/datasets/{left}/relationships/{edge['id']}",
                             headers=h)).status_code == 200
