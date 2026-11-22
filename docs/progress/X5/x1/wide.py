"""C1 probe: POST /uploads a W-column x 2-row CSV, then /check; sample API RSS while it runs. usage: wide.py W [kill_gb]"""
import sys, time, threading, json, subprocess, urllib.request
W = int(sys.argv[1]); KILL = float(sys.argv[2]) if len(sys.argv) > 2 else 6.0
H = {"X-Ensemble-Actor": "fx1"}
body = (",".join(f"c{i}" for i in range(W)) + "\n" + ",".join("1" * W) + "\n" + ",".join("2" * W) + "\n").encode()
def req(m, url, data=None, ctype="text/csv"):
    r = urllib.request.Request("http://localhost:8000" + url, data=data, method=m, headers={**H, "Content-Type": ctype})
    try:
        with urllib.request.urlopen(r, timeout=900) as f: return f.status, json.loads(f.read())
    except urllib.error.HTTPError as e: return e.code, json.loads(e.read() or b"{}")
pid = open("/var/tmp/x5/x1/api_8000.pid").read().strip()
def rss():
    try: return int(open(f"/proc/{pid}/status").read().split("VmRSS:")[1].split()[0]) / 1e6
    except Exception: return -1
peak = [rss()]; stop = [False]
def watch():
    while not stop[0]:
        r = rss(); peak[0] = max(peak[0], r)
        if r > KILL: subprocess.run(["kill", "-9", pid]); print(f"WATCHDOG: RSS {r:.2f} GB > {KILL} GB, killed"); return
        time.sleep(0.1)
threading.Thread(target=watch, daemon=True).start()
st, up = req("POST", f"/api/uploads?filename=w{W}.csv&by=fx1", body)
print("upload", st, up.get("upload_id") or up.get("id") or up)
u = up.get("upload_id") or up.get("id")
t = time.time()
try: st, ck = req("POST", f"/api/uploads/{u}/check")
except Exception as e: st, ck = "conn", {"error": str(e)[:100]}
stop[0] = True
print(f"check W={W}: {st} in {time.time()-t:.1f}s, peak RSS {peak[0]:.2f} GB;", json.dumps(ck.get("detail", ck))[:300] if st != 200 else f"n_cols={ck['tables'][0]['n_cols']} blocks={ck['blocks']}")
