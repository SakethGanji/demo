"""P10b tamper helper, as someone with direct access would act: tamper.py <cmd> <data dir> [args].
  cache-flip <key>            flip one byte of the cached copy (chmod u+w first, as root could)
  cache-flip-keep-mtime <key> the same, then put the old mtime back (defeats the stat check)
  bucket-put <key>            PUT other bytes over the key in the bucket (no If-None-Match, as a writer with bucket rights)
  bucket-versions <key>       the object's versions, their retention and sha256
  bucket-undo <key>           delete the newest version (the tamper; it has no retention) → the locked original is current
  bucket-delete <key>         delete the original without bypass (Object Lock refuses), then show it is still there
  race <n>                    n threads put the same new blob at once through S3BlobStore → versions, errors
Credentials and the endpoint come from the environment (AWS_*, ENSEMBLE_S3_*)."""
import hashlib
import logging
import os
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "backend"))
from app import config  # noqa: E402
from app.lake.s3store import S3BlobStore  # noqa: E402

logging.disable(logging.WARNING)
cmd, data = sys.argv[1], Path(sys.argv[2])
settings = config.Settings(data_dir=data)
st = S3BlobStore(config.s3_config(), scratch_dir=settings.lake_dir, cache_dir=config.cache_dir(settings))
c, B = st.client, st.bucket


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


if cmd.startswith("cache-flip"):
    p = st.cache.path(sys.argv[3])
    s0 = p.stat()
    os.chmod(p, 0o644)
    b = bytearray(p.read_bytes()); b[len(b) // 2] ^= 0xFF; p.write_bytes(bytes(b))
    os.chmod(p, 0o444)
    if cmd == "cache-flip-keep-mtime":
        os.utime(p, ns=(s0.st_atime_ns, s0.st_mtime_ns))
    print("cached copy", p.name[:16] + "…", "now hashes to", sha(p.read_bytes())[:16] + "…", "| mtime kept" if cmd.endswith("mtime") else "")
elif cmd == "bucket-put":
    k = st._k(sys.argv[3])
    orig = c.get_object(Bucket=B, Key=k)["Body"].read()
    bad = bytearray(orig); bad[len(bad) // 2] ^= 0xFF
    r = c.put_object(Bucket=B, Key=k, Body=bytes(bad))
    print("PUT without If-None-Match → HTTP", r["ResponseMetadata"]["HTTPStatusCode"], "version", r.get("VersionId"), "| new bytes sha", sha(bytes(bad))[:16] + "…")
elif cmd == "bucket-versions":
    k = st._k(sys.argv[3])
    for v in c.list_object_versions(Bucket=B, Prefix=k).get("Versions", []):
        body = c.get_object(Bucket=B, Key=k, VersionId=v["VersionId"])["Body"].read()
        try:
            ret = c.get_object_retention(Bucket=B, Key=k, VersionId=v["VersionId"])["Retention"]
            ret = f"{ret['Mode']} until {ret['RetainUntilDate']:%Y-%m-%d}"
        except Exception:  # noqa: BLE001
            ret = "no retention"
        print("  version", v["VersionId"][:8], "latest" if v["IsLatest"] else "      ", "|", ret, "| sha", sha(body)[:16] + "…")
elif cmd == "bucket-undo":
    k = st._k(sys.argv[3])
    v = [v for v in c.list_object_versions(Bucket=B, Prefix=k)["Versions"] if v["IsLatest"]][0]
    c.delete_object(Bucket=B, Key=k, VersionId=v["VersionId"])
    print("deleted the tampering version", v["VersionId"][:8])
elif cmd == "bucket-delete":
    k = st._k(sys.argv[3])
    v = [v for v in c.list_object_versions(Bucket=B, Prefix=k)["Versions"] if not v["IsLatest"]
         or len(c.list_object_versions(Bucket=B, Prefix=k)["Versions"]) == 1][-1]  # the oldest: the locked original
    print("deleting version", v["VersionId"][:8], "(the original) without x-amz-bypass-governance-retention")
    try:
        c.delete_object(Bucket=B, Key=k, VersionId=v["VersionId"])
        print("DELETED (Object Lock did not hold!)")
    except Exception as e:  # noqa: BLE001
        print("delete of the locked version refused:", str(e)[:110])
    print("still there:", st.exists(sys.argv[3]))
elif cmd == "race":
    n = int(sys.argv[3])
    f = data / "race.bin"
    f.write_bytes(os.urandom(8_000_000))
    s = sha(f.read_bytes())
    errs, keys = [], []

    def put():
        try:
            keys.append(st.put_file(f, s, "bin"))
        except Exception as e:  # noqa: BLE001
            errs.append(repr(e))
    ts = [threading.Thread(target=put) for _ in range(n)]
    [t.start() for t in ts]; [t.join() for t in ts]
    k = st._k(keys[0]) if keys else None
    vs = c.list_object_versions(Bucket=B, Prefix=k).get("Versions", []) if k else []
    print(f"{n} concurrent puts of one sha: keys returned {len(keys)} (distinct {len(set(keys))}), errors {errs},",
          f"object versions {len(vs)}, stats puts {st.stats['puts']} existing {st.stats['put_existing']} raced {st.stats['put_raced']}")
    st.delete(keys[0], bypass_governance=True)  # scratch blob: not catalogued
    f.unlink()
