"""AUDIT-BACKEND-FIXES #2 — published-app workflow runs in a release environment are pinned to the
active release (workflow version + PythonStep module sources), like router runs (D8)."""

from __future__ import annotations

import pytest

from .conftest import uniq
from .journeys._helpers import SIMPLE_APP, app_token, bearer, needs_bwrap, needs_esbuild, new_project, save_app

pytestmark = pytest.mark.integration


def _wf(module: str, extra: dict | None = None) -> dict:
    return {"name": uniq("calc"), "nodes": [
        {"name": "Start", "type": "Start", "parameters": {}, "position": {"x": 0, "y": 0}},
        {"name": "Step", "type": "PythonStep", "parameters": {"module": module, "function": "calc"},
         "position": {"x": 200, "y": 0}}],
        "connections": [{"source_node": "Start", "target_node": "Step", "source_output": "main", "target_input": "main"}],
        **(extra or {})}


@needs_bwrap
@needs_esbuild
def test_published_app_workflow_run_in_prod_uses_the_release_not_the_drafts(api):
    P, S = new_project(api, "pin")
    M = api.post(f"/projects/{P}/modules", {"name": "steps", "source": "def calc(items):\n    return [{'v': 2}]\n"}).json()["module"]["id"]
    W = api.post(f"/projects/{P}/workflows", _wf("steps")).json()["id"]
    A = api.post(f"/projects/{P}/apps", {"name": "UI", "definition": {}}).json()["id"]
    assert api.put(f"/apps/{A}", {"workflow_ids": [W]}).status_code == 200
    save_app(api, A, SIMPLE_APP)
    slug = S + "-ui"
    assert api.post(f"/apps/{A}/publish", {"slug": slug, "access": "public"}).status_code == 200
    rel = api.post(f"/projects/{P}/releases", {"environment": "dev"})
    assert rel.status_code == 201, rel.text
    assert api.post(f"/projects/{P}/releases/{rel.json()['id']}/promote", {"to_environment": "prod"}).status_code == 201
    tok = app_token(api, slug)
    url = f"/a/{slug}/api/workflow/{W}/run"

    def run() -> dict:
        r = api.raw("POST", url, json={}, headers=bearer(tok))
        assert r.status_code == 200, r.text
        return r.json()

    first = run()
    assert first["status"] == "success" and first["data"] == [{"v": 2}]
    # edit the draft module AND the draft workflow: prod must keep serving the released versions
    assert api.put(f"/projects/{P}/modules/{M}", {"source": "def calc(items):\n    return [{'v': 3}]\n"}).status_code == 200
    wf = _wf("steps")
    wf["nodes"].append({"name": "Extra", "type": "Set", "position": {"x": 400, "y": 0},
                        "parameters": {"mode": "manual", "fields": [{"name": "w", "value": "draft"}], "keepOnlySet": False}})
    wf["connections"].append({"source_node": "Step", "target_node": "Extra", "source_output": "main", "target_input": "main"})
    assert api.put(f"/workflows/{W}", wf).status_code == 200
    again = run()
    assert again["data"] == [{"v": 2}], f"prod served unreleased code: {again}"
    run_row = next(x for x in api.get(f"/projects/{P}/runs").json() if x["id"] == again["execution_id"])
    assert run_row["environment"] == "prod"
    # the studio preview (draft env) still sees the drafts
    PT = api.post(f"/projects/{P}/apps/{A}/preview-token").json()["token"]
    r = api.raw("POST", f"/api/apps/{A}/workflow/{W}/run", json={}, headers=bearer(PT))
    assert r.status_code == 200 and r.json()["data"] == [{"v": 3, "w": "draft"}], r.text
