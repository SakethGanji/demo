"""F-X5b: X3 m17 (edit and export take X-Ensemble-Actor) and X3 m11 (16 concurrent edits: 201 / 409 commit_contended,
never a 400 "try again"). A = API base (env)."""
import collections, os, concurrent.futures as cf
import httpx
A = os.environ.get("A", "http://localhost:18103/api")
H = {"X-Ensemble-Actor": "Ada"}
c = httpx.Client(timeout=300)
r = c.post(f"{A}/datasets", files={"file": ("m11.csv", "a,b,y\n" + "".join(f"{i},{i*2},{i%2}\n" for i in range(200)), "text/csv")},
           data={"name": "fx5b m11", "target": "y", "by": "Ada"}, headers=H)
V = r.json()["id"]; print("dataset", r.status_code, V)
r = c.post(f"{A}/datasets/{V}/edit", json={"ops": [{"op": "add_rows", "rows": [{"a": 999, "b": 1, "y": 0}]}], "reason": "m17 header only"}, headers=H)
print("m17 edit with only X-Ensemble-Actor →", r.status_code, r.json().get("id") if r.status_code < 300 else r.text[:150])
r = c.get(f"{A}/datasets/{V}/export", params={"format": "csv"}, headers=H)
print("m17 export with only X-Ensemble-Actor →", r.status_code, r.headers.get("content-type"))
r = c.get(f"{A}/datasets/{V}/export", headers={})
print("m17 export with no name at all →", r.status_code, r.text[:120])
def one(i):
    rr = httpx.post(f"{A}/datasets/{V}/edit", json={"ops": [{"op": "set_cells", "cells": [{"row_id": 1, "column": "b", "value": 1000 + i}]}],
                                                    "reason": f"m11 {i}", "by": "Ada"}, headers=H, timeout=300)
    d = rr.json().get("detail") if rr.status_code >= 400 else None
    return rr.status_code, (d.get("code") if isinstance(d, dict) else (str(d)[:60] if d else None))
with cf.ThreadPoolExecutor(16) as ex:
    res = list(ex.map(one, range(16)))
print("m11 16 concurrent edits →", dict(collections.Counter(res)))
