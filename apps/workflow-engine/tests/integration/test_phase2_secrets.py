"""Phase 2 — project secrets: project value wins over team value; write-only; missing scan."""

from __future__ import annotations

import pytest

from .conftest import http_workflow, uniq

pytestmark = pytest.mark.integration


def _key() -> str:
    return "TOKEN_" + uniq("k").replace("-", "_").upper()


def test_phase2_project_overrides_team_and_is_write_only(api, upstream):
    key = _key()
    pid = api.post("/projects", {"name": uniq("sec")}).json()["id"]
    # Team-level var (Variables modal, env "dev")
    team = api.post("/variables", {"team_id": "default", "key": key, "value": "team-value-a", "type": "secret", "environment": "dev"}).json()

    wf = http_workflow(f"{upstream}/x", headers=[{"name": "X-Tok", "value": f"{{{{ $vars.{key} }}}}"}])
    proj_wid = api.post(f"/projects/{pid}/workflows", wf).json()["id"]
    dflt_wid = api.post("/workflows", http_workflow(f"{upstream}/x", headers=[{"name": "X-Tok", "value": f"{{{{ $vars.{key} }}}}"}])).json()["id"]

    def seen(wid: str, env: str = "default") -> str:
        r = api.post(f"/workflows/{wid}/run", {"input_data": None, "environment": env})
        return r.json()["data"]["HTTP"][0]["json"]["body"]["headers"].get("x-tok")

    # Before override: project run sees the team value
    assert seen(proj_wid) == "team-value-a"

    # Project Secrets page: create override
    r = api.post(f"/projects/{pid}/secrets", {"key": key, "value": "project-value-b", "environment": "dev",
                                               "type": "secret", "allowed_hosts": ["api.example.com"]})
    assert r.status_code == 201, r.text
    s = r.json()
    assert s["value"] is None and s["scope"] == "project" and s["overrides_team"] is True
    assert s["allowed_hosts"] == ["api.example.com"]
    assert api.post(f"/projects/{pid}/secrets", {"key": key, "value": "x", "environment": "dev"}).status_code == 409
    assert api.post(f"/projects/{pid}/secrets", {"key": "bad-key", "value": "x", "environment": "dev"}).json()["detail"]["code"] == "invalid_key"
    assert api.post(f"/projects/{pid}/secrets", {"key": "OK", "value": "x", "environment": "nope"}).json()["detail"]["code"] == "unknown_environment"

    assert seen(proj_wid) == "project-value-b"
    assert seen(dflt_wid, "dev") == "team-value-a"  # Default-project workflow unaffected

    # Effective list
    lst = api.get(f"/projects/{pid}/secrets?environment=dev").json()
    mine = [x for x in lst if x["key"] == key]
    assert {(x["scope"], x["overrides_team"], x["overridden"]) for x in mine} == {("project", True, False), ("team", False, True)}
    assert all(x["value"] is None for x in mine)

    # Variables modal still shows only team rows (unchanged behaviour)
    team_view = [x for x in api.get("/variables?environment=dev").json() if x["key"] == key]
    assert len(team_view) == 1 and team_view[0]["id"] == team["id"]

    # Update without description keeps description; value rotation takes effect
    api.put(f"/projects/{pid}/secrets/{s['id']}", {"description": "rotated monthly"})
    r = api.put(f"/projects/{pid}/secrets/{s['id']}", {"value": "project-value-c"})
    assert r.status_code == 200 and r.json()["description"] == "rotated monthly" and r.json()["value"] is None
    assert seen(proj_wid) == "project-value-c"
    # Team row id is not editable through the project page
    assert api.put(f"/projects/{pid}/secrets/{team['id']}", {"value": "x"}).status_code == 404

    # Delete → falls back to team value
    assert api.delete(f"/projects/{pid}/secrets/{s['id']}").json() == {"success": True}
    assert seen(proj_wid) == "team-value-a"


def test_phase2_missing_secret_scan(api, upstream):
    key = _key()
    pid = api.post("/projects", {"name": uniq("miss")}).json()["id"]
    wf = http_workflow(f"{upstream}/x?k={{{{ $vars.{key} }}}}")
    wid = api.post(f"/projects/{pid}/workflows", wf).json()["id"]
    missing = api.get(f"/projects/{pid}/secrets/missing").json()
    entry = next(m for m in missing if m["key"] == key)
    assert entry["missing_in"] == ["dev", "prod"]
    assert entry["referenced_by"] == [{"kind": "workflow", "id": wid, "name": wf["name"], "node": "HTTP"}]
    assert api.get(f"/projects/{pid}/summary").json()["missing_secrets"] >= 1

    # Inline "create $vars.X?" → POST with the key
    api.post(f"/projects/{pid}/secrets", {"key": key, "value": "v", "environment": "dev"})
    entry = next(m for m in api.get(f"/projects/{pid}/secrets/missing").json() if m["key"] == key)
    assert entry["missing_in"] == ["prod"]
    assert not [m for m in api.get(f"/projects/{pid}/secrets/missing?environment=dev").json() if m["key"] == key]


def test_phase2_env_in_use_and_secret_redaction_in_url(api, upstream):
    key = _key()
    pid = api.post("/projects", {"name": uniq("inuse")}).json()["id"]
    api.post(f"/projects/{pid}/secrets", {"key": key, "value": "very-secret-123", "environment": "prod"})
    r = api.put(f"/projects/{pid}", {"environments": [{"name": "dev", "kind": "draft"}], "live_environment": "dev"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "environment_in_use"

    wid = api.post(f"/projects/{pid}/workflows", http_workflow(f"{upstream}/x?k={{{{ $vars.{key} }}}}")).json()["id"]
    run = api.post(f"/workflows/{wid}/run", {"input_data": None, "environment": "prod"}).json()
    det = api.get(f"/executions/{run['execution_id']}").json()
    assert "very-secret-123" not in det["node_metrics"]["HTTP"]["requestUrl"]
