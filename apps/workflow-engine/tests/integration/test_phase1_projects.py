"""Phase 1 — projects foundation (BACKEND-DESIGN §2.2 M1, §3.1)."""

from __future__ import annotations

import json

import pytest

from .conftest import TestDatabase, start_set_workflow, uniq

pytestmark = pytest.mark.integration

def test_phase1_baseline_seeds_default_project_and_defaults_project_id():
    db = TestDatabase()
    db.create()
    try:
        db.migrate("apply")
        envs, de, le = db.query("SELECT environments, default_environment, live_environment FROM projects WHERE id='proj_default'")[0]
        assert [e["name"] for e in envs] == ["default"]
        assert (de, le) == ("default", "default")
        # Rows inserted without project_id land in the Default project
        db.query("INSERT INTO workflows (id, team_id, name, active, draft_definition, created_at, updated_at) "
                 "VALUES ('wf_plain','default','plain',false,'{}',now(),now())")
        db.query("INSERT INTO apps (id, team_id, name, active, draft_definition, workflow_ids, api_execution_ids, access, embed_enabled, created_at, updated_at) "
                 "VALUES ('app_plain','default','plain',false,'{}','[]','[]','private',false,now(),now())")
        db.query("INSERT INTO folders (id, team_id, name, created_at, updated_at) VALUES ('fold_plain','default','f',now(),now())")
        db.query("INSERT INTO data_tables (id, team_id, name, columns, created_at, updated_at) VALUES ('dt_plain','default','t','[]',now(),now())")
        db.query("INSERT INTO variables (team_id, environment, key, value, type, created_at, updated_at) "
                 "VALUES ('default','default','K','v','string',now(),now())")
        assert db.query("SELECT project_id FROM workflows WHERE id='wf_plain'") == [("proj_default",)]
        assert db.query("SELECT project_id FROM apps WHERE id='app_plain'") == [("proj_default",)]
        assert db.query("SELECT project_id FROM folders WHERE id='fold_plain'") == [("proj_default",)]
        assert db.query("SELECT project_id FROM data_tables WHERE id='dt_plain'") == [("proj_default",)]
        # Variables without project_id stay team-shared
        assert db.query("SELECT project_id FROM variables WHERE key='K'") == [(None,)]
        # Full rollback + re-apply of the single baseline works
        db.migrate("reset")
    finally:
        db.drop()


def test_phase1_projects_grid_has_default_project(api):
    r = api.get("/projects")
    assert r.status_code == 200
    default = next(p for p in r.json() if p["id"] == "proj_default")
    assert default["slug"] == "default"
    assert set(default) >= {"id", "name", "slug", "environments", "default_environment", "live_environment",
                            "settings", "counts", "last_run", "health"}
    assert set(default["counts"]) == {"apps", "workflows", "routes", "tables"}


