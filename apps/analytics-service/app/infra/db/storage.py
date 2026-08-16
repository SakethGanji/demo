"""Storage abstraction — local filesystem or any S3-compatible object store.

All persistent data is addressed by *keys* — relative paths like
``datasets/default/abc123/v000001/parquet/dataset.parquet``.

The active ``StorageBackend`` resolves keys to full paths (local) or URIs
(``s3://bucket/…``). DuckDB consumes both directly (``s3://`` via httpfs — see
:func:`duckdb_s3_statements`). Code that needs real local files (checksums,
Excel conversion) builds artifacts in a temp dir and publishes them with
``put_file``; streaming reads go through ``open_read``.

To switch to S3 (MinIO in dev, an internal S3 gateway in prod):
    ACCELERATOR_STORAGE_BACKEND=s3
    ACCELERATOR_S3_BUCKET=analytics
    ACCELERATOR_S3_ENDPOINT_URL=http://localhost:9000   # or the internal gateway
    ACCELERATOR_S3_ACCESS_KEY_ID=…  ACCELERATOR_S3_SECRET_ACCESS_KEY=…

TUS/simple-upload staging always stays on the local filesystem.
"""

from __future__ import annotations

import json
import re
import shutil
import stat
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO


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
# Artifact layout — key builder for derived outputs
# ---------------------------------------------------------------------------


ARTIFACT_ROOT = "artifacts"

# Used when an artifact has no owning dataset (inline-data sources, ad-hoc
# uploads) or no resolvable team. Kept as explicit path segments so those
# artifacts are still swept by a prefix rule rather than scattered.
ADHOC_DATASET = "_adhoc"
SHARED_TEAM = "_shared"


@dataclass(frozen=True)
class ArtifactLayout:
    """Key builder for derived outputs (samples, aggregations, diffs, exports…).

    Layout::

        artifacts/{team_id}/{dataset_id}/{kind}/{filename}

    Every segment earns its place:

    * **team** — lets a bucket policy or IAM prefix scope one tenant's derived
      data, which a single shared prefix cannot express.
    * **dataset** — makes deleting a dataset a prefix delete rather than an
      enumerate-then-delete over a global namespace.
    * **kind** — the reason this matters most: retention differs sharply by
      kind. Query and diff scratch can expire in days; a published source must
      not. With the kind only in the filename, no lifecycle rule can tell them
      apart.

    Deliberately *not* date-partitioned. Expiry rules key off object age, which
    S3 lifecycle evaluates natively, and listings come from the ``artifacts``
    table rather than a bucket scan — so a date segment would buy nothing and
    would cost the property that makes this class safe to use from two places:
    the key is a pure function of its fields, so the writer and the ownership
    registration derive the same string without threading it between them.

    Readers still go through the ``artifacts`` table: ``/samples/{filename}``
    knows neither team, dataset, nor kind, and that row governs authorization
    anyway.
    """

    kind: str
    team_id: str | None = None
    dataset_id: str | None = None

    def key(self, filename: str) -> str:
        return "/".join((
            ARTIFACT_ROOT,
            _sanitize(self.team_id or SHARED_TEAM),
            _sanitize(self.dataset_id or ADHOC_DATASET),
            _sanitize(self.kind),
            _sanitize(filename),
        ))

    def dataset_prefix(self) -> str:
        """Prefix covering every artifact of this team+dataset, any kind."""
        return dataset_artifact_prefix(self.team_id, self.dataset_id)


