"""F-X5a (X5 J4): SIGTERM while the heavy pool is busy and a request waits on it.
usage: sigterm.py <port> <api pid> <run id>
Starts GET /runs/{run}/shap?row=… style heavy calls (the Model Doctor here, uncached), waits until a pool process is busy,
sends SIGTERM, and reports how long the API took to exit."""
import json, os, sys, threading, time, urllib.request
port, pid, run = sys.argv[1], int(sys.argv[2]), sys.argv[3]
base = f"http://127.0.0.1:{port}"
res = {}
def call(path):
    t = time.time()
    try:
        with urllib.request.urlopen(urllib.request.Request(base + path, headers={"X-Ensemble-Actor": "x5"}), timeout=900) as r:
            res[path] = (r.status, round(time.time() - t, 1))
    except Exception as e:  # noqa
        res[path] = (type(e).__name__, str(e)[:120], round(time.time() - t, 1))
paths = [f"/api/runs/{run}/feature-importance?shuffles={s}" for s in range(50, 42, -1)]
for p in paths:
    threading.Thread(target=call, args=(p,), daemon=True).start()
def busy():
    kids = open(f"/proc/{pid}/task/{pid}/children").read().split()
    tot = 0
    for k in kids:
        try:
            cpu = open(f"/proc/{k}/stat").read().split()
            tot += int(cpu[13]) + int(cpu[14])
        except OSError:
            pass
    return tot
b0 = busy(); time.sleep(4); b1 = busy()
print(f"pool children busy: cpu ticks {b1 - b0} in 4 s; requests open: {[p for p in paths if p not in res]}", flush=True)
t = time.time()
os.kill(pid, 15)
print("SIGTERM sent", flush=True)
while os.path.exists(f"/proc/{pid}") and time.time() - t < float(os.environ.get("WAIT_S", "300")):
    try:
        st = open(f"/proc/{pid}/stat").read().split()[2]
        if st == "Z":
            break
    except OSError:
        break
    time.sleep(0.2)
alive = os.path.exists(f"/proc/{pid}") and open(f"/proc/{pid}/stat").read().split()[2] != "Z"
print(f"API {'STILL ALIVE' if alive else 'exited'} after {time.time() - t:.1f} s", flush=True)
time.sleep(1)
print("requests:", json.dumps(res))
