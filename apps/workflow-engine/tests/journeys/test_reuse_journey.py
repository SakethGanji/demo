"""Journey: a bank team reuses work instead of rebuilding it, entirely over HTTP (DESIGN-STAGE8-REUSE 14.3).

The story, one long scenario on one engine and one project:

  1. A new project; the analyst previews, then installs the ``refund-triage`` template (it brings ``customer-notify`` with it).
  2. The installed tests all pass; the entry stays a draft until the project holds the secret the template needs.
  3. She moves the payout step into its own sub-workflow: two selections are refused with a reason, a dry run changes nothing,
     the real conversion publishes the child and pins the parent to "latest".
  4. The tests that checked the moved step are reviewed and re-approved; the entry then publishes. "Used by" shows the call, and
     deleting the called workflow is refused.
  5. A quote route calls a pricing sub-workflow that calls a rate sub-workflow. A release is cut and promoted to prod.
  6. The pricing workflow is republished with a new required input. The publish says which parent it breaks.
  7. Prod keeps answering from the pinned version; the run succeeds.
  8. Re-cutting the release is refused (contracts not compatible) and the release is blocked.
  9. She maps the new input, re-cuts and promotes: prod now answers from version 2.
 10. The execution tree of a prod run is root, child, grandchild, linked both ways.

(The design's step 4 names the nodes "Issue refund" and "Notify requester"; the shipped template has no such node, and the only
convex single-exit selections of its payout part are the one used here. Steps 5-10 use small Set-only workflows because the
template's own sub-workflows call bank APIs that do not exist in a test.)
"""

from __future__ import annotations

import pytest

from ..integration._stage8_b4_support import C, N, make, publish, save, setf, trigger
from ..integration.conftest import api, uniq  # noqa: F401  (the `api` fixture)

pytestmark = pytest.mark.integration


def step(msg: str, r, expect: int | tuple = 200):
    ok = r.status_code in (expect if isinstance(expect, tuple) else (expect,))
    assert ok, f"{msg}: {r.status_code} {r.text[:600]}"
    return r.json()


