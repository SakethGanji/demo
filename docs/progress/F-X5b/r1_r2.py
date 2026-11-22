"""F-X5b: X5 R1 (concurrent POST /datasets/{id}/decision-time read a half-written decisions.json) and R2 (concurrent
DELETE /uploads/{u} → FileExistsError). A = API base, S = X5 fixture state.json."""
import collections, json, os, concurrent.futures as cf
import httpx
A = os.environ["A"]; st = json.load(open(os.environ["S"])); H = {"X-Ensemble-Actor": "Ada"}
V = st["v1"]
def dt(i):
    return httpx.post(f"{A}/datasets/{V}/decision-time", json={"by": "Ada", "reason": f"r1 {i}", "known": {}}, headers=H, timeout=120).status_code
with cf.ThreadPoolExecutor(16) as ex:
    print("R1 64 × POST decision-time, 16 at a time →", dict(collections.Counter(ex.map(dt, range(64)))))
ups = []
for i in range(4):
    r = httpx.post(f"{A}/uploads", params={"project_id": st["pd"]}, files={"file": ("r2.csv", b"a,b,y\n1,2,0\n3,4,1\n5,6,0\n", "text/csv")}, headers=H, timeout=120)
    ups.append(r.json()["upload_id"])
def disc(u):
    return httpx.delete(f"{A}/uploads/{u}", headers=H, timeout=120).status_code
with cf.ThreadPoolExecutor(16) as ex:
    print("R2 4 uploads × 8 concurrent DELETE →", dict(collections.Counter(ex.map(disc, [u for u in ups for _ in range(8)]))))
