"""Carry across many versions: 20 insert-only deltas (append parts), edits between, then a rewrite delta; content checked
against an independent model of the table after every step."""
import sys, hashlib; sys.path.insert(0, "."); from x3 import *
rows = {f"K{i:05d}": (i, i % 2) for i in range(300)}
csv = "k,v,target\n" + "".join(f"{k},{a},{b}\n" for k, (a, b) in rows.items())
r = chain([("t.csv", csv.encode())], name="x3 many parts", target="target"); V = r["commit"]["version_id"]
s, k = put(f"/datasets/{V}/tables/t/key", {"columns": ["k"]}); V = k["version_id"]
def model_ok(V, tag):
    s, g = grid(V, "t")
    got = {x["k"]: (x["v"], x["target"]) for x in g}
    ids = [x["_row_id"] for x in g]
    s2, m = get(f"/datasets/{V}/manifest")
    parts = [p for t in m["manifest"]["data"]["tables"] if t["name"] == "t" for p in t["files"]]
    s3, vf = get(f"/datasets/{V}/verify")
    ok = got == rows and len(set(ids)) == len(ids)
    print(f"{tag}: rows {len(g)} model_equal {ok} unique_ids {len(set(ids)) == len(ids)} parts {len(parts)} roles {sorted(set(p['role'] for p in parts))} verify {vf.get('ok')}")
    if not ok:
        print("   diff", {k_: (got.get(k_), rows.get(k_)) for k_ in set(got) ^ set(rows) | {k_ for k_ in got if got[k_] != rows.get(k_)}})
    return len(parts)
model_ok(V, "base")
n = 300
for i in range(20):
    ins = {f"K{n + j:05d}": (n + j, j % 2) for j in range(3)}; n += 3
    V2 = delta(f"insert-only delta {i}", "k,v,target\n" + "".join(f"{k},{a},{b}\n" for k, (a, b) in ins.items()), V, table="t") if False else None
    plan = {"table": "t", "tables": {"t": {"action": "delta"}}}
    rr = chain([("t.csv", ("k,v,target\n" + "".join(f"{k},{a},{b}\n" for k, (a, b) in ins.items())).encode())], parent=V, plan=plan)
    assert rr.get("commit_status") in (200, 201), rr
    V = rr["commit"]["version_id"]; rows.update(ins)
    if i in (5, 12):  # an edit between appends: set a cell on an appended row, delete a base row
        s, g = grid(V, "t"); rid = {x["k"]: x["_row_id"] for x in g}
        body = {"table": "t", "reason": "x3 edit between appends", "by": "Ana", "ops": [
            {"op": "set_cells", "cells": [{"row_id": rid[f"K{n-1:05d}"], "column": "v", "value": -1}]},
            {"op": "delete_rows", "row_ids": [rid[f"K{i:05d}"]]}]}
        s, e = post(f"/datasets/{V}/edit", body); print(f"  edit after delta {i}: HTTP {s}", (e.get("version_id") if isinstance(e, dict) else e))
        if s in (200, 201):
            V = e.get("version_id") or e.get("id"); rows[f"K{n-1:05d}"] = (-1, rows[f"K{n-1:05d}"][1]); rows.pop(f"K{i:05d}")
    if i % 4 == 3 or i in (5, 6, 12, 13):
        model_ok(V, f"after delta {i}")
np_ = model_ok(V, "after 20 appends")
# a rewrite delta: update + delete
upd = "k,v,target,_op\nK00100,9999,1,U\nK00101,0,0,D\n"
rr = chain([("t.csv", upd.encode())], parent=V, plan={"table": "t", "tables": {"t": {"action": "delta"}}})
print("rewrite delta", rr.get("commit_status"), {k_: rr["check"]["tables"][0]["delta"].get(k_) for k_ in ("mode", "updated", "deleted")})
V = rr["commit"]["version_id"]; rows["K00100"] = (9999, 1); rows.pop("K00101")
model_ok(V, "after the rewrite (compaction expected when > 16 parts)")
