"""Characterization: webhooks and $vars (variables modal) as used today."""

from __future__ import annotations

import time

import pytest

from .conftest import http_workflow, uniq, webhook_workflow

pytestmark = pytest.mark.integration


def _publish(api, wf: dict) -> str:
    wid = api.post("/workflows", wf).json()["id"]
    assert api.post(f"/workflows/{wid}/publish", {}).status_code == 200
    return wid


def test_webhook_by_id_last_node(api):
    wid = _publish(api, webhook_workflow())
    r = api.raw("POST", f"/webhook/{wid}", json={"x": 5})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "success" and body["executionId"].startswith("exec_")
    assert body["data"] == [{"got": 5}]


def test_webhook_wrong_method_inactive_missing(api):
    wid = _publish(api, webhook_workflow())
    assert api.raw("GET", f"/webhook/{wid}").status_code == 405
    inactive = api.post("/workflows", webhook_workflow()).json()["id"]
    assert api.raw("POST", f"/webhook/{inactive}", json={}).status_code == 400
    assert api.raw("POST", "/webhook/wf_nope", json={}).status_code == 404


def test_webhook_by_path_and_on_received(api):
    path = uniq("p")
    _publish(api, webhook_workflow(path=path, method="PUT"))
    r = api.raw("PUT", f"/webhook/p/{path}", json={"x": "hi"})
    assert r.status_code == 200 and r.json()["data"] == [{"got": "hi"}]
    assert api.raw("POST", f"/webhook/p/{uniq('none')}", json={}).status_code == 404

    wid = _publish(api, webhook_workflow(response_mode="onReceived"))
    r = api.raw("POST", f"/webhook/{wid}", json={"x": 1})
    assert r.status_code == 200
    assert r.json()["status"] == "success" and r.json()["message"] == "Workflow triggered"
    exec_id = r.json()["executionId"]
    for _ in range(40):
        d = api.get(f"/executions/{exec_id}")
        if d.status_code == 200 and d.json()["status"] == "success":
            break
        time.sleep(0.1)
    assert d.json()["status"] == "success"


def test_variables_modal_flow(api):
    env = uniq("env")
    key = "K_" + uniq("v").replace("-", "_").upper()
    # useVariablesApi.ts:64
    r = api.post("/variables", {"team_id": "default", "key": key, "value": "s3cr3t-value", "type": "secret", "environment": env})
    assert r.status_code == 201, r.text
    v = r.json()
    assert v["value"] is None and v["type"] == "secret" and v["environment"] == env
    assert api.post("/variables", {"team_id": "default", "key": key, "value": "x", "type": "secret", "environment": env}).status_code == 409
    plain = api.post("/variables", {"team_id": "default", "key": key + "_P", "value": "plain", "type": "string", "environment": env}).json()

    lst = api.get(f"/variables?environment={env}").json()
    assert {x["key"]: x["value"] for x in lst} == {key: None, key + "_P": "plain"}
    assert set(lst[0]) >= {"id", "key", "environment", "value", "type", "description"}
    assert env in api.get("/variables/environments").json()

    # useVariablesApi.ts:84 always sends description:null
    r = api.put(f"/variables/{plain['id']}", {"value": "plain2", "description": None})
    assert r.status_code == 200 and r.json()["value"] == "plain2"
    assert api.get(f"/variables/{v['id']}").json()["value"] is None
    assert api.delete(f"/variables/{plain['id']}").status_code == 200
    assert api.delete(f"/variables/{plain['id']}").status_code == 404


def test_secret_resolves_in_run_and_url_is_redacted(api, upstream):
    env = uniq("env")
    key = "TOK_" + uniq("v").replace("-", "_").upper()
    api.post("/variables", {"team_id": "default", "key": key, "value": "tok-ABC-123456", "type": "secret", "environment": env})
    wf = http_workflow(f"{upstream}/echo?api_key={{{{ $vars.{key} }}}}",
                       headers=[{"name": "X-Tok", "value": f"{{{{ $vars.{key} }}}}"}])
    wid = api.post("/workflows", wf).json()["id"]
    r = api.post(f"/workflows/{wid}/run", {"input_data": None, "environment": env})
    assert r.status_code == 200, r.text
    out = r.json()["data"]["HTTP"][0]["json"]
    assert out["body"]["headers"]["x-tok"] == "tok-ABC-123456"
    assert out["body"]["query"]["api_key"] == "tok-ABC-123456"
    det = api.get(f"/executions/{r.json()['execution_id']}").json()
    assert "tok-ABC-123456" not in det["node_metrics"]["HTTP"]["requestUrl"]
    assert "***" in det["node_metrics"]["HTTP"]["requestUrl"]


def test_webhook_inbound_vars_substitution_and_x_env(api):
    """Existing behaviour (kept by decision 1): caller payload {{ $vars.X }} is expanded."""
    env = uniq("env")
    key = "W_" + uniq("v").replace("-", "_").upper()
    api.post("/variables", {"team_id": "default", "key": key, "value": "expanded!", "type": "string", "environment": env})
    wid = _publish(api, webhook_workflow())
    r = api.raw("POST", f"/webhook/{wid}", json={"x": f"{{{{ $vars.{key} }}}}"}, headers={"X-Env": env})
    assert r.json()["data"] == [{"got": "expanded!"}]
    r = api.raw("POST", f"/webhook/{wid}?env={env}", json={"x": f"{{{{ $vars.{key} }}}}"})
    assert r.json()["data"] == [{"got": "expanded!"}]
