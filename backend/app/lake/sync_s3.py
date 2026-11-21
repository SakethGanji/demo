"""P10b: copy an existing local lake into the S3 / MinIO bucket, verify every sha, resumable.

    python -m app.lake.sync_s3 [--from <local lake dir>] [--dry-run] [--deep-verify] [--by <admin>]

The destination is the configured S3 store (ENSEMBLE_S3_* / ENSEMBLE_BLOB_STORE=s3://bucket/prefix; credentials from
the AWS chain). The source is `Settings.lake_dir` unless `--from` is given. Also `POST /api/admin/lake/sync-s3`.

For every key under the local `blobs/`:
1. the local file is re-hashed; a file whose bytes do not hash to its name is **not copied** (an integrity finding);
2. a key already in the bucket is checked (length and `x-amz-meta-sha256`; with `--deep-verify` re-hashed in full) and
   skipped, so a re-run after a crash only copies what is missing (resumable; progress in `catalog_meta sync_s3:*`);
3. otherwise it is put write-once with its sha256 checksum (S3BlobStore.put_file), which HEAD-checks it after the put.
Then every row of `blobs` (states pending / live) is checked in the bucket: present, and its bytes re-hashed from the
bucket (streamed). Rows missing from both are listed. The local lake is not changed. Switch the deployment with
ENSEMBLE_BLOB_STORE=s3 once `ok` is true; keep the local lake until a verify-all against S3 has passed.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

from . import manifest as M
from .store import LocalBlobStore, parse_key


def _meta(db, key: str, value: Any) -> None:
    with db.connect() as con:
        con.execute("INSERT INTO catalog_meta (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                    (key, json.dumps(value, default=str)))


def sync(db, src: LocalBlobStore, dst, *, dry_run: bool = False, deep_verify: bool = False, by: str = "system",
         progress_every: int = 200) -> dict[str, Any]:
    t0 = time.monotonic()
    copied, existing, bad_local, failed = [], 0, [], []
    bytes_copied, put_s = 0, 0.0
    keys = [(k, n) for k, n, _ in src.list("blobs") if not k.rsplit("/", 1)[-1].startswith(".")]
    with db.connect() as con:
        rows = {r[0]: (r[1], r[2]) for r in con.execute("SELECT key, sha256, state FROM blobs")}
    if not dry_run:
        _meta(db, "sync_s3:progress", {"state": "running", "by": by, "dst": dst.root, "total": len(keys), "done": 0,
                                       "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
    for i, (key, size) in enumerate(keys):
        parsed = parse_key(key)
        if parsed is None:
            bad_local.append({"key": key, "finding": "not a blob key"})
            continue
        sha, ext = parsed
        try:
            if dst.exists(key):
                h = dst._head(key)
                meta = (h.get("Metadata") or {}).get("sha256")
                ok = int(h["ContentLength"]) == size and meta in (None, sha)
                if ok and deep_verify:
                    with dst.open(key) as f:
                        ok = M.file_sha256(f)[0] == sha
                if not ok:
                    failed.append({"key": key, "error": "in the bucket with other bytes (not overwritten: write-once)"})
                else:
                    existing += 1
                continue
            with src.open(key) as f:
                now = M.file_sha256(f)[0]
            if now != sha:
                bad_local.append({"key": key, "finding": "local bytes do not hash to the name", "sha256_now": now})
                continue
            if dry_run:
                copied.append(key)
                bytes_copied += size
                continue
            t1 = time.monotonic()
            dst.put_file(src.local_copy(key), sha, ext)
            put_s += time.monotonic() - t1
            copied.append(key)
            bytes_copied += size
        except Exception as e:  # noqa: BLE001 - reported per key; a re-run picks it up
            failed.append({"key": key, "error": f"{type(e).__name__}: {e}"})
        if not dry_run and (i + 1) % progress_every == 0:
            _meta(db, "sync_s3:progress", {"state": "running", "by": by, "dst": dst.root, "total": len(keys), "done": i + 1})
    # every catalogued blob must now be in the bucket, with its bytes
    checked, missing, mismatch = 0, [], []
    if not dry_run:
        for key, (sha, state) in rows.items():
            if state not in ("pending", "live"):
                continue
            checked += 1
            try:
                with dst.open(key) as f:
                    now = M.file_sha256(f)[0]
            except FileNotFoundError:
                missing.append({"key": key, "state": state, "local": src.exists(key)})
                continue
            if now != sha:
                mismatch.append({"key": key, "sha256_now": now})
    out = {"dry_run": dry_run, "src": src.root, "dst": dst.root, "local_objects": len(keys), "copied": len(copied),
           "copied_bytes": bytes_copied, "already_in_bucket": existing,
           "put_mb_s": round(bytes_copied / 1e6 / put_s, 1) if put_s else None,
           "integrity_local": bad_local, "failed": failed, "verified_in_bucket": checked,
           "missing_in_bucket": missing, "mismatch_in_bucket": mismatch, "seconds": round(time.monotonic() - t0, 2)}
    out["ok"] = not (failed or missing or mismatch) and not dry_run
    if dry_run:
        out["would_copy"] = copied[:50]
    else:
        _meta(db, "sync_s3:progress", {"state": "done", "by": by, "dst": dst.root, "total": len(keys), "done": len(keys),
                                       "ok": out["ok"], "copied": len(copied), "seconds": out["seconds"]})
        from ..catalog import schema as S

        with db.connect() as con:
            S.audit(con, by, "lake_synced_to_s3", {k: v for k, v in out.items() if k not in ("integrity_local", "failed",
                    "missing_in_bucket", "mismatch_in_bucket")} | {"n_integrity_local": len(bad_local),
                    "n_failed": len(failed), "n_missing": len(missing), "n_mismatch": len(mismatch)})
    return out


def s3_destination(settings):
    from .. import config
    from .s3store import S3BlobStore

    cfg = config.s3_config()
    if not cfg["bucket"]:
        raise SystemExit("set ENSEMBLE_S3_BUCKET (or ENSEMBLE_BLOB_STORE=s3://bucket/prefix)")
    return S3BlobStore(cfg, scratch_dir=settings.lake_dir, cache_dir=config.cache_dir(settings))


def main(argv: list[str] | None = None) -> int:
    from .. import config
    from ..db import Database

    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--from", dest="src", help="the local lake directory (default: Settings.lake_dir)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--deep-verify", action="store_true", help="re-hash objects already in the bucket")
    ap.add_argument("--by", default="system:sync_s3")
    a = ap.parse_args(argv)
    settings = config.load_settings()
    src = LocalBlobStore(Path(a.src) if a.src else settings.lake_dir)
    out = sync(Database(settings.db_path), src, s3_destination(settings), dry_run=a.dry_run, deep_verify=a.deep_verify, by=a.by)
    print(json.dumps(out, indent=1, default=str))
    return 0 if (out["ok"] or a.dry_run) else 1


if __name__ == "__main__":
    sys.exit(main())
