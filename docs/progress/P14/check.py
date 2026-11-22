"""Independent check of a join frame: reads the DB (SQLite path or Postgres URL) and the lake directly, recomputes the
frame of the 'apps_full' spec with pandas from the input tables' Parquet parts, and compares every cell with the cache
blob; then prints what the runs recorded. Usage: check.py <db path | postgres url> <lake dir> <frame id> <run id>..."""
import hashlib, json, sys, warnings
warnings.filterwarnings("ignore")
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

db, lake, fid, runs = sys.argv[1], Path(sys.argv[2]), sys.argv[3], sys.argv[4:]
if db.startswith("postgres"):
    import psycopg
    from psycopg.rows import dict_row
    con = psycopg.connect(db, row_factory=dict_row); P = "%s"
else:
    import sqlite3
    con = sqlite3.connect(db); con.row_factory = sqlite3.Row; P = "?"
def q(sql, *a): return [dict(r) for r in con.execute(sql.replace("?", P), a).fetchall()]

f = q("SELECT * FROM join_frames WHERE id = ?", fid)[0]
spec = json.loads(q("SELECT spec_json FROM joins WHERE id = ?", f["join_id"])[0]["spec_json"])
blob = q("SELECT key, bytes FROM blobs WHERE sha256 = ?", f["cache_blob"])[0]
raw = (lake / blob["key"]).read_bytes()
print("cache blob", f["cache_blob"][:16], "sha256 of the file", hashlib.sha256(raw).hexdigest()[:16], "equal", hashlib.sha256(raw).hexdigest() == f["cache_blob"])
def table(vid, name):
    parts = q("SELECT b.key FROM table_files t JOIN blobs b ON b.sha256 = t.blob_sha256 WHERE t.version_id = ? AND t.table_name = ? ORDER BY t.part", vid, name)
    return pd.concat([pq.read_table(lake / p["key"]).to_pandas() for p in parts], ignore_index=True)
ins = json.loads(f["inputs_json"])
g = table(ins[0]["version_id"], ins[0]["table"]).sort_values("__row_id").reset_index(drop=True)
exp = g.copy()
for i, j in enumerate(spec["joins"]):
    t = table(ins[i + 1]["version_id"], ins[i + 1]["table"])
    l, r = [p[0] for p in j["on"]], [p[1] for p in j["on"]]
    if j["type"] == "aggregate":
        gk = g[["__row_id", *l, *{a["window"]["relative_to"] for a in j["aggs"] if a.get("window")}]].rename(columns={"__row_id": "__gid"})
        t2 = t.drop(columns=["__row_id"]).merge(gk, left_on=r, right_on=l, suffixes=("", "__g"))
        cols = {}
        for a in j["aggs"]:
            s = t2
            if a.get("window"):
                w = a["window"]; d = pd.to_datetime(s[w["date"]]); rel = pd.to_datetime(s[w["relative_to"] + ("__g" if w["relative_to"] in t.columns else "")])
                n = pd.Timedelta(days=int(w["days"]))
                s = s[(d >= rel + n) & (d < rel)] if w["days"] < 0 else s[(d > rel) & (d <= rel + n)]
            gb = s.groupby("__gid")
            v = {"count": gb.size() if not a.get("column") else gb[a["column"]].count(), "sum": gb[a["column"]].sum() if a.get("column") else None,
                 "mean": gb[a["column"]].mean() if a.get("column") else None, "max": gb[a["column"]].max() if a.get("column") else None,
                 "min": gb[a["column"]].min() if a.get("column") else None}[a["fn"]]
            col = exp["__row_id"].map(v)
            if a["fn"] == "count":
                col = col.fillna(0)
            cols[j["prefix"] + a["name"]] = col
        for k, v in cols.items():
            exp[k] = v
    else:
        keep = j["columns"] or [c for c in t.columns if c not in r and c != "__row_id"]
        tt = t[[*r, *keep]].rename(columns={c: j["prefix"] + c for c in keep})
        exp = exp.merge(tt, left_on=l, right_on=r, how="left", suffixes=("", "__r"))
        exp = exp.drop(columns=[c for c in r if c not in l] + [c + "__r" for c in r if c in l and c + "__r" in exp.columns])
fr = pq.read_table(lake / blob["key"]).to_pandas()
print("frame rows", len(fr), "grain rows", len(g), "__row_id equal and ordered", bool(np.array_equal(fr["__row_id"].to_numpy(), g["__row_id"].to_numpy())))
bad = []
for c in fr.columns:
    a, b = fr[c].reset_index(drop=True), exp[c].reset_index(drop=True)
    if a.dtype.kind in "fiu" or b.dtype.kind in "fiu":
        ok = np.allclose(pd.to_numeric(a, errors="coerce").astype(float), pd.to_numeric(b, errors="coerce").astype(float), equal_nan=True)
    else:
        ok = a.astype(str).where(a.notna(), None).equals(b.astype(str).where(b.notna(), None))
    if not ok:
        bad.append(c)
print("columns", len(fr.columns) - 1, "| cells equal to the pandas recomputation:", "all" if not bad else f"NOT {bad}")
for c in fr.columns:
    if fr[c].isna().any():
        print("   missing in", c, int(fr[c].isna().sum()))
for rid in runs:
    for r in q("SELECT role, version_id, table_name, join_frame_id, table_sha256, read_sha256, match, split_id FROM run_inputs WHERE run_id = ? ORDER BY role, table_name", rid):
        print(" ", rid, r["role"], r["version_id"], r["table_name"], r["join_frame_id"], (r["table_sha256"] or "")[:16], (r["read_sha256"] or "")[:16], "match", r["match"], r["split_id"])
