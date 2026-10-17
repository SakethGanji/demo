"""Stage 1 demo story over HTTP: a failed refund run is debugged, fixed, resumed and rolled back.

Uses tests/fixtures/workflows/refund_triage.json (HttpRequest url placeholder __REFUND_API_URL__)
and the stdlib fake in tests/fake_refund_api.py as the working risk API.
"""

from __future__ import annotations

import json
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from ..fake_refund_api import Handler
from .conftest import uniq

pytestmark = pytest.mark.integration

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "workflows" / "refund_triage.json"
PLACEHOLDER = "__REFUND_API_URL__"
FAILING_URL = "http://127.0.0.1:9/ok"  # nothing listens on the discard port: connection refused


@pytest.fixture(scope="module")
def risk_api():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}"
    finally:
        srv.shutdown()


def _workflow(url: str) -> dict:
    wf = json.loads(FIXTURE.read_text().replace(PLACEHOLDER, url))
    wf["name"] = uniq("refund-triage")
    wf["settings"] = {}
    for n in wf["nodes"]:  # unique webhook path so a repeated run of the suite can't collide
        if n["type"] == "Webhook":
            n["parameters"]["path"] = uniq("refund")
    return wf


def _node(defn: dict, name: str) -> dict:
    return next(n for n in defn["nodes"] if n["name"] == name)


def _node_rows(db, exec_id: str) -> list[str]:
    return sorted(r[0] for r in db.query("SELECT node_name FROM node_outputs WHERE execution_id = %s", (exec_id,)))


