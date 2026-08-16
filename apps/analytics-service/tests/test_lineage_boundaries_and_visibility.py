"""Two things the lineage graph got wrong: where it stops, and what it names.

**Where it stops.** The recursive CTE's recursive terms are gated by `depth <
:max_depth`, so rows AT `max_depth` are fully materialised and returned — they
are included, not cut. `truncated` was computed as `any(depth >= max_depth)`,
i.e. inferred from an edge that came back successfully, with no evidence at all
about whether the DAG continues. A complete chain whose depth happened to equal
the cap reported `truncated: true`, and both the schema and the MCP tool tell the
caller that means "raise depth to see further" — which returns an identical
graph. The walk now goes one hop past the cap and uses the overflow as the
signal.

**What it names.** Lineage crosses team boundaries: a cross-team join publishes
into the left team while recording a lineage row naming the right-hand dataset,
including its denormalized name. Neither query had a team predicate and neither
took a principal, so a member of the left team read the other team's dataset id,
name and domain out of `/lineage` and `/lineage/graph` — the exact existence
`GET /datasets/{id}` hides behind a 404, and which
`relationships/api.py` calls out as the thing a relationship must not become
("a side channel for reading — or learning the existence of — another team's
dataset").
"""

from __future__ import annotations

import json

from conftest import (
    XLSX_MIME,
    auth,
    create_team_user,
    make_crm_workbook,
    upload_file,
    upload_inline,
)

DEFAULT_TEAM = "00000000-0000-0000-0000-000000000001"
ROWS = [{"id": i, "region": "NY" if i % 2 else "LA", "amount": i * 10.0}
        for i in range(1, 6)]


async def _chain(client, admin_id):
    """upload → transformation → published → transformation → published."""
    h = auth(admin_id)
    root = (await upload_inline(client, admin_id, json.dumps(ROWS)))["dataset_id"]

    async def step(source, name):
        d = (await client.post(f"/api/v1/datasets/{source}/transformations",
                               headers=h, json={
                                   "name": f"t-{name}", "sheet": "data",
                                   "steps": [{"type": "sort",
                                              "by": [{"column": "amount"}]}]})).json()
        run = (await client.post(
            f"/api/v1/datasets/{source}/transformations/{d['id']}/run",
            headers=h)).json()
        return (await client.post(
            f"/api/v1/datasets/{source}/transformations/runs/{run['id']}/publish",
            headers=h, json={"mode": "new_dataset", "name": name})).json()["dataset_id"]

    mid = await step(root, f"mid-{root[:8]}")
    leaf = await step(mid, f"leaf-{root[:8]}")
    return root, mid, leaf


# --- the depth boundary -------------------------------------------------------

async def test_a_graph_that_ends_exactly_at_the_depth_cap_is_not_truncated(
        client, admin_id):
    root, mid, leaf = await _chain(client, admin_id)

    r = await client.get(f"/api/v1/datasets/{leaf}/lineage/graph?max_depth=2",
                         headers=auth(admin_id))
    assert r.status_code == 200, r.text
    body = r.json()
    # Both hops are present, and there is nothing beyond them.
    assert {(e["child_id"], e["parent_id"]) for e in body["edges"]} == {
        (leaf, mid), (mid, root)}
    assert body["truncated"] is False


async def test_a_graph_that_really_continues_still_says_so(client, admin_id):
    root, mid, leaf = await _chain(client, admin_id)

    r = await client.get(f"/api/v1/datasets/{leaf}/lineage/graph?max_depth=1",
                         headers=auth(admin_id))
    assert r.json()["truncated"] is True
    assert {e["parent_id"] for e in r.json()["edges"]} == {mid}


async def test_the_probe_hop_does_not_leak_into_the_response(client, admin_id):
    """The extra hop exists only to prove continuation; its nodes and edges must
    not appear, or `max_depth` would silently mean one more than it says."""
    root, mid, leaf = await _chain(client, admin_id)

    r = await client.get(f"/api/v1/datasets/{leaf}/lineage/graph?max_depth=1",
                         headers=auth(admin_id))
    body = r.json()
    assert all(e["depth"] <= 1 for e in body["edges"])
    assert root not in {n["id"] for n in body["nodes"]}


# --- cross-team visibility ----------------------------------------------------

