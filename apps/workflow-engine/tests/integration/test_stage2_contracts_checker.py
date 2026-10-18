"""Stage 2 over HTTP, written from CONTRACTS-STAGE2.md: every WF code through POST /check, publish blocking,
the learn / approve / delete contract routes and POST /workflows/import/n8n."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from .conftest import uniq

pytestmark = pytest.mark.integration

N8N_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "n8n"
N8N_FILES = sorted(N8N_DIR.glob("*.json"))


# ── builders ─────────────────────────────────────────────────────────────────────────────────


def N(name: str, type_: str, params: dict | None = None, **extra) -> dict:
    return {"name": name, "type": type_, "parameters": params or {}, "position": {"x": 0, "y": 0}, **extra}


def E(a: str, b: str, out: str = "main", inp: str = "main") -> dict:
    return {"source_node": a, "target_node": b, "source_output": out, "target_input": inp}


def S(name: str, **fields: str) -> dict:
    return N(name, "Set", {"mode": "manual", "keepOnlySet": True, "fields": [{"name": k, "value": v} for k, v in fields.items()]})


def WH(name: str = "Webhook", **p) -> dict:
    return N(name, "Webhook", {"method": "POST", "path": uniq("p"), "responseMode": "onReceived", **p})


def body(nodes: list[dict], conns: list[dict], **extra) -> dict:
    return {"name": uniq("s2c"), "settings": {}, "nodes": nodes, "connections": conns, **extra}


def make(api, wf: dict, project: str | None = None) -> str:
    r = api.post(f"/projects/{project}/workflows" if project else "/workflows", wf)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def check(api, wf: dict, project: str | None = None) -> dict:
    wid = make(api, wf, project)
    r = api.post(f"/workflows/{wid}/check", {})
    assert r.status_code == 200, r.text
    return r.json()


def codes(res: dict, severity: str | None = None) -> list[str]:
    return sorted(f["code"] for f in res["findings"] if severity is None or f["severity"] == severity)


def find(res: dict, code: str) -> list[dict]:
    return [f for f in res["findings"] if f["code"] == code]


def project(api, allowlist: list[str] | None = None) -> str:
    pid = api.post("/projects", {"name": uniq("s2p")}).json()["id"]
    if allowlist is not None:
        r = api.put(f"/projects/{pid}", {"settings": {"egress_allowlist": allowlist}})
        assert r.status_code == 200, r.text
    return pid


def http_node(name: str, url: str) -> dict:
    return N(name, "HttpRequest", {"method": "GET", "url": url, "responseType": "json"})


# ── one test per code ────────────────────────────────────────────────────────────────────────


def test_wf001_missing_and_non_ancestor_node(api):
    res = check(api, body([N("Start", "Start"), S("A", x='{{ $node["Ghost"].json.x }}')], [E("Start", "A")]))
    f = find(res, "WF001")
    assert len(f) == 1 and f[0]["severity"] == "error" and f[0]["node"] == "A" and "Ghost" in f[0]["message"]
    # a node that exists but is not upstream (a sibling branch) is also WF001
    wf = body([N("Start", "Start"), S("B", x="1"), S("A", x='{{ $node["B"].json.x }}')], [E("Start", "A"), E("Start", "B")])
    res = check(api, wf)
    assert [f["node"] for f in find(res, "WF001")] == ["A"]
    # upstream ancestor: clean
    wf = body([N("Start", "Start"), S("B", x="1"), S("A", x='{{ $node["B"].json.x }}')], [E("Start", "B"), E("B", "A")])
    assert find(check(api, wf), "WF001") == []


def test_wf002_field_missing_from_known_shape(api):
    wf = body([N("Start", "Start"), S("A", total="1"), S("B", y="{{ $json.totl }}")], [E("Start", "A"), E("A", "B")])
    res = check(api, wf)
    f = find(res, "WF002")
    assert len(f) == 1 and f[0]["severity"] == "error" and f[0]["node"] == "B"
    assert "totl" in (f[0]["path"] or "") + f[0]["message"] and "total" in f[0]["message"]  # did-you-mean
    ok = body([N("Start", "Start"), S("A", total="1"), S("B", y="{{ $json.total }}")], [E("Start", "A"), E("A", "B")])
    assert codes(check(api, ok)) == []


def test_wf003_type_mismatch_is_a_warning(api):
    wf = body([WH(), S("A", y="{{ $json.method > 5 }}")], [E("Webhook", "A")])
    res = check(api, wf)
    f = find(res, "WF003")
    assert len(f) == 1 and f[0]["severity"] == "warning" and f[0]["node"] == "A"
    assert "WF003" not in codes(check(api, body([WH(), S("A", y="{{ $json.method == 'POST' }}")], [E("Webhook", "A")])))


def test_wf004_unused_branch_and_unreachable_node(api):
    wf = body([N("Start", "Start"), N("If", "If", {"condition": "{{ $json.mode == 'x' }}"}), S("T", a="1")],
              [E("Start", "If"), E("If", "T", "true")])
    f = find(check(api, wf), "WF004")
    assert len(f) == 1 and f[0]["severity"] == "warning" and f[0]["node"] == "If"
    both = body([N("Start", "Start"), N("If", "If", {"condition": "{{ $json.mode == 'x' }}"}), S("T", a="1"), S("F", a="2")],
                [E("Start", "If"), E("If", "T", "true"), E("If", "F", "false")])
    assert find(check(api, both), "WF004") == []
    # connected but unreachable from any trigger: the connected pair Orphan1 -> Orphan2 has no trigger
    unreachable = body([N("Start", "Start"), S("O1", a="1"), S("O2", b="2")], [E("O1", "O2")])
    f = find(check(api, unreachable), "WF004")
    assert f and f[0]["severity"] == "warning" and {x["node"] for x in f} & {"O1", "O2"}


def test_wf005_reference_into_unknown_shape_is_info(api):
    res = check(api, body([WH(), S("A", y="{{ $json.body.order }}")], [E("Webhook", "A")]))  # webhook body is unknown
    f = find(res, "WF005")
    assert f and all(x["severity"] == "info" for x in f) and f[0]["node"] == "A"
    assert find(res, "WF002") == []
    assert res["coverage"]["refs_unknown"] >= 1


def test_wf006_missing_var_and_clean_with_a_real_var(api):
    key = "S2VAR_" + uniq("k").replace("-", "_").upper()
    wf = lambda: body([N("Start", "Start"), S("A", y="{{ $vars." + key + " }}")], [E("Start", "A")])
    res = check(api, wf())
    f = find(res, "WF006")
    assert len(f) == 1 and f[0]["severity"] == "error" and key in f[0]["message"]
    r = api.post("/variables", {"team_id": "default", "key": key, "value": "v", "type": "string", "environment": "default"})
    assert r.status_code in (200, 201), r.text
    assert find(check(api, wf()), "WF006") == []


def test_wf006_missing_table_and_real_table(api):
    tname = "s2t_" + uniq("t").replace("-", "_")
    dt = lambda: body([N("Start", "Start"), N("Log", "DataTable", {"table": tname, "operation": "insert", "data": {"a": "1"}})],
                      [E("Start", "Log")])
    pid = project(api)
    assert [f["node"] for f in find(check(api, dt(), pid), "WF006")] == ["Log"]
    r = api.post(f"/projects/{pid}/tables", {"name": tname, "columns": [{"name": "a", "type": "string"}]})
    assert r.status_code == 201, r.text
    assert find(check(api, dt(), pid), "WF006") == []


def test_wf006_missing_subworkflow_target(api):
    wf = body([N("Start", "Start"), N("Sub", "ExecuteWorkflow", {"workflowId": "wf_does_not_exist_" + uniq("x")})], [E("Start", "Sub")])
    res = check(api, wf)
    f = find(res, "WF006")
    assert len(f) == 1 and f[0]["severity"] == "error" and f[0]["node"] == "Sub"
    target = make(api, body([N("Start", "Start")], []))
    wf2 = body([N("Start", "Start"), N("Sub", "ExecuteWorkflow", {"workflowId": target})], [E("Start", "Sub")])
    assert find(check(api, wf2), "WF006") == []


def test_wf007_host_not_on_the_project_egress_allowlist(api):
    pid = project(api, ["api.allowed.test"])
    bad = body([N("Start", "Start"), http_node("H", "https://evil.example.com/x")], [E("Start", "H")])
    f = find(check(api, bad, pid), "WF007")
    assert len(f) == 1 and f[0]["severity"] == "error" and f[0]["node"] == "H" and "evil.example.com" in f[0]["message"]
    good = body([N("Start", "Start"), http_node("H", "https://api.allowed.test/x")], [E("Start", "H")])
    assert find(check(api, good, pid), "WF007") == []
    # an expression in the host part cannot be judged statically
    dyn = body([N("Start", "Start"), http_node("H", "https://{{ $json.mode }}.example.com/x")], [E("Start", "H")])
    assert find(check(api, dyn, pid), "WF007") == []
    # no allowlist (empty) means unrestricted
    open_pid = project(api)
    assert find(check(api, bad, open_pid), "WF007") == []


def test_wf008_required_param_invalid_option_and_cycle(api):
    res = check(api, body([N("Start", "Start"), N("H", "HttpRequest", {"method": "GET", "url": ""})], [E("Start", "H")]))
    f = [x for x in find(res, "WF008") if x["node"] == "H"]
    assert f and f[0]["severity"] == "error" and f[0]["param"] == "url"
    res = check(api, body([N("Start", "Start"), N("H", "HttpRequest", {"method": "FETCH", "url": "http://x.test"})], [E("Start", "H")]))
    assert any(x["param"] == "method" for x in find(res, "WF008"))
    cyc = body([N("Start", "Start"), S("A", x="1"), S("B", x="2")], [E("Start", "A"), E("A", "B"), E("B", "A")])
    f = find(check(api, cyc), "WF008")
    assert len(f) == 1 and f[0]["node"] in ("A", "B") and "cycle" in f[0]["message"].lower() and f[0]["severity"] == "error"


def test_cycle_through_a_loop_is_not_wf008(api):
    wf = body([N("Start", "Start"), N("Loop", "Loop", {"batchSize": 1}), S("Body", x="1")],
              [E("Start", "Loop"), E("Loop", "Body"), E("Body", "Loop")])
    assert "cycle" not in " ".join(f["message"].lower() for f in find(check(api, wf), "WF008"))


def test_wf009_expression_does_not_parse(api):
    res = check(api, body([N("Start", "Start"), S("A", x="{{ $json.mode + }}")], [E("Start", "A")]))
    f = find(res, "WF009")
    assert len(f) == 1 and f[0]["severity"] == "error" and f[0]["node"] == "A"


def test_disabled_node_params_are_not_checked_but_it_passes_through(api):
    wf = body([N("Start", "Start"), S("A", x="1"), {**S("Off", y="{{ $node['Ghost'].json.q + }}"), "disabled": True}, S("B", z="{{ $json.x }}")],
              [E("Start", "A"), E("A", "Off"), E("Off", "B")])
    res = check(api, wf)
    assert find(res, "WF001") == [] and find(res, "WF009") == []
    assert find(res, "WF002") == []  # passthrough keeps A's shape (x) for B


def test_shapes_by_node_and_coverage_shape(api):
    wf = body([WH(), S("A", order="{{ $json.method }}", n="1"), S("B", z="{{ $json.order }}")], [E("Webhook", "A"), E("A", "B")])
    res = check(api, wf)
    assert set(res) == {"findings", "shapes_by_node", "coverage"}
    sb = res["shapes_by_node"]
    assert set(sb) == {"Webhook", "A", "B"}
    for v in sb.values():
        assert set(v) >= {"inputs", "outputs"}
    out = sb["A"]["outputs"]["main"]
    assert out["type"] == "object" and set(out["properties"]) == {"order", "n"}
    assert sb["B"]["inputs"]["main"]["properties"].keys() == {"order", "n"}
    wh = sb["Webhook"]["outputs"]["main"]["properties"]
    assert {"body", "headers", "query", "method"} <= set(wh) and wh["body"]["type"] == "unknown"
    cov = res["coverage"]
    assert set(cov) == {"refs_total", "refs_checked", "refs_unknown", "nodes_total", "nodes_known_output"}
    assert cov["nodes_total"] == 3 and cov["refs_total"] >= 2 and cov["refs_checked"] >= 1
    assert cov["refs_checked"] + cov["refs_unknown"] <= cov["refs_total"]
    for f in res["findings"]:
        assert set(f) >= {"code", "severity", "message", "node", "param", "path"}


def test_check_body_definition_overrides_draft_and_environment_is_accepted(api):
    wid = make(api, body([N("Start", "Start"), S("A", x="{{ $json.mode }}")], [E("Start", "A")]))
    bad = {"nodes": [N("Start", "Start"), S("A", x="{{ $json.mode + }}")], "connections": [E("Start", "A")]}
    r = api.post(f"/workflows/{wid}/check", {"definition": bad, "environment": "dev"})
    assert r.status_code == 200 and codes(r.json()) == ["WF009"]
    assert codes(api.post(f"/workflows/{wid}/check").json()) == []  # no body: the stored draft


# ── publish ──────────────────────────────────────────────────────────────────────────────────


def test_publish_blocked_by_error_422_body_then_allowed_after_fix(api):
    wid = make(api, body([N("Start", "Start"), S("A", x="{{ $vars.NOPE_" + uniq("v").replace("-", "_") + " }}")], [E("Start", "A")]))
    r = api.post(f"/workflows/{wid}/publish", {"message": "m"})
    assert r.status_code == 422, r.text
    d = r.json()["detail"]
    assert d["message"] == "Workflow has errors"
    assert [f["code"] for f in d["findings"]] == ["WF006"] and all(f["severity"] == "error" for f in d["findings"])
    assert api.get(f"/workflows/{wid}").json()["active"] is False
    fixed = body([N("Start", "Start"), S("A", x="1")], [E("Start", "A")])
    assert api.put(f"/workflows/{wid}", fixed).status_code == 200
    assert api.post(f"/workflows/{wid}/publish", {"message": "m"}).status_code == 200


def test_publish_not_blocked_by_warnings_or_info(api):
    wf = body([WH(), S("A", y="{{ $json.body.x }}"), S("B", z="{{ $json.method > 5 }}")], [E("Webhook", "A"), E("Webhook", "B")])
    res = check(api, wf)
    assert codes(res, "error") == [] and {"WF003", "WF005"} <= set(codes(res))
    wid = make(api, wf)
    assert api.post(f"/workflows/{wid}/publish", {}).status_code == 200


def test_save_never_blocks_on_errors(api):
    wid = make(api, body([N("Start", "Start"), S("A", x="{{ $json.mode + }}")], [E("Start", "A")]))
    assert api.put(f"/workflows/{wid}", body([N("Start", "Start"), S("A", x="{{ $node['Z'].json.q }}")], [E("Start", "A")])).status_code == 200


# ── learn / approve / delete ─────────────────────────────────────────────────────────────────


class _Json(BaseHTTPRequestHandler):
    def do_GET(self):
        data = json.dumps({"id": 7, "name": "widget", "price": 9.5, "tags": ["a", "b"], "owner": {"email": "a@b.test"}}).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


@pytest.fixture(scope="module")
def json_server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Json)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}/item"
    finally:
        srv.shutdown()


def contract_wf(url: str, downstream: str) -> dict:
    return body([N("Start", "Start"), http_node("Http", url), S("Down", v=downstream)], [E("Start", "Http"), E("Http", "Down")])


def node_of(detail: dict, name: str) -> dict:
    return next(n for n in detail["definition"]["nodes"] if n["name"] == name)


def test_learn_409_before_any_run_and_404s(api, json_server):
    wid = make(api, contract_wf(json_server, "{{ $json.id }}"))
    r = api.post(f"/workflows/{wid}/nodes/Http/contract/learn", {})
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["missing_nodes"] == ["Http"] and r.json()["detail"]["message"]
    assert api.post(f"/workflows/{wid}/nodes/Nope/contract/learn", {}).status_code == 404
    assert api.post("/workflows/wf_nope_x/nodes/Http/contract/learn", {}).status_code == 404


def test_learn_approve_survive_reload_publish_restore_and_delete(api, json_server):
    wid = make(api, contract_wf(json_server, '{{ $node["Http"].json.nothere }}'))
    # before approval the HTTP output is unknown: a reference into it is a coverage gap (WF005), not WF002
    pre = api.post(f"/workflows/{wid}/check", {}).json()
    assert "WF005" in codes(pre) and "WF002" not in codes(pre)
    assert pre["shapes_by_node"]["Http"]["outputs"]["main"]["type"] == "unknown"

    run = api.post(f"/workflows/{wid}/run", {})
    assert run.status_code == 200 and run.json()["status"] == "success", run.text
    eid = run.json()["execution_id"]

    # learn: proposes, does not save
    learn = api.post(f"/workflows/{wid}/nodes/Http/contract/learn", {})
    assert learn.status_code == 200, learn.text
    L = learn.json()
    assert L["from_execution"] == eid and L["sample_count"] == 1 and set(L["outputs"]) == {"main"}
    main = L["outputs"]["main"]
    assert main["type"] == "object" and main["properties"]
    assert api.get(f"/workflows/{wid}").json()["definition"]["nodes"][1].get("contract") is None
    explicit = api.post(f"/workflows/{wid}/nodes/Http/contract/learn", {"execution_id": eid})
    assert explicit.status_code == 200 and explicit.json()["from_execution"] == eid
    assert api.post(f"/workflows/{wid}/nodes/Http/contract/learn", {"execution_id": "ex_nope"}).status_code in (404, 409)

    # approve needs a name
    assert api.post(f"/workflows/{wid}/nodes/Http/contract/approve", {"outputs": L["outputs"], "from_execution": eid}).status_code == 422
    r = api.post(f"/workflows/{wid}/nodes/Http/contract/approve",
                 {"outputs": L["outputs"], "from_execution": eid, "approved_by": "Dana Tester"})
    assert r.status_code == 200, r.text
    c = node_of(r.json(), "Http")["contract"]
    assert c["approved_by"] == "Dana Tester" and c["source"] == "observed" and c["from_execution"] == eid and c["approved_at"]
    assert c["outputs"]["main"]["type"] == "object"

    # survives reload and a PUT that round-trips the definition
    detail = api.get(f"/workflows/{wid}").json()
    assert node_of(detail, "Http")["contract"]["approved_by"] == "Dana Tester"
    put = api.put(f"/workflows/{wid}", {"name": detail["name"], "nodes": detail["definition"]["nodes"],
                                        "connections": detail["definition"]["connections"], "settings": {}})
    assert put.status_code == 200 and node_of(put.json(), "Http")["contract"]["approved_by"] == "Dana Tester"

    # the contract now makes the output known: the missing field becomes WF002 (was WF005 before)
    post = api.post(f"/workflows/{wid}/check", {}).json()
    f = find(post, "WF002")
    assert len(f) == 1 and f[0]["node"] == "Down" and "nothere" in f[0]["message"] + (f[0]["path"] or "")
    assert f[0]["severity"] in ("error", "warning")  # observed shapes: builders downgrade to warning (see report)
    assert "WF005" not in codes(post)
    assert set(post["shapes_by_node"]["Http"]["outputs"]["main"]["properties"]) == set(main["properties"])

    # publish snapshots the contract into the version definition
    pub = api.post(f"/workflows/{wid}/publish", {"message": "with contract"})
    assert pub.status_code == 200, pub.text
    vid = pub.json()["version_id"]
    ver = api.get(f"/workflows/{wid}/versions/{vid}").json()
    assert node_of(ver, "Http")["contract"]["approved_by"] == "Dana Tester"

    # delete removes it from the draft, but not from the published snapshot
    r = api.delete(f"/workflows/{wid}/nodes/Http/contract")
    assert r.status_code == 200 and "contract" not in node_of(r.json(), "Http") or node_of(r.json(), "Http").get("contract") is None
    assert node_of(api.get(f"/workflows/{wid}").json(), "Http").get("contract") is None
    assert "WF005" in codes(api.post(f"/workflows/{wid}/check", {}).json())
    assert node_of(api.get(f"/workflows/{wid}/versions/{vid}").json(), "Http")["contract"]["approved_by"] == "Dana Tester"

    # restore brings the contract back into the draft
    rest = api.post(f"/workflows/{wid}/versions/{vid}/restore")
    assert rest.status_code == 200, rest.text
    assert node_of(rest.json(), "Http")["contract"]["approved_by"] == "Dana Tester"
    assert node_of(api.get(f"/workflows/{wid}").json(), "Http")["contract"]["outputs"]["main"]["type"] == "object"


def test_approve_validation_and_delete_404s(api, json_server):
    wid = make(api, contract_wf(json_server, "{{ $json.id }}"))
    ok_out = {"main": {"type": "object", "properties": {"id": {"type": "integer"}}}}
    assert api.post(f"/workflows/{wid}/nodes/Http/contract/approve", {"outputs": ok_out, "approved_by": ""}).status_code in (400, 422)
    assert api.post(f"/workflows/{wid}/nodes/Nope/contract/approve", {"outputs": ok_out, "approved_by": "x"}).status_code == 404
    assert api.post("/workflows/wf_nope_x/nodes/Http/contract/approve", {"outputs": ok_out, "approved_by": "x"}).status_code == 404
    assert api.delete(f"/workflows/{wid}/nodes/Nope/contract").status_code == 404
    assert api.delete("/workflows/wf_nope_x/nodes/Http/contract").status_code == 404
    # deleting when none exists is harmless; a hand-written (edited) contract is accepted without a run
    assert api.delete(f"/workflows/{wid}/nodes/Http/contract").status_code == 200
    r = api.post(f"/workflows/{wid}/nodes/Http/contract/approve", {"outputs": ok_out, "approved_by": "Ed"})
    assert r.status_code == 200 and node_of(r.json(), "Http")["contract"]["outputs"]["main"]["properties"]["id"]["type"] == "integer"


# ── n8n import ───────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("path", N8N_FILES, ids=[p.name[:12] for p in N8N_FILES])
def test_n8n_import_each_fixture_returns_workflow_and_report(api, path):
    n8n = json.loads(path.read_text())
    r = api.post("/workflows/import/n8n", {"n8n": n8n})
    assert r.status_code == 200, r.text
    out = r.json()
    assert set(out) == {"workflow", "report"}
    wf, rep = out["workflow"], out["report"]
    assert wf["name"] and isinstance(wf["nodes"], list) and wf["nodes"] and isinstance(wf["connections"], list)
    names = [n["name"] for n in wf["nodes"]]
    assert len(set(names)) == len(names)
    for n in wf["nodes"]:
        assert set(n["position"]) >= {"x", "y"}
    for c in wf["connections"]:
        assert c["source_node"] in names and c["target_node"] in names
    assert set(rep) >= {"mapped", "unmapped_nodes", "untranslated_expressions", "disabled_code_nodes", "findings"}
    for m in rep["mapped"]:
        assert set(m) >= {"n8n_name", "n8n_type", "node_type"}
    for u in rep["unmapped_nodes"]:
        assert set(u) >= {"n8n_name", "n8n_type", "reason"} and u["n8n_name"] in names
    real = [n for n in n8n["nodes"] if not n["type"].endswith("stickyNote")]
    assert len(rep["mapped"]) + len(rep["unmapped_nodes"]) == len(real)
    # the importer adds a Start node (and says so in report.notes) when no trigger could be mapped
    extra = len(wf["nodes"]) - len(real)
    assert extra in (0, 1)
    assert extra == 0 or any("Start" in json.dumps(x) for x in rep.get("notes", []))
    # code nodes are always disabled and listed
    code_nodes = [n for n in n8n["nodes"] if n["type"].split(".")[-1] in ("code", "function")]
    assert sorted(rep["disabled_code_nodes"]) == sorted(n["name"] for n in code_nodes)
    for n in wf["nodes"]:
        if n["name"] in rep["disabled_code_nodes"]:
            assert n["type"] == "Code" and n.get("disabled") is True
    for f in rep["findings"]:
        assert f["code"].startswith("WF") and f["severity"] in ("error", "warning", "info")
    # it creates (saving never blocks on findings)
    created = api.post("/workflows", {**wf, "name": uniq("n8n")})
    assert created.status_code == 201, created.text


def test_n8n_import_name_override_and_nothing_saved(api):
    before = len(api.get("/workflows").json())
    n8n = json.loads(N8N_FILES[0].read_text())
    r = api.post("/workflows/import/n8n", {"n8n": n8n, "name": "Renamed import"})
    assert r.status_code == 200 and r.json()["workflow"]["name"] == "Renamed import"
    assert len(api.get("/workflows").json()) == before


def hand_n8n() -> dict:
    return {
        "name": "Hand made",
        "nodes": [
            {"name": "Manual", "type": "n8n-nodes-base.manualTrigger", "typeVersion": 1, "position": [0, 0], "parameters": {}},
            {"name": "Prep", "type": "n8n-nodes-base.set", "typeVersion": 3.4, "position": [200, 0], "parameters": {
                "assignments": {"assignments": [{"id": "1", "name": "amount", "value": 700, "type": "number"},
                                                {"id": "2", "name": "who", "value": "Ann", "type": "string"}]}}},
            {"name": "Big?", "type": "n8n-nodes-base.if", "typeVersion": 2, "position": [400, 0], "parameters": {
                "conditions": {"conditions": [{"leftValue": "={{ $json.amount }}", "rightValue": 500,
                                               "operator": {"type": "number", "operation": "gt"}}]}}},
            {"name": "Mark", "type": "n8n-nodes-base.set", "typeVersion": 3.4, "position": [600, -80], "parameters": {
                "assignments": {"assignments": [{"id": "1", "name": "flag", "value": "={{ $('Prep').item.json.who }}", "type": "string"}]}}},
            {"name": "Note", "type": "n8n-nodes-base.stickyNote", "typeVersion": 1, "position": [0, 200], "parameters": {"content": "hi"}},
        ],
        "connections": {
            "Manual": {"main": [[{"node": "Prep", "type": "main", "index": 0}]]},
            "Prep": {"main": [[{"node": "Big?", "type": "main", "index": 0}]]},
            "Big?": {"main": [[{"node": "Mark", "type": "main", "index": 0}], []]},
        },
    }


def test_n8n_import_hand_made_workflow_creates_and_runs(api):
    r = api.post("/workflows/import/n8n", {"n8n": hand_n8n()})
    assert r.status_code == 200, r.text
    out = r.json()
    rep, wf = out["report"], out["workflow"]
    assert [n["type"] for n in wf["nodes"]] == ["Start", "Set", "If", "Set"]  # the sticky note is dropped silently
    assert rep["unmapped_nodes"] == [] and rep["disabled_code_nodes"] == []
    # n8n position [x, y] became {x, y}; port 0 of If is 'true'
    assert wf["nodes"][1]["position"] == {"x": 200, "y": 0}
    assert any(c["source_node"] == "Big?" and c["source_output"] == "true" and c["target_node"] == "Mark" for c in wf["connections"])
    assert not [f for f in rep["findings"] if f["severity"] == "error"], rep["findings"]
    # translated expression has no $('Prep') call
    mark = next(n for n in wf["nodes"] if n["name"] == "Mark")
    assert mark["parameters"]["fields"][0]["value"] == '{{ $node["Prep"].json.who }}'
    wid = api.post("/workflows", {**wf, "name": uniq("n8nrun")}).json()["id"]
    run = api.post(f"/workflows/{wid}/run", {})
    assert run.status_code == 200 and run.json()["status"] == "success", run.text
    data = run.json()["data"]
    assert data["Mark"][0]["json"]["flag"] == "Ann"
    assert api.post(f"/workflows/{wid}/publish", {}).status_code == 200


@pytest.mark.parametrize("bad", [{"n8n": "not json at all"}, {"n8n": {"foo": 1}}, {"n8n": []}, {"n8n": {"nodes": "x", "connections": {}}},
                                 {"n8n": {"nodes": [{"name": "a"}], "connections": 5}}])
def test_n8n_import_rejects_non_n8n_input_with_400(api, bad):
    r = api.post("/workflows/import/n8n", bad)
    assert r.status_code == 400, r.text
    assert isinstance(r.json()["detail"], str) and r.json()["detail"]


def test_n8n_import_body_without_n8n_is_422(api):
    assert api.post("/workflows/import/n8n", {}).status_code == 422


# ── importer translation (CONTRACTS-STAGE2 §4 "Expressions") ─────────────────────────────────


def _import_set(api, value: str, name: str = "x") -> dict:
    n8n = {"name": "expr", "nodes": [
        {"name": "T", "type": "n8n-nodes-base.manualTrigger", "typeVersion": 1, "position": [0, 0], "parameters": {}},
        {"name": "Src", "type": "n8n-nodes-base.set", "typeVersion": 3.4, "position": [200, 0], "parameters": {
            "assignments": {"assignments": [{"id": "1", "name": "items", "type": "string", "value": "plain"}]}}},
        {"name": "S", "type": "n8n-nodes-base.set", "typeVersion": 3.4, "position": [400, 0], "parameters": {
            "assignments": {"assignments": [{"id": "2", "name": name, "type": "string", "value": value}]}}}],
        "connections": {"T": {"main": [[{"node": "Src", "type": "main", "index": 0}]]},
                        "Src": {"main": [[{"node": "S", "type": "main", "index": 0}]]}}}
    r = api.post("/workflows/import/n8n", {"n8n": n8n})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.parametrize("js,ours", [
    ("={{ $json.items[0].name }}", "{{ $json.items[0].name }}"),
    ("={{ $('Src').item.json.items[0].name }}", '{{ $node["Src"].json.items[0].name }}'),
])
def test_n8n_import_translates_index_then_property_access(api, js, ours):
    out = _import_set(api, js)
    node = next(n for n in out["workflow"]["nodes"] if n["name"] == "S")
    assert node["parameters"]["fields"][0]["value"] == ours
    assert [u for u in out["report"]["untranslated_expressions"] if u["node"] == "S"] == []
    assert not [f for f in out["report"]["findings"] if f["code"] == "WF009" and f["node"] == "S"]


def test_n8n_import_translates_or_reports_expressions_in_http_body_parameter_names(api):
    n8n = {"name": "bodyname", "nodes": [
        {"name": "T", "type": "n8n-nodes-base.manualTrigger", "typeVersion": 1, "position": [0, 0], "parameters": {}},
        {"name": "H", "type": "n8n-nodes-base.httpRequest", "typeVersion": 4, "position": [200, 0], "parameters": {
            "url": "https://api.example.test/x", "method": "POST", "sendBody": True,
            "bodyParameters": {"parameters": [{"name": "={{ $json.mode }}", "value": "={{true}}"}]}}}],
        "connections": {"T": {"main": [[{"node": "H", "type": "main", "index": 0}]]}}}
    out = api.post("/workflows/import/n8n", {"n8n": n8n}).json()
    body = next(n for n in out["workflow"]["nodes"] if n["name"] == "H")["parameters"]["body"]
    assert "={{" not in body, body  # the n8n '=' marker must not leak into the translated body
