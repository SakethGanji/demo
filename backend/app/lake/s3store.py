"""P10b: `S3BlobStore`, the blob store on S3 / MinIO (plan §3.1, §7 risk 10, §11 item 1; B §6.2, B7).

Same keys and contract as `LocalBlobStore` (see store.py); the differences:

- **Write-once puts.** `PutObject` carries `If-None-Match: *` and `x-amz-checksum-sha256` (the server refuses bytes
  whose sha256 differs). An existing key answers 412 and the put is a no-op; two writers racing on one sha get one
  object and no error (a 409 conditional conflict is waited out, then the object is checked). Where the server does
  not honour the condition (`ENSEMBLE_S3_CONDITIONAL=off`, or a 501 answer), the store HEADs first, then PUTs.
  After every put the object is HEADed: its length, its sha256 checksum and its `x-amz-meta-sha256` must be ours.
  A file larger than `ENSEMBLE_S3_MULTIPART_MB` (4,096) goes up as a multipart upload with a SHA256 checksum per part
  and the condition on the completion. A lifecycle rule on the bucket aborts multipart uploads left incomplete after
  1 day (set at start when missing). MinIO refuses that rule (it aborts stale uploads itself, `stale_uploads_expiry`
  24 h), so `abort_stale_uploads()` also sweeps uploads older than a day at start.
- **Object Lock.** When the bucket has Object Lock enabled, every blob is put with GOVERNANCE retention for
  `ENSEMBLE_S3_LOCK_DAYS` (30). Compliance mode is never used (purge would be impossible). `delete(...,
  bypass_governance=True)` removes every version of the key with `x-amz-bypass-governance-retention`: only GC and
  purge (P24) call it, through `store.delete_blob`, which writes the audit row.
- **Reads.** `open()` streams from the bucket (verify, verify-all and GC re-hash what is really stored).
  `local_copy()` goes through the sha-keyed `BlobCache` (blobcache.py). `uri()` is what DuckDB reads:
  - `ENSEMBLE_S3_READ=cache` (the default) gives the cached local path;
  - `httpfs` gives `s3://bucket/prefix/key`, and every DuckDB connection gets the httpfs extension and a session
    secret (duck.py).
- **Scratch.** `tmp_dir()` and `exports/` stay on local disk (`Settings.lake_dir`). `list("tmp")` lists that scratch,
  so GC phase 2 is unchanged.
- **Credentials** come only from the environment or the AWS default chain (`AWS_ACCESS_KEY_ID` /
  `AWS_SECRET_ACCESS_KEY`, `AWS_PROFILE`, an instance role, IRSA). Nothing is stored in the DB or written to disk.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import shutil
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, BinaryIO, Iterator

from .blobcache import BlobCache
from .store import KEY_RE, StoreError, blob_key

log = logging.getLogger("ensemble.lake.s3")

LIFECYCLE_RULE_ID = "ensemble-abort-incomplete-multipart"


class BlobMissing(StoreError, FileNotFoundError):
    pass


class BlobStacked(StoreError):
    """X3 m8: the key's current object holds other bytes (a version stacked on the original). The writer holds the
    right bytes but may not overwrite (If-None-Match; the bucket policy). 409 `blob_stacked` with the fix path:
    `POST /api/admin/lake/blobs/{sha}/restore` (an admin; audited) verifies the newest version whose bytes hash to
    the key and removes the versions stacked above it, so that version is current again."""

    status, code = 409, "blob_stacked"

    def __init__(self, key: str, sha256: str, matching: str | None, stacked: list[dict[str, Any]]) -> None:
        msg = (f"{key} holds other bytes (a newer object version was stacked on it), so this commit can't reuse it. "
               + (f"Version {matching} still has the right bytes: an admin restores it with POST /api/admin/lake/blobs/"
                  f"{sha256}/restore {{\"by\", \"confirm\": \"restore\"}}, then retry."
                  if matching else "No version of it has the right bytes: an admin removes the object's versions with "
                                   f"POST /api/admin/lake/blobs/{sha256}/restore (it re-puts nothing), then retry the commit."))
        super().__init__(msg)
        self.payload = {"code": self.code, "message": msg, "key": key, "sha256": sha256, "matching_version": matching,
                        "stacked_versions": stacked}


class ObjectLocked(StoreError):
    status, code = 409, "object_locked"


def _b64(hexsha: str) -> str:
    return base64.b64encode(bytes.fromhex(hexsha)).decode()


class S3BlobStore:
    def __init__(self, cfg: dict[str, Any], *, scratch_dir: Path, cache_dir: Path, client: Any = None) -> None:
        import boto3
        from botocore.config import Config

        self.cfg = cfg
        self.bucket = cfg["bucket"]
        self.prefix = (cfg.get("prefix") or "").strip("/")
        self.root = f"s3://{self.bucket}/{self.prefix}".rstrip("/")
        self.read_mode = cfg.get("read", "cache")
        self.lock_days = float(cfg.get("lock_days", 30))
        self.multipart_bytes = int(cfg.get("multipart_mb", 4096)) * 1024 * 1024
        self.conditional = cfg.get("conditional", "auto")  # auto | on | off
        self.scratch = Path(scratch_dir).resolve()
        (self.scratch / "tmp").mkdir(parents=True, exist_ok=True)
        self.path = self.scratch  # local scratch only (tmp/, exports/); blobs are in the bucket
        self.cache = BlobCache(cache_dir, max_bytes=int(cfg.get("cache_max_gb", 20) * 1024 ** 3),
                               verify=cfg.get("cache_verify", "stat"), pin_s=float(cfg.get("cache_pin_s", 300)))
        self.client = client or boto3.client(
            "s3", endpoint_url=cfg.get("endpoint") or None, region_name=cfg.get("region") or None,
            config=Config(signature_version="s3v4", retries={"max_attempts": 5, "mode": "standard"},
                          s3={"addressing_style": cfg.get("url_style", "path")},
                          request_checksum_calculation="when_required", response_checksum_validation="when_required"))
        self.stats: dict[str, Any] = {"puts": 0, "put_bytes": 0, "put_seconds": 0.0, "put_existing": 0, "put_raced": 0,
                                      "gets": 0, "get_bytes": 0, "get_seconds": 0.0, "deletes": 0}
        self._slock = threading.Lock()
        self.object_lock = False
        self.versioning = None
        self.setup_notes: list[str] = []
        self.lifecycle_rule = False
        self._setup()

    # -- bucket setup ------------------------------------------------------------------------------------------------

    def _setup(self) -> None:
        from botocore.exceptions import ClientError

        c = self.client
        try:
            c.head_bucket(Bucket=self.bucket)
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code")
            if code in ("404", "NoSuchBucket", "NotFound") and self.cfg.get("create_bucket"):
                kw: dict[str, Any] = {"Bucket": self.bucket, "ObjectLockEnabledForBucket": bool(self.cfg.get("create_locked", True))}
                if self.cfg.get("region") and self.cfg["region"] != "us-east-1":
                    kw["CreateBucketConfiguration"] = {"LocationConstraint": self.cfg["region"]}
                c.create_bucket(**kw)
                self.setup_notes.append("bucket created" + (" with Object Lock" if kw["ObjectLockEnabledForBucket"] else ""))
            else:
                raise StoreError(f"the bucket {self.bucket!r} is not reachable ({code}): {e}") from e
        try:
            r = c.get_object_lock_configuration(Bucket=self.bucket)
            self.object_lock = (r.get("ObjectLockConfiguration") or {}).get("ObjectLockEnabled") == "Enabled"
        except ClientError:
            self.object_lock = False
        try:
            self.versioning = c.get_bucket_versioning(Bucket=self.bucket).get("Status")
        except ClientError:
            self.versioning = None
        # §7 risk 10: abort incomplete multipart uploads after 1 day (merge with the bucket's other rules)
        try:
            try:
                rules = c.get_bucket_lifecycle_configuration(Bucket=self.bucket).get("Rules", [])
            except ClientError as e:
                if e.response.get("Error", {}).get("Code") not in ("NoSuchLifecycleConfiguration",):
                    raise
                rules = []
            if not any(r.get("ID") == LIFECYCLE_RULE_ID for r in rules):
                rules.append({"ID": LIFECYCLE_RULE_ID, "Status": "Enabled", "Filter": {"Prefix": self._k("")},
                              "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": 1}})
                c.put_bucket_lifecycle_configuration(Bucket=self.bucket, LifecycleConfiguration={"Rules": rules})
                self.setup_notes.append("lifecycle rule added: abort incomplete multipart uploads after 1 day")
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code")
            self.lifecycle_rule = False
            if code in ("InvalidArgument", "MalformedXML", "NotImplemented"):  # MinIO: no AbortIncompleteMultipartUpload
                self.setup_notes.append(f"the server refuses the AbortIncompleteMultipartUpload rule ({code}); MinIO "
                                        "aborts stale uploads itself (api stale_uploads_expiry, 24 h by default) and "
                                        "abort_stale_uploads() sweeps them too")
            else:
                self.setup_notes.append(f"lifecycle rule not set ({code}): ask the bucket owner")
            log.warning("S3 lifecycle rule not set: %s", e)
        else:
            self.lifecycle_rule = True
        try:
            n = self.abort_stale_uploads()
            if n:
                self.setup_notes.append(f"aborted {n} multipart upload(s) older than 1 day")
        except ClientError as e:
            self.setup_notes.append(f"stale multipart sweep failed ({e.response.get('Error', {}).get('Code')})")

    def abort_stale_uploads(self, hours: float = 24.0) -> int:
        """Abort multipart uploads under the prefix started more than `hours` ago (§7 risk 10; the lifecycle rule
        does the same where the server supports it). Returns how many were aborted."""
        cut = datetime.now(timezone.utc) - timedelta(hours=hours)
        n = 0
        pag = self.client.get_paginator("list_multipart_uploads")
        for page in pag.paginate(Bucket=self.bucket, Prefix=self._k("")):
            for u in page.get("Uploads", []):
                if u["Initiated"] < cut:
                    self.client.abort_multipart_upload(Bucket=self.bucket, Key=u["Key"], UploadId=u["UploadId"])
                    n += 1
        return n

    # -- keys --------------------------------------------------------------------------------------------------------

    def _k(self, key: str) -> str:
        if key.startswith("/") or ".." in key.split("/"):
            raise StoreError(f"bad key: {key!r}")
        return f"{self.prefix}/{key}" if self.prefix else key

    def _unk(self, k: str) -> str:
        return k[len(self.prefix) + 1:] if self.prefix else k

    # -- write -------------------------------------------------------------------------------------------------------

    def _head(self, key: str, checksum: bool = False) -> dict[str, Any] | None:
        from botocore.exceptions import ClientError

        try:
            kw = {"ChecksumMode": "ENABLED"} if checksum else {}
            return self.client.head_object(Bucket=self.bucket, Key=self._k(key), **kw)
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return None
            raise

    def _lock_kw(self) -> dict[str, Any]:
        if not self.object_lock or self.lock_days <= 0:
            return {}
        until = datetime.now(timezone.utc) + timedelta(days=self.lock_days)
        return {"ObjectLockMode": "GOVERNANCE", "ObjectLockRetainUntilDate": until}

    def put_file(self, local: Path, sha256: str, ext: str) -> str:
        from botocore.exceptions import ClientError

        key = blob_key(sha256, ext)
        local = Path(local)
        size = local.stat().st_size
        # HEAD first when the conditional PUT cannot answer cheaply: no condition support, or a multipart upload (its
        # 412 only comes at the completion, after every part was sent). The condition still guards a race after it.
        if (self.conditional == "off" or size > self.multipart_bytes) and self._head(key) is not None:
            self._count("put_existing")
            return self._check_existing(key, sha256, size)
        t0 = time.monotonic()
        try:
            if size > self.multipart_bytes:
                composite = self._put_multipart(local, key, sha256)
            else:
                composite = None
                with open(local, "rb") as f:
                    self.client.put_object(Bucket=self.bucket, Key=self._k(key), Body=f, ContentLength=size,
                                           ChecksumSHA256=_b64(sha256), Metadata={"sha256": sha256},
                                           **self._cond(), **self._lock_kw())
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code")
            status = e.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            if code == "PreconditionFailed" or status == 412:
                self._count("put_existing")
                return self._check_existing(key, sha256, size)
            if code == "ConditionalRequestConflict" or status == 409:
                self._count("put_raced")
                return self._wait_existing(key, sha256, size)
            if code in ("NotImplemented",) and self.conditional == "auto":
                self.conditional = "off"
                log.warning("S3 server does not support If-None-Match: falling back to HEAD then PUT")
                return self.put_file(local, sha256, ext)
            if code in ("BadDigest", "XAmzContentSHA256Mismatch", "XAmzContentChecksumMismatch", "InvalidDigest"):
                raise StoreError(f"the server refused {key}: its bytes do not hash to {sha256} ({code})") from e
            raise
        dt = time.monotonic() - t0
        with self._slock:
            self.stats["puts"] += 1
            self.stats["put_bytes"] += size
            self.stats["put_seconds"] += dt
        self._verify_put(key, sha256, size, composite)
        return key

    def _cond(self) -> dict[str, Any]:
        return {"IfNoneMatch": "*"} if self.conditional != "off" else {}

    def _put_multipart(self, local: Path, key: str, sha256: str) -> str:
        part_size = max(8 * 1024 * 1024, min(self.multipart_bytes, 512 * 1024 * 1024))
        c = self.client
        up = c.create_multipart_upload(Bucket=self.bucket, Key=self._k(key), ChecksumAlgorithm="SHA256",
                                       Metadata={"sha256": sha256}, **self._lock_kw())
        uid, parts, digests = up["UploadId"], [], []
        try:
            with open(local, "rb") as f:
                n = 1
                while b := f.read(part_size):
                    d = hashlib.sha256(b).digest()
                    digests.append(d)
                    r = c.upload_part(Bucket=self.bucket, Key=self._k(key), UploadId=uid, PartNumber=n, Body=b,
                                      ChecksumSHA256=base64.b64encode(d).decode())
                    parts.append({"PartNumber": n, "ETag": r["ETag"], "ChecksumSHA256": base64.b64encode(d).decode()})
                    n += 1
            c.complete_multipart_upload(Bucket=self.bucket, Key=self._k(key), UploadId=uid,
                                        MultipartUpload={"Parts": parts}, **self._cond())
        except Exception:
            try:
                c.abort_multipart_upload(Bucket=self.bucket, Key=self._k(key), UploadId=uid)
            except Exception:  # noqa: BLE001 - the lifecycle rule aborts it after a day anyway
                pass
            raise
        return base64.b64encode(hashlib.sha256(b"".join(digests)).digest()).decode() + f"-{len(digests)}"

    def _verify_put(self, key: str, sha256: str, size: int, composite: str | None) -> None:
        h = self._head(key, checksum=True)
        if h is None:
            raise StoreError(f"{key} is not in the bucket after its put")
        problems = []
        if int(h.get("ContentLength", -1)) != size:
            problems.append(f"length {h.get('ContentLength')} != {size}")
        cs = h.get("ChecksumSHA256")
        want = composite or _b64(sha256)
        if cs and cs != want:
            problems.append(f"checksum {cs} != {want}")
        meta = (h.get("Metadata") or {}).get("sha256")
        if meta and meta != sha256:
            problems.append(f"x-amz-meta-sha256 {meta} != {sha256}")
        if problems:
            raise StoreError(f"{key} does not match what was put: " + "; ".join(problems))

    def _check_existing(self, key: str, sha256: str, size: int) -> str:
        h = self._head(key)
        if h is None:
            raise StoreError(f"{key}: the put was refused as existing but the object is not there")
        meta = (h.get("Metadata") or {}).get("sha256")
        if int(h.get("ContentLength", -1)) != size or (meta and meta != sha256):
            matching, stacked = self._stack(key, sha256, size)
            raise BlobStacked(key, sha256, matching, stacked)
        return key

    def _versions(self, key: str) -> list[dict[str, Any]]:
        """Every object version of the key, newest first."""
        k = self._k(key)
        out: list[dict[str, Any]] = []
        if self.versioning not in ("Enabled", "Suspended"):
            return out
        for page in self.client.get_paginator("list_object_versions").paginate(Bucket=self.bucket, Prefix=k):
            out += [v for v in page.get("Versions", []) if v["Key"] == k]
        return sorted(out, key=lambda v: (v["LastModified"], v.get("IsLatest", False)), reverse=True)

    def _version_sha(self, key: str, version_id: str) -> str:
        h = hashlib.sha256()
        body = self.client.get_object(Bucket=self.bucket, Key=self._k(key), VersionId=version_id)["Body"]
        try:
            while b := body.read(8 * 1024 * 1024):
                h.update(b)
        finally:
            body.close()
        return h.hexdigest()

    def _stack(self, key: str, sha256: str, size: int) -> tuple[str | None, list[dict[str, Any]]]:
        """(the newest version whose bytes hash to sha256, the versions above it)."""
        stacked: list[dict[str, Any]] = []
        for v in self._versions(key):
            if int(v.get("Size", -1)) == size and self._version_sha(key, v["VersionId"]) == sha256:
                return v["VersionId"], stacked
            stacked.append({"version": v["VersionId"], "bytes": int(v.get("Size", -1)), "modified": str(v.get("LastModified"))})
        return None, stacked

    def restore_stacked(self, key: str, sha256: str, *, bypass_governance: bool = True) -> dict[str, Any]:
        """X3 m8, the fix path for `BlobStacked`: verify the newest version that hashes to `sha256` and remove every
        version above it (under Object Lock with the governance bypass). Without a matching version every version is
        removed (the next commit of those bytes puts them again). The read-cache file is dropped. Call it through the
        admin route, which audits it."""
        from botocore.exceptions import ClientError

        h = self._head(key)
        size = int(h["ContentLength"]) if h else -1
        cur_meta = (h.get("Metadata") or {}).get("sha256") if h else None
        vs = self._versions(key)
        matching, above = None, []
        for v in vs:
            if self._version_sha(key, v["VersionId"]) == sha256:
                matching = v["VersionId"]
                break
            above.append(v)
        if matching is None and not vs and h is not None:
            raise StoreError(f"{key}: the bucket keeps no versions, so the original bytes can't be restored")
        removed, refused = [], []
        for v in above:
            try:
                self.client.delete_object(Bucket=self.bucket, Key=self._k(key), VersionId=v["VersionId"],
                                          **({"BypassGovernanceRetention": True} if bypass_governance else {}))
                removed.append({"version": v["VersionId"], "bytes": int(v.get("Size", -1)), "modified": str(v.get("LastModified"))})
            except ClientError as e:
                refused.append({"version": v["VersionId"], "code": e.response.get("Error", {}).get("Code")})
        p = self.cache.path(key)
        if p.exists():
            self.cache._drop(p)
        if refused:
            raise ObjectLocked(f"{key}: {len(refused)} stacked version(s) could not be removed: {refused}")
        return {"key": key, "sha256": sha256, "matching_version": matching, "removed_versions": removed,
                "current_before": {"bytes": size, "meta_sha256": cur_meta}, "restored": matching is not None}

    def _wait_existing(self, key: str, sha256: str, size: int) -> str:
        for i in range(40):
            if self._head(key) is not None:
                return self._check_existing(key, sha256, size)
            time.sleep(min(0.05 * (i + 1), 0.5))
        raise StoreError(f"{key}: a concurrent put of the same blob did not finish")

    def _count(self, k: str) -> None:
        with self._slock:
            self.stats[k] += 1

    # -- read --------------------------------------------------------------------------------------------------------

    def exists(self, key: str) -> bool:
        if not key.startswith("blobs/"):
            return (self.scratch / key).is_file()
        return self._head(key) is not None

    def size(self, key: str) -> int | None:
        h = self._head(key)
        return int(h["ContentLength"]) if h else None

    def open(self, key: str) -> BinaryIO:
        from botocore.exceptions import ClientError

        try:
            r = self.client.get_object(Bucket=self.bucket, Key=self._k(key))
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") in ("NoSuchKey", "404", "NotFound"):
                raise BlobMissing(f"{key} is not in the bucket") from e
            raise
        return _Counted(r["Body"], self)  # type: ignore[return-value]

    def local_copy(self, key: str) -> Path:
        if not KEY_RE.match(key):
            raise StoreError(f"only blobs are cached: {key!r}")
        return self.cache.get(key, lambda: self.open(key))

    def cache_path(self, key: str) -> Path:
        return self.cache.path(key)

    def uri(self, key: str) -> str:
        if self.read_mode == "httpfs":
            return f"s3://{self.bucket}/{self._k(key)}"
        return str(self.local_copy(key))

    def list(self, prefix: str) -> Iterator[tuple[str, int, datetime]]:
        if prefix.startswith("tmp") or prefix.startswith("exports"):
            base = self.scratch / prefix.rstrip("/")
            if not base.exists():
                return
            for dirpath, _d, files in os.walk(base):
                for f in files:
                    p = Path(dirpath) / f
                    try:
                        st = p.stat()
                    except FileNotFoundError:
                        continue
                    yield (p.relative_to(self.scratch).as_posix(), st.st_size, datetime.fromtimestamp(st.st_mtime, timezone.utc))
            return
        pag = self.client.get_paginator("list_objects_v2")
        for page in pag.paginate(Bucket=self.bucket, Prefix=self._k(prefix.rstrip("/") + "/" if prefix else "")):
            for o in page.get("Contents", []):
                yield (self._unk(o["Key"]), int(o["Size"]), o["LastModified"])

    # -- delete (GC / purge, P24) ------------------------------------------------------------------------------------

    def delete(self, key: str, *, bypass_governance: bool = False) -> dict[str, Any]:
        """Remove the key and every version of it. Under Object Lock this needs `bypass_governance=True` and the
        `s3:BypassGovernanceRetention` permission. Call it through `store.delete_blob`, which audits it."""
        from botocore.exceptions import ClientError

        k = self._k(key)
        versions: list[dict[str, str]] = []
        if self.versioning in ("Enabled", "Suspended"):
            pag = self.client.get_paginator("list_object_versions")
            for page in pag.paginate(Bucket=self.bucket, Prefix=k):
                for v in page.get("Versions", []) + page.get("DeleteMarkers", []):
                    if v["Key"] == k:
                        versions.append({"Key": k, "VersionId": v["VersionId"]})
        else:
            versions = [{"Key": k}]
        removed, refused = 0, []
        for v in versions:
            try:
                self.client.delete_object(Bucket=self.bucket, **v, **({"BypassGovernanceRetention": True} if bypass_governance else {}))
                removed += 1
            except ClientError as e:
                refused.append({"version": v.get("VersionId"), "code": e.response.get("Error", {}).get("Code")})
        self._count("deletes")
        p = self.cache.path(key) if KEY_RE.match(key) else None
        if p is not None and p.exists():
            self.cache._drop(p)
        if refused:
            raise ObjectLocked(f"{key}: {len(refused)} version(s) are under retention"
                               + ("" if bypass_governance else " (governance: purge needs bypass_governance)")
                               + f": {refused}")
        return {"key": key, "versions_removed": removed}

    # -- scratch -----------------------------------------------------------------------------------------------------

    def tmp_dir(self, writer_id: str | None = None) -> Path:
        d = self.scratch / "tmp" / (writer_id or uuid.uuid4().hex)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def remove_tmp(self, d: Path) -> None:
        d = Path(d).resolve()
        if d.parent == (self.scratch / "tmp").resolve():
            shutil.rmtree(d, ignore_errors=True)

    # -- admin -------------------------------------------------------------------------------------------------------

    def retention(self, key: str) -> dict[str, Any] | None:
        from botocore.exceptions import ClientError

        try:
            return self.client.get_object_retention(Bucket=self.bucket, Key=self._k(key)).get("Retention")
        except ClientError:
            return None

    def info(self) -> dict[str, Any]:
        s = dict(self.stats)
        s["put_mb_s"] = round(s["put_bytes"] / 1e6 / s["put_seconds"], 1) if s["put_seconds"] else None
        s["get_mb_s"] = round(s["get_bytes"] / 1e6 / s["get_seconds"], 1) if s["get_seconds"] else None
        return {"kind": "s3", "root": self.root, "endpoint": self.cfg.get("endpoint"), "region": self.cfg.get("region"),
                "read": self.read_mode, "conditional_puts": self.conditional, "object_lock": self.object_lock,
                "lock_mode": "GOVERNANCE" if self.object_lock and self.lock_days > 0 else None,
                "lock_days": self.lock_days if self.object_lock else None, "versioning": self.versioning,
                "abort_multipart_rule": self.lifecycle_rule,
                "setup": self.setup_notes, "stats": s, "cache": self.cache.info()}


class _Counted:
    """A streaming GetObject body that adds its bytes and time to the store's get stats when closed."""

    def __init__(self, body: Any, store: S3BlobStore) -> None:
        self.body, self.store, self.n, self.t0 = body, store, 0, time.monotonic()

    def read(self, n: int = -1) -> bytes:
        b = self.body.read() if n is None or n < 0 else self.body.read(n)
        self.n += len(b)
        return b

    def close(self) -> None:
        self.body.close()
        with self.store._slock:
            self.store.stats["gets"] += 1
            self.store.stats["get_bytes"] += self.n
            self.store.stats["get_seconds"] += time.monotonic() - self.t0

    def __enter__(self) -> "_Counted":
        return self

    def __exit__(self, *a: Any) -> None:
        self.close()
