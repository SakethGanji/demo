"""UI-DESIGN-REVIEW.md D3 — published-app tokens: bound to the app (+ rotation key), refreshable,
and surviving re-publish / promote / rollback. The harness runs the engine with WORKFLOW_TEST_MODE=1 so
a per-app ``token_ttl_s`` can shorten the TTL."""

from __future__ import annotations

import shutil
import time

import pytest

from .conftest import uniq
from .journeys._helpers import SET_WF, app_token, bearer, new_project, save_app

pytestmark = [pytest.mark.integration,
              pytest.mark.skipif(shutil.which("esbuild") is None, reason="esbuild not on PATH")]

SRC = "export default function App(){return <div>x</div>}"


def _published(api, P, *, ttl=None, access="public", extra=None):
    W = api.post(f"/projects/{P}/workflows", SET_WF("{{ 1 + 1 }}")).json()["id"]
    A = api.post(f"/projects/{P}/apps", {"name": uniq("app"), "definition": {}}).json()["id"]
    body = {"source_code": SRC, "files": [{"path": "App.tsx", "content": SRC}], "create_version": True,
            "version_trigger": "manual", "workflow_ids": [W]}
    if ttl is not None:
        body["token_ttl_s"] = ttl
    r = api.put(f"/apps/{A}", body)
    assert r.status_code == 200, r.text
    slug = uniq("tok")
    pub = {"slug": slug, "access": access, **(extra or {})}
    assert api.post(f"/apps/{A}/publish", pub).status_code == 200
    return A, W, slug


def test_d3_expired_token_is_refreshed_through_a_token_endpoint(api):
    P, S = new_project(api, "d3")
    api.post(f"/projects/{P}/routes", {"method": "GET", "path": "/ping", "auth": "app_token",
                                       "handler": {"type": "workflow", "workflow_id": api.post(
                                           f"/projects/{P}/workflows", SET_WF("{{ 'pong' }}")).json()["id"]}})
    A, W, slug = _published(api, P, ttl=2)
    tok = app_token(api, slug)
    assert api.raw("GET", f"/api/p/{S}/ping", headers=bearer(tok)).status_code == 200
    assert api.raw("POST", f"/a/{slug}/api/workflow/{W}/run", json={}, headers=bearer(tok)).status_code == 200
    time.sleep(3.2)
    r = api.raw("GET", f"/api/p/{S}/ping", headers=bearer(tok))
    assert r.status_code == 401 and r.json() == {"error": "token_expired"}
    assert api.raw("POST", f"/a/{slug}/api/workflow/{W}/run", json={}, headers=bearer(tok)).status_code == 401
    r = api.raw("POST", f"/a/{slug}/_token")
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["token"] and b["token"] != tok and b["expires_at"].endswith("Z")
    assert api.raw("GET", f"/api/p/{S}/ping", headers=bearer(b["token"])).status_code == 200
    assert api.raw("POST", f"/a/{slug}/api/workflow/{W}/run", json={}, headers=bearer(b["token"])).status_code == 200


def test_d3_token_survives_republish_and_release_promote_and_rollback(api):
    P, S = new_project(api, "d3b")
    W = api.post(f"/projects/{P}/workflows", SET_WF("{{ 'pong' }}")).json()["id"]
    api.post(f"/projects/{P}/routes", {"method": "GET", "path": "/ping", "auth": "app_token",
                                       "handler": {"type": "workflow", "workflow_id": W}})
    A, AW, slug = _published(api, P)
    tok = app_token(api, slug)
    # a new version is published → the old page's token keeps working (bound to the app, not the version)
    v1 = api.post(f"/apps/{A}/publish", {"access": "public"}).json()["version_id"]
    save_app(api, A, SRC.replace("<div>x", "<div>y"))
    r = api.post(f"/apps/{A}/publish", {"access": "public"})
    assert r.status_code == 200 and r.json()["version_id"] != v1
    assert api.raw("GET", f"/api/p/{S}/ping", headers=bearer(tok)).status_code == 200
    assert api.raw("POST", f"/a/{slug}/api/workflow/{AW}/run", json={}, headers=bearer(tok)).status_code == 200
    # promote a release into the live env (re-publishes the app) and roll back: still valid
    rel = api.post(f"/projects/{P}/releases", {"environment": "dev"}).json()
    assert api.post(f"/projects/{P}/releases/{rel['id']}/promote", {"to_environment": "prod"}).status_code == 201
    assert api.raw("GET", f"/api/p/{S}/ping", headers=bearer(tok)).status_code == 200
    save_app(api, A, SRC.replace("<div>x", "<div>z"))
    rel2 = api.post(f"/projects/{P}/releases", {"environment": "dev"}).json()
    assert api.post(f"/projects/{P}/releases/{rel2['id']}/promote", {"to_environment": "prod"}).status_code == 201
    assert api.post(f"/projects/{P}/environments/prod/rollback", {}).status_code == 200
    assert api.raw("GET", f"/api/p/{S}/ping", headers=bearer(tok)).status_code == 200
    # unpublish rotates the key: after re-publishing, the old token is dead, a fresh page works
    api.post(f"/apps/{A}/unpublish")
    assert api.raw("POST", f"/a/{slug}/_token").status_code == 404
    assert api.post(f"/apps/{A}/publish", {"slug": slug, "access": "public"}).status_code == 200
    assert api.raw("GET", f"/api/p/{S}/ping", headers=bearer(tok)).status_code == 401
    assert api.raw("GET", f"/api/p/{S}/ping", headers=bearer(app_token(api, slug))).status_code == 200
    # a token of another app never works for this one
    P2, _ = new_project(api, "d3o")
    _, _, other = _published(api, P2)
    assert api.raw("GET", f"/api/p/{S}/ping", headers=bearer(app_token(api, other))).status_code == 401


