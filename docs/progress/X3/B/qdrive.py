"""Queue crash / lease driver (X3/B). Starts workerd processes itself, POSTs a 10M edit (→ 202 job), polls the job, and
at a chosen phase/progress either kill -9s the holding worker or SIGSTOPs it past the lease and SIGCONTs it later.
Then reports the root's versions, the versions with this job_id, the job row and the audit rows.
Usage: qdrive.py <api> <db url> <dataset id> <kill|stop> <pct> <find> <replace> [workers=2]"""
import json, os, signal, subprocess, sys, time
import psycopg, requests

A, DBURL, DS, MODE, PCT, FIND, REPL = sys.argv[1:8]; NW = int(sys.argv[8]) if len(sys.argv) > 8 else 2
PCT = float(PCT); H = {"X-Ensemble-Actor": "Ana"}; PY = sys.executable; BACK = "/home/user/demo/backend"
LOG = os.environ.get("QLOG", "/tmp/qdrive")
def worker(i):
    return subprocess.Popen([PY, "-m", "app.engine.workerd", "--slots", "train=1,duck=1,light=2"], cwd=BACK,
                            stdout=open(f"{LOG}.w{i}.log", "a"), stderr=subprocess.STDOUT)
def q(sql, *a):
    with psycopg.connect(DBURL) as c: return c.execute(sql, a).fetchall()
root = q("select root_id from datasets where id=%s", DS)[0][0]
before = q("select count(*) from datasets where root_id=%s", root)[0][0]
ws = [worker(0)]; time.sleep(6)
for i in range(1, NW): ws.append(worker(i))
time.sleep(4)
r = requests.post(f"{A}/datasets/{DS}/edit", headers=H, json={"ops": [{"op": "replace_values", "column": "channel", "find": FIND, "replace": REPL, "match": "exact"}], "reason": "x3 queue", "by": "Ana"})
print("POST edit", r.status_code, r.text[:200]); jid = r.json()["job_id"]; t0 = time.time(); acted = None; resumed_at = None
while True:
    j = requests.get(f"{A}/jobs/{jid}", headers=H).json(); pr = j.get("progress") or {}
    row = q("select leased_by, attempts, status from jobs where id=%s", jid)[0]
    st = (j["status"], row[1], pr.get("phase"), pr.get("pct"))
    if st != globals().get("_last"): print(f"  +{time.time()-t0:.1f}s", st, row[0]); globals()["_last"] = st
    if acted is None and (pr.get("pct") or 0) >= PCT and j["status"] not in ("queued", "succeeded", "failed"):
        pid = int(row[0].rsplit(":", 1)[1]); acted = (pid, row[1], pr)
        if MODE == "kill":
            os.kill(pid, signal.SIGKILL); print(f"+{time.time()-t0:.1f}s KILL -9 {row[0]} attempt {row[1]} at {pr}")
        else:
            os.kill(pid, signal.SIGSTOP); print(f"+{time.time()-t0:.1f}s SIGSTOP {row[0]} attempt {row[1]} at {pr}")
            if NW == 1: ws.append(worker(9))
        if MODE == "kill" and NW == 1: time.sleep(3); ws.append(worker(8))
    if MODE == "stop" and acted and resumed_at is None and row[1] > acted[1] and (j["status"] in ("succeeded", "failed") or (pr.get("pct") or 0) >= float(os.environ.get("CONT_AT", "0.3"))):
        os.kill(acted[0], signal.SIGCONT); resumed_at = time.time(); print(f"+{time.time()-t0:.1f}s SIGCONT {acted[0]} (attempt {row[1]} now holds, {j['status']} {pr})")
    if j["status"] in ("succeeded", "failed", "cancelled"):
        if MODE != "stop" or resumed_at: break
    time.sleep(0.25)
print(f"+{time.time()-t0:.1f}s job {j['status']} attempts {row[1]} result {json.dumps(j.get('result'))[:300]} error {str(j.get('error'))[:200]}")
if MODE == "stop": time.sleep(40)  # let the resumed old attempt finish whatever it does
after = q("select id, version_no, op, job_id, created_at from datasets where root_id=%s order by version_no", root)
print("versions of the root", before, "→", len(after))
print("versions with this job_id", [r_ for r_ in after if r_[3] == jid])
print("version_no duplicates", [v for v in {r_[1] for r_ in after} if sum(1 for x in after if x[1] == v) > 1])
print("job row", q("select status, attempts, leased_by, error, left(result::text, 200) from jobs where id=%s", jid))
print("audit dataset_version_created for new versions", q("select count(*) from audit_log where action='dataset_version_created' and details like %s", f"%{jid}%"))
for w in ws:
    try: os.kill(w.pid, signal.SIGCONT)
    except Exception: pass
    w.terminate()
for w in ws:
    try: w.wait(15)
    except Exception: w.kill()
