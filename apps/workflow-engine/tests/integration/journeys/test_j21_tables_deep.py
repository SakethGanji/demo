"""Journey J21: data-table engine under load — bulk insert, filters, sort, cursor paging, validation, isolation."""

from __future__ import annotations

import pytest

from ._helpers import bearer, new_project, qfilter

pytestmark = pytest.mark.integration

COLS = [{"name": "sku", "type": "string", "required": True}, {"name": "price", "type": "number"},
        {"name": "stock", "type": "integer"}, {"name": "tag", "type": "string"}, {"name": "live", "type": "boolean", "default": False},
        {"name": "added", "type": "datetime"}, {"name": "attrs", "type": "json"}]
N = 250


@pytest.fixture(scope="module")
def tbl(api):
    P, S = new_project(api, "tbl")
    T = api.post(f"/projects/{P}/tables", {"name": "products", "columns": COLS}).json()["id"]
    rows = []
    for i in range(N):
        row = {"sku": f"SKU-{i:04d}", "price": round((i % 50) * 1.5 + 0.5, 2), "stock": i % 17, "live": i % 2 == 0,
               "added": f"2026-01-{(i % 28) + 1:02d}T00:00:00Z", "attrs": {"i": i}}
        if i % 5 == 0:
            row["tag"] = "50%_off"          # LIKE wildcards inside data
        elif i % 5 == 1:
            row["tag"] = "plain"
        if i % 7 == 0:
            del row["price"]                  # absent column
        rows.append(row)
    r = api.post(f"/projects/{P}/tables/{T}/rows", {"rows": rows})
    assert r.status_code == 201 and r.json()["inserted"] == N
    return type("T", (), {"P": P, "S": S, "T": T, "base": f"/projects/{P}/tables/{T}/rows", "rows": rows})


def rows_of(api, tbl, **q):
    qs = "&".join(f"{k}={v}" for k, v in q.items())
    r = api.get(tbl.base + ("?" + qs if qs else ""))
    assert r.status_code == 200, r.text
    return r.json()


def skus(api, tbl, flt=None, **q):
    if flt is not None:
        q["filter"] = qfilter(flt)
    q.setdefault("limit", 1000)
    return [x["data"]["sku"] for x in rows_of(api, tbl, **q)["rows"]]


def test_bulk_limits_and_atomic_validation(api, tbl):
    P, T = tbl.P, tbl.T
    assert api.get(f"/projects/{P}/tables/{T}").json()["row_count"] == N
    big = [{"sku": f"B{i}"} for i in range(1001)]
    r = api.post(tbl.base, {"rows": big})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "too_many_rows"
    assert api.post(tbl.base, {"rows": big[:1000]}).json()["inserted"] == 1000                    # exactly the cap is fine
    assert rows_of(api, tbl, count="true", limit=1)["count"] == N + 1000
    # one bad row in a batch rejects the whole batch and reports every error with its index
    batch = [{"sku": "ok1"}, {"price": 1}, {"sku": "ok2", "stock": 1.5}, {"sku": "ok3", "ghost": 1}, "notanobject"]
    r = api.post(tbl.base, {"rows": batch})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "row_invalid"
    got = {(e["row"], e["column"]) for e in r.json()["detail"]["errors"]}
    assert got == {(1, "sku"), (2, "stock"), (3, "ghost"), (4, None)}
    assert rows_of(api, tbl, count="true", limit=1, filter=qfilter({"sku": {"$contains": "ok"}}))["count"] == 0
    assert api.post(tbl.base, {"rows": []}).json()["inserted"] == 0
    # clean up the 1000 filler rows so the dataset stays at N
    ids = [x["id"] for x in rows_of(api, tbl, filter=qfilter({"sku": {"$contains": "B"}}), limit=1000)["rows"] if x["data"]["sku"].startswith("B")]
    assert len(ids) == 1000
    for i in ids[:3]:
        assert api.delete(f"{tbl.base}/{i}").json() == {"success": True}
    assert api.get(f"{tbl.base}/{ids[0]}").status_code == 404
    assert api.get(f"{tbl.base}/notint").status_code == 422
    # remove the rest through the project-scoped table API in one go: drop+recreate keeps the story simple
    mid = api.post(f"/projects/{P}/tables", {"name": "scratch", "columns": [{"name": "a", "type": "integer"}]}).json()["id"]
    assert api.post(f"/projects/{P}/tables/{mid}/rows", {"rows": [{"a": 1}]}).status_code == 201
    assert api.delete(f"/projects/{P}/tables/{mid}").json() == {"success": True}
    assert api.get(f"/projects/{P}/tables/{mid}/rows").status_code == 404                           # rows go with the table
    for i in ids[3:]:
        api.delete(f"{tbl.base}/{i}")
    assert rows_of(api, tbl, count="true", limit=1)["count"] == N


