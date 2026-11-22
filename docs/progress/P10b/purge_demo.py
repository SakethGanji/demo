"""P10b: store.delete_blob (the one audited delete, for GC and purge in P24) against the Object-Locked bucket: a scratch
blob is put and catalogued as 'pending', deleted without the governance bypass (refused, audited), then with it (all
versions removed, audited). usage: purge_demo.py <data dir> (ENSEMBLE_BLOB_STORE=s3 environment)."""
import hashlib
import json
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "backend"))
logging.disable(logging.WARNING)
from app import config  # noqa: E402
from app.catalog import schema as S  # noqa: E402
from app.db import Database  # noqa: E402
from app.lake.store import delete_blob, open_store  # noqa: E402

settings = config.Settings(data_dir=Path(sys.argv[1]))
db, st = Database(settings.db_path), open_store(settings)
f = settings.lake_dir / "tmp" / "purge-demo.json"
f.parent.mkdir(parents=True, exist_ok=True)
f.write_bytes(json.dumps({"purge_demo": os.urandom(8).hex()}).encode())
sha = hashlib.sha256(f.read_bytes()).hexdigest()
key = st.put_file(f, sha, "json")
with db.connect() as con:
    con.execute("INSERT INTO blobs (sha256, key, kind, bytes, state, created_at) VALUES (?, ?, 'source', ?, 'pending', ?)",
                (sha, key, f.stat().st_size, S.now()))
print("put", key, "| retention", st.retention(key))
try:
    delete_blob(db, st, sha, by="Ada", reason="P10b demo: purge without the bypass")
    print("DELETED without bypass (unexpected)")
except Exception as e:  # noqa: BLE001
    print("without bypass:", type(e).__name__, str(e)[:160])
print("still there:", st.exists(key))
out = delete_blob(db, st, sha, by="Ada", reason="P10b demo: purge with the governance bypass", bypass_governance=True)
print("with bypass:", {k: out[k] for k in ("key", "versions_removed", "bypass_governance")}, "| still there:", st.exists(key))
with db.connect() as con:
    for r in con.execute("SELECT id, actor, action, comment, json_extract(details, '$.bypass_governance'), row_hash FROM audit_log"
                         " WHERE object_id = ? ORDER BY id", (sha,)):
        print("audit", r[0], r[1], r[2], "|", r[3], "| bypass", r[4], "| row_hash", r[5][:12] + "…")
    con.execute("DELETE FROM blobs WHERE sha256 = ?", (sha,))  # scratch row
f.unlink()
