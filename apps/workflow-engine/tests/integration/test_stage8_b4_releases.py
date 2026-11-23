"""Stage 8 B4: releases pin the sub-workflow closure, run through the snapshot, and are blocked when a child breaks
(DESIGN-STAGE8-REUSE 4.1, 4.4, 4.5, 10.3) over HTTP on real Postgres."""

from __future__ import annotations

import pytest

from ._stage8_b4_support import C, N, child_body, make, parent_body, publish, save, trigger
from .conftest import uniq
from .journeys._helpers import new_project

pytestmark = pytest.mark.integration


class Shop:
    """A project with a child workflow (published v1 'one'), and a route whose handler calls it."""

    def __init__(self, api, test_db, *, version="latest", inputs=None, mode="passThrough", tag="one", fields=()):
        self.api, self.db = api, test_db
        self.P, self.S = new_project(api, "s8b4")
        self.child = make(api, child_body(tag, *fields, project_id=self.P))
        publish(api, self.child)
        r = api.post(f"/projects/{self.P}/routes", {"method": "GET", "path": "/quote", "auth": "none",
                                                    "handler": {"type": "workflow", "create": True}})
        assert r.status_code == 201, r.text
        self.route = r.json()["route"]["id"]
        self.W = r.json()["route"]["handler"]["workflow_id"]
        body = parent_body(self.child, version, mode=mode, inputs=inputs)
        save(api, self.W, body)

    def release(self, env="dev", expect=201):
        r = self.api.post(f"/projects/{self.P}/releases", {"environment": env})
        assert r.status_code == expect, r.text
        return r.json() if expect == 201 else r.json()["detail"]

    def promote(self, rid: str, expect=201):
        r = self.api.post(f"/projects/{self.P}/releases/{rid}/promote", {"to_environment": "prod"})
        assert r.status_code == expect, r.text
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

    def republish_child(self, tag: str, *fields) -> None:
        save(self.api, self.child, child_body(tag, *fields))
        publish(self.api, self.child)


def test_a_release_pins_the_child_version_and_prod_never_follows_the_publish_pointer(api, test_db):
    shop = Shop(api, test_db)
    shop.republish_child("two")                                     # child v2 is live; the handler follows 'latest'
    r1 = shop.release()
    assert r1["pins_subworkflows"] is True and r1["status"] == "snapshot"

    snap = shop.snapshot(r1["id"])
    [edge] = snap["subworkflows"]
    assert (edge["parent_workflow_id"], edge["node"], edge["child_workflow_id"], edge["spec"]) == (shop.W, "call", shop.child, "latest")
    assert edge["child_version_number"] == 2 and edge["parent_version_id"] > 0 and edge["child_version_id"] > 0
    assert "error" not in edge and "cycle" not in edge
    by = {(w["workflow_id"], w["via"]): w for w in snap["workflows"]}
    assert by[(shop.W, "route")]["version_id"] == edge["parent_version_id"]
    assert by[(shop.child, "subworkflow")]["version_id"] == edge["child_version_id"]      # the child is in the snapshot too
    check = {c["id"]: c for c in api.get(f"/projects/{shop.P}/releases/{r1['id']}").json()["checks"]}
    assert check["subworkflows_resolved"]["status"] == "pass" and check["subworkflow_contracts_compatible"]["status"] == "pass"
    ran = [c for c in api.get(f"/projects/{shop.P}/releases/{r1['id']}").json()["checks"] if c["id"] == "workflow_tests_pass"]
    assert {c["details"]["workflow_id"] for c in ran} == {shop.W, shop.child}      # the child's tests run too: it is in the snapshot

    shop.promote(r1["id"])
    assert shop.prod()["tag"] == "two"

    shop.republish_child("three")                                   # the live pointer moves on...
    assert shop.prod()["tag"] == "two"                              # ...prod keeps the pinned version
    assert shop.dev()["tag"] == "three"                             # a draft environment resolves live

    r2 = shop.release()
    assert shop.snapshot(r2["id"])["subworkflows"][0]["child_version_number"] == 3
    diff = api.get(f"/projects/{shop.P}/releases/{r2['id']}/diff?against={r1['id']}").json()
    [change] = diff["subworkflows"]
    assert (change["workflow"], change["from_version"], change["to_version"], change["via"]) == (shop.child, [2], [3], "subworkflow")
    assert change["parents"] == [{"workflow_id": shop.W, "node": "call"}]
    assert all(w["workflow_id"] != shop.child for w in diff["workflows"])                 # roots only in the classic list

    shop.promote(r2["id"])
    assert shop.prod()["tag"] == "three"
    rb = api.post(f"/projects/{shop.P}/environments/prod/rollback", {})
    assert rb.status_code == 200, rb.text
    assert shop.prod()["tag"] == "two"                              # rolling back restores the pinned child as well


