"""F-X2: in-process checks with the code under test (cwd = <code>/backend, ENSEMBLE_DATA_DIR = the data dir):
  m1  pre-DL4 bool column with gaps (loans `flag`) read through the legacy path: the NA kind (NaN vs None)
  M4  run rL1 (outside change e): its holdout_predictions rows vs the holdout of the Parquet split it is pinned to
usage: fx2_inproc.py <state.json>"""
import json, os, sys
from pathlib import Path
sys.path.insert(0, os.getcwd())
import numpy as np, pandas as pd
from app import config, frames
from app.lake import splitstore as SS
from app.lake.store import open_store
from app.db import Database
st = json.loads(Path(sys.argv[1]).read_text()); s = config.load_settings(); db = Database(s.db_path); store = open_store(s)
for k in ("L1", "L2", "LR"):
    try:
        df = frames.read(frames.LegacyPath(str(s.datasets_dir / st[k] / "data.csv")))
    except Exception as e:  # noqa: BLE001
        print(f"m1 {k}: {type(e).__name__} {e}"[:200]); continue
    col = df["flag"]
    na = col[col.isna()]
    print(f"m1 {k} flag: dtype={col.dtype} missing={len(na)} kinds={sorted({type(v).__name__ for v in na})} "
          f"astype(str) of a missing cell={sorted(set(na.astype(str)))}")
with db.connect() as con:
    ri = con.execute("SELECT split_id FROM run_inputs WHERE run_id = ?", (st["rL1"],)).fetchone()
pred = set(pd.read_csv(s.runs_dir / st["rL1"] / "holdout_predictions.csv", usecols=["row"])["row"].tolist())
if ri and ri[0]:
    row = SS.get_by_id(db, ri[0]); ids, folds = SS.read_blob(store, row.get("blob_key") or SS._blob_key(db, row["blob_sha256"]))
    tids = SS.table_row_ids(st["L1"]); pos = {int(i): p for p, i in enumerate(tids.tolist())}
    hold = {pos[int(i)] for i, f in zip(ids.tolist(), folds.tolist()) if f == -1}
    print(f"M4 rL1 pinned to {ri[0]}: Parquet holdout {len(hold)} rows, holdout_predictions {len(pred)} rows, "
          f"preds not in the Parquet holdout: {len(pred - hold)}")
else:
    print(f"M4 rL1: no split_id (version not migrated); holdout_predictions {len(pred)} rows")
