"""C2: Feature Lab's SQL apply (_version_sql) writes the same columns featurelab.compute gives on the full frame.
Run in-process against a data dir (API stopped): python fl_sql_eq.py <data_dir> <version_id>"""
import json, os, sys
from types import SimpleNamespace
os.environ["ENSEMBLE_DATA_DIR"] = sys.argv[1]
sys.path.insert(0, "/home/user/demo/backend")
import numpy as np, pandas as pd
from app import config, frames, featurelab as fl
from app.db import Database
from app.datasets import DatasetStore
from app import featurelab_api as FA
st = config.load_settings(); db = Database(st.db_path); store = DatasetStore(st, db)
v = sys.argv[2]; rec = db.get_dataset(v)
df = frames.read_version(v).reset_index(drop=True)
CANDS = json.load(open("fl_cands.json"))
kept = []
for t in CANDS:
    t = dict(t); t["name"] = fl.name_of(t)
    if t["op"] == "bin":  # edges fitted on the training rows (as the search does)
        fl.compute(df, t, np.ones(len(df), bool))
    kept.append(t)
res = {"kept": kept, "metric": "auc", "improvement_oof": 0.0, "holdout": None, "sample": {"note": "test"}}
req = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(datasets=store, db=db)))
out = FA._version_sql(req, rec, res, "fx1")
print("sql version:", out)
new = frames.read_version(out["id"]).reset_index(drop=True)
assert len(new) == len(df)
for t in kept:
    want = fl.compute(df, t).reset_index(drop=True)
    got = new[t["name"]]
    if t["op"] == "cross":
        same = (want.astype(str) == got.astype(str)).all()
    else:
        w, g = pd.to_numeric(want, errors="coerce").to_numpy(float), pd.to_numeric(got, errors="coerce").to_numpy(float)
        same = bool(np.all((np.isnan(w) & np.isnan(g)) | np.isclose(w, g, rtol=1e-9, atol=1e-12)))
    print(f"  {t['name']:28s} {'same' if same else 'DIFFERENT'}" + ("" if same else f"  e.g. pandas {want[:5].tolist()} sql {got[:5].tolist()}"))
print("other columns unchanged:", all(new[c].equals(df[c]) or (new[c].astype(str) == df[c].astype(str)).all() for c in df.columns))
