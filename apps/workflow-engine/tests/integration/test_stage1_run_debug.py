"""Stage 1 (CONTRACTS-STAGE1 §1-4): node test, run-to, run-from, disabled, on_error, restore.

Written from the contract, over HTTP against the real engine + Postgres. Row counts in
``node_outputs`` are read straight from the database.
"""

from __future__ import annotations

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from .conftest import uniq

pytestmark = pytest.mark.integration

DEAD_URL = "http://127.0.0.1:9/"  # discard port: nothing listens -> connection refused


# ── helpers ──────────────────────────────────────────────────────────────────

def node(name: str, type_: str = "Set", params: dict | None = None, **extra) -> dict:
    return {"name": name, "type": type_, "parameters": params or {}, "position": {"x": 0, "y": 0}, **extra}


def sets(name: str, field: str, expr, **extra) -> dict:
    return node(name, "Set", {"mode": "manual", "fields": [{"name": field, "value": expr}], "keepOnlySet": False}, **extra)


def conn(a: str, b: str, out: str = "main") -> dict:
    return {"source_node": a, "target_node": b, "source_output": out, "target_input": "main"}


def chain_wf(extra_a: dict | None = None, extra_b: dict | None = None) -> dict:
    """Start -> A (n=1) -> B (m=n+1) -> C (k=m+1)."""
    return {"name": uniq("s1"), "settings": {},
            "nodes": [node("Start", "Start"), sets("A", "n", "{{ 1 }}", **(extra_a or {})),
                      sets("B", "m", "{{ $json.n + 1 }}", **(extra_b or {})), sets("C", "k", "{{ $json.m + 1 }}")],
            "connections": [conn("Start", "A"), conn("A", "B"), conn("B", "C")]}


def make(api, wf: dict) -> str:
    r = api.post("/workflows", wf)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def outputs(db, exec_id: str) -> list[str]:
    return sorted(r[0] for r in db.query("SELECT node_name FROM node_outputs WHERE execution_id = %s", (exec_id,)))


def trigger_of(db, exec_id: str) -> dict:
    rows = db.query("SELECT metadata FROM executions WHERE id = %s", (exec_id,))
    return rows[0][0]["trigger"]


def jsons(run: dict, name: str) -> list[dict]:
    """Item json of a node, without the trigger's own bookkeeping fields."""
    return [{k: v for k, v in i["json"].items() if k not in ("mode", "triggeredAt")} for i in run["data"][name]]


def full_run(api, wid: str) -> dict:
    r = api.post(f"/workflows/{wid}/run")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "success", r.text
    return r.json()


def new_table(api, columns: list[str]) -> tuple[str, str]:
    name = uniq("tbl").replace("-", "_")
    r = api.post("/projects/proj_default/tables", {"name": name, "columns": [{"name": c, "type": "string"} for c in columns]})
    assert r.status_code == 201, r.text
    return name, r.json()["id"]


def rows(api, tid: str) -> list[dict]:
    r = api.get(f"/projects/proj_default/tables/{tid}/rows")
    assert r.status_code == 200, r.text
    return [x["data"] for x in r.json()["rows"]]


def ins(name: str, table: str, data: dict) -> dict:
    return node(name, "DataTable", {"table": table, "operation": "insert", "data": data})


def failing_http(name: str = "Flaky", url: str = DEAD_URL, **extra) -> dict:
    return node(name, "HttpRequest", {"method": "GET", "url": url, "responseType": "json"}, **extra)


# ── 1.2 node test ────────────────────────────────────────────────────────────

def test_node_test_typed_input(api, test_db):
    wid = make(api, chain_wf())
    r = api.post(f"/workflows/{wid}/nodes/B/test", {"source": "input", "input_items": [{"n": 10}]})
    assert r.status_code == 200, r.text
    run = r.json()
    assert run["status"] == "success" and run["execution_id"].startswith("exec_") and run["errors"] == []
    assert set(run["data"]) == {"B"}
    assert jsons(run, "B") == [{"n": 10, "m": 11}]
    assert outputs(test_db, run["execution_id"]) == ["B"]
    assert trigger_of(test_db, run["execution_id"]) == {"type": "node_test", "node": "B"}
    ex = api.get(f"/executions/{run['execution_id']}").json()
    assert ex["mode"] == "manual" and set(ex["node_data"]) == {"B"}


