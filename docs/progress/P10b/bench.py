"""P10b throughput against the configured bucket, through S3BlobStore: put (single PUT and multipart), get (streamed from
the bucket), cache fill (get + sha256 + write) and cache hit (stat check), for blobs of the given sizes in MB.
usage: bench.py <scratch dir> <MB> [<MB> …]. The blobs are scratch (not catalogued) and deleted after (governance bypass)."""
import hashlib
import json
import logging
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "backend"))
logging.disable(logging.WARNING)
from app import config  # noqa: E402
from app.lake.s3store import S3BlobStore  # noqa: E402

work = Path(sys.argv[1]); work.mkdir(parents=True, exist_ok=True)
st = S3BlobStore(config.s3_config(), scratch_dir=work / "scratch", cache_dir=work / "bench-cache")
out = []
for mb in map(int, sys.argv[2:]):
    for mode in ("single", "multipart"):
        f = work / f"bench-{mb}.bin"
        f.write_bytes(os.urandom(mb * 1024 * 1024))
        sha = hashlib.sha256(f.read_bytes()).hexdigest()
        st.multipart_bytes = (mb * 1024 * 1024 - 1) if mode == "multipart" else 1 << 40
        t = time.monotonic(); k = st.put_file(f, sha, "bin"); put = time.monotonic() - t
        t = time.monotonic()
        with st.open(k) as r:
            while r.read(8 << 20):
                pass
        get = time.monotonic() - t
        t = time.monotonic(); st.local_copy(k); fill = time.monotonic() - t
        t = time.monotonic()
        for _ in range(100):
            st.local_copy(k)
        hit = (time.monotonic() - t) / 100
        t = time.monotonic(); st.put_file(f, sha, "bin"); again = time.monotonic() - t
        row = {"mb": mb, "mode": mode, "put_mb_s": round(mb * 1.048576 / put, 1), "get_mb_s": round(mb * 1.048576 / get, 1),
               "cache_fill_mb_s": round(mb * 1.048576 / fill, 1), "cache_hit_ms": round(hit * 1000, 3),
               "put_existing_ms": round(again * 1000, 1)}
        print(json.dumps(row), flush=True); out.append(row)
        st.delete(k, bypass_governance=True); f.unlink()
print(json.dumps({"cache": st.cache.info()["stats"]}))

# LRU eviction: a 50 MB cap and no pin window; five 16 MB blobs read in turn, the first one touched again before the 4th
st.cache.max_bytes, st.cache.pin_s = 50 * 1024 * 1024, 0
keys = []
for i in range(5):
    f = work / f"lru-{i}.bin"
    f.write_bytes(os.urandom(16 * 1024 * 1024))
    keys.append(st.put_file(f, hashlib.sha256(f.read_bytes()).hexdigest(), "bin")); f.unlink()
    st.local_copy(keys[-1])
    if i == 2:
        time.sleep(0.05); st.local_copy(keys[0])  # key 0 becomes the most recently used
    time.sleep(0.05)
info = st.cache.info()
print(json.dumps({"lru": {"cap_mb": 50, "entries": info["entries"], "bytes": info["bytes"],
                          "evictions": info["stats"]["evictions"],
                          "cached": [i for i, k in enumerate(keys) if st.cache.path(k).exists()]}}))
for k in keys:
    st.delete(k, bypass_governance=True)
