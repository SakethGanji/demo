"""Garbage input on the P10–P15 routes of lane M (uploads, plan, keys, relations, versions, diff, edit {table}, verify,
manifest). Prints every 5xx with the request."""
import sys, itertools; sys.path.insert(0, "."); from x3 import *
import requests
VM = json.load(open("Vc.json"))["Vc"]            # a 3-table version with keys
VA = json.load(open("V.json"))["V2"]             # single-table version
ROOT = get(f"/datasets/{VM}")[1].get("root_id")
bodies = [None, [], "x", 1, True, {}, {"columns": None}, {"columns": []}, {"columns": [1]}, {"columns": [""]}, {"columns": ["nope"]},
          {"columns": "a" * 5000}, {"columns": ["cust_id"], "kind": "x"}, {"columns": ["cust_id"], "kind": None}, {"columns": [["x"]]},
          {"columns": ["cust_id", "cust_id"]}, {"columns": ["cust_id"], "reason": 5}, {"columns": ["cust_id"], "by": 7},
          {"from": None}, {"from": {"table": "apps"}}, {"from": {"table": "apps", "columns": "cust_id"}, "to": {"table": "customers", "columns": "cust_id"}},
          {"from": {"table": "apps", "columns": ["amount"]}, "to": {"table": "customers", "columns": ["cust_id"]}},
          {"from": {"table": "apps", "columns": ["cust_id"]}, "to": {"table": "apps", "columns": ["app_id"]}, "cardinality": "1:1"},
          {"from": {"table": "apps", "columns": ["cust_id"]}, "to": {"table": "customers", "columns": ["cust_id"]}, "cardinality": "n:n"},
          {"from": {"table": "nope", "columns": ["x"]}, "to": {"table": "customers", "columns": ["cust_id"]}},
          {"from_table": "apps", "from_columns": ["cust_id"], "to_table": "customers", "to_columns": ["region", "cust_id"]},
          {"table": 5}, {"table": "nope"}, {"table": ["x"]}, {"tables": "x"}, {"tables": {"": {}}}, {"tables": {"apps": "x"}},
          {"tables": {"apps": {"action": "delta", "key": 5}}}, {"tables": {"apps": {"action": "delta", "ops": "x"}}},
          {"tables": {"apps": {"action": "delta", "ops": {"I": "explode"}}}}, {"tables": {"apps": {"action": "delta", "op_column": ""}}},
          {"tables": {"apps": {"action": "delta", "allow_new_columns": "yes"}}}, {"tables": {"apps": {"types": {"x": "BLOB"}}}},
          {"tables": {"apps": {"formats": {"x": 5}}}}, {"tables": {"apps": {"match": "key"}}}, {"tables": {"apps": {"match": {"method": "key", "columns": "x"}}}},
          {"relations": "x"}, {"relations": [None]}, {"relations": [{"from": 1}]}, {"primary_table": 5}, {"primary_table": "nope"},
          {"types": {"a": "INT128"}}, {"target": ["x"]}, {"name": "x" * 100000}, {"match": {"loans": 5}}, {"check_sha256": 5},
          {"ops": [{"op": "set_cells", "cells": [{"row_id": "x", "column": "acct", "value": 1}]}], "reason": "xxx", "by": "Ana", "table": "loans"},
          {"ops": [{"op": "set_cells"}], "reason": "xxx", "by": "Ana", "table": "nope"},
          {"ops": [{"op": "add_rows", "rows": [{"nope": 1}]}], "reason": "xxx", "by": "Ana", "table": "customers"},
          {"ops": [{"op": "delete_rows", "where": {"op": "eq", "column": "nope", "value": 1}}], "reason": "xxx", "by": "Ana", "table": "customers"},
          {"ops": [{"op": "cast_column", "column": "score", "to": "DATE"}], "reason": "xxx", "by": "Ana", "table": "customers"},
          {"ops": [{"op": "rename_column", "column": "cust_id", "to": "region"}], "reason": "xxx", "by": "Ana", "table": "customers"},
          {"ops": [{"op": "drop_column", "column": "cust_id"}], "reason": "xxx", "by": "Ana", "table": "customers"},
          {"ops": [{"op": "set_cells", "cells": [{"row_id": 0, "column": "cust_id", "value": "C005"}]}], "reason": "xxx", "by": "Ana", "table": "customers"}]
