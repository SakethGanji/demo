"""AUDIT-BACKEND-FIXES #1: read_only broker tokens must not reach the engine itself (SSRF to self) or
un-allowlisted loopback / private / link-local addresses. Normal tokens are unchanged."""

from __future__ import annotations

from urllib.parse import urlparse

import pytest

from .conftest import uniq, webhook_workflow
from .journeys._helpers import bearer, new_project

pytestmark = pytest.mark.integration


def _token(api, P, **body):
    r = api.post(f"/projects/{P}/broker-tokens", {"environment": "dev", **body})
    assert r.status_code == 201, r.text
    return r.json()["token"]


def _get(api, tok, url, headers=None):
    return api.post("/broker/call", {"function": "http_request",
                                     "parameters": {"method": "GET", "url": url, "headers": headers or []}},
                    headers=bearer(tok))


def _webhook_runs(api, wid) -> int:
    r = api.get(f"/executions?workflow_id={wid}")
    assert r.status_code == 200, r.text
    return len(r.json())


def test_read_only_get_to_the_engine_itself_is_denied_even_when_allowlisted(api, engine):
    P, _ = new_project(api, "ro-self")
    # the owner allowlists loopback explicitly — the engine's own port still may not be reached
    api.put(f"/projects/{P}", {"settings": {"egress_allowlist": ["127.0.0.1", "localhost", "0.0.0.0"]}})
    path = uniq("ssrf")
    wf = webhook_workflow(path=path, method="GET", set_value="{{ 'ran' }}")
    wid = api.post("/workflows", wf).json()["id"]
    assert api.post(f"/workflows/{wid}/publish", {}).status_code == 200
    assert api.raw("GET", f"/webhook/p/{path}").status_code == 200   # the hook works for normal callers
    before = _webhook_runs(api, wid)
    port = urlparse(engine.base_url).port
    tok = _token(api, P, read_only=True)
    for url in (f"http://127.0.0.1:{port}/webhook/p/{path}", f"http://localhost:{port}/webhook/p/{path}",
                f"http://127.0.0.1:{port}/api/p/anything", f"http://0.0.0.0:{port}/webhook/p/{path}"):
        r = _get(api, tok, url)
        assert r.status_code == 403, (url, r.text)
        assert r.json()["error"]["code"] == "read_only" and r.json()["error"]["function"] == "http_request", r.text
    assert _webhook_runs(api, wid) == before   # no execution was triggered through the broker
    log = api.get(f"/projects/{P}/broker-calls?status=denied").json()
    assert sum(1 for c in log if c["error_code"] == "read_only") == 4


def test_read_only_get_to_unallowlisted_private_addresses_is_denied(api, upstream):
    P, _ = new_project(api, "ro-priv")
    tok = _token(api, P, read_only=True)
    for url in (f"{upstream}/echo", "http://10.20.30.40/x", "http://169.254.169.254/latest/meta-data",
                "http://192.168.1.1/", "http://127.9.9.9:1/", "http://[::1]:1/"):
        r = _get(api, tok, url)
        assert r.status_code == 403 and r.json()["error"]["code"] == "read_only", (url, r.text)
    # a normal (non read-only) token is unchanged: loopback upstream without an allowlist still works
    rw = _token(api, P)
    r = _get(api, rw, f"{upstream}/echo")
    assert r.status_code == 200 and r.json()["items"][0]["body"]["method"] == "GET"


def test_read_only_get_to_an_allowlisted_upstream_is_allowed(api, upstream):
    host = urlparse(upstream).hostname
    # (1) via the project egress allowlist
    P, _ = new_project(api, "ro-ok")
    api.put(f"/projects/{P}", {"settings": {"egress_allowlist": [host]}})
    tok = _token(api, P, read_only=True)
    r = _get(api, tok, f"{upstream}/echo")
    assert r.status_code == 200 and r.json()["items"][0]["body"]["method"] == "GET", r.text
    # (2) via the allowed_hosts of a secret used in the request
    P2, _ = new_project(api, "ro-sec")
    key = "K_" + uniq("k").replace("-", "_").upper()
    assert api.post(f"/projects/{P2}/secrets", {"key": key, "value": "sekret-value-123", "environment": "dev",
                                                "allowed_hosts": [host]}).status_code == 201
    tok2 = _token(api, P2, read_only=True)
    hdr = [{"name": "Authorization", "value": f"Bearer {{{{ $vars.{key} }}}}"}]
    r = _get(api, tok2, f"{upstream}/v1/balance", headers=hdr)
    assert r.status_code == 200 and r.json()["items"][0]["body"] == {"available": 12840.25}, r.text
    # the same token without the secret has no allowlisted host for loopback → denied
    r = _get(api, tok2, f"{upstream}/v1/balance")
    assert r.status_code == 403 and r.json()["error"]["code"] == "read_only"
