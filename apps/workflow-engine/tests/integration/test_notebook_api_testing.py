"""API testing in the http cell (notebook item 4): params, auth modes that take a secret by key (never its value),
form bodies, sentence assertions with the actual value, extraction + chaining through variables, named environments,
the previous-run diff, cURL / API-catalog import, and keeping the requests as a test suite.

Every request goes to the echo upstream, which reports exactly what it received, so each auth mode is checked on the
wire; what the notebook stores is checked for the secret value.
"""

from __future__ import annotations

import base64

import pytest

from .conftest import _EchoHandler, uniq
from .nb_jobs_support import add_secret
from .notebook_support import add, bank_db, nb_api, nb_engine, nb_project, new_notebook, result_of, run_and_wait  # noqa: F401

pytestmark = pytest.mark.integration

SECRET = "s3cr3t-Token-value-42"


@pytest.fixture(scope="module")
def secret_key(nb_api, nb_project) -> str:  # noqa: F811
    key = "API_TOKEN_" + uniq("k").replace("-", "_").upper()
    add_secret(nb_api, nb_project, key, SECRET)
    return key


def _seen(path: str) -> dict:
    return [r for r in _EchoHandler.seen if r["path"] == path][-1]


def test_assertions_report_the_actual_value_and_extract_chains_into_the_next_request(nb_api, nb_project, upstream):  # noqa: F811
    api = nb_api
    nb = new_notebook(api, nb_project)
    p1 = "/" + uniq("login")
    c1 = add(api, nb, "http", {"method": "POST", "url": upstream + p1, "body": {"token": "tok-123", "user": {"id": 7}},
                               "assertions": [{"kind": "status", "value": 200},
                                              {"kind": "time", "op": "lt", "value": 5000},
                                              {"kind": "json_path", "path": "$.body.user.id", "op": "eq", "value": 7},
                                              {"kind": "header", "name": "content-type", "op": "contains",
                                               "value": "json"},
                                              {"kind": "schema", "json_schema": {
                                                  "type": "object", "required": ["method", "body"],
                                                  "properties": {"method": {"type": "string"}}}}],
                               "extract": [{"name": "tok", "path": "$.body.token"}]})
    p2 = "/" + uniq("me")
    c2 = add(api, nb, "http", {"method": "GET", "url": upstream + p2, "params": {"q": "a b", "n": 3},
                               "headers": {"X-Session": "{{tok}}", "X-Again": "{{vars.tok}}"},
                               "assertions": [{"kind": "json_path", "path": "$.query.q", "value": "a b"},
                                              {"kind": "status", "value": 201},
                                              {"kind": "json_path", "path": "$.nope", "op": "exists"}]})
    run = run_and_wait(api, nb)
    r1 = result_of(run, c1)
    assert r1["status"] == "success", r1
    o1 = r1["output"]
    assert [a["passed"] for a in o1["assertions"]] == [True] * 5
    assert o1["assertions"][0]["label"] == "Status is 200" and o1["assertions"][0]["actual"] == 200
    assert o1["assertions"][1]["label"] == "Responds in under 5000 ms" and isinstance(o1["assertions"][1]["actual"], float)
    assert o1["assertions"][2]["actual"] == 7
    assert o1["vars"] == {"tok": "tok-123"}
    assert isinstance(o1["ms"], float) and o1["size"] > 0 and "content-type" in {k.lower() for k in o1["headers"]}
    r2 = result_of(run, c2)
    assert r2["status"] == "error" and r2["error"]["code"] == "http_failed", r2
    a = r2["output"]["assertions"]
    assert [x["passed"] for x in a] == [True, False, False]
    assert a[1]["actual"] == 200 and a[1]["expected"] == 201
    assert a[2]["label"] == "$.nope exists"
    assert "Status is 201: got 200" in r2["error"]["message"]
    got = _seen(p2)
    assert got["headers"]["x-session"] == "tok-123" and got["headers"]["x-again"] == "tok-123"
    assert r2["output"]["body"]["query"] == {"q": "a b", "n": "3"}
    base, _, qs = r2["output"]["url"].partition("?")
    assert base == upstream + p2 and sorted(qs.split("&")) == ["n=3", "q=a%20b"]  # stored JSONB orders the keys


