"""Storage abstraction — local filesystem or S3.

All persistent data is addressed by *keys* — relative paths like
``datasets/default/abc123/v000001/parquet/dataset.parquet``.

The active ``StorageBackend`` resolves keys to full paths (local) or URIs
(``s3://…``) that DuckDB, pandas, and application code can consume directly.

To switch to S3:
  1. Set ``ANALYTICS_STORAGE_BACKEND=s3`` plus ``S3_BUCKET`` / ``S3_PREFIX``
  2. The ``S3StorageBackend`` handles resolution and I/O via boto3

TUS uploads always stay on the local filesystem (staging area).
"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Key sanitisation
# ---------------------------------------------------------------------------


def _sanitize(value: str) -> str:
    """Sanitize a value for use as a storage key component."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip())
    cleaned = cleaned.strip(".-")
    return cleaned or "unknown"


# ---------------------------------------------------------------------------
# Dataset layout — key builder for versioned dataset storage
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DatasetLayout:
    """Builds storage keys for every artifact of a dataset version.

    Usage::

        layout = DatasetLayout("team-1", "ds-abc", 3, source_filename="data.csv")
        storage = get_storage()

        # Full path/URI for DuckDB:
        path = storage.resolve(layout.canonical_parquet)

        # Ensure dirs + write manifest:
        layout.ensure_dirs()
        layout.write_manifest(row_count=1000, size_bytes=4096)
    """

    team_id: str
    dataset_id: str
    version: int
    source_filename: str | None = None

    # -- keys ---------------------------------------------------------------

    @property
    def _base(self) -> str:
        t = _sanitize(self.team_id or "default")
        d = _sanitize(self.dataset_id)
        return f"datasets/{t}/{d}/v{self.version:06d}"

    @property
    def root(self) -> str:
        """Version root key."""
        return self._base

    @property
    def source_file(self) -> str | None:
        """Key to the original uploaded file."""
        if not self.source_filename:
            return None
        return f"{self._base}/source/{_sanitize(self.source_filename)}"

    @property
    def canonical_parquet(self) -> str:
        """Key to the main parquet file."""
        return f"{self._base}/parquet/dataset.parquet"

    def sheet_parquet(self, sheet_name: str) -> str:
        """Key to an Excel sheet's parquet file."""
        return f"{self._base}/parquet/sheets/{_sanitize(sheet_name)}.parquet"

    @property
    def manifest(self) -> str:
        """Key to the version manifest JSON."""
        return f"{self._base}/manifest.json"

    def derived(self, filename: str) -> str:
        """Key to a derived artifact (e.g. profiling cache)."""
        return f"{self._base}/derived/{_sanitize(filename)}"

    # -- directory keys (for ensure_dir) ------------------------------------

    def dirs(self) -> list[str]:
        """All directory keys that should exist for this version."""
        return [
            f"{self._base}/source",
            f"{self._base}/parquet",
            f"{self._base}/parquet/sheets",
            f"{self._base}/derived",
        ]

    # -- convenience methods (operate via active backend) -------------------

    def ensure_dirs(self) -> None:
        """Create all directories for this version."""
        backend = get_storage()
        for d in self.dirs():
            backend.ensure_dir(d)

    def write_manifest(self, **metadata: Any) -> None:
        """Write version manifest JSON."""
        self.ensure_dirs()
        manifest = {
            "team_id": self.team_id,
            "dataset_id": self.dataset_id,
            "version": self.version,
            "source_file": self.source_file,
            "canonical_parquet": self.canonical_parquet,
            **metadata,
        }
        get_storage().write_text(
            self.manifest,
            json.dumps(manifest, indent=2, sort_keys=True),
        )


# ---------------------------------------------------------------------------
# Sample key helper
# ---------------------------------------------------------------------------


def sample_key(filename: str) -> str:
    """Build a storage key for a sample output file."""
    return f"samples/{_sanitize(filename)}"


# ---------------------------------------------------------------------------
# Abstract backend
# ---------------------------------------------------------------------------