def test_the_reuse_journey(api, test_db):
    slug = uniq("jr-reuse")

    # ── 1. project, preview, install ────────────────────────────────────────────────────────────────────────────
    P = step("create project", api.post("/projects", {"name": uniq("Reuse journey"), "slug": slug}), 201)["id"]
    preview = step("preview", api.post(f"/projects/{P}/templates/refund-triage/install-preview", {}))
    assert preview["status"] == "new" and [d["slug"] for d in preview["dependencies"]] == ["customer-notify"]
    assert api.get(f"/projects/{P}/workflows").json() == [], "a preview writes nothing"
    installed = step("install", api.post(f"/projects/{P}/templates/refund-triage/install", {}), 201)
    ids = {w["key"]: w["id"] for w in installed["workflows"]}
    main, eligibility = ids["main"], ids["eligibility"]
    assert installed["dependencies"][0]["slug"] == "customer-notify"
    notify = next(w["id"] for w in api.get(f"/projects/{P}/workflows").json() if w["name"] == "Notify customer")

    # ── 2. the installed tests pass; the entry waits for its secret ─────────────────────────────────────────────────
    for wid in (main, eligibility, notify):
        rep = step("run tests", api.post(f"/workflows/{wid}/tests/run", {}))
        assert rep["summary"]["failed"] == 0 and rep["summary"]["errored"] == 0 and rep["summary"]["total"] >= 3, rep["summary"]
    blocked = api.post(f"/workflows/{main}/publish", {})
    assert blocked.status_code == 422 and any(f["code"] == "WF006" for f in blocked.json()["detail"]["findings"])
    step("secret", api.post(f"/projects/{P}/secrets", {"key": "REFUNDS_API_KEY", "value": "k-test", "type": "string", "environment": "dev"}), (200, 201))

    # ── 3. convert selection: refusals, dry run, commit ─────────────────────────────────────────────────────────────
    def attempt(nodes, dry_run, name="Pay refund"):
        return api.post(f"/workflows/{main}/extract-subworkflow", {"nodes": nodes, "name": name, "dry_run": dry_run})

    two_exits = attempt(["Issue refund", "Refund accepted?"], True)
    assert two_exits.status_code == 422 and two_exits.json()["detail"]["code"] == "CS04"
    approval = attempt(["Manager approval", "Issue refund"], False)
    assert approval.status_code == 422 and approval.json()["detail"]["code"] == "CS06"

    draft_before = api.get(f"/workflows/{main}").json()["definition"]
    count_before = len(api.get(f"/projects/{P}/workflows").json())
    plan = step("dry run", attempt(["Issue refund"], True))
    assert plan["plan"]["call_node"] == "Pay refund" and plan["plan"]["trigger_node"] == "Inputs"
    assert api.get(f"/workflows/{main}").json()["definition"] == draft_before
    assert len(api.get(f"/projects/{P}/workflows").json()) == count_before

    done = step("commit", attempt(["Issue refund"], False))
    child = done["child_workflow_id"]
    assert done["child_version_number"] == 1 and done["call_version"] == "latest" and done["child_publish_errors"] == []
    parent_def = api.get(f"/workflows/{main}").json()["definition"]
    call = next(n for n in parent_def["nodes"] if n["name"] == "Pay refund")
    assert call["type"] == "ExecuteWorkflow" and call["parameters"]["workflowId"] == child and call["parameters"]["version"] == "latest"
    assert "Issue refund" not in {n["name"] for n in parent_def["nodes"]}
    assert [n["name"] for n in api.get(f"/workflows/{child}").json()["definition"]["nodes"]][:2] == ["Inputs", "Issue refund"]

    # ── 4. review what the move changed, publish, used-by, delete protection ────────────────────────────────────────
    report = step("tests after the move", api.post(f"/workflows/{main}/tests/run", {}))
    by_name = {r["name"]: r for r in report["results"]}
    assert by_name["A refund the manager rejects is not paid and the customer is told"]["status"] == "passed"
    needs_review = {r["id"] if "id" in r else r["test_id"] for r in report["results"] if r["status"] != "passed"}
    stale = {a["id"] for a in done["affected_tests"]}
    tests = api.get(f"/workflows/{main}").json()["definition"]["tests"]
    needs_review |= stale | {t["id"] for t in tests if t.get("stale")}
    for t in tests:
        if t["id"] not in needs_review:
            continue
        # the review: what used to be asserted on the payout request is now asserted on the call that makes it
        fixed = []
        for e in t["expect"]:
            if e.get("node") == "Pay refund" and e.get("subject") == "request":
                e = {"node": "Pay refund", "subject": "node", "op": "status", "value": "success"}
            fixed.append(e)
        t["expect"] = list({repr(sorted(e.items())): e for e in fixed}.values())
        if t.get("locked"):
            unlocked = step("unlock", api.post(f"/workflows/{main}/tests/{t['id']}/unlock", {"by": "reviewer"}))
            back = next(x for x in unlocked["definition"]["tests"] if x["id"] == t["id"])
            t.update({k: back.get(k) for k in ("status", "locked", "approved_by", "approved_at")})
    step("save reviewed tests", api.put(f"/workflows/{main}", {"tests": _merged(api, main, tests, needs_review)}))
    for tid in needs_review:
        step("re-approve", api.post(f"/workflows/{main}/tests/{tid}/approve", {"approved_by": "reviewer"}))
    final = step("tests again", api.post(f"/workflows/{main}/tests/run", {}))
    assert final["summary"]["failed"] == 0 and final["summary"]["errored"] == 0, final["summary"]
    assert not [t for t in api.get(f"/workflows/{main}").json()["definition"]["tests"] if t.get("stale")]
    step("publish the entry", api.post(f"/workflows/{main}/publish", {}))

    used = api.get(f"/workflows/{child}/used-by").json()
    assert {(u["parent_workflow_id"], u["node"], u["where"]) for u in used} == {(main, "Pay refund", "draft"), (main, "Pay refund", "published")}
    refused = api.delete(f"/workflows/{child}")
    assert refused.status_code == 409 and refused.json()["detail"]["code"] == "WORKFLOW_IN_USE"
    assert api.delete(f"/workflows/{notify}").status_code == 409, "customer-notify is called by the published entry too"

    # ── 5. a route that calls pricing that calls rate; release and promote ──────────────────────────────────────────
    rate = make(api, {"name": uniq("Rate lookup"), "project_id": P, "settings": {}, "nodes": [
        trigger(("amount", "number", False)), setf("Rate", rate="3")], "connections": [C("Inputs", "Rate")]})
    publish(api, rate)

    def pricing_body(tag: str, *fields):
        return {"name": uniq("Pricing"), "project_id": P, "settings": {}, "nodes": [
            trigger(*fields),
            N("Rate", "ExecuteWorkflow", {"workflowId": rate, "version": "latest", "inputMode": "passThrough"}, 200),
            setf("Fee", 400, fee=tag)], "connections": [C("Inputs", "Rate"), C("Rate", "Fee")]}

    pricing = make(api, pricing_body("v1", ("amount", "number", True)))
    publish(api, pricing)
    route = step("route", api.post(f"/projects/{P}/routes", {"method": "GET", "path": "/quote", "auth": "none",
                                                              "handler": {"type": "workflow", "create": True}}), 201)["route"]
    handler = route["handler"]["workflow_id"]

    def handler_body(inputs):
        return {"nodes": [N("Start", "Start"),
                          N("Price", "ExecuteWorkflow", {"workflowId": pricing, "version": "latest", "inputMode": "map", "inputs": inputs}, 200)],
                "connections": [C("Start", "Price")]}

    save(api, handler, handler_body({"amount": "{{ 100 }}"}))
    publish(api, handler)

    def release(expect=201):
        r = api.post(f"/projects/{P}/releases", {"environment": "dev"})
        assert r.status_code == expect, r.text
        return r.json() if expect == 201 else r.json()["detail"]

    def prod():
        r = api.raw("GET", f"/api/p/{slug}/quote")
        assert r.status_code == 200, r.text
        return r.json()

    r1 = release()
    assert r1["pins_subworkflows"] is True
    edges = {(e["parent_workflow_id"], e["child_workflow_id"]): e for e in api.get(f"/projects/{P}/releases/{r1['id']}").json()["snapshot"]["subworkflows"]}
    assert set(edges) == {(handler, pricing), (pricing, rate)}
    step("promote", api.post(f"/projects/{P}/releases/{r1['id']}/promote", {"to_environment": "prod"}), 201)
    assert prod()["fee"] == "v1"

    # ── 6. republish pricing with a new required input ──────────────────────────────────────────────────────────────
    save(api, pricing, pricing_body("v2", ("amount", "number", True), ("currency", "string", True)))
    impact = step("publish pricing v2", api.post(f"/workflows/{pricing}/publish", {}))["impact"]
    assert [i["parent_workflow_id"] for i in impact] == [handler]
    assert impact[0]["breaking"] and impact[0]["breaking"][0]["code"] == "WF024" and "currency" in impact[0]["breaking"][0]["message"]

    # ── 7. prod still answers from the pinned version ───────────────────────────────────────────────────────────────
    assert prod()["fee"] == "v1"

    # ── 8. re-cutting is refused and the release is blocked ─────────────────────────────────────────────────────────
    refused = release(expect=422)
    assert refused["code"] == "checks_failed"
    bad = next(c for c in refused["checks"] if c["id"] == "subworkflow_contracts_compatible")
    assert bad["status"] == "fail" and "currency" in bad["message"]
    assert api.get(f"/projects/{P}/releases/{refused['release_id']}").json()["status"] == "blocked"
    assert prod()["fee"] == "v1"

    # ── 9. map the new input, re-cut, promote ───────────────────────────────────────────────────────────────────────
    save(api, handler, handler_body({"amount": "{{ 100 }}", "currency": "{{ 'EUR' }}"}))
    publish(api, handler)
    r2 = release()
    assert next(e for e in api.get(f"/projects/{P}/releases/{r2['id']}").json()["snapshot"]["subworkflows"]
                if e["child_workflow_id"] == pricing)["child_version_number"] == 2
    step("promote v2", api.post(f"/projects/{P}/releases/{r2['id']}/promote", {"to_environment": "prod"}), 201)
    assert prod()["fee"] == "v2"

    # ── 10. the execution tree ──────────────────────────────────────────────────────────────────────────────────────
    roots = api.get(f"/executions?workflow_id={handler}").json()
    assert roots, "the route runs left a root execution"
    root = max(roots, key=lambda e: e["start_time"])
    d_root = api.get(f"/executions/{root['id']}").json()
    [k1] = d_root["children"]
    assert (k1["workflow_id"], k1["node"], k1["status"]) == (pricing, "Price", "success") and k1["version_number"] == 2
    d1 = api.get(f"/executions/{k1['execution_id']}").json()
    assert d1["parent"]["execution_id"] == root["id"] and d1["depth"] == 1
    [k2] = d1["children"]
    assert (k2["workflow_id"], k2["node"], k2["version_number"]) == (rate, "Rate", 1)
    d2 = api.get(f"/executions/{k2['execution_id']}").json()
    assert d2["parent"]["execution_id"] == k1["execution_id"] and d2["root_execution_id"] == root["id"] and d2["depth"] == 2


def _merged(api, wid: str, edited: list[dict], touched: set[str]) -> list[dict]:
    """The workflow's full test list with the reviewed tests swapped in (an ordinary save may not change a locked test)."""
    out = []
    for t in api.get(f"/workflows/{wid}").json()["definition"]["tests"]:
        if t["id"] in touched:
            t = next(e for e in edited if e["id"] == t["id"])
            t = {k: v for k, v in t.items() if k not in ("stale", "stale_reason")}
        out.append(t)
    return out
