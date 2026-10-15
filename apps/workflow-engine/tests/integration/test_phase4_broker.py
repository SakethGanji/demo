"""Phase 4 — nodes as functions: SDK generation + broker policy (BACKEND-DESIGN §3.4, §5)."""

from __future__ import annotations

import subprocess
import sys

import pytest

from .conftest import uniq

pytestmark = pytest.mark.integration


def _key() -> str:
    return "BK_" + uniq("k").replace("-", "_").upper()


@pytest.fixture()
def proj(api):
    return api.post("/projects", {"name": uniq("brk")}).json()


def _token(api, pid: str, **body) -> str:
    r = api.post(f"/projects/{pid}/broker-tokens", body)
    assert r.status_code == 201, r.text
    return r.json()["token"]


def _call(api, token: str | None, body: dict):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return api.post("/broker/call", body, headers=headers)


def test_phase4_sdk_catalogue_and_package_imports(api, tmp_path):
    cat = api.get("/sdk/functions").json()
    fns = {c["function"]: c for c in cat}
    assert "http_request" in fns and "set" in fns
    for not_fn in ("webhook", "if", "code", "start", "cron", "merge"):
        assert not_fn not in fns
    assert fns["http_request"]["node_type"] == "HttpRequest" and fns["http_request"]["egress"] is True
    assert "url" in fns["http_request"]["parameters"]["properties"]

    sdk = api.get("/sdk/python").json()
    assert len(sdk["version"]) == 16
    for path, src in sdk["files"].items():
        p = tmp_path / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(src)
    out = subprocess.run(
        [sys.executable, "-I", "-c",
         "import sys; sys.path.insert(0, %r); import studio; "
         "assert callable(studio.nodes.http_request); "
         "assert studio.secret('API_KEY') == '{{ $vars.API_KEY }}'; "
         "print(studio.__version__)" % str(tmp_path)],
        capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == sdk["version"]
    z = api.get("/sdk/python.zip")
    assert z.status_code == 200 and z.headers["content-type"] == "application/zip"


def test_phase4_dev_token_rules(api, proj):
    assert api.post(f"/projects/{proj['id']}/broker-tokens", {"environment": "prod"}).status_code == 403
    assert api.post(f"/projects/{proj['id']}/broker-tokens", {"environment": "nope"}).status_code == 422
    r = api.post(f"/projects/{proj['id']}/broker-tokens", {})
    assert r.status_code == 201 and r.json()["environment"] == "dev" and r.json()["token"].startswith("brk_")


def test_phase4_broker_policy_matrix(api, proj, upstream):
    pid = proj["id"]
    host = "127.0.0.1"
    bound, unbound, plain = _key(), _key(), _key()
    api.post(f"/projects/{pid}/secrets", {"key": bound, "value": "bound-secret-XYZ123", "environment": "dev",
                                          "allowed_hosts": [host]})
    api.post(f"/projects/{pid}/secrets", {"key": unbound, "value": "unbound-secret-777", "environment": "dev"})
    api.post(f"/projects/{pid}/secrets", {"key": plain, "value": "plain-value", "environment": "dev", "type": "string"})
    tok = _token(api, pid)
    hdr = lambda k: [{"name": "X-Tok", "value": f"{{{{ $vars.{k} }}}}"}]  # noqa: E731

    # no / bad token
    assert _call(api, None, {"function": "set"}).status_code == 401
    r = _call(api, "brk_nope", {"function": "set"})
    assert r.status_code == 401 and r.json()["error"]["code"] == "invalid_token"

    # pure function works
    r = _call(api, tok, {"function": "set", "parameters": {"mode": "manual", "fields": [{"name": "a", "value": "{{ 1 + 1 }}"}],
                                                          "keepOnlySet": True}, "items": [{}]})
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True and r.json()["items"] == [{"a": 2}] and r.json()["call_id"].startswith("bcall_")

    # bound secret to its host: used upstream, redacted in the response
    r = _call(api, tok, {"function": "http_request", "parameters": {"method": "GET", "url": f"{upstream}/echo", "headers": hdr(bound)}})
    assert r.status_code == 200, r.text
    assert r.json()["items"][0]["body"]["headers"]["x-tok"] == "***"
    assert "bound-secret-XYZ123" not in r.text
    # base64-encoded echo is redacted too
    r = _call(api, tok, {"function": "http_request", "parameters": {"method": "GET", "url": f"{upstream}/b64", "headers": hdr(bound)}})
    assert r.json()["items"][0]["body"]["encoded"] == "***"

    # same secret to another host (localhost != 127.0.0.1) → host_not_allowed
    other = upstream.replace("127.0.0.1", "localhost")
    r = _call(api, tok, {"function": "http_request", "parameters": {"method": "GET", "url": f"{other}/echo", "headers": hdr(bound)}})
    assert r.status_code == 403 and r.json()["error"]["code"] == "host_not_allowed"
    # redirects are not followed for broker calls
    r = _call(api, tok, {"function": "http_request", "parameters": {"method": "GET", "url": f"{upstream}/redirect", "headers": hdr(bound)}})
    assert r.status_code == 200 and r.json()["items"][0]["statusCode"] == 302

    # unbound secret → refused (default-deny, decision 3)
    r = _call(api, tok, {"function": "http_request", "parameters": {"method": "GET", "url": f"{upstream}/echo", "headers": hdr(unbound)}})
    assert r.status_code == 403 and r.json()["error"]["code"] == "secret_not_bound" and r.json()["error"]["key"] == unbound
    # secret in a node without egress → refused
    r = _call(api, tok, {"function": "set", "parameters": {"mode": "manual", "fields": [{"name": "s", "value": f"{{{{ $vars.{bound} }}}}"}]}})
    assert r.status_code == 403 and r.json()["error"]["code"] == "secret_not_bound"
    # non-secret variable is fine anywhere
    r = _call(api, tok, {"function": "set", "parameters": {"mode": "manual", "fields": [{"name": "p", "value": f"{{{{ $vars.{plain} }}}}"}], "keepOnlySet": True}})
    assert r.status_code == 200 and r.json()["items"] == [{"p": "plain-value"}]

    # credentialId / computed $vars / missing secret / unknown function
    r = _call(api, tok, {"function": "http_request", "parameters": {"url": f"{upstream}/x", "credentialId": "cred_x"}})
    assert r.status_code == 422 and r.json()["error"]["code"] == "credential_not_allowed"
    r = _call(api, tok, {"function": "http_request", "parameters": {"url": f"{upstream}/x?k={{{{ $vars.{bound}.upper() }}}}"}})
    assert r.status_code == 422 and r.json()["error"]["code"] == "secret_expression_not_allowed"
    r = _call(api, tok, {"function": "http_request", "parameters": {"url": f"{upstream}/x", "headers": hdr("NOPE_MISSING")}})
    assert r.status_code == 422 and r.json()["error"] == {"code": "missing_secret", "message": r.json()["error"]["message"],
                                                          "key": "NOPE_MISSING", "environment": "dev"}
    for fn in ("webhook", "code", "nope"):
        r = _call(api, tok, {"function": fn})
        assert r.status_code == 403 and r.json()["error"]["code"] == "function_not_allowed"

    # node failure → 502 node_failed, message redacted
    r = _call(api, tok, {"function": "http_request", "parameters": {"method": "GET", "url": "http://127.0.0.1:1/x", "headers": hdr(bound)}})
    assert r.status_code == 502 and r.json()["error"]["code"] == "node_failed"

    # project egress allowlist applies to every call
    api.put(f"/projects/{pid}", {"settings": {"egress_allowlist": ["api.example.org"]}})
    r = _call(api, tok, {"function": "http_request", "parameters": {"method": "GET", "url": f"{upstream}/echo"}})
    assert r.status_code == 403 and r.json()["error"]["code"] == "host_not_allowed"
    r = _call(api, tok, {"function": "send_email", "parameters": {}})
    assert r.status_code == 403 and r.json()["error"]["code"] == "host_not_allowed"
    api.put(f"/projects/{pid}", {"settings": {"egress_allowlist": []}})

    # call log is redacted
    log = api.get(f"/projects/{pid}/broker-calls").json()
    assert log and all("bound-secret-XYZ123" not in str(row) for row in log)
    assert {row["status"] for row in log} >= {"ok", "denied", "error"}


def test_phase4_call_limit(api, proj):
    tok = _token(api, proj["id"], max_calls=2)
    body = {"function": "set", "parameters": {"mode": "manual", "fields": []}}
    assert _call(api, tok, body).status_code == 200
    assert _call(api, tok, body).status_code == 200
    r = _call(api, tok, body)
    assert r.status_code == 429 and r.json()["error"]["code"] == "call_limit_exceeded"


def test_phase4_code_node_calls_studio_functions(api, proj):
    code = ("rows = studio.nodes.set(mode='manual', fields=[{'name': 'via', 'value': 'broker'}], keep_only_set=True)\n"
            "return [{'json': {'rows': rows, 'n': len(items)}}]")
    wf = {
        "name": uniq("code"),
        "nodes": [
            {"name": "Start", "type": "Start", "parameters": {}, "position": {"x": 0, "y": 0}},
            {"name": "Code", "type": "Code", "parameters": {"code": code}, "position": {"x": 200, "y": 0}},
        ],
        "connections": [{"source_node": "Start", "target_node": "Code", "source_output": "main", "target_input": "main"}],
    }
    wid = api.post(f"/projects/{proj['id']}/workflows", wf).json()["id"]
    run = api.post(f"/workflows/{wid}/run").json()
    assert run["status"] == "success", run
    assert run["data"]["Code"][0]["json"] == {"rows": [{"via": "broker"}], "n": 1}
    calls = api.get(f"/projects/{proj['id']}/broker-calls?execution_id={run['execution_id']}").json()
    assert len(calls) == 1 and calls[0]["origin"] == "code_node" and calls[0]["node_type"] == "Set"

    # broker errors surface as node errors
    bad = dict(wf, name=uniq("code"))
    bad["nodes"] = [wf["nodes"][0], {**wf["nodes"][1], "parameters": {"code": "return studio.nodes.call('Webhook')"}}]
    wid2 = api.post(f"/projects/{proj['id']}/workflows", bad).json()["id"]
    run = api.post(f"/workflows/{wid2}/run").json()
    assert run["status"] == "failed" and "function_not_allowed" in run["errors"][0]["error"]
