"""Stage 8 T1 (tester): the sub-workflow runtime over HTTP on real Postgres, tried the way a hostile or careless author would.

Cycles through three workflows, the depth limit, the timeout, every ``on_error`` mode with a failing child, ``mode: each`` with
mixed child success, child lineage (three levels), cascade delete, cross-project calls (DESIGN-STAGE8-REUSE sections 2.3,
3.3-3.6, 5, 11, 14.2)."""

from __future__ import annotations

import pytest

from ._stage8_t1_support import (
    C, N, call_node, chain_workflow, check, child_body, child_rows, codes, detail, executions, failing_child, get, make,
    new_project, parent_body, publish, rows_parent, run, save, setf, trigger, uniq,
)

pytestmark = pytest.mark.integration

ROWS = {"rows": [{"n": 1}, {"n": 2}, {"n": 3}]}


def put(api, wid: str, body: dict) -> None:
    r = api.put(f"/workflows/{wid}", {"nodes": body["nodes"], "connections": body["connections"]})
    assert r.status_code == 200, r.text


def rows_of(db, wid: str) -> list[tuple]:
    return db.query("SELECT id, mode, status, depth, parent_execution_id, root_execution_id FROM executions "
                    "WHERE workflow_id = %s ORDER BY start_time, id", (wid,))


# ── cycles ──────────────────────────────────────────────────────────────────────────────────────────────────────────


def three_cycle(api, version="draft"):
    """A -> B -> C -> A, built so that the closing edge is the last save."""
    c = make(api, chain_workflow(uniq("s8t1-C"), None))
    b = make(api, chain_workflow(uniq("s8t1-B"), c, version))
    a = make(api, chain_workflow(uniq("s8t1-A"), b, version))
    put(api, c, chain_workflow("ignored", a, version))                       # the second edge of the cycle closes it
    return a, b, c


def test_a_cycle_through_three_workflows_is_flagged_on_every_member_by_the_checker(api):
    a, b, c = three_cycle(api)
    for wid in (a, b, c):
        found = [f for f in check(api, wid) if f["code"] == "WF020"]
        assert found and all(f["severity"] == "error" and f["node"] == "call" for f in found), (wid, found)
    msg = next(f["message"] for f in check(api, c) if f["code"] == "WF020")
    assert "→" in msg or "->" in msg                                          # the message names the whole path


def test_a_workflow_that_only_calls_into_a_cycle_is_not_on_it(api):
    a, b, c = three_cycle(api)
    d = make(api, chain_workflow(uniq("s8t1-D"), a))
    assert "WF020" not in codes(check(api, d))                                  # D is not in the strongly connected component
    assert "WF020" in codes(check(api, a))


def test_a_cycle_member_cannot_be_published(api):
    a, b, c = three_cycle(api, "latest")                                       # 'latest' pins need published children, so publish bottom-up first
    # c now calls a with 'latest' while a was never published: the publish is refused either way, but the cycle is named
    r = publish(api, c, expect=422)
    assert "WF020" in {f["code"] for f in r.json()["detail"]["findings"]}


def test_running_a_draft_cycle_stops_at_the_call_stack_guard_not_at_depth_ten(api, test_db):
    a, b, c = three_cycle(api)
    out = run(api, a)
    assert out["status"] == "failed"
    err = out["errors"][0]
    assert err["node_name"] == "call" and err["error_code"] == "SUBWORKFLOW_FAILED"
    assert "can't call itself" in err["error"]                                  # the guard's message travelled up through B and C
    for wid in (a, b, c):
        assert get(api, wid)["name"] in err["error"]
    # B ran at depth 1, C at depth 2; A was refused before a row existed
    [(_, mode_b, status_b, depth_b, parent_b, root_b)] = rows_of(test_db, b)
    [(_, mode_c, status_c, depth_c, parent_c, root_c)] = rows_of(test_db, c)
    assert (mode_b, status_b, depth_b) == ("sub_workflow", "failed", 1) and (mode_c, status_c, depth_c) == ("sub_workflow", "failed", 2)
    assert root_b == root_c == parent_b and parent_c is not None
    assert len(rows_of(test_db, a)) == 1                                       # only the root run of A


