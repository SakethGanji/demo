"""Publishing records the dataset the SOURCE VERSION came from, not the target.

``publish_artifact_as_version`` receives the publish *target* (``ds``) and the
*source* version (``parent_ver``). For every library/transform caller those name
the same dataset, so deriving the lineage parent from the target went unnoticed.
A join breaks the assumption: ``POST /joins/{run}/publish`` may target the
right-hand dataset (or any third dataset the caller can write) while the run's
version is always the LEFT side's.

Without this, publishing a join into the right-hand dataset writes a lineage row
pairing the TARGET's dataset id and name with the LEFT's version id and number —
a parent edge naming a version that dataset never had — and the real left-hand
parent vanishes from the graph entirely. Provenance is the whole point of
lineage: an analyst tracing a joined dataset back to its inputs is told it came
from itself, and the version's own ``source.from_dataset_id`` agrees, so there
is nothing left to reconstruct the truth from.
"""

from __future__ import annotations

from conftest import (
    XLSX_MIME,
    auth,
    make_crm_workbook,
    upload_file,
)


async def _crm_dataset(client, admin_id, tmp_path, name):
    path = tmp_path / name
    make_crm_workbook(path)
    body = await upload_file(client, admin_id, path, name=name,
                             content_type=XLSX_MIME)
    return body["dataset_id"]


async def _confirmed_relationship(client, admin_id, left_ds, right_ds):
    r = await client.post(
        f"/api/v1/datasets/{left_ds}/relationships", headers=auth(admin_id),
        json={"from_sheet": "Orders", "from_column": "customer_id",
              "to_sheet": "Customers", "to_column": "customer_id",
              "to_dataset_id": right_ds, "confirmed": True})
    assert r.status_code == 201, r.text
    return r.json()


async def test_publishing_a_join_into_the_right_dataset_still_names_the_left_as_parent(
        client, admin_id, tmp_path):
    """Lineage must name the left dataset, not repeat the publish target.

    Production impact: the joined version claims to descend from a version of
    the dataset it was written into, and the left-hand input disappears from
    lineage, so the derivation cannot be audited or reproduced.
    """
    left = await _crm_dataset(client, admin_id, tmp_path, "left.xlsx")
    right = await _crm_dataset(client, admin_id, tmp_path, "right.xlsx")
    h = auth(admin_id)
    rel = await _confirmed_relationship(client, admin_id, left, right)

    run_id = (await client.post("/api/v1/joins/execute", headers=h,
                                json={"relationship_id": rel["id"]})).json()["run_id"]

    # Publish into the RIGHT dataset — the side the run's version does NOT
    # belong to. This is the case the target/source conflation gets wrong.
    r = await client.post(f"/api/v1/joins/{run_id}/publish", headers=h,
                          json={"mode": "new_version", "dataset_id": right})
    assert r.status_code == 200, r.text
    published = r.json()
    assert published["dataset_id"] == right

    lineage = (await client.get(f"/api/v1/datasets/{right}/lineage",
                                headers=h)).json()
    parents = [p for p in lineage["parents"]
               if p["dataset_version_id"] == published["version_id"]]
    assert len(parents) == 2, parents
    # Both inputs are named, and neither is invented.
    assert {p["parent_dataset_id"] for p in parents} == {left, right}

    by_dataset = {p["parent_dataset_id"]: p for p in parents}
    # The left parent must carry the LEFT dataset's own version, not a version
    # number borrowed from the publish target.
    left_versions = (await client.get(f"/api/v1/datasets/{left}/versions",
                                      headers=h)).json()["items"]
    left_version_ids = {v["id"] for v in left_versions}
    assert by_dataset[left]["parent_version_id"] in left_version_ids

    right_versions = (await client.get(f"/api/v1/datasets/{right}/versions",
                                       headers=h)).json()["items"]
    right_version_ids = {v["id"] for v in right_versions}
    assert by_dataset[right]["parent_version_id"] in right_version_ids

    # And the denormalized name must match the dataset it points at.
    catalog = (await client.get("/api/v1/datasets", headers=h,
                                params={"limit": 200})).json()["items"]
    names = {d["id"]: d["name"] for d in catalog}
    assert by_dataset[left]["parent_dataset_name"] == names[left]
    assert by_dataset[right]["parent_dataset_name"] == names[right]


async def test_a_join_published_into_the_right_dataset_records_the_left_as_its_source(
        client, admin_id, tmp_path):
    """The version's own `source.from_dataset_id` must be the left dataset.

    Production impact: `source` is the version row's self-describing provenance
    and is read independently of the lineage table. Naming the publish target
    there while carrying the left side's version number produces a version that
    claims to be derived from a version of itself that never existed.
    """
    left = await _crm_dataset(client, admin_id, tmp_path, "left.xlsx")
    right = await _crm_dataset(client, admin_id, tmp_path, "right.xlsx")
    h = auth(admin_id)
    rel = await _confirmed_relationship(client, admin_id, left, right)
    run_id = (await client.post("/api/v1/joins/execute", headers=h,
                                json={"relationship_id": rel["id"]})).json()["run_id"]

    r = await client.post(f"/api/v1/joins/{run_id}/publish", headers=h,
                          json={"mode": "new_version", "dataset_id": right})
    assert r.status_code == 200, r.text
    published = r.json()

    # `source` is stored on the version row and not projected by any route, so
    # read it where it lives.
    from sqlalchemy import text

    from app.infra.db.postgres import async_session_factory

    async with async_session_factory() as s:
        source = (await s.execute(
            text("SELECT source FROM dataset_versions WHERE id = :id"),
            {"id": published["version_id"]},
        )).scalar()
    assert source["type"] == "published", source
    assert source["from_dataset_id"] == left, source


async def test_publishing_an_ordinary_analytics_run_still_names_its_own_dataset(
        client, admin_id, tmp_path):
    """The single-source path is unchanged: parent is the dataset it ran on.

    Production impact: deriving the parent from the source version must not
    regress the common case, where the run, the target and the parent are all
    the same dataset.
    """
    ds = await _crm_dataset(client, admin_id, tmp_path, "solo.xlsx")
    h = auth(admin_id)
    r = await client.post(
        f"/api/v1/datasets/{ds}/analytics", headers=h,
        json={"name": "orders sample", "kind": "sample", "sheet": "Orders",
              "params": {"target_total_volume": 2,
                         "sampling_steps": [{"method": "random", "sample_size": 2}],
                         "seed": 1}})
    assert r.status_code == 201, r.text
    definition = r.json()
    run = (await client.post(
        f"/api/v1/datasets/{ds}/analytics/{definition['id']}/run",
        headers=h)).json()
    assert run["status"] == "completed", run

    r = await client.post(
        f"/api/v1/datasets/{ds}/analytics/runs/{run['id']}/publish", headers=h,
        json={"mode": "new_version"})
    assert r.status_code == 200, r.text

    lineage = (await client.get(f"/api/v1/datasets/{ds}/lineage", headers=h)).json()
    assert lineage["parents"], lineage
    assert lineage["parents"][0]["parent_dataset_id"] == ds
