"""AUDIT-BACKEND-FIXES #4 — python-route traces/errors are redacted with the project's TEAM secrets too."""

from __future__ import annotations

import json

import pytest

from .conftest import _EchoHandler, uniq
from .journeys._helpers import needs_bwrap, new_project

pytestmark = pytest.mark.integration

SRC = '''from studio import api, Response
import studio


def _seen(upstream):
    auth = f"Bearer {studio.secret('TEAMSEC')}"
    echo = studio.nodes.http_request(method="GET", url=upstream + "/echo",
                                     headers=[{"name": "Authorization", "value": auth}])
    return echo[0]["body"]["headers"]["authorization"]


@api.get("/boom")
def boom(req):
    raise ValueError("upstream said " + _seen("UPSTREAM"))


@api.get("/hdr")
def hdr(req):
    seen = _seen("UPSTREAM")
    return Response(status=200, body={"seen": seen}, headers={"X-Seen": seen})
'''


@needs_bwrap
def test_python_route_error_trace_and_headers_redact_team_secrets(api, test_db, upstream):
    from urllib.parse import urlparse

    P, _ = new_project(api, "d4t")
    team = "team_" + uniq("d4")[-8:].replace("-", "_")
    secret = "team-sec-" + uniq("v")
    test_db.query("INSERT INTO teams (id, name, slug, created_at, updated_at) VALUES (%s, %s, %s, now(), now())",
                  (team, team, team))
    test_db.query("UPDATE projects SET team_id = %s WHERE id = %s", (team, P))
    r = api.post("/variables", {"team_id": team, "key": "TEAMSEC", "value": secret, "type": "secret", "environment": "dev"})
    assert r.status_code in (200, 201), r.text
    test_db.query("UPDATE variables SET allowed_hosts = %s::jsonb WHERE team_id = %s AND key = 'TEAMSEC'",
                  (json.dumps([urlparse(upstream).hostname]), team))
    src = SRC.replace("UPSTREAM", upstream)
    assert api.post(f"/projects/{P}/modules", {"name": "leaky", "source": src}).status_code == 201
    routes = {x["path"]: x["id"] for x in api.get(f"/projects/{P}/routes").json()}
    _EchoHandler.seen.clear()

    for path in ("/boom", "/hdr"):
        r = api.post(f"/projects/{P}/routes/{routes[path]}/test", {})
        assert r.status_code == 200, r.text
        b = r.json()
        assert secret not in r.text, f"{path}: secret in the /test response"
        run = api.get(f"/projects/{P}/runs/{b['execution_id']}")
        assert secret not in run.text, f"{path}: secret in the stored run (trace/error/outputs)"
        # what is STORED, not just what the API shows: execution metadata (trace) and node outputs (error)
        xid = b["execution_id"]
        stored = test_db.query("SELECT metadata::text FROM executions WHERE id = %s", (xid,))
        stored += test_db.query("SELECT row_to_json(n)::text FROM node_outputs n WHERE execution_id = %s", (xid,))
        assert stored, f"{path}: nothing stored?"
        assert all(secret not in (row[0] or "") for row in stored), f"{path}: secret persisted in the database"
    # the secret genuinely reached the upstream, so the redaction above was doing real work
    seen = [s for s in _EchoHandler.seen if s["path"] == "/echo"]
    assert seen and all(s["headers"]["authorization"] == f"Bearer {secret}" for s in seen)
    boom = api.post(f"/projects/{P}/routes/{routes['/boom']}/test", {}).json()
    assert "***" in json.dumps(boom["error"]) and "upstream said" in json.dumps(boom["error"])
    run = api.get(f"/projects/{P}/runs/{boom['execution_id']}").json()
    assert "***" in json.dumps(run["trace"]) or "***" in json.dumps(run)
