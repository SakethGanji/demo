"""X5/x3 probes: X3 m17 (edit ignores X-Ensemble-Actor), m11 (contended edit 400 vs 409), the open note on non-text `by`
in shares / certifications / contracts, and m10 (POST /runs on a tampered table with another row count).
Usage: probes.py <port> <data dir> [postgres url]   (venv python; prints every call and its response)"""
import concurrent.futures as cf
import json, os, sqlite3, sys, threading
import pyarrow.parquet as pq
import requests

P, DATA = sys.argv[1], sys.argv[2]
PG = sys.argv[3] if len(sys.argv) > 3 else None
A = f"http://localhost:{P}/api"


def call(who, method, path, body=None, **kw):
    h = {"X-Ensemble-Actor": who} if who else {}
    r = requests.request(method, A + path, headers=h, json=body, timeout=600, **kw)
    try:
        d = r.json()
    except ValueError:
        d = r.text[:300]
    return r.status_code, d


def short(d, n=260):
    return json.dumps(d, default=str)[:n]


def show(label, who, method, path, body=None, **kw):
    s, d = call(who, method, path, body, **kw)
    print(f"{label}: {method} {path} {short(body, 160) if body is not None else ''} [actor {who}] → {s} {short(d)}")
    return s, d


def upload(name, csv, who="Ana"):
    s, u = call(who, "POST", "/uploads", files=[("file", (f"{name}.csv", csv.encode()))], data={"name": name, "target": "target"})
    up = u["upload_id"]
    call(who, "POST", f"/uploads/{up}/check")
    s, c = call(who, "POST", f"/uploads/{up}/commit", {"reason": "X5 x3"})
    assert s in (200, 201), (s, c)
    return c["version_id"]


csv = "id,x,cat,target\n" + "".join(f"{i},{i * 0.37:.2f},{'abc'[i % 3]},{i % 2}\n" for i in range(300))
V = upload("x5p", csv)
print("dataset", V)

print("\n## m17: POST …/edit with the actor header and no body `by`")
ops = [{"op": "set_cells", "cells": [{"row_id": 1, "column": "x", "value": 9.99}]}]
show("m17 header only", "Ana", "POST", f"/datasets/{V}/edit", {"ops": ops, "reason": "x5 m17"})
show("m17 body by", "Ana", "POST", f"/datasets/{V}/edit", {"ops": ops, "reason": "x5 m17", "by": "Ana"})
show("m17 preview header only", "Ana", "POST", f"/datasets/{V}/edit", {"ops": ops, "reason": "x5 m17", "preview": True})
show("compare: PUT key header only", "Ana", "PUT", f"/datasets/{V}/tables/x5p/key", {"columns": ["id"], "reason": "x5"})

print("\n## m11: 16 concurrent edits (add_rows) on one version, at a barrier")
s, d = call("Ana", "GET", f"/datasets/{V}")
V0 = d.get("default_version_id") or V
bar = threading.Barrier(16)


def one(i):
    bar.wait()
    return call("Ana", "POST", f"/datasets/{V}/edit",
                {"ops": [{"op": "add_rows", "rows": [{"id": 1000 + i, "x": i, "cat": "a", "target": i % 2}]}], "reason": f"x5 m11 {i}", "by": "Ana"})


for rnd in range(3):
    bar = threading.Barrier(16)
    with cf.ThreadPoolExecutor(16) as ex:
        res = list(ex.map(one, range(16 * rnd, 16 * rnd + 16)))
    codes = {}
    for s, d in res:
        k = (s, d.get("detail", {}).get("code") if isinstance(d.get("detail"), dict) else str(d.get("detail"))[:90] if s >= 300 else "")
        codes[k] = codes.get(k, 0) + 1
    print(f"round {rnd}:", codes)