def test_phase1_project_journey(api):
    # Home: create project
    name = "Orders " + uniq("p")
    r = api.post("/projects", {"name": name})
    assert r.status_code == 201, r.text
    proj = r.json()
    pid, slug = proj["id"], proj["slug"]
    assert pid.startswith("proj_") and slug.startswith("orders-")
    assert [e["name"] for e in proj["environments"]] == ["dev", "prod"]
    assert proj["default_environment"] == "dev" and proj["live_environment"] == "prod"

    # duplicate slug
    assert api.post("/projects", {"name": "x", "slug": slug}).status_code == 409
    assert api.post("/projects", {"name": "x", "slug": "Bad Slug"}).status_code == 422

    # get by id and by slug
    assert api.get(f"/projects/{pid}").json()["id"] == pid
    assert api.get(f"/projects/{slug}").json()["id"] == pid
    assert api.get("/projects/nope-nope").status_code == 404

    # workflow inside the project (two ways)
    r = api.post(f"/projects/{pid}/workflows", start_set_workflow())
    assert r.status_code == 201, r.text
    wid = r.json()["id"]
    assert r.json()["project_id"] == pid
    wf2 = {**start_set_workflow(), "project_id": slug}
    wid2 = api.post("/workflows", wf2).json()["id"]

    in_project = {w["id"] for w in api.get(f"/projects/{pid}/workflows").json()}
    assert in_project == {wid, wid2}
    all_ids = {w["id"] for w in api.get("/workflows").json()}
    assert {wid, wid2} <= all_ids
    assert {w["id"] for w in api.get(f"/workflows?project_id={pid}").json()} == {wid, wid2}
    # Legacy create lands in Default project
    legacy = api.post("/workflows", start_set_workflow()).json()
    assert legacy["project_id"] == "proj_default"
    assert api.post("/workflows", {**start_set_workflow(), "project_id": "nope"}).status_code == 400

    # app inside project
    r = api.post(f"/projects/{pid}/apps", {"name": uniq("app"), "definition": {}})
    assert r.status_code == 201 and r.json()["project_id"] == pid
    aid = r.json()["id"]
    assert [a["id"] for a in api.get(f"/projects/{pid}/apps").json()] == [aid]
    assert all(a["project_id"] for a in api.get("/apps").json())

    # run → appears in project runs + summary
    run = api.post(f"/workflows/{wid}/run").json()
    assert run["status"] == "success"
    runs = api.get(f"/projects/{pid}/runs").json()
    assert runs[0]["id"] == run["execution_id"] and runs[0]["environment"] == "dev"
    detail = api.get(f"/projects/{pid}/runs/{run['execution_id']}").json()
    assert detail["node_data"]["Set"][0]["json"]["answer"] == 42
    assert api.get(f"/projects/proj_default/runs/{run['execution_id']}").status_code == 404

    summary = api.get(f"/projects/{pid}/summary").json()
    assert summary["counts"] == {"apps": 1, "workflows": 2, "routes": 0, "tables": 0}
    assert summary["last_run"]["execution_id"] == run["execution_id"]
    assert summary["health"]["status"] == "ok"
    grid = next(p for p in api.get("/projects").json() if p["id"] == pid)
    assert grid["counts"]["workflows"] == 2

    # update + environments
    r = api.put(f"/projects/{pid}", {"name": "Renamed", "environments": [
        {"name": "dev", "kind": "draft"}, {"name": "staging", "kind": "draft"}, {"name": "prod", "kind": "release"}]})
    assert r.status_code == 200 and r.json()["name"] == "Renamed" and len(r.json()["environments"]) == 3
    assert api.put(f"/projects/{pid}", {"live_environment": "nope"}).status_code == 422
    assert api.put(f"/projects/{pid}", {"settings": {"egress_allowlist": ["api.example.com"]}}).json()["settings"]["egress_allowlist"] == ["api.example.com"]

    # move workflow between projects
    other = api.post("/projects", {"name": uniq("other")}).json()
    wf = api.get(f"/workflows/{wid2}").json()
    body = {"name": wf["name"], "nodes": wf["definition"]["nodes"], "connections": wf["definition"]["connections"],
            "project_id": other["id"]}
    r = api.put(f"/workflows/{wid2}", body)
    assert r.status_code == 200, r.text
    assert r.json()["project_id"] == other["id"]

    # delete: non-empty → 409, default → 409, empty → ok
    r = api.delete(f"/projects/{pid}")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "project_not_empty"
    assert api.delete("/projects/proj_default").json()["detail"]["code"] == "default_project"
    empty = api.post("/projects", {"name": uniq("empty")}).json()
    assert api.delete(f"/projects/{empty['id']}").json() == {"success": True}
    assert api.get(f"/projects/{empty['id']}").status_code == 404


def test_phase1_run_environment_mapping(api):
    """Studio sends environment "default"; in a dev/prod project that means the project default (dev)."""
    pid = api.post("/projects", {"name": uniq("envmap")}).json()["id"]
    wid = api.post(f"/projects/{pid}/workflows", start_set_workflow()).json()["id"]
    api.post(f"/workflows/{wid}/run", {"input_data": None, "environment": "default"})
    api.post(f"/workflows/{wid}/run", {"input_data": None, "environment": "prod"})
    envs = [r["environment"] for r in api.get(f"/projects/{pid}/runs").json()]
    assert sorted(envs) == ["dev", "prod"]
    # streaming run records the project too
    api.stream_events(f"/execution-stream/{wid}", {"input_data": None, "environment": "default"})
    assert len(api.get(f"/projects/{pid}/runs").json()) == 3