def test_a_workflow_calling_itself_is_flagged_and_refused_at_run(api, test_db):
    wid = make(api, chain_workflow(uniq("s8t1-self"), None))
    put(api, wid, chain_workflow("x", wid))
    f = [f for f in check(api, wid) if f["code"] == "WF020"]
    assert f and f[0]["node"] == "call"
    out = run(api, wid)
    assert out["status"] == "failed" and "can't call itself" in out["errors"][0]["error"]
    assert len(rows_of(test_db, wid)) == 1


# ── depth ───────────────────────────────────────────────────────────────────────────────────────────────────────────


def test_a_chain_deeper_than_ten_stops_at_the_limit_and_the_checker_warns(api, test_db):
    ids = [make(api, chain_workflow(uniq("s8t1-d12"), None))]
    for _ in range(11):                                                         # W0 calls W1 ... W10 calls W11 (the leaf)
        ids.append(make(api, chain_workflow(uniq("s8t1-dN"), ids[-1])))
    root = ids[-1]                                                              # a root with eleven children below it
    wf028 = [f for f in check(api, root) if f["code"] == "WF028"]
    assert wf028 and wf028[0]["severity"] == "warning"

    out = run(api, root)
    assert out["status"] == "failed"
    assert "10" in out["errors"][0]["error"] and "deep" in out["errors"][0]["error"]
    assert out["errors"][0]["error_code"] == "SUBWORKFLOW_FAILED"
    depths = sorted(r[0] for r in test_db.query(
        "SELECT depth FROM executions WHERE root_execution_id = %s", (out["execution_id"],)))
    assert depths == list(range(1, 11))                                         # depth 10 ran; the eleventh call never started
    assert test_db.query("SELECT count(*) FROM executions WHERE workflow_id = %s", (ids[0],))[0][0] == 0   # the leaf never ran


def test_a_chain_of_exactly_ten_levels_runs(api, test_db):
    ids = [make(api, chain_workflow(uniq("s8t1-leaf"), None, tag="bottom"))]
    for _ in range(10):
        ids.append(make(api, chain_workflow(uniq("s8t1-link"), ids[-1])))
    out = run(api, ids[-1])
    assert out["status"] == "success", out["errors"]
    assert "WF028" not in codes(check(api, ids[-1]))
    assert test_db.query("SELECT max(depth) FROM executions WHERE root_execution_id = %s", (out["execution_id"],))[0][0] == 10


# ── timeout ─────────────────────────────────────────────────────────────────────────────────────────────────────────


def slow_child(seconds: int = 30) -> dict:
    return {"name": uniq("s8t1-slow"), "settings": {}, "nodes": [
        trigger(("x", "string", False)), N("Nap", "Wait", {"unit": "seconds", "duration": seconds}, 200), setf("Done", 400, finished="yes")],
        "connections": [C("Inputs", "Nap"), C("Nap", "Done")]}


@pytest.mark.parametrize("on_error", ["stop", "continue", "error_output"])
def test_a_slow_child_times_out_and_every_on_error_mode_sees_the_same_failure(api, test_db, on_error):
    slow = make(api, slow_child())
    nodes = [N("Start", "Start"), call_node("call", slow, "draft", on_error=None if on_error == "stop" else on_error, timeoutSeconds=1),
             setf("After", 400, seen="yes"), setf("Handler", 400, handled="yes")]
    conns = [C("Start", "call"), C("call", "After")]
    if on_error == "error_output":
        conns.append(C("call", "Handler", "error"))
    parent = make(api, {"name": uniq("s8t1-to"), "nodes": nodes, "connections": conns, "settings": {}})
    import time
    t0 = time.monotonic()
    out = run(api, parent)
    assert time.monotonic() - t0 < 10, "the 30 second nap must be cancelled, not awaited"

    [(cid, mode, status, depth, _, _)] = rows_of(test_db, slow)
    assert (mode, status, depth) == ("sub_workflow", "failed", 1)
    assert test_db.query("SELECT cancelled_at IS NOT NULL FROM executions WHERE id = %s", (cid,)) == [(True,)]
    if on_error == "error_output":
        assert out["status"] == "success" and out["errors"] == []
        handled = out["data"]["Handler"][0]["json"]
        assert handled["code"] == "SUBWORKFLOW_TIMEOUT" and handled["child_execution_id"] == cid
        assert "After" not in out["data"]
    else:
        assert out["errors"][0]["error_code"] == "SUBWORKFLOW_TIMEOUT"
        assert out["errors"][0]["details"]["child_execution_id"] == cid
        assert ("After" in out["data"]) is (on_error == "continue")
    assert detail(api, cid)["status"] == "failed"


