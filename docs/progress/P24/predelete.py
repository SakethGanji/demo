"""P24: a version deleted before P24 (the old decide_delete marked it deleted and kept every blob_ref). On a stopped
chain's data dir: make a scratch dataset through the VersionWriter and mark it deleted the pre-P24 way (state only).
The caller then runs GC dry run → run → dry run → run through the API. usage: predelete.py <data dir>"""
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "backend"))
logging.disable(logging.WARNING)
from app import config  # noqa: E402
from app.catalog import schema as S  # noqa: E402
from app.db import Database  # noqa: E402
from app.lake.store import open_store  # noqa: E402
from app.lake.writer import VersionWriter  # noqa: E402

settings = config.Settings(data_dir=Path(sys.argv[1]))
db, st = Database(settings.db_path), open_store(settings)
w = VersionWriter(db, st, op="seed", by="Ada", name="pre-P24 deleted", reason="P24 demo")
with w:
    w.write("t", "SELECT i::BIGINT AS __row_id, i * 7 AS x FROM range(0, 1000) r(i)")
    out = w.commit(make_default=True)
vid = out["version_id"]
with db.connect() as con:
    con.execute("UPDATE datasets SET state = 'deleted', state_at = ?, state_by = 'Ada' WHERE id = ?", (S.now(), vid))
    refs = [tuple(r) for r in con.execute("SELECT owner_kind, role, blob_sha256 FROM blob_refs WHERE owner_id = ? OR owner_id IN"
                                          " (SELECT id FROM splits WHERE version_id = ?)", (vid, vid))]
print("version", vid, "deleted the pre-P24 way; refs kept:", [(k, r, s[:12]) for k, r, s in refs])
