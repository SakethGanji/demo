"""n:1 relation broken by a delta keyed on a wider key (customers cust_id becomes non-unique)."""
import sys; sys.path.insert(0, "."); from x3 import *
VR = json.load(open("Vc.json"))["VR"]
Vc = delta("customers delta keyed (cust_id, region) inserting C001/N (cust_id now repeats)", "cust_id,region,score\nC001,N,999\nC002,S,998\n", VR, spec={"key": ["cust_id", "region"]}, table="customers")
if Vc:
    s, d = version(Vc); print("  tables", [(t["name"], t["change"], t["n_rows"], [(k["columns"], k["valid"], k.get("n_duplicates")) for k in t["keys"]]) for t in d["tables"]])
    s, rl = get(f"/datasets/{Vc}/relations"); print("  relations", [(x["name"], x.get("cardinality"), x.get("n_orphans"), x.get("to_unique"), x.get("coverage")) for x in rl["relations"]])
    s, v = get(f"/datasets/{Vc}/verify"); print("  verify", s, v.get("ok") if isinstance(v, dict) else v)
    open("Vc.json", "w").write(json.dumps({"Vc": Vc, "VR": VR}))
# a plain delta on customers by its declared key cust_id that would be fine
Vo = delta("delta on customers by cust_id (declared key) on the broken version", "cust_id,region,score\nC003,N,1\nC004,N,2\n", Vc, table="customers") if Vc else None
