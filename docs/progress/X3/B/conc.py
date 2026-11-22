"""Two API processes on one Postgres DB and one lake (plus external workers): concurrent writers on ONE root.
Upload the book (applications keyed) through API 1, then at one barrier: K deltas (each inserts its own 20 new keys and
updates 5 shared rows) alternating API 1 / API 2, K edits (add_rows 3 rows) on the same parent, 2 key PUTs on bureau,
2 relation PUTs, and the same upload committed twice (once through each API). Then check: version numbers unique,
every version's __row_ids unique, no __row_id given to two different new rows across siblings, each successful call
has exactly one version, the double commit made one version.
Usage: conc.py <api1> <api2> <files dir> <db url> <lake dir> <K>"""
import collections, json, sys, threading
import duckdb, psycopg, requests
A1, A2, F, DBURL, LAKE, K = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5], int(sys.argv[6])
H = {"X-Ensemble-Actor": "Ana"}
def up(A, files, params=None, data=None, plan=None, check=True):
    fs = [("file", (n, open(f"{F}/{n}", "rb"))) for n in files]
    u = requests.post(f"{A}/uploads", headers=H, files=fs, params=params or {}, data=data or {}).json()["upload_id"]
    if plan: requests.patch(f"{A}/uploads/{u}", headers=H, json=plan)
    if check: requests.post(f"{A}/uploads/{u}/check", headers=H)
    return u
u = up(A1, ["applications.csv", "bureau.csv"], data={"name": "conc book", "target": "defaulted"}, plan={"tables": {"applications": {"key": ["app_id"]}}})
V1 = requests.post(f"{A1}/uploads/{u}/commit", headers=H, json={"reason": "base"}).json()["version_id"]; ROOT = V1
print("base", V1)
import pandas as pd
apps = pd.read_csv(f"{F}/applications.csv")
calls = []
for i in range(K):  # delta files
    d = pd.concat([apps.iloc[100:105].assign(income=lambda x: x.income + i), apps.iloc[:20].assign(app_id=[f"K{i:02d}{j:04d}" for j in range(20)])])
    d.to_csv(f"{F}/conc_delta_{i}.csv", index=False)
    A = A1 if i % 2 == 0 else A2
    uid = up(A, [f"conc_delta_{i}.csv"], params={"parent_version_id": V1}, plan={"table": "applications", "tables": {"applications": {"action": "delta"}}})
    calls.append(("delta", A, lambda A=A, uid=uid: requests.post(f"{A}/uploads/{uid}/commit", headers=H, json={"reason": "conc delta"})))
for i in range(K):
    A = A2 if i % 2 == 0 else A1
    row = apps.iloc[0].to_dict(); rows = [{**row, "app_id": f"E{i:02d}{j}"} for j in range(3)]
    calls.append(("edit", A, lambda A=A, rows=rows: requests.post(f"{A}/datasets/{V1}/edit", headers=H, json={"table": "applications", "ops": [{"op": "add_rows", "rows": rows}], "reason": "conc edit", "by": "Ana"})))
for A in (A1, A2):
    calls.append(("key", A, lambda A=A: requests.put(f"{A}/datasets/{V1}/tables/bureau/key", headers=H, json={"columns": ["customer_id"], "reason": "conc key"})))
    calls.append(("relation", A, lambda A=A: requests.put(f"{A}/datasets/{V1}/relations/app_bureau", headers=H, json={"from": {"table": "applications", "columns": ["customer_id"]}, "to": {"table": "bureau", "columns": ["customer_id"]}, "reason": "conc rel"})))
ud = up(A1, ["conc_delta_0.csv"], params={"parent_version_id": V1}, plan={"table": "applications", "tables": {"applications": {"action": "delta"}}})
for A in (A1, A2):
    calls.append(("double_commit", A, lambda A=A: requests.post(f"{A}/uploads/{ud}/commit", headers=H, json={"reason": "double"})))
bar = threading.Barrier(len(calls)); res = []
def go(c):
    bar.wait(); r = c[2]()
    try: b = r.json()
    except Exception: b = r.text[:200]
    res.append((c[0], c[1][-10:], r.status_code, b))
ts = [threading.Thread(target=go, args=(c,)) for c in calls]; [t.start() for t in ts]; [t.join() for t in ts]
print("results", collections.Counter((k, s) for k, _, s, _ in res))
for k, a, s, b in res:
    if s >= 300: print("  ", k, a, s, str(b)[:300])
with psycopg.connect(DBURL) as c:
    vs = c.execute("select id, version_no, op, parent_id, created_at from datasets where root_id=%s order by version_no", (ROOT,)).fetchall()
print("versions", len(vs), "| duplicate version_no", [v for v, n in collections.Counter(x[1] for x in vs).items() if n > 1])
print("  ", [(x[0], x[1], x[2]) for x in vs])
ids_new = collections.defaultdict(set); base_ids = None; prob = []
for vid, vno, op, par, _ in vs:
    man = requests.get(f"{A1}/datasets/{vid}/manifest", headers=H).json()["manifest"]
    t = [t for t in man["data"]["tables"] if t["name"] == "applications"][0]
    paths = [f"{LAKE}/{f['key']}" for f in t["files"]]
    df = duckdb.sql(f"select __row_id, app_id from read_parquet({paths})").df()
    if df.__row_id.duplicated().any(): prob.append((vid, "dup __row_id in version"))
    m = dict(zip(df.__row_id, df.app_id))
    if base_ids is None: base_ids = m; continue
    for rid, key in m.items():
        if rid not in base_ids: ids_new[rid].add(key)
clash = {r: k for r, k in ids_new.items() if len(k) > 1}
print("problems", prob, "| __row_ids given to different new rows across siblings", len(clash), list(clash.items())[:5])
dc = [x for x in res if x[0] == "double_commit"]
print("double commit", [(a, s, (b.get("version_id") if isinstance(b, dict) else b) if s < 300 else str(b)[:150]) for _, a, s, b in dc])
