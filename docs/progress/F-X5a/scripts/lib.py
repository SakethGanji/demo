"""X5 scale helpers: HTTP calls and a process-tree peak-RSS sampler (API pid + all descendants).
`Peak(pid)` samples every 0.25 s: the largest single process RSS and the largest tree sum seen. VmHWM per process is
read at exit too (catches spikes between samples)."""
import json
import os
import threading
import time
import urllib.error
import urllib.request

BASE = os.environ.get("X5_BASE", "http://127.0.0.1:8601")
ACT = {"X-Ensemble-Actor": os.environ.get("X5_ACTOR", "x5")}


def c(method, path, body=None, timeout=3600, headers=None, raw=None):
    h = dict(ACT)
    h.update(headers or {})
    data = None
    if raw is not None:
        data = raw
    elif body is not None:
        data = json.dumps(body).encode()
        h["Content-Type"] = "application/json"
    r = urllib.request.Request(BASE + path, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            b = resp.read()
            st = resp.status
    except urllib.error.HTTPError as e:
        b, st = e.read(), e.code
    try:
        return st, json.loads(b)
    except Exception:
        return st, b[:2000]


def tree(pid):
    out, todo = [], [pid]
    while todo:
        p = todo.pop()
        out.append(p)
        try:
            for t in os.listdir(f"/proc/{p}/task"):
                try:
                    out_c = open(f"/proc/{p}/task/{t}/children").read().split()
                    todo += [int(x) for x in out_c]
                except OSError:
                    pass
        except OSError:
            pass
    return sorted(set(out))


def rss_kb(p, key="VmRSS"):
    try:
        for line in open(f"/proc/{p}/status"):
            if line.startswith(key + ":"):
                return int(line.split()[1])
    except OSError:
        return 0
    return 0


def cmd(p):
    try:
        return open(f"/proc/{p}/cmdline").read().replace("\0", " ")[:80]
    except OSError:
        return "?"


class Peak:
    def __init__(self, pid, label=""):
        self.pid, self.label = pid, label
        self.max_sum = self.max_one = 0
        self.who = ""
        self.hwm = {}

    def _loop(self):
        roots = [int(x) for x in os.environ.get("X5_PIDS", "").split(",") if x.strip()] or [self.pid]
        while not self._stop.is_set():
            ps = sorted(set(p for r in roots for p in tree(r)))
            vals = {p: rss_kb(p) for p in ps}
            s = sum(vals.values())
            self.max_sum = max(self.max_sum, s)
            for p, v in vals.items():
                if v > self.max_one:
                    self.max_one, self.who = v, cmd(p)
                self.hwm[p] = max(self.hwm.get(p, 0), rss_kb(p, "VmHWM"))
            time.sleep(0.25)

    def __enter__(self):
        self._stop = threading.Event()
        self.t0 = time.time()
        self.th = threading.Thread(target=self._loop, daemon=True)
        self.th.start()
        return self

    def __exit__(self, *a):
        self._stop.set()
        self.th.join()
        self.secs = time.time() - self.t0
        print(f"  [{self.label}] {self.secs:.1f} s | peak single process {self.max_one/1e6:.2f} GB ({self.who.strip()[:60]})"
              f" | peak tree sum {self.max_sum/1e6:.2f} GB", flush=True)

    def gb(self):
        return self.max_one / 1e6


def poll_job(job_id, every=2, limit=3600):
    t0, last = time.time(), None
    while time.time() - t0 < limit:
        st, j = c("GET", f"/api/jobs/{job_id}")
        p = (j.get("progress") or {}) if isinstance(j, dict) else {}
        cur = (p.get("phase"), round(p.get("pct") or 0, 2))
        if cur != last:
            print(f"    {time.time()-t0:6.1f}s {j.get('status')} {cur}", flush=True)
            last = cur
        if j.get("status") in ("succeeded", "failed", "cancelled"):
            return j
        time.sleep(every)
    return j
