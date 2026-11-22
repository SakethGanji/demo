"""Delta edge cases (lane M). Base table loans(acct TEXT with leading zeros, amt, seg), key acct."""
import sys; sys.path.insert(0, "."); from x3 import *
base = "acct,amt,seg,target\n" + "".join(f"{i:05d},{100+i},{'ab'[i%2]},{i%2}\n" for i in range(1, 41))
r = chain([("loans.csv", base.encode())], name="x3 delta", target="target")
print("v1", r["stage"], r.get("commit_status")); V1 = r["commit"]["version_id"]
s, d = version(V1); print("v1 schema", [(c["name"], c["type"]) for c in d["tables"][0]["schema"]] if s == 200 else (s, d))
s, k = put(f"/datasets/{V1}/tables/loans/key", {"columns": ["acct"], "reason": "x3"}); print("key", s, k.get("version_id") if isinstance(k, dict) else k); V2 = k["version_id"]
def delta(name, csv, parent, spec=None, show=True):
    plan = {"table": "loans", "tables": {"loans": {"action": "delta", **(spec or {})}}}
    r = chain([("loans.csv", csv.encode())], parent=parent, plan=plan)
    c = r.get("check", {})
    t = [x for x in c.get("tables", [])] if isinstance(c, dict) else []
    print(f"\n### {name}: stage={r['stage']} check={r.get('check_status')} commit={r.get('commit_status')}")
    if isinstance(c, dict) and c.get("blocks"): print("  blocks", c["blocks"])
    if isinstance(c, dict) and "detail" in c: print("  detail", c["detail"])
    for x in t:
        if x.get("delta"): print("  delta", {k: x["delta"].get(k) for k in ("mode","key","inserted","updated","updated_unchanged","deleted","missing","update_of_missing_key","insert_of_existing_key","rows_after")})
    if isinstance(c, dict): print("  warnings", [(w["code"], w.get("message","")[:100]) for w in c.get("warnings", [])])
    if r.get("commit_status") and r["commit_status"] >= 300: print("  commit body", r["commit"])
    return r.get("commit", {}).get("version_id") if r.get("commit_status") == 201 else None
# 1 update of a missing key via _op=U
V = delta("update of missing key (_op=U)", "acct,amt,seg,target,_op\n00099,1,a,0,U\n", V2)
if V: s, g = grid(V, "loans"); print("  rows", len(g), [x for x in g if x.get("acct") in ("00099", "99", 99)])
# 2 delete + insert same key in one file
delta("delete+insert same key", "acct,amt,seg,target,_op\n00001,0,a,0,D\n00001,5,b,1,I\n", V2)
# 3 two updates same key
delta("two updates same key", "acct,amt,seg,target,_op\n00002,7,a,0,U\n00002,8,a,0,U\n", V2)
# 4 leading zeros: delta acct written without zeros (sniffed as int)
V = delta("key dtype: '3' vs '00003' (no zeros)", "acct,amt,seg,target\n3,999,a,1\n", V2)
if V: s, g = grid(V, "loans"); print("  rows", len(g), [x for x in g if str(x.get("acct")).lstrip('0') == '3'])
# 5 leading zeros kept, but all-digit → maybe sniffed as int
V = delta("key with zeros '00003'", "acct,amt,seg,target\n00003,999,a,1\n", V2)
if V: s, g = grid(V, "loans"); print("  rows", len(g), [x for x in g if str(x.get("acct")).lstrip('0') == '3'])
# 6 a delta that changes nothing
V6 = delta("no-op delta (same values)", "acct,amt,seg,target\n00004,104,a,0\n", V2)
if V6: s, d = version(V6); print("  v", d.get("version_no"), [(t["name"], t["change"], t["table_sha256"][:12]) for t in d["tables"]])
s, d = version(V2); print("  parent", d.get("version_no"), [(t["name"], t["change"], t["table_sha256"][:12]) for t in d["tables"]])
# 7 the same no-op again (dedupe?)
V7 = delta("no-op delta again", "acct,amt,seg,target\n00004,104,a,0\n", V2)
print("  same version as first no-op?", V6 == V7)
# 8 key with NULLs in delta
delta("NULL key in delta", "acct,amt,seg,target\n,1,a,0\n00005,1,a,0\n", V2)
# 9 amt as text in delta
delta("type drift in a value column", "acct,amt,seg,target\n00006,abc,a,0\n", V2)
# 10 delete of a missing key only
V = delta("delete missing key only", "acct,amt,seg,target,_op\n00777,0,a,0,D\n", V2)
if V: s, d = version(V); print("  ", [(t["name"], t["change"]) for t in d["tables"]])
# 11 lower-case ops and whitespace
delta("ops lower-case/space", "acct,amt,seg,target,_op\n00007,1,a,0, u \n00008,1,a,0,d\n", V2)
# 12 delta without the key column
delta("delta without key column", "amt,seg,target\n1,a,0\n", V2)
# 13 delta with only key + _op=D (missing columns)
V = delta("delete-only file with key only", "acct,_op\n00009,D\n00010,D\n", V2)
# 14 update with missing columns (only acct,amt): seg/target keep parent's?
V = delta("partial-column update", "acct,amt\n00011,5555\n", V2)
if V: s, g = grid(V, "loans"); print("  row", [x for x in g if x.get("acct") == "00011"])
open("V.json", "w").write(json.dumps({"V1": V1, "V2": V2}))