class StorageBackend(ABC):
    """Storage interface. Keys are relative paths (e.g. ``datasets/team/ds/v1/...``)."""

    @abstractmethod
    def resolve(self, key: str) -> str:
        """Resolve key to full path (local) or URI (``s3://…``).

        The returned string can be used directly with DuckDB, pandas, etc.
        """

    @abstractmethod
    def ensure_dir(self, key: str) -> None:
        """Ensure directory exists for *key*. No-op for object stores."""

    @abstractmethod
    def write_text(self, key: str, content: str) -> None:
        """Write text content."""

    @abstractmethod
    def write_bytes(self, key: str, data: bytes) -> None:
        """Write binary content."""

    @abstractmethod
    def read_text(self, key: str) -> str:
        """Read text content."""

    @abstractmethod
    def read_bytes(self, key: str) -> bytes:
        """Read binary content."""

    @abstractmethod
    def exists(self, key: str) -> bool:
        """Check whether key exists."""

    @abstractmethod
    def delete(self, key: str) -> None:
        """Delete an object/file."""

    @abstractmethod
    def size(self, key: str) -> int:
        """Get object/file size in bytes."""

    @abstractmethod
    def list_keys(self, prefix: str) -> list[str]:
        """List keys under a prefix."""


# ---------------------------------------------------------------------------
# Local filesystem backend
# ---------------------------------------------------------------------------


class LocalStorageBackend(StorageBackend):
    """Stores everything under a single base directory.

    Key ``datasets/default/abc/v000001/parquet/dataset.parquet``
    resolves to ``{base_dir}/datasets/default/abc/v000001/parquet/dataset.parquet``.
    """

    def __init__(self, base_dir: Path):
        self._base = base_dir

    def resolve(self, key: str) -> str:
        return str(self._base / key)

    def ensure_dir(self, key: str) -> None:
        Path(self.resolve(key)).mkdir(parents=True, exist_ok=True)

    def write_text(self, key: str, content: str) -> None:
        p = Path(self.resolve(key))
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)

    def write_bytes(self, key: str, data: bytes) -> None:
        p = Path(self.resolve(key))
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    def read_text(self, key: str) -> str:
        return Path(self.resolve(key)).read_text()

    def read_bytes(self, key: str) -> bytes:
        return Path(self.resolve(key)).read_bytes()

    def exists(self, key: str) -> bool:
        return Path(self.resolve(key)).exists()

    def delete(self, key: str) -> None:
        p = Path(self.resolve(key))
        if p.exists():
            p.unlink()

    def size(self, key: str) -> int:
        return Path(self.resolve(key)).stat().st_size

    def list_keys(self, prefix: str) -> list[str]:
        root = Path(self.resolve(prefix))
        if not root.exists():
            return []
        base_len = len(str(self._base)) + 1  # strip base + separator
        return sorted(str(p)[base_len:] for p in root.rglob("*") if p.is_file())


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_backend: StorageBackend | None = None


def init_storage(backend: StorageBackend) -> None:
    """Set the storage backend explicitly. Call before first use to override default."""
    global _backend
    _backend = backend


def get_storage() -> StorageBackend:
    """Get the storage backend. Lazy-inits from settings."""
    global _backend
    if _backend is None:
        from app.infra.config import settings

        if settings.storage_backend == "s3":
            raise NotImplementedError(
                "S3 backend not yet implemented. "
                "Set ANALYTICS_STORAGE_BACKEND=local or implement S3StorageBackend."
            )
        _backend = LocalStorageBackend(base_dir=settings.storage_dir)
    return _backend


# ---------------------------------------------------------------------------
# Convenience — resolve keys without touching the backend directly
# ---------------------------------------------------------------------------


def resolve(key: str) -> str:
    """Resolve a storage key to a full path/URI."""
    return get_storage().resolve(key)


def uploads_dir() -> Path:
    """TUS upload staging directory (always local — TUS needs file locks)."""
    from app.infra.config import settings

    return settings.tus_upload_dir