def test_node_test_last_run_input(api, test_db):
    wid = make(api, chain_wf())
    full_run(api, wid)
    r = api.post(f"/workflows/{wid}/nodes/B/test", {"source": "last_run"})
    assert r.status_code == 200, r.text
    run = r.json()
    assert set(run["data"]) == {"B"}
    assert jsons(run, "B") == [{"n": 1, "m": 2}]  # A's output from the full run
    assert outputs(test_db, run["execution_id"]) == ["B"]
    assert trigger_of(test_db, run["execution_id"])["type"] == "node_test"


def test_node_test_pinned_input(api, test_db):
    wf = chain_wf(extra_a={"pinned_data": [{"json": {"n": 100}}]})
    wid = make(api, wf)
    r = api.post(f"/workflows/{wid}/nodes/B/test", {"source": "pinned"})
    assert r.status_code == 200, r.text
    run = r.json()
    assert jsons(run, "B") == [{"n": 100, "m": 101}]
    assert outputs(test_db, run["execution_id"]) == ["B"]


def test_node_test_pinned_after_update(api):
    wid = make(api, chain_wf())
    wf = chain_wf(extra_a={"pinned_data": [{"json": {"n": 7}}]})
    assert api.put(f"/workflows/{wid}", wf).status_code == 200
    r = api.post(f"/workflows/{wid}/nodes/B/test", {"source": "pinned"})
    assert r.status_code == 200 and jsons(r.json(), "B") == [{"n": 7, "m": 8}]


@pytest.mark.parametrize("source", ["last_run", "pinned"])
def test_node_test_409_without_upstream_data(api, source):
    wid = make(api, chain_wf())
    r = api.post(f"/workflows/{wid}/nodes/B/test", {"source": source})
    assert r.status_code == 409, r.text
    d = r.json()["detail"]
    assert isinstance(d["message"], str) and d["missing_nodes"] == ["A"]


def test_node_test_404_unknown_node(api):
    wid = make(api, chain_wf())
    assert api.post(f"/workflows/{wid}/nodes/Nope/test", {"source": "input", "input_items": []}).status_code == 404
    assert api.post(f"/workflows/wf_does_not_exist/nodes/B/test", {"source": "input", "input_items": []}).status_code == 404


def test_node_test_input_without_items_is_400(api):
    wid = make(api, chain_wf())
    r = api.post(f"/workflows/{wid}/nodes/B/test", {"source": "input"})
    assert r.status_code == 400, r.text
    assert "input_items" in json.dumps(r.json())


def test_node_test_on_trigger_needs_no_input(api, test_db):
    wid = make(api, chain_wf())
    r = api.post(f"/workflows/{wid}/nodes/Start/test", {"source": "last_run"})
    assert r.status_code == 200, r.text
    assert set(r.json()["data"]) == {"Start"}
    assert outputs(test_db, r.json()["execution_id"]) == ["Start"]


# ── 1.2 run-to ───────────────────────────────────────────────────────────────

def test_run_to_stops_at_node(api, test_db):
    wid = make(api, chain_wf())
    r = api.post(f"/workflows/{wid}/run-to/B")
    assert r.status_code == 200, r.text
    run = r.json()
    assert run["status"] == "success"
    assert set(run["data"]) == {"Start", "A", "B"}
    assert "C" not in run["data"]
    assert outputs(test_db, run["execution_id"]) == ["A", "B", "Start"]
    assert trigger_of(test_db, run["execution_id"]) == {"type": "run_to", "node": "B"}
    # explicit empty body is also accepted
    r2 = api.post(f"/workflows/{wid}/run-to/A", {})
    assert r2.status_code == 200 and set(r2.json()["data"]) == {"Start", "A"}
    assert api.post(f"/workflows/{wid}/run-to/Nope").status_code == 404


