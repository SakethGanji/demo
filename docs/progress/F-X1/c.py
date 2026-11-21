"""Tiny API client for F-X1's manual calls: c(method, path, body=dict|bytes) -> (status, json). Prints a one-line log.
Peak-RSS sampler: with Peak() as p: ...; p.gb"""
import json, subprocess, threading, time, urllib.request, urllib.error
BASE = "http://localhost:8000"
ACT = {"X-Ensemble-Actor": "fx1"}
def c(m, path, body=None, quiet=False, ctype=None, timeout=3600, show=400):
    data = body if isinstance(body, (bytes, type(None))) else json.dumps(body).encode()
    h = {**ACT, "Content-Type": ctype or ("application/json" if not isinstance(body, bytes) else "text/csv")}
    r = urllib.request.Request(BASE + path, data=data, method=m, headers=h)
    t = time.time()
    try:
        with urllib.request.urlopen(r, timeout=timeout) as f: st, raw = f.status, f.read()
    except urllib.error.HTTPError as e: st, raw = e.code, e.read()
    try: js = json.loads(raw or b"null")
    except Exception: js = raw[:300]
    if not quiet: print(f"{m} {path} -> {st} ({time.time()-t:.1f}s) {json.dumps(js, default=str)[:show]}")
    return st, js
def pid():
    return subprocess.check_output(["pgrep", "-f", "uvicorn app.main:app"]).split()[0].decode()
def rss(p):
    try: return int(open(f"/proc/{p}/status").read().split("VmRSS:")[1].split()[0]) / 1e6
    except Exception: return -1
class Peak:
    def __init__(self, kill_gb=12.0): self.kill = kill_gb
    def __enter__(self):
        self.p = pid(); self.gb = rss(self.p); self.stop = False
        def w():
            while not self.stop:
                r = rss(self.p); self.gb = max(self.gb, r)
                if r > self.kill: subprocess.run(["kill", "-9", self.p]); print(f"WATCHDOG killed API at {r:.2f} GB"); return
                time.sleep(0.05)
        self.t = threading.Thread(target=w, daemon=True); self.t.start(); return self
    def __exit__(self, *a): self.stop = True; print(f"  peak API RSS {self.gb:.2f} GB")
def upload(csv_bytes, name, **q):
    qs = "&".join(f"{k}={v}" for k, v in {"filename": name, "by": "fx1", **q}.items())
    st, u = c("POST", f"/api/uploads?{qs}", csv_bytes)
    return u.get("upload_id") or u.get("id")
