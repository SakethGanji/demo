"""Stage 8 T1 (tester): release closure beyond one level, pins versus later publishes, and delete protection in every state a
parent can be in (DESIGN-STAGE8-REUSE 4.1, 4.4, 4.5, 10.3, 11) over HTTP on real Postgres."""

from __future__ import annotations

import pytest

from ._stage8_t1_support import C, N, call_node, check, child_body, detail, get, make, new_project, parent_body, publish, run, save, setf, trigger, uniq

pytestmark = pytest.mark.integration


class Estate:
    """A project whose route ``GET /quote`` is handled by a workflow ``W``; tests wire W to children and cut releases."""

    def __init__(self, api, prefix: str = "s8t1r"):
        self.api = api
        self.P, self.S = new_project(api, prefix)
        r = api.post(f"/projects/{self.P}/routes", {"method": "GET", "path": "/quote", "auth": "none",
                                                    "handler": {"type": "workflow", "create": True}})
        assert r.status_code == 201, r.text
        self.route, self.W = r.json()["route"]["id"], r.json()["route"]["handler"]["workflow_id"]

    def wf(self, body: dict) -> str:
        return make(self.api, {**body, "project_id": self.P})

    def leaf(self, tag: str) -> str:
        wid = self.wf(child_body(tag))
        publish(self.api, wid)
        return wid

    def edit_leaf(self, wid: str, tag: str, publish_it: bool = True) -> None:
        save(self.api, wid, child_body(tag))
        if publish_it:
            publish(self.api, wid)

    def middle(self, child: str, version="latest") -> str:
        """Inputs -> call(child) -> Wrap (adds ``via: middle``): a published link in a chain."""
        body = {"name": uniq("s8t1-mid"), "settings": {}, "nodes": [
            trigger(("x", "string", False)), call_node("call", child, version), setf("Wrap", 400, via="middle")],
            "connections": [C("Inputs", "call"), C("call", "Wrap")]}
        wid = self.wf(body)
        if version != "draft":
            publish(self.api, wid)
        return wid

    def handler(self, body: dict) -> None:
        save(self.api, self.W, body)

    def release(self, env="dev", expect=201):
        r = self.api.post(f"/projects/{self.P}/releases", {"environment": env})
        assert r.status_code == expect, r.text
        return r.json() if expect == 201 else r.json()["detail"]

    def promote(self, rid: str):
        r = self.api.post(f"/projects/{self.P}/releases/{rid}/promote", {"to_environment": "prod"})
        assert r.status_code == 201, r.text
        return r.json()

    def prod(self) -> dict:
        r = self.api.raw("GET", f"/api/p/{self.S}/quote")
        assert r.status_code == 200, r.text
        return r.json()

    def dev(self) -> dict:
        r = self.api.post(f"/projects/{self.P}/routes/{self.route}/test", {
            "params": {}, "query": {}, "headers": {}, "body": None, "environment": "dev", "apply_schemas": False})
        assert r.status_code == 200 and r.json()["status"] == 200, r.text
        return r.json()["body"]

    def snapshot(self, rid: str) -> dict:
        r = self.api.get(f"/projects/{self.P}/releases/{rid}")
        assert r.status_code == 200, r.text
        return r.json()["snapshot"]


def handler_calling(child: str, version="latest") -> dict:
    return parent_body(child, version)


# ── the closure ─────────────────────────────────────────────────────────────────────────────────────────────────────


def test_the_release_pins_the_whole_chain_and_prod_follows_neither_level_after_republishes(api):
    e = Estate(api)
    grand = e.leaf("g1")
    mid = e.middle(grand)
    e.handler(handler_calling(mid))
    rel = e.release()
    snap = e.snapshot(rel["id"])
    edges = {(x["parent_workflow_id"], x["child_workflow_id"]): x for x in snap["subworkflows"]}
    assert set(edges) == {(e.W, mid), (mid, grand)}                           # both hops are recorded, not only the first
    assert edges[(mid, grand)]["parent_version_id"] == edges[(e.W, mid)]["child_version_id"]   # the second hop starts at the pinned middle
    via = {w["workflow_id"]: w["via"] for w in snap["workflows"]}
    assert via[mid] == via[grand] == "subworkflow" and via[e.W] == "route"
    e.promote(rel["id"])
    assert e.prod()["tag"] == "g1" and e.prod()["via"] == "middle"

    e.edit_leaf(grand, "g2")                                                  # the bottom moves
    e.edit_leaf(mid, "ignored", publish_it=False)                             # (a different draft write on the middle: no effect on prod)
    assert e.prod()["tag"] == "g1"
    assert e.dev()["tag"] == "g2"                                             # a draft environment resolves live: middle(latest) -> grand(latest)
    r2 = e.release()
    assert {x["child_version_number"] for x in e.snapshot(r2["id"])["subworkflows"] if x["child_workflow_id"] == grand} == {2}
    assert e.prod()["tag"] == "g1"                                            # cutting a release does not move prod; promoting does
    e.promote(r2["id"])
    assert e.prod()["tag"] == "g2"


