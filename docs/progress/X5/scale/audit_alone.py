"""X5: autopilot.audit.audit alone on the demo credit-default (12k rows), peak RSS (VmHWM) of this process.
usage (from backend/): ENSEMBLE_DATA_DIR=<dir with the demo samples> python audit_alone.py"""
import json, os, sqlite3, sys, time, resource
sys.path.insert(0, os.getcwd())
import numpy as np
from app import frames
from app.autopilot import audit as A
db = sqlite3.connect(os.path.join(os.environ["ENSEMBLE_DATA_DIR"], "ensemble.sqlite3"))
vid, = db.execute("SELECT id FROM datasets WHERE id = 'credit-default'").fetchone()
path = str(frames.version_file(vid))
df = frames.read_csv_path(path)
label = "default" if "default" in df.columns else df.columns[-1]
feats = [c for c in df.columns if c != label]
num = [c for c in feats if str(df[c].dtype).startswith(("int", "float"))]
cat = [c for c in feats if c not in num]
print("dataset", vid, df.shape, "label", label, "num", len(num), "cat", len(cat), flush=True)
classes = sorted(df[label].astype(str).unique())
rng = np.random.default_rng(0)
rows = list(range(len(df))); p = rng.random(len(df)); proba = [[1 - x, x] for x in p]
hold = rows[-2400:]
del df
t = time.time()
out = A.audit(path, label, feats, [], num, cat, classes, rows, proba, hold)
hwm = int([l for l in open("/proc/self/status") if l.startswith("VmHWM")][0].split()[1]) / 1e6
print(f"audit {time.time()-t:.1f} s, items {len(out.get('items', []))}, VmHWM {hwm:.2f} GB")
