"""M2 + M6 repro. usage: m26.py <data_dir> setup | attack | restore
setup:   v1 (3,000 rows) -> run r1 on v1 -> edit child (every 7th row deleted; v1's split carried into it)
attack:  forge v1's split IN PLACE (new self-consistent blob, UPDATE splits with splits_ro_u dropped), rewrite its
         split_created audit row (audit_ro_u dropped), DELETE its run_inputs rows: (a) as is, (b) with ri_ro_d dropped.
         Then GET verify v1 / child and POST /runs on v1.
restore: POST /restore-splits offer -> apply, then verify v1 + child, and a run on the child."""
import hashlib, json, os, secrets, sqlite3, sys, time
from datetime import datetime, timezone
import numpy as np, pyarrow as pa, pyarrow.parquet as pq
from c import *
D, what = sys.argv[1], sys.argv[2]
FULL = what == "attack"  # "forge": M2's tamper only (blob + splits row); "attack": M6's (+ audit row + run_inputs)
if what == "forge": what = "attack"
st_file = os.path.join(D, "m26_ids.json")
def wait_run(r):
    for _ in range(150):
        st, x = c("GET", f"/api/runs/{r}", quiet=True)
        if x.get("status") in ("succeeded", "failed"): return x
        time.sleep(3)
    return x
def run_on(v, name):
    return c("POST", "/api/runs", {"dataset_id": v, "label": "y", "time_limit": 20, "presets": "medium_quality",
                                    "drop_columns": ["acct"], "name": name, "by": "fx1"}, show=300)
def checks(v):
    st, x = c("GET", f"/api/datasets/{v}/verify", quiet=True)
    print(f"  verify {v}: {st} ok={x.get('ok')}")
    for sp in (x.get("splits") or []):
        for ch in sp.get("checks") or []:
            if not ch["ok"]: print(f"    split {sp.get('split_id') or sp.get('id')} check {ch['n']} FAILED: {ch['detail'][:200]}")
    return x
if what == "setup":
    rng = np.random.default_rng(11); N = 3000
    x1, x2 = rng.normal(size=N).round(4), rng.normal(size=N).round(4)
    y = (rng.random(N) < 1 / (1 + np.exp(-(x1 * 1.5 - x2)))).astype(int)
    body = ("acct,x1,x2,y\n" + "".join(f"A{i:05d},{a},{b},{t}\n" for i, (a, b, t) in enumerate(zip(x1, x2, y)))).encode()
    u = upload(body, "w1.csv"); c("PATCH", f"/api/uploads/{u}", {"target": "y"}, quiet=True)
    st, ck = c("POST", f"/api/uploads/{u}/check", quiet=True)
    st, v = c("POST", f"/api/uploads/{u}/commit", {"by": "fx1", "check_sha256": ck["check_sha256"]}, show=100); v1 = v["version_id"]
    st, r = run_on(v1, "fx1-r1"); r1 = r["id"]; x = wait_run(r1); print("r1", r1, x["status"])
    st, e = c("POST", f"/api/datasets/{v1}/edit", {"by": "fx1", "reason": "F-X1 M2: every 7th row deleted",
                                                   "ops": [{"op": "delete_rows", "row_ids": list(range(0, N, 7))}]}, show=150)
    child = e.get("version_id") or e.get("id")
    json.dump({"v1": v1, "r1": r1, "child": child}, open(st_file, "w"))
    con = sqlite3.connect(os.path.join(D, "ensemble.sqlite3"))
    print("splits:", con.execute("SELECT id, version_id, carried_from, n_holdout FROM splits").fetchall())
