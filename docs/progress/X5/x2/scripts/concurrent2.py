"""X5/x2: writers X2/F-X2 did not try during a version's migration window (hook X2_SLEEP_AFTER_RUNS=<F1>:25):
Feature Lab and a Data Doctor apply on fl v1 (CSV-era) while it sits between its runs listing and its commit.
Afterwards: the FL job record (where it is, blob?), GET …/feature-lab, the Doctor child, lake_migrations, unpreserved files.
usage: concurrent2.py <state> <port> <data dir>"""
import glob, hashlib, json, sqlite3, sys, time
import httpx
st = json.load(open(sys.argv[1])); port = sys.argv[2]; D = sys.argv[3]
c = httpx.Client(base_url=f"http://localhost:{port}/api", timeout=600, headers={"X-Ensemble-Actor": "Ana"})
adm = {"X-Ensemble-Actor": "Ada"}; F1 = st["F1"]; t0 = time.time()
say = lambda *a: print(f"[{time.time() - t0:5.1f}s]", *a, flush=True)
say("migrate job", c.post("/admin/lake/migrate?dry_run=0", json={"by": "Ada"}, headers=adm).status_code)
while (c.get("/admin/lake/migrate", headers=adm).json().get("progress") or {}).get("current") != F1:
    time.sleep(0.3)
time.sleep(3)
r = c.post(f"/datasets/{F1}/feature-lab", json={"candidates": [{"op": "ratio", "a": "a", "b": "b"}], "time_limit": 20, "max_candidates": 2, "by": "Ana"})
say("POST feature-lab on fl v1 (in its window)", r.status_code, r.text[:220]); fl = r.json() if r.status_code < 300 else {}
r = c.post(f"/datasets/{F1}/data-doctor/apply", json={"transforms": [{"op": "drop_rows", "rows": [0, 1], "reason": "x5"}], "by": "Ana"})
say("POST data-doctor/apply on fl v1 (in its window)", r.status_code, r.text[:220]); doc = (r.json().get("dataset") or {}).get("id") if r.status_code < 300 else None
while (c.get("/admin/lake/migrate", headers=adm).json().get("progress") or {}).get("status") != "finished":
    time.sleep(1)
say("job finished", {k: c.get("/admin/lake/migrate", headers=adm).json()["progress"][k] for k in ("migrated", "skipped", "failed")})
jid = fl.get("id") or fl.get("job_id")
if jid:
    for _ in range(100):
        s = c.get(f"/datasets/{F1}/feature-lab/{jid}")
        if s.status_code != 200 or s.json().get("status") in ("done", "succeeded", "failed", "error", "cancelled"): break
        time.sleep(3)
    say("FL job", jid, s.status_code, json.dumps(s.json())[:300])
say("GET feature-lab list", json.dumps(c.get(f"/datasets/{F1}/feature-lab").json())[:300])
db = sqlite3.connect(f"{D}/ensemble.sqlite3")
blobs = {r[0] for r in db.execute("SELECT sha256 FROM blobs")}
for f in sorted(glob.glob(f"{D}/datasets/{F1}*/featurelab/*.json")):
    sha = hashlib.sha256(open(f, "rb").read()).hexdigest()
    say("  ", f.split("/datasets/")[1], "blob" if sha in blobs else "NO BLOB")
say("lake_migrations F1", db.execute("SELECT status, reason, attempts, message FROM lake_migrations WHERE version_id=?", (F1,)).fetchall())
if doc:
    d = c.get("/datasets/diff", params={"a": F1, "b": doc}).json()
    say("Doctor child", doc, db.execute("SELECT storage FROM datasets WHERE id=?", (doc,)).fetchone(), "diff:", d.get("identical"), {k: (d.get("rows") or {}).get(k) for k in ("matched", "added", "removed", "changed")})