def test_run_to_branch_node_runs_neither_branch(api):
    wf = {"name": uniq("s1"), "settings": {},
          "nodes": [node("Start", "Start"), node("Gate", "If", {"condition": "{{ true }}"}),
                    sets("T", "t", "{{ 1 }}"), sets("F", "f", "{{ 1 }}")],
          "connections": [conn("Start", "Gate"), conn("Gate", "T", "true"), conn("Gate", "F", "false")]}
    wid = make(api, wf)
    r = api.post(f"/workflows/{wid}/run-to/Gate")
    assert r.status_code == 200, r.text
    assert "T" not in r.json()["data"] and "F" not in r.json()["data"] and "Gate" in r.json()["data"]


# ── 1.2 run-from ─────────────────────────────────────────────────────────────

def test_run_from_reruns_only_downstream(api, test_db):
    wid = make(api, chain_wf())
    first = full_run(api, wid)
    # change A after the run: run-from B must still use A's *saved* output (n=1), not recompute
    assert api.put(f"/workflows/{wid}", {**chain_wf(), "nodes": chain_wf()["nodes"]}).status_code == 200
    r = api.post(f"/workflows/{wid}/run-from/B", {"execution_id": first["execution_id"]})
    assert r.status_code == 200, r.text
    run = r.json()
    assert run["status"] == "success"
    assert run["execution_id"] != first["execution_id"]
    assert outputs(test_db, run["execution_id"]) == ["B", "C"]
    assert set(run["data"]) >= {"B", "C"} and "A" not in outputs(test_db, run["execution_id"])
    assert jsons(run, "C") == [{"n": 1, "m": 2, "k": 3}]
    assert trigger_of(test_db, run["execution_id"]) == {"type": "run_from", "node": "B",
                                                         "source_execution": first["execution_id"]}
    # from the last node only
    r = api.post(f"/workflows/{wid}/run-from/C", {"execution_id": first["execution_id"]})
    assert r.status_code == 200 and outputs(test_db, r.json()["execution_id"]) == ["C"]


def test_run_from_uses_saved_not_current_definition(api):
    wid = make(api, chain_wf())
    first = full_run(api, wid)
    wf = chain_wf()
    wf["nodes"][1] = sets("A", "n", "{{ 500 }}")  # A would now produce 500
    assert api.put(f"/workflows/{wid}", wf).status_code == 200
    r = api.post(f"/workflows/{wid}/run-from/B", {"execution_id": first["execution_id"]})
    assert r.status_code == 200, r.text
    assert jsons(r.json(), "B") == [{"n": 1, "m": 2}]


def test_run_from_default_skips_node_test_executions(api, test_db):
    wid = make(api, chain_wf())
    full = full_run(api, wid)
    t = api.post(f"/workflows/{wid}/nodes/B/test", {"source": "input", "input_items": [{"n": 40}]}).json()
    assert t["execution_id"] != full["execution_id"]
    r = api.post(f"/workflows/{wid}/run-from/C")  # no body at all
    assert r.status_code == 200, r.text
    run = r.json()
    assert trigger_of(test_db, run["execution_id"])["source_execution"] == full["execution_id"]
    assert jsons(run, "C") == [{"n": 1, "m": 2, "k": 3}]  # not the node test's m=41
    # empty object body behaves the same
    r = api.post(f"/workflows/{wid}/run-from/C", {})
    assert r.status_code == 200
    assert trigger_of(test_db, r.json()["execution_id"])["source_execution"] == full["execution_id"]


