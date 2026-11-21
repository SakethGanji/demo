"""C2: Feature Lab's SQL apply at 10M rows, in-process (peak RSS of this process). python fl_big_sql.py <data_dir> <vid>"""
import os, sys, time, resource
from types import SimpleNamespace
os.environ["ENSEMBLE_DATA_DIR"] = sys.argv[1]
sys.path.insert(0, "/home/user/demo/backend")
from app import config, featurelab as fl
from app.db import Database
from app.datasets import DatasetStore
from app import featurelab_api as FA
st = config.load_settings(); db = Database(st.db_path); store = DatasetStore(st, db)
v = sys.argv[2]; rec = db.get_dataset(v)
kept = [{"op": "agg", "entity": "branch", "col": "amount", "fn": "mean"}, {"op": "ratio", "a": "amount", "b": "income"},
        {"op": "date_part", "col": "app_date", "part": "month"}, {"op": "bin", "col": "age", "k": 5, "edges": [29.0, 41.0, 53.0, 65.0]},
        {"op": "cross", "a": "segment", "b": "channel"}, {"op": "log", "col": "income"}]
for t in kept: t["name"] = fl.name_of(t)
res = {"kept": kept, "metric": "auc", "improvement_oof": 0.0, "holdout": None, "sample": {"note": "in-process check"}}
req = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(datasets=store, db=db)))
t0 = time.time(); r0 = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e6
out = FA._version_sql(req, rec, res, "fx1")
print(f"version {out['id']} rows {out['rows']} columns_added {out['columns_added']} in {time.time() - t0:.1f}s;"
      f" peak RSS of this process {resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e6:.2f} GB (before the call {r0:.2f} GB)")
