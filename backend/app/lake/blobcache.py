"""P10b: the sha-keyed local read cache in front of an object store (plan §7 risk 10, B §6.2 / B7).

    <cache_dir>/blobs/<aa>/<sha256>.<ext>        the bytes (chmod 0444)
    <cache_dir>/blobs/<aa>/<sha256>.<ext>.meta   {"size", "mtime_ns", "ctime_ns", "ino", "filled_at"}; its mtime is the
                                                 last use (LRU)

The key is the content hash, so an entry can never be stale; there is nothing to invalidate.
- **Filled verified:** the object is streamed into a temp file while it is hashed. A file whose sha256 is not its name
  is never renamed into place (`CacheIntegrityError`). The rename is atomic, so a reader never sees a partial file.
- **Checked on every hit** (`ENSEMBLE_CACHE_VERIFY`):
  - `stat` (the default): the size, mtime, ctime and inode must equal what the fill recorded. Any write, chmod or
    utime changes the ctime, which user space cannot set back, so even a tamper that restores the mtime makes the
    file re-hashed. A file that no longer hashes to its name is dropped and fetched again (`stats.tamper_refetch`).
  - `always`: every hit is re-hashed. This is slow and for audits.
  - `fill`: trusted after the fill (B §6.2's original rule).
  Only a raw-device edit (or a clock change) gets past `stat`; `always` or `POST /api/admin/lake/cache/verify` catches
  that, and `frames.read(hash_into=…)` (the run's data gate) hashes the bytes it parses anyway.
- **Size-capped LRU** (`ENSEMBLE_CACHE_MAX_GB`, 20 GB): after a fill that takes the cache over the cap, the least
  recently used entries are removed. An entry used in the last `ENSEMBLE_CACHE_PIN_S` seconds (300) is never evicted,
  because a caller may still hold its path. The cap can therefore be passed for a short time. Several processes (API,
  workers) may share one cache directory: fills are atomic renames and eviction only unlinks files.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat as _stat
import threading
import time
import uuid
from pathlib import Path
from typing import Any, BinaryIO, Callable

from .store import KEY_RE, StoreError

CHUNK = 8 * 1024 * 1024


class CacheIntegrityError(StoreError):
    """The object store returned bytes whose sha256 is not their key's."""


