"""F-X3a: M1 (acknowledge + later deltas), M2 (lossy casts: CSV, Parquet DECIMAL, plain declared), M3 (formats),
M4 (one-row files), and the minors, as HTTP calls in the UI's order. A = the API base (env)."""
import io, sys
sys.path.insert(0, ".")
from x3 import *  # noqa: F401,F403
import pyarrow as pa, pyarrow.parquet as pq

def code(x):
    return x.get("detail", {}).get("code") if isinstance(x, dict) and isinstance(x.get("detail"), dict) else x

def staged(files, parent, plan):
    up, err = upload(files, parent, plan)
    assert not err, err
    s, c = check(up)
    return up, s, c

print("## M1: a delta keyed on a wider key")
apps = "app_id,cust_id,amount,target\n" + "".join(f"A{i:04d},C{i%20:03d},{1000+i},{i%2}\n" for i in range(200))
cust = "cust_id,region,score\n" + "".join(f"C{i:03d},{'NS'[i%2]},{500+i}\n" for i in range(20))
r = chain([("apps.csv", apps.encode()), ("customers.csv", cust.encode())], name="fx3a m1", target="target")
V = r["commit"]["version_id"]
s, k = put(f"/datasets/{V}/tables/customers/key", {"columns": ["cust_id"]}); V = k["version_id"]
s, rr = put(f"/datasets/{V}/relations/app_cust", {"from": {"table": "apps", "columns": ["cust_id"]}, "to": {"table": "customers", "columns": ["cust_id"]}, "cardinality": "n:1"})
VR = rr["version_id"]; print("parent", "key cust_id + relation app_cust", s)
plan = {"table": "customers", "tables": {"customers": {"action": "delta", "key": ["cust_id", "region"]}}}
up, s, c = staged([("customers.csv", b"cust_id,region,score\nC001,N,999\nC002,S,998\n")], VR, plan)
print("check", s, "blocks", c.get("blocks"))
for w in c["warnings"]:
    print("  warning", w["code"], w.get("table"), w.get("columns") or w.get("relation"), "needs_ack", w.get("needs_ack"), "|", w.get("why"))
s, m = commit(up); print("commit, no acknowledge →", s, code(m), m["detail"].get("acknowledge"))
s, m = commit(up, acknowledge=["key_invalidated"]); print("commit, key_invalidated only →", s, code(m), m["detail"].get("acknowledge"))
s, m = commit(up, acknowledge=["key_invalidated", "relation_dropped"]); print("commit, both →", s, m.get("version_id"))
VB = m["version_id"]
s, d = version(VB)
print("  keys", [(t["name"], [(x["columns"], x["kind"], x["valid"]) for x in t["keys"]]) for t in d["tables"] if t["name"] == "customers"])
s, rl = get(f"/datasets/{VB}/relations"); print("  relations", rl["relations"])
s, mf = get(f"/datasets/{VB}/manifest")
ops = (mf.get("manifest") or mf).get("provenance", {}).get("ops", []) if isinstance(mf, dict) else []
print("  manifest ops", [o for o in ops if o.get("op") == "relation_dropped"])
print("  commit warnings acknowledged", [(w["code"], w.get("acknowledged")) for w in m.get("warnings", []) if w["code"] in ("key_invalidated", "relation_dropped")])
up, s, c = staged([("customers.csv", b"cust_id,region,score\nC003,N,1\n")], VB, {"table": "customers", "tables": {"customers": {"action": "delta"}}})
print("next delta, no key →", s, code(c), c.get("detail", {}).get("invalid_key"))
up, s, c = staged([("customers.csv", b"cust_id,region,score\nC003,N,1\n")], VB, {"table": "customers", "tables": {"customers": {"action": "delta", "key": ["cust_id", "region"]}}})
print("next delta, key named →", s, [t.get("delta", {}).get("key") for t in c.get("tables", [])], c.get("blocks"))
# a wider-key delta that breaks nothing: no warning; the plan key is kept as a unique key, the primary stays the default
up, s, c = staged([("customers.csv", b"cust_id,region,score\nC001,S,777\n")], VR, plan)
print("harmless wider-key delta: check", s, [w["code"] for w in c["warnings"]])
s, m = commit(up); VH = m["version_id"]; print("  commit", s)
s, d = version(VH)
print("  keys", [(t["name"], [(x["columns"], x["kind"], x["valid"]) for x in t["keys"]]) for t in d["tables"] if t["name"] == "customers"])
up, s, c = staged([("customers.csv", b"cust_id,region,score\nC002,N,5\n")], VH, {"table": "customers", "tables": {"customers": {"action": "delta"}}})
print("  next delta, no key → key", [t.get("delta", {}).get("key") for t in c.get("tables", [])], "from", [t.get("delta", {}).get("key_from") for t in c.get("tables", [])])
# the one-file path (P06 rewrite) breaking the relation
cust_dup = cust + "C001,N,1\n"
up, s, c = staged([("customers.csv", cust_dup.encode())], VR, {"table": "customers"})
print("one-file rewrite with a repeated cust_id: check", s, [(w["code"], w.get("needs_ack")) for w in c.get("warnings", []) if w["code"] in ("key_invalidated", "relation_dropped")])
s, m = commit(up, match={"method": "row_hash"}); print("  commit no ack →", s, code(m), (m.get("detail") or {}).get("acknowledge"))
s, m = commit(up, match={"method": "row_hash"}, acknowledge=["key_invalidated", "relation_dropped"]); print("  commit with ack →", s)

