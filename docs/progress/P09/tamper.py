"""P09 verification: DLA4 #3's tamper, done with the database and the lake opened directly (not through the app).

    python tamper.py <data_dir> <version_id> update   # 1. try to point the split row at another blob → the trigger refuses
    python tamper.py <data_dir> <version_id> replace  # 2. a new blob with a 50-row holdout + a new splits row (the old row
                                                      #    deleted after dropping the delete trigger), as someone with file
                                                      #    access would; no audit row
    python tamper.py <data_dir> <version_id> forge    # 3. also forge a split_created audit row for the tampered split
"""
import hashlib
import json
import os
import secrets
import sqlite3
import sys
from datetime import datetime, timezone

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

data_dir, vid, what = sys.argv[1], sys.argv[2], sys.argv[3]
con = sqlite3.connect(os.path.join(data_dir, "ensemble.sqlite3"))
con.row_factory = sqlite3.Row
row = dict(con.execute("SELECT s.*, b.key FROM splits s JOIN blobs b ON b.sha256 = s.blob_sha256 WHERE s.version_id = ?"
                       " ORDER BY s.created_at DESC LIMIT 1", (vid,)).fetchone())
now = datetime.now(timezone.utc).isoformat(timespec="seconds")

if what == "update":
    try:
        con.execute("UPDATE splits SET blob_sha256 = ?, split_sha256 = ? WHERE id = ?", ("0" * 64, "0" * 64, row["id"]))
        print("UPDATE went through (unexpected)")
    except sqlite3.DatabaseError as e:
        print(f"UPDATE splits … WHERE id = {row['id']} → refused: {e}")
    try:
        con.execute("DELETE FROM splits WHERE id = ?", (row["id"],))
        print("DELETE went through (unexpected)")
    except sqlite3.DatabaseError as e:
        print(f"DELETE FROM splits WHERE id = {row['id']} → refused: {e}")
    sys.exit(0)

if what == "replace":
    t = pq.read_table(os.path.join(data_dir, "lake", row["key"]))
    ids, fold = t.column(0).to_numpy(), t.column(1).to_numpy().astype("int8").copy()
    hold = np.flatnonzero(fold == -1)
    keep, move = hold[:50], hold[50:]  # 50 of the 180 holdout rows stay held out; 130 move into training folds
    fold[move] = (np.arange(len(move)) % 5).astype("int8")
    tmp = os.path.join(data_dir, "lake", "tmp", "tamper.parquet")
    os.makedirs(os.path.dirname(tmp), exist_ok=True)
    pq.write_table(pa.table({"__row_id": pa.array(ids, pa.int64()), "fold": pa.array(fold, pa.int8())}), tmp, compression="zstd")
    raw = open(tmp, "rb").read()
    bsha = hashlib.sha256(raw).hexdigest()
    h = hashlib.sha256(b"ensemble.split/1\n")
    a = np.empty(len(ids), dtype=[("r", "<i8"), ("f", "i1")])
    a["r"], a["f"] = ids, fold
    h.update(a.tobytes())
    ssha = h.hexdigest()
    key = f"blobs/{bsha[:2]}/{bsha}.parquet"
    os.makedirs(os.path.join(data_dir, "lake", "blobs", bsha[:2]), exist_ok=True)
    os.replace(tmp, os.path.join(data_dir, "lake", key))
    con.execute("INSERT OR IGNORE INTO blobs (sha256, key, kind, bytes, n_rows, state, created_at) VALUES (?, ?, 'split', ?, ?, 'live', ?)",
                (bsha, key, len(raw), len(ids), now))
    con.execute("DROP TRIGGER splits_ro_d")
    con.execute("DELETE FROM splits WHERE id = ?", (row["id"],))
    nid = "spl_" + secrets.token_hex(6)
    con.execute("INSERT INTO splits (id, version_id, table_name, split_key, target, strategy, params_json, seed, holdout_share, n_folds,"
                " blob_sha256, split_sha256, legacy_sha256, n_rows, n_holdout, carried_from, created_at, created_by)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?)",
                (nid, vid, row["table_name"], row["split_key"], row["target"], row["strategy"], row["params_json"], row["seed"],
                 row["holdout_share"], row["n_folds"], bsha, ssha, len(ids), int((fold == -1).sum()), row["carried_from"],
                 row["created_at"], row["created_by"]))
    con.commit()
    print(f"replaced {row['id']} (holdout {row['n_holdout']}, split_sha256 {row['split_sha256'][:12]}…) by {nid}: "
          f"holdout {int((fold == -1).sum())}, split_sha256 {ssha[:12]}…, blob {bsha[:12]}… (self-consistent: its key is its hash)")
    sys.exit(0)

if what == "forge":
    con.execute("INSERT INTO audit_log (ts, actor, action, run_id, comment, details) VALUES (?, 'Ana', 'split_created', NULL, '', ?)",
                (now, json.dumps({"dataset_id": vid, "root_id": vid, "split_id": row["id"], "split_sha256": row["split_sha256"],
                                  "blob_sha256": row["blob_sha256"], "table": row["table_name"], "split_key": row["split_key"]})))
    con.commit()
    print(f"forged a split_created audit row for {row['id']}")