async def _cross_team_published_join(client, admin_id, tmp_path):
    """Publish a join of a DEFAULT-team dataset with another team's dataset."""
    h = auth(admin_id)

    async def crm(name, team_id):
        path = tmp_path / name
        make_crm_workbook(path)
        body = await upload_file(client, admin_id, path, name=name,
                                 content_type=XLSX_MIME, team_id=team_id)
        return body["dataset_id"]

    left = await crm("left.xlsx", DEFAULT_TEAM)
    _, other_team = await create_team_user(client, admin_id, "editor")
    right = await crm("right.xlsx", other_team)
    rel = (await client.post(f"/api/v1/datasets/{left}/relationships", headers=h, json={
        "from_sheet": "Orders", "from_column": "customer_id",
        "to_sheet": "Customers", "to_column": "customer_id",
        "to_dataset_id": right, "confirmed": True})).json()
    run_id = (await client.post("/api/v1/joins/execute", headers=h,
                                json={"relationship_id": rel["id"]})).json()["run_id"]
    published = (await client.post(f"/api/v1/joins/{run_id}/publish", headers=h, json={
        "mode": "new_version", "dataset_id": left})).json()
    return left, right, published


async def test_lineage_does_not_name_a_parent_in_a_team_the_caller_cannot_read(
        client, admin_id, tmp_path):
    left, right, _ = await _cross_team_published_join(client, admin_id, tmp_path)
    insider, _ = await create_team_user(client, admin_id, "editor", team_id=DEFAULT_TEAM)
    ih = auth(insider)

    # The premise: the direct route hides that dataset entirely.
    assert (await client.get(f"/api/v1/datasets/{right}", headers=ih)).status_code == 404

    r = await client.get(f"/api/v1/datasets/{left}/lineage", headers=ih)
    assert r.status_code == 200, r.text
    assert right not in r.text
    hidden = [p for p in r.json()["parents"] if p["parent_visible"] is False]
    assert hidden, "the invisible parent should still be reported, just unnamed"
    assert all(p["parent_dataset_id"] is None and p["parent_dataset_name"] is None
               for p in hidden)
    # The derivation itself is not hidden — only the identity of the other side.
    assert {p["relation"] for p in hidden} == {"joined_from"}


async def test_the_lineage_graph_withholds_other_teams_nodes_and_counts_them(
        client, admin_id, tmp_path):
    left, right, _ = await _cross_team_published_join(client, admin_id, tmp_path)
    insider, _ = await create_team_user(client, admin_id, "editor", team_id=DEFAULT_TEAM)

    r = await client.get(f"/api/v1/datasets/{left}/lineage/graph", headers=auth(insider))
    assert r.status_code == 200, r.text
    body = r.json()
    assert right not in r.text
    assert right not in {n["id"] for n in body["nodes"]}
    # Edges touching a withheld node would still carry its UUID.
    assert all(right not in (e["child_id"], e["parent_id"]) for e in body["edges"])
    assert body["hidden_nodes"] >= 1


async def test_a_member_of_both_teams_sees_the_whole_graph(client, admin_id, tmp_path):
    """The filter is about visibility, not about hiding lineage from everyone."""
    left, right, _ = await _cross_team_published_join(client, admin_id, tmp_path)

    r = await client.get(f"/api/v1/datasets/{left}/lineage", headers=auth(admin_id))
    assert r.status_code == 200, r.text
    assert right in {p["parent_dataset_id"] for p in r.json()["parents"]}
    assert all(p["parent_visible"] for p in r.json()["parents"])

    g = await client.get(f"/api/v1/datasets/{left}/lineage/graph", headers=auth(admin_id))
    assert right in {n["id"] for n in g.json()["nodes"]}
    assert g.json()["hidden_nodes"] == 0


async def test_same_team_lineage_is_unchanged(client, admin_id):
    """The common case must keep naming its parents."""
    root, mid, leaf = await _chain(client, admin_id)

    r = await client.get(f"/api/v1/datasets/{leaf}/lineage", headers=auth(admin_id))
    parents = r.json()["parents"]
    assert {p["parent_dataset_id"] for p in parents} == {mid}
    assert all(p["parent_visible"] for p in parents)
    assert all(p["parent_dataset_name"] for p in parents)
