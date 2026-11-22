"""P13: holdout identity across a delta. Reads both versions' committed splits of a table (needs the API's
ENSEMBLE_DATA_DIR / ENSEMBLE_DB_URL). Usage: holdout.py <parent v> <child v> <table> <delta.csv> <key>"""
import csv, sys

import numpy as np

from app import frames
from app.lake import duck, splitstore as SS

pv, cv, table, delta, key = sys.argv[1:6]
db, st = frames._db(), frames._store()


def folds(v):
    rows = [r for r in SS.rows_of_version(db, v) if r["table_name"] == table]
    r = rows[0]
    ids, f = SS.read_blob(st, r["blob_key"])
    return r["split_key"], dict(zip(ids.tolist(), f.tolist()))


def ids_of(v):
    lt = frames.lake_table(frames.Table(v, table))
    con = duck.connect("job")
    return {k: i for i, k in con.execute(f'SELECT __row_id, "{key}" FROM {frames.lake_sql(lt)}').fetchall()}


ops = {}
for r in csv.DictReader(open(delta)):
    ops[r[key]] = (r.get("_op") or "U").strip().upper()[0]
(kp, fp), (kc, fc) = folds(pv), folds(cv)
ip, ic = ids_of(pv), ids_of(cv)
upd = [k for k, o in ops.items() if o in "UIM" and k in ip]
same_id = sum(ip[k] == ic.get(k) for k in upd)
same_fold = sum(fp[ip[k]] == fc[ic[k]] for k in upd)
hold_upd = sum(fp[ip[k]] == -1 for k in upd)
kept = [k for k in ip if k not in ops]
print(f"split {kp} -> {kc} (same key: {kp == kc})")
print(f"updated rows: {len(upd)}, same __row_id {same_id}, same fold {same_fold}, of which in the holdout {hold_upd}")
print(f"untouched rows: {len(kept)}, same fold {sum(fp[ip[k]] == fc[ic[k]] for k in kept)}")
print(f"parent holdout {sum(1 for x in fp.values() if x == -1)}, child holdout {sum(1 for x in fc.values() if x == -1)}, "
      f"deleted holdout rows {sum(fp[ip[k]] == -1 for k, o in ops.items() if o == 'D' and k in ip)}, "
      f"new rows in the holdout {sum(fc[ic[k]] == -1 for k in ic if k not in ip)}")
