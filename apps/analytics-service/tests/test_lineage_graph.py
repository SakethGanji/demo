"""Transitive lineage — the whole derivation chain, not one hop.

`/lineage` answers "what is this dataset's immediate parent". This answers
"where did this actually come from", which for a published output means walking
back through every join, transformation, and upload that produced it.
"""

from __future__ import annotations

import json

from conftest import auth, create_team_user, upload_inline

ROWS = [{"id": i, "region": "NY" if i % 2 else "LA", "amount": i * 10.0}
        for i in range(1, 6)]


async def _chain(client, admin_id):
    """upload → transformation → published dataset → transformation → published."""
    h = auth(admin_id)
    root = (await upload_inline(client, admin_id, json.dumps(ROWS)))["dataset_id"]

    async def transform_and_publish(source, name):
        d = (await client.post(f"/api/v1/datasets/{source}/transformations",
                               headers=h, json={
                                   "name": f"t-{name}", "sheet": "data",
                                   "steps": [{"type": "sort",
                                              "by": [{"column": "amount"}]}]})).json()
        run = (await client.post(
            f"/api/v1/datasets/{source}/transformations/{d['id']}/run",
            headers=h)).json()
        published = (await client.post(
            f"/api/v1/datasets/{source}/transformations/runs/{run['id']}/publish",
            headers=h, json={"mode": "new_dataset", "name": name})).json()
        return published["dataset_id"]

    mid = await transform_and_publish(root, f"mid-{root[:8]}")
    leaf = await transform_and_publish(mid, f"leaf-{root[:8]}")
    return root, mid, leaf


async def test_the_graph_walks_the_whole_chain(client, admin_id):
    root, mid, leaf = await _chain(client, admin_id)
    h = auth(admin_id)

    r = await client.get(f"/api/v1/datasets/{leaf}/lineage/graph", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()

    node_ids = {n["id"] for n in body["nodes"]}
    assert {root, mid, leaf} <= node_ids       # two hops back, not one
    assert next(n for n in body["nodes"] if n["id"] == leaf)["is_root"] is True

    edges = {(e["child_id"], e["parent_id"]) for e in body["edges"]}
    assert (leaf, mid) in edges and (mid, root) in edges
    assert all(e["relation"] == "transformed_from" for e in body["edges"])
    assert body["truncated"] is False


async def test_the_graph_reaches_downstream_too(client, admin_id):
    root, mid, leaf = await _chain(client, admin_id)

    r = await client.get(f"/api/v1/datasets/{root}/lineage/graph",
                         headers=auth(admin_id))
    assert r.status_code == 200
    node_ids = {n["id"] for n in r.json()["nodes"]}
    assert {mid, leaf} <= node_ids      # descendants, from the other direction


async def test_depth_can_be_capped(client, admin_id):
    root, mid, leaf = await _chain(client, admin_id)
    r = await client.get(f"/api/v1/datasets/{leaf}/lineage/graph?max_depth=1",
                         headers=auth(admin_id))
    assert r.status_code == 200
    assert r.json()["max_depth"] == 1
    assert r.json()["truncated"] is True
    # Only the immediate parent is reached at depth 1.
    assert {e["parent_id"] for e in r.json()["edges"]} == {mid}


async def test_a_dataset_with_no_lineage_returns_just_itself(client, admin_id):
    ds = (await upload_inline(client, admin_id, json.dumps(ROWS)))["dataset_id"]
    r = await client.get(f"/api/v1/datasets/{ds}/lineage/graph",
                         headers=auth(admin_id))
    assert r.status_code == 200
    assert r.json()["edges"] == []
    assert [n["id"] for n in r.json()["nodes"]] == [ds]


async def test_self_referential_lineage_does_not_appear_as_an_edge(client, admin_id):
    """Publishing as a new VERSION of the same dataset is not a graph edge."""
    h = auth(admin_id)
    ds = (await upload_inline(client, admin_id, json.dumps(ROWS)))["dataset_id"]
    d = (await client.post(f"/api/v1/datasets/{ds}/transformations", headers=h,
                           json={"name": "t", "sheet": "data",
                                 "steps": [{"type": "limit", "count": 2}]})).json()
    run = (await client.post(f"/api/v1/datasets/{ds}/transformations/{d['id']}/run",
                             headers=h)).json()
    await client.post(
        f"/api/v1/datasets/{ds}/transformations/runs/{run['id']}/publish",
        headers=h, json={"mode": "new_version"})

    r = await client.get(f"/api/v1/datasets/{ds}/lineage/graph", headers=h)
    assert r.status_code == 200
    assert r.json()["edges"] == []


async def test_the_graph_is_hidden_across_teams(client, admin_id):
    ds = (await upload_inline(client, admin_id, json.dumps(ROWS)))["dataset_id"]
    outsider, _ = await create_team_user(client, admin_id, "admin")
    r = await client.get(f"/api/v1/datasets/{ds}/lineage/graph",
                         headers=auth(outsider))
    assert r.status_code == 404