print("\n## M2: lossy casts")
loans = "acct,amt,target\n" + "".join(f"{i:05d},{100+i},{i%2}\n" for i in range(1, 41))
r = chain([("loans.csv", loans.encode())], name="fx3a m2", target="target"); V = r["commit"]["version_id"]
s, k = put(f"/datasets/{V}/tables/loans/key", {"columns": ["acct"]}); VL = k["version_id"]
dplan = {"table": "loans", "tables": {"loans": {"action": "delta"}}}
up, s, c = staged([("loans.csv", b"acct,amt,target\n00004,104.4,0\n00005,105.4,1\n")], VL, dplan)
print("delta 104.4 into BIGINT → blocks", [(b["code"], b.get("column"), b.get("n"), b.get("examples")) for b in c.get("blocks", [])])
up, s, c = staged([("loans.csv", b"acct,amt,target\n00004,104.0,0\n")], VL, dplan)
print("delta 104.0 (lossless) →", s, c.get("blocks"), [t.get("delta", {}).get("mode") for t in c["tables"]])
up, s, c = staged([("t.csv", b"acct,amt\n1,0.5\n2,1.5\n3,2.5\n")], None, {"types": {"amt": "BIGINT"}})
print("plain upload, declared BIGINT, 0.5 … → blocks", [(b["code"], b.get("n"), b.get("examples")) for b in c.get("blocks", [])])
up, s, c = staged([("t.csv", b"k,ts\n1,2024-01-02 10:30\n2,2024-01-03 00:00\n")], None, {"types": {"ts": "DATE"}})
print("plain upload, declared DATE, a time of day → blocks", [(b["code"], b.get("n"), b.get("examples")) for b in c.get("blocks", [])])
def pqbytes(t):
    b = io.BytesIO(); pq.write_table(t, b); return b.getvalue()
