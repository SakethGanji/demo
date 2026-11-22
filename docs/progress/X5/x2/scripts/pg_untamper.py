"""X5/x2: X2 untamper.py + F-X2 fx2_untamper.py for a Postgres metadata DB: files from the untampered snapshot
(C1 data.csv, T1/A2/A3/L1 split JSONs, LR data.csv), split_files hashes of A2/A3 and LR's datasets hashes from the
snapshot's SQLite (ds_hash_ro disabled for that one UPDATE, as fx2_untamper drops/re-creates it).
usage: pg_untamper.py <state.json> <snapshot> <data dir> <pg url>"""
import json, shutil, sqlite3, sys
from pathlib import Path
import psycopg
st = json.loads(Path(sys.argv[1]).read_text()); snap, D, url = Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4]
for v in ("C1", "LR"):
    shutil.copy2(snap / "datasets" / st[v] / "data.csv", D / "datasets" / st[v] / "data.csv")
for v in ("T1", "A2", "A3", "L1"):
    for p in [*(snap / "datasets" / st[v] / "splits").glob("*.json"), snap / "datasets" / st[v] / "splits.json"]:
        if p.exists(): shutil.copy2(p, D / "datasets" / st[v] / p.relative_to(snap / "datasets" / st[v]))
a = sqlite3.connect(snap / "ensemble.sqlite3")
with psycopg.connect(url) as b:
    for v in ("A2", "A3"):
        for f, sha in a.execute("SELECT file, sha256 FROM split_files WHERE dataset_id = ?", (st[v],)):
            b.execute("UPDATE split_files SET sha256 = %s WHERE dataset_id = %s AND file = %s", (sha, st[v], f))
    sha, leg = a.execute("SELECT sha256, legacy_sha256 FROM datasets WHERE id = ?", (st["LR"],)).fetchone()
    b.execute("ALTER TABLE datasets DISABLE TRIGGER ds_hash_ro")
    b.execute("UPDATE datasets SET sha256 = %s, legacy_sha256 = %s WHERE id = %s", (sha, leg, st["LR"]))
    b.execute("ALTER TABLE datasets ENABLE TRIGGER ds_hash_ro")
print("undone a-f on Postgres")
