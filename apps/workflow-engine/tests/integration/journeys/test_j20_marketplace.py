"""Journey J20: a realistic 'Marketplace' project built by hand (no LLM), end to end.

Three hand-written Python modules (refunds / disputes / customers), four tables, dev+prod secrets,
a project egress allowlist, a release with a missing-secret blocker, promote, behaviour change,
rollback, API-key lifecycle and ~100 historical runs queried through every run filter.

The tests in this file are an ordered story sharing one module-scoped project (`mkt`); run the
file as a whole (``pytest tests/integration/journeys/test_j20_marketplace.py``).
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from urllib.parse import urlparse

import pytest

from ..conftest import _EchoHandler
from ._helpers import bearer, needs_bwrap, new_project, qfilter

pytestmark = [pytest.mark.integration, needs_bwrap]

RISK_DEV, RISK_PROD = "risk-dev-key-7f3a9c1d", "risk-prod-key-b81e44a2"
PAY_DEV, PAY_PROD = "pay-dev-tok-5c0de1aa", "pay-prod-tok-92ab33ef"

REFUNDS = '''from studio import api, Response, HTTPError
import studio

LIMIT = __LIMIT__

@api.post("/refunds", name="createRefund")
def create_refund(req):
    body = req.body or {}
    missing = [k for k in ("order_id", "amount") if k not in body]
    if missing:
        raise HTTPError(422, {"error": "missing_fields", "fields": missing})
    amount = body["amount"]
    if not isinstance(amount, (int, float)) or isinstance(amount, bool) or amount <= 0:
        raise HTTPError(422, {"error": "invalid_amount"})
    if amount > LIMIT:
        raise HTTPError(422, {"error": "amount_exceeds_threshold", "threshold": LIMIT, "amount": amount})
    row = studio.db.table("refunds").insert({"order_id": body["order_id"], "amount": amount, "status": "approved"})
    return Response(status=201, body=row)

@api.get("/refunds", name="listRefunds")
def list_refunds(req):
    flt = {}
    if "status" in req.query:
        flt["status"] = req.query["status"]
    if "min_amount" in req.query:
        flt["amount"] = {"$gte": float(req.query["min_amount"])}
    return studio.db.table("refunds").list(filter=flt or None, sort="-amount", limit=int(req.query.get("limit", 50)))

@api.get("/refunds/stats", name="refundStats", auth="api_key")
def refund_stats(req):
    rows = studio.db.table("refunds").list(limit=1000)
    balance = studio.nodes.http_request(method="GET", url="__UP__/v1/balance",
                                        headers=[{"name": "Authorization", "value": "Bearer " + studio.secret("PAYMENTS_TOKEN")}])
    return {"count": len(rows), "total": sum(r.get("amount") or 0 for r in rows),
            "available": (balance[0].get("body") or {}).get("available")}
'''

DISPUTES = '''from studio import api, Response, HTTPError
import studio

@api.post("/webhooks/disputes", name="ingestDispute")
def ingest(req):
    body = req.body or {}
    eid = body.get("event_id")
    if not eid:
        raise HTTPError(400, {"error": "event_id_required"})
    events = studio.db.table("dispute_events")
    seen = events.list(filter={"event_id": eid}, limit=1)
    if seen:
        return Response(status=200, body={"duplicate": True, "event_id": eid})
    events.insert({"event_id": eid, "type": body.get("type", "unknown")})
    d = studio.db.table("disputes").upsert(["dispute_id"], {"dispute_id": body.get("dispute_id", eid),
                                                              "state": body.get("type", "unknown")})
    return Response(status=201, body={"duplicate": False, "event_id": eid, "dispute": d})

@api.get("/disputes", name="listDisputes")
def list_disputes(req):
    flt = {"state": req.query["state"]} if "state" in req.query else None
    return studio.db.table("disputes").list(filter=flt, sort="dispute_id", limit=200)
'''

CUSTOMERS = '''from studio import api, Response, HTTPError
import studio

@api.get("/customers/{id}/risk", name="customerRisk")
def risk(req):
    cust = studio.db.table("customers").list(filter={"email": req.params["id"] + "@mkt.test"}, limit=1)
    if not cust:
        raise HTTPError(404, {"error": "customer_not_found"})
    out = studio.nodes.http_request(method="GET", url="__UP__/risk/" + req.params["id"],
                                    headers=[{"name": "X-Api-Key", "value": studio.secret("RISK_API_KEY")}])
    return {"customer": cust[0], "risk_echo": out[0].get("body")}

@api.get("/customers/{id}/enrich", name="customerEnrich")
def enrich(req):
    # same upstream, reached through a host that is NOT on the egress allowlist
    out = studio.nodes.http_request(method="GET", url="__BLOCKED__/enrich/" + req.params["id"])
    return {"enriched": out[0].get("body")}
'''


def _src(template: str, up: str, limit: int = 500) -> str:
    blocked = up.replace("127.0.0.1", "localhost")
    return template.replace("__UP__", up).replace("__BLOCKED__", blocked).replace("__LIMIT__", str(limit))


def _routes(api, P):
    return {r["name"]: r for r in api.get(f"/projects/{P}/routes").json()}


@pytest.fixture(scope="module")
def mkt(api, upstream):
    P, S = new_project(api, "mkt")
    host = urlparse(upstream).hostname
    assert api.put(f"/projects/{P}", {"settings": {"egress_allowlist": [host]}}).status_code == 200
    tables = {
        "refunds": [{"name": "order_id", "type": "string", "required": True}, {"name": "amount", "type": "number", "required": True},
                    {"name": "status", "type": "string", "default": "pending"}],
        "dispute_events": [{"name": "event_id", "type": "string", "required": True}, {"name": "type", "type": "string"}],
        "disputes": [{"name": "dispute_id", "type": "string", "required": True}, {"name": "state", "type": "string"}],
        "customers": [{"name": "email", "type": "string", "required": True}, {"name": "tier", "type": "string", "default": "basic"},
                      {"name": "risk_score", "type": "integer"}, {"name": "active", "type": "boolean", "default": True}],
    }
    ids = {}
    for name, cols in tables.items():
        r = api.post(f"/projects/{P}/tables", {"name": name, "columns": cols})
        assert r.status_code == 201, r.text
        ids[name] = r.json()["id"]
    mods = {}
    for name, tpl in (("refunds", REFUNDS), ("disputes", DISPUTES), ("customers", CUSTOMERS)):
        r = api.post(f"/projects/{P}/modules", {"name": name, "source": _src(tpl, upstream)})
        assert r.status_code == 201, r.text
        assert r.json()["errors"] == []
        mods[name] = r.json()["module"]["id"]
    for key, val, hosts in (("RISK_API_KEY", RISK_DEV, [host]), ("PAYMENTS_TOKEN", PAY_DEV, [host])):
        r = api.post(f"/projects/{P}/secrets", {"key": key, "value": val, "environment": "dev", "allowed_hosts": hosts})
        assert r.status_code == 201, r.text
    # prod only has PAYMENTS_TOKEN at first (RISK_API_KEY is the release blocker)
    r = api.post(f"/projects/{P}/secrets", {"key": "PAYMENTS_TOKEN", "value": PAY_PROD, "environment": "prod", "allowed_hosts": [host]})
    assert r.status_code == 201, r.text
    return SimpleNamespace(P=P, S=S, upstream=upstream, host=host, tables=ids, modules=mods, state={})


def _call(api, mkt, method, path, **kw):
    return api.raw(method, f"/api/p/{mkt.S}{path}", **kw)


# ── 1. structure ────────────────────────────────────────────────────────────

def test_01_modules_register_routes_and_summary(api, mkt):
    routes = _routes(api, mkt.P)
    assert {"createRefund", "listRefunds", "refundStats", "ingestDispute", "listDisputes", "customerRisk", "customerEnrich"} <= set(routes)
    assert routes["refundStats"]["auth"] == "api_key" and routes["createRefund"]["handler"]["type"] == "python"
    s = api.get(f"/projects/{mkt.P}/summary").json()
    assert s["counts"]["tables"] == 4 and s["counts"]["routes"] == 7
    missing = {m["key"]: m for m in api.get(f"/projects/{mkt.P}/secrets/missing?environment=prod").json()}
    assert set(missing) == {"RISK_API_KEY"}
    assert any(ref["kind"] == "module" and ref["name"] == "customers" for ref in missing["RISK_API_KEY"]["referenced_by"])
    assert api.get(f"/projects/{mkt.P}/secrets/missing?environment=dev").json() == []
    # module analysis: every module maps cleanly and exposes its handlers
    for name, mid in mkt.modules.items():
        src = api.get(f"/projects/{mkt.P}/modules/{mid}").json()["source"]
        cm = api.post(f"/projects/{mkt.P}/modules/code-map", {"source": src, "module_id": mid}).json()
        assert cm["valid"] is True, (name, cm)


# ── 2. refunds: threshold 422 ───────────────────────────────────────────────

def test_02_refunds_threshold_and_validation(api, mkt):
    r = _call(api, mkt, "POST", "/refunds", json={"order_id": "o-1", "amount": 120.5})
    assert r.status_code == 201 and {k: v for k, v in r.json().items() if k != "id"} == {"order_id": "o-1", "amount": 120.5, "status": "approved"}
    r = _call(api, mkt, "POST", "/refunds", json={"order_id": "o-2", "amount": 500})            # boundary is allowed
    assert r.status_code == 201
    r = _call(api, mkt, "POST", "/refunds", json={"order_id": "o-3", "amount": 500.01})
    assert r.status_code == 422 and r.json() == {"error": "amount_exceeds_threshold", "threshold": 500, "amount": 500.01}
    for body, err in (({"order_id": "x"}, "missing_fields"), ({}, "missing_fields"), ({"order_id": "x", "amount": "9"}, "invalid_amount"),
                      ({"order_id": "x", "amount": -3}, "invalid_amount"), ({"order_id": "x", "amount": True}, "invalid_amount")):
        r = _call(api, mkt, "POST", "/refunds", json=body)
        assert r.status_code == 422 and r.json()["error"] == err, (body, r.text)
    rows = api.get(f"/projects/{mkt.P}/tables/{mkt.tables['refunds']}/rows?count=true").json()
    assert rows["count"] == 2                                                                    # rejected refunds never reached the table
    listed = _call(api, mkt, "GET", "/refunds?min_amount=300").json()
    assert [x["amount"] for x in listed] == [500]
    assert [x["amount"] for x in _call(api, mkt, "GET", "/refunds").json()] == [500, 120.5]    # -amount sort


# ── 3. disputes: idempotent webhook ingestion ───────────────────────────────

def test_03_dispute_webhook_is_idempotent_by_event_id(api, mkt):
    ev = {"event_id": "evt_1", "dispute_id": "dp_1", "type": "opened"}
    r1 = _call(api, mkt, "POST", "/webhooks/disputes", json=ev)
    assert r1.status_code == 201 and r1.json()["duplicate"] is False and r1.json()["dispute"]["dispute_id"] == "dp_1"
    for _ in range(3):
        r = _call(api, mkt, "POST", "/webhooks/disputes", json=ev)
        assert r.status_code == 200 and r.json() == {"duplicate": True, "event_id": "evt_1"}
    # a new event for the same dispute updates state via upsert, not a second row
    r = _call(api, mkt, "POST", "/webhooks/disputes", json={"event_id": "evt_2", "dispute_id": "dp_1", "type": "won"})
    assert r.status_code == 201
    r = _call(api, mkt, "POST", "/webhooks/disputes", json={"event_id": "evt_3", "dispute_id": "dp_2", "type": "opened"})
    assert r.status_code == 201
    assert _call(api, mkt, "POST", "/webhooks/disputes", json={"type": "opened"}).json() == {"error": "event_id_required"}
    ds = _call(api, mkt, "GET", "/disputes").json()
    assert [(d["dispute_id"], d["state"]) for d in ds] == [("dp_1", "won"), ("dp_2", "opened")]
    assert [d["dispute_id"] for d in _call(api, mkt, "GET", "/disputes?state=opened").json()] == ["dp_2"]
    n_events = api.get(f"/projects/{mkt.P}/tables/{mkt.tables['dispute_events']}/rows?count=true").json()["count"]
    assert n_events == 3


# ── 4. customers: egress allowed vs blocked, secret redaction ───────────────

def test_04_customers_risk_egress_allowed_blocked_and_redaction(api, mkt):
    tid = mkt.tables["customers"]
    rows = [{"email": f"c{i:03d}@mkt.test", "tier": ["basic", "gold", "vip"][i % 3], "risk_score": (i * 7) % 100, **({"active": False} if i % 10 == 0 else {})}
            for i in range(60)]
    r = api.post(f"/projects/{mkt.P}/tables/{tid}/rows", {"rows": rows})
    assert r.status_code == 201 and r.json()["inserted"] == 60
    # Public traffic runs in the live env (prod) even before a release; prod has no RISK_API_KEY yet -> clean 502
    r = _call(api, mkt, "GET", "/customers/c007/risk")
    assert r.status_code == 502 and r.json()["error"] == "handler_failed" and r.json()["error_code"] == "missing_secret"
    assert "RISK_API_KEY" in r.json()["message"] and RISK_DEV not in r.text
    # In dev the same handler works: the route tester runs it with dev secrets
    rid = _routes(api, mkt.P)["customerRisk"]["id"]
    _EchoHandler.seen.clear()
    t = api.post(f"/projects/{mkt.P}/routes/{rid}/test", {"params": {"id": "c007"}, "query": {}, "headers": {}, "body": None,
                                                         "environment": "dev", "apply_schemas": False})
    assert t.status_code == 200 and t.json()["status"] == 200, t.text
    body = t.json()["body"]
    assert body["customer"]["email"] == "c007@mkt.test" and body["customer"]["tier"] == "gold"
    assert body["risk_echo"]["path"] == "/risk/c007"
    # the upstream saw the real dev key, but the engine redacts it from everything it returns
    assert _EchoHandler.seen and _EchoHandler.seen[-1]["headers"]["x-api-key"] == RISK_DEV
    assert RISK_DEV not in t.text and "***" in json.dumps(body["risk_echo"]["headers"])
    run = api.get(f"/projects/{mkt.P}/runs/{t.json()['execution_id']}")
    assert run.status_code == 200 and RISK_DEV not in run.text and run.json()["environment"] == "dev"
    # unknown customer -> handler's own 404
    assert _call(api, mkt, "GET", "/customers/nobody/risk").status_code == 404
    # blocked host: localhost is not on the allowlist [127.0.0.1] -> the call fails, nothing reaches upstream
    _EchoHandler.seen.clear()
    r = _call(api, mkt, "GET", "/customers/c007/enrich")
    assert r.status_code >= 400 and r.status_code != 404, r.text
    assert not _EchoHandler.seen
    assert "host_not_allowed" in r.text or "allowlist" in r.text
    denied = api.get(f"/projects/{mkt.P}/broker-calls?status=denied").json()
    assert any(c["error_code"] == "host_not_allowed" for c in denied)


# ── 5. release -> missing-secret blocker -> fix -> promote ───────────────────

def _test_route(api, mkt, name, env="dev", **req):
    rid = _routes(api, mkt.P)[name]["id"]
    body = {"params": {}, "query": {}, "headers": {}, "body": None, "environment": env, "apply_schemas": False, **req}
    return api.post(f"/projects/{mkt.P}/routes/{rid}/test", body)


def test_05_release_blocked_by_missing_prod_secret_then_fixed(api, mkt):
    P = mkt.P
    dry = api.post(f"/projects/{P}/releases?dry_run=true", {"environment": "dev"})
    assert dry.status_code == 200 and all(c["status"] != "fail" for c in dry.json()["checks"])
    assert api.get(f"/projects/{P}/releases").json() == []                                       # a dry run stores nothing
    r = api.post(f"/projects/{P}/releases", {"environment": "dev", "notes": "marketplace v1"})
    assert r.status_code == 201 and r.json()["status"] == "snapshot" and r.json()["number"] == 1
    dev1 = r.json()["id"]
    snap = api.get(f"/projects/{P}/releases/{dev1}").json()["snapshot"]
    assert {m["name"] for m in snap["modules"]} == {"refunds", "disputes", "customers"}
    assert snap["referenced_vars"] == ["PAYMENTS_TOKEN", "RISK_API_KEY"]
    assert all("value" not in b for b in snap["secret_bindings"])                               # fingerprints only, never values
    assert RISK_DEV not in json.dumps(snap) and PAY_DEV not in json.dumps(snap)
    # promotion preview and real promote are both refused for the same reason
    p = api.post(f"/projects/{P}/releases/{dev1}/promote?dry_run=true", {"to_environment": "prod"})
    assert p.status_code == 200
    fail = [c for c in p.json()["checks"] if c["status"] == "fail"]
    assert [(c["id"], c["details"]["missing"], c["details"]["environment"]) for c in fail] == [("secrets_present", ["RISK_API_KEY"], "prod")]
    r = api.post(f"/projects/{P}/releases/{dev1}/promote", {"to_environment": "prod"})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "checks_failed"
    blocked = r.json()["detail"]["release_id"]
    prod = api.get(f"/projects/{P}/releases?environment=prod").json()
    assert [(x["id"], x["status"], x["is_active"]) for x in prod] == [(blocked, "blocked", False)]
    # a blocked release can neither be promoted nor rolled back to
    assert api.post(f"/projects/{P}/releases/{blocked}/promote", {"to_environment": "prod"}).json()["detail"]["code"] == "release_blocked"
    assert api.post(f"/projects/{P}/environments/prod/rollback", {"release_id": blocked}).json()["detail"]["code"] == "release_blocked"
    assert api.post(f"/projects/{P}/environments/prod/rollback", {}).json()["detail"]["code"] == "no_previous_release"
    assert api.post(f"/projects/{P}/environments/dev/rollback", {}).json()["detail"]["code"] == "not_a_release_environment"
    assert api.post(f"/projects/{P}/environments/nope/rollback", {}).status_code == 422
    # fix: add the prod secret (a *different* value), then promote
    r = api.post(f"/projects/{P}/secrets", {"key": "RISK_API_KEY", "value": RISK_PROD, "environment": "prod", "allowed_hosts": [mkt.host]})
    assert r.status_code == 201
    assert api.get(f"/projects/{P}/secrets/missing?environment=prod").json() == []
    r = api.post(f"/projects/{P}/releases/{dev1}/promote", {"to_environment": "prod"})
    assert r.status_code == 201, r.text
    assert (r.json()["environment"], r.json()["is_active"], r.json()["status"], r.json()["number"]) == ("prod", True, "active", 2)
    mkt.state["dev1"], mkt.state["prod1"] = dev1, r.json()["id"]
    # prod now serves the pinned release with the *prod* secret; both values are redacted from responses
    _EchoHandler.seen.clear()
    r = _call(api, mkt, "GET", "/customers/c007/risk")
    assert r.status_code == 200, r.text
    assert _EchoHandler.seen[-1]["headers"]["x-api-key"] == RISK_PROD
    assert RISK_PROD not in r.text and RISK_DEV not in r.text
    assert api.get(f"/projects/{P}/runs/{r.headers['x-execution-id']}").json()["environment"] == "prod"
    s = api.get(f"/projects/{P}/summary").json()
    assert s["active_releases"]["prod"]["release_id"] == mkt.state["prod1"] and s["missing_secrets"] == 0


def test_06_release_changes_behaviour_and_rollback_restores_it(api, mkt):
    P = mkt.P
    ok = {"order_id": "o-rel", "amount": 300}
    assert _call(api, mkt, "POST", "/refunds", json=ok).status_code == 201
    # tighten the threshold in the draft (dev) only
    mid = mkt.modules["refunds"]
    r = api.put(f"/projects/{P}/modules/{mid}", {"source": _src(REFUNDS, mkt.upstream, limit=100)})
    assert r.status_code == 200, r.text
    assert _call(api, mkt, "POST", "/refunds", json=ok).status_code == 201                       # prod still pinned to release 1
    t = _test_route(api, mkt, "createRefund", env="dev", body=ok)
    assert t.json()["status"] == 422 and t.json()["body"]["threshold"] == 100                     # dev serves the draft
    r = api.post(f"/projects/{P}/releases", {"environment": "dev", "notes": "tighten"})
    assert r.status_code == 201 and r.json()["number"] == 2
    dev2 = r.json()["id"]
    d = api.get(f"/projects/{P}/releases/{dev2}/diff?against={mkt.state['dev1']}").json()
    assert d["secrets"] == [] and isinstance(d["routes"], list)
    r = api.post(f"/projects/{P}/releases/{dev2}/promote", {"to_environment": "prod"})
    assert r.status_code == 201 and r.json()["number"] == 3
    prod2 = r.json()["id"]
    r = _call(api, mkt, "POST", "/refunds", json=ok)
    assert r.status_code == 422 and r.json()["threshold"] == 100                                  # new behaviour live
    prod = api.get(f"/projects/{P}/releases?environment=prod").json()
    assert [(x["number"], x["status"]) for x in prod if x["status"] != "blocked"] == [(3, "active"), (2, "superseded")]
    # rotate a prod secret: the release diff reports value_changed, not the value
    sid = next(x["id"] for x in api.get(f"/projects/{P}/secrets?environment=prod").json() if x["key"] == "PAYMENTS_TOKEN")
    assert api.put(f"/projects/{P}/secrets/{sid}", {"value": "pay-prod-rotated-0001"}).status_code == 200
    dry = api.post(f"/projects/{P}/releases/{dev2}/promote?dry_run=true", {"to_environment": "prod"}).json()
    assert {"key": "PAYMENTS_TOKEN", "change": "value_changed"} in dry["diff"]["secrets"]
    assert "pay-prod-rotated-0001" not in json.dumps(dry)
    # roll back: no id -> the previous good release
    r = api.post(f"/projects/{P}/environments/prod/rollback", {})
    assert r.status_code == 200 and r.json()["id"] == mkt.state["prod1"] and r.json()["is_active"] is True
    r = _call(api, mkt, "POST", "/refunds", json=ok)
    assert r.status_code == 201                                                                    # old behaviour is back
    prod = {x["id"]: x for x in api.get(f"/projects/{P}/releases?environment=prod").json() if x["status"] != "blocked"}
    assert prod[prod2]["status"] == "rolled_back" and sum(1 for x in prod.values() if x["is_active"]) == 1
    # rollback to a specific release re-activates it (roll forward)
    r = api.post(f"/projects/{P}/environments/prod/rollback", {"release_id": prod2})
    assert r.status_code == 200 and r.json()["is_active"] is True
    assert _call(api, mkt, "POST", "/refunds", json=ok).status_code == 422
    # rolling back to the already-active release is a no-op
    assert api.post(f"/projects/{P}/environments/prod/rollback", {"release_id": prod2}).json()["id"] == prod2
    assert api.post(f"/projects/{P}/environments/prod/rollback", {"release_id": "rel_missing"}).status_code == 404
    assert api.post(f"/projects/{P}/environments/prod/rollback", {"release_id": mkt.state["dev1"]}).json()["detail"]["code"] == "wrong_environment"
    # leave prod on the original (threshold 500) release for the remaining tests
    assert api.post(f"/projects/{P}/environments/prod/rollback", {"release_id": mkt.state["prod1"]}).status_code == 200


# ── 7. API keys: create / call / revoke -> 401 ─────────────────────────────

def test_07_api_key_lifecycle_on_stats(api, mkt):
    P = mkt.P
    assert _call(api, mkt, "GET", "/refunds/stats").status_code == 401
    assert _call(api, mkt, "GET", "/refunds/stats", headers=bearer("sk_prod_garbage")).status_code == 401
    assert api.post(f"/projects/{P}/api-keys", {"name": "  ", "environment": "dev"}).status_code in (422, 400)
    assert api.post(f"/projects/{P}/api-keys", {"name": "x", "environment": "staging"}).json()["detail"]["code"] == "unknown_environment"
    k = api.post(f"/projects/{P}/api-keys", {"name": "finance-prod", "environment": "prod"})
    assert k.status_code == 201 and k.json()["key"].startswith("sk_prod_")
    key, kid = k.json()["key"], k.json()["id"]
    _EchoHandler.seen.clear()
    r = _call(api, mkt, "GET", "/refunds/stats", headers=bearer(key))
    assert r.status_code == 200, r.text
    assert r.json()["available"] == 12840.25 and r.json()["count"] >= 3
    assert _EchoHandler.seen[-1]["headers"]["authorization"] == f"Bearer pay-prod-rotated-0001"   # rotated prod secret is live (no release needed)
    assert _call(api, mkt, "GET", "/refunds/stats", headers={"X-API-Key": key}).status_code == 200
    # a key issued for one project is useless on another
    Q, SQ = new_project(api, "mkt-other")
    api.post(f"/projects/{Q}/routes", {"method": "GET", "path": "/refunds/stats", "auth": "api_key", "handler": {"type": "workflow", "create": True}})
    assert api.raw("GET", f"/api/p/{SQ}/refunds/stats", headers=bearer(key)).status_code == 401
    listed = {x["id"]: x for x in api.get(f"/projects/{P}/api-keys").json()}
    assert listed[kid]["last_used_at"] and listed[kid]["revoked_at"] is None and "key" not in listed[kid]
    assert api.delete(f"/projects/{P}/api-keys/{kid}").status_code == 200
    assert api.delete(f"/projects/{P}/api-keys/{kid}").status_code == 200                           # revoke is idempotent
    assert api.delete(f"/projects/{P}/api-keys/key_nope").status_code == 404
    assert _call(api, mkt, "GET", "/refunds/stats", headers=bearer(key)).status_code == 401
    assert _call(api, mkt, "GET", "/refunds/stats", headers={"X-API-Key": key}).status_code == 401
    mkt.state["dev_key"] = api.post(f"/projects/{P}/api-keys", {"name": "ci", "environment": "dev"}).json()["key"]


# ── 8. ~100 historical runs and every run filter ───────────────────────────

def _all_runs(api, P, **q):
    qs = "&".join(f"{k}={v}" for k, v in q.items())
    r = api.get(f"/projects/{P}/runs?limit=500" + (("&" + qs) if qs else ""))
    assert r.status_code == 200, r.text
    return r.json()


def test_08_hundred_runs_and_run_filters(api, mkt):
    P = mkt.P
    jobs = []
    for i in range(36):                                              # 27 ok + 9 over-threshold (422)
        jobs.append(("POST", "/refunds", {"json": {"order_id": f"h{i}", "amount": 900 if i % 4 == 3 else 10 + i}}, {}))
    for i in range(30):                                              # 20 distinct events + 10 replays
        jobs.append(("POST", "/webhooks/disputes", {"json": {"event_id": f"h_evt_{i % 20}", "dispute_id": f"h_dp_{i % 7}", "type": "opened"}}, {}))
    for i in range(12):
        jobs.append(("GET", "/refunds/stats", {"headers": bearer(mkt.state["dev_key"])}, {}))
    for i in range(10):
        jobs.append(("GET", f"/customers/c{i:03d}/risk", {}, {}))
    for i in range(8):
        jobs.append(("GET", f"/customers/c{i:03d}/enrich", {}, {}))    # egress-blocked -> failing runs
    assert len(jobs) == 96

    def fire(job):
        method, path, kw, _ = job
        r = _call(api, mkt, method, path, **kw)
        return job, r.status_code, r.headers.get("x-execution-id")
    with ThreadPoolExecutor(max_workers=8) as ex:
        res = list(ex.map(fire, jobs))
    # 4 dev route tests -> exactly 100 fresh runs. The engine keeps only the newest 100 executions (see
    # test_09), so the listing below is precisely this batch.
    for i in range(4):
        assert _test_route(api, mkt, "createRefund", env="dev", body={"order_id": f"t{i}", "amount": 5}).json()["status"] == 201
    assert all(xid for _, _, xid in res)
    assert len({xid for _, _, xid in res}) == 96                                                    # one run per request
    by_path = {}
    for (m, path, _, _), code, _ in res:
        by_path.setdefault(path.split("/")[1] + ("/" + path.split("/")[-1] if path.endswith(("risk", "enrich")) else ""), []).append(code)
    assert by_path["refunds"].count(422) == 9 and by_path["refunds"].count(201) == 27
    assert set(by_path["refunds/stats"] if "refunds/stats" in by_path else []) <= {200}

    runs = _all_runs(api, P)
    assert len(runs) == 100
    assert {x["id"] for x in runs} >= {xid for _, _, xid in res}
    ids = [x["id"] for x in runs]
    assert len(set(ids)) == len(ids)
    starts = [x["start_time"] for x in runs]
    assert starts == sorted(starts, reverse=True)                                                    # newest first
    assert all(x["mode"] == "api" for x in runs if x["id"] in {xid for _, _, xid in res})

    # every simple filter equals the same filter applied client-side to the unfiltered listing
    def ids_of(**q):
        return [x["id"] for x in _all_runs(api, P, **q)]
    for key, vals in (("status", {x["status"] for x in runs}), ("mode", {x["mode"] for x in runs}),
                      ("environment", {"dev", "prod"}), ("route_id", {x["route_id"] for x in runs if x["route_id"]}),
                      ("trigger", {(x["trigger"] or {}).get("type") for x in runs if x["trigger"]})):
        for v in vals:
            want = [x["id"] for x in runs if (x["trigger"] or {}).get("type") == v] if key == "trigger" else [x["id"] for x in runs if x[key] == v]
            assert ids_of(**{key: v}) == want, (key, v)
    assert ids_of(status="no_such_status") == [] and ids_of(environment="staging") == [] and ids_of(route_id="route_nope") == []
    assert len(ids_of(status="failed")) >= 8                                                         # the 8 blocked-egress calls
    triggers = {(x["trigger"] or {}).get("type") for x in runs}
    assert {"public", "api_key", "route_test"} <= triggers
    assert {x["environment"] for x in runs if (x["trigger"] or {}).get("type") == "api_key"} == {"dev"}
    # combined filters AND together
    both = ids_of(environment="prod", status="failed")
    assert both and set(both) == {x["id"] for x in runs if x["environment"] == "prod" and x["status"] == "failed"}
    # route_id filter really isolates a route
    rid = _routes(api, P)["customerEnrich"]["id"]
    enrich = _all_runs(api, P, route_id=rid)
    assert len(enrich) >= 8 and all(x["route_id"] == rid and x["status"] == "failed" for x in enrich)
    assert all(x["route"] and x["route"].startswith("GET /customers/{id}/enrich") for x in enrich)

    # pagination: walk the whole history in pages of 17 through X-Next-Cursor
    seen, cursor, pages = [], None, 0
    while True:
        r = api.get(f"/projects/{P}/runs?limit=17" + (f"&before={cursor}" if cursor else ""))
        assert r.status_code == 200
        page = r.json()
        assert 0 < len(page) <= 17
        seen += [x["id"] for x in page]
        pages += 1
        cursor = r.headers.get("x-next-cursor")
        if not cursor:
            break
        assert cursor == page[-1]["id"] and len(page) == 17
    assert seen == ids and pages == -(-len(ids) // 17)                                               # complete, ordered, no duplicates
    assert api.get(f"/projects/{P}/runs?limit=500&before=exec_pruned_or_unknown").json() == []       # D6: never page 1 again
    assert api.get(f"/projects/{P}/runs?limit=3&status=failed").headers.get("x-next-cursor")
    assert "x-next-cursor" not in api.get(f"/projects/{P}/runs?limit=500").headers
    for bad in (0, 501, -1):
        assert api.get(f"/projects/{P}/runs?limit={bad}").status_code == 422
    # run detail is scoped to its project
    Q, _ = new_project(api, "mkt-scope")
    assert api.get(f"/projects/{Q}/runs/{ids[0]}").status_code == 404
    assert api.get(f"/projects/{Q}/runs").json() == []
    d = api.get(f"/projects/{P}/runs/{ids[0]}").json()
    assert d["id"] == ids[0] and "trace" in d and "errors" in d
    failed = api.get(f"/projects/{P}/runs/{enrich[0]['id']}").json()
    assert failed["errors"] and failed["status"] == "failed"
    # release-pinned runs carry their release number
    pinned = [x for x in runs if x["environment"] == "prod" and x["trigger"] and x["trigger"].get("release_id")]
    if pinned:
        assert api.get(f"/projects/{P}/runs/{pinned[0]['id']}").json()["release"]["environment"] == "prod"


@pytest.mark.xfail(strict=True, reason="BUG: run retention is one global cap of 100 executions for the whole engine "
                                       "(ExecutionRepository(max_records=100) in core/dependencies.py, route_dispatcher, python_handler, "
                                       "draft_runner), so a busy project evicts every other project's history")
def test_09_run_retention_is_per_project(api, mkt):
    quiet, qs = new_project(api, "mkt-quiet")
    wf = api.post(f"/projects/{quiet}/routes", {"method": "GET", "path": "/ping", "auth": "none", "handler": {"type": "workflow", "create": True}})
    assert wf.status_code == 201
    keep = api.raw("GET", f"/api/p/{qs}/ping").headers["x-execution-id"]
    for i in range(101):                                                       # flood a different project
        _test_route(api, mkt, "listRefunds", env="dev")
    assert any(x["id"] == keep for x in api.get(f"/projects/{quiet}/runs").json())