def test_the_timeout_is_capped_by_the_parents_remaining_time(api, test_db):
    slow = make(api, slow_child())
    parent_def = {"name": uniq("s8t1-cap"), "settings": {"max_execution_time": 1},
                  "nodes": [N("Start", "Start"), call_node("call", slow, "draft", timeoutSeconds=3000)],
                  "connections": [C("Start", "call")]}
    parent = make(api, parent_def)
    import time
    t0 = time.monotonic()
    out = run(api, parent)
    assert time.monotonic() - t0 < 12
    assert out["status"] == "failed"
    assert out["errors"][0]["error_code"] in ("SUBWORKFLOW_TIMEOUT", "EXECUTION_TIMEOUT") or "time" in out["errors"][0]["error"].lower()


# ── a failing child under each on_error mode, and mode: each with mixed results ─────────────────────────────────────


@pytest.fixture
def gate(api):
    cid = make(api, failing_child())
    publish(api, cid)
    return cid


def test_each_stop_halts_at_the_first_failing_item_and_names_the_child_run(api, test_db, gate):
    parent = make(api, rows_parent(gate))
    out = run(api, parent, ROWS)
    assert out["status"] == "failed" and "After" not in out["data"]
    err = out["errors"][0]
    assert err["error_code"] == "SUBWORKFLOW_FAILED" and err["details"]["child_node"] == "Boom"
    assert "n too big: 2" in err["error"]
    kids = rows_of(test_db, gate)
    assert [k[2] for k in kids] == ["success", "failed"]                        # item 1 ran and passed, item 2 failed, item 3 never started
    assert err["details"]["child_execution_id"] == kids[1][0]


def test_each_error_output_splits_successes_from_failures_per_item(api, test_db, gate):
    parent = make(api, rows_parent(gate, on_error="error_output", with_handler=True))
    out = run(api, parent, ROWS)
    assert out["status"] == "success" and out["errors"] == []
    assert [i["json"]["n"] for i in out["data"]["After"]] == [1]                  # main: only the item whose child succeeded
    handled = out["data"]["Handler"]
    assert [i["json"]["n"] for i in handled] == [2, 3]                            # error port: the two that failed, in input order
    assert {i["json"]["code"] for i in handled} == {"SUBWORKFLOW_FAILED"} and {i["json"]["failed_node"] for i in handled} == {"Boom"}
    kids = rows_of(test_db, gate)
    assert [k[2] for k in kids] == ["success", "failed", "failed"]
    assert [i["json"]["child_execution_id"] for i in handled] == [kids[1][0], kids[2][0]]   # each error links to its own child run
    assert all("n too big" in i["json"]["error"]["message"] for i in handled)


def test_each_continue_lets_the_run_go_on_past_the_failed_child(api, test_db, gate):
    parent = make(api, rows_parent(gate, on_error="continue"))
    out = run(api, parent, ROWS)
    assert "After" in out["data"]                                                # the run went on
    assert out["errors"][0]["error_code"] == "SUBWORKFLOW_FAILED"                # and still records the failure


@pytest.mark.xfail(strict=True, raises=AssertionError, reason="DEFECT S8-1: on_error=continue on ExecuteWorkflow emits only {error,_errorNode}, not the input items "
                   "with `error` added (DESIGN-STAGE8-REUSE 3.6); src/engine/workflow_runner.py:930-933 is the generic branch and "
                   "ExecuteWorkflow has no continue handling of its own")
def test_continue_keeps_the_input_items_and_adds_the_error(api, gate):
    parent = make(api, rows_parent(gate, mode="once", on_error="continue"))
    out = run(api, parent, {"rows": [{"n": 5}]})
    after = out["data"]["After"][0]["json"]
    assert "error" in after and after.get("n") == 5, after      # the incoming item's fields survive next to the error


