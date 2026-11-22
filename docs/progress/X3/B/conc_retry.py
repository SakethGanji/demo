"""Contended delta commits are retryable: 4 delta uploads on one parent committed at once (2 per API), then every
409 commit_contended upload is committed again. Usage: conc_retry.py <api1> <api2> <files dir> <parent version>"""
import sys, threading, requests, collections
A1, A2, F, V = sys.argv[1:5]; H = {"X-Ensemble-Actor": "Ana"}
ups = []
for i in range(4):
    A = (A1, A2)[i % 2]
    u = requests.post(f"{A}/uploads", headers=H, params={"parent_version_id": V}, files=[("file", (f"conc_delta_{i}.csv", open(f"{F}/conc_delta_{i}.csv", "rb")))]).json()["upload_id"]
    requests.patch(f"{A}/uploads/{u}", headers=H, json={"table": "applications", "tables": {"applications": {"action": "delta"}}})
    requests.post(f"{A}/uploads/{u}/check", headers=H); ups.append((A, u))
bar = threading.Barrier(4); res = {}
def go(A, u): bar.wait(); r = requests.post(f"{A}/uploads/{u}/commit", headers=H, json={"reason": "r"}); res[u] = (r.status_code, r.json())
ts = [threading.Thread(target=go, args=x) for x in ups]; [t.start() for t in ts]; [t.join() for t in ts]
print("first", collections.Counter(s for s, _ in res.values()))
for A, u in ups:
    if res[u][0] == 409:
        r = requests.post(f"{A}/uploads/{u}/commit", headers=H, json={"reason": "retry"}); print("retry", u, r.status_code, str(r.json())[:200])
