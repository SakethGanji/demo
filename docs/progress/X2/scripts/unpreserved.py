"""X2: every file under datasets/<id>.migrated/ that cleanup would delete, and whether its bytes survive elsewhere:
a blob (any kind, by sha256), or a byte-identical file in the fresh <id>/ folder. usage: unpreserved.py <data dir>"""
import hashlib, sqlite3, sys
from pathlib import Path
D = Path(sys.argv[1]); con = sqlite3.connect(D / "ensemble.sqlite3")
blobs = {r[0] for r in con.execute("SELECT sha256 FROM blobs WHERE state = 'live'")}
lost, kept = [], 0
for f in sorted((D / "datasets").glob("*.migrated")):
    fresh = D / "datasets" / f.name[:-len(".migrated")]
    for p in sorted(x for x in f.rglob("*") if x.is_file()):
        rel = p.relative_to(f); sha = hashlib.sha256(p.read_bytes()).hexdigest()
        q = fresh / rel
        if sha in blobs or (q.exists() and hashlib.sha256(q.read_bytes()).hexdigest() == sha):
            kept += 1
        else:
            lost.append(f"{f.name}/{rel} ({p.stat().st_size} B)")
print("files preserved (a live blob or the same bytes in the fresh folder):", kept)
print("files that cleanup deletes with no other copy:", len(lost))
for x in lost:
    print("  ", x)