def test_once_mode_with_a_failing_child_and_retries_makes_one_child_row_per_attempt(api, test_db, gate):
    body = rows_parent(gate, mode="once", name=uniq("s8t1-retry"))
    body["nodes"][2]["retry_on_fail"] = 2
    body["nodes"][2]["retry_delay"] = 10
    body["nodes"][2]["parameters"]["inputs"] = {"n": "{{ 5 }}"}
    parent = make(api, body)
    out = run(api, parent, ROWS)
    assert out["status"] == "failed"
    assert len(rows_of(test_db, gate)) == 3                                      # the first try plus two retries, each its own execution


def test_each_over_no_items_emits_nothing_and_starts_no_child(api, test_db, gate):
    parent = make(api, rows_parent(gate))
    out = run(api, parent, {"rows": []})
    assert out["status"] == "success" and rows_of(test_db, gate) == []
    assert "After" not in out["data"] or out["data"]["After"] in (None, [])


def test_a_child_that_continues_past_its_own_error_is_a_success_for_the_parent(api, test_db):
    kid = {"name": uniq("s8t1-soft"), "settings": {}, "nodes": [
        trigger(("n", "number", False)),
        {**N("Risky", "HttpRequest", {"method": "GET", "url": "http://127.0.0.1:1/nothing", "responseType": "json"}, 200), "on_error": "continue"},
        setf("After", 400, reached="yes")], "connections": [C("Inputs", "Risky"), C("Risky", "After")]}
    cid = make(api, kid)
    publish(api, cid)
    parent = make(api, parent_body(cid, "latest"))
    out = run(api, parent)
    assert out["status"] == "success", out["errors"]
    assert out["data"]["call"][0]["json"]["reached"] == "yes"
    # (the child's own row keeps the top-level rule: a run that recorded an error, continued or not, is "failed")
    assert len(rows_of(test_db, cid)) == 1


def test_a_failing_child_input_contract_fails_the_call_before_any_child_node_runs(api, test_db):
    kid = make(api, child_body("v1", ("amount", "number", True)))
    publish(api, kid)
    parent = make(api, parent_body(kid, "latest", mode="map", inputs={"amount": "{{ 'not a number' }}"}))
    out = run(api, parent)
    assert out["status"] == "failed"
    assert out["errors"][0]["details"]["child_error_code"] == "SUBWORKFLOW_INPUT_INVALID"
    assert "amount" in out["errors"][0]["error"]
    data = test_db.query("SELECT node_name FROM node_outputs WHERE execution_id = %s", (rows_of(test_db, kid)[0][0],))
    assert "Mark" not in {r[0] for r in data}


# ── lineage over three levels ───────────────────────────────────────────────────────────────────────────────────────


def tree(api):
    gc = make(api, chain_workflow(uniq("s8t1-grand"), None, tag="grand"))
    publish(api, gc)
    ch = make(api, chain_workflow(uniq("s8t1-child"), gc, "latest"))
    publish(api, ch)
    root = make(api, chain_workflow(uniq("s8t1-root"), ch, "latest"))
    publish(api, root)
    return root, ch, gc


def test_the_execution_tree_is_root_child_grandchild_with_links_both_ways(api, test_db):
    root, ch, gc = tree(api)
    out = run(api, root)
    assert out["status"] == "success", out["errors"]
    rid = out["execution_id"]
    d_root = detail(api, rid)
    assert (d_root["parent"], d_root["root_execution_id"], d_root["depth"]) == (None, rid, 0)
    [k1] = d_root["children"]
    assert (k1["workflow_id"], k1["node"], k1["version_number"], k1["status"]) == (ch, "call", 1, "success")
    d_child = detail(api, k1["execution_id"])
    assert d_child["parent"]["execution_id"] == rid and d_child["parent"]["workflow_id"] == root and d_child["parent"]["node"] == "call"
    assert (d_child["root_execution_id"], d_child["depth"]) == (rid, 1)
    [k2] = d_child["children"]
    assert k2["workflow_id"] == gc
    d_grand = detail(api, k2["execution_id"])
    assert d_grand["parent"]["execution_id"] == k1["execution_id"] and d_grand["root_execution_id"] == rid
    assert (d_grand["depth"], d_grand["children"]) == (2, [])

    # the list shows roots only unless asked; asking for the root's own children works by id
    assert {e["id"] for e in executions(api)} >= {rid} and not {k1["execution_id"], k2["execution_id"]} & {e["id"] for e in executions(api)}
    everything = {e["id"] for e in executions(api, include_children="true")}
    assert {rid, k1["execution_id"], k2["execution_id"]} <= everything
    assert detail(api, k1["execution_id"])["workflow_id"] == ch


