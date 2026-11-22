"""P24: verify-all re-hashes legacy_json blobs (the F-X2 note), so GC arming covers them. On a stopped chain's data dir
(local store): put a DL4-style split JSON as a live `legacy_json` blob, then the caller runs verify-all through the API
(ok, checked 1); `tamper` rewrites the stored bytes and verify-all fails on it.
usage: legacy_json.py <data dir> put|tamper"""
import hashlib
import json
import logging
import os
import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "backend"))
logging.disable(logging.WARNING)
from app import config  # noqa: E402
from app.catalog import schema as S  # noqa: E402
from app.db import Database  # noqa: E402
from app.lake.store import open_store  # noqa: E402

settings = config.Settings(data_dir=Path(sys.argv[1]))
db, st = Database(settings.db_path), open_store(settings)
if sys.argv[2] == "put":
    f = settings.lake_dir / "tmp" / "splits.json"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(json.dumps({"format": "dl4", "folds": [0, 1, 2, -1]}).encode())
    sha = hashlib.sha256(f.read_bytes()).hexdigest()
    key = st.put_file(f, sha, "json")
    with db.connect() as con:
        con.execute("INSERT INTO blobs (sha256, key, kind, bytes, state, created_at) VALUES (?, ?, 'legacy_json', ?, 'live', ?)",
                    (sha, key, f.stat().st_size, S.now()))
        con.execute("INSERT INTO blob_refs (blob_sha256, owner_kind, owner_id, role, team_id, created_at) VALUES (?, 'run', 'run_demo',"
                    " 'legacy_json', 'tm_default', ?)", (sha, S.now()))
    print("put", key)
else:
    with db.connect() as con:
        key = con.execute("SELECT key FROM blobs WHERE kind = 'legacy_json' ORDER BY created_at DESC LIMIT 1").fetchone()[0]
    p = st.local_copy(key)
    os.chmod(p, stat.S_IRUSR | stat.S_IWUSR)
    p.write_bytes(b'{"format": "dl4", "folds": [0, 0, 0, -1]}')
    print("tampered", key)
