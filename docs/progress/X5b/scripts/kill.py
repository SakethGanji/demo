"""X5b K2: kill a durable heavy job's pool process mid-build while a batch runs beside it; then a new build and a new
batch must work with no restart. usage: kill.py <api pid> <run id> <model version for batches> <batch csv> [roots...]
X5_BASE set. Pool processes are found as new 'multiprocessing.spawn' descendants of the given roots."""
import os, sys, time, json, signal, subprocess
sys.path.insert(0, "/var/tmp/x5b/scale")
from lib import c, tree, cmd, BASE
api, run, mv, csv = int(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4]
roots = [int(x) for x in sys.argv[5:]] or [api]
def spawns():
    return {p for r in roots for p in tree(r) if "multiprocessing.spawn" in cmd(p)}
def batch():
    out = subprocess.run(["curl", "-s", "-H", "X-Ensemble-Actor: x5", "-F", f"file=@{csv}", "-F", "created_by=x5", f"{BASE}/api/registry/versions/{mv}/batches"], capture_output=True, text=True).stdout
    return json.loads(out)["id"]
def wait_batch(b, limit=1800):
    t = time.time()
    while time.time() - t < limit:
        st, x = c("GET", f"/api/registry/batches/{b}")
        if x.get("status") in ("succeeded", "failed", "cancelled"):
            return x
        time.sleep(2)
    return x
def wait_mv(v, limit=1800):
    t = time.time()
    while time.time() - t < limit:
        st, m = c("GET", f"/api/registry/versions/{v}")
        if m.get("status") not in ("building", "queued"):
            return m
        time.sleep(2)
    return m
base = spawns()
st, g = c("POST", f"/api/runs/{run}/register", {"created_by": "x5", "model_name": f"x5b-kill-{int(time.time())}"})
v1 = g["id"]; print("1. build", v1, st, g.get("status"), flush=True)
t = time.time(); bproc = None
while time.time() - t < 600:
    st, m = c("GET", f"/api/registry/versions/{v1}")
    steps = [(s["name"], s["status"]) for s in (m.get("progress") or {}).get("steps", [])]
    new = spawns() - base
    if new and bproc is None:
        bproc = sorted(new)[0]
    if any(s == ("measure", "running") for s in steps) or (len(steps) >= 3 and time.time() - t > 20):
        break
    time.sleep(0.5)
print("   build process", bproc, cmd(bproc)[:60] if bproc else None, "steps", steps, flush=True)
base2 = spawns()
b1 = batch(); print("2. batch beside it", b1, flush=True)
t = time.time()
while time.time() - t < 60 and not (spawns() - base2):
    time.sleep(0.3)
print("   batch process", sorted(spawns() - base2), flush=True)
time.sleep(2)
os.kill(bproc, signal.SIGKILL); print("3. SIGKILL build process", bproc, flush=True)
m = wait_mv(v1); print("   build", v1, "→", m.get("status"), "|", str(m.get("error"))[:300], flush=True)
print("   steps", [(s["name"], s["status"], (s.get("summary") or "")[:120]) for s in (m.get("progress") or {}).get("steps", []) if s["status"] != "done"], flush=True)
x = wait_batch(b1); print("4. batch beside it", b1, "→", x.get("status"), "rows", x.get("n_rows"), str(x.get("error") or "")[:200], flush=True)
st, g = c("POST", f"/api/runs/{run}/register", {"created_by": "x5", "model_name": f"x5b-after-{int(time.time())}"})
v2 = g["id"]; m = wait_mv(v2); print("5. next build (no restart)", v2, "→", m.get("status"), str(m.get("error") or "")[:200], flush=True)
b2 = batch(); x = wait_batch(b2); print("6. next batch (no restart)", b2, "→", x.get("status"), "rows", x.get("n_rows"), str(x.get("error") or "")[:200], flush=True)
st, h = c("GET", "/api/health"); print("7. health", st, "API pid alive", os.path.exists(f"/proc/{api}"), flush=True)
