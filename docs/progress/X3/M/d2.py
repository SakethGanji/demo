"""Delta edge cases with ≥2 rows (one-row deltas are refused, see d1)."""
import sys; sys.path.insert(0, "."); from x3 import *
V2 = json.load(open("V.json"))["V2"]
def delta(name, csv, parent, spec=None):
    plan = {"table": "loans", "tables": {"loans": {"action": "delta", **(spec or {})}}}
    r = chain([("loans.csv", csv.encode())], parent=parent, plan=plan)
    c = r.get("check", {})
    print(f"\n### {name}: stage={r['stage']} check={r.get('check_status')} commit={r.get('commit_status')}")
    if isinstance(c, dict) and c.get("blocks"): print("  blocks", c["blocks"])
    if isinstance(c, dict) and "detail" in c: print("  detail", c["detail"])
    for x in (c.get("tables", []) if isinstance(c, dict) else []):
        if x.get("delta"): print("  delta", {k: x["delta"].get(k) for k in ("mode","key","inserted","updated","updated_unchanged","deleted","missing","update_of_missing_key","insert_of_existing_key","rows_after")})
        print("  schema", [(s["name"], s["type"]) for s in x.get("schema", [])])
    if isinstance(c, dict): print("  warnings", [(w["code"], w.get("message","")[:110]) for w in c.get("warnings", [])])
    if r.get("commit_status") and r["commit_status"] >= 300: print("  commit body", r["commit"])
    return r.get("commit", {}).get("version_id") if r.get("commit_status") == 201 else None
def show(V, accts):
    s, g = grid(V, "loans"); print("  n rows", len(g), [{k: x[k] for k in ("_row_id","acct","amt","seg","target") if k in x} for x in g if str(x.get("acct")) in accts])
V = delta("update of missing key (_op=U)", "acct,amt,seg,target,_op\n00099,1,a,0,U\n00098,1,a,0,U\n", V2)
if V: show(V, {"00099", "00098"})
V = delta("key dtype: 3,4 vs '00003' (no zeros)", "acct,amt,seg,target\n3,999,a,1\n4,999,a,1\n", V2)
if V: show(V, {"3", "4", "00003", "00004"})
V = delta("key with zeros '00003','00004'", "acct,amt,seg,target\n00003,999,a,1\n00004,999,a,1\n", V2)
if V: show(V, {"3", "4", "00003", "00004"})
V6 = delta("no-op delta (same values)", "acct,amt,seg,target\n00004,104,b,0\n00005,105,a,1\n", V2)
if V6:
    s, d = version(V6); print("  v", d.get("version_no"), [(t["name"], t["change"], t["table_sha256"][:12]) for t in d["tables"]], d.get("data_sha256", "")[:12])
    s, p = version(V2); print("  parent v", p.get("version_no"), [(t["name"], t["change"], t["table_sha256"][:12]) for t in p["tables"]], p.get("data_sha256", "")[:12])
V7 = delta("no-op delta again", "acct,amt,seg,target\n00004,104,b,0\n00005,105,a,1\n", V2)
print("  same version as first no-op?", V6 == V7)
delta("type drift in a value column (amt text)", "acct,amt,seg,target\n00006,abc,a,0\n00007,1,a,0\n", V2)
V = delta("delete missing keys only", "acct,amt,seg,target,_op\n00777,0,a,0,D\n00778,0,a,0,D\n", V2)
if V: s, d = version(V); print("  ", d.get("version_no"), [(t["name"], t["change"]) for t in d["tables"]])
delta("delta without key column", "amt,seg,target\n1,a,0\n2,a,0\n", V2)
V = delta("partial-column update (acct,amt)", "acct,amt\n00011,5555\n00012,6666\n", V2)
if V: show(V, {"00011", "00012"})
V = delta("partial-column INSERT (acct,amt) new keys", "acct,amt\n00511,5555\n00512,6666\n", V2)
if V: show(V, {"00511", "00512"})
V = delta("target set to NULL by update", "acct,amt,seg,target\n00013,1,a,\n00014,1,a,\n", V2)
if V: show(V, {"00013", "00014"})
V = delta("insert of existing key (_op=I)", "acct,amt,seg,target,_op\n00015,1,a,0,I\n00016,1,a,0,I\n", V2)
if V: show(V, {"00015", "00016"})
V = delta("upsert column named _op with a value 'X'", "acct,amt,seg,target,_op\n00017,1,a,0,X\n00018,1,a,0,U\n", V2)
V = delta("float amt into BIGINT column", "acct,amt,seg,target\n00019,1.5,a,0\n00020,2.7,a,0\n", V2)
if V: show(V, {"00019", "00020"})
V = delta("case-different key '0001A' style + whitespace key", "acct,amt,seg,target\n 00021,1,a,0\n00022 ,1,a,0\n", V2)
if V: show(V, {"00021", "00022", " 00021", "00022 "})