weird_ids = ["nope", "ds_00000000", "../../etc", "%2e%2e", "ds_' OR 1=1--", "a" * 300, "ds_\u0000"]
weird_tables = ["nope", "../x", "apps;DROP", "Apps", "", "a" * 300, "%00", "__row_id"]
hits = []; n = 0
def rec(method, path, body=None, **kw):
    global n; n += 1
    try:
        r = requests.request(method, A + path, headers=H, json=body, timeout=60, **kw)
    except Exception as e:
        hits.append((method, path, str(body)[:120], "EXC " + type(e).__name__)); return
    if r.status_code >= 500:
        hits.append((method, path, str(body)[:160], r.status_code, r.text[:160]))
for b in bodies:
    rec("PUT", f"/datasets/{VM}/tables/customers/key", b)
    rec("PUT", f"/datasets/{VM}/relations/r1", b)
    rec("POST", f"/datasets/{VM}/edit", b)
    up, _ = upload([("customers.csv", b"cust_id,region,score\nC001,S,1\nC009,N,2\n")], parent=VM)
    rec("PATCH", f"/uploads/{up}", b); rec("POST", f"/uploads/{up}/check"); rec("POST", f"/uploads/{up}/commit", b)
for t in weird_tables:
    rec("PUT", f"/datasets/{VM}/tables/{t}/key", {"columns": ["x"]})
    rec("GET", f"/datasets/{VM}/grid", params={"table": t}); rec("GET", f"/datasets/{VA}/diff/{VM}", params={"table": t})
    rec("DELETE", f"/datasets/{VM}/relations/{t}"); rec("GET", f"/datasets/{VM}/stats", params={"table": t})
    rec("GET", f"/datasets/{VM}/rows/count", params={"table": t}); rec("POST", f"/datasets/{VM}/rows/preview", {"table": t})
    rec("GET", f"/datasets/{VM}/export", params={"table": t})
for i in weird_ids:
    for p in [f"/datasets/{ROOT}/versions/{i}", f"/datasets/{i}/versions/{VM}", f"/datasets/{i}/manifest", f"/datasets/{i}/verify",
              f"/datasets/{i}/relations", f"/uploads/{i}", f"/datasets/{VM}/diff/{i}"]:
        rec("GET", p)
    rec("POST", f"/uploads/{i}/check"); rec("POST", f"/uploads/{i}/commit", {}); rec("PATCH", f"/uploads/{i}", {})
    rec("PUT", f"/datasets/{i}/tables/x/key", {"columns": ["x"]}); rec("POST", "/uploads", params={"parent_version_id": i}, files=[("file", ("a.csv", b"a,b\n1,2\n3,4\n"))])
# uploads with garbage files
for name, data in [("a.xlsx", b"PK\x03\x04garbage"), ("a.csv", b"\xff\xfe\x00\x00"), ("a.csv", b""), ("a.csv", b"a\n" + b"x" * 10_000_000 + b"\n1\n"),
                   ("a.parquet", b"PAR1xxxxPAR1"), ("a.csv", b'a,b\n"unterminated,1\n2,3\n'), ("a.csv", "a;b\n1;2\n3;4\n".encode()),
                   ("a.csv", b"a,b\n1,2,3,4\n5,6\n"), ("a.xls", b"\xd0\xcf\x11\xe0garbage"), ("../../x.csv", b"a,b\n1,2\n3,4\n")]:
    up, err = upload([(name, data)], name="fz")
    if err and err[0] >= 500: hits.append(("POST", "/uploads", name, err))
    if up:
        rec("POST", f"/uploads/{up}/check"); rec("POST", f"/uploads/{up}/commit", {})
print(n, "requests;", len(hits), "5xx")
for h in hits: print(" ", h)
