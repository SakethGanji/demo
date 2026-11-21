"""P02: content-addressed blob storage (plan §3.1, A §5.1).

Keys are relative to the store root and never change once visible:

    blobs/<aa>/<sha256>.<ext>      write-once; aa = the first two hex characters
    tmp/<writer-id>/…              in-flight writes, never referenced (swept by GC phase 2)
    exports/<version_id>/<table>…  re-creatable export cache, not a blob

`BlobStore` is the interface. `LocalBlobStore` implements it on a local directory (dev and the single-host POC).
P10b adds `S3BlobStore` (MinIO) behind the same interface. Its contract, which the local store mirrors:
- `put_file` is write-once and idempotent: S3 `PutObject` with `If-None-Match: *` and `x-amz-checksum-sha256`
  (S3 refuses bytes whose hash differs); an existing key is a no-op;
- `uri` is what DuckDB/pyarrow read (a local path, or `s3://bucket/prefix/key` with a DuckDB secret);
- `local_copy` goes through a sha-keyed read cache (no invalidation needed: keys are hashes);
- `delete` is used only by GC / purge (P24), and on S3 also removes every object version.
Nothing outside this module builds a path from a key.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO, Iterator, Protocol, runtime_checkable

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
    def delete(self, key: str) -> None: ...                            # GC / purge only (P24)
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

    def delete(self, key: str) -> None:
        p = self._p(key)
        if p.exists():
            os.chmod(p, stat.S_IRUSR | stat.S_IWUSR)
            p.unlink()

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


def open_store(settings) -> BlobStore:
    """The deployment's store (config.lake_store_kind()). Only 'local' exists until P10b."""
    from .. import config

    kind = config.lake_store_kind()
    if kind == "local":
        return LocalBlobStore(settings.lake_dir)
    raise StoreError(f"ENSEMBLE_LAKE_STORE={kind!r} is not available yet (S3/MinIO arrives in P10b); use 'local'.")