def test_run_from_409_when_ancestor_output_missing(api, test_db):
    wid = make(api, chain_wf())
    t = api.post(f"/workflows/{wid}/nodes/B/test", {"source": "input", "input_items": [{"n": 1}]}).json()
    for body in (None, {"execution_id": t["execution_id"]}):
        r = api.post(f"/workflows/{wid}/run-from/C", body)
        assert r.status_code == 409, r.text
        d = r.json()["detail"]
        assert isinstance(d["message"], str)
        assert {"Start", "A"} <= set(d["missing_nodes"]) and "B" not in d["missing_nodes"]


def test_run_from_409_with_explicit_run_to_execution(api):
    wid = make(api, chain_wf())
    part = api.post(f"/workflows/{wid}/run-to/A").json()  # has Start, A only
    r = api.post(f"/workflows/{wid}/run-from/C", {"execution_id": part["execution_id"]})
    assert r.status_code == 409 and r.json()["detail"]["missing_nodes"] == ["B"]


def test_run_from_404s(api):
    wid = make(api, chain_wf())
    full_run(api, wid)
    assert api.post(f"/workflows/{wid}/run-from/B", {"execution_id": "exec_does_not_exist"}).status_code == 404
    assert api.post(f"/workflows/{wid}/run-from/Nope", {}).status_code == 404
    empty = make(api, chain_wf())  # never ran
    assert api.post(f"/workflows/{empty}/run-from/B").status_code == 404


def merge_wf() -> dict:
    return {"name": uniq("s1"), "settings": {},
            "nodes": [node("Start", "Start"), sets("P", "p", "{{ 1 }}"), sets("Q", "q", "{{ 2 }}"),
                      node("Join", "Merge", {"mode": "append"}), sets("Z", "z", "{{ true }}")],
            "connections": [conn("Start", "P"), conn("Start", "Q"), conn("P", "Join"), conn("Q", "Join"),
                            conn("Join", "Z")]}


def test_run_from_merge_gets_both_inputs(api, test_db):
    wid = make(api, merge_wf())
    first = full_run(api, wid)
    assert len(first["data"]["Join"]) == 2
    r = api.post(f"/workflows/{wid}/run-from/Join", {"execution_id": first["execution_id"]})
    assert r.status_code == 200, r.text
    run = r.json()
    assert outputs(test_db, run["execution_id"]) == ["Join", "Z"]
    assert sorted(jsons(run, "Join"), key=lambda j: sorted(j)) == [{"p": 1}, {"q": 2}]
    assert len(run["data"]["Z"]) == 2


def test_node_test_merge_gets_both_inputs(api, test_db):
    wid = make(api, merge_wf())
    full_run(api, wid)
    r = api.post(f"/workflows/{wid}/nodes/Join/test", {"source": "last_run"})
    assert r.status_code == 200, r.text
    run = r.json()
    assert outputs(test_db, run["execution_id"]) == ["Join"]
    assert sorted(jsons(run, "Join"), key=lambda j: sorted(j)) == [{"p": 1}, {"q": 2}]


# ── 1.3 disabled ─────────────────────────────────────────────────────────────

def test_disabled_node_passes_multiple_items_through(api, test_db):
    wid = make(api, chain_wf(extra_b={"disabled": True}))
    items = [{"n": 1, "tag": "a"}, {"n": 2, "tag": "b"}, {"n": 3, "tag": "c"}]
    r = api.post(f"/workflows/{wid}/nodes/B/test", {"source": "input", "input_items": items})
    assert r.status_code == 200, r.text
    run = r.json()
    assert run["status"] == "success"
    assert jsons(run, "B") == items  # untouched: no "m" added
    saved = test_db.query("SELECT output FROM node_outputs WHERE execution_id = %s AND node_name = 'B'",
                          (run["execution_id"],))
    assert [i["json"] for i in saved[0][0]] == items


def test_disabled_node_in_full_run(api):
    wid = make(api, chain_wf(extra_b={"disabled": True}))
    run = full_run(api, wid)
    assert jsons(run, "B") == jsons(run, "A") == [{"n": 1}]
    # C computes m+1 from a missing m; the point is B did not add it
    assert "m" not in jsons(run, "B")[0]