def test_a_draft_pin_is_frozen_by_the_release_and_later_edits_do_not_reach_prod(api):
    e = Estate(api)
    kid = e.wf(child_body("first"))                                           # never published: the handler pins its draft
    e.handler(handler_calling(kid, "draft"))
    rel = e.release()
    [edge] = e.snapshot(rel["id"])["subworkflows"]
    assert edge["spec"] == "draft" and edge["child_version_id"] > 0           # the draft became a version at release time
    e.promote(rel["id"])
    assert e.prod()["tag"] == "first"
    save(api, kid, child_body("second"))                                      # an unsaved experiment on the draft
    assert e.prod()["tag"] == "first" and e.dev()["tag"] == "second"


def test_one_parent_can_pin_two_versions_of_the_same_child_and_both_run_in_prod(api):
    e = Estate(api)
    kid = e.leaf("one")
    e.edit_leaf(kid, "two")
    body = {"name": "ignored", "settings": {}, "nodes": [
        N("Start", "Start"), call_node("first", kid, 1), setf("Remember", 300, seen="{{ $json.tag }}"),
        call_node("second", kid, 2, x=500)],
        "connections": [C("Start", "first"), C("first", "Remember"), C("Remember", "second")]}
    e.handler(body)
    rel = e.release()
    versions = {x["node"]: x["child_version_number"] for x in e.snapshot(rel["id"])["subworkflows"]}
    assert versions == {"first": 1, "second": 2}
    e.promote(rel["id"])
    out = e.prod()
    assert out["seen"] == "one" and out["tag"] == "two"
    e.edit_leaf(kid, "three")
    out = e.prod()
    assert out["seen"] == "one" and out["tag"] == "two"                       # numbered pins and release pins agree; v3 changes nothing


def test_releases_diff_names_a_child_that_moved_two_levels_down(api):
    e = Estate(api)
    grand = e.leaf("a")
    mid = e.middle(grand)
    e.handler(handler_calling(mid))
    r1 = e.release()
    e.edit_leaf(grand, "b")
    r2 = e.release()
    diff = api.get(f"/projects/{e.P}/releases/{r2['id']}/diff?against={r1['id']}").json()
    moved = [c for c in diff["subworkflows"] if c["workflow"] == grand]
    assert moved and moved[0]["from_version"] == [1] and moved[0]["to_version"] == [2]
    assert {"workflow_id": mid, "node": "call"} in moved[0]["parents"]
    assert all(c["workflow"] != mid for c in diff["subworkflows"])            # the middle did not change, so it is not listed


def test_a_snapshot_never_reads_a_pin_from_another_parent_version(api, test_db):
    """Pins are keyed ``<parent version>:<node>``; two releases of two parents that use the same node name must not cross."""
    e = Estate(api)
    a, b = e.leaf("A1"), e.leaf("B1")
    other = e.wf({**parent_body(b, "latest"), "name": uniq("s8t1-other")})
    publish(api, other)
    e.handler(handler_calling(a))
    r = api.post(f"/projects/{e.P}/routes", {"method": "GET", "path": "/other", "auth": "none",
                                             "handler": {"type": "workflow", "workflow_id": other}})
    assert r.status_code == 201, r.text
    rel = e.release()
    e.promote(rel["id"])
    assert e.prod()["tag"] == "A1"                                            # both handlers use a node called 'call'
    assert api.raw("GET", f"/api/p/{e.S}/other").json()["tag"] == "B1"


# ── delete protection ───────────────────────────────────────────────────────────────────────────────────────────────


def delete(api, wid: str, **q):
    suffix = ("?" + "&".join(f"{k}={v}" for k, v in q.items())) if q else ""
    return api.delete(f"/workflows/{wid}{suffix}")


def test_republishing_the_parent_without_the_call_frees_the_child(api):
    kid_id = make(api, child_body("v1"))
    publish(api, kid_id)
    parent = make(api, parent_body(kid_id, "latest"))
    publish(api, parent)
    assert delete(api, kid_id).status_code == 409
    plain = parent_body(kid_id, "latest")
    save(api, parent, {"nodes": [plain["nodes"][0], setf("Plain", tag="none")], "connections": [C("Start", "Plain")]})
    assert delete(api, kid_id).status_code == 409, "the published version still calls it until the parent is republished"
    publish(api, parent)
    assert delete(api, kid_id).status_code == 200