class BlobCache:
    def __init__(self, root: Path, *, max_bytes: int, verify: str = "stat", pin_s: float = 300.0) -> None:
        self.root = Path(root).resolve()
        self.dir = self.root / "blobs"
        self.tmp = self.root / "tmp"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.tmp.mkdir(parents=True, exist_ok=True)
        self.max_bytes = int(max_bytes)
        self.verify = verify if verify in ("stat", "always", "fill") else "stat"
        self.pin_s = float(pin_s)
        self._locks: dict[str, threading.Lock] = {}
        self._glock = threading.Lock()
        self.stats: dict[str, Any] = {"hits": 0, "misses": 0, "fills": 0, "fill_bytes": 0, "fill_seconds": 0.0,
                                      "evictions": 0, "evicted_bytes": 0, "rehash_on_hit": 0, "tamper_refetch": 0,
                                      "fill_integrity_errors": 0}
        self._bytes = self._scan_bytes()

    # -- paths -------------------------------------------------------------------------------------------------------

    def path(self, key: str) -> Path:
        m = KEY_RE.match(key)
        if not m:
            raise StoreError(f"the cache holds blobs only, not {key!r}")
        return self.dir / m.group(1) / f"{m.group(2)}.{m.group(3)}"

    @staticmethod
    def _meta(p: Path) -> Path:
        return p.with_name(p.name + ".meta")

    def _lock(self, key: str) -> threading.Lock:
        with self._glock:
            return self._locks.setdefault(key, threading.Lock())

    # -- the one entry point -----------------------------------------------------------------------------------------

    def get(self, key: str, fetch: Callable[[], BinaryIO]) -> Path:
        """The local path of `key`'s bytes: a checked hit, or a verified fill from `fetch()` (a readable stream)."""
        sha = KEY_RE.match(key).group(2) if KEY_RE.match(key) else None
        if sha is None:
            raise StoreError(f"not a blob key: {key!r}")
        p = self.path(key)
        with self._lock(key):
            if p.exists():
                if self._hit_ok(p, sha):
                    self.stats["hits"] += 1
                    self._touch(p)
                    return p
                self.stats["tamper_refetch"] += 1
                self._drop(p)
            self.stats["misses"] += 1
            self._fill(p, sha, fetch)
        self._evict(keep=p)
        return p

    def _hit_ok(self, p: Path, sha: str) -> bool:
        if self.verify == "fill":
            return True
        try:
            st = p.stat()
            meta = json.loads(self._meta(p).read_text())
        except (OSError, ValueError):
            meta, st = None, None
        if (self.verify == "stat" and meta and st and meta.get("size") == st.st_size
                and meta.get("mtime_ns") == st.st_mtime_ns and meta.get("ino") == st.st_ino
                and meta.get("ctime_ns") == st.st_ctime_ns):
            return True
        self.stats["rehash_on_hit"] += 1
        ok = _file_sha256(p) == sha
        if ok and st is not None:  # a lost / stale sidecar on good bytes: re-record it
            self._write_meta(p, p.stat())
        return ok

    def _fill(self, p: Path, sha: str, fetch: Callable[[], BinaryIO]) -> None:
        p.parent.mkdir(parents=True, exist_ok=True)
        self.tmp.mkdir(parents=True, exist_ok=True)  # the cache directory may have been deleted under us
        part = self.tmp / f"{sha}.{uuid.uuid4().hex[:8]}.part"
        t0 = time.monotonic()
        h, n = hashlib.sha256(), 0
        try:
            with fetch() as src, open(part, "wb") as out:
                while b := src.read(CHUNK):
                    h.update(b)
                    out.write(b)
                    n += len(b)
            if h.hexdigest() != sha:
                self.stats["fill_integrity_errors"] += 1
                raise CacheIntegrityError(f"the object store returned bytes for {p.name} whose sha256 is {h.hexdigest()}; "
                                          "they were not cached (verify the version: the object may have been altered).")
            os.chmod(part, _stat.S_IRUSR | _stat.S_IRGRP | _stat.S_IROTH)
            os.replace(part, p)
        finally:
            part.unlink(missing_ok=True)
        self._write_meta(p, p.stat())
        self.stats["fills"] += 1
        self.stats["fill_bytes"] += n
        self.stats["fill_seconds"] += time.monotonic() - t0
        with self._glock:
            self._bytes += n

    def _write_meta(self, p: Path, st: os.stat_result) -> None:
        m = self._meta(p)
        tmp = m.with_name(m.name + f".{uuid.uuid4().hex[:6]}")
        tmp.write_text(json.dumps({"size": st.st_size, "mtime_ns": st.st_mtime_ns, "ino": st.st_ino,
                                   "ctime_ns": st.st_ctime_ns,
                                   "filled_at": time.time()}))
        os.replace(tmp, m)

    def _touch(self, p: Path) -> None:
        try:
            os.utime(self._meta(p))
        except OSError:
            pass

    def _drop(self, p: Path) -> int:
        try:
            n = p.stat().st_size
        except OSError:
            n = 0
        for f in (p, self._meta(p)):
            try:
                f.unlink()
            except FileNotFoundError:
                pass
        with self._glock:
            self._bytes = max(0, self._bytes - n)
        return n

    # -- LRU ---------------------------------------------------------------------------------------------------------

    def _entries(self) -> list[tuple[float, int, Path]]:
        out = []
        for d in self.dir.iterdir() if self.dir.exists() else []:
            if not d.is_dir():
                continue
            for f in d.iterdir():
                if f.name.endswith(".meta") or ".meta." in f.name:
                    continue
                try:
                    size = f.stat().st_size
                    used = self._meta(f).stat().st_mtime
                except OSError:
                    used, size = 0.0, f.stat().st_size if f.exists() else 0
                out.append((used, size, f))
        return out

    def _scan_bytes(self) -> int:
        return sum(s for _, s, _ in self._entries())

    def _evict(self, keep: Path | None = None) -> None:
        if self._bytes <= self.max_bytes:
            return
        entries = self._entries()
        total = sum(s for _, s, _ in entries)
        now = time.time()
        for used, size, f in sorted(entries, key=lambda e: e[0]):
            if total <= self.max_bytes:
                break
            if f == keep or now - used < self.pin_s:
                continue
            self._drop(f)
            total -= size
            self.stats["evictions"] += 1
            self.stats["evicted_bytes"] += size
        with self._glock:
            self._bytes = total

    # -- admin -------------------------------------------------------------------------------------------------------

    def info(self) -> dict[str, Any]:
        entries = self._entries()
        s = dict(self.stats)
        s["fill_mb_s"] = round(s["fill_bytes"] / 1e6 / s["fill_seconds"], 1) if s["fill_seconds"] else None
        s["fill_seconds"] = round(s["fill_seconds"], 3)
        return {"dir": str(self.root), "entries": len(entries), "bytes": sum(e[1] for e in entries),
                "max_bytes": self.max_bytes, "verify": self.verify, "pin_s": self.pin_s, "stats": s}

    def verify_all(self) -> dict[str, Any]:
        """Re-hash every cached file; drop the ones that no longer match (they are fetched again on next use)."""
        bad = []
        n = 0
        for _, _, f in self._entries():
            n += 1
            sha = f.name.split(".", 1)[0]
            if _file_sha256(f) != sha:
                bad.append(f.name)
                self._drop(f)
        return {"checked": n, "dropped": bad}


def _file_sha256(p: Path) -> str | None:
    h = hashlib.sha256()
    try:
        with open(p, "rb") as f:
            while b := f.read(CHUNK):
                h.update(b)
    except OSError:
        return None
    return h.hexdigest()
