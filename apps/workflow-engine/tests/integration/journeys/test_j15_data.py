"""Journey J15 (UI-INTEGRATION.md §6): data tables + DataTable node; plus studio.db via the broker."""

from __future__ import annotations

import pytest

from ._helpers import bearer, chain, new_project, qfilter

pytestmark = pytest.mark.integration


def test_j15_data_tables_and_node(api):
    P, S = new_project(api, "j15")                                                             # 1
    cols = [{"name": "email", "type": "string", "required": True}, {"name": "total", "type": "number"},
            {"name": "status", "type": "string", "default": "open"}]
    r = api.post(f"/projects/{P}/tables", {"name": "orders", "columns": cols})
    assert r.status_code == 201 and r.json()["name"] == "orders" and len(r.json()["columns"]) == 3
    T = r.json()["id"]
    r = api.post(f"/projects/{P}/tables", {"name": "orders", "columns": cols})                  # 2
    assert r.status_code == 409 and r.json()["detail"]["code"] == "name_taken"
    r = api.post(f"/projects/{P}/tables", {"name": "bad", "columns": [{"name": "x", "type": "float"}]})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "invalid_column"
    assert any(t["id"] == T for t in api.get(f"/projects/{P}/tables").json())                   # 3
    assert api.get(f"/projects/{P}/tables/{T}").json()["columns"][2]["default"] == "open"
    base = f"/projects/{P}/tables/{T}/rows"
    r = api.post(base, {"data": {"email": "a@x.io", "total": 10}})                              # 4
    assert r.status_code == 201 and r.json()["data"] == {"email": "a@x.io", "total": 10, "status": "open"}
    R1 = r.json()["id"]
    r = api.post(base, {"rows": [{"email": "b@x.io", "total": 3}, {"email": "c@x.io", "total": 7.5, "status": "paid"},  # 5
                                 {"email": "d@x.io", "total": 1}]})
    assert r.status_code == 201 and r.json()["inserted"] == 3
    r = api.post(base, {"data": {"total": "x"}})                                                # 6
    assert r.status_code == 422 and r.json()["detail"]["code"] == "row_invalid"
    errs = r.json()["detail"]["errors"]
    assert any(e["row"] == 0 and e["column"] == "email" for e in errs) and any(e["row"] == 0 and e["column"] == "total" for e in errs)
    r = api.get(f"{base}?limit=2&sort=-created_at")                                             # 7
    assert len(r.json()["rows"]) == 2 and r.json()["next_cursor"]
    r = api.get(f"{base}?limit=2&sort=-created_at&cursor={r.json()['next_cursor']}")
    assert len(r.json()["rows"]) == 2 and r.json()["next_cursor"] is None

    def emails(flt):
        return {x["data"]["email"] for x in api.get(f"{base}?filter={qfilter(flt)}").json()["rows"]}
    assert emails({"total": {"$gt": 5}}) == {"a@x.io", "c@x.io"}                                # 8
    assert emails({"$or": [{"status": "paid"}, {"email": {"$contains": "b@"}}]}) == {"b@x.io", "c@x.io"}
    assert len(emails({"status": {"$in": ["open"]}})) == 3
    r = api.get(f"{base}?filter={qfilter({'nope': 1})}")
    assert r.status_code == 422 and r.json()["detail"]["code"] == "unknown_column"
    assert api.get(f"{base}?count=true").json()["count"] == 4
    assert api.get(f"{base}/{R1}").status_code == 200                                           # 9
    r = api.patch(f"{base}/{R1}", {"data": {"status": "paid"}})
    assert r.json()["data"]["total"] == 10 and r.json()["data"]["status"] == "paid"
    assert api.put(f"{base}/{R1}", {"data": {"email": "a@x.io", "total": 11}}).json()["data"]["total"] == 11
    assert api.delete(f"{base}/{R1}").json() == {"success": True}
    assert api.get(f"{base}/{R1}").status_code == 404
    new_cols = [{"name": "email", "type": "string", "required": True}, {"name": "total", "type": "integer"},  # 10
                {"name": "status", "type": "string", "default": "open"}]
    r = api.put(f"/projects/{P}/tables/{T}", {"columns": new_cols})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "rows_incompatible" and r.json()["detail"]["count"] == 1
    Q, _ = new_project(api, "j15q")                                                            # 11
    assert api.get(f"/projects/{Q}/tables/{T}").status_code == 404
    node = {"name": "Orders", "type": "DataTable", "parameters": {"table": "orders", "operation": "list", "limit": 10},  # 12
            "position": {"x": 200, "y": 0}}
    WT = api.post(f"/projects/{P}/workflows", chain("list", node)).json()["id"]
    assert api.post(f"/projects/{P}/routes", {"method": "GET", "path": "/orders", "response_mode": "all_items",
                                              "handler": {"type": "workflow", "workflow_id": WT}}).status_code == 201
    r = api.raw("GET", f"/api/p/{S}/orders")
    assert r.status_code == 200 and len(r.json()) == 3 and all({"id", "email"} <= set(x) for x in r.json())
    assert api.delete(f"/projects/{P}/tables/{T}").json() == {"success": True}                  # 13
    assert not any(t["id"] == T for t in api.get(f"/projects/{P}/tables").json())


def test_phase7_studio_db_via_broker_and_code_node(api):
    P, _ = new_project(api, "p7")
    T = api.post(f"/projects/{P}/tables", {"name": "notes", "columns": [{"name": "text", "type": "string", "required": True}]}).json()["id"]
    tok = api.post(f"/projects/{P}/broker-tokens", {}).json()["token"]
    r = api.raw("POST", "/api/broker/call", headers=bearer(tok),
                json={"function": "data_table", "parameters": {"table": "notes", "operation": "insert", "data": {"text": "hi"}}})
    assert r.status_code == 200 and r.json()["items"][0]["text"] == "hi"
    r = api.raw("POST", "/api/broker/call", headers=bearer(tok),
                json={"function": "data_table", "parameters": {"table": "notes", "operation": "insert", "data": {"nope": 1}}})
    assert r.status_code == 502 and "row validation" in r.json()["error"]["message"]
    code = "studio.db.table('notes').insert({'text': 'from code'})\nreturn [{'json': {'n': len(studio.db.table('notes').list())}}]"
    W = api.post(f"/projects/{P}/workflows", chain("c", {"name": "Code", "type": "Code", "parameters": {"code": code},
                                                          "position": {"x": 200, "y": 0}})).json()["id"]
    run = api.post(f"/workflows/{W}/run").json()
    assert run["status"] == "success", run
    assert run["data"]["Code"][0]["json"]["n"] == 2
    assert api.get(f"/projects/{P}/tables").json()[0]["row_count"] == 2
    # table names resolve inside the run's project only
    Q, _ = new_project(api, "p7q")
    qtok = api.post(f"/projects/{Q}/broker-tokens", {}).json()["token"]
    r = api.raw("POST", "/api/broker/call", headers=bearer(qtok),
                json={"function": "data_table", "parameters": {"table": "notes", "operation": "list"}})
    assert r.status_code == 502 and "not found" in r.json()["error"]["message"]
    assert T
