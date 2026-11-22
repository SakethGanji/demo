"""P02: content-addressed blob storage (plan §3.1, A §5.1).

Keys are relative to the store root and never change once visible:

    blobs/<aa>/<sha256>.<ext>      write-once; aa = the first two hex characters
    tmp/<writer-id>/…              in-flight writes, never referenced (swept by GC phase 2)
    exports/<version_id>/<table>…  re-creatable export cache, not a blob

`BlobStore` is the interface. `LocalBlobStore` implements it on a local directory (dev and the single-host POC).
P10b: `S3BlobStore` (s3store.py, S3 / MinIO) implements it too; `open_store` picks one by ENSEMBLE_BLOB_STORE.
The contract, which the local store mirrors:
- `put_file` is write-once and idempotent: S3 `PutObject` with `If-None-Match: *` and `x-amz-checksum-sha256`
  (S3 refuses bytes whose hash differs); an existing key is a no-op;
- `uri` is what DuckDB/pyarrow read (a local path, or `s3://bucket/prefix/key` with a DuckDB secret);
- `local_copy` goes through a sha-keyed read cache (no invalidation needed: keys are hashes);
- `delete` is used only by GC / purge (P24), through `delete_blob` (audited); on S3 it removes every object
  version and, under Object Lock, needs `bypass_governance=True`.
Nothing outside this module builds a path from a key.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO, Iterator, Protocol, runtime_checkable

SHA_RE = re.compile(r"^[0-9a-f]{64}$")
EXT_RE = re.compile(r"^[a-z0-9]{1,10}$")
KEY_RE = re.compile(r"^blobs/([0-9a-f]{2})/([0-9a-f]{64})\.([a-z0-9]{1,10})$")


class StoreError(Exception):
    pass


def blob_key(sha256: str, ext: str) -> str:
    """blobs/<aa>/<sha256>.<ext>, the one key scheme (§3.1)."""
    if not SHA_RE.match(sha256 or ""):
        raise StoreError(f"not a sha256: {sha256!r}")
    if not EXT_RE.match(ext or ""):
        raise StoreError(f"bad extension: {ext!r}")
    return f"blobs/{sha256[:2]}/{sha256}.{ext}"


def parse_key(key: str) -> tuple[str, str] | None:
    """(sha256, ext) of a blob key, or None when the key is not one (GC reports such names as integrity findings)."""
    m = KEY_RE.match(key)
    if not m or m.group(1) != m.group(2)[:2]:
        return None
    return m.group(2), m.group(3)


@runtime_checkable
class BlobStore(Protocol):
    root: str  # 'file:///…/data/lake' | 's3://bucket/prefix'

    def put_file(self, local: Path, sha256: str, ext: str) -> str: ...  # → key; no-op when the key exists
    def exists(self, key: str) -> bool: ...
    def size(self, key: str) -> int | None: ...
    def open(self, key: str) -> BinaryIO: ...                          # streaming read
    def uri(self, key: str) -> str: ...                                # what DuckDB / pyarrow read
    def local_copy(self, key: str) -> Path: ...                        # a local file (cache on S3)
    def delete(self, key: str, *, bypass_governance: bool = False) -> Any: ...  # GC / purge only (P24): delete_blob
    def list(self, prefix: str) -> Iterator[tuple[str, int, datetime]]: ...  # (key, bytes, modified) for GC and verify-all
    def tmp_dir(self, writer_id: str | None = None) -> Path: ...       # local scratch for one writer


class LocalBlobStore:
    """Blobs under a local directory. `put_file` hard-links (or copies, across filesystems) the finished tmp file into
    place under a temporary name, then renames it: readers never see a partial file. Blobs are chmod 0444."""

    def __init__(self, root: Path) -> None:
        self.path = Path(root).resolve()
        self.root = self.path.as_uri()
        (self.path / "blobs").mkdir(parents=True, exist_ok=True)
        (self.path / "tmp").mkdir(parents=True, exist_ok=True)

    # -- keys → paths (the only place) -----------------------------------------------------------------------------

    def _p(self, key: str) -> Path:
        if not key or key.startswith("/") or ".." in Path(key).parts:
            raise StoreError(f"bad key: {key!r}")
        return self.path / key

    # -- interface ----------------------------------------------------------------------------------------------------

    def put_file(self, local: Path, sha256: str, ext: str) -> str:
        key = blob_key(sha256, ext)
        dest = self._p(key)
        if dest.exists():
            return key  # write-once: the bytes are already there (dedupe across datasets for free)
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_name(f".{dest.name}.{uuid.uuid4().hex[:8]}.part")
        try:
            try:
                os.link(local, part)
            except OSError:
                shutil.copyfile(local, part)
            os.chmod(part, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
            try:
                os.link(part, dest)  # fails if another writer got there first: theirs is the same bytes
            except FileExistsError:
                pass
        finally:
            part.unlink(missing_ok=True)
        return key

    def exists(self, key: str) -> bool:
        return self._p(key).is_file()

    def size(self, key: str) -> int | None:
        p = self._p(key)
        return p.stat().st_size if p.is_file() else None

    def open(self, key: str) -> BinaryIO:
        return open(self._p(key), "rb")

    def uri(self, key: str) -> str:
        return str(self._p(key))

    def local_copy(self, key: str) -> Path:
        return self._p(key)

    def cache_path(self, key: str) -> Path:
        return self._p(key)

    def delete(self, key: str, *, bypass_governance: bool = False) -> dict:
        p = self._p(key)
        if p.exists():
            os.chmod(p, stat.S_IRUSR | stat.S_IWUSR)
            p.unlink()
            return {"key": key, "versions_removed": 1}
        return {"key": key, "versions_removed": 0}

    def list(self, prefix: str) -> Iterator[tuple[str, int, datetime]]:
        base = self._p(prefix.rstrip("/")) if prefix else self.path
        if not base.exists():
            return
        for dirpath, _dirs, files in os.walk(base):
            for f in files:
                p = Path(dirpath) / f
                try:
                    st = p.stat()
                except FileNotFoundError:
                    continue
                yield (p.relative_to(self.path).as_posix(), st.st_size, datetime.fromtimestamp(st.st_mtime, timezone.utc))

    def tmp_dir(self, writer_id: str | None = None) -> Path:
        d = self.path / "tmp" / (writer_id or uuid.uuid4().hex)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def remove_tmp(self, d: Path) -> None:
        d = Path(d).resolve()
        if d.parent == (self.path / "tmp").resolve():
            shutil.rmtree(d, ignore_errors=True)


_stores: dict[tuple, BlobStore] = {}
_stores_lock = threading.Lock()


def open_store(settings) -> BlobStore:
    """The deployment's store (config.lake_store_kind()): 'local' or 's3'. One instance per configuration and process
    (the S3 client, its stats and the read cache's counters are shared)."""
    from .. import config

    kind = config.lake_store_kind()
    if kind == "local":
        k: tuple = ("local", str(settings.lake_dir))
    elif kind == "s3":
        cfg = config.s3_config()
        if not cfg["bucket"]:
            raise StoreError("ENSEMBLE_BLOB_STORE=s3 needs a bucket: ENSEMBLE_S3_BUCKET, or ENSEMBLE_BLOB_STORE=s3://bucket/prefix.")
        k = ("s3", str(settings.lake_dir), str(config.cache_dir(settings)), tuple(sorted((a, str(b)) for a, b in cfg.items())))
    else:
        raise StoreError(f"ENSEMBLE_BLOB_STORE={kind!r} is not a store; use 'local' or 's3'.")
    with _stores_lock:
        st = _stores.get(k)
        if st is None:
            if kind == "local":
                st = LocalBlobStore(settings.lake_dir)
            else:
                from .s3store import S3BlobStore

                st = S3BlobStore(cfg, scratch_dir=settings.lake_dir, cache_dir=config.cache_dir(settings))
            _stores[k] = st
        return st


def store_info(store: BlobStore) -> dict:
    info = getattr(store, "info", None)
    return info() if callable(info) else {"kind": "local", "root": store.root}


def delete_blob(db, store: BlobStore, sha256: str, *, by: str, reason: str, bypass_governance: bool = False) -> dict:
    """P10b: the one audited way to remove a blob's bytes (GC and purge, P24). Under S3 Object Lock (governance)
    `bypass_governance=True` is needed and needs the bypass permission. Writes audit `blob_deleted` (or
    `blob_delete_refused`). The `blobs` row is the caller's (P24 decides its state)."""
    from ..catalog import schema as S

    with db.connect() as con:
        r = con.execute("SELECT key, kind, bytes FROM blobs WHERE sha256 = ?", (sha256,)).fetchone()
    if not r:
        raise StoreError(f"no blob {sha256}")
    details = {"sha256": sha256, "key": r[0], "kind": r[1], "bytes": r[2], "store": store.root,
               "bypass_governance": bool(bypass_governance), "reason": reason}
    try:
        out = store.delete(r[0], bypass_governance=bypass_governance)
    except StoreError as e:
        with db.connect() as con:
            S.audit(con, by, "blob_delete_refused", {**details, "error": str(e)}, comment=reason, object_kind="blob", object_id=sha256)
        raise
    with db.connect() as con:
        S.audit(con, by, "blob_deleted", {**details, **out}, comment=reason, object_kind="blob", object_id=sha256)
    return {**details, **out}
