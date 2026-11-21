"""P11 independent checks of a run (opens the DB, the lake and the run folder directly; no app code for the sample):
  1. the holdout predictions cover exactly the split's holdout rows with a label (by __row_id, from the pinned split blob);
  2. the sample: recomputed here from the table's __row_id + label and the split blob with a pure-numpy splitmix64 and the
     recorded rates → its sha256 = the run's sample_sha256 = run_inputs.sample_sha256;
  3. (with --evaluate) the run's holdout metrics = predictor.evaluate on the WHOLE holdout frame read in one piece.
    python check.py <data_dir> <run_id> [--evaluate]"""
import hashlib
import json
import sqlite3
import sys
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

data, run_id = Path(sys.argv[1]), sys.argv[2]
con = sqlite3.connect(data / "ensemble.sqlite3"); con.row_factory = sqlite3.Row
run = con.execute("SELECT config, result FROM runs WHERE id = ?", (run_id,)).fetchone()
cfg, res = json.loads(run["config"]), json.loads(run["result"])
label, vid = cfg["label"], cfg["dataset_id"]
lake = data / "lake"
key = lambda sha: con.execute("SELECT key FROM blobs WHERE sha256 = ?", (sha,)).fetchone()[0]
parts = [lake / key(r[0]) for r in con.execute("SELECT blob_sha256 FROM table_files WHERE version_id = ? ORDER BY part", (vid,))]
t = pq.read_table([str(p) for p in parts] if len(parts) > 1 else str(parts[0]), columns=["__row_id", label])
ids = t.column(0).to_numpy().astype("int64"); y = t.column(1).to_pandas()
sp = pq.read_table(str(lake / key(cfg["split_pin"]["blob_sha256"])))
fold = pd.Series(sp.column("fold").to_numpy().astype(int), index=sp.column("__row_id").to_numpy().astype("int64")).reindex(ids).to_numpy()
has = y.notna().to_numpy()
hold = np.sort(ids[(fold == -1) & has])
hp = pd.read_csv(Path(cfg["run_dir"]) / "holdout_predictions.csv", usecols=["row"])
print(f"  version {vid}: {len(ids):,} rows, __row_id max {ids.max():,} (gaps: {ids.max() + 1 - len(ids):,})")
print(f"  1. holdout_predictions.csv: {len(hp):,} rows; the split's holdout rows with a label: {len(hold):,}; "
      f"same __row_id set: {np.array_equal(np.sort(hp['row'].to_numpy()), hold)}; result holdout.n_rows {res['holdout']['n_rows']:,}")
fr = res["data"].get("frame") or {}
ri = con.execute("SELECT rows_total, rows_used, sample_sha256, sample_spec, frame_mb FROM run_inputs WHERE run_id = ? AND role = 'train'",
                 (run_id,)).fetchone()
print(f"  run_inputs: rows_total {ri['rows_total']:,} rows_used {ri['rows_used']:,} frame_mb {ri['frame_mb']} sample_sha256 {ri['sample_sha256']}")
if fr.get("sampled"):
    spec = json.loads(ri["sample_spec"])
    M64 = (1 << 64) - 1
    def splitmix64(x):  # written out again here, not imported
        with np.errstate(over="ignore"):
            z = x + np.uint64(0x9E3779B97F4A7C15)
            z = (z ^ (z >> np.uint64(30))) * np.uint64(0xBF58476D1CE4E5B9)
            z = (z ^ (z >> np.uint64(27))) * np.uint64(0x94D049BB133111EB)
            return z ^ (z >> np.uint64(31))
    tr = (fold >= 0) & has  # training rows: folds ≥ 0 with a label (holdout −1, excluded −2 and tuning −3 are not sampled)
    tid, ty = ids[tr], y[tr].astype(str).to_numpy()
    u = splitmix64(tid.astype(np.uint64) ^ np.uint64((spec["seed"] * 0x9E3779B97F4A7C15) & M64)) % np.uint64(1_000_000)
    rates = spec["rates"]
    thr = np.array([int(Decimal(rates.get(c, rates.get("*"))) * 1_000_000) for c in ty]) if "*" not in rates else \
        np.full(len(tid), int(Decimal(rates["*"]) * 1_000_000))
    kept = np.sort(tid[u.astype("int64") < thr])
    sha = hashlib.sha256(b"ensemble.sample/1\n" + kept.astype("<i8").tobytes()).hexdigest()
    blob = fr["sample_blob"]["blob_sha256"]
    bids = pq.read_table(str(lake / key(blob))).column(0).to_numpy()
    print(f"  2. sample recomputed: {len(kept):,} of {len(tid):,} training rows, sha256 {sha}; = result {sha == fr['sample_sha256']}, "
          f"= run_inputs {sha == ri['sample_sha256']}; sample blob {blob[:12]}… holds the same ids {np.array_equal(bids, kept)}")
    print("     blob_refs:", [tuple(r) for r in con.execute("SELECT owner_kind, owner_id, role FROM blob_refs WHERE blob_sha256 = ?", (blob,))])
if "--evaluate" in sys.argv:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "backend"))
    import os
    os.environ["ENSEMBLE_DATA_DIR"] = str(data)
    from autogluon.tabular import TabularPredictor
    from app import frames
    df = frames.read(frames.Table(vid))
    ho = df.loc[hold]  # by __row_id (the index)
    p = TabularPredictor.load(cfg["predictor_path"], verbosity=0, require_py_version_match=False)
    ev = p.evaluate(ho, silent=True)
    got = res["holdout"]["scores"]
    print(f"  3. predictor.evaluate on the whole holdout frame ({len(ho):,} rows, one piece): {ev}")
    print(f"     the run's batched result: {got}; equal: {all(abs(ev[k] - got[k]) < 1e-12 for k in ev)}")
