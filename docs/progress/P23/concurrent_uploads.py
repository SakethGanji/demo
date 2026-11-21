"""Two uploads to one root, committed at the same moment: versions n and n+1 (no duplicate, no lost number).
Usage: python concurrent_uploads.py <base> <ids.json>."""
import json, random, sys, threading
import httpx

B = sys.argv[1] + "/api"
ids = json.load(open(sys.argv[2]))
SALT = random.randint(1, 10**6)  # new content each run (a repeat would dedupe to the existing version)
H = {"X-Ensemble-Actor": "Ana"}
c = httpx.Client(timeout=600)


def body(k):
    rows = "".join(f"A{i:04d},{30000 + i * 37 % 50000 + (k * 500 + SALT if i < 40 else 0)},{20 + i % 50},{(i * 7) % 3 == 0:d}\n" for i in range(1000))
    return ("acct,income,age,y\n" + rows).encode()


ups = []
for k in (1, 2):
    u = c.post(f"{B}/uploads", params={"filename": f"c{k}.csv", "root_id": ids["v1"], "by": "Ana"}, content=body(k), headers=H).json()
    c.post(f"{B}/uploads/{u['upload_id']}/check", headers=H).raise_for_status()
    ups.append(u["upload_id"])
print("staged + checked:", ups)
res, gate = {}, threading.Barrier(2)


def commit(up):
    gate.wait()
    r = httpx.post(f"{B}/uploads/{up}/commit", json={"match": {"method": "key", "columns": ["acct"]}, "reason": "concurrent"}, headers=H, timeout=600)
    res[up] = (r.status_code, r.json().get("version_id"), r.json().get("version_no"))


ts = [threading.Thread(target=commit, args=(u,)) for u in ups]
[t.start() for t in ts]
[t.join() for t in ts]
for u in ups:
    print(" ", u, "→ HTTP %s %s version_no %s" % res[u])
nos = sorted(v[2] for v in res.values())
print("RESULT:", "n and n+1" if nos[1] == nos[0] + 1 else f"BAD {nos}")
