"""P24: the bucket side of the S3 chain. Every `purged` blob has no object version left in the bucket (GC and purge
delete all versions, with the governance bypass); every `live` blob is still there and still under Object Lock.
usage: s3check.py <data dir>   (ENSEMBLE_BLOB_STORE=s3://… and the MinIO credentials in the environment)"""
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "backend"))
logging.disable(logging.WARNING)
from app import config  # noqa: E402
from app.db import Database  # noqa: E402
from app.lake.store import open_store  # noqa: E402

settings = config.Settings(data_dir=Path(sys.argv[1]))
db, st = Database(settings.db_path), open_store(settings)
s3, bucket = st.client, st.bucket
lock = s3.get_object_lock_configuration(Bucket=bucket)["ObjectLockConfiguration"]
print("bucket", bucket, "| object lock:", lock.get("ObjectLockEnabled"))
with db.connect() as con:
    rows = [dict(r) for r in con.execute("SELECT sha256, key, state, purge_reason FROM blobs ORDER BY state, sha256")]
bad = []
by = {"purged": [0, 0], "live": [0, 0]}
for r in rows:
    if r["state"] not in by:
        continue
    k = st._k(r["key"]) if hasattr(st, "_k") else r["key"]
    vs = s3.list_object_versions(Bucket=bucket, Prefix=k).get("Versions", [])
    vs = [v for v in vs if v["Key"] == k]
    if r["state"] == "purged":
        by["purged"][0] += 1
        if vs:
            bad.append(("purged but still stored", r["key"], len(vs)))
        else:
            by["purged"][1] += 1
    else:
        by["live"][0] += 1
        if not vs:
            bad.append(("live but missing", r["key"]))
            continue
        ret = s3.get_object_retention(Bucket=bucket, Key=k).get("Retention", {})
        if ret.get("Mode") == "GOVERNANCE":
            by["live"][1] += 1
print("purged blobs with no version left:", f"{by['purged'][1]}/{by['purged'][0]}",
      "| live blobs present under GOVERNANCE retention:", f"{by['live'][1]}/{by['live'][0]}")
print("problems:", bad or "none")