def dataset_artifact_prefix(team_id: str | None,
                            dataset_id: str | None) -> str:
    """Prefix covering every derived artifact of a dataset, any kind.

    Standalone because deleting a dataset has no kind to name — asking for one
    just to compute a prefix that ignores it reads as a mistake.
    """
    return "/".join((
        ARTIFACT_ROOT,
        _sanitize(team_id or SHARED_TEAM),
        _sanitize(dataset_id or ADHOC_DATASET),
    ))


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
    def modified_at(self, key: str) -> datetime:
        """Last-modified time, timezone-aware UTC.

        Retention needs an object's age from the backend, not from a database
        row, because the blobs it most needs to judge are exactly the ones with
        no row.
        """

    @abstractmethod
    def list_keys(self, prefix: str) -> list[str]:
        """List keys under a prefix."""

    @abstractmethod
    def list_sizes(self, prefix: str) -> list[tuple[str, int]]:
        """(key, size) for everything under *prefix*, in ONE backend traversal.

        Summing a prefix by calling :meth:`size` per key costs a HEAD request
        per object on S3, which turns a usage report into thousands of round
        trips. Both backends already know each object's size while listing, so
        this returns it rather than asking again.
        """

    @abstractmethod
    def put_file(self, key: str, local_path: Path) -> None:
        """Publish a locally-built file into storage at *key*.

        The source file is left in place (callers use temp dirs that clean up).
        """

    @abstractmethod
    def key_of(self, path_or_uri: str) -> str | None:
        """Inverse of :meth:`resolve` — map a stored path/URI back to its key.

        Returns None if the path does not belong to this backend (e.g. a
        legacy absolute path from before a backend switch).
        """

    @abstractmethod
    def delete_prefix(self, prefix: str) -> int:
        """Recursively delete everything under *prefix*. Returns objects removed."""

    @abstractmethod
    def open_read(self, key: str) -> BinaryIO:
        """Open *key* for streaming reads. Caller must close it."""


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

    def modified_at(self, key: str) -> datetime:
        return datetime.fromtimestamp(
            Path(self.resolve(key)).stat().st_mtime, tz=timezone.utc)

    def list_keys(self, prefix: str) -> list[str]:
        root = Path(self.resolve(prefix))
        if not root.exists():
            return []
        base_len = len(str(self._base)) + 1  # strip base + separator
        return sorted(str(p)[base_len:] for p in root.rglob("*") if p.is_file())

    def list_sizes(self, prefix: str) -> list[tuple[str, int]]:
        root = Path(self.resolve(prefix))
        if not root.exists():
            return []
        base_len = len(str(self._base)) + 1
        out: list[tuple[str, int]] = []
        for p in root.rglob("*"):
            try:
                st = p.stat()
            except OSError:  # vanished mid-walk
                continue
            if stat.S_ISREG(st.st_mode):
                out.append((str(p)[base_len:], st.st_size))
        return sorted(out)

    def put_file(self, key: str, local_path: Path) -> None:
        dest = Path(self.resolve(key))
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(local_path, dest)

    def key_of(self, path_or_uri: str) -> str | None:
        try:
            return Path(path_or_uri).resolve().relative_to(self._base.resolve()).as_posix()
        except ValueError:
            return None

    def delete_prefix(self, prefix: str) -> int:
        root = Path(self.resolve(prefix))
        if not root.exists():
            return 0
        count = sum(1 for p in root.rglob("*") if p.is_file())
        shutil.rmtree(root, ignore_errors=True)
        return count

    def open_read(self, key: str) -> BinaryIO:
        return open(self.resolve(key), "rb")


# ---------------------------------------------------------------------------
# S3-compatible backend (AWS, MinIO, internal gateways)
# ---------------------------------------------------------------------------


