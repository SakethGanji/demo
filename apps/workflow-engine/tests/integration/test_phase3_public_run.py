"""Phase 3 — published apps run real workflows (BACKEND-DESIGN §3.3)."""

from __future__ import annotations

import json
import re
import shutil

import pytest

from .conftest import http_workflow, start_set_workflow, uniq

pytestmark = [pytest.mark.integration,
              pytest.mark.skipif(shutil.which("esbuild") is None, reason="esbuild not on PATH")]

APP_TSX = "/** a */\n/** b */\nexport default function App() { return <div>x</div> }\n"


def _published_app(api, project_id: str, workflow_ids: list[str]) -> tuple[str, str, str]:
    aid = api.post(f"/projects/{project_id}/apps", {"name": uniq("app"), "definition": {}}).json()["id"]
    api.put(f"/apps/{aid}", {"source_code": APP_TSX, "files": [{"path": "App.tsx", "content": APP_TSX}],
                             "create_version": True, "version_trigger": "manual", "workflow_ids": workflow_ids})
    slug = uniq("run")
    assert api.post(f"/apps/{aid}/publish", {"slug": slug, "access": "public"}).status_code == 200
    page = api.raw("GET", f"/a/{slug}").text
    token = json.loads(re.search(r"window.__APP_TOKEN=(\"[^\"]+\")", page).group(1))
    return aid, slug, token


def test_phase3_published_app_runs_workflow_with_redaction(api, upstream):
    pid = api.post("/projects", {"name": uniq("pub")}).json()["id"]
    key = "PUB_" + uniq("k").replace("-", "_").upper()
    api.post(f"/projects/{pid}/secrets", {"key": key, "value": "live-secret-987654", "environment": "prod"})

    calc = start_set_workflow(expr="{{ $json.body.n * 2 }}", field="doubled")
    calc_id = api.post(f"/projects/{pid}/workflows", calc).json()["id"]
    leak = http_workflow(f"{upstream}/echo", headers=[{"name": "X-Tok", "value": f"{{{{ $vars.{key} }}}}"}])
    leak_id = api.post(f"/projects/{pid}/workflows", leak).json()["id"]
    other_id = api.post("/workflows", start_set_workflow()).json()["id"]  # Default project

    aid, slug, token = _published_app(api, pid, [calc_id, leak_id, other_id])
    auth = {"Authorization": f"Bearer {token}"}

    # Real run (was a stub echo before)
    r = api.raw("POST", f"/a/{slug}/api/workflow/{calc_id}/run", json={"n": 21}, headers=auth)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "success" and body["execution_id"].startswith("exec_")
    assert body["data"] == [{"doubled": 42}] and body["errors"] == []
    assert r.headers["x-execution-id"] == body["execution_id"]
    run = next(x for x in api.get(f"/projects/{pid}/runs").json() if x["id"] == body["execution_id"])
    assert run["mode"] == "app" and run["environment"] == "prod"

    # Secret from the live env is used upstream but redacted in the response
    r = api.raw("POST", f"/a/{slug}/api/workflow/{leak_id}/run", json={}, headers=auth)
    assert r.status_code == 200
    assert "live-secret-987654" not in r.text
    assert r.json()["data"][0]["body"]["headers"]["x-tok"] == "***"

    # Caller payload {{ $vars.X }} is NOT expanded on the public surface
    echo = start_set_workflow(expr="{{ $json.body.x }}", field="x")
    echo_id = api.post(f"/projects/{pid}/workflows", echo).json()["id"]
    api.put(f"/apps/{aid}", {"workflow_ids": [calc_id, leak_id, other_id, echo_id]})
    api.post(f"/apps/{aid}/publish", {})
    page = api.raw("GET", f"/a/{slug}").text
    auth = {"Authorization": "Bearer " + json.loads(re.search(r"window.__APP_TOKEN=(\"[^\"]+\")", page).group(1))}
    r = api.raw("POST", f"/a/{slug}/api/workflow/{echo_id}/run", json={"x": f"{{{{ $vars.{key} }}}}"}, headers=auth)
    assert r.json()["data"] == [{"x": f"{{{{ $vars.{key} }}}}"}]

    # Allow-list + project scoping + auth
    assert api.raw("POST", f"/a/{slug}/api/workflow/{other_id}/run", json={}, headers=auth).status_code == 403
    not_listed = api.post(f"/projects/{pid}/workflows", start_set_workflow()).json()["id"]
    assert api.raw("POST", f"/a/{slug}/api/workflow/{not_listed}/run", json={}, headers=auth).status_code == 403
    assert api.raw("POST", f"/a/{slug}/api/workflow/{calc_id}/run", json={}).status_code == 401
    assert api.raw("POST", f"/a/{slug}/api/workflow/{calc_id}/run", json={}, headers={"Authorization": "Bearer x.y"}).status_code == 401


def test_phase3_runtime_rewrite_fixed(api):
    pid = api.post("/projects", {"name": uniq("rt")}).json()["id"]
    _, slug, _ = _published_app(api, pid, [])
    page = api.raw("GET", f"/a/{slug}").text
    assert "url.indexOf('/api/workflow/')===0)?(base+url.slice(4))" in page
    assert "url.indexOf('/api/p/')===0" in page


def test_phase3_default_project_app_uses_default_env(api):
    """Existing (Default project) apps run in env 'default' — today's behaviour."""
    key = "DEF_" + uniq("k").replace("-", "_").upper()
    api.post("/variables", {"team_id": "default", "key": key, "value": "def-val", "type": "string", "environment": "default"})
    wid = api.post("/workflows", start_set_workflow(expr=f"{{{{ $vars.{key} }}}}", field="v")).json()["id"]
    _, slug, token = _published_app(api, "proj_default", [wid])
    r = api.raw("POST", f"/a/{slug}/api/workflow/{wid}/run", json={}, headers={"Authorization": f"Bearer {token}"})
    assert r.json()["data"] == [{"v": "def-val"}]