def test_auth_modes_send_the_secret_and_never_store_it(nb_api, nb_project, upstream, secret_key):  # noqa: F811
    api = nb_api
    nb = new_notebook(api, nb_project)
    paths = {m: "/" + uniq(m) for m in ("bearer", "basic", "hkey", "qkey", "none", "form")}
    cells = {
        "bearer": add(api, nb, "http", {"url": upstream + paths["bearer"], "auth": {"type": "bearer", "secret": secret_key}}),
        "basic": add(api, nb, "http", {"url": upstream + paths["basic"],
                                       "auth": {"type": "basic", "secret": secret_key, "username": "ana"}}),
        "hkey": add(api, nb, "http", {"url": upstream + paths["hkey"],
                                      "auth": {"type": "api_key", "secret": secret_key, "name": "X-Api-Key"}}),
        "qkey": add(api, nb, "http", {"url": upstream + paths["qkey"], "params": {"page": 2},
                                      "auth": {"type": "api_key", "secret": secret_key, "name": "key",
                                               "add_to": "query"}}),
        "none": add(api, nb, "http", {"url": upstream + paths["none"], "auth": {"type": "none"}}),
        "form": add(api, nb, "http", {"method": "POST", "url": upstream + paths["form"], "body_type": "form",
                                      "body": {"amount": "10.50", "note": "a&b"}}),
    }
    run = run_and_wait(api, nb)
    for name, c in cells.items():
        assert result_of(run, c)["status"] == "success", (name, result_of(run, c))
    assert _seen(paths["bearer"])["headers"]["authorization"] == f"Bearer {SECRET}"
    assert _seen(paths["basic"])["headers"]["authorization"] == "Basic " + base64.b64encode(f"ana:{SECRET}".encode()).decode()
    assert _seen(paths["hkey"])["headers"]["x-api-key"] == SECRET
    assert "authorization" not in _seen(paths["none"])["headers"]
    q = result_of(run, cells["qkey"])["output"]["body"]["query"]
    assert q["page"] == "2" and q["key"] == "***"  # sent (the echo saw it), redacted in what is stored
    f = result_of(run, cells["form"])["output"]["body"]
    assert sorted(f["body"].split("&")) == ["amount=10.50", "note=a%26b"]
    assert f["headers"]["content-type"] == "application/x-www-form-urlencoded"
    full = api.get(f"/notebooks/{nb['id']}").json()
    text = str(full) + str(run)
    assert SECRET not in text and base64.b64encode(f"ana:{SECRET}".encode()).decode() not in text
    assert "{{ $vars.%s }}" % secret_key in str(result_of(run, cells["qkey"])["output"]["url"])
    # a bad auth spec is refused at the door
    r = api.post(f"/notebooks/{nb['id']}/cells", {"kind": "http", "spec": {"url": upstream, "auth": {"type": "bearer"}}})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "invalid_spec", r.text
    r = api.post(f"/notebooks/{nb['id']}/cells", {"kind": "http", "spec": {"url": upstream, "auth": {
        "type": "bearer", "secret": "x }} {{ $vars.OTHER"}}})
    assert r.status_code == 422