import decimal
base = pa.table({"k": pa.array(list(range(10)), pa.int64()), "price": pa.array([decimal.Decimal(f"{i}.25") for i in range(10)], pa.decimal128(10, 2)), "target": pa.array([i % 2 for i in range(10)], pa.int64())})
r = chain([("prices.parquet", pqbytes(base))], name="fx3a decimal", target="target"); VP = r["commit"]["version_id"]
s, k = put(f"/datasets/{VP}/tables/prices/key", {"columns": ["k"]}); VP = k["version_id"]
s, d = version(VP); print("parquet parent types", [(c["name"], c["type"]) for t in d["tables"] for c in t["schema"]][:3])
dl = pa.table({"k": pa.array([1, 2], pa.int64()), "price": pa.array([1.234, 2.5], pa.float64()), "target": pa.array([1, 0], pa.int64())})
up, s, c = staged([("prices.parquet", pqbytes(dl))], VP, {"table": "prices", "tables": {"prices": {"action": "delta"}}})
print("parquet delta 1.234 into DECIMAL(10,2) →", s, [(b["code"], b.get("columns")) for b in c.get("blocks", [])])

print("\n## M3: ambiguous dates with a format")
t = "k,d,target\n" + "".join(f"{i},2024-01-{i:02d},{i%2}\n" for i in range(1, 21))
r = chain([("t.csv", t.encode())], name="fx3a m3", target="target"); V = r["commit"]["version_id"]
s, k = put(f"/datasets/{V}/tables/t/key", {"columns": ["k"]}); VD = k["version_id"]
for fmt in (None, "%m/%d/%Y", "%d/%m/%Y"):
    sp = {"action": "delta", **({"formats": {"d": fmt}} if fmt else {})}
    up, s, c = staged([("t.csv", b"k,d,target\n1,02/03/2024,1\n2,02/04/2024,0\n")], VD, {"table": "t", "tables": {"t": sp}})
    if c.get("blocks"):
        print(f"formats {fmt} →", s, [b["code"] for b in c["blocks"]], [w["code"] for w in c["warnings"]])
        continue
    s, m = commit(up); s2, g = grid(m["version_id"], "t")
    print(f"formats {fmt} → commit", s, [(x["k"], x["d"]) for x in g if x["k"] in (1, 2)])

print("\n## M4: one-row files")
up, s, c = staged([("loans.csv", b"acct,amt,target\n00007,777,1\n")], VL, dplan)
print("one-row delta (update) →", s, [t.get("delta", {}).get("updated") for t in c.get("tables", [])]); s, m = commit(up); print("  commit", s)
up, s, c = staged([("loans.csv", b"acct,_op\n00008,D\n")], VL, dplan)
print("one-row delta (delete, key only) →", s, [t.get("delta", {}).get("deleted") for t in c.get("tables", [])])
up, s, c = staged([("loans.csv", b"acct,amt,target\n00099,1,1\n")], VL, {"table": "loans", "tables": {"loans": {"action": "append"}}})
print("one-row append →", s, c.get("blocks"))
up, s, c = staged([("loans.csv", b"acct,amt,target\n")], VL, dplan)
print("zero-row delta →", s, code(c))
up, s, c = staged([("new.csv", b"a,b\n1,2\n")], None, {})
print("one-row NEW dataset →", s, code(c))

print("\n## minors")
up, s, c = staged([("h.csv", b"a,A,b\n1,2,3\n4,5,6\n")], None, {})
print("case-only duplicate headers →", s, code(c))
up, s, c = staged([("branches.csv", b"branch,name\n01,a\n02,b\n")], VB, {"table": "branches"})
print("plan {table: X}, X not in the parent →", s, code(c))
for b in ("nope", "ds_00000000", "x" * 300):
    s, d = get(f"/datasets/{VB}/diff/{b}", by="Ana"); print(f"diff {VB} → {b[:20]} →", s, code(d))
s, d = put(f"/datasets/{VB}/tables/customers/key", {"columns": ["cust_id", "region"], "by": 7}); print("key, by: 7 →", s, code(d))
up, err = upload([("t.csv", b"a,b\n1,2\n3,4\n")], None, {"tabels": {}}); print("PATCH {tabels} →", err[0] if err else None, code(err[1]) if err else None)
