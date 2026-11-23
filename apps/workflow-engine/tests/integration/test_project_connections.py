"""Project-level external database connections: routes, introspection, read-only queries, broker/SDK, Postgres node, plan facts."""

from __future__ import annotations

import pytest

from tests.fixtures.shopdemo import ensure_shopdemo

from .conftest import DB_HOST, uniq
from .journeys._helpers import needs_bwrap

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def shop_url() -> str:
    return ensure_shopdemo()


@pytest.fixture()
def proj(api):
    return api.post("/projects", {"name": uniq("conn")}).json()


@pytest.fixture()
def conn(api, proj, shop_url):
    r = api.post(f"/projects/{proj['id']}/connections", {"name": "Shop Demo", "kind": "postgres", "url": shop_url})
    assert r.status_code == 201, r.text
    assert shop_url not in r.text
    return r.json()


def _tables(api, pid, cid):
    return {t["name"]: t for t in api.get(f"/projects/{pid}/connections/{cid}/schema").json()["tables"]}


def test_create_lists_and_never_returns_the_url(api, proj, conn, shop_url):
    pid = proj["id"]
    assert conn["key"] == "shop_demo" and conn["name"] == "Shop Demo" and conn["kind"] == "postgres"
    assert conn["status"] == "ok" and conn["readonly"] is True and conn["database"] == "shopdemo"
    assert conn["host"] == DB_HOST and conn["table_count"] == 5 and conn["introspected_at"]
    lst = api.get(f"/projects/{pid}/connections")
    assert lst.json() == [conn] and shop_url not in lst.text
    # the URL is a project secret: listed by key, its value write-only
    secrets = api.get(f"/projects/{pid}/secrets").json()
    mine = [s for s in secrets if s["key"] == "CONN_SHOP_DEMO_URL"]
    assert mine and all(s["value"] is None for s in mine) and shop_url not in str(secrets)
    # duplicate name, bad url, bad kind
    assert api.post(f"/projects/{pid}/connections", {"name": "shop demo", "url": shop_url}).status_code == 409
    r = api.post(f"/projects/{pid}/connections", {"name": "x", "url": "not a url ::"})
    assert r.status_code == 422
    assert api.post(f"/projects/{pid}/connections", {"name": "x", "kind": "mysql", "url": shop_url}).status_code == 422
    r = api.post(f"/projects/{pid}/connections", {"url": shop_url})
    assert r.status_code == 422 and shop_url not in r.text   # validation errors never echo the body


def test_unreachable_database_is_still_created_with_error_status(api, proj):
    r = api.post(f"/projects/{proj['id']}/connections",
                 {"name": "Dead", "url": "postgresql://nobody:pw-SECRET-123@127.0.0.1:1/none"})
    assert r.status_code == 201
    c = r.json()
    assert c["status"] == "error" and c["error"] and "pw-SECRET-123" not in r.text and c["table_count"] == 0
    t = api.post(f"/projects/{proj['id']}/connections/{c['id']}/test", {}).json()
    assert t["ok"] is False and t["error"] and "pw-SECRET-123" not in str(t)


