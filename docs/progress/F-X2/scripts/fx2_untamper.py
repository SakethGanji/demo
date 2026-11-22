"""F-X2: undo X2's outside changes c, e and f from the untampered snapshot (a/b/d: X2's untamper.py): A3's split JSONs
and split_files hashes, L1's split JSON, LR's data.csv and its datasets hashes. usage: fx2_untamper.py <state.json> <snapshot> <data dir>"""
import json, shutil, sqlite3, sys
from pathlib import Path
st = json.loads(Path(sys.argv[1]).read_text()); snap, D = Path(sys.argv[2]), Path(sys.argv[3])
for v in ("A3", "L1"):
    for p in [*(snap / "datasets" / st[v] / "splits").glob("*.json"), snap / "datasets" / st[v] / "splits.json"]:
        if p.exists():
            shutil.copy2(p, D / "datasets" / st[v] / p.relative_to(snap / "datasets" / st[v]))
shutil.copy2(snap / "datasets" / st["LR"] / "data.csv", D / "datasets" / st["LR"] / "data.csv")
a, b = sqlite3.connect(snap / "ensemble.sqlite3"), sqlite3.connect(D / "ensemble.sqlite3")
for f, sha in a.execute("SELECT file, sha256 FROM split_files WHERE dataset_id = ?", (st["A3"],)):
    b.execute("UPDATE split_files SET sha256 = ? WHERE dataset_id = ? AND file = ?", (sha, st["A3"], f))
sha, leg = a.execute("SELECT sha256, legacy_sha256 FROM datasets WHERE id = ?", (st["LR"],)).fetchone()
trig = [r for r in b.execute("SELECT name, sql FROM sqlite_master WHERE type = 'trigger' AND name = 'ds_hash_ro'")]
for n, _ in trig:
    b.execute(f"DROP TRIGGER {n}")
b.execute("UPDATE datasets SET sha256 = ?, legacy_sha256 = ? WHERE id = ?", (sha, leg, st["LR"]))
for _, q in trig:
    b.execute(q)
b.commit()
print("undone: c (A3 splits + split_files), e (L1 split), f (LR data.csv + hashes)")
