"""UI-DESIGN-REVIEW.md fixes, group 4 — runs.

C2 trigger.type taxonomy + manual runs, C3 redacted/capped request & response on route runs,
D6 retention on every insert path + broker_calls cascade + pruned cursor, C13 UTC expires_at.
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone

import pytest

from .conftest import start_set_workflow, uniq, webhook_workflow
from .journeys._helpers import app_token, bearer, chain, needs_bwrap, new_project

pytestmark = pytest.mark.integration


def _run(api, P, xid):
    r = api.get(f"/projects/{P}/runs/{xid}")
    assert r.status_code == 200, r.text
    return r.json()


def _echo_route(api, P, path="/echo", method="POST"):
    wf = chain(uniq("echo"), {"name": "Respond", "type": "Respond", "position": {"x": 1, "y": 1},
                              "parameters": {"statusCode": 201, "headers": [{"name": "X-Out", "value": "yes"}],
                                             "body": "{{ $json.body }}"}})
    W = api.post(f"/projects/{P}/workflows", wf).json()["id"]
    r = api.post(f"/projects/{P}/routes", {"method": method, "path": path, "handler": {"type": "workflow", "workflow_id": W}})
    assert r.status_code == 201, r.text
    return r.json()["route"]["id"], W


# ── C2 ───────────────────────────────────────────────────────────────────────


def test_c2_trigger_types_on_studio_webhook_and_router_runs(api):
    P, S = new_project(api, "c2")
    W = api.post(f"/projects/{P}/workflows", start_set_workflow()).json()["id"]
    x1 = api.post(f"/workflows/{W}/run").json()["execution_id"]
    x2 = api.post(f"/workflows/{W}/run", {"input_data": {"n": 1}, "environment": "default"}).json()["execution_id"]
    for xid in (x1, x2):
        run = _run(api, P, xid)
        assert run["mode"] == "manual" and run["trigger"]["type"] == "manual", run
    events = api.stream_events(f"/execution-stream/{W}", {"input_data": {"n": 2}, "environment": "default"})
    xs = next(e for e in events if e.get("executionId"))["executionId"]
    assert _run(api, P, xs)["mode"] == "manual" and _run(api, P, xs)["trigger"]["type"] == "manual"

    hook = webhook_workflow(path=uniq("c2hook"))
    HW = api.post(f"/projects/{P}/workflows", hook).json()["id"]
    api.post(f"/workflows/{HW}/publish", {})
    api.raw("POST", f"/webhook/p/{hook['nodes'][0]['parameters']['path']}", json={"x": 1})
    hook_run = next(x for x in api.get(f"/projects/{P}/runs?workflow_id={HW}").json())
    assert hook_run["mode"] == "webhook" and hook_run["trigger"]["type"] == "webhook"

    rid, _ = _echo_route(api, P)
    pub = api.raw("POST", f"/api/p/{S}/echo", json={"a": 1}).headers["x-execution-id"]
    key = api.post(f"/projects/{P}/api-keys", {"name": "k", "environment": "dev"}).json()["key"]
    keyed = api.raw("POST", f"/api/p/{S}/echo", json={"a": 2}, headers=bearer(key)).headers["x-execution-id"]
    tested = api.post(f"/projects/{P}/routes/{rid}/test", {"body": {"a": 3}}).json()["execution_id"]
    A = api.post(f"/projects/{P}/apps", {"name": uniq("app"), "definition": {}}).json()["id"]
    pt = api.post(f"/projects/{P}/apps/{A}/preview-token").json()["token"]
    prev = api.raw("POST", f"/api/p/{S}/echo", json={"a": 4}, headers=bearer(pt)).headers["x-execution-id"]
    types = {x: _run(api, P, x)["trigger"]["type"] for x in (pub, keyed, tested, prev)}
    assert types == {pub: "public", keyed: "api_key", tested: "route_test", prev: "app_preview"}
    assert all(_run(api, P, x)["mode"] == "api" for x in (pub, keyed, tested, prev))

    # GET …/runs?trigger= filters by trigger.type
    only = api.get(f"/projects/{P}/runs?trigger=route_test").json()
    assert [r["id"] for r in only] == [tested]
    assert {r["id"] for r in api.get(f"/projects/{P}/runs?trigger=manual").json()} == {x1, x2, xs}


@pytest.mark.skipif(shutil.which("esbuild") is None, reason="esbuild not on PATH")
def test_c2_app_runs_are_app_or_app_preview(api):
    P, S = new_project(api, "c2a")
    rid, W = _echo_route(api, P, "/e", "POST")
    A = api.post(f"/projects/{P}/apps", {"name": uniq("app"), "definition": {}}).json()["id"]
    src = "export default function App(){return <div>x</div>}"
    api.put(f"/apps/{A}", {"source_code": src, "files": [{"path": "App.tsx", "content": src}], "create_version": True,
                           "version_trigger": "manual", "workflow_ids": [W]})
    slug = uniq("c2a")
    assert api.post(f"/apps/{A}/publish", {"slug": slug, "access": "public"}).status_code == 200
    tok = app_token(api, slug)
    r1 = api.raw("POST", f"/a/{slug}/api/workflow/{W}/run", json={}, headers=bearer(tok)).headers["x-execution-id"]
    r2 = api.raw("POST", f"/api/p/{S}/e", json={}, headers=bearer(tok)).headers["x-execution-id"]
    pt = api.post(f"/projects/{P}/apps/{A}/preview-token").json()["token"]
    r3 = api.raw("POST", f"/api/apps/{A}/workflow/{W}/run", json={}, headers=bearer(pt)).headers["x-execution-id"]
    got = {x: (_run(api, P, x)["trigger"]["type"], _run(api, P, x)["trigger"].get("app_id")) for x in (r1, r2, r3)}
    assert got == {r1: ("app", A), r2: ("app", A), r3: ("app_preview", A)}


def test_c2_default_trigger_type_for_every_mode():
    from src.repositories.execution_repository import default_trigger_type

    assert {m: default_trigger_type(m) for m in ("manual", "webhook", "cron", "retry", "app", "api", "sandbox")} == {
        "manual": "manual", "webhook": "webhook", "cron": "cron", "retry": "retry", "app": "app", "api": "public",
        "sandbox": "draft_test"}


# ── C3 ───────────────────────────────────────────────────────────────────────


def test_c3_route_runs_store_redacted_capped_request_and_response(api):
    P, S = new_project(api, "c3")
    secret = "c3-secret-value-77"
    api.post(f"/projects/{P}/secrets", {"key": "C3K", "value": secret, "environment": "prod", "type": "secret"})
    _echo_route(api, P)
    r = api.raw("POST", f"/api/p/{S}/echo?q=1", json={"msg": f"hello {secret}"},
                headers={"X-Custom": "c", "Cookie": "sess=1", "Authorization": "Bearer nope.nope"})
    # an invalid bearer → 401 still produces no run; send without auth instead
    assert r.status_code == 401
    r = api.raw("POST", f"/api/p/{S}/echo?q=1", json={"msg": f"hello {secret}"}, headers={"X-Custom": "c", "Cookie": "sess=1"})
    assert r.status_code == 201, r.text
    meta = _run(api, P, r.headers["x-execution-id"])["metadata"]
    req, resp = meta["request"], meta["response"]
    assert req["method"] == "POST" and req["path"] == "/echo" and req["query"] == {"q": "1"}
    assert req["body"] == {"msg": "hello ***"} and req["truncated"] is False
    assert req["headers"]["x-custom"] == "c" and "cookie" not in req["headers"] and "authorization" not in req["headers"]
    assert resp["status"] == 201 and resp["body"] == {"msg": "hello ***"} and resp["headers"]["X-Out"] == "yes"
    assert resp["truncated"] is False
    assert secret not in json.dumps(meta)
    # bodies over 16 KB are capped and flagged
    big = {"blob": "x" * 40000}
    r = api.raw("POST", f"/api/p/{S}/echo", json=big)
    meta = _run(api, P, r.headers["x-execution-id"])["metadata"]
    for part in (meta["request"], meta["response"]):
        assert part["truncated"] is True and part["body"] is None
        assert isinstance(part["body_preview"], str) and len(part["body_preview"].encode()) <= 16384
    # run detail resolves the release number when the run was served from a release
    assert _run(api, P, r.headers["x-execution-id"])["release"] is None


# ── D6 ───────────────────────────────────────────────────────────────────────

PROBE = 'from studio import api\nimport studio\n\n@api.get("/p")\ndef p(req):\n    return studio.nodes.set(mode="manual", fields=[{"name": "x", "value": "1"}], keep_only_set=True)\n'


@needs_bwrap
def test_d6_retention_on_every_insert_path_cascades_broker_calls_and_pruned_cursor_is_empty(api, test_db):
    P, S = new_project(api, "d6")
    api.post(f"/projects/{P}/modules", {"name": "probe", "source": PROBE})
    W = api.post(f"/projects/{P}/workflows", start_set_workflow()).json()["id"]
    api.post(f"/projects/{P}/routes", {"method": "GET", "path": "/w", "handler": {"type": "workflow", "workflow_id": W}})
    first = api.raw("GET", f"/api/p/{S}/p")
    assert first.status_code == 200, first.text
    X0 = first.headers["x-execution-id"]
    assert len(api.get(f"/projects/{P}/broker-calls?execution_id={X0}").json()) == 1
    wruns = [api.raw("GET", f"/api/p/{S}/w").headers["x-execution-id"] for _ in range(100)]
    # X0 fell out of the newest 100: the run and its broker calls are gone
    assert api.get(f"/projects/{P}/runs/{X0}").status_code == 404
    assert api.get(f"/projects/{P}/broker-calls?execution_id={X0}").json() == []
    assert test_db.query("select count(*) from broker_calls where execution_id = %s", (X0,))[0][0] == 0
    # a python-route insert also applies retention: the oldest workflow run is pruned
    assert api.get(f"/projects/{P}/runs/{wruns[0]}").status_code == 200
    assert api.raw("GET", f"/api/p/{S}/p").status_code == 200
    assert api.get(f"/projects/{P}/runs/{wruns[0]}").status_code == 404
    assert test_db.query("select count(*) from executions")[0][0] <= 100
    # paging past a pruned cursor returns an empty page (never restarts at page 1)
    r = api.get(f"/projects/{P}/runs?before={X0}&limit=10")
    assert r.status_code == 200 and r.json() == [] and "x-next-cursor" not in r.headers
    r = api.get(f"/projects/{P}/runs?before=exec_does_not_exist")
    assert r.json() == [] and "x-next-cursor" not in r.headers
    # and a live cursor still pages
    page1 = api.get(f"/projects/{P}/runs?limit=5")
    nxt = page1.headers["x-next-cursor"]
    page2 = api.get(f"/projects/{P}/runs?limit=5&before={nxt}").json()
    assert len(page2) == 5 and not {x["id"] for x in page2} & {x["id"] for x in page1.json()}


def test_d6_retention_cleaner_prunes_old_and_orphaned_broker_calls(api, test_db):
    P, _ = new_project(api, "d6c")
    tok = api.post(f"/projects/{P}/broker-tokens", {"environment": "dev"}).json()["token"]
    api.post("/broker/call", {"function": "set", "parameters": {"mode": "manual", "fields": [], "keepOnlySet": True}},
             headers=bearer(tok))
    cid = api.get(f"/projects/{P}/broker-calls").json()[0]["id"]
    test_db.query("update broker_calls set created_at = now() - interval '40 days' where id = %s", (cid,))
    test_db.query("insert into broker_calls (id, execution_id, project_id, environment, origin, node_type, params_redacted, status, created_at) "
                  "values (%s, 'exec_gone_forever', %s, 'dev', 'python_route', 'Set', '{}', 'ok', now())", ("bcall_orphan_" + uniq(), P))
    # Audit #6: run the REAL cleaner tick (not the SQL by hand) against the test database.
    import asyncio

    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker

    from src.engine.retention_cleaner import RetentionCleaner

    async def tick() -> None:
        e = create_async_engine(f"postgresql+asyncpg://{test_db.env()['WORKFLOW_DB_USER']}:{test_db.env()['WORKFLOW_DB_PASSWORD']}"
                                f"@{test_db.env()['WORKFLOW_DB_HOST']}:{test_db.env()['WORKFLOW_DB_PORT']}/{test_db.name}",
                                connect_args={"server_settings": {"search_path": '"workflow-app"'}})
        try:
            await RetentionCleaner(sessionmaker(e, class_=AsyncSession, expire_on_commit=False))._tick()
        finally:
            await e.dispose()

    asyncio.run(tick())
    assert test_db.query("select count(*) from broker_calls where id = %s", (cid,))[0][0] == 0
    assert test_db.query("select count(*) from broker_calls where execution_id = 'exec_gone_forever'")[0][0] == 0
    # a fresh, parented call is NOT pruned
    api.post("/broker/call", {"function": "set", "parameters": {"mode": "manual", "fields": [], "keepOnlySet": True}},
             headers=bearer(tok))
    assert test_db.query("select count(*) from broker_calls where project_id = %s", (P,))[0][0] == 1
    asyncio.run(tick())
    assert test_db.query("select count(*) from broker_calls where project_id = %s", (P,))[0][0] == 1


# ── C13 ──────────────────────────────────────────────────────────────────────


def test_c13_preview_token_expires_at_is_utc_z(api):
    P, _ = new_project(api, "c13")
    A = api.post(f"/projects/{P}/apps", {"name": uniq("app"), "definition": {}}).json()["id"]
    b = api.post(f"/projects/{P}/apps/{A}/preview-token").json()
    assert b["expires_at"].endswith("Z")
    exp = datetime.fromisoformat(b["expires_at"].replace("Z", "+00:00"))
    delta = (exp - datetime.now(timezone.utc)).total_seconds()
    assert 3000 < delta <= 3601   # a naive local-time string would be off by the UTC offset
    d = api.post(f"/projects/{P}/broker-tokens", {"environment": "dev"}).json()
    assert d["expires_at"].endswith("Z")
