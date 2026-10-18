"""``run_workflow`` end to end: a python route (real sandbox, real broker socket) calls a workflow of its project.

Draft environment = the workflow's draft; a promoted release = its latest published version (python handlers don't pin
the workflows they call). A PythonStep that calls the workflow running it is refused as a cycle.
"""

from __future__ import annotations

import pytest

from .conftest import uniq
from .journeys._helpers import needs_bwrap, new_project

pytestmark = pytest.mark.integration


def plain(items: list[dict]) -> list[dict]:
    """Without the Execute Workflow Trigger's bookkeeping fields (_triggeredAt, ...), as any ExecuteWorkflow returns."""
    return [{k: v for k, v in i.items() if not k.startswith("_")} for i in items]


def scorer(name: str, tag: str) -> dict:
    return {
        "name": name,
        "nodes": [
            {"name": "Inputs", "type": "ExecuteWorkflowTrigger", "parameters": {}, "position": {"x": 0, "y": 0}},
            {"name": "Mark", "type": "Set", "parameters": {"mode": "manual", "fields": [{"name": "tag", "value": tag}]},
             "position": {"x": 200, "y": 0}},
        ],
        "connections": [{"source_node": "Inputs", "target_node": "Mark", "source_output": "main", "target_input": "main"}],
        "settings": {},
    }


ROUTES = '''from studio import api, BrokerError
import studio


@api.post("/score", name="score")
def score(req):
    return studio.nodes.run_workflow(workflow="{name}", input=req.json())


@api.get("/missing", name="missing")
def missing(req):
    try:
        studio.nodes.run_workflow(workflow="no-such-workflow")
    except BrokerError as e:
        return {{"code": e.code, "status": e.status}}
'''


@needs_bwrap
def test_route_runs_draft_in_dev_and_published_version_in_release(api):
    P, S = new_project(api, "rw")
    name = uniq("scorer")
    r = api.post(f"/projects/{P}/workflows", scorer(name, "v1"))
    assert r.status_code == 201, r.text
    W = r.json()["id"]
    assert api.post(f"/workflows/{W}/publish", {}).status_code == 200
    assert api.put(f"/workflows/{W}", scorer(name, "v2-draft")).status_code == 200
    r = api.post(f"/projects/{P}/modules", {"name": "score", "source": ROUTES.format(name=name)})
    assert r.status_code == 201, r.text
    routes = {x["name"]: x["id"] for x in api.get(f"/projects/{P}/routes").json()}

    # dev (draft environment): the draft runs
    r = api.post(f"/projects/{P}/routes/{routes['score']}/test", {"body": {"amount": 5}})
    assert r.status_code == 200, r.text
    assert plain(r.json()["body"]) == [{"amount": 5, "tag": "v2-draft"}], r.text
    # an unknown workflow is a structured BrokerError
    r = api.post(f"/projects/{P}/routes/{routes['missing']}/test", {})
    assert r.json()["body"] == {"code": "workflow_not_found", "status": 404}, r.text

    # prod (a promoted release): the latest published version, never the draft
    rel = api.post(f"/projects/{P}/releases", {"environment": "dev"})
    assert rel.status_code == 201, rel.text
    assert api.post(f"/projects/{P}/releases/{rel.json()['id']}/promote", {"to_environment": "prod"}).status_code == 201
    r = api.raw("POST", f"/api/p/{S}/score", json={"amount": 7})
    assert r.status_code == 200, r.text
    assert plain(r.json()) == [{"amount": 7, "tag": "v1"}]

    # the child runs are ordinary sub-workflow runs of the workflow
    r = api.get(f"/executions?workflow_id={W}&include_children=true")
    assert r.status_code == 200, r.text
    assert len(r.json()) == 2, r.text                      # the dev call and the prod call


LOOP = '''import studio
from studio import BrokerError


def again(items):
    try:
        studio.nodes.run_workflow(workflow="{name}", input={{"again": True}})
        return [{{"inner": "ran"}}]
    except BrokerError as e:
        return [{{"inner": e.code, "status": e.status}}]
'''


@needs_bwrap
def test_python_step_calling_its_own_workflow_is_a_cycle(api):
    P, _ = new_project(api, "rwc")
    name = uniq("loop")
    r = api.post(f"/projects/{P}/modules", {"name": "loopmod", "source": LOOP.format(name=name)})
    assert r.status_code == 201, r.text
    body = {
        "name": name,
        "nodes": [
            {"name": "Inputs", "type": "ExecuteWorkflowTrigger", "parameters": {}, "position": {"x": 0, "y": 0}},
            {"name": "Step", "type": "PythonStep", "parameters": {"module": "loopmod", "function": "again"},
             "position": {"x": 200, "y": 0}},
        ],
        "connections": [{"source_node": "Inputs", "target_node": "Step", "source_output": "main", "target_input": "main"}],
        "settings": {},
    }
    assert api.post(f"/projects/{P}/workflows", body).status_code == 201
    tok = api.post(f"/projects/{P}/broker-tokens", {"environment": "dev"}).json()["token"]
    r = api.post("/broker/call", {"function": "run_workflow", "parameters": {"workflow": name}},
                 headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200, r.text
    assert r.json()["items"] == [{"inner": "SUBWORKFLOW_CYCLE", "status": 508}], r.text