def test_environments_feed_variables_and_secret_refs(nb_api, nb_project, upstream, secret_key):  # noqa: F811
    api = nb_api
    nb = new_notebook(api, nb_project)
    assert api.get(f"/notebooks/{nb['id']}/environments").json() == {"sets": []}
    envs = {"active": "staging", "sets": [
        {"name": "dev", "vars": [{"name": "base_url", "value": upstream + "/dev"}]},
        {"name": "staging", "vars": [{"name": "base_url", "value": upstream + "/stg"},
                                     {"name": "token", "secret": secret_key}]}]}
    r = api.put(f"/notebooks/{nb['id']}/environments", envs)
    assert r.status_code == 200, r.text
    assert api.get(f"/notebooks/{nb['id']}/environments").json() == envs
    bad = api.put(f"/notebooks/{nb['id']}/environments", {"active": "prod", "sets": []})
    assert bad.status_code == 422
    c1 = add(api, nb, "http", {"url": "{{base_url}}/refunds", "headers": {"Authorization": "Bearer {{token}}"},
                               "assertions": [{"kind": "json_path", "path": "$.path", "value": "/stg/refunds"}]})
    run = run_and_wait(api, nb)
    res = result_of(run, c1)
    assert res["status"] == "success", res
    assert _seen("/stg/refunds")["headers"]["authorization"] == f"Bearer {SECRET}"
    assert SECRET not in str(res)
    envs["active"] = "dev"  # dev has no token: the reference is reported, nothing is sent
    api.put(f"/notebooks/{nb['id']}/environments", envs)
    bad = result_of(run_and_wait(api, nb), c1)
    assert bad and bad["error"], bad
    assert bad["error"]["code"] == "bad_ref" and "no variable 'token'" in bad["error"]["message"], bad
    envs["sets"][0]["vars"].append({"name": "token", "secret": secret_key})
    api.put(f"/notebooks/{nb['id']}/environments", envs)
    run = run_and_wait(api, nb)
    res = result_of(run, c1)
    assert res is not None and res["status"] == "error", run
    assert res["output"]["assertions"][0]["actual"] == "/dev/refunds"
    # an unknown variable is a bad reference
    c2 = add(api, nb, "http", {"url": "{{nope}}/x"})  # c1 fails above, so run from c2
    assert result_of(run_and_wait(api, nb, from_cell_id=c2["id"]), c2)["error"]["code"] == "bad_ref"


def test_a_rerun_carries_the_previous_response_for_the_diff(nb_api, nb_project, upstream):  # noqa: F811
    api = nb_api
    nb = new_notebook(api, nb_project)
    c1 = add(api, nb, "http", {"method": "POST", "url": upstream + "/diff", "body": {"v": 1}})
    first = result_of(run_and_wait(api, nb), c1)["output"]
    assert "last" not in first
    api.patch(f"/notebook-cells/{c1['id']}", {"spec": {"method": "POST", "url": upstream + "/diff", "body": {"v": 2}}})
    second = result_of(run_and_wait(api, nb, reapply=[c1["id"]]), c1)["output"]  # a POST re-sends only when asked
    assert second.get("last"), second
    assert second["last"]["status"] == 200, second["last"] and second["last"]["body"]["body"] == {"v": 1}
    assert second["body"]["body"] == {"v": 2}


def test_import_from_curl_and_from_the_api_catalog(nb_api, nb_project, upstream):  # noqa: F811
    api = nb_api
    nb = new_notebook(api, nb_project)
    curl = (f"curl -X POST '{upstream}/refunds?page=1&api_key=SHOULDNOTKEEP' -H 'Content-Type: application/json' "
            "-H 'Authorization: Bearer abc.def' -H 'X-Trace: 1' --data-raw '{\"amount\": 5}'")
    r = api.post(f"/notebooks/{nb['id']}/import-http", {"curl": curl})
    assert r.status_code == 200, r.text
    [p] = r.json()["planned"]
    assert p["spec"]["method"] == "POST" and p["spec"]["url"] == upstream + "/refunds"
    assert p["spec"]["params"] == {"page": "1"} and p["spec"]["body"] == {"amount": 5}
    assert p["spec"]["headers"] == {"Content-Type": "application/json", "X-Trace": "1"}
    assert "abc.def" not in r.text and "SHOULDNOTKEEP" not in r.text
    assert len(r.json()["warnings"]) == 2 and r.json()["cells"] == []
    form = api.post(f"/notebooks/{nb['id']}/import-http",
                    {"curl": f"curl {upstream}/f -d 'a=1' -d 'b=two' -u ana:pw", "add": True}).json()
    assert form["planned"][0]["spec"]["body"] == {"a": "1", "b": "two"} and form["planned"][0]["spec"]["body_type"] == "form"
    assert form["cells"][0]["kind"] == "http" and "pw" not in str(form["cells"][0]["spec"])
    assert api.post(f"/notebooks/{nb['id']}/import-http", {"curl": "wget x"}).status_code == 422

    spec_text = """openapi: 3.0.0
info: {title: Refunds, version: '1'}
servers: [{url: '%s'}]
paths:
  /refunds:
    get:
      operationId: listRefunds
      summary: List refunds
      responses: {'200': {description: ok}}
  /refunds/{id}:
    get:
      operationId: getRefund
      parameters: [{name: id, in: path, required: true, schema: {type: string}}]
      responses: {'200': {description: ok}, '404': {description: missing}}
""" % upstream
    sp = api.post("/api-specs", {"project_id": nb_project["id"], "spec_text": spec_text})
    assert sp.status_code in (200, 201), sp.text
    sid = sp.json()["id"]
    r = api.post(f"/notebooks/{nb['id']}/import-http", {"spec_id": sid, "add": True})
    assert r.status_code == 200, r.text
    planned = r.json()["planned"]
    assert [x["title"] for x in planned] == ["List refunds", "GET /refunds/{id}"]
    assert planned[0]["spec"]["api_ref"] == f"{sid}:listRefunds"
    assert planned[0]["spec"]["assertions"] == [{"kind": "status", "op": "eq", "value": 200, "path": None,
                                                 "name": None, "json_schema": None}]
    assert planned[1]["spec"]["url"] == upstream + "/refunds/{{id}}"
    run = run_and_wait(api, nb)
    listed = [c for c in r.json()["cells"]]
    assert result_of(run, listed[0])["status"] == "success", result_of(run, listed[0])
    assert api.post(f"/notebooks/{nb['id']}/import-http", {"spec_id": "nope"}).status_code == 404


