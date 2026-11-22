import sys; sys.path.insert(0, "."); from x3 import *
V2 = json.load(open("V.json"))["V2"]
base = "acct,amt,seg,target\n" + "".join(f"{i:05d},{100+i}.6,{'ab'[i%2]},{i%2}\n" for i in range(1, 41))
r = chain([("loans.csv", base.encode())], parent=V2, plan={"table": "loans"})
print(r.get("commit_status"), r.get("commit"))
# declared types on a fresh upload: amt declared BIGINT, values 1.5
csv = "acct,amt\n" + "".join(f"{i},{i}.5\n" for i in range(5))
up, err = upload([("t.csv", csv.encode())], name="x3 declared", plan={"types": {"amt": "BIGINT"}})
print("patch err", err); s, c = check(up); print("check", s, c.get("blocks") if isinstance(c, dict) else c, [(w["code"]) for w in c.get("warnings", [])] if isinstance(c, dict) else "")
print([[(s_["name"], s_["type"]) for s_ in t["schema"]] for t in c.get("tables", [])] if isinstance(c, dict) else "")