print("\n## open note: non-text `by` on shares / certifications / contracts")
s, t = call("Ada", "POST", "/teams", {"name": "X5 Grantee", "by": "Ada"})
gt = t.get("id")
print("grantee team", s, gt)
call("Ada", "POST", f"/teams/{gt}/members", {"name": "Gus", "role": "owner", "by": "Ada"})
s, fz = call("Ana", "POST", f"/datasets/{V}/contract/from-version/{V}", {"by": "Ana"})
print("contract frozen", s, short(fz, 120))
s, c0 = call("Ana", "POST", f"/datasets/{V}/certifications", {"level": "certified", "reason": "x5 by probe", "by": "Ana"})
cid = c0.get("id")
print("certification", s, cid, short(c0, 160))
s, s0 = call("Ana", "POST", "/shares", {"object_id": V, "grantee_team": gt, "scope": "all", "reason": "x5 by probe", "by": "Ana"})
sid = s0.get("id")
print("share", s, sid, short(s0, 160))
BAD = [123, 1.5, True, ["x"], {"a": 1}, None, "", "   ", "!!!", "x" * 200]
routes = [
    ("POST", "/shares", lambda b: {"object_id": V, "grantee_team": gt, "scope": "all", "reason": "x5", **b}),
    ("POST", f"/shares/{sid}/decide", lambda b: {"approve": False, "comment": "x5", **b}),
    ("POST", f"/shares/{sid}/revoke", lambda b: {"reason": "x5", **b}),
    ("POST", f"/datasets/{V}/certifications", lambda b: {"level": "approved_for_model", "reason": "x5", **b}),
    ("POST", f"/certifications/{cid}/decide", lambda b: {"approve": False, "comment": "x5", **b}),
    ("PUT", f"/datasets/{V}/contract", lambda b: {"spec": (fz.get("spec") if isinstance(fz, dict) else None) or {}, "reason": "x5", **b}),
    ("POST", f"/datasets/{V}/contract/from-version/{V}", lambda b: {"reason": "x5", **b}),
]
n5 = 0
for method, path, mk in routes:
    for by in BAD:
        for who in ("Ben", None):  # with and without the actor header (None → by alone)
            s, d = call(who, method, path, mk({"by": by}))
            n5 += s >= 500
            print(f"  {method} {path} by={json.dumps(by)[:24]:<24} actor={who!s:<4} → {s} {short(d.get('detail', d) if isinstance(d, dict) else d, 150)}")
print("by-probe 5xx:", n5)

print("\n## m10: POST /runs on a table whose part was rewritten with fewer rows")
csv2 = "id,x,cat,target\n" + "".join(f"{i},{i * 0.11:.2f},{'abc'[i % 3]},{i % 2}\n" for i in range(400))
V2 = upload("x5m10", csv2)
if PG:
    import psycopg
    con = psycopg.connect(PG); ph = "%s"
else:
    con = sqlite3.connect(f"{DATA}/ensemble.sqlite3"); ph = "?"
cur = con.cursor()
cur.execute(f"SELECT f.blob_sha256, b.key FROM table_files f JOIN blobs b ON b.sha256 = f.blob_sha256 WHERE f.version_id = {ph}", (V2,))
sha, key = cur.fetchone()
path = f"{DATA}/lake/{key}"
t = pq.read_table(path)
os.chmod(path, 0o644)
pq.write_table(t.slice(0, t.num_rows - 10), path)
print("part", sha[:16], "rows", t.num_rows, "→", t.num_rows - 10)
show("m10 run", "Ana", "POST", "/runs", {"dataset_id": V2, "label": "target", "time_limit": 10})
show("m10 run (problem_type binary)", "Ana", "POST", "/runs", {"dataset_id": V2, "label": "target", "time_limit": 10, "problem_type": "binary"})
show("m10 verify", "Ana", "GET", f"/datasets/{V2}/verify")
# the opposite direction: more rows than the split
pq.write_table(pq.concat_tables([t, t.slice(0, 10)]) if hasattr(pq, "concat_tables") else __import__("pyarrow").concat_tables([t, t.slice(0, 10)]), path)
print("part rewritten with", t.num_rows + 10, "rows")
show("m10 run (more rows)", "Ana", "POST", "/runs", {"dataset_id": V2, "label": "target", "time_limit": 10})
pq.write_table(t, path)
print("part restored with", t.num_rows, "rows (same content, bytes may differ)")