class S3StorageBackend(StorageBackend):
    """Key-addressed storage on any S3-compatible endpoint via boto3.

    ``resolve`` returns ``s3://bucket/prefix/key`` URIs — DuckDB reads these
    natively once a connection is configured with :func:`duckdb_s3_statements`.
    Byte-level I/O (manifests, streaming downloads) goes through boto3.
    """

    def __init__(
        self,
        *,
        bucket: str,
        prefix: str = "",
        endpoint_url: str | None = None,
        region: str | None = None,
        access_key_id: str | None = None,
        secret_access_key: str | None = None,
        force_path_style: bool = True,
    ):
        import boto3  # deferred: only needed when the s3 backend is active
        from botocore.config import Config

        self._bucket = bucket
        self._prefix = prefix.strip("/")
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            region_name=region,
            # None values fall back to the standard AWS credential chain
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            config=Config(s3={"addressing_style": "path" if force_path_style else "auto"}),
        )

    def _k(self, key: str) -> str:
        """Full object key including the configured prefix."""
        return f"{self._prefix}/{key}" if self._prefix else key

    def resolve(self, key: str) -> str:
        return f"s3://{self._bucket}/{self._k(key)}"

    def ensure_dir(self, key: str) -> None:
        pass  # object stores have no directories

    def write_text(self, key: str, content: str) -> None:
        self.write_bytes(key, content.encode("utf-8"))

    def write_bytes(self, key: str, data: bytes) -> None:
        self._client.put_object(Bucket=self._bucket, Key=self._k(key), Body=data)

    def read_text(self, key: str) -> str:
        return self.read_bytes(key).decode("utf-8")

    def read_bytes(self, key: str) -> bytes:
        obj = self._client.get_object(Bucket=self._bucket, Key=self._k(key))
        return obj["Body"].read()

    def exists(self, key: str) -> bool:
        from botocore.exceptions import ClientError
        try:
            self._client.head_object(Bucket=self._bucket, Key=self._k(key))
            return True
        except ClientError:
            return False

    def delete(self, key: str) -> None:
        self._client.delete_object(Bucket=self._bucket, Key=self._k(key))

    def size(self, key: str) -> int:
        head = self._client.head_object(Bucket=self._bucket, Key=self._k(key))
        return int(head["ContentLength"])

    def modified_at(self, key: str) -> datetime:
        head = self._client.head_object(Bucket=self._bucket, Key=self._k(key))
        stamp = head["LastModified"]
        return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)

    def list_keys(self, prefix: str) -> list[str]:
        full_prefix = self._k(prefix).rstrip("/") + "/"
        strip = len(self._prefix) + 1 if self._prefix else 0
        keys: list[str] = []
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self._bucket, Prefix=full_prefix):
            keys.extend(obj["Key"][strip:] for obj in page.get("Contents", []))
        return sorted(keys)

    def list_sizes(self, prefix: str) -> list[tuple[str, int]]:
        full_prefix = self._k(prefix).rstrip("/") + "/"
        strip = len(self._prefix) + 1 if self._prefix else 0
        out: list[tuple[str, int]] = []
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self._bucket, Prefix=full_prefix):
            out.extend((o["Key"][strip:], int(o["Size"]))
                       for o in page.get("Contents", []))
        return sorted(out)

    def put_file(self, key: str, local_path: Path) -> None:
        self._client.upload_file(str(local_path), self._bucket, self._k(key))

    def key_of(self, path_or_uri: str) -> str | None:
        want = f"s3://{self._bucket}/"
        if not path_or_uri.startswith(want):
            return None
        full_key = path_or_uri[len(want):]
        if self._prefix:
            if not full_key.startswith(self._prefix + "/"):
                return None
            return full_key[len(self._prefix) + 1:]
        return full_key

    def delete_prefix(self, prefix: str) -> int:
        keys = self.list_keys(prefix)
        for batch_start in range(0, len(keys), 1000):  # delete_objects caps at 1000
            batch = keys[batch_start:batch_start + 1000]
            self._client.delete_objects(
                Bucket=self._bucket,
                Delete={"Objects": [{"Key": self._k(k)} for k in batch], "Quiet": True},
            )
        return len(keys)

    def open_read(self, key: str) -> BinaryIO:
        obj = self._client.get_object(Bucket=self._bucket, Key=self._k(key))
        return obj["Body"]  # StreamingBody: read()/close()


def duckdb_s3_statements() -> list[str]:
    """SQL statements configuring a DuckDB connection to read this deployment's S3.

    Uses a DuckDB secret so ``read_parquet('s3://…')`` works against MinIO or
    an internal gateway. Empty when the local backend is active.
    """
    from urllib.parse import urlparse

    from app.infra.config import settings

    if settings.storage_backend != "s3":
        return []

    parts = [f"REGION '{settings.s3_region}'"]
    if settings.s3_access_key_id and settings.s3_secret_access_key:
        parts.append(f"KEY_ID '{settings.s3_access_key_id}'")
        parts.append(f"SECRET '{settings.s3_secret_access_key}'")
    if settings.s3_endpoint_url:
        u = urlparse(settings.s3_endpoint_url)
        parts.append(f"ENDPOINT '{u.netloc}'")
        parts.append(f"USE_SSL {'true' if u.scheme == 'https' else 'false'}")
    if settings.s3_force_path_style:
        parts.append("URL_STYLE 'path'")

    return [
        "CREATE OR REPLACE SECRET accelerator_s3 (TYPE s3, " + ", ".join(parts) + ")",
    ]


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
            if not settings.s3_bucket:
                raise RuntimeError(
                    "ACCELERATOR_S3_BUCKET must be set when ACCELERATOR_STORAGE_BACKEND=s3"
                )
            _backend = S3StorageBackend(
                bucket=settings.s3_bucket,
                prefix=settings.s3_prefix,
                endpoint_url=settings.s3_endpoint_url,
                region=settings.s3_region,
                access_key_id=settings.s3_access_key_id,
                secret_access_key=settings.s3_secret_access_key,
                force_path_style=settings.s3_force_path_style,
            )
        else:
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