def test_deleting_the_middle_run_takes_its_grandchild_and_unlinks_it_from_the_root(api, test_db):
    root, ch, gc = tree(api)
    rid = run(api, root)["execution_id"]
    [k1] = detail(api, rid)["children"]
    [k2] = detail(api, k1["execution_id"])["children"]
    r = api.delete(f"/executions/{k1['execution_id']}")
    assert r.status_code == 200, r.text
    assert test_db.query("SELECT 1 FROM executions WHERE id IN (%s, %s)", (k1["execution_id"], k2["execution_id"])) == []
    assert test_db.query("SELECT 1 FROM node_outputs WHERE execution_id IN (%s, %s)", (k1["execution_id"], k2["execution_id"])) == []
    assert detail(api, rid)["children"] == []
    assert api.get(f"/executions/{k2['execution_id']}").status_code == 404


def test_deleting_the_root_removes_all_three_levels_and_their_outputs(api, test_db):
    root, ch, gc = tree(api)
    rid = run(api, root)["execution_id"]
    ids = [r[0] for r in test_db.query("SELECT id FROM executions WHERE root_execution_id = %s OR id = %s", (rid, rid))]
    assert len(ids) == 3
    assert api.delete(f"/executions/{rid}").status_code == 200
    assert test_db.query("SELECT count(*) FROM executions WHERE id = ANY(%s)", (ids,))[0][0] == 0
    assert test_db.query("SELECT count(*) FROM node_outputs WHERE execution_id = ANY(%s)", (ids,))[0][0] == 0


def test_a_child_row_carries_the_parents_project_and_environment(api, test_db):
    P, _ = new_project(api, "s8t1lin")
    kid = make(api, {**child_body("one"), "project_id": P})
    publish(api, kid)
    parent = make(api, {**parent_body(kid, "latest"), "project_id": P})
    out = run(api, parent)
    [(pid, env)] = test_db.query("SELECT project_id, environment FROM executions WHERE workflow_id = %s", (kid,))
    [(ppid, penv)] = test_db.query("SELECT project_id, environment FROM executions WHERE id = %s", (out["execution_id"],))
    assert (pid, env) == (ppid, penv) and pid == P


# ── 404-hides-existence and cross-project calls ─────────────────────────────────────────────────────────────────────


def test_a_call_across_projects_is_flagged_refused_at_publish_and_looks_absent_at_run(api, test_db):
    P1, _ = new_project(api, "s8t1a")
    P2, _ = new_project(api, "s8t1b")
    stranger = make(api, {**child_body("secret"), "project_id": P2})
    publish(api, stranger)
    parent = make(api, {**parent_body(stranger, "latest"), "project_id": P1})
    f = [f for f in check(api, parent) if f["code"] == "WF027"]
    assert f and f[0]["severity"] == "error"
    refused = publish(api, parent, expect=422)
    assert "WF027" in {x["code"] for x in refused.json()["detail"]["findings"]}

    draft = make(api, {**parent_body(stranger, "draft"), "project_id": P1})
    out = run(api, draft)
    assert out["status"] == "failed"
    err = out["errors"][0]
    assert err["error_code"] == "SUBWORKFLOW_NOT_FOUND" and "isn't in this project" in err["error"]
    assert "secret" not in str(out["data"])                                      # nothing of the other project's workflow leaked
    assert rows_of(test_db, stranger) == []


