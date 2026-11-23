"""Stage 3A over HTTP on a real engine + Postgres: the API catalog, spec-based contracts and mock generation,
written from CONTRACTS-STAGE3A section 6."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, urlparse

import pytest

from .conftest import DB_HOST, DB_PASSWORD, DB_PORT, DB_USER, uniq

pytestmark = pytest.mark.integration

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "openapi"
RISK_YAML = (FIX / "risk_api.yaml").read_text()
PETSTORE = (FIX / "petstore_3_0.json").read_text()
SWAGGER2 = 'swagger: "2.0"\ninfo: {title: old, version: "1"}\npaths: {}\n'


# ── helpers ──────────────────────────────────────────────────────────────────────────────────


def project(api, allowlist: list[str] | None = None) -> str:
    pid = api.post("/projects", {"name": uniq("s3a")}).json()["id"]
    if allowlist is not None:
        assert api.put(f"/projects/{pid}", {"settings": {"egress_allowlist": allowlist}}).status_code == 200
    return pid


def add_spec(api, pid: str, text: str = RISK_YAML, name: str | None = None, base: str | None = None) -> dict:
    if base:
        text = text.replace("http://127.0.0.1:9999", base)
    r = api.post("/api-specs", {"project_id": pid, "name": name, "spec_text": text} if name else {"project_id": pid, "spec_text": text})
    assert r.status_code == 200, r.text
    return r.json()


def N(name: str, type_: str, params: dict | None = None) -> dict:
    return {"name": name, "type": type_, "parameters": params or {}, "position": {"x": 0, "y": 0}}


def E(a: str, b: str) -> dict:
    return {"source_node": a, "target_node": b, "source_output": "main", "target_input": "main"}


def make_wf(api, pid: str, nodes: list[dict], conns: list[dict]) -> str:
    r = api.post(f"/projects/{pid}/workflows", {"name": uniq("s3a"), "settings": {}, "nodes": nodes, "connections": conns})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def risk_node(spec_id: str, op: str = "riskCheck", **extra) -> dict:
    return N("Risk API", "HttpRequest", {"api_ref": {"spec_id": spec_id, "operation_id": op}, "path_params": {}, **extra})


def generate(api, wid: str, node: str, kind: str, **body):
    return api.post(f"/workflows/{wid}/nodes/{quote(node)}/mock/generate", {"kind": kind, **body})


class SpecServer:
    """Serves the risk spec, a 3 MB document and a redirect to a foreign host."""

    def __init__(self) -> None:
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/risk.yaml":
                    data, code = RISK_YAML.encode(), 200
                elif self.path == "/big.json":
                    data, code = b'{"openapi": "3.0.0", "x": "' + b"a" * (3 * 1024 * 1024) + b'"}', 200
                elif self.path == "/notfound":
                    data, code = b"nope", 404
                elif self.path == "/redirect":
                    self.send_response(302)
                    self.send_header("location", "http://blocked.invalid/spec.yaml")
                    self.end_headers()
                    return
                else:
                    data, code = b"{}", 200
                self.send_response(code)
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *a):
                pass

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        del outer


@pytest.fixture(scope="module")
def spec_server():
    s = SpecServer()
    yield s
    s.srv.shutdown()


# ── create / list / get / delete ─────────────────────────────────────────────────────────────


def test_create_from_yaml_paste_list_get_delete(api):
    pid = project(api)
    d = add_spec(api, pid)
    assert d["name"] == "Risk API" and d["source"] == "paste" and d["base_url"] == "http://127.0.0.1:9999"
    assert [o["operation_id"] for o in d["operations"]] == ["riskCheck", "GET /orders/{order_id}"]
    ok = d["operations"][0]["responses"]["200"]["shape"]
    assert ok["x-source"] == "spec" and ok["properties"]["decision"]["enum"] == ["approve", "review", "deny"]

    rows = api.get(f"/api-specs?project_id={pid}").json()
    assert rows == [{"id": d["id"], "name": "Risk API", "base_url": "http://127.0.0.1:9999", "operation_count": 2,
                     "source": "paste", "project_id": pid}]
    assert api.get(f"/api-specs/{d['id']}").json() == d
    other = project(api)
    assert api.get(f"/api-specs?project_id={other}").json() == []

    assert api.delete(f"/api-specs/{d['id']}").json() == {"success": True}
    assert api.get(f"/api-specs/{d['id']}").status_code == 404
    assert api.delete(f"/api-specs/{d['id']}").status_code == 404
    assert api.get(f"/api-specs?project_id={pid}").json() == []


def test_create_from_json_paste_with_refs_and_explicit_name(api):
    pid = project(api)
    d = add_spec(api, pid, PETSTORE, name="Pets")
    assert d["name"] == "Pets" and d["base_url"] == "/api/v3" and len(d["operations"]) == 4
    pet = next(o for o in d["operations"] if o["operation_id"] == "getPetById")
    assert pet["path_params"] == ["petId"] and pet["responses"]["200"]["shape"]["properties"]["category"]["type"] == "object"


def test_upload_source_is_kept(api):
    pid = project(api)
    r = api.post("/api-specs", {"project_id": pid, "spec_text": RISK_YAML, "source": "upload"})
    assert r.status_code == 200 and r.json()["source"] == "upload"


def test_bad_specs_are_422_with_a_reason(api):
    pid = project(api)
    r = api.post("/api-specs", {"project_id": pid, "spec_text": SWAGGER2})
    assert r.status_code == 422 and r.json()["detail"] == "Swagger 2 isn't supported; convert to OpenAPI 3"
    for text, word in [("{not json", "JSON"), ("openapi: 3.0.0\n", "paths"), ("just words", "object"),
                       ('{"openapi":"3.0.0","paths":{"/a":{"get":{"responses":{"200":{"$ref":"http://x.test/r.json"}}}}}}', "Remote")]:
        r = api.post("/api-specs", {"project_id": pid, "spec_text": text})
        assert r.status_code == 422 and word in r.json()["detail"], (text, r.text)
    assert api.post("/api-specs", {"project_id": pid}).status_code == 422  # neither spec_text nor url
    assert api.post("/api-specs", {"project_id": pid, "spec_text": RISK_YAML, "url": "http://x.test"}).status_code == 422
    assert api.post("/api-specs", {"spec_text": RISK_YAML}).status_code == 422  # project_id is required
    assert api.post("/api-specs", {"project_id": "proj_nope", "spec_text": RISK_YAML}).status_code == 404
    assert api.get(f"/api-specs?project_id={pid}").json() == []  # nothing was stored


def test_from_example_infers_a_one_operation_spec(api):
    pid = project(api)
    example = {"id": "o1", "total": 12.5, "paid": True, "items": [{"sku": "x", "qty": 2}]}
    r = api.post("/api-specs/from-example", {"project_id": pid, "name": "Orders", "method": "get", "base_url": "https://orders.test/v1",
                                              "path": "/orders/{id}", "example": example})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["source"] == "example" and d["base_url"] == "https://orders.test/v1" and len(d["operations"]) == 1
    op = d["operations"][0]
    assert op["operation_id"] == "GET /orders/{id}" and op["method"] == "GET" and op["path_params"] == ["id"]
    resp = op["responses"]["200"]
    assert resp["example"] == example
    shape = resp["shape"]
    assert shape["x-source"] == "spec" and shape["properties"]["total"]["type"] == "number"
    assert shape["properties"]["items"]["items"]["properties"]["qty"]["type"] == "integer"
    assert set(shape["required"]) == {"id", "total", "paid", "items"}
    # a custom status
    r = api.post("/api-specs/from-example", {"project_id": pid, "name": "Created", "method": "POST", "path": "/things",
                                              "status": 201, "example": {"ok": True}})
    assert list(r.json()["operations"][0]["responses"]) == ["201"]
    assert api.post("/api-specs/from-example", {"project_id": pid, "name": "x", "path": "nope", "example": {}}).status_code == 422
    assert api.post("/api-specs/from-example", {"project_id": pid, "name": "x", "method": "BREW", "path": "/p", "example": {}}).status_code == 422


def test_mocks_endpoint(api):
    pid = project(api)
    d = add_spec(api, pid)
    r = api.get(f"/api-specs/{d['id']}/operations/riskCheck/mocks")
    assert r.status_code == 200, r.text
    by = {m["label"]: m for m in r.json()}
    assert list(by) == ["200 success", "400 Bad request", "404 Not found", "500 Internal server error", "200 empty", "Timeout"]
    assert by["200 success"]["category"] == "ext_success"
    assert by["200 success"]["mock"] == {"http": {"status": 200, "body": {"decision": "approve", "score": 0}}}
    assert by["404 Not found"]["category"] == "ext_error" and by["200 empty"]["category"] == "ext_empty"
    assert by["Timeout"] == {"label": "Timeout", "category": "ext_timeout", "mock": {"timeout": True}}
    # an operation id with a space and slashes (the fallback form) round-trips
    r = api.get(f"/api-specs/{d['id']}/operations/{quote('GET /orders/{order_id}', safe='')}/mocks")
    assert r.status_code == 200 and r.json()[0]["mock"]["http"]["body"]["id"] == "00000000-0000-4000-8000-000000000001"
    assert api.get(f"/api-specs/{d['id']}/operations/zzz/mocks").status_code == 404
    assert api.get("/api-specs/api_nope/operations/riskCheck/mocks").status_code == 404


# ── URL import ───────────────────────────────────────────────────────────────────────────────


def test_url_import_blocked_by_allowlist(api, spec_server):
    pid = project(api, allowlist=["only.example.com"])
    r = api.post("/api-specs", {"project_id": pid, "url": spec_server.url + "/risk.yaml"})
    assert r.status_code == 422 and "egress allowlist" in r.json()["detail"] and "127.0.0.1" in r.json()["detail"]
    assert api.get(f"/api-specs?project_id={pid}").json() == []
    r = api.post("/api-specs", {"project_id": pid, "url": "ftp://only.example.com/x"})
    assert r.status_code == 422


def test_url_import_allowed_host_and_failures(api, spec_server):
    host = urlparse(spec_server.url).hostname
    pid = project(api, allowlist=[host])
    r = api.post("/api-specs", {"project_id": pid, "url": spec_server.url + "/risk.yaml", "name": "From URL"})
    assert r.status_code == 200, r.text
    assert r.json()["source"] == "url" and r.json()["source_url"].endswith("/risk.yaml") and len(r.json()["operations"]) == 2
    big = api.post("/api-specs", {"project_id": pid, "url": spec_server.url + "/big.json"})
    assert big.status_code == 422 and "2 MB" in big.json()["detail"]
    nf = api.post("/api-specs", {"project_id": pid, "url": spec_server.url + "/notfound"})
    assert nf.status_code == 422 and "404" in nf.json()["detail"]
    junk = api.post("/api-specs", {"project_id": pid, "url": spec_server.url + "/other"})
    assert junk.status_code == 422
    # a redirect to a host that isn't allowlisted is refused on that hop
    rd = api.post("/api-specs", {"project_id": pid, "url": spec_server.url + "/redirect"})
    assert rd.status_code == 422 and "allowlist" in rd.json()["detail"]
    # no allowlist (empty) = unrestricted, same as HttpRequest
    free = project(api)
    assert api.post("/api-specs", {"project_id": free, "url": spec_server.url + "/risk.yaml"}).status_code == 200


def test_pasted_text_over_2mb_is_refused(api):
    pid = project(api)
    r = api.post("/api-specs", {"project_id": pid, "spec_text": '{"openapi": "3.0.0", "x": "' + "a" * (2 * 1024 * 1024 + 10) + '"}'})
    assert r.status_code == 422 and "2 MB" in r.json()["detail"]


# ── checker (WF006) and contract ─────────────────────────────────────────────────────────────


def test_check_wf006_for_dangling_api_ref_and_spec_shapes_otherwise(api):
    pid = project(api)
    d = add_spec(api, pid)
    use = N("Use", "Set", {"mode": "manual", "keepOnlySet": True, "fields": [{"name": "d", "value": "{{ $json.body.decision }}"}]})
    good = make_wf(api, pid, [N("Start", "Start"), risk_node(d["id"]), use], [E("Start", "Risk API"), E("Risk API", "Use")])
    res = api.post(f"/workflows/{good}/check", {}).json()
    assert [f for f in res["findings"] if f["severity"] == "error"] == []
    main = res["shapes_by_node"]["Risk API"]["outputs"]["main"]
    assert main["x-source"] == "spec" and main["properties"]["statusCode"]["type"] == "integer"
    assert main["properties"]["body"]["properties"]["decision"]["enum"] == ["approve", "review", "deny"]

    bad_op = make_wf(api, pid, [N("Start", "Start"), risk_node(d["id"], "nope")], [E("Start", "Risk API")])
    f = [x for x in api.post(f"/workflows/{bad_op}/check", {}).json()["findings"] if x["code"] == "WF006"]
    assert len(f) == 1 and f[0]["severity"] == "error" and f[0]["node"] == "Risk API" and f[0]["param"] == "api_ref"

    api.delete(f"/api-specs/{d['id']}")
    f = [x for x in api.post(f"/workflows/{good}/check", {}).json()["findings"] if x["code"] == "WF006"]
    assert len(f) == 1 and f[0]["node"] == "Risk API"
    # WF006 is an error, so publish refuses
    r = api.post(f"/workflows/{good}/publish", {})
    assert r.status_code == 422 and any(x["code"] == "WF006" for x in r.json()["detail"]["findings"])


def test_check_flags_a_missing_body_field_in_a_spec_shape(api):
    pid = project(api)
    d = add_spec(api, pid)
    use = N("Use", "Set", {"mode": "manual", "fields": [{"name": "d", "value": "{{ $json.body.decison }}"}]})
    wid = make_wf(api, pid, [N("Start", "Start"), risk_node(d["id"]), use], [E("Start", "Risk API"), E("Risk API", "Use")])
    f = [x for x in api.post(f"/workflows/{wid}/check", {}).json()["findings"] if x["code"] == "WF002"]
    assert len(f) == 1 and f[0]["severity"] == "error" and "decision" in f[0]["message"]


# ── mock/generate ────────────────────────────────────────────────────────────────────────────


def test_generate_from_spec(api):
    pid = project(api)
    d = add_spec(api, pid)
    wid = make_wf(api, pid, [N("Start", "Start"), risk_node(d["id"])], [E("Start", "Risk API")])
    r = generate(api, wid, "Risk API", "from_spec")
    assert r.status_code == 200, r.text
    assert r.json()["mock"] == {"http": {"status": 200, "body": {"decision": "approve", "score": 0}}}
    assert isinstance(r.json()["source"], str) and r.json()["source"]
    r = generate(api, wid, "Risk API", "from_spec", status=400)
    assert r.json()["mock"] == {"http": {"status": 400, "body": {"error": "error_example"}}}
    assert generate(api, wid, "Risk API", "from_spec", status=404).json()["mock"] == {"http": {"status": 404}}
    assert generate(api, wid, "Risk API", "from_spec", status=418).status_code == 409
    # never saves: the workflow definition is unchanged
    assert "test_defaults" not in api.get(f"/workflows/{wid}").json()["definition"] or not api.get(f"/workflows/{wid}").json()["definition"]["test_defaults"]["mocks"]
    # no api_ref on the node
    plain = make_wf(api, pid, [N("Start", "Start"), N("H", "HttpRequest", {"method": "GET", "url": "http://x.test"})], [E("Start", "H")])
    assert generate(api, plain, "H", "from_spec").status_code == 422
    assert generate(api, wid, "Nope", "from_spec").status_code == 404
    assert api.post("/workflows/wf_nope/nodes/x/mock/generate", {"kind": "from_spec"}).status_code == 404
    assert generate(api, wid, "Risk API", "bogus").status_code == 422


def test_generate_from_spec_conflict_when_spec_was_deleted(api):
    pid = project(api)
    d = add_spec(api, pid)
    wid = make_wf(api, pid, [N("Start", "Start"), risk_node(d["id"])], [E("Start", "Risk API")])
    api.delete(f"/api-specs/{d['id']}")
    assert generate(api, wid, "Risk API", "from_spec").status_code == 409


def test_generate_from_contract_data_table_columns(api):
    pid = project(api)
    t = api.post(f"/projects/{pid}/tables", {"name": "refunds", "columns": [
        {"name": "order_id", "type": "string", "required": True}, {"name": "amount", "type": "number"},
        {"name": "paid", "type": "boolean"}, {"name": "at", "type": "datetime"}]})
    assert t.status_code in (200, 201), t.text
    wid = make_wf(api, pid, [N("Start", "Start"), N("Refunds", "DataTable", {"table": "refunds", "operation": "list"})], [E("Start", "Refunds")])
    r = generate(api, wid, "Refunds", "from_contract")
    assert r.status_code == 200, r.text
    assert r.json()["mock"] == {"items": [{"id": 1, "order_id": "order_id_example", "amount": 1, "paid": True, "at": "2026-01-01T09:00:00Z"}]}


def test_generate_from_contract_declared_and_spec_shapes(api):
    pid = project(api)
    schema = {"type": "object", "properties": {"n": {"type": "integer", "minimum": 5}, "kind": {"type": "string", "enum": ["a", "b"]}}}
    d = add_spec(api, pid)
    wid = make_wf(api, pid, [N("Hook", "Start", {"input_schema": schema}), N("Keep", "Set", {"mode": "manual", "fields": []}), risk_node(d["id"])],
                  [E("Hook", "Keep"), E("Keep", "Risk API")])
    assert generate(api, wid, "Hook", "from_contract").json()["mock"] == {"items": [{"n": 5, "kind": "a"}]}
    # an HttpRequest on a catalog endpoint gets an http mock built from the body shape
    r = generate(api, wid, "Risk API", "from_contract")
    assert r.status_code == 200 and r.json()["mock"]["http"]["status"] == 200
    assert r.json()["mock"]["http"]["body"]["decision"] == "approve"


def test_generate_from_contract_409_without_a_known_shape(api):
    pid = project(api)
    wid = make_wf(api, pid, [N("Start", "Start"), N("H", "HttpRequest", {"method": "GET", "url": "http://x.test"})], [E("Start", "H")])
    r = generate(api, wid, "H", "from_contract")
    assert r.status_code == 409 and r.json()["detail"]["hint"] == "paste an example" and r.json()["detail"]["message"]


def test_generate_from_contract_postgres_information_schema(api, test_db):
    pid = project(api)
    conn = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{test_db.name}"
    wid = make_wf(api, pid, [
        N("Start", "Start"),
        N("Tables", "Postgres", {"connectionString": conn, "operation": "executeQuery", "query": "SELECT * FROM api_specs"}),
        N("NoTable", "Postgres", {"connectionString": conn, "operation": "executeQuery", "query": "SELECT 1"}),
        N("Dynamic", "Postgres", {"connectionString": "{{ $vars.DB }}", "operation": "executeQuery", "query": "SELECT * FROM api_specs"}),
        N("Missing", "Postgres", {"connectionString": conn, "operation": "executeQuery", "query": "SELECT * FROM no_such_table_xyz"}),
        N("Dead", "Postgres", {"connectionString": "postgresql://u:p@127.0.0.1:1/x", "operation": "executeQuery", "query": "SELECT * FROM api_specs"}),
    ], [E("Start", "Tables"), E("Start", "NoTable"), E("Start", "Dynamic"), E("Start", "Missing"), E("Start", "Dead")])
    r = generate(api, wid, "Tables", "from_contract")
    assert r.status_code == 200, r.text
    row = r.json()["mock"]["items"][0]
    assert list(row) == ["id", "project_id", "name", "base_url", "spec", "source", "source_url", "created_at", "updated_at"]
    assert row["created_at"] == "2026-01-01T09:00:00Z" and row["id"] == "id_example" and row["spec"] is None
    for node in ("NoTable", "Dynamic", "Missing", "Dead"):
        r = generate(api, wid, node, "from_contract")
        assert r.status_code == 409 and r.json()["detail"]["hint"] == "paste an example", (node, r.text)
        assert "postgresql" not in r.text and DB_PASSWORD not in r.text  # never leaks the connection string


def test_generate_last_real(api, upstream, test_db):
    pid = project(api)
    wid = make_wf(api, pid, [N("Start", "Start"), N("Get", "HttpRequest", {"method": "GET", "url": f"{upstream}/v1/balance", "responseType": "json"}),
                             N("Keep", "Set", {"mode": "manual", "keepOnlySet": True, "fields": [{"name": "a", "value": "{{ $json.body.available }}"}]})],
                  [E("Start", "Get"), E("Get", "Keep")])
    r = generate(api, wid, "Get", "last_real")
    assert r.status_code == 409 and r.json()["detail"]["message"]  # nothing has run yet
    run = api.post(f"/workflows/{wid}/run", {})
    assert run.status_code == 200 and run.json()["status"] == "success", run.text
    r = generate(api, wid, "Get", "last_real")
    assert r.status_code == 200, r.text
    http = r.json()["mock"]["http"]
    assert http["status"] == 200 and http["body"] == {"available": 12840.25} and http["headers"]["content-type"] == "application/json"
    assert r.json()["source"]
    r = generate(api, wid, "Keep", "last_real")
    assert r.json()["mock"] == {"items": [{"a": 12840.25}]}

    # a newer execution with mode "test" is ignored
    rows = test_db.query("SELECT id, workflow_name FROM executions WHERE workflow_id = %s", (wid,))
    assert len(rows) == 1
    test_db.query(
        "INSERT INTO executions (id, workflow_id, workflow_name, status, mode, start_time) VALUES (%s, %s, %s, 'success', 'test', now() + interval '1 hour')",
        ("exec_s3a_test_mode", wid, rows[0][1]))
    test_db.query("INSERT INTO node_outputs (execution_id, node_name, output, status, run_index) VALUES (%s, 'Keep', %s, 'success', 0)",
                  ("exec_s3a_test_mode", json.dumps([{"json": {"a": "from the test run"}}])))
    assert generate(api, wid, "Keep", "last_real").json()["mock"] == {"items": [{"a": 12840.25}]}
    # a node that never produced output has nothing to copy
    assert generate(api, wid, "Start", "last_real").status_code in (200, 409)


def test_last_real_409_when_only_test_runs_exist(api, test_db):
    pid = project(api)
    wid = make_wf(api, pid, [N("Start", "Start"), N("Keep", "Set", {"mode": "manual", "fields": []})], [E("Start", "Keep")])
    test_db.query("INSERT INTO executions (id, workflow_id, workflow_name, status, mode, start_time) VALUES (%s, %s, 'w', 'success', 'test', now())",
                  (uniq("exec_s3a"), wid))
    r = generate(api, wid, "Keep", "last_real")
    assert r.status_code == 409


# ── HttpRequest api_ref at run time ──────────────────────────────────────────────────────────


def test_http_request_api_ref_runs_against_a_local_server(api, upstream):
    pid = project(api)
    d = add_spec(api, pid, base=upstream)
    get_op = "GET /orders/{order_id}"
    nodes = [N("Start", "Start"),
             N("Order", "HttpRequest", {"api_ref": {"spec_id": d["id"], "operation_id": get_op}, "path_params": {"order_id": "{{ $json.body.oid }}"},
                                        "url": "http://ignored.invalid/x", "method": "DELETE"}),
             N("Risk API", "HttpRequest", {"api_ref": {"spec_id": d["id"], "operation_id": "riskCheck"},
                                           "body": '{"order_id": "A-7", "amount": 650}'})]
    wid = make_wf(api, pid, [N("Start", "Start", {}), nodes[1], nodes[2]], [E("Start", "Order"), E("Order", "Risk API")])
    r = api.post(f"/workflows/{wid}/run", {"input_data": {"oid": "A 7"}})
    assert r.status_code == 200, r.text
    run = r.json()
    assert run["status"] == "success", run
    order = run["data"]["Order"][0]["json"]
    assert order["body"]["method"] == "GET" and order["body"]["path"] == "/orders/A%207"
    risk = run["data"]["Risk API"][0]["json"]
    assert risk["body"]["method"] == "POST" and risk["body"]["path"] == "/risk-check"
    assert risk["body"]["body"] == {"order_id": "A-7", "amount": 650}