elif what == "attack":
    ids = json.load(open(st_file)); v1 = ids["v1"]
    con = sqlite3.connect(os.path.join(D, "ensemble.sqlite3")); con.row_factory = sqlite3.Row
    row = dict(con.execute("SELECT s.*, b.key FROM splits s JOIN blobs b ON b.sha256 = s.blob_sha256 WHERE s.version_id = ?", (v1,)).fetchone())
    t = pq.read_table(os.path.join(D, "lake", row["key"]))
    rid, fold = t.column(0).to_numpy(), t.column(1).to_numpy().astype("int8").copy()
    hold = np.flatnonzero(fold == -1); move = hold[50:]
    fold[move] = (np.arange(len(move)) % 5).astype("int8")
    tmp = os.path.join(D, "lake", "tmp", "forge.parquet"); os.makedirs(os.path.dirname(tmp), exist_ok=True)
    pq.write_table(pa.table({"__row_id": pa.array(rid, pa.int64()), "fold": pa.array(fold, pa.int8())}), tmp, compression="zstd")
    raw = open(tmp, "rb").read(); bsha = hashlib.sha256(raw).hexdigest()
    h = hashlib.sha256(b"ensemble.split/1\n"); a = np.empty(len(rid), dtype=[("r", "<i8"), ("f", "i1")]); a["r"], a["f"] = rid, fold
    h.update(a.tobytes()); ssha = h.hexdigest()
    key = f"blobs/{bsha[:2]}/{bsha}.parquet"; os.makedirs(os.path.join(D, "lake", "blobs", bsha[:2]), exist_ok=True)
    os.replace(tmp, os.path.join(D, "lake", key))
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    con.execute("INSERT OR IGNORE INTO blobs (sha256, key, kind, bytes, n_rows, state, created_at) VALUES (?, ?, 'split', ?, ?, 'live', ?)",
                (bsha, key, len(raw), len(rid), now))
    con.execute("DROP TRIGGER splits_ro_u")
    con.execute("UPDATE splits SET blob_sha256 = ?, split_sha256 = ?, n_holdout = ? WHERE id = ?", (bsha, ssha, int((fold == -1).sum()), row["id"]))
    con.execute("CREATE TRIGGER splits_ro_u BEFORE UPDATE ON splits BEGIN SELECT RAISE(ABORT, 'splits are immutable'); END")
    print(f"forged {row['id']} in place: holdout {row['n_holdout']} -> {int((fold == -1).sum())}, split_sha256 {ssha[:12]}")
    if not FULL:
        con.commit(); checks(v1); checks(ids["child"]); sys.exit(0)
    au = con.execute("SELECT id, details FROM audit_log WHERE action = 'split_created' AND details LIKE ?", (f'%{row["id"]}%',)).fetchone()
    d = json.loads(au["details"]); d.update(split_sha256=ssha, blob_sha256=bsha)
    con.execute("DROP TRIGGER audit_ro_u")
    con.execute("UPDATE audit_log SET details = ? WHERE id = ?", (json.dumps(d), au["id"]))
    con.execute("CREATE TRIGGER audit_ro_u BEFORE UPDATE ON audit_log BEGIN SELECT RAISE(ABORT, 'audit is append-only'); END")
    print(f"rewrote audit #{au['id']} (split_created) to the forged hashes")
    con.commit()
    try:
        n = con.execute("DELETE FROM run_inputs WHERE split_id = ?", (row["id"],)).rowcount; con.commit()
        print(f"(a) DELETE FROM run_inputs WHERE split_id = {row['id']}: deleted {n} row(s)")
    except sqlite3.DatabaseError as e:
        con.rollback(); print(f"(a) DELETE FROM run_inputs → refused: {e}")
        checks(v1); st, r = run_on(v1, "fx1-after-forge-a")
        con.execute("DROP TRIGGER ri_ro_d")
        n = con.execute("DELETE FROM run_inputs WHERE split_id = ?", (row["id"],)).rowcount
        con.execute("CREATE TRIGGER ri_ro_d BEFORE DELETE ON run_inputs BEGIN SELECT RAISE(ABORT, 'run inputs are immutable'); END")
        con.commit(); print(f"(b) with ri_ro_d dropped by hand: deleted {n} run_inputs row(s)")
    checks(v1); checks(ids["child"])
    st, r = run_on(v1, "fx1-after-forge")
    if st == 201:
        x = wait_run(r["id"]); print("  run on the forged split:", r["id"], x["status"])
    st, au = c("GET", "/api/teams/tm_default/audit/verify", show=300)
elif what == "restore":
    ids = json.load(open(st_file)); v1, child = ids["v1"], ids["child"]
    st, off = c("POST", f"/api/datasets/{v1}/restore-splits", {"by": "fx1"}, show=500)
    o = next(x for x in off.get("splits", off.get("offers", [])) if x.get("offer")) if isinstance(off, dict) else None
    st, res = c("POST", f"/api/datasets/{v1}/restore-splits", {"by": "fx1", "reason": "F-X1 M2: put back r1's split",
                "split_key": o["split_key"], "blob_sha256": o["offer"]["blob_sha256"]}, show=600)
    checks(v1); checks(child)
    st, r = run_on(child, "fx1-child-after-restore")
    if st == 201:
        x = wait_run(r["id"]); print("  run on the child:", r["id"], x["status"])