def test_cursor_paging_is_complete_and_stable_for_every_sort(api, tbl):
    everything = {r["sku"] for r in tbl.rows}
    for sort in ("created_at", "-created_at", "id", "-id", "sku", "-sku", "price", "-price", "stock", "-stock", "tag", "added", "-live"):
        seen, cursor, pages = [], None, 0
        while True:
            q = {"limit": 37, "sort": sort, **({"cursor": cursor} if cursor else {})}
            page = rows_of(api, tbl, **q)
            seen += [x["data"]["sku"] for x in page["rows"]]
            pages += 1
            cursor = page["next_cursor"]
            if not cursor:
                break
            assert len(page["rows"]) == 37
        assert len(seen) == N and set(seen) == everything and pages == -(-N // 37), sort            # no loss, no duplicate
    assert skus(api, tbl, sort="sku", limit=3) == ["SKU-0000", "SKU-0001", "SKU-0002"]
    assert skus(api, tbl, sort="-sku", limit=2) == ["SKU-0249", "SKU-0248"]
    prices = [x["data"].get("price") for x in rows_of(api, tbl, sort="price", limit=1000)["rows"]]
    nums = [p for p in prices if p is not None]
    assert nums == sorted(nums)                                                                      # numeric, not lexicographic (10.0 after 9.5)
    stock = [x["data"]["stock"] for x in rows_of(api, tbl, sort="-stock", limit=1000)["rows"]]
    assert stock == sorted(stock, reverse=True) and stock[0] == 16
    # limit clamping
    assert len(rows_of(api, tbl, limit=-5)["rows"]) == 1
    assert len(rows_of(api, tbl, limit=0)["rows"]) == 50           # quirk (pinned): limit=0 is "unset" -> default page, not clamped to 1
    assert len(rows_of(api, tbl, limit=5000)["rows"]) == N and rows_of(api, tbl, limit=5000)["next_cursor"] is None
    assert len(rows_of(api, tbl)["rows"]) == 50 and rows_of(api, tbl)["next_cursor"]                 # default page = 50
    exact = rows_of(api, tbl, limit=N)
    assert len(exact["rows"]) == N and exact["next_cursor"] is None                                  # exact fit has no phantom page


def test_filters_operators_nesting_and_escaping(api, tbl):
    R = tbl.rows
    price = lambda r: r.get("price")
    want = lambda pred: sorted(r["sku"] for r in R if pred(r))
    f = lambda flt: sorted(skus(api, tbl, flt))
    assert f({"live": True}) == want(lambda r: r["live"] is True)
    assert f({"stock": 3}) == want(lambda r: r["stock"] == 3)
    assert f({"stock": {"$ne": 3}}) == want(lambda r: r["stock"] != 3)
    assert f({"price": {"$gt": 40}}) == want(lambda r: price(r) is not None and price(r) > 40)
    assert f({"price": {"$gte": 40.5, "$lt": 60}}) == want(lambda r: price(r) is not None and 40.5 <= price(r) < 60)
    assert f({"price": {"$lte": 2}}) == want(lambda r: price(r) is not None and price(r) <= 2)
    assert f({"stock": {"$in": [1, 2, 3]}}) == want(lambda r: r["stock"] in (1, 2, 3))
    assert f({"stock": {"$in": []}}) == []
    assert f({"price": {"$exists": False}}) == want(lambda r: "price" not in r)
    assert f({"tag": {"$exists": True}}) == want(lambda r: "tag" in r)
    assert f({"sku": {"$contains": "sku-002"}}) == want(lambda r: "sku-002" in r["sku"].lower())    # case-insensitive
    assert f({"tag": {"$contains": "%"}}) == want(lambda r: r.get("tag") == "50%_off")                # % is literal, not a wildcard
    assert f({"tag": {"$contains": "_"}}) == want(lambda r: r.get("tag") == "50%_off")
    assert f({"tag": {"$contains": "a_n"}}) == []                                                      # "_" must not match any char
    assert f({"added": {"$gte": "2026-01-20T00:00:00Z"}}) == want(lambda r: r["added"] >= "2026-01-20T00:00:00Z")
    assert f({"$and": [{"live": True}, {"stock": {"$gt": 10}}]}) == want(lambda r: r["live"] and r["stock"] > 10)
    assert f({"$or": [{"stock": 0}, {"tag": "plain"}]}) == want(lambda r: r["stock"] == 0 or r.get("tag") == "plain")
    assert f({"$or": [{"$and": [{"live": True}, {"stock": 1}]}, {"sku": "SKU-0009"}]}) == \
        want(lambda r: (r["live"] and r["stock"] == 1) or r["sku"] == "SKU-0009")
    assert f({"attrs": {"i": 7}}) == ["SKU-0007"]                                                     # json column equality is by containment
    assert f({}) == want(lambda r: True)
    one = rows_of(api, tbl, limit=1)["rows"][0]["id"]
    assert [x["id"] for x in rows_of(api, tbl, filter=qfilter({"id": one}))["rows"]] == [one]
    assert len(rows_of(api, tbl, filter=qfilter({"id": {"$in": [one, one - 1]}}))["rows"]) == 2
    c = rows_of(api, tbl, filter=qfilter({"live": True}), count="true", limit=1)
    assert c["count"] == sum(1 for r in R if r["live"]) and len(c["rows"]) == 1 and c["next_cursor"]
    # filter + sort + cursor together
    seen, cur = [], None
    while True:
        p = rows_of(api, tbl, filter=qfilter({"live": True}), sort="-stock", limit=20, **({"cursor": cur} if cur else {}))
        seen += [x["data"]["sku"] for x in p["rows"]]
        cur = p["next_cursor"]
        if not cur:
            break
    assert sorted(seen) == want(lambda r: r["live"]) and len(set(seen)) == len(seen)


def test_query_validation_errors(api, tbl):
    def code(**q):
        r = api.get(tbl.base + "?" + "&".join(f"{k}={v}" for k, v in q.items()))
        assert r.status_code == 422, (q, r.text)
        return r.json()["detail"]["code"]
    assert code(filter="notjson") == "invalid_filter"
    assert code(filter=qfilter([1, 2])) == "invalid_filter"
    assert code(filter=qfilter({"nope": 1})) == "unknown_column"
    assert code(filter=qfilter({"stock": {"$regex": "x"}})) == "invalid_filter"
    assert code(filter=qfilter({"stock": {"$in": 3}})) == "invalid_filter"
    assert code(filter=qfilter({"$or": {"a": 1}})) == "invalid_filter"
    assert code(filter=qfilter({"id": {"$contains": "1"}})) == "invalid_filter"
    assert code(sort="nope") == "unknown_column"
    assert code(sort="-nope") == "unknown_column"
    assert code(cursor="@@@") == "invalid_cursor"
    assert code(cursor="Zm9v") == "invalid_cursor"                                                    # valid base64, wrong shape
    # an injection attempt in a column name is just an unknown column
    assert code(filter=qfilter({"sku') OR 1=1 --": 1})) == "unknown_column"
    assert api.get("/projects/nope/tables/x/rows").status_code == 404
    assert api.get(f"/projects/{tbl.P}/tables/tbl_missing/rows").status_code == 404


def test_row_crud_semantics_and_isolation(api, tbl):
    P = tbl.P
    r = api.post(tbl.base, {"data": {"sku": "NEW-1", "price": 9.99}})
    assert r.status_code == 201 and r.json()["data"] == {"sku": "NEW-1", "price": 9.99, "live": False}   # default applied
    rid = r.json()["id"]
    r = api.patch(f"{tbl.base}/{rid}", {"data": {"stock": 4}})
    assert r.json()["data"] == {"sku": "NEW-1", "price": 9.99, "live": False, "stock": 4}               # PATCH merges
    r = api.put(f"{tbl.base}/{rid}", {"data": {"sku": "NEW-1"}})
    assert r.json()["data"] == {"sku": "NEW-1", "live": False}                                           # PUT replaces (defaults re-applied)
    assert r.json()["updated_at"] >= r.json()["created_at"]
    for bad in ({"sku": None}, {"ghost": 1}, {"stock": 1.5}, {"added": "someday"}, {"live": "yes"}):
        r = api.patch(f"{tbl.base}/{rid}", {"data": bad})
        assert r.status_code == 422 and r.json()["detail"]["code"] == "row_invalid", bad
    assert api.put(f"{tbl.base}/{rid}", {"data": {"price": 1}}).status_code == 422                     # required sku missing
    assert api.get(f"{tbl.base}/{rid}").json()["data"]["sku"] == "NEW-1"                                # failed writes changed nothing
    assert api.post(tbl.base, {"data": "str"}).status_code == 422
    assert api.post(tbl.base, {}).status_code == 422
    # another project cannot see or touch the row
    Q, _ = new_project(api, "tbl-other")
    assert api.get(f"/projects/{Q}/tables/{tbl.T}/rows/{rid}").status_code == 404
    assert api.delete(f"/projects/{Q}/tables/{tbl.T}").status_code == 404
    # a row id from another table is not addressable through this table
    T2 = api.post(f"/projects/{P}/tables", {"name": "other", "columns": [{"name": "a", "type": "string"}]}).json()["id"]
    assert api.get(f"/projects/{P}/tables/{T2}/rows/{rid}").status_code == 404
    assert api.delete(f"/projects/{P}/tables/{T2}/rows/{rid}").status_code == 404
    assert api.delete(f"{tbl.base}/{rid}").json() == {"success": True}


def test_table_schema_evolution(api, tbl):
    P = tbl.P
    tp = f"/projects/{P}/tables"
    T3 = api.post(tp, {"name": "evo", "columns": [{"name": "a", "type": "string"}]}).json()["id"]
    api.post(f"{tp}/{T3}/rows", {"rows": [{"a": "x"}, {"a": "y"}]})
    for bad in ("1bad", "has space", "", "a" * 64, "a-b"):
        assert api.post(tp, {"name": bad, "columns": []}).json()["detail"]["code"] == "invalid_name", bad
    assert api.put(f"{tp}/{T3}", {"name": "products"}).json()["detail"]["code"] == "name_taken"
    assert api.put(f"{tp}/{T3}", {"name": "evo2", "description": "renamed"}).json()["name"] == "evo2"
    # adding an optional column is fine; a new required column without default breaks existing rows
    cols = [{"name": "a", "type": "string"}, {"name": "b", "type": "number"}]
    assert api.put(f"{tp}/{T3}", {"columns": cols}).status_code == 200
    r = api.put(f"{tp}/{T3}", {"columns": cols + [{"name": "c", "type": "string", "required": True}]})
    assert r.status_code == 409 and r.json()["detail"] == {**r.json()["detail"], "code": "rows_incompatible", "count": 2}
    assert api.put(f"{tp}/{T3}", {"columns": cols + [{"name": "c", "type": "string", "required": True, "default": "z"}]}).status_code == 200
    r = api.put(f"{tp}/{T3}", {"columns": [{"name": "a", "type": "integer"}]})                         # strings are not integers
    assert r.status_code == 409 and r.json()["detail"]["count"] == 2
    assert api.put(f"{tp}/{T3}", {"columns": [{"name": "a", "type": "string"}, {"name": "a", "type": "string"}]}).status_code == 422
    names = [t["name"] for t in api.get(tp).json()]
    assert names == sorted(names) and "evo2" in names
    assert api.get(f"{tp}/{T3}").json()["description"] == "renamed"


def test_data_table_function_upsert_filter_delete_via_broker(api, tbl):
    tok = api.post(f"/projects/{tbl.P}/broker-tokens", {}).json()["token"]
    call = lambda params: api.raw("POST", "/api/broker/call", headers=bearer(tok), json={"function": "data_table", "parameters": {"table": "products", **params}})
    for _ in range(3):
        r = call({"operation": "upsert", "matchColumns": ["sku"], "data": {"sku": "UPS-1", "stock": 5}})
        assert r.status_code == 200, r.text
    r = call({"operation": "list", "filter": {"sku": "UPS-1"}})
    assert len(r.json()["items"]) == 1 and r.json()["items"][0]["stock"] == 5
    assert call({"operation": "upsert", "matchColumns": ["sku"], "data": {"sku": "UPS-1", "stock": 9}}).status_code == 200
    items = call({"operation": "list", "filter": {"sku": "UPS-1"}}).json()["items"]
    assert len(items) == 1 and items[0]["stock"] == 9
    assert call({"operation": "delete", "rowId": items[0]["id"]}).status_code == 200
    assert call({"operation": "list", "filter": {"sku": "UPS-1"}}).json()["items"] == []
    r = call({"operation": "list", "filter": {"nope": 1}})
    assert r.status_code == 502 and "nope" in r.json()["error"]["message"]
