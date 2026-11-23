"""A Loop that stops after N failures in a row: the saved run (GET /executions/{id}) keeps the LOOP_STOPPED_EARLY code
and its details on the Loop's error, the same as the in-memory run (tests/unit/test_loop_history.py)."""
from __future__ import annotations


def _n(name, typ, params=None, i=0):
    return {"name": name, "type": typ, "parameters": params or {}, "position": {"x": i * 200, "y": 0}}


def _c(s, t, out="main"):
    return {"source_node": s, "target_node": t, "source_output": out, "target_input": "main"}


def test_stopped_early_code_survives_the_saved_run(api):
    wf = {"name": "stop early saved", "nodes": [
        _n("Start", "Start"),
        _n("Rows", "Set", {"mode": "manual", "keepOnlySet": True, "fields": [{"name": "ids", "value": "{{ [1, 2, 3, 4] }}"}]}, 1),
        _n("Split", "ItemLists", {"operation": "splitOut", "arrayField": "ids", "includeOther": False}, 2),
        _n("Each", "Loop", {"batchSize": 1, "onItemFailure": "skip", "stopAfterFailures": 2}, 3),
        _n("Fail", "HttpRequest", {"method": "GET", "url": "http://127.0.0.1:1/item/{{ $json.ids }}"}, 4),
        _n("Done", "Set", {"mode": "manual", "fields": [{"name": "x", "value": "1"}]}, 5)],
        "connections": [_c("Start", "Rows"), _c("Rows", "Split"), _c("Split", "Each"), _c("Each", "Fail", "loop"),
                        _c("Fail", "Each"), _c("Each", "Done", "done")]}
    r = api.post("/workflows", wf)
    assert r.status_code == 201, r.text
    run = api.post(f"/workflows/{r.json()['id']}/run", None).json()
    assert run["status"] == "failed", run
    e = api.get(f"/executions/{run['execution_id']}").json()
    loop_err = next((x for x in e["errors"] if x["node_name"] == "Each"), None)
    assert loop_err is not None, e["errors"]
    assert loop_err["error_code"] == "LOOP_STOPPED_EARLY", e["errors"]
    assert loop_err["details"]["failures_in_a_row"] == 2 and loop_err["details"]["last_failed_step"] == "Fail"
    assert e["loop_runs"]["Each"]["stoppedEarly"] is True
