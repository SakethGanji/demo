"""P15 independent check, outside the API: reads the DB (SQLite path or Postgres URL) and the files directly.

1. The batch frame (built from the 3 batch files with the model's join) equals the training frame's rows with the same
   app_id, every cell (the batch is rows 3000–3499 of the generator), and has exactly the batch grain's rows.
2. scored.csv's probabilities equal the version's predictor on those training-frame rows.
3. The version's input record lists the run's join_input rows (same tables, versions and shas).
Usage: check.py <db> <lake dir> <join run> <version> <batch> <data dir>"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

db, lake, run_id, vid, bid, data = sys.argv[1:7]
if db.startswith("postgres"):
    import psycopg

    con = psycopg.connect(db)
    q = lambda s, a=(): con.execute(s.replace("?", "%s"), a).fetchall()  # noqa: E731
else:
    import sqlite3

    con = sqlite3.connect(db)
    q = lambda s, a=(): con.execute(s, a).fetchall()  # noqa: E731
cfg = json.loads(q("SELECT config FROM runs WHERE id = ?", (run_id,))[0][0])
jn = cfg["join"]
key = q("SELECT key FROM blobs WHERE sha256 = ?", (jn["cache_blob"],))[0][0]
cache = next(Path(lake).rglob(Path(key).name))
train = pd.read_parquet(cache)
bdir = next(Path(data).rglob(f"batches/{bid}"))
frame = pd.read_parquet(bdir / "input.csv")
apps = pd.read_parquet(bdir / "tables" / "applications.parquet")
print(f"   batch frame rows {len(frame)} = batch grain rows {len(apps)}: {len(frame) == len(apps)}; __row_id 0…{frame['__row_id'].max()}")
t = train.set_index("app_id").loc[frame["app_id"]].reset_index()
cols = [c for c in frame.columns if c not in ("__row_id", "defaulted")]
bad = {}
for c in cols:
    a, b = frame[c].reset_index(drop=True), t[c].reset_index(drop=True)
    eq = (a == b) | (a.isna() & b.isna())
    if a.dtype.kind in "f" or b.dtype.kind in "f":
        eq = eq | np.isclose(pd.to_numeric(a, errors="coerce"), pd.to_numeric(b, errors="coerce"), rtol=0, atol=0, equal_nan=True)
    if not eq.all():
        bad[c] = int((~eq).sum())
print(f"   {len(cols)} columns compared with the training frame by app_id: mismatched cells {bad or 0}; dtypes equal",
      all(str(frame[c].dtype) == str(train[c].dtype) for c in cols))
path = q("SELECT path FROM model_versions WHERE id = ?", (vid,))[0][0]
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "backend"))
from autogluon.tabular import TabularPredictor  # noqa: E402

import ensemble_models  # noqa: E402,F401
from app import frames  # noqa: E402

p = TabularPredictor.load(path, verbosity=0, require_py_version_match=False)

v1 = pd.DataFrame({c: frames._v1_column(__import__("pyarrow").array(t[c]), c) for c in p.original_features})
pr = p.predict_proba(v1)
sc = pd.read_csv(bdir / "scored.csv")
d = max(float(np.abs(sc[f"p_{c}"].to_numpy() - pr[c].to_numpy()).max()) for c in pr.columns)
print(f"   scored.csv {len(sc)} rows; max |p(batch) − p(training-frame rows)| = {d:.2e}")
rec = json.loads(q("SELECT result FROM model_versions WHERE id = ?", (vid,))[0][0])["inputs"]
ri = sorted((r[0], r[1], r[2]) for r in q("SELECT version_id, table_name, table_sha256 FROM run_inputs WHERE run_id = ? AND role = 'join_input'", (run_id,)))
mine = sorted((x["dv"], x["name"], x["sha256"]) for x in rec["tables"])
print(f"   input record tables = run_inputs join_input rows: {ri == mine} ({len(ri)} tables)")