def test_test_introspect_and_schema(api, proj, conn):
    pid, cid = proj["id"], conn["id"]
    t = api.post(f"/projects/{pid}/connections/{cid}/test", {}).json()
    assert t["ok"] is True and t["server_version"]
    snap = api.post(f"/projects/{pid}/connections/{cid}/introspect", {}).json()
    assert {x["name"] for x in snap["tables"]} == {"customers", "products", "orders", "order_items", "refunds"}
    tables = _tables(api, pid, cid)
    assert tables["orders"]["row_estimate"] == 2000 and tables["orders"]["kind"] == "table" and tables["orders"]["schema"] == "public"
    cols = {c["name"]: c for c in tables["orders"]["columns"]}
    assert cols["id"]["pk"] is True and cols["customer_id"]["fk"] == {"table": "customers", "column": "id"}
    assert cols["total_cents"]["type"] == "integer" and cols["shipping_address"]["nullable"] is True
    # samples: low-cardinality only
    assert sorted(cols["channel"]["samples"]) == ["mobile", "store", "web"]
    assert cols["total_cents"]["samples"] == [] and cols["created_at"]["samples"] == [] and cols["id"]["samples"] == []
    assert _tables(api, pid, cid)["products"]["columns"][5]["samples"] in ([False, True], [True, False])
    # masking: sensitive-looking columns never carry samples
    cust = {c["name"]: c for c in tables["customers"]["columns"]}
    for name in ("email", "first_name", "last_name", "phone", "city"):
        assert cust[name]["samples"] == [], name
    assert cust["country"]["samples"] and cust["segment"]["samples"]
    assert {c["name"] for c in tables["order_items"]["columns"]} >= {"order_id", "product_id"}
    assert cols["shipping_address"]["samples"] == []


def test_query_route_is_read_only_with_limit_and_params(api, proj, conn):
    pid, cid = proj["id"], conn["id"]
    url = f"/projects/{pid}/connections/{cid}/query"
    r = api.post(url, {"sql": "SELECT status, count(*) AS n FROM orders WHERE channel = %s GROUP BY status ORDER BY n DESC",
                       "params": ["web"], "limit": 3})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["columns"] == ["status", "n"] and len(d["rows"]) == 3 and d["truncated"] is True and d["ms"] >= 0
    assert d["rows"][0]["n"] >= d["rows"][1]["n"]
    d = api.post(url, {"sql": "SELECT id FROM orders ORDER BY id"}).json()
    assert len(d["rows"]) == 100 and d["truncated"] is True            # default limit
    assert api.post(url, {"sql": "SELECT 1", "limit": 501}).status_code == 422
    assert api.post(url, {"sql": "SELECT 1 AS one;"}).json()["rows"] == [{"one": 1}]
    before = api.post(url, {"sql": "SELECT count(*) AS n FROM refunds"}).json()["rows"][0]["n"]
    for bad in ("INSERT INTO refunds (order_id, amount_cents, reason, created_at) VALUES (1, 1, 'x', now())",
                "UPDATE orders SET status = 'paid'", "DELETE FROM refunds", "DROP TABLE refunds",
                "CREATE TABLE zzz (id int)", "TRUNCATE refunds", "SELECT 1; DELETE FROM refunds",
                "WITH x AS (INSERT INTO refunds (order_id, amount_cents, reason, created_at) VALUES (1, 1, 'x', now()) RETURNING id) SELECT * FROM x",
                "WITH x AS (DELETE FROM refunds RETURNING id) SELECT * FROM x",
                "SELECT * INTO zzz FROM refunds", "SET default_transaction_read_only = off", ""):
        r = api.post(url, {"sql": bad})
        assert r.status_code == 422 and r.json()["detail"]["code"] == "query_not_allowed", (bad, r.text)
    assert api.post(url, {"sql": "SELECT count(*) AS n FROM refunds"}).json()["rows"][0]["n"] == before
    r = api.post(url, {"sql": "SELECT * FROM no_such_table"})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "query_failed"


def test_query_timeout(api, proj, conn):
    r = api.post(f"/projects/{proj['id']}/connections/{conn['id']}/query",
                 {"sql": "SELECT count(*) FROM generate_series(1, 2000000000) g"})
    assert r.status_code == 408 and r.json()["detail"]["code"] == "timeout"


def test_delete_removes_connection_and_secret(api, proj, conn):
    pid = proj["id"]
    assert api.delete(f"/projects/{pid}/connections/{conn['id']}").json() == {"success": True}
    assert api.get(f"/projects/{pid}/connections").json() == []
    assert not [s for s in api.get(f"/projects/{pid}/secrets").json() if s["key"] == "CONN_SHOP_DEMO_URL"]
    assert api.delete(f"/projects/{pid}/connections/{conn['id']}").status_code == 404
    assert api.get(f"/projects/{pid}/connections/{conn['id']}/schema").status_code == 404


