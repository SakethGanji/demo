"""P11: AutoGluon peak-RSS calibration of the frame budget (default preset), through the real worker.
    python calibrate.py <data_dir> <csv> <time_limit> <out.jsonl>
1. POST /uploads → check → commit the CSV (the API on :8000 with ENSEMBLE_DATA_DIR=<data_dir>);
2. in this process: frames.read of the version (what an unsampled worker reads) → the R12 estimate vs the frame's real
   pandas size (memory_usage(deep=True)) and the load's RSS peak;
3. POST /runs {presets medium_quality, time_limit, frame_budget_mb: 10⁶ (never sampled)} and poll the worker's process
   tree (python -m app.engine.worker <run_dir> and its children) every 0.25 s: peak RSS (sum over the tree) and the run
   event current at the peak."""
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

import psutil

data, csv, tl, out = Path(sys.argv[1]), Path(sys.argv[2]), float(sys.argv[3]), Path(sys.argv[4])
API = "http://localhost:8000/api"
H = {"X-Ensemble-Actor": "Ana", "Content-Type": "application/json"}


def call(path, body=None, method=None):
    req = urllib.request.Request(API + path, data=None if body is None else json.dumps(body).encode(), headers=H,
                                 method=method or ("POST" if body is not None else "GET"))
    with urllib.request.urlopen(req, timeout=3600) as r:
        return json.loads(r.read())


t0 = time.time()
up = json.loads(subprocess.run(["curl", "-s", "-H", "X-Ensemble-Actor:Ana", f"{API}/uploads", "-F", f"file=@{csv}", "-F",
                                f"name=p11 calibration {csv.stem}", "-F", "target=y"], capture_output=True, text=True).stdout)
call(f"/uploads/{up['upload_id']}/check", {})
v = call(f"/uploads/{up['upload_id']}/commit", {"reason": "P11 calibration"})["version_id"]
upload_s = time.time() - t0

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "backend"))
os.environ["ENSEMBLE_DATA_DIR"] = str(data)
from app import frames  # noqa: E402
from app.lake import frame as LF  # noqa: E402

me = psutil.Process()
rss0 = me.memory_info().rss
peak = [rss0]
stop = threading.Event()


def watch_self():
    while not stop.is_set():
        peak[0] = max(peak[0], me.memory_info().rss)
        time.sleep(0.05)


th = threading.Thread(target=watch_self); th.start()
t1 = time.time()
df = frames.read(frames.Table(v))
read_s = time.time() - t1
stop.set(); th.join()
actual = int(df.memory_usage(deep=True).sum())
est = LF.estimate(frames.Table(v))
n_rows, n_cols = df.shape
del df

run = call("/runs", {"dataset_id": v, "label": "y", "presets": "medium_quality", "time_limit": tl, "frame_budget_mb": 1_000_000,
                     "name": f"p11 calibration {n_rows}"})["id"]
run_dir = str(data / "runs" / run)


def tree():
    for p in psutil.process_iter(["cmdline"]):
        try:
            if "app.engine.worker" in " ".join(p.info["cmdline"] or []) and run_dir in " ".join(p.info["cmdline"] or []):
                return p
        except psutil.Error:
            pass
    return None


fit_peak, at, seen = 0, None, False
while True:
    st = call(f"/runs/{run}")["status"]
    p = tree()
    if p is not None:
        seen = True
        try:
            rss = p.memory_info().rss + sum(c.memory_info().rss for c in p.children(recursive=True))
        except psutil.Error:
            rss = 0
        if rss > fit_peak:
            fit_peak = rss
            ev = call(f"/runs/{run}").get("n_events")
            at = ev
    if st in ("succeeded", "failed") and (p is None or not p.is_running()):
        break
    time.sleep(0.25)
r = call(f"/runs/{run}")
events = subprocess.run(["curl", "-s", f"{API}/runs/{run}/events"], capture_output=True, text=True).stdout
types = [json.loads(l[6:]) for l in events.splitlines() if l.startswith("data: ")]
at_ev = next((e for e in types if e.get("seq") == (at or 0) - 1), {})
fitted = [e["model"] for e in types if e.get("type") == "model_finished"]
rec = {"rows": n_rows, "cols": n_cols, "version": v, "run": run, "status": r["status"], "error": r.get("error"),
       "time_limit": tl, "preset": "medium_quality", "upload_s": round(upload_s, 1),
       "estimate_mb": est["mb"], "estimate_bytes_per_row": est["bytes_per_row"], "frame_actual_mb": round(actual / 2 ** 20, 1),
       "estimate_over_actual": round(est["bytes"] / actual, 3), "read_s": round(read_s, 1),
       "load_peak_rss_mb": round((peak[0] - rss0) / 2 ** 20, 1), "load_peak_over_actual": round((peak[0] - rss0) / actual, 2),
       "fit_peak_rss_mb": round(fit_peak / 2 ** 20, 1), "fit_peak_per_estimate_byte": round(fit_peak / est["bytes"], 2),
       "fit_peak_per_actual_byte": round(fit_peak / actual, 2), "peak_during_event": {k: at_ev.get(k) for k in ("type", "model", "what")},
       "models_fitted": fitted, "fit_seconds": r.get("fit_seconds"), "elapsed_seconds": r.get("elapsed_seconds")}
with open(out, "a") as f:
    f.write(json.dumps(rec) + "\n")
print(json.dumps(rec, indent=1))