def test_a_missing_child_and_another_projects_child_give_the_same_error(api):
    P1, _ = new_project(api, "s8t1c")
    P2, _ = new_project(api, "s8t1d")
    other = make(api, {**child_body("x"), "project_id": P2})
    publish(api, other)
    a = make(api, {**parent_body(other, "draft"), "project_id": P1})
    b = make(api, {**parent_body("wf_does_not_exist_zz", "draft"), "project_id": P1})
    ea, eb = run(api, a)["errors"][0], run(api, b)["errors"][0]
    assert ea["error_code"] == eb["error_code"] == "SUBWORKFLOW_NOT_FOUND"
    assert ea["error"].replace(other, "ID") == eb["error"].replace("wf_does_not_exist_zz", "ID")


def test_the_picker_lists_only_its_own_project_and_an_unknown_project_is_404(api):
    P1, _ = new_project(api, "s8t1e")
    P2, _ = new_project(api, "s8t1f")
    mine = make(api, {**child_body("m"), "project_id": P1})
    theirs = make(api, {**child_body("t"), "project_id": P2})
    publish(api, mine)
    publish(api, theirs)
    ids = {r["id"] for r in api.get(f"/projects/{P1}/subworkflows").json()}
    assert mine in ids and theirs not in ids
    assert api.get("/projects/proj_nope_zz/subworkflows").status_code == 404


# ── G6: the child resolves its own API spec, the parent has none ─────────────────────────────────────────────────────


SPEC = """openapi: 3.0.3
info: {title: Balances, version: "1"}
servers: [{url: "__BASE__"}]
paths:
  /v1/balance:
    get:
      operationId: getBalance
      responses:
        "200":
          description: ok
          content:
            application/json:
              schema: {type: object, required: [available], properties: {available: {type: number}}}
"""


def test_a_child_with_an_api_ref_node_resolves_its_spec_though_the_parent_has_none(api, test_db, upstream):
    P, _ = new_project(api, "s8t1g6")
    spec = api.post("/api-specs", {"project_id": P, "name": uniq("balances"), "spec_text": SPEC.replace("__BASE__", upstream)})
    assert spec.status_code == 200, spec.text
    kid = make(api, {"name": uniq("s8t1-bal"), "project_id": P, "settings": {}, "nodes": [
        trigger(("x", "string", False)),
        N("Balance", "HttpRequest", {"api_ref": {"spec_id": spec.json()["id"], "operation_id": "getBalance"}, "path_params": {}}, 200)],
        "connections": [C("Inputs", "Balance")]})
    publish(api, kid)
    parent = make(api, {**parent_body(kid, "latest"), "project_id": P})
    out = run(api, parent)
    assert out["status"] == "success", out["errors"]
    item = out["data"]["call"][0]["json"]
    assert item.get("statusCode") == 200 and item["body"]["available"] == 12840.25
    [(status,)] = test_db.query("SELECT status FROM executions WHERE workflow_id = %s", (kid,))
    assert status == "success"


# ── a Respond inside the child answers nobody ────────────────────────────────────────────────────────────────────────


def test_a_respond_node_in_the_child_never_answers_the_parents_http_caller(api):
    kid = make(api, {"name": uniq("s8t1-resp"), "settings": {}, "nodes": [
        trigger(("x", "string", False)), setf("Mark", 200, tag="kid"),
        N("Reply", "Respond", {"statusCode": 418, "body": {"from": "child"}}, 400)], "connections": [C("Inputs", "Mark"), C("Mark", "Reply")]})
    publish(api, kid)
    answers = {}
    for label, tail, conns in (("plain", [], []), ("own", [N("Final", "Respond", {"statusCode": 200, "body": {"from": "parent"}}, 600)], [C("call", "Final")])):
        hook = N("Hook", "Webhook", {"method": "POST", "responseMode": "lastNode", "path": uniq("s8t1rsp")})
        parent = make(api, {"name": uniq("s8t1-rp"), "settings": {}, "nodes": [hook, call_node("call", kid, "latest", x=300), *tail],
                            "connections": [C("Hook", "call"), *conns]})
        publish(api, parent)
        answers[label] = api.raw("POST", f"/webhook/{parent}", json={"x": "1"})
    assert answers["plain"].status_code == 200 and answers["plain"].json()["data"][0]["tag"] == "kid"   # the child's items, not its 418
    assert answers["own"].status_code == 200 and answers["own"].json() == {"from": "parent"}