def test_force_is_refused_in_v1_and_deletes_nothing(api):
    kid_id = make(api, child_body("v1"))
    publish(api, kid_id)
    parent = make(api, parent_body(kid_id, "latest"))
    publish(api, parent)
    r = delete(api, kid_id, force="true")
    assert r.status_code in (400, 403, 409, 422), r.text
    assert api.get(f"/workflows/{kid_id}").status_code == 200


def test_a_draft_only_caller_does_not_block_but_a_numbered_pin_in_a_published_parent_does(api):
    kid_id = make(api, child_body("v1"))
    publish(api, kid_id)
    drafty = make(api, parent_body(kid_id, "draft"))
    pinned = make(api, parent_body(kid_id, 1))
    publish(api, pinned)
    used = delete(api, kid_id).json()["detail"]["used_by"]
    assert [u["parent_workflow_id"] for u in used] == [pinned] and drafty not in str(used)


def test_a_workflow_reached_only_through_a_release_snapshot_is_protected_even_when_no_parent_is_published(api):
    e = Estate(api)
    kid_id = e.leaf("one")
    e.handler(handler_calling(kid_id))
    rel = e.release()
    r = delete(api, kid_id)
    assert r.status_code == 409
    [row] = r.json()["detail"]["used_by"]
    assert row["where"] == "release" and rel["id"] in row["release_ids"]


def test_deleting_the_handler_workflow_is_refused_while_a_route_serves_it_and_the_child_stays(api):
    e = Estate(api)
    kid_id = e.leaf("one")
    e.handler(handler_calling(kid_id))
    r = api.delete(f"/workflows/{e.W}")
    # whatever the route layer decides about its own handler, the child must survive a failed or successful parent delete
    assert get(api, kid_id)["id"] == kid_id
    assert r.status_code in (200, 409)


def test_used_by_does_not_list_workflows_of_another_project_that_merely_share_a_name(api):
    P1, _ = new_project(api, "s8t1u1")
    P2, _ = new_project(api, "s8t1u2")
    mine = make(api, {**child_body("x"), "project_id": P1})
    publish(api, mine)
    caller = make(api, {**parent_body(mine, "latest"), "project_id": P1})
    stranger = make(api, {**parent_body("wf_not_this_one_zz", "latest"), "project_id": P2})
    rows = api.get(f"/workflows/{mine}/used-by").json()
    assert {r["parent_workflow_id"] for r in rows} == {caller} and stranger not in str(rows)


# ── publish impact says when a parent is part of a release; the agent tool counts as a caller ────────────────────────


def test_publish_impact_marks_a_parent_that_a_release_pinned(api):
    e = Estate(api)
    kid = e.leaf("one")
    e.handler(handler_calling(kid))
    publish(api, e.W)
    e.release()
    e.edit_leaf(kid, "two", publish_it=False)
    impact = publish(api, kid).json()["impact"]
    assert [(i["parent_workflow_id"], i["in_release"]) for i in impact] == [(e.W, True)]
    loner = e.wf(parent_body(kid, "latest"))
    publish(api, loner)
    again = {i["parent_workflow_id"]: i["in_release"] for i in publish(api, kid).json()["impact"]}
    assert again[e.W] is True and again[loner] is False


def test_an_agent_tool_that_calls_a_workflow_is_a_caller_for_used_by_and_delete_protection(api):
    kid = make(api, child_body("tool"))
    publish(api, kid)
    holder = make(api, {"name": uniq("s8t1-agent"), "settings": {}, "nodes": [
        N("Start", "Start"),
        N("Agent", "AIAgent", {"model": "claude-sonnet-4-20250514", "task": "look it up",
                               "builtinTools": [{"type": "workflow", "parameters": {"workflowId": kid, "version": "latest", "toolName": "lookup"}}]}, 200)],
        "connections": [C("Start", "Agent")]})
    rows = api.get(f"/workflows/{kid}/used-by").json()
    assert [(u["parent_workflow_id"], u["node"]) for u in rows] == [(holder, "Agent")], "the agent's tool call is not indexed"
    publish(api, holder)
    assert api.delete(f"/workflows/{kid}").status_code == 409, "a published agent that can call the workflow must protect it"


def test_moving_a_called_workflow_to_another_project_is_refused_like_deleting_it(api):
    P1, _ = new_project(api, "s8t1mv1")
    P2, _ = new_project(api, "s8t1mv2")
    kid = make(api, {**child_body("x"), "project_id": P1})
    publish(api, kid)
    parent = make(api, {**parent_body(kid, "latest"), "project_id": P1})
    publish(api, parent)
    assert run(api, parent)["status"] == "success"
    r = api.put(f"/workflows/{kid}", {"project_id": P2})
    assert r.status_code == 409, f"the move went through ({r.status_code}) and the published parent now runs: {run(api, parent)['errors'][0]['error_code']}"
