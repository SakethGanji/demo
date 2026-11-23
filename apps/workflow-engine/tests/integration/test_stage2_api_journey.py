"""Stage 2 demo story over HTTP: Refund triage is checked, broken by a typo, blocked from publishing, fixed,
published, run, and the Risk API node's response shape is learned and approved as a contract."""

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
COLUMNS = [{"name": "order_id", "type": "string"}, {"name": "amount", "type": "number"},
           {"name": "reason", "type": "string"}, {"name": "status", "type": "string"}]


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
    wf["name"] = uniq("s2-refund")
    wf["settings"] = {}
    for n in wf["nodes"]:
        if n["type"] == "Webhook":
            n["parameters"]["path"] = uniq("refund")
    return wf


def _node(defn: dict, name: str) -> dict:
    return next(n for n in defn["nodes"] if n["name"] == name)


def _payload(wf: dict) -> dict:
    return {"name": wf["name"], "nodes": wf["nodes"], "connections": wf["connections"], "settings": {}}


def _errors(res: dict) -> list[dict]:
    return [f for f in res["findings"] if f["severity"] == "error"]


def test_refund_triage_contracts_and_checker_journey(api, risk_api):
    # 0. the refunds data table the workflow writes to (reuse one from an earlier test in this DB)
    r = api.post("/projects/proj_default/tables", {"name": "refunds", "columns": COLUMNS})
    assert r.status_code in (201, 409), r.text

    # 1. create Refund triage and check it: clean (webhook body fields are only coverage gaps, WF005)
    wf = _workflow(risk_api + "/ok")
    r = api.post("/workflows", wf)
    assert r.status_code == 201, r.text
    wid = r.json()["id"]
    clean = api.post(f"/workflows/{wid}/check", {}).json()
    assert _errors(clean) == [], clean["findings"]
    assert {f["code"] for f in clean["findings"]} <= {"WF005"}
    assert clean["coverage"]["nodes_total"] == 6

    # 2. a typo in the If condition's field, saved with a plain PUT (saving never blocks)
    typo = _workflow(risk_api + "/ok")
    typo["name"], typo["nodes"][0]["parameters"]["path"] = wf["name"], _node(wf, "Webhook")["parameters"]["path"]
    _node(typo, "Amount over 500")["parameters"]["condition"] = "{{ $json.amout > 500 }}"
    assert api.put(f"/workflows/{wid}", _payload(typo)).status_code == 200

    # 3. the check shows WF002 on the If node, with a did-you-mean
    res = api.post(f"/workflows/{wid}/check", {}).json()
    f = [x for x in res["findings"] if x["code"] == "WF002"]
    assert len(f) == 1 and f[0]["severity"] == "error" and f[0]["node"] == "Amount over 500"
    assert "amout" in f[0]["message"] and "amount" in f[0]["message"].replace("amout", "")

    # 4. publish is refused with the error findings
    r = api.post(f"/workflows/{wid}/publish", {"message": "typo"})
    assert r.status_code == 422, r.text
    d = r.json()["detail"]
    assert d["message"] == "Workflow has errors" and [x["code"] for x in d["findings"]] == ["WF002"]
    assert api.get(f"/workflows/{wid}").json()["active"] is False

    # 5. fix it; publish succeeds
    assert api.put(f"/workflows/{wid}", _payload(wf)).status_code == 200
    assert _errors(api.post(f"/workflows/{wid}/check", {}).json()) == []
    r = api.post(f"/workflows/{wid}/publish", {"message": "fixed"})
    assert r.status_code == 200 and r.json()["active"] is True, r.text

    # 6. run with an amount over 500 so the Risk API node executes
    run = api.post(f"/workflows/{wid}/run", {"input_data": {"order_id": "A-1", "amount": 900, "reason": "damaged"}})
    assert run.status_code == 200 and run.json()["status"] == "success", run.text
    eid = run.json()["execution_id"]
    assert run.json()["data"]["Risk API"]

    # before approval the Risk API output is unknown
    before = api.post(f"/workflows/{wid}/check", {}).json()
    assert before["shapes_by_node"]["Risk API"]["outputs"]["main"]["type"] == "unknown"

    # 7. learn the HTTP node's contract (nothing saved), then approve it with a typed name
    learn = api.post(f"/workflows/{wid}/nodes/Risk API/contract/learn", {})
    assert learn.status_code == 200, learn.text
    L = learn.json()
    assert L["from_execution"] == eid and L["sample_count"] == 1
    props = L["outputs"]["main"]["properties"]
    assert props, L
    assert "contract" not in _node(api.get(f"/workflows/{wid}").json()["definition"], "Risk API")
    r = api.post(f"/workflows/{wid}/nodes/Risk API/contract/approve",
                 {"outputs": L["outputs"], "from_execution": eid, "approved_by": "Riley Reviewer"})
    assert r.status_code == 200, r.text
    assert _node(r.json()["definition"], "Risk API")["contract"]["approved_by"] == "Riley Reviewer"

    # 8. the check now lists the HTTP fields in shapes_by_node, and still has no errors
    after = api.post(f"/workflows/{wid}/check", {}).json()
    out = after["shapes_by_node"]["Risk API"]["outputs"]["main"]
    assert out["type"] == "object" and set(out["properties"]) == set(props)
    assert _errors(after) == []
    assert after["coverage"]["nodes_known_output"] > before["coverage"]["nodes_known_output"]
    # the downstream DataTable node sees those fields as its input
    assert set(after["shapes_by_node"]["Log escalated"]["inputs"]["main"]["properties"]) == set(props)

    # 9. the approved contract rides along in a new published version
    pub = api.post(f"/workflows/{wid}/publish", {"message": "with contract"})
    assert pub.status_code == 200, pub.text
    ver = api.get(f"/workflows/{wid}/versions/{pub.json()['version_id']}").json()
    assert _node(ver["definition"], "Risk API")["contract"]["approved_by"] == "Riley Reviewer"
