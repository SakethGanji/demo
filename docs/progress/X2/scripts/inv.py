"""X2: inventory of a data dir: every version (storage, state, root, v, op, rows), its folder's files, split_files rows."""
import json, sqlite3, sys
from pathlib import Path
d = Path(sys.argv[1]); con = sqlite3.connect(d / "ensemble.sqlite3"); con.row_factory = sqlite3.Row
cols = [r[1] for r in con.execute("PRAGMA table_info(datasets)")]
sel = ", ".join(c for c in ["id", "root_id", "parent_id", "version_no", "name", "state", "storage", "op", "n_rows", "sha256", "legacy_sha256", "splits_sha256"] if c in cols)
sf = {}
try:
    for r in con.execute("SELECT dataset_id, file, sha256 FROM split_files"):
        sf.setdefault(r[0], []).append(r[1])
except sqlite3.OperationalError:
    pass
for r in con.execute(f"SELECT {sel} FROM datasets ORDER BY COALESCE(root_id, id), version_no"):
    r = dict(r); f = d / "datasets" / r["id"]
    files = sorted(str(p.relative_to(f)) for p in f.rglob("*") if p.is_file()) if f.is_dir() else ["(no folder)"]
    print(f"{r['id']:<22} root={str(r.get('root_id'))[:22]:<22} v={r.get('version_no')} st={r.get('state')} sto={r.get('storage')} op={r.get('op')} rows={r.get('n_rows')} name={r.get('name')!r}")
    print("     files:", ", ".join(files)[:600]); 
    if r["id"] in sf: print("     split_files:", sf[r["id"]])