def test_disabled_flag_survives_save_reload_and_publish(api):
    wid = make(api, chain_wf(extra_b={"disabled": True}))

    def flags(defn):
        return {n["name"]: bool(n.get("disabled", False)) for n in defn["nodes"]}
    assert flags(api.get(f"/workflows/{wid}").json()["definition"]) == {"Start": False, "A": False, "B": True, "C": False}
    # toggle off then on through PUT
    assert api.put(f"/workflows/{wid}", chain_wf()).status_code == 200
    assert flags(api.get(f"/workflows/{wid}").json()["definition"])["B"] is False
    disabled = chain_wf(extra_b={"disabled": True})
    # C must not read B's field: with B disabled `m` never exists, which the publish-time checker rightly rejects (WF002)
    disabled["nodes"][3] = sets("C", "k", "{{ $json.n + 1 }}")
    assert api.put(f"/workflows/{wid}", disabled).status_code == 200
    assert flags(api.get(f"/workflows/{wid}").json()["definition"])["B"] is True
    pub = api.post(f"/workflows/{wid}/publish", {"message": "d"}).json()
    v = api.get(f"/workflows/{wid}/versions/{pub['version_id']}").json()
    assert flags(v["definition"])["B"] is True


def test_disabled_trigger_is_400_on_save_run_and_publish(api, test_db):
    bad = chain_wf()
    bad["nodes"][0]["disabled"] = True
    msg = "Trigger node 'Start' is disabled"
    r = api.post("/workflows", bad)
    assert r.status_code == 400 and msg in json.dumps(r.json()), r.text
    wid = make(api, chain_wf())
    r = api.put(f"/workflows/{wid}", bad)
    assert r.status_code == 400 and r.json()["detail"] == msg, r.text
    # the rejected save did not change the stored draft
    assert not api.get(f"/workflows/{wid}").json()["definition"]["nodes"][0].get("disabled")
    # force a disabled trigger into the stored draft (as an older client could have), then run / publish
    defn = api.get(f"/workflows/{wid}").json()["definition"]
    defn["nodes"][0]["disabled"] = True
    test_db.query("UPDATE workflows SET draft_definition = %s::jsonb WHERE id = %s", (json.dumps(defn), wid))
    r = api.post(f"/workflows/{wid}/run")
    assert r.status_code == 400 and r.json()["detail"] == msg, r.text
    r = api.post(f"/workflows/{wid}/publish", {})
    assert r.status_code == 400 and r.json()["detail"] == msg, r.text


def test_disabled_trigger_adhoc_run_is_400(api):
    bad = chain_wf()
    bad["nodes"][0]["disabled"] = True
    r = api.post("/workflows/run-adhoc", bad)
    assert r.status_code == 400 and "Trigger node 'Start' is disabled" in json.dumps(r.json()), r.text


# ── 1.4 on_error ─────────────────────────────────────────────────────────────

def err_wf(on_error: str | None, table: str | None = None, main_table: str | None = None, **extra) -> dict:
    nodes = [node("Start", "Start"), sets("Prep", "order", "abc"),
             failing_http(**({"on_error": on_error} if on_error else {}), **extra), sets("After", "after", "{{ true }}")]
    conns = [conn("Start", "Prep"), conn("Prep", "Flaky"), conn("Flaky", "After")]
    if table:
        nodes.append(ins("ErrLog", table, {"message": "{{ $json.error.message }}", "node": "{{ $json.error.node }}",
                                           "orig": "{{ $json.order }}"}))
        conns.append(conn("Flaky", "ErrLog", "error"))
    if main_table:
        nodes[3] = ins("After", main_table, {"orig": "{{ $json.order }}"})
    return {"name": uniq("s1"), "settings": {}, "nodes": nodes, "connections": conns}