# ── broker / SDK ─────────────────────────────────────────────────────────────

def _token(api, pid, **body):
    r = api.post(f"/projects/{pid}/broker-tokens", body)
    assert r.status_code == 201, r.text
    return r.json()["token"]


def _call(api, token, params):
    return api.post("/broker/call", {"node": "Postgres", "parameters": params, "items": [{}]},
                    headers={"Authorization": f"Bearer {token}"})


def test_broker_connection_call_is_read_only_and_server_side(api, proj, conn, shop_url):
    pid = proj["id"]
    tok = _token(api, pid)
    r = _call(api, tok, {"connection": "shop_demo", "operation": "query", "query": "SELECT status, count(*) AS n FROM orders GROUP BY 1",
                         "queryParameters": [], "limit": 100})
    assert r.status_code == 200, r.text
    row = r.json()["items"][0]
    assert len(row["rows"]) == 6 and row["truncated"] is False and shop_url not in r.text
    r = _call(api, tok, {"connection": "shop_demo", "query": "SELECT id FROM orders WHERE id < %s ORDER BY id", "queryParameters": [4]})
    assert [x["id"] for x in r.json()["items"][0]["rows"]] == [1, 2, 3]
    r = _call(api, tok, {"connection": "shop_demo", "query": "SELECT id FROM orders", "limit": 7})
    assert len(r.json()["items"][0]["rows"]) == 7 and r.json()["items"][0]["truncated"] is True
    # writes and tricks are refused
    for q in ("DELETE FROM refunds", "WITH x AS (DELETE FROM refunds RETURNING id) SELECT * FROM x", "SELECT 1; SELECT 2"):
        r = _call(api, tok, {"connection": "shop_demo", "query": q})
        assert r.status_code != 200 and "query_not_allowed" in r.text, (q, r.text)
    # the caller can not smuggle in its own connection string or a secret placeholder
    r = _call(api, tok, {"connection": "shop_demo", "connectionString": "postgresql://x:y@evil.example.com/db",
                         "query": "SELECT '{{ $vars.CONN_SHOP_DEMO_URL }}' AS leak"})
    assert shop_url not in r.text
    if r.status_code == 200:   # the placeholder is just text in a literal query: never resolved
        assert r.json()["items"][0]["rows"][0]["leak"].startswith("{{ $vars")
    r = _call(api, tok, {"connection": "nope", "query": "SELECT 1"})
    assert r.status_code == 404 and r.json()["error"]["code"] == "connection_not_found"
    r = _call(api, tok, {"connection": "shop_demo", "operation": "transaction", "statements": []})
    assert r.status_code == 400
    # a read-only token may use a read-only connection; a plain Postgres call is still refused for it
    ro = _token(api, pid, read_only=True)
    assert _call(api, ro, {"connection": "shop_demo", "query": "SELECT 1 AS one"}).status_code == 200
    r = _call(api, ro, {"connectionString": shop_url, "query": "SELECT 1"})
    assert r.status_code == 403


def test_connection_host_passes_a_restrictive_egress_allowlist_without_opening_it(api, proj, conn, shop_url):
    pid = proj["id"]
    api.put(f"/projects/{pid}", {"settings": {"egress_allowlist": ["api.example.com"]}})
    tok = _token(api, pid)
    r = _call(api, tok, {"connection": "shop_demo", "query": "SELECT 1 AS one"})
    assert r.status_code == 200, r.text
    # the allowlist is not opened for other databases / hosts
    r = _call(api, tok, {"connectionString": "postgresql://u:p@127.0.0.2:5433/db", "query": "SELECT 1"})
    assert r.status_code == 403 and r.json()["error"]["code"] in ("host_not_allowed", "secret_not_bound")


