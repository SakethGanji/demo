"""Multi-table: relations under deltas and key changes; composite-key delta; drop + re-add; carry over many versions."""
import sys; sys.path.insert(0, "."); from x3 import *
apps = "app_id,cust_id,region,amount,target\n" + "".join(f"A{i:04d},C{i%20:03d},{'NS'[i%2]},{1000+i},{i%3==0:d}\n" for i in range(200))
cust = "cust_id,region,score\n" + "".join(f"C{i:03d},{'NS'[i%2]},{500+i}\n" for i in range(20))
pay = "app_id,seq,amount,paid_on\n" + "".join(f"A{i%200:04d},{i//200},{i},2024-0{1+i%9}-1{i%9}\n" for i in range(600))
r = chain([("apps.csv", apps.encode()), ("customers.csv", cust.encode()), ("payments.csv", pay.encode())], name="x3 multi", target="target")
print("v1", r["stage"], r.get("check_status"), r.get("commit_status"), r.get("check", {}).get("blocks"))
V1 = r["commit"]["version_id"]
s, k = put(f"/datasets/{V1}/tables/customers/key", {"columns": ["cust_id"]}); print("key customers", s); V = k["version_id"]
s, k = put(f"/datasets/{V}/tables/apps/key", {"columns": ["app_id"]}); print("key apps", s); V = k["version_id"]
s, k = put(f"/datasets/{V}/tables/payments/key", {"columns": ["app_id", "seq"]}); print("key payments composite", s, k.get("keys") if isinstance(k, dict) else k); V = k["version_id"]
s, rr = put(f"/datasets/{V}/relations/app_cust", {"from": {"table": "apps", "columns": ["cust_id"]}, "to": {"table": "customers", "columns": ["cust_id"]}, "cardinality": "n:1"})
print("relation", s, rr.get("relation") if isinstance(rr, dict) else rr); V = rr["version_id"]; VR = V
# 1 composite key delta on payments: update (A0001,0), delete (A0002,0), insert (A0001,9)
Vp = delta("composite-key delta on payments", "app_id,seq,amount,paid_on,_op\nA0001,0,77,2024-01-10,U\nA0002,0,0,2024-01-10,D\nA0001,9,5,2024-02-02,I\n", V, table="payments")
if Vp:
    s, d = version(Vp); print("  tables", [(t["name"], t["change"], t["n_rows"]) for t in d["tables"]])
    s, rl = get(f"/datasets/{Vp}/relations"); print("  relations carried", [(x["name"], x.get("n_from_missing"), x.get("to_unique")) for x in rl["relations"]])
# 2 delta on customers keyed by (cust_id, region): adds C001 with region S → cust_id no longer unique (n:1 becomes 1:n)
Vc = delta("customers delta keyed (cust_id, region) duplicating cust_id", "cust_id,region,score\nC001,S,999\nC002,N,998\n", VR, spec={"key": ["cust_id", "region"]}, table="customers")
if Vc:
    s, d = version(Vc); print("  tables", [(t["name"], t["change"], t["n_rows"], [k["columns"] for k in t["keys"]]) for t in d["tables"]])
    s, rl = get(f"/datasets/{Vc}/relations"); print("  relations", [(x["name"], x.get("cardinality"), x.get("n_from_missing"), x.get("to_unique"), x.get("coverage")) for x in rl["relations"]])
    open("Vc.json", "w").write(json.dumps({"Vc": Vc, "VR": VR}))
# 3 key change on customers to score; relation still points at cust_id
s, k = put(f"/datasets/{VR}/tables/customers/key", {"columns": ["score"]}); print("\n### key change customers→score", s, k.get("keys") if isinstance(k, dict) else k)
if s in (200, 201):
    s, rl = get(f"/datasets/{k['version_id']}/relations"); print("  relations", [(x["name"], x.get("to_unique"), x.get("n_from_missing")) for x in rl["relations"]])
    s, d = version(k["version_id"]); print("  keys", [(t["name"], [x["columns"] for x in t["keys"]]) for t in d["tables"]])
# 4 drop the customers table, then re-add it
r = chain([("apps.csv", apps.encode())], parent=VR, plan={"tables": {"apps": {"action": "rewrite", "match": {"method": "key", "columns": ["app_id"]}}, "customers": {"action": "drop"}}})
print("\n### drop customers", r["stage"], r.get("check_status"), r.get("commit_status"), (r.get("check") or {}).get("blocks"), str(r.get("commit"))[:300])
if r.get("commit_status") in (200, 201):
    Vd = r["commit"]["version_id"]; s, d = version(Vd); print("  tables", [(t["name"], t["change"]) for t in d["tables"]])
    s, rl = get(f"/datasets/{Vd}/relations"); print("  relations", rl.get("relations"))
    r2 = chain([("customers.csv", cust.encode())], parent=Vd, plan={"tables": {"customers": {"action": "new"}}})
    print("### re-add customers", r2["stage"], r2.get("check_status"), r2.get("commit_status"), (r2.get("check") or {}).get("blocks"), str(r2.get("commit"))[:300])
    if r2.get("commit_status") in (200, 201):
        Va = r2["commit"]["version_id"]; s, d = version(Va)
        print("  tables", [(t["name"], t["change"], t["table_sha256"][:12], [k["columns"] for k in t["keys"]]) for t in d["tables"]])
        s, d1 = version(VR); print("  vs VR", [(t["name"], t["table_sha256"][:12]) for t in d1["tables"]])
        s, g = grid(Va, "customers"); print("  re-added customers row ids", [x["_row_id"] for x in g][:5], "…")
        s, g = grid(VR, "customers"); print("  original customers row ids", [x["_row_id"] for x in g][:5], "…")