def test_on_error_default_stop(api):
    wid = make(api, err_wf(None))
    r = api.post(f"/workflows/{wid}/run")
    assert r.status_code == 200, r.text
    run = r.json()
    assert run["status"] == "failed"
    assert [e["node_name"] for e in run["errors"]] == ["Flaky"]
    assert "After" not in run["data"]


def test_on_error_stop_explicit(api):
    wid = make(api, err_wf("stop"))
    run = api.post(f"/workflows/{wid}/run").json()
    assert run["status"] == "failed" and run["errors"][0]["node_name"] == "Flaky"
    assert "After" not in run["data"]


def test_on_error_continue_keeps_running(api):
    wid = make(api, err_wf("continue"))
    run = api.post(f"/workflows/{wid}/run").json()
    # unchanged continue_on_fail behaviour: the error is recorded, the run keeps going
    assert [e["node_name"] for e in run["errors"]] == ["Flaky"]
    assert "After" in run["data"], run


def test_on_error_error_output_routes_to_error_branch(api, test_db):
    name, tid = new_table(api, ["message", "node", "orig"])
    mname, mtid = new_table(api, ["orig"])
    wid = make(api, err_wf("error_output", table=name, main_table=mname))
    r = api.post(f"/workflows/{wid}/run")
    assert r.status_code == 200, r.text
    run = r.json()
    assert run["status"] == "success"
    assert run["errors"] == []
    got = rows(api, tid)
    assert len(got) == 1
    assert got[0]["node"] == "Flaky" and got[0]["message"] and got[0]["orig"] == "abc"
    assert rows(api, mtid) == []  # main branch received nothing
    # node statuses: the failing node counts as success
    ex = api.get(f"/executions/{run['execution_id']}").json()
    assert ex["status"] == "success" and ex["errors"] == []
    # error item shape: the input item's json plus error{message,node}
    [item] = jsons(run, "Flaky")
    assert item["order"] == "abc" and item["error"]["node"] == "Flaky"
    assert isinstance(item["error"]["message"], str) and item["error"]["message"]
    assert set(item["error"]) == {"message", "node"}


def test_on_error_error_output_without_connection_drops_items(api):
    wid = make(api, err_wf("error_output"))
    run = api.post(f"/workflows/{wid}/run").json()
    assert run["status"] == "success" and run["errors"] == []
    assert not run["data"].get("After")


def test_on_error_error_output_multiple_items_each_get_error(api):
    name, tid = new_table(api, ["message", "node", "orig"])
    wf = err_wf("error_output", table=name)
    wid = make(api, wf)
    items = [{"order": "o1"}, {"order": "o2"}]
    r = api.post(f"/workflows/{wid}/nodes/Flaky/test", {"source": "input", "input_items": items})
    assert r.status_code == 200 and r.json()["status"] == "success" and r.json()["errors"] == [], r.text
    errs = [i["json"] for i in r.json()["data"]["Flaky"]]
    assert [e["order"] for e in errs] == ["o1", "o2"]  # one error item per failed input item
    assert all(e["error"]["node"] == "Flaky" and e["error"]["message"] for e in errs)
    assert rows(api, tid) == []  # a node test runs only that node: the error branch did not run


class _CountingDropper(BaseHTTPRequestHandler):
    hits = 0

    def do_GET(self):  # close without replying -> connection error
        type(self).hits += 1
        self.close_connection = True
        self.connection.shutdown(socket.SHUT_RDWR)

    def log_message(self, *a):
        pass


def test_on_error_retry_then_error_output(api):
    class H(_CountingDropper):
        hits = 0
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        name, tid = new_table(api, ["message", "node", "orig"])
        wf = err_wf("error_output", table=name)
        wf["nodes"][2] = failing_http(url=f"http://127.0.0.1:{srv.server_address[1]}/x", on_error="error_output",
                                      retry_on_fail=2, retry_delay=10)
        wid = make(api, wf)
        run = api.post(f"/workflows/{wid}/run").json()
        assert run["status"] == "success" and run["errors"] == []
        assert H.hits == 3  # 1 try + 2 retries, then (and only then) the error branch
        got = rows(api, tid)
        assert len(got) == 1 and got[0]["node"] == "Flaky"
    finally:
        srv.shutdown()


