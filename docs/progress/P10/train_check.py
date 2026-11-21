"""P10 chain step 5: train on a migrated version; read_mode engine_v1; the new run's holdout ids = the old run's.
usage: train_check.py <state.json> <version key> <old run key> <data dir>"""
import json
import sqlite3
import sys
import time
from pathlib import Path

import httpx
import pandas as pd

st = json.loads(Path(sys.argv[1]).read_text())
vid, old = st[sys.argv[2]], st[sys.argv[3]] if sys.argv[3] != "-" else None
db = sqlite3.connect(Path(sys.argv[4]) / "ensemble.sqlite3")
c = httpx.Client(base_url="http://localhost:8000/api", timeout=600, headers={"X-Ensemble-Actor": "Ana"})
r = c.post("/runs", json={"dataset_id": vid, "label": "y", "time_limit": 20, "presets": "medium_quality", "by": "Ana",
                          "name": f"after P10 on {sys.argv[2]}"})
assert r.status_code == 201, r.text
rid = r.json()["id"]
for _ in range(200):
    s = c.get(f"/runs/{rid}").json()
    if s["status"] in ("succeeded", "failed"):
        break
    time.sleep(3)
print(f" new run {rid} on {sys.argv[2]} ({vid}): {s['status']} {(s.get('error') or '')[:300]}")
ri = db.execute("SELECT read_mode, substr(table_sha256,1,12), match, split_id, substr(split_sha256,1,12), legacy_csv_sha256"
                " FROM run_inputs WHERE run_id = ?", (rid,)).fetchone()
print("  run_inputs: read_mode", ri[0], "| table_sha256", ri[1], "match", ri[2], "| split", ri[3], ri[4], "| legacy_csv", ri[5])
row = db.execute("SELECT legacy_sha256, split_sha256 FROM splits WHERE id = ?", (ri[3],)).fetchone()
print("  its split: legacy_sha256 (the DL4 JSON)", (row[0] or "")[:12], "| split_sha256", row[1][:12])


def holdout_ids(run_id, positional):
    cfg = json.loads(db.execute("SELECT config FROM runs WHERE id = ?", (run_id,)).fetchone()[0])
    hp = pd.read_csv(Path(cfg["run_dir"]) / "holdout_predictions.csv")
    rows = hp["row"].to_numpy()
    if not positional:
        return set(int(x) for x in rows)
    import pyarrow.parquet as pq
    keys = [k for (k,) in db.execute("SELECT b.key FROM table_files f JOIN blobs b ON b.sha256 = f.blob_sha256"
                                     " WHERE f.version_id = ? ORDER BY f.part", (vid,))]
    lake = Path(sys.argv[4]) / "lake"
    ids = pd.concat([pq.read_table(lake / k, columns=["__row_id"]).to_pandas() for k in keys])["__row_id"].to_numpy()
    return set(int(ids[p]) for p in rows)  # an old run's rows are data.csv positions; the table is in data.csv order


new = holdout_ids(rid, False)
print("  new run holdout:", len(new), "rows")
if old:
    o = holdout_ids(old, True)
    print(f"  old run {old} holdout (positions → __row_id through the migrated table):", len(o), "rows")
    print("  identical holdout ids:", o == new)
    cmp = c.post("/compare", json={"a": old, "b": rid}).json()
    print("  compare old new:", cmp.get("paired"), "|", cmp.get("label"), "|", cmp.get("overlap", {}).get("text"),
          "| how", {k: v["how"] for k, v in cmp.get("roots", {}).items()})
else:  # no old run on this version: the DL4 JSON the split was migrated from, by position → __row_id
    rel = json.loads(db.execute("SELECT detail FROM lake_migrations WHERE version_id = ?", (vid,)).fetchone()[0])["splits"][0]["file"]
    j = json.loads((Path(sys.argv[4]) / "datasets" / (vid + ".migrated") / rel).read_text())
    import pyarrow.parquet as pq
    keys = [k for (k,) in db.execute("SELECT b.key FROM table_files f JOIN blobs b ON b.sha256 = f.blob_sha256"
                                     " WHERE f.version_id = ? ORDER BY f.part", (vid,))]
    ids = pd.concat([pq.read_table(Path(sys.argv[4]) / "lake" / k, columns=["__row_id"]).to_pandas() for k in keys])["__row_id"].to_numpy()
    o = set(int(ids[p]) for p in j["holdout"])
    print(f"  the DL4 JSON {rel} holdout (positions → __row_id):", len(o), "rows; identical:", o == new,
          "| table in data.csv order, ids increasing:", bool((ids[1:] > ids[:-1]).all()))
print("RUN", rid)
