import json, threading
from c import *
v1 = json.load(open("m1_ids.json"))["v1"]
res = [None] * 3
def go(k):
    res[k] = c("POST", f"/api/datasets/{v1}/edit", {"by": "fx1", "reason": "F-X1 M1 concurrent add_rows", "ops": [{"op": "add_rows", "rows": [{"acct": f"E{k}{i:03d}", "x1": 0.5, "y": 1} for i in range(5)]}]}, quiet=True)
ts = [threading.Thread(target=go, args=(k,)) for k in range(3)]; [t.start() for t in ts]; [t.join() for t in ts]
for st, r in res:
    v = r.get("version_id") or r.get("id")
    print("edit", st, v, r.get("detail", ""))
    st, rows = c("GET", f"/api/datasets/{v}/rows?after=2999&limit=100", quiet=True)
    print("   new ids", [(x["_row_id"], x["acct"]) for x in rows["rows"]][:5])
