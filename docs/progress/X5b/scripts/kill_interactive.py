"""X5b K2: kill the interactive heavy-pool child while a request waits on it (X5: the GET hung for ever, later durable
jobs failed with BrokenProcessPool). usage: kill_interactive.py <api pid> <run> <mv> <csv>"""
import os, sys, time, json, signal, threading, subprocess
sys.path.insert(0, "/var/tmp/x5b/scale"); sys.path.insert(0, "/var/tmp/x5b/k2")
from lib import c, tree, cmd, BASE
api, run, mv, csv = int(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4]
def cpu(p):
    try:
        s = open(f"/proc/{p}/stat").read().split(); return int(s[13]) + int(s[14])
    except OSError:
        return 0
kids = [p for p in tree(api) if "multiprocessing.spawn" in cmd(p)]
res = {}
def call():
    t = time.time(); st, r = c("GET", f"/api/runs/{run}/feature-importance?shuffles=50", timeout=600); res["r"] = (st, str(r)[:200], round(time.time() - t, 1))
th = threading.Thread(target=call); th.start()
b0 = {p: cpu(p) for p in kids}; time.sleep(3); b1 = {p: cpu(p) for p in kids}
busy = max(kids, key=lambda p: b1[p] - b0[p]); print("interactive pool children", kids, "busy", busy, "ticks", b1[busy] - b0[busy], flush=True)
os.kill(busy, signal.SIGKILL); print("SIGKILL", busy, flush=True)
th.join(120); print("waiting request →", res.get("r", "STILL WAITING after 120 s"), flush=True)
t = time.time(); st, r = c("GET", f"/api/runs/{run}/feature-importance?shuffles=3", timeout=300); print("next interactive call →", st, round(time.time() - t, 1), "s", flush=True)
st, g = c("POST", f"/api/runs/{run}/register", {"created_by": "x5", "model_name": f"x5b-ik-{int(time.time())}"})
t = time.time()
while time.time() - t < 600:
    st, m = c("GET", f"/api/registry/versions/{g['id']}")
    if m.get("status") not in ("building", "queued"): break
    time.sleep(2)
print("next build →", m.get("status"), str(m.get("error") or "")[:200], flush=True)
out = subprocess.run(["curl", "-s", "-H", "X-Ensemble-Actor: x5", "-F", f"file=@{csv}", "-F", "created_by=x5", f"{BASE}/api/registry/versions/{mv}/batches"], capture_output=True, text=True).stdout
b = json.loads(out)["id"]; t = time.time()
while time.time() - t < 600:
    st, x = c("GET", f"/api/registry/batches/{b}")
    if x.get("status") in ("succeeded", "failed"): break
    time.sleep(2)
print("next batch →", x.get("status"), x.get("n_rows"), flush=True)
