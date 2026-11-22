"""frame_of.py <db> <lake dir> <frame id> [cols...]: the frame's cache blob as pandas (null counts / describe)."""
import json, sqlite3, sys
from pathlib import Path
import pandas as pd
db, lake, fid = sys.argv[1:4]
con = sqlite3.connect(db) if not db.startswith("postgres") else __import__("psycopg").connect(db)
sha = con.execute("SELECT cache_blob FROM join_frames WHERE id = ?" if not db.startswith("postgres") else "SELECT cache_blob FROM join_frames WHERE id = %s", (fid,)).fetchone()[0]
p = next(Path(lake).rglob(f"{sha}.parquet"))
df = pd.read_parquet(p)
cols = sys.argv[4:] or [c for c in df.columns if "__" in c and c != "__row_id"]
for c in cols:
    print(f"   {c}: non-null {df[c].notna().sum()} of {len(df)}, sum {df[c].sum() if df[c].dtype.kind in 'if' else '-'}")
