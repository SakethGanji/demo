"""Stage 3A Wave 2 over HTTP on a real engine + Postgres: suggested tests, "save as test" and the node catalog,
written from CONTRACTS-STAGE3A section 7. The suggested drafts must be correct, not just present: all of them are
saved and run, and they have to pass."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from .conftest import uniq
from .test_stage3a_api_catalog import add_spec, project

pytestmark = pytest.mark.integration

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "workflows" / "refund_triage.json"
COLUMNS = [{"name": "order_id", "type": "string"}, {"name": "amount", "type": "number"},
           {"name": "reason", "type": "string"}, {"name": "status", "type": "string"}]
TABLE_MOCK = {"items": [{"id": 1, "order_id": "o-1", "status": "logged"}]}


def refund_def(pid_spec: str | None = None, url: str = "http://risk.example.test/v1") -> dict:
    wf = json.loads(FIXTURE.read_text().replace("__REFUND_API_URL__", url))
    wf["name"] = uniq("s3a-suggest")
    wf["settings"] = {}
    for n in wf["nodes"]:
        if n["type"] == "Webhook":
            n["parameters"]["path"] = uniq("refund")
        if n["name"] == "Risk API" and pid_spec:
            n["parameters"] = {"api_ref": {"spec_id": pid_spec, "operation_id": "riskCheck"}, "path_params": {},
                               "body": n["parameters"]["body"], "responseType": "json"}
    return wf


def make(api, pid: str, wf: dict) -> str:
    r = api.post(f"/projects/{pid}/workflows", wf)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def suggest(api, wid: str, **body) -> list[dict]:
    r = api.post(f"/workflows/{wid}/tests/suggest", body)
    assert r.status_code == 200, r.text
    return r.json()


def save(api, wid: str, wf: dict, tests: list, defaults: dict):
    payload = {k: wf[k] for k in ("name", "nodes", "connections", "settings")}
    r = api.put(f"/workflows/{wid}", {**payload, "tests": tests, "test_defaults": defaults})
    assert r.status_code == 200, r.text


class RiskServer:
    """A stand-in risk API for the one real run."""

    def __init__(self) -> None:
        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                data = json.dumps({"decision": "review", "score": 70}).encode()
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *a):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/risk-check"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()


# ── suggest ──────────────────────────────────────────────────────────────────


def test_suggest_reads_boundaries_and_one_draft_per_mock_option_and_all_drafts_pass(api):
    pid = project(api)
    spec = add_spec(api, pid)
    wf = refund_def(spec["id"])
    wid = make(api, pid, wf)

    drafts = suggest(api, wid)
    ids = [d["id"] for d in drafts]
    assert len(ids) == len(set(ids)) and all(d["id"].startswith("t_") and len(d["id"]) == 10 for d in drafts)
    assert all(d["status"] == "draft" and d["source"] == "suggested" and d["locked"] is False for d in drafts)

    boundary = [d for d in drafts if d["category"] == "boundary" and d.get("field") == "amount"]
    amounts = sorted(d["input"]["items"][0]["body"]["amount"] for d in boundary)
    assert 500 in amounts and 501 in amounts
    by_amount = {d["input"]["items"][0]["body"]["amount"]: d for d in boundary}
    assert by_amount[501]["target"] == {"kind": "workflow"}
    assert {"node": "Amount over 500", "port": "true", "op": "count", "value": 1} in by_amount[501]["expect"]
    assert {"node": "Amount over 500", "port": "false", "op": "count", "value": 1} in by_amount[500]["expect"]

    risk = [d for d in drafts if d["covers"] == ["Risk API"]]
    assert sorted(d["category"] for d in risk) == sorted(["ext_success", "ext_error", "ext_error", "ext_error", "ext_empty", "ext_timeout"])
    assert {d["mocks"]["Risk API"].get("http", {}).get("status") for d in risk} >= {200, 400, 404, 500}

    only = suggest(api, wid, node="Amount over 500")
    assert only and all(d["covers"] == ["Amount over 500"] for d in only)
    assert api.post(f"/workflows/{wid}/tests/suggest", {"node": "Nope"}).status_code == 404
    assert api.post("/workflows/wf_missing/tests/suggest", {}).status_code == 404

    risk_ok = next(d for d in risk if d["category"] == "ext_success")["mocks"]["Risk API"]
    defaults = {"mocks": {"Risk API": risk_ok, "Log escalated": TABLE_MOCK, "Log approved": TABLE_MOCK}}
    save(api, wid, wf, drafts, defaults)
    report = api.post(f"/workflows/{wid}/tests/run", {}).json()
    bad = [(r["name"], r["status"], r["failures"]) for r in report["results"] if r["status"] != "passed"]
    assert not bad, bad
    assert report["summary"]["total"] == len(drafts)

    grid = {row["node"]: row["categories"] for row in report["grid"]["rows"]}
    assert grid["Amount over 500"]["boundary"]["status"] == "pass" and grid["Risk API"]["ext_timeout"]["status"] == "pass"
    assert grid["Risk API"]["boundary"]["status"] == "na"

    assert suggest(api, wid) == []  # everything suggested is already saved: nothing is offered twice


def test_suggest_without_an_api_spec_uses_generic_outside_drafts(api):
    pid = project(api)
    wf = refund_def()
    wid = make(api, pid, wf)
    drafts = suggest(api, wid, node="Risk API")
    assert sorted(d["category"] for d in drafts) == ["ext_empty", "ext_error", "ext_success", "ext_timeout"]
    save(api, wid, wf, drafts, {"mocks": {}})
    report = api.post(f"/workflows/{wid}/tests/run", {}).json()
    assert [r["status"] for r in report["results"]] == ["passed"] * 4, [r["failures"] for r in report["results"]]


# ── save as test ─────────────────────────────────────────────────────────────


def test_from_execution_builds_a_replay_test_that_passes(api):
    server = RiskServer()
    try:
        pid = project(api)
        table = uniq("refunds").replace("-", "_")
        assert api.post(f"/projects/{pid}/tables", {"name": table, "columns": COLUMNS}).status_code == 201
        wf = refund_def(url=server.url)
        for n in wf["nodes"]:
            if n["type"] == "DataTable":
                n["parameters"]["table"] = table
        wid = make(api, pid, wf)
        run = api.post(f"/workflows/{wid}/run", {"input_data": {"order_id": "o-9", "amount": 900, "reason": "late"}})
        assert run.status_code == 200 and run.json()["status"] == "success", run.text
        eid = run.json()["execution_id"]

        r = api.post(f"/workflows/{wid}/tests/from-execution", {"execution_id": eid, "name": "Big refund replay"})
        assert r.status_code == 200, r.text
        t = r.json()
        assert t["source"] == "replay" and t["status"] == "draft" and t["name"] == "Big refund replay"
        assert t["target"] == {"kind": "workflow"}
        assert t["input"]["items"][0]["body"]["amount"] == 900
        assert set(t["mocks"]) == {"Risk API", "Log escalated"}
        assert t["mocks"]["Risk API"]["http"]["status"] == 200 and t["mocks"]["Risk API"]["http"]["body"]["decision"] == "review"
        assert {"node": "Amount over 500", "port": "true", "op": "count", "value": 1} in t["expect"]
        assert {"node": "Log escalated", "op": "status", "value": "success"} in t["expect"]
        assert any(a["op"] == "shape" for a in t["expect"])

        save(api, wid, wf, [t], {})
        report = api.post(f"/workflows/{wid}/tests/run", {}).json()
        assert report["results"][0]["status"] == "passed", report["results"][0]["failures"]

        assert api.post(f"/workflows/{wid}/tests/from-execution", {"execution_id": "exec_nope"}).status_code == 404
        other = make(api, pid, refund_def(url=server.url))
        assert api.post(f"/workflows/{other}/tests/from-execution", {"execution_id": eid}).status_code == 404
        assert api.post("/workflows/wf_missing/tests/from-execution", {"execution_id": eid}).status_code == 404
    finally:
        server.close()


# ── node catalog ─────────────────────────────────────────────────────────────


def test_node_catalog_covers_every_registered_type(api):
    r = api.get("/nodes/catalog")
    assert r.status_code == 200, r.text
    catalog = r.json()
    types = {n["type"] for n in api.get("/nodes").json()}
    assert {c["type"] for c in catalog} == types
    http = next(c for c in catalog if c["type"] == "HttpRequest")
    assert http["effect"] == "network" and "ext_timeout" in http["recipe"]["categories"] and http["recipe"]["checks"]
    assert next(c for c in catalog if c["type"] == "LLMChat")["recipe"] == {"checks": [], "categories": []}
    assert all(c["contract_kind"] in ("derived", "declared", "observed") and c["example"]["type"] == c["type"] for c in catalog)
    assert api.get("/nodes/If").status_code == 200  # the catalog route doesn't shadow the per-type route


# ── logic nodes ──────────────────────────────────────────────────────────────


def _n(name, t, p=None, **kw):
    return {"name": name, "type": t, "parameters": p or {}, "position": {"x": 0, "y": 0}, **kw}
def _e(a, b, port="main"):
    return {"source_node": a, "target_node": b, "source_output": port, "target_input": "main"}

def test_suggested_drafts_for_logic_nodes_all_pass(api):
    pid = project(api)
    nodes = [
        _n("Hook", "Webhook", {"method": "POST", "path": uniq("x"), "responseMode": "onReceived"}),
        _n("Prep", "Set", {"mode": "manual", "keepOnlySet": False, "fields": [{"name": "qty", "value": "{{ $json.body.qty }}"}, {"name": "kind", "value": "{{ $json.body.kind }}"}, {"name": "tag", "value": "fixed"}]}),
        _n("Big", "Filter", {"mode": "rules", "field": "qty", "operation": "gte", "value": "5"}),
        _n("Kind", "Switch", {"mode": "rules", "numberOfOutputs": 3, "rules": [{"output": 0, "field": "kind", "operation": "equals", "value": "a"}, {"output": 1, "field": "kind", "operation": "contains", "value": "b"}]}),
        _n("Ok", "If", {"field": "kind", "operation": "isNotEmpty"}),
        _n("Small", "If", {"condition": "{{ $json.qty < 3 }}"}),
        _n("C", "Code", {"code": "return items"}),
        _n("L", "ItemLists", {"operation": "sort", "sortBy": "qty"}),
        _n("W", "Wait", {"unit": "seconds", "duration": 1}),
        _n("R", "Respond", {}),
        _n("Lp", "Loop", {}),
        _n("M", "Merge", {}),
        _n("Py", "PythonStep", {"code": "return items"}),
    ]
    conns = [_e("Hook", "Prep")] + [_e("Prep", n["name"]) for n in nodes[2:] if n["name"] not in ("Kind", "Ok", "Small", "Big")]
    conns += [_e("Prep", "Big"), _e("Big", "Kind"), _e("Big", "Ok"), _e("Big", "Small")]
    conns = [c for c in conns if not (c["source_node"] == "Prep" and c["target_node"] in ("Kind", "Ok", "Small"))]
    wf = {"name": uniq("explore"), "settings": {}, "nodes": nodes, "connections": conns}
    wid = make(api, pid, wf)
    drafts = suggest(api, wid)
    assert len(drafts) > 20
    save(api, wid, wf, drafts, {})
    report = api.post(f"/workflows/{wid}/tests/run", {}).json()
    bad = [(r["name"], r["failures"]) for r in report["results"] if r["status"] != "passed"]
    assert not bad, bad
