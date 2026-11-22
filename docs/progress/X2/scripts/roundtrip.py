"""X2: round-trip spot checks with the head code (wt_head/backend on PYTHONPATH, ENSEMBLE_DATA_DIR = the migrated dir).
Per version: the 'before' frame (frame_io.read of data.csv with the pre-P10 code) vs the head's reads after the migration
  - legacy path (what an old run's readers get, by position): exact equality (values, dtypes, NA kinds, order, text)
  - Table(vid) (what a new run gets): the same values, index = __row_id; ids = lineage root_rows / 0..n-1 / fresh
Per old run: its holdout/fold rows (split JSON at its splits_path before) vs after: the Parquet split it is pinned to
(run_inputs.split_id, mapped to positions through the table's ids in read order), the view file at splits_path, and
the run's holdout_predictions rows. usage: roundtrip.py <before dir> <orig before dir> <data dir>"""
import json, pickle, sqlite3, sys
from pathlib import Path
import numpy as np, pandas as pd, pyarrow.parquet as pq
from app import frames, config
from app.lake import splitstore as SS
from app.lake.store import open_store

B, O, D = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
before = pickle.load(open(B / "frames.pkl", "rb")); runs_b = pickle.load(open(B / "runs.pkl", "rb"))
runs_o = pickle.load(open(O / "runs.pkl", "rb")); frames_o = pickle.load(open(O / "frames.pkl", "rb"))
con = sqlite3.connect(D / "ensemble.sqlite3"); con.row_factory = sqlite3.Row
lm = {r["version_id"]: dict(r) for r in con.execute("SELECT * FROM lake_migrations")}
store = open_store(config.load_settings())


def na_kinds(s):
    return [type(v).__name__ for v in s if v is None or (isinstance(v, float) and np.isnan(v))] if s.dtype == object else None


def lz(s):
    return int(sum(1 for v in s if isinstance(v, str) and len(v) > 1 and v[0] == "0" and v[1:2].isdigit())) if s.dtype == object else 0


rows = []
for vid, A in before.items():
    st = (lm.get(vid) or {}).get("status")
    r = {"version": vid, "status": st, "reason": (lm.get(vid) or {}).get("reason"), "rows": len(A), "cols": A.shape[1]}
    if st == "migrated":
        L = frames.read(frames.LegacyPath(str(D / "datasets" / vid / "data.csv")))
        T = frames.read(frames.Table(vid))
        try:
            pd.testing.assert_frame_equal(A, L, check_exact=True); r["legacy_equal"] = True
        except AssertionError as e:
            r["legacy_equal"] = False; r["legacy_diff"] = str(e)[:300]
        r["dtypes_equal"] = A.dtypes.astype(str).to_dict() == L.dtypes.astype(str).to_dict()
        r["na_kinds_equal"] = all(na_kinds(A[c]) == na_kinds(L[c]) for c in A.columns)
        r["na_kinds_diff"] = [c for c in A.columns if na_kinds(A[c]) != na_kinds(L[c])]
        r["leading_zero_cells"] = [sum(lz(A[c]) for c in A.columns), sum(lz(L[c]) for c in L.columns)]
        r["na_cells"] = [int(A.isna().sum().sum()), int(L.isna().sum().sum())]
        r["empty_str_cells"] = [int((A.astype(str) == "").sum().sum()), int((L.astype(str) == "").sum().sum())]
        r["text_equal"] = A.to_csv(index=False) == L.to_csv(index=False)
        r["table_values_equal"] = T.reset_index(drop=True).equals(A)
        ids = np.asarray(T.index, dtype="int64")
        lin = None
        lp = D / "datasets" / (vid + ".migrated") / "lineage.json"
        if lp.exists():
            lin = json.loads(lp.read_text()).get("root_rows")
        r["ids"] = ("root_rows" if lin is not None and len(lin) == len(ids) and list(ids) == list(lin) else
                    "0..n-1" if list(ids) == list(range(len(ids))) else f"other [{ids.min()}..{ids.max()}]")
        r["ids_sorted"] = bool((np.diff(ids) > 0).all()) if len(ids) > 1 else True
        r["neg_ids"] = int((ids < 0).sum())
        r["dtypes"] = {c: str(t) for c, t in A.dtypes.items()}
    rows.append(r)
runs = []
for rid, rb in runs_b.items():
    vid = rb["version"]; ro = runs_o.get(rid) or {}
    ri = con.execute("SELECT * FROM run_inputs WHERE run_id = ? AND role = 'train'", (rid,)).fetchone()
    e = {"run": rid, "version": vid, "status": (lm.get(vid) or {}).get("status"), "run_inputs": dict(ri) if ri else None}
    if e["status"] != "migrated" or rb["fold"] is None or (ri and ri["table_sha256"]):
        e["note"] = "not a CSV-era run on a migrated version" if not (ri and ri["table_sha256"]) or e["status"] != "migrated" else "Parquet run"
        runs.append(e); continue
    if ri and ri["split_id"]:
        sp = con.execute("SELECT s.*, b.key FROM splits s JOIN blobs b ON b.sha256 = s.blob_sha256 WHERE s.id = ?", (ri["split_id"],)).fetchone()
        t = pq.read_table(store.local_copy(sp["key"])).to_pandas()
        order = SS.table_row_ids(vid)
        fold_by_id = dict(zip(t["__row_id"].astype("int64"), t["fold"].astype(int)))
        after_fold = [fold_by_id[int(i)] for i in order]
        e["parquet_fold_eq_before"] = after_fold == list(rb["fold"])
        e["parquet_fold_eq_original"] = after_fold == list(ro.get("fold") or [])
        e["parquet_holdout_eq_pred_rows"] = sorted(i for i, f in enumerate(after_fold) if f == -1) == rb["pred_rows"]
    else:
        e["parquet_split"] = None
    vp = D / "datasets" / vid / (rb["rel"] or "")
    if rb["rel"] and vp.exists():
        v = json.loads(vp.read_text())
        e["view_fold_eq_before"] = list(v.get("fold") or []) == list(rb["fold"])
        e["view_fold_eq_original"] = list(v.get("fold") or []) == list(ro.get("fold") or [])
    else:
        e["view"] = "missing"
    e["pred_rows_eq_original_holdout"] = rb["pred_rows"] == ro.get("holdout")
    runs.append(e)
print(json.dumps({"versions": rows, "runs": runs}, indent=1, default=str))
