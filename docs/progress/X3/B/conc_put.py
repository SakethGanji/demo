"""N identical 3-file uploads committed at the same moment (new datasets each): every commit must succeed, and the
blobs must dedupe. Usage: conc_put.py <api> <files dir> <n>"""
import sys, threading, requests, collections
A, F, N = sys.argv[1], sys.argv[2], int(sys.argv[3]); H = {"X-Ensemble-Actor": "Ana"}
ups = []
for i in range(N):
    fs = [("file", (n, open(f"{F}/{n}", "rb"))) for n in ("applications.csv", "bureau.csv", "payments.csv")]
    u = requests.post(f"{A}/uploads", headers=H, files=fs, data={"name": f"conc {i}", "target": "defaulted"}).json()["upload_id"]
    requests.post(f"{A}/uploads/{u}/check", headers=H); ups.append(u)
res = {}; bar = threading.Barrier(N)
def go(u):
    bar.wait(); r = requests.post(f"{A}/uploads/{u}/commit", headers=H, json={"reason": "conc"}); res[u] = (r.status_code, r.json())
ts = [threading.Thread(target=go, args=(u,)) for u in ups]; [t.start() for t in ts]; [t.join() for t in ts]
print("codes", collections.Counter(c for c, _ in res.values()))
shas = collections.Counter()
for c, m in res.values():
    if c == 201:
        v = requests.get(f"{A}/datasets/{m['version_id']}/versions/{m['version_id']}", headers=H).json()
        shas[(v["data_sha256"], tuple(t["table_sha256"] for t in v["tables"]))] += 1
        ok = requests.get(f"{A}/datasets/{m['version_id']}/verify", headers=H).json()["ok"]
        shas[("verify", ok)] += 1
    else: print(c, str(m)[:300])
print(dict(shas))
