"""X2: the 'before' truth, with the pre-P10 code (acfe988 on PYTHONPATH): every CSV-era version's frame as the worker
read it (frame_io.read: schema.json when present, else plain pd.read_csv) and every run's split (the JSON at its
splits_path, by name, in this data dir) + its holdout_predictions rows. usage: frames_before.py <data dir> <out dir>"""
import json, pickle, sqlite3, sys
from pathlib import Path
from app import frame_io as FIO  # acfe988

D, out = Path(sys.argv[1]), Path(sys.argv[2]); out.mkdir(parents=True, exist_ok=True)
con = sqlite3.connect(D / "ensemble.sqlite3")
frames = {}
for vid, sto, state in con.execute("SELECT id, COALESCE(storage,'csv'), state FROM datasets"):
    p = D / "datasets" / vid / "data.csv"
    if sto != "csv" or not p.exists():
        continue
    frames[vid] = FIO.read(p.read_bytes(), FIO.load(p.parent))
pickle.dump(frames, open(out / "frames.pkl", "wb"))
runs = {}
for rid, vid, cfg in con.execute("SELECT id, dataset_id, config FROM runs"):
    cfg = json.loads(cfg or "{}"); sp = cfg.get("splits_path")
    rel = None
    if sp:
        q = Path(sp); rel = f"splits/{q.name}" if q.parent.name == "splits" else q.name
    j = None
    if rel and (D / "datasets" / vid / rel).exists():
        j = json.loads((D / "datasets" / vid / rel).read_text())
    hp = D / "runs" / rid / "holdout_predictions.csv"
    hrows = sorted(int(x) for x in __import__("pandas").read_csv(hp)["row"]) if hp.exists() else None
    runs[rid] = {"version": vid, "rel": rel, "fold": j.get("fold") if j else None, "holdout": sorted(j["holdout"]) if j else None,
                 "pred_rows": hrows}
pickle.dump(runs, open(out / "runs.pkl", "wb"))
print("frames", len(frames), "runs", len(runs), "runs with split json", sum(1 for r in runs.values() if r["fold"] is not None))
for rid, r in runs.items():
    if r["holdout"] is not None and r["pred_rows"] is not None and r["holdout"] != r["pred_rows"]:
        print(" note:", rid, r["version"], "split JSON holdout ≠ holdout_predictions rows:",
              len(set(r["holdout"]) ^ set(r["pred_rows"])), "rows differ")
