"""X2: undo the outside changes a/b/d (the skipped versions) from the untampered snapshot: data.csv of C1, the split
JSONs of T1 and A2, and A2's split_files hashes. usage: untamper.py <state.json> <snapshot dir> <data dir>"""
import json, shutil, sqlite3, sys
from pathlib import Path
st = json.loads(Path(sys.argv[1]).read_text()); snap, D = Path(sys.argv[2]), Path(sys.argv[3])
shutil.copy2(snap / "datasets" / st["C1"] / "data.csv", D / "datasets" / st["C1"] / "data.csv")
for v in ("T1", "A2"):
    for p in [*(snap / "datasets" / st[v] / "splits").glob("*.json"), snap / "datasets" / st[v] / "splits.json"]:
        shutil.copy2(p, D / "datasets" / st[v] / p.relative_to(snap / "datasets" / st[v]))
a, b = sqlite3.connect(snap / "ensemble.sqlite3"), sqlite3.connect(D / "ensemble.sqlite3")
for f, sha in a.execute("SELECT file, sha256 FROM split_files WHERE dataset_id = ?", (st["A2"],)):
    b.execute("UPDATE split_files SET sha256 = ? WHERE dataset_id = ? AND file = ?", (sha, st["A2"], f))
b.commit(); print("restored C1 data.csv, T1 + A2 split JSONs, A2 split_files hashes")