@needs_bwrap
def test_sdk_connection_query_through_the_sandbox(api, proj, conn, shop_url):
    pid = proj["id"]
    source = '''from studio import api
import studio

@api.get("/top", name="top")
def top(req):
    db = studio.connection("shop_demo")
    rows = db.query("SELECT channel, count(*) AS n FROM orders WHERE status <> %s GROUP BY channel ORDER BY n DESC", ["cancelled"], limit=10)
    cols = [r["column"] for r in db.schema() if r["table"] == "refunds"]
    return {"rows": rows, "cols": cols}
'''
    assert api.post(f"/projects/{pid}/modules", {"name": "shop", "source": source}).status_code == 201
    route = api.get(f"/projects/{pid}/routes").json()[0]
    r = api.post(f"/projects/{pid}/routes/{route['id']}/test", {"environment": "dev"})
    assert r.status_code == 200 and r.json()["status"] == 200, r.text
    body = r.json()["body"]
    assert {x["channel"] for x in body["rows"]} == {"web", "mobile", "store"}
    assert "amount_cents" in body["cols"] and shop_url not in r.text


# ── Postgres node ────────────────────────────────────────────────────────────

def _wf(params):
    return {"name": uniq("pgconn"), "nodes": [
        {"name": "Start", "type": "Start", "parameters": {}, "position": {"x": 0, "y": 0}},
        {"name": "PG", "type": "Postgres", "parameters": params, "position": {"x": 200, "y": 0}}],
        "connections": [{"source_node": "Start", "target_node": "PG", "source_output": "main", "target_input": "main"}], "settings": {}}


def test_postgres_node_connection_property(api, proj, conn, shop_url):
    pid = proj["id"]
    from src.nodes.integrations.postgres import PostgresNode

    props = {p.name: p for p in PostgresNode.node_description.properties}
    assert props["connectionString"].required is False and "connection" in props
    wid = api.post(f"/projects/{pid}/workflows", _wf(
        {"connection": "shop_demo", "operation": "query", "query": "SELECT count(*) AS n FROM orders", "queryParameters": "[]"})).json()["id"]
    r = api.post(f"/workflows/{wid}/run", {"input_data": None, "environment": "dev"})
    assert r.status_code == 200, r.text
    assert r.json()["data"]["PG"][0]["json"]["rows"] == [{"n": 2000}] and shop_url not in r.text
    wid = api.post(f"/projects/{pid}/workflows", _wf(
        {"connection": "shop_demo", "operation": "query", "query": "DELETE FROM refunds", "queryParameters": "[]"})).json()["id"]
    r = api.post(f"/workflows/{wid}/run", {"input_data": None, "environment": "dev"})
    assert "read-only" in r.text or "query_not_allowed" in r.text
    assert api.post(f"/projects/{pid}/connections/{conn['id']}/query", {"sql": "SELECT count(*) AS n FROM refunds"}).json()["rows"][0]["n"] > 0


# ── plan facts ───────────────────────────────────────────────────────────────

def test_plan_validation_sees_external_tables(api, proj, conn):
    pid = proj["id"]
    body = {"summary": "dashboard", "data": {"tables": [{"name": "saved_views", "owner": "views", "columns": [
        {"name": "customer_id", "type": "integer", "ref": "shop_demo.customers.id"},
        {"name": "bad_id", "type": "integer", "ref": "shop_demo.nothing.id"}]}]},
        "modules": [{"key": "views", "owns": ["saved_views"]}]}
    r = api.post(f"/projects/{pid}/plan/draft", {"body": body}, headers={"Accept": "application/json"})
    assert r.status_code == 200, r.text
    msgs = [i["message"] for i in r.json()["plan"]["issues"] if i["code"] == "PLAN002"]
    assert any("shop_demo.nothing.id" in m for m in msgs)
    assert not any("shop_demo.customers.id" in m for m in msgs)