def test_keep_as_test_suite_judges_the_kept_requests_the_same_way(nb_api, nb_project, upstream, secret_key):  # noqa: F811
    api = nb_api
    nb = new_notebook(api, nb_project)
    api.put(f"/notebooks/{nb['id']}/environments", {"active": "ci", "sets": [
        {"name": "ci", "vars": [{"name": "base_url", "value": upstream}]}]})
    c1 = add(api, nb, "http", {"method": "POST", "url": "{{base_url}}/token", "body": {"token": "T-9"},
                               "assertions": [{"kind": "status", "value": 200}],
                               "extract": [{"name": "tok", "path": "$.body.token"}]})
    c2 = add(api, nb, "http", {"url": "{{base_url}}/orders", "params": {"limit": 5},
                               "headers": {"X-Tok": "{{tok}}"},
                               "auth": {"type": "api_key", "secret": secret_key, "name": "k", "add_to": "query"},
                               "assertions": [{"kind": "json_path", "path": "$.headers.x-tok", "value": "T-9"},
                                              {"kind": "header", "name": "Content-Type", "op": "exists"},
                                              {"kind": "time", "op": "lt", "value": 5000}]})
    run = run_and_wait(api, nb)
    assert result_of(run, c1)["status"] == result_of(run, c2)["status"] == "success", run
    p = api.post(f"/notebooks/{nb['id']}/keep/preview", {"target": "test_suite"}).json()
    assert p["can_save"] is True, p["items"]
    nodes = {n["name"]: n for n in p["definition"]["nodes"]}
    assert nodes["c2_run"]["parameters"]["url"] == "{{ $json.req.url }}k={{ $vars.%s }}" % secret_key
    r = api.post(f"/notebooks/{nb['id']}/keep", {"target": "test_suite"})
    assert r.status_code == 201, r.text
    wid = r.json()["workflow_id"]
    res = api.post(f"/workflows/{wid}/tests/run", {}).json()
    assert res["summary"]["passed"] == res["summary"]["total"] == 1, str(res["results"])[:3000]
    # and the kept workflow runs live: the token chains and the key goes on the query
    ex = api.post(f"/workflows/{wid}/run", {"input_data": {}}).json()
    assert ex["status"] in ("success", "completed"), str(ex.get("errors") or ex)[-3000:]
    seen = _seen("/orders")
    assert seen["headers"]["x-tok"] == "T-9"
    # a failing check blocks keeping a suite until it passes
    api.patch(f"/notebook-cells/{c1['id']}", {"spec": {**c1["spec"], "assertions": [{"kind": "status", "value": 418}]}})
    run_and_wait(api, nb, reapply=[c1["id"]])  # c1 is a POST: a rerun re-sends it only when asked
    p = api.post(f"/notebooks/{nb['id']}/keep/preview", {"target": "test_suite"}).json()
    assert p["can_save"] is False and "failed in the last run" in next(
        i for i in p["items"] if i["key"] == "excluded:run")["message"]
