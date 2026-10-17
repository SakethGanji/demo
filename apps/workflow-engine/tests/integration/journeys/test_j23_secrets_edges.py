"""Journey J23: project secrets — validation, scope precedence, write-only values, host binding edits."""

from __future__ import annotations

import pytest

from ..conftest import uniq
from ._helpers import SET_WF, new_project

pytestmark = pytest.mark.integration


def test_secret_validation_matrix(api):
    P, _ = new_project(api, "sec")
    url = f"/projects/{P}/secrets"
    ok = {"key": "GOOD_KEY", "value": "v", "environment": "dev"}
    cases = [
        ({**ok, "key": "1bad"}, 422, "invalid_key"), ({**ok, "key": "has space"}, 422, "invalid_key"), ({**ok, "key": ""}, 422, "invalid_key"),
        ({**ok, "key": "a-b"}, 422, "invalid_key"), ({**ok, "environment": "staging"}, 422, "unknown_environment"),
        ({**ok, "type": "password"}, 422, "invalid_type"), ({**ok, "allowed_hosts": ["http://a.com"]}, 422, "invalid_allowed_hosts"),
    ]
    for body, status, code in cases:
        r = api.post(url, body)
        assert r.status_code == status and r.json()["detail"]["code"] == code, (body, r.text)
    assert api.post(url, {**ok, "allowed_hosts": "a.com"}).status_code == 422                               # pydantic: must be a list
    assert api.post(url, {"key": "NOVALUE", "environment": "dev"}).status_code == 422                       # pydantic: value required
    r = api.post(url, ok)
    assert r.status_code == 201 and r.json()["value"] is None and r.json()["type"] == "secret"               # secrets are write-only
    assert api.post(url, ok).json()["detail"]["code"] == "exists"                                            # (key, env) is unique
    assert api.post(url, {**ok, "environment": "prod"}).status_code == 201                                   # same key in another env is fine
    assert api.post(url, {"key": "LIMIT", "value": 42, "type": "number", "environment": "dev"}).status_code == 422   # value must be a JSON string
    n = api.post(url, {"key": "LIMIT", "value": "42", "type": "number", "environment": "dev"})
    assert n.status_code == 201 and n.json()["value"] == "42"                                                # non-secret values are readable (stringified)
    s = api.post(url, {"key": "GREETING", "value": "hi", "type": "string", "environment": "dev", "description": "d"}).json()
    assert s["value"] == "hi" and s["description"] == "d"
    default_env = api.post(url, {"key": "NOENV", "value": "x"}).json()
    assert default_env["environment"] == "dev"                                                               # default_environment
    r = api.post(url, {"key": "LONGDESC", "value": "x", "description": "d" * 501})
    assert r.status_code == 422
    listed = api.get(f"{url}?environment=dev").json()
    assert all(x["value"] is None for x in listed if x["type"] == "secret")
    assert {x["environment"] for x in api.get(url).json()} == {"dev", "prod"}


def test_secret_update_delete_and_host_binding(api):
    P, _ = new_project(api, "sec2")
    url = f"/projects/{P}/secrets"
    sid = api.post(url, {"key": "TOKEN", "value": "first", "environment": "dev", "allowed_hosts": ["API.One.com"]}).json()
    assert sid["allowed_hosts"] == ["api.one.com"]                                                           # normalised to lowercase
    r = api.put(f"{url}/{sid['id']}", {"description": "only desc"})
    assert r.status_code == 200 and r.json()["description"] == "only desc" and r.json()["allowed_hosts"] == ["api.one.com"]
    r = api.put(f"{url}/{sid['id']}", {"allowed_hosts": ["*.two.io"]})
    assert r.json()["allowed_hosts"] == ["*.two.io"]
    assert api.put(f"{url}/{sid['id']}", {"allowed_hosts": ["bad host"]}).json()["detail"]["code"] == "invalid_allowed_hosts"
    assert api.put(f"{url}/{sid['id']}", {"allowed_hosts": []}).json()["allowed_hosts"] == []                 # explicit unbind
    assert api.put(f"{url}/{sid['id']}", {"value": None}).status_code == 200                                 # null value = keep
    assert api.put(f"{url}/999999", {"value": "x"}).json()["detail"]["code"] == "secret_not_found"
    Q, _ = new_project(api, "sec3")
    assert api.put(f"/projects/{Q}/secrets/{sid['id']}", {"value": "hijack"}).status_code == 404           # other project cannot touch it
    assert api.delete(f"/projects/{Q}/secrets/{sid['id']}").status_code == 404
    assert api.delete(f"{url}/{sid['id']}").json() == {"success": True}
    assert api.delete(f"{url}/{sid['id']}").status_code == 404


def test_project_secret_overrides_team_variable_at_run_time(api):
    P, _ = new_project(api, "sec4")
    key = "OV_" + uniq("k").replace("-", "_").upper()
    tv = api.post("/variables", {"team_id": "default", "key": key, "value": "team-value", "type": "string", "environment": "dev"})
    assert tv.status_code == 201
    wf = SET_WF("{{ $vars.%s }}" % key)
    W = api.post(f"/projects/{P}/workflows", wf).json()["id"]
    run = lambda: api.post(f"/workflows/{W}/run", {"input_data": None, "environment": "dev"}).json()["data"]["Set"][0]["json"]["answer"]
    assert run() == "team-value"
    ps = api.post(f"/projects/{P}/secrets", {"key": key, "value": "project-value", "type": "string", "environment": "dev"}).json()
    assert ps["overrides_team"] is True
    assert run() == "project-value"                                                                           # project scope wins
    rows = [x for x in api.get(f"/projects/{P}/secrets?environment=dev").json() if x["key"] == key]
    assert {(x["scope"], x["overrides_team"], x["overridden"]) for x in rows} == {("project", True, False), ("team", False, True)}
    assert api.delete(f"/projects/{P}/secrets/{ps['id']}").status_code == 200
    assert run() == "team-value"                                                                              # falls back
    # the workflow references the key, so a missing-secret scan sees it in an env where it is undefined
    miss = {m["key"]: m for m in api.get(f"/projects/{P}/secrets/missing?environment=prod").json()}
    assert key in miss and miss[key]["referenced_by"][0]["kind"] == "workflow"
    assert key not in {m["key"] for m in api.get(f"/projects/{P}/secrets/missing?environment=dev").json()}
    api.delete(f"/variables/{tv.json()['id']}")
