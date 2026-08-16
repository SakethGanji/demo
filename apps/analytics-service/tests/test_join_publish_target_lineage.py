"""Publishing a join must record the parents it ACTUALLY read (§23).

Lineage is the audit answer to "where did these numbers come from". The left
parent is pinned by the run's ``dataset_version_id``, but the right-hand side
had no such pin: publish re-resolved "the current version of the target
dataset". A join that named an explicit ``right_version``, or whose right
dataset gained a version between execute and publish, was therefore published
with a lineage row crediting rows to a version they were never computed from —
a silent wrong answer no later query can detect.

The second contract here is that a join publish keeps BOTH of its sources
traceable whichever side it is published onto, with every parent row naming a
version that really belongs to the dataset it names.
"""

from __future__ import annotations

from conftest import XLSX_MIME, auth, make_crm_workbook, upload_file
from test_join_builder import crm_dataset, declare


async def _executed_join(client, admin_id, tmp_path, **spec):
    left = await crm_dataset(client, admin_id, tmp_path, "left.xlsx")
    right = await crm_dataset(client, admin_id, tmp_path, "right.xlsx")
    rel = await declare(client, admin_id, left, right_ds=right)
    body = {"relationship_id": rel["id"], **spec}
    r = await client.post("/api/v1/joins/execute", headers=auth(admin_id), json=body)
    assert r.status_code == 200, r.text
    return left, right, r.json()["run_id"]


async def _version_ids(client, admin_id, dataset_id) -> set[str]:
    r = await client.get(f"/api/v1/datasets/{dataset_id}/versions",
                         headers=auth(admin_id))
    assert r.status_code == 200, r.text
    return {v["id"] for v in r.json()["items"]}


async def _parents_of(client, admin_id, dataset_id, version_id) -> list[dict]:
    lineage = (await client.get(f"/api/v1/datasets/{dataset_id}/lineage",
                                headers=auth(admin_id))).json()
    return [p for p in lineage["parents"] if p["dataset_version_id"] == version_id]


async def test_publishing_a_join_records_the_right_version_the_join_actually_read(
        client, admin_id, tmp_path):
    """A newer version on the joined side must not be credited with the result.

    Publish used to resolve the right-hand parent as "the current version of
    that dataset". Any upload landing between execute and publish therefore
    became the recorded source of rows it had no part in, and the version that
    really produced them vanished from lineage — the audit trail says the
    figures came from data they were never computed from.
    """
    left, right, run_id = await _executed_join(client, admin_id, tmp_path)
    h = auth(admin_id)

    # The joined side moves on AFTER the join ran, before it is published.
    second = tmp_path / "right-v2.xlsx"
    make_crm_workbook(second)
    await upload_file(client, admin_id, second, name="right.xlsx",
                      content_type=XLSX_MIME, dataset_id=right)
    versions = (await client.get(f"/api/v1/datasets/{right}/versions",
                                 headers=h)).json()
    assert versions["total"] == 2

    published = (await client.post(f"/api/v1/joins/{run_id}/publish", headers=h,
                                   json={"mode": "new_version"})).json()
    parents = await _parents_of(client, admin_id, left, published["version_id"])
    right_parent = next(p for p in parents if p["parent_dataset_id"] == right)
    assert right_parent["parent_version_number"] == 1, parents


async def test_publishing_a_join_of_an_older_version_records_that_older_version(
        client, admin_id, tmp_path):
    """An explicit ``right_version`` is the version lineage must name.

    The join builder lets a caller join against an earlier version on purpose.
    Recording the current one instead makes the published dataset unreproducible:
    re-running the join against the version lineage names gives different rows.
    """
    left = await crm_dataset(client, admin_id, tmp_path, "left.xlsx")
    right = await crm_dataset(client, admin_id, tmp_path, "right.xlsx")
    h = auth(admin_id)
    second = tmp_path / "right-v2.xlsx"
    make_crm_workbook(second)
    await upload_file(client, admin_id, second, name="right.xlsx",
                      content_type=XLSX_MIME, dataset_id=right)
    rel = await declare(client, admin_id, left, right_ds=right)

    run_id = (await client.post(
        "/api/v1/joins/execute", headers=h,
        json={"relationship_id": rel["id"], "right_version": 1})).json()["run_id"]
    published = (await client.post(f"/api/v1/joins/{run_id}/publish", headers=h,
                                   json={"mode": "new_version"})).json()

    parents = await _parents_of(client, admin_id, left, published["version_id"])
    right_parent = next(p for p in parents if p["parent_dataset_id"] == right)
    assert right_parent["parent_version_number"] == 1, parents


async def test_publishing_a_join_onto_the_right_dataset_still_records_both_sources(
        client, admin_id, tmp_path):
    """Both join inputs stay traceable when the result lands on the RIGHT side.

    Publishing names its target, and the target is not always the join's left
    side. If the primary lineage row is derived from the target rather than
    from the source version, the new version claims itself as its only parent —
    against a version id owned by the other dataset — and the left-hand source
    disappears from the audit trail entirely.
    """
    left, right, run_id = await _executed_join(client, admin_id, tmp_path)
    h = auth(admin_id)

    r = await client.post(f"/api/v1/joins/{run_id}/publish", headers=h,
                          json={"mode": "new_version", "dataset_id": right})
    assert r.status_code == 200, r.text
    published = r.json()
    assert published["dataset_id"] == right

    parents = await _parents_of(client, admin_id, right, published["version_id"])
    assert {p["parent_dataset_id"] for p in parents} == {left, right}
    assert {p["relation"] for p in parents} == {"joined_from"}

    # Every parent row names a version that actually belongs to the dataset it
    # names — the pairing is what makes lineage resolvable.
    owned = {left: await _version_ids(client, admin_id, left),
             right: await _version_ids(client, admin_id, right)}
    for p in parents:
        assert p["parent_version_id"] in owned[p["parent_dataset_id"]], p


async def test_publishing_a_join_onto_its_left_dataset_records_both_sources(
        client, admin_id, tmp_path):
    """The default target records left and right, each against its own version."""
    left, right, run_id = await _executed_join(client, admin_id, tmp_path)
    h = auth(admin_id)

    r = await client.post(f"/api/v1/joins/{run_id}/publish", headers=h,
                          json={"mode": "new_version"})
    assert r.status_code == 200, r.text
    published = r.json()
    assert published["dataset_id"] == left

    parents = await _parents_of(client, admin_id, left, published["version_id"])
    assert {p["parent_dataset_id"] for p in parents} == {left, right}
    owned = {left: await _version_ids(client, admin_id, left),
             right: await _version_ids(client, admin_id, right)}
    for p in parents:
        assert p["parent_version_id"] in owned[p["parent_dataset_id"]], p