def test_legacy_continue_on_fail_loads_as_continue_and_is_not_written_back(api):
    wf = err_wf(None)
    wf["nodes"][2]["continue_on_fail"] = True
    wid = make(api, wf)
    n = next(x for x in api.get(f"/workflows/{wid}").json()["definition"]["nodes"] if x["name"] == "Flaky")
    assert n["on_error"] == "continue"
    assert not n.get("continue_on_fail")
    run = api.post(f"/workflows/{wid}/run").json()
    assert [e["node_name"] for e in run["errors"]] == ["Flaky"] and "After" in run["data"], run
    # a new-style value wins over the legacy flag
    wf2 = err_wf("stop")
    wf2["nodes"][2]["continue_on_fail"] = True
    wid2 = make(api, wf2)
    n2 = next(x for x in api.get(f"/workflows/{wid2}").json()["definition"]["nodes"] if x["name"] == "Flaky")
    assert n2["on_error"] == "stop"


def test_on_error_default_is_stop_when_unset(api):
    wid = make(api, err_wf(None))
    n = next(x for x in api.get(f"/workflows/{wid}").json()["definition"]["nodes"] if x["name"] == "Flaky")
    assert n.get("on_error", "stop") == "stop"


def test_on_error_invalid_value_rejected(api):
    wf = err_wf("bogus")
    assert api.post("/workflows", wf).status_code in (400, 422)


# ── 1.7 restore ──────────────────────────────────────────────────────────────

def test_restore_version_copies_definition_into_draft(api):
    wid = make(api, chain_wf())
    pub = api.post(f"/workflows/{wid}/publish", {"message": "v1"})
    assert pub.status_code == 200
    v1 = pub.json()["version_id"]
    v1_def = api.get(f"/workflows/{wid}/versions/{v1}").json()["definition"]
    draft_at_v1 = api.get(f"/workflows/{wid}").json()["definition"]
    edited = chain_wf()
    edited["name"] = draft_at_v1["name"]
    edited["nodes"][3] = sets("C", "k", "{{ 999 }}")
    edited["nodes"].append(sets("Extra", "e", "{{ 1 }}"))
    edited["connections"].append(conn("C", "Extra"))
    assert api.put(f"/workflows/{wid}", edited).status_code == 200
    draft = api.get(f"/workflows/{wid}").json()["definition"]
    assert draft != v1_def and len(draft["nodes"]) == 5

    r = api.post(f"/workflows/{wid}/versions/{v1}/restore")
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) >= {"id", "name", "active", "webhook_url", "definition", "created_at", "updated_at"}
    assert body["id"] == wid
    assert body["definition"] == draft_at_v1  # exactly what GET showed when v1 was published

    def core(d):
        return ({n["name"]: (n["type"], n["parameters"], n.get("disabled", False), n.get("on_error", "stop"))
                 for n in d["nodes"]}, d["connections"])
    assert core(body["definition"]) == core(v1_def)
    assert api.get(f"/workflows/{wid}").json()["definition"] == body["definition"]
    # the restored draft runs like v1
    assert jsons(full_run(api, wid), "C") == [{"n": 1, "m": 2, "k": 3}]
    # publishing history is untouched
    assert len(api.get(f"/workflows/{wid}/versions").json()) == 1


def test_restore_version_404s(api):
    wid = make(api, chain_wf())
    other = make(api, chain_wf())
    ov = api.post(f"/workflows/{other}/publish", {}).json()["version_id"]
    api.post(f"/workflows/{wid}/publish", {})
    assert api.post(f"/workflows/{wid}/versions/999999/restore").status_code == 404
    assert api.post(f"/workflows/{wid}/versions/{ov}/restore").status_code == 404  # another workflow's version
    assert api.post(f"/workflows/wf_does_not_exist/versions/{ov}/restore").status_code == 404