def test_d3_password_app_refresh_needs_the_unlock_cookie(api):
    P, _ = new_project(api, "d3p")
    A, W, slug = _published(api, P, access="password", extra={"access_password": "hunter22"})
    assert api.raw("POST", f"/a/{slug}/_token").status_code == 401
    import httpx
    with httpx.Client(base_url=api.base_url, follow_redirects=False) as c:
        r = c.post(f"/a/{slug}/_unlock", data={"password": "hunter22"})
        assert r.status_code == 303
        r = c.post(f"/a/{slug}/_token")
        assert r.status_code == 200 and r.json()["token"]


def _ping_route(api, P):
    W = api.post(f"/projects/{P}/workflows", SET_WF("{{ 'pong' }}")).json()["id"]
    api.post(f"/projects/{P}/routes", {"method": "GET", "path": "/ping", "auth": "app_token",
                                       "handler": {"type": "workflow", "workflow_id": W}})


def test_d3_password_change_kills_outstanding_tokens_and_cookies(api):
    """Audit #3: reverting `rotate_token_key` on a password change must fail a test."""
    import httpx

    P, S = new_project(api, "d3pw")
    _ping_route(api, P)
    A, W, slug = _published(api, P, access="password", extra={"access_password": "hunter22"})
    c = httpx.Client(base_url=api.base_url, follow_redirects=False)
    assert c.post(f"/a/{slug}/_unlock", data={"password": "hunter22"}).status_code == 303
    page = c.get(f"/a/{slug}").text
    import json as _json, re
    old = _json.loads(re.search(r'window.__APP_TOKEN=("[^"]+")', page).group(1))
    assert api.raw("GET", f"/api/p/{S}/ping", headers=bearer(old)).status_code == 200
    assert c.post(f"/a/{slug}/_token").status_code == 200
    # the owner changes the password
    r = api.post(f"/apps/{A}/publish", {"access": "password", "access_password": "new-pass-9"})
    assert r.status_code == 200, r.text
    assert api.raw("GET", f"/api/p/{S}/ping", headers=bearer(old)).status_code == 401      # token dead
    assert c.post(f"/a/{slug}/_token").status_code == 401                                   # old unlock cookie dead
    assert c.get(f"/a/{slug}").status_code == 401
    with httpx.Client(base_url=api.base_url, follow_redirects=False) as c2:
        assert c2.post(f"/a/{slug}/_unlock", data={"password": "hunter22"}).status_code != 303   # old password
        assert c2.post(f"/a/{slug}/_unlock", data={"password": "new-pass-9"}).status_code == 303
        fresh = c2.post(f"/a/{slug}/_token").json()["token"]
    assert api.raw("GET", f"/api/p/{S}/ping", headers=bearer(fresh)).status_code == 200
    assert fresh != old


def test_d3_switching_to_private_kills_outstanding_tokens_and_hides_the_token_endpoint(api):
    P, S = new_project(api, "d3pr")
    _ping_route(api, P)
    A, W, slug = _published(api, P)
    tok = app_token(api, slug)
    assert api.raw("GET", f"/api/p/{S}/ping", headers=bearer(tok)).status_code == 200
    r = api.post(f"/apps/{A}/publish", {"access": "private"})
    assert r.status_code == 200, r.text
    assert api.raw("GET", f"/api/p/{S}/ping", headers=bearer(tok)).status_code == 401
    assert api.raw("POST", f"/a/{slug}/_token").status_code == 404
    assert api.raw("POST", f"/a/{slug}/api/workflow/{W}/run", json={}, headers=bearer(tok)).status_code in (401, 404)
    # going public again does not resurrect the old token
    assert api.post(f"/apps/{A}/publish", {"access": "public"}).status_code == 200
    assert api.raw("GET", f"/api/p/{S}/ping", headers=bearer(tok)).status_code == 401
    assert api.raw("GET", f"/api/p/{S}/ping", headers=bearer(app_token(api, slug))).status_code == 200


def test_d3_runtime_and_generated_client_refresh_on_401(api):
    P, S = new_project(api, "d3r")
    api.post(f"/projects/{P}/routes", {"method": "GET", "path": "/x", "auth": "app_token", "handler": {"type": "stub"}})
    A, W, slug = _published(api, P)
    page = api.raw("GET", f"/a/{slug}").text
    assert "window.__APP_REFRESH=" in page and "'/_token'" in page and "res.status===401" in page
    client = api.raw("GET", f"/api/projects/{P}/client.ts").text
    assert "__APP_REFRESH" in client and "res.status === 401" in client


def test_d3_token_ttl_override_is_test_mode_only(monkeypatch):
    from src.core.public_tokens import DEFAULT_TTL_SECONDS, app_token_ttl

    class App:
        settings = {"token_ttl_s": 5}
    # re-review N7: test mode counts only on a workflows_test_* database
    monkeypatch.setenv("WORKFLOW_DB_NAME", "workflows_test_ttl")
    monkeypatch.setenv("WORKFLOW_TEST_MODE", "1")
    assert app_token_ttl(App()) == 5
    monkeypatch.setenv("WORKFLOW_DB_NAME", "workflows")
    assert app_token_ttl(App()) == DEFAULT_TTL_SECONDS
    monkeypatch.setenv("WORKFLOW_DB_NAME", "workflows_test_ttl")
    monkeypatch.setenv("WORKFLOW_TEST_MODE", "0")
    assert app_token_ttl(App()) == DEFAULT_TTL_SECONDS
    monkeypatch.delenv("WORKFLOW_TEST_MODE")
    assert app_token_ttl(App()) == DEFAULT_TTL_SECONDS == 3600