def test_refund_triage_debug_journey(api, test_db, risk_api):
    # 1. the refunds data table
    r = api.post("/projects/proj_default/tables", {"name": "refunds", "columns": [
        {"name": "order_id", "type": "string"}, {"name": "amount", "type": "number"},
        {"name": "reason", "type": "string"}, {"name": "status", "type": "string"}]})
    if r.status_code == 409:  # another run of the suite in the same DB already made it: use a clean one
        existing = next(t for t in api.get("/projects/proj_default/tables").json() if t["name"] == "refunds")
        assert api.delete(f"/projects/proj_default/tables/{existing['id']}").status_code == 200
        r = api.post("/projects/proj_default/tables", {"name": "refunds", "columns": [
            {"name": "order_id", "type": "string"}, {"name": "amount", "type": "number"},
            {"name": "reason", "type": "string"}, {"name": "status", "type": "string"}]})
    assert r.status_code == 201, r.text
    tid = r.json()["id"]
    assert r.json()["name"] == "refunds"

    def table_rows() -> list[dict]:
        rr = api.get(f"/projects/proj_default/tables/{tid}/rows")
        assert rr.status_code == 200, rr.text
        return [x["data"] for x in rr.json()["rows"]]
    assert table_rows() == []

    # 2. create the workflow with a failing risk-API url
    wf = _workflow(FAILING_URL)
    r = api.post("/workflows", wf)
    assert r.status_code == 201, r.text
    wid = r.json()["id"]
    assert set(r.json()) >= {"id", "name", "active", "webhook_url", "created_at"}
    detail = api.get(f"/workflows/{wid}").json()
    assert _node(detail["definition"], "Risk API")["parameters"]["url"] == FAILING_URL

    # 3. publish v1
    r = api.post(f"/workflows/{wid}/publish", {"message": "v1 failing url"})
    assert r.status_code == 200, r.text
    v1 = r.json()["version_id"]
    assert r.json()["active"] is True and isinstance(v1, int)

    # 4. run with amount 900: the Risk API node fails
    body = {"input_data": {"order_id": "A-1001", "amount": 900, "reason": "damaged"}}  # wrapped as the webhook body
    r = api.post(f"/workflows/{wid}/run", body)
    assert r.status_code == 200, r.text
    failed = r.json()
    assert failed["status"] == "failed"
    assert [e["node_name"] for e in failed["errors"]] == ["Risk API"]
    assert failed["errors"][0]["error"]
    assert {"Webhook", "Normalize", "Amount over 500"} <= set(failed["data"])
    assert "Log escalated" not in failed["data"] and "Log approved" not in failed["data"]
    assert table_rows() == []

    # 5. GET the execution: the nodes before the failure keep their data
    r = api.get(f"/executions/{failed['execution_id']}")
    assert r.status_code == 200, r.text
    ex = r.json()
    assert ex["status"] == "failed" and ex["workflow_id"] == wid and ex["mode"] == "manual"
    assert [e["node_name"] for e in ex["errors"]] == ["Risk API"]
    assert ex["node_data"]["Normalize"][0]["json"] == {"order_id": "A-1001", "amount": 900, "reason": "damaged"}
    assert ex["node_data"]["Amount over 500"], "the If node's output should have been saved"
    assert "Log escalated" not in ex["node_data"]

    # 6. fix the url in the draft (PUT)
    fixed = _workflow(risk_api + "/ok")
    fixed["name"] = wf["name"]
    for n_old, n_new in zip(wf["nodes"], fixed["nodes"]):
        if n_old["type"] == "Webhook":
            n_new["parameters"]["path"] = n_old["parameters"]["path"]
    r = api.put(f"/workflows/{wid}", fixed)
    assert r.status_code == 200, r.text
    assert _node(r.json()["definition"], "Risk API")["parameters"]["url"] == risk_api + "/ok"

    # 7. test only that step, input taken from the failed run's output of the node before it
    r = api.post(f"/workflows/{wid}/nodes/Risk API/test", {"source": "last_run"})
    assert r.status_code == 200, r.text
    step = r.json()
    assert step["status"] == "success" and step["errors"] == []
    assert set(step["data"]) == {"Risk API"}
    out = step["data"]["Risk API"][0]["json"]
    assert "ok" in json.dumps(out)  # the fake's /ok reply made it into the item
    assert _node_rows(test_db, step["execution_id"]) == ["Risk API"]
    meta = test_db.query("SELECT metadata FROM executions WHERE id = %s", (step["execution_id"],))[0][0]
    assert meta["trigger"] == {"type": "node_test", "node": "Risk API"}
    assert table_rows() == []  # downstream untouched

    # 8. run from that node, no execution_id: skips the node-test execution and reuses the failed run
    r = api.post(f"/workflows/{wid}/run-from/Risk API")
    assert r.status_code == 200, r.text
    resumed = r.json()
    assert resumed["status"] == "success" and resumed["errors"] == []
    assert _node_rows(test_db, resumed["execution_id"]) == ["Log escalated", "Risk API"]
    assert set(resumed["data"]) >= {"Risk API", "Log escalated"} and "Log approved" not in resumed["data"]
    meta = test_db.query("SELECT metadata FROM executions WHERE id = %s", (resumed["execution_id"],))[0][0]
    assert meta["trigger"] == {"type": "run_from", "node": "Risk API", "source_execution": failed["execution_id"]}
    got = table_rows()
    assert len(got) == 1
    assert got[0]["order_id"] == "A-1001" and got[0]["status"] == "needs_review"
    assert float(got[0]["amount"]) == 900 and got[0]["reason"] == "damaged"

    # 9. restore v1: the draft has the failing url again
    r = api.post(f"/workflows/{wid}/versions/{v1}/restore")
    assert r.status_code == 200, r.text
    restored = r.json()
    assert restored["id"] == wid
    assert _node(restored["definition"], "Risk API")["parameters"]["url"] == FAILING_URL
    again = api.get(f"/workflows/{wid}").json()
    assert _node(again["definition"], "Risk API")["parameters"]["url"] == FAILING_URL
    assert len(api.get(f"/workflows/{wid}/versions").json()) == 1
    assert api.post(f"/workflows/{wid}/versions/{v1 + 100000}/restore").status_code == 404