def test_a_numbered_pin_and_two_parents_with_different_pins(api, test_db):
    shop = Shop(api, test_db, version=1, tag="one")
    shop.republish_child("two")
    r = api.post(f"/projects/{shop.P}/routes", {"method": "GET", "path": "/other", "auth": "none",
                                                "handler": {"type": "workflow", "create": True}})
    other = r.json()["route"]["handler"]["workflow_id"]                       # a second route handler, following 'latest'
    save(api, other, parent_body(shop.child, "latest"))
    rel = shop.release()
    edges = {e["parent_workflow_id"]: e for e in shop.snapshot(rel["id"])["subworkflows"]}
    assert edges[shop.W]["child_version_number"] == 1 and edges[other]["child_version_number"] == 2
    # the child's two versions are both in the snapshot, each under 'subworkflow'
    versions = sorted(w["version_id"] for w in shop.snapshot(rel["id"])["workflows"] if w["workflow_id"] == shop.child)
    assert len(versions) == 2


def test_a_child_that_breaks_its_parent_blocks_the_release(api, test_db):
    shop = Shop(api, test_db, mode="map", inputs={"amount": "{{ 5 }}"}, fields=(("amount", "number", True),))
    publish(api, shop.W)
    ok = shop.release()
    assert all(c["status"] != "fail" for c in ok["checks"])

    # v2 asks for a new required input the handler does not map
    save(api, shop.child, child_body("two", ("amount", "number", True), ("currency", "string", True)))
    impact = publish(api, shop.child).json()["impact"]
    assert [i["parent_workflow_id"] for i in impact] == [shop.W] and impact[0]["breaking"][0]["code"] == "WF024"

    blocked = shop.release(expect=422)
    assert blocked["code"] == "checks_failed"
    bad = next(c for c in blocked["checks"] if c["id"] == "subworkflow_contracts_compatible")
    assert bad["status"] == "fail" and bad["details"]["broken"][0]["code"] == "WF024"
    assert "currency" in bad["message"] and bad["details"]["broken"][0]["workflow_id"] == shop.W
    rel = api.get(f"/projects/{shop.P}/releases/{blocked['release_id']}").json()
    assert rel["status"] == "blocked" and rel["is_active"] is False
    assert shop.promote(ok["id"])["status"] == "active"                       # the earlier, good release still promotes

    # a compatible v3 (the new input is optional) lets the release through again
    save(api, shop.child, child_body("three", ("amount", "number", True), ("currency", "string", False)))
    publish(api, shop.child)
    assert shop.release()["status"] == "snapshot"


def test_unresolvable_and_approval_children_block_the_release(api, test_db):
    shop = Shop(api, test_db)
    never = make(api, child_body("x", project_id=shop.P))                       # never published
    body = parent_body(never, "latest")
    save(api, shop.W, body)
    blocked = shop.release(expect=422)
    resolved = next(c for c in blocked["checks"] if c["id"] == "subworkflows_resolved")
    assert resolved["status"] == "fail" and resolved["details"]["problems"][0]["reason"] == "unresolved"
    assert "never been published" in resolved["details"]["problems"][0]["message"]

    human = make(api, {"name": uniq("human"), "project_id": shop.P, "settings": {},
                       "nodes": [trigger(("x", "string", False)), N("Ask", "Approval", {"title": "ok?"}, 200)],
                       "connections": [C("Inputs", "Ask")]})
    publish(api, human)
    save(api, shop.W, parent_body(human, "latest"))
    blocked = shop.release(expect=422)
    resolved = next(c for c in blocked["checks"] if c["id"] == "subworkflows_resolved")
    assert resolved["status"] == "fail" and resolved["details"]["problems"][0]["reason"] == "approval"


def test_a_release_keeps_the_child_from_being_deleted_and_used_by_names_it(api, test_db):
    shop = Shop(api, test_db)
    rel = shop.release()
    r = api.delete(f"/workflows/{shop.child}")
    assert r.status_code == 409, r.text
    used = r.json()["detail"]["used_by"]
    assert [(u["parent_workflow_id"], u["where"], u["release_ids"]) for u in used] == [(shop.W, "release", [rel["id"]])]
    rows = api.get(f"/workflows/{shop.child}/used-by").json()
    assert {u["where"] for u in rows} == {"draft", "release"}


def test_a_release_cut_before_pinning_resolves_live_and_is_flagged(api, test_db):
    shop = Shop(api, test_db)
    rel = shop.release()
    test_db.query("UPDATE releases SET snapshot = snapshot - 'subworkflows' WHERE id = %s", (rel["id"],))
    listed = {r["id"]: r for r in api.get(f"/projects/{shop.P}/releases").json()}
    assert listed[rel["id"]]["pins_subworkflows"] is False
    shop.promote(rel["id"])                                                      # old releases still promote (nothing to check)
    shop.republish_child("two")
    assert shop.prod()["tag"] == "two"                                           # no pins: the call follows the live pointer
